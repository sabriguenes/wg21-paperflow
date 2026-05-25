#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Content-coverage check: does the converted Markdown represent the source?

Complement to ``lib.pdf.qa``. QA scores structure on Markdown alone;
this module compares source text against the converted Markdown and
flags drops, truncations, and tool-emitted artefacts.

Headline metric is *coverage*: the fraction of source-text tokens that
appear in the Markdown. A second metric, *drift*, measures the
opposite (tokens in Markdown that are not in the source). Both run on
a normalized token stream; structural classification (heading vs
paragraph vs code) is QA's job, not this module's.

The "markdown-only" rule in ``lib.pdf.qa`` is QA-specific. This
sibling reads both source and Markdown by design.

See ``check_content_arch.md`` (colocated) for the algorithm
walkthrough: pipeline steps, techniques by layer, named constants,
edge cases, and the calibration plan for ``_COVERAGE_NEEDS_REVIEW``.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
import contextlib
import difflib
import fitz
import json
import logging
import os
import re
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import blake2b
from pathlib import Path
from typing import Iterable, Literal

import mistune

from paperstore import SqliteBackend
from paperstore.backend import StorageBackend
from paperstore.errors import MissingPaperMdError, MissingSourceError
from tomd.errors import CheckContentArgError
from tomd.lib.html.extract import detect_generator, strip_boilerplate

__all__ = [
    "ContentCheckResult",
    "MisalignedRegion",
    "check_paper_content",
    "run_content_check_report",
]

_log = logging.getLogger(__name__)


# -- Algorithm constants -----------------------------------------------------

# Headline alignment: blake2b digest size keeps the hash cheap and the
# collision rate negligible for paper-sized streams.
_SHINGLE_WIDTH = 5
_HASH_DIGEST_SIZE = 8

# Locality detection: only run targeted LCS when a window's local
# shingle coverage falls below the floor. Cost stays bounded per
# window, not per paper.
_LOCAL_WINDOW_TOKENS = 200
_LOCAL_COVERAGE_FLOOR = 0.6
_MIN_REGION_TOKENS = 8

# Distribution buckets used in the stdout summary. _COVERAGE_NEEDS_REVIEW
# stays None until phase 4 calibration commits a value from real data.
_COVERAGE_BUCKETS = (0.95, 0.85, 0.70)
_COVERAGE_NEEDS_REVIEW: float | None = None

# Header / footer / page-number mitigation: drop line text appearing
# on at least this fraction of pages. Crude position-free repeat
# detection, looser than tomd's vertical-band rule.
_REPEAT_RATIO = 0.5

# Sample text length surfaced for each MisalignedRegion in stdout / JSON.
_REGION_SAMPLE_CHARS = 60

# Display caps for the stdout report.
_WORST_FILES_DISPLAY_LIMIT = 30

# Batch worker plumbing matches qa.py's settings so combined runs feel
# uniform.
_CHECK_BATCH_TIMEOUT_SEC = 120
_WORKER_POLL_INTERVAL = 0.5

# JSON schema version bump rules: increment when the per-paper fields,
# constants block, or top-level keys change.
_JSON_SCHEMA_VERSION = 1


# -- Data ---------------------------------------------------------------------


@dataclass(frozen=True)
class MisalignedRegion:
    """A contiguous run of tokens present on one side but not the other.

    ``side`` is the side the tokens came from. Source-side regions are
    "missing" (in the PDF/HTML, dropped by tomd); Markdown-side regions
    are "extra" (in the Markdown without a counterpart in the source).
    """

    side: Literal["source", "markdown"]
    token_start: int
    token_end: int
    sample: str
    page: int | None = None


@dataclass(frozen=True)
class ContentCheckResult:
    """Per-paper content-coverage result.

    ``coverage`` is the share of *source* tokens whose shingles appear
    in the Markdown; ``drift`` is the share of *markdown* tokens whose
    shingles do not appear in the source. Both are in ``[0.0, 1.0]``.

    Coverage will never reach 1.0 in practice: tomd intentionally
    strips headers, footers, page numbers, and tables of contents, so
    the source extraction always carries content the Markdown cannot.
    """

    paper_id: str
    source_format: Literal["pdf", "html"]
    coverage: float
    drift: float
    source_token_count: int
    markdown_token_count: int
    missing_regions: tuple[MisalignedRegion, ...] = field(default_factory=tuple)
    extra_regions: tuple[MisalignedRegion, ...] = field(default_factory=tuple)


# -- Normalization ------------------------------------------------------------

_SMART_QUOTE_TABLE = str.maketrans({
    "‘": "'", "’": "'",
    "‚": "'", "‛": "'",
    "“": '"', "”": '"',
    "„": '"', "‟": '"',
    "′": "'", "″": '"',
    "«": '"', "»": '"',
    "–": "-", "—": "-",
    "−": "-",
    " ": " ",
    " ": " ",
    " ": " ",
    "​": " ",
    " ": " ",
    " ": " ",
    "　": " ",
})

_HYPHEN_BREAK_RE = re.compile(r"(\w+)-\n(\w+)")
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_TOKEN_RE = re.compile(r"\S+")

# Compound prefixes that should retain the hyphen across a line break.
_KEEP_HYPHEN_PREFIXES = ("self", "non", "well", "cross")


def _dehyphenate(text: str) -> str:
    """Join line-broken words; keep hyphenated compounds intact."""
    def repl(m: re.Match[str]) -> str:
        left, right = m.group(1), m.group(2)
        if left.lower() in _KEEP_HYPHEN_PREFIXES:
            return f"{left}-{right}"
        return f"{left}{right}"

    return _HYPHEN_BREAK_RE.sub(repl, text)


def _normalize(text: str) -> str:
    """Shared normalization pipeline applied to both source and Markdown."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_SMART_QUOTE_TABLE)
    text = _dehyphenate(text)
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _tokenize(text: str) -> list[str]:
    """Whitespace tokenization on already-normalized text."""
    return _TOKEN_RE.findall(text)


# -- Source extraction --------------------------------------------------------


@dataclass(frozen=True)
class _SourceStream:
    """Internal representation: normalized tokens with per-token page numbers."""

    tokens: tuple[str, ...]
    pages: tuple[int | None, ...]
    source_format: Literal["pdf", "html"]


def _extract_pdf_pages(path: Path) -> list[str]:
    """Return raw page text via PyMuPDF ``page.get_text()``.

    Closed in a ``finally`` block per tomd convention. No classification:
    this is the deliberately stripped-down extraction path that bypasses
    tomd's pipeline.
    """
    pages: list[str] = []
    doc = None
    try:
        doc = fitz.open(str(path))
        for i in range(doc.page_count):
            pages.append(str(doc[i].get_text()))
    finally:
        if doc is not None:
            doc.close()
    return pages


def _strip_repeating_lines(pages: list[str]) -> list[str]:
    """Drop line-text appearing on ``>=_REPEAT_RATIO`` of pages.

    Position-free header/footer/page-number scrubber. Cruder than tomd's
    vertical-band rule but works on the flat ``get_text()`` output and
    catches the dominant repeat artefacts (running titles, running
    document numbers, "Page N of M").
    """
    if not pages:
        return pages
    counts: dict[str, int] = {}
    for page in pages:
        seen_on_page: set[str] = set()
        for raw in page.splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped in seen_on_page:
                continue
            seen_on_page.add(stripped)
            counts[stripped] = counts.get(stripped, 0) + 1

    page_count = len(pages)
    threshold = max(2, int(page_count * _REPEAT_RATIO))
    repeats = {line for line, n in counts.items() if n >= threshold}
    if not repeats:
        return pages

    cleaned: list[str] = []
    for page in pages:
        kept = [ln for ln in page.splitlines() if ln.strip() not in repeats]
        cleaned.append("\n".join(kept))
    return cleaned


def _extract_pdf_stream(path: Path) -> _SourceStream:
    pages = _strip_repeating_lines(_extract_pdf_pages(path))
    tokens: list[str] = []
    page_map: list[int | None] = []
    for page_idx, raw in enumerate(pages, start=1):
        normalized = _normalize(raw)
        if not normalized:
            continue
        page_tokens = _tokenize(normalized)
        tokens.extend(page_tokens)
        page_map.extend([page_idx] * len(page_tokens))
    return _SourceStream(
        tokens=tuple(tokens),
        pages=tuple(page_map),
        source_format="pdf",
    )


def _extract_html_stream(path: Path) -> _SourceStream:
    """HTML extraction reusing tomd's per-generator boilerplate strip.

    Sacrifices full extractor-level independence on the HTML side. The
    alternative (raw ``soup.get_text()``) keeps nav, generator headers,
    and auto-TOCs, making HTML coverage uninterpretable. Documented
    trade-off; HTML inputs produce a narrower "did tomd's renderer drop
    body content the stripper kept?" check.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")
    generator = detect_generator(soup)
    strip_boilerplate(soup, generator)
    text = soup.get_text(separator=" ")
    normalized = _normalize(text)
    tokens = _tokenize(normalized)
    return _SourceStream(
        tokens=tuple(tokens),
        pages=tuple([None] * len(tokens)),
        source_format="html",
    )


# -- Markdown extraction ------------------------------------------------------

_AST_RENDERER = mistune.create_markdown(renderer="ast", plugins=["table"])

# Mistune AST node types whose ``raw`` value contains literal text to
# include in the comparison stream. Block-level wrappers (paragraph,
# heading, list_item, table cell) recurse through their children.
_INLINE_TEXT_TYPES = frozenset({
    "text", "codespan", "linebreak", "softbreak",
})

_BLOCK_CODE_TYPES = frozenset({"block_code"})

_SKIP_TYPES = frozenset({"thematic_break", "blank_line"})

# Recognized tomd-emitted HTML markers that must be excluded from the
# markdown token stream (otherwise they would inflate drift).
_TOMD_HTML_MARKER_RE = re.compile(
    r"<!--\s*tomd:[^>]*?-->", re.IGNORECASE | re.DOTALL,
)
_FRONT_MATTER_RE = re.compile(r"^---\n.+?\n---\n?", re.DOTALL)


def _collect_node_text(node: dict, out: list[str]) -> None:
    """Walk a mistune AST node, appending text to ``out``."""
    ntype = node.get("type", "")
    if ntype in _SKIP_TYPES:
        return
    if ntype in _BLOCK_CODE_TYPES:
        raw = node.get("raw", "")
        if raw:
            out.append(raw)
        return
    if ntype in _INLINE_TEXT_TYPES:
        raw = node.get("raw", "")
        if raw:
            out.append(raw)
        # Inline nodes have no recursable children.
        return
    if ntype == "block_html":
        raw = node.get("raw", "")
        # tomd markers are intentionally dropped; arbitrary other inline
        # HTML survives as text.
        cleaned = _TOMD_HTML_MARKER_RE.sub(" ", raw)
        if cleaned.strip():
            out.append(cleaned)
        return
    if ntype == "inline_html":
        raw = node.get("raw", "")
        cleaned = _TOMD_HTML_MARKER_RE.sub(" ", raw)
        if cleaned.strip():
            out.append(cleaned)
        return
    for child in node.get("children", []) or []:
        _collect_node_text(child, out)


def _extract_markdown_stream(md_text: str) -> tuple[str, ...]:
    """Return the normalized token stream for the converted Markdown.

    Front matter and tomd-emitted ``<!-- tomd:* -->`` markers are
    stripped before AST parsing so they do not appear as drift tokens.
    """
    body = _FRONT_MATTER_RE.sub("", md_text, count=1)
    body = _TOMD_HTML_MARKER_RE.sub(" ", body)
    tokens_raw: list[str] = []
    for node in _AST_RENDERER(body):
        if isinstance(node, dict):
            _collect_node_text(node, tokens_raw)
    text = " ".join(tokens_raw)
    return tuple(_tokenize(_normalize(text)))


# -- Shingled-hash coverage ---------------------------------------------------


def _shingle_hashes(tokens: Iterable[str], width: int = _SHINGLE_WIDTH) -> list[int]:
    """Sliding-window hashes over ``tokens`` of size ``width``.

    blake2b digest packed into a Python int. A duplicated shingle
    produces a duplicated hash; downstream uses multiset semantics so
    repetition counts toward coverage proportionally.
    """
    toks = list(tokens)
    if len(toks) < width:
        return []
    hashes: list[int] = []
    for i in range(len(toks) - width + 1):
        h = blake2b(digest_size=_HASH_DIGEST_SIZE)
        h.update(" ".join(toks[i:i + width]).encode("utf-8"))
        hashes.append(int.from_bytes(h.digest(), "big"))
    return hashes


def _multiset_coverage(source: list[int], target: list[int]) -> float:
    """Fraction of ``source`` shingles also present in ``target`` (multiset)."""
    if not source:
        return 1.0
    target_counts: dict[int, int] = {}
    for h in target:
        target_counts[h] = target_counts.get(h, 0) + 1
    matched = 0
    for h in source:
        n = target_counts.get(h, 0)
        if n > 0:
            matched += 1
            target_counts[h] = n - 1
    return matched / len(source)


def _local_coverage_windows(
    source_hashes: list[int],
    md_hash_set: set[int],
    window_tokens: int,
) -> list[tuple[int, int]]:
    """Return token-index ranges where local shingle coverage is low.

    A window covers tokens ``[i, i + window_tokens)``; the corresponding
    shingle slice is ``source_hashes[i : i + window_tokens - WIDTH + 1]``.
    Adjacent low-coverage windows are merged.
    """
    if not source_hashes:
        return []
    step = max(1, window_tokens // 2)
    width_offset = _SHINGLE_WIDTH - 1
    low_ranges: list[tuple[int, int]] = []
    for start in range(0, len(source_hashes), step):
        end = min(start + window_tokens - width_offset, len(source_hashes))
        if end <= start:
            break
        slice_ = source_hashes[start:end]
        if not slice_:
            continue
        matched = sum(1 for h in slice_ if h in md_hash_set)
        ratio = matched / len(slice_)
        if ratio < _LOCAL_COVERAGE_FLOOR:
            token_end = min(start + window_tokens, len(source_hashes) + width_offset)
            low_ranges.append((start, token_end))

    if not low_ranges:
        return []
    merged: list[tuple[int, int]] = [low_ranges[0]]
    for lo, hi in low_ranges[1:]:
        prev_lo, prev_hi = merged[-1]
        if lo <= prev_hi:
            merged[-1] = (prev_lo, max(prev_hi, hi))
        else:
            merged.append((lo, hi))
    return merged


# -- Region collection --------------------------------------------------------


def _regions_from_opcodes(
    matcher: difflib.SequenceMatcher,
    source_tokens: list[str],
    md_tokens: list[str],
    source_offset: int,
    md_offset: int,
    page_map: tuple[int | None, ...],
) -> tuple[list[MisalignedRegion], list[MisalignedRegion]]:
    """Convert SequenceMatcher opcodes into MisalignedRegion lists."""
    missing: list[MisalignedRegion] = []
    extra: list[MisalignedRegion] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        src_len = i2 - i1
        md_len = j2 - j1
        if tag in ("delete", "replace") and src_len >= _MIN_REGION_TOKENS:
            tok_start = source_offset + i1
            tok_end = source_offset + i2
            sample = _sample(source_tokens[i1:i2])
            page = _page_for(page_map, tok_start, tok_end)
            missing.append(MisalignedRegion(
                side="source",
                token_start=tok_start,
                token_end=tok_end,
                sample=sample,
                page=page,
            ))
        if tag in ("insert", "replace") and md_len >= _MIN_REGION_TOKENS:
            tok_start = md_offset + j1
            tok_end = md_offset + j2
            sample = _sample(md_tokens[j1:j2])
            extra.append(MisalignedRegion(
                side="markdown",
                token_start=tok_start,
                token_end=tok_end,
                sample=sample,
                page=None,
            ))
    return missing, extra


def _sample(tokens: list[str]) -> str:
    joined = " ".join(tokens)
    if len(joined) <= _REGION_SAMPLE_CHARS:
        return joined
    return joined[: _REGION_SAMPLE_CHARS - 1].rstrip() + "..."


def _page_for(
    page_map: tuple[int | None, ...], start: int, end: int,
) -> int | None:
    if not page_map:
        return None
    end_clamped = min(end, len(page_map))
    for i in range(start, end_clamped):
        if page_map[i] is not None:
            return page_map[i]
    return None


# -- Public entry point -------------------------------------------------------


def check_paper_content(
    pid: str,
    backend: StorageBackend,
) -> ContentCheckResult:
    """Run the content-coverage check for one paper.

    Reads source and Markdown through the backend. Library function:
    returns data, never persists. The CLI module owns reporting.

    Raises:
        paperstore.MissingSourceError: if ``<pid>.pdf|.html`` not staged.
        paperstore.MissingPaperMdError: if ``<pid>.md`` not written.
        tomd.CheckContentArgError: if the source suffix is neither
            ``.pdf`` nor ``.html``.
    """
    source_path = backend.get_source_path(pid)
    md_text = backend.get_paper_md(pid)

    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        src_stream = _extract_pdf_stream(source_path)
    elif suffix == ".html":
        src_stream = _extract_html_stream(source_path)
    else:
        raise CheckContentArgError(
            f"Unsupported source suffix {suffix!r} for {pid}; expected .pdf or .html."
        )

    md_tokens = list(_extract_markdown_stream(md_text))
    src_tokens = list(src_stream.tokens)

    src_hashes = _shingle_hashes(src_tokens)
    md_hashes = _shingle_hashes(md_tokens)

    coverage = _multiset_coverage(src_hashes, md_hashes)
    drift = 1.0 - _multiset_coverage(md_hashes, src_hashes) if md_hashes else 0.0

    missing_regions: list[MisalignedRegion] = []
    extra_regions: list[MisalignedRegion] = []

    md_hash_set = set(md_hashes)
    src_hash_set = set(src_hashes)

    # Source-side scan: gaps in the source that don't appear in the markdown.
    src_windows = _local_coverage_windows(
        src_hashes, md_hash_set, _LOCAL_WINDOW_TOKENS,
    )
    for win_start, win_end in src_windows:
        end_clamped = min(win_end, len(src_tokens))
        if end_clamped - win_start < _MIN_REGION_TOKENS:
            continue
        window_tokens = src_tokens[win_start:end_clamped]
        matcher = difflib.SequenceMatcher(
            a=window_tokens, b=md_tokens, autojunk=False,
        )
        miss, ext = _regions_from_opcodes(
            matcher,
            window_tokens,
            md_tokens,
            source_offset=win_start,
            md_offset=0,
            page_map=src_stream.pages,
        )
        missing_regions.extend(miss)
        extra_regions.extend(ext)

    # Markdown-side scan: tokens in markdown with no counterpart in the
    # source. The source-side scan only fires on low source-coverage,
    # which never triggers when markdown adds content without dropping
    # any. The symmetric pass surfaces those extra regions.
    md_windows = _local_coverage_windows(
        md_hashes, src_hash_set, _LOCAL_WINDOW_TOKENS,
    )
    for win_start, win_end in md_windows:
        end_clamped = min(win_end, len(md_tokens))
        if end_clamped - win_start < _MIN_REGION_TOKENS:
            continue
        window_tokens = md_tokens[win_start:end_clamped]
        matcher = difflib.SequenceMatcher(
            a=src_tokens, b=window_tokens, autojunk=False,
        )
        miss, ext = _regions_from_opcodes(
            matcher,
            src_tokens,
            window_tokens,
            source_offset=0,
            md_offset=win_start,
            page_map=src_stream.pages,
        )
        # Only the markdown-side opcodes are new information here;
        # any source-side opcodes would just duplicate the first pass.
        extra_regions.extend(ext)

    missing_regions = _dedup_regions(missing_regions)
    extra_regions = _dedup_regions(extra_regions)

    return ContentCheckResult(
        paper_id=pid,
        source_format=src_stream.source_format,
        coverage=coverage,
        drift=drift,
        source_token_count=len(src_tokens),
        markdown_token_count=len(md_tokens),
        missing_regions=tuple(missing_regions),
        extra_regions=tuple(extra_regions),
    )


def _dedup_regions(regions: list[MisalignedRegion]) -> list[MisalignedRegion]:
    """Drop overlapping duplicates from a list of MisalignedRegions.

    Windows are 50%-overlapping, so the same gap can appear twice. Keep
    the longest covering region for each token range.
    """
    if not regions:
        return []
    regions = sorted(regions, key=lambda r: (r.token_start, -r.token_end))
    kept: list[MisalignedRegion] = []
    for r in regions:
        if kept and r.token_start < kept[-1].token_end:
            prev = kept[-1]
            if r.token_end > prev.token_end:
                kept[-1] = MisalignedRegion(
                    side=prev.side,
                    token_start=prev.token_start,
                    token_end=r.token_end,
                    sample=prev.sample,
                    page=prev.page,
                )
            continue
        kept.append(r)
    return kept


# -- Batch runner -------------------------------------------------------------


def _check_one_from_paths(item: dict) -> dict:
    """Worker entry: re-open the backend, then call check_paper_content.

    Pickling a StorageBackend across processes is fragile, so we ship a
    workspace path and let the worker construct its own backend.
    """
    pid = item["paper_id"]
    workspace_dir = item["workspace_dir"]
    try:
        backend = SqliteBackend(Path(workspace_dir))
        result = check_paper_content(pid, backend)
        return {"status": "ok", "paper_id": pid, "result": _result_to_dict(result)}
    except (MissingSourceError, MissingPaperMdError) as exc:
        return {"status": "skipped", "paper_id": pid, "reason": str(exc)}
    # Batch robustness: one bad paper must not crash the run
    except Exception as exc:  # noqa: BLE001
        _log.error("check-content failed for %s: %s", pid, exc)
        return {"status": "error", "paper_id": pid, "error": str(exc)}


def _result_to_dict(r: ContentCheckResult) -> dict:
    return {
        "paper_id": r.paper_id,
        "source_format": r.source_format,
        "coverage": r.coverage,
        "drift": r.drift,
        "source_token_count": r.source_token_count,
        "markdown_token_count": r.markdown_token_count,
        "missing_regions": [asdict(reg) for reg in r.missing_regions],
        "extra_regions": [asdict(reg) for reg in r.extra_regions],
    }


def run_content_check_report(
    items: list[tuple[str, Path]],
    json_path: Path | None = None,
    workers: int = 1,
    timeout: int = _CHECK_BATCH_TIMEOUT_SEC,
) -> None:
    """Run check-content for each ``(paper_id, workspace_dir)`` pair.

    Mirrors :func:`tomd.lib.pdf.qa.run_qa_report`: parallel workers,
    straggler timeout, ranked stdout summary, optional atomic JSON
    writer. The workspace path travels with each item so workers can
    open their own backend (a ``StorageBackend`` does not survive
    ``pickle``-based IPC reliably).
    """
    total = len(items)
    results: list[ContentCheckResult] = []
    errors: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    t0 = time.monotonic()

    worker_payloads = [
        {"paper_id": pid, "workspace_dir": str(workspace)} for pid, workspace in items
    ]

    if workers > 1:
        done_count = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_id = {
                pool.submit(_check_one_from_paths, p): p["paper_id"]
                for p in worker_payloads
            }
            pending = set(future_to_id.keys())
            last_completion = time.monotonic()

            while pending:
                newly_done = {f for f in pending if f.done()}
                if newly_done:
                    last_completion = time.monotonic()
                    for f in newly_done:
                        pending.discard(f)
                        done_count += 1
                        pid = future_to_id[f]
                        _emit_progress(done_count, total, pid, t0)
                        try:
                            d = f.result()
                            _accumulate(d, results, skipped, errors)
                        # Batch robustness: one bad paper must not crash the run
                        except Exception as exc:  # noqa: BLE001
                            errors.append((pid, str(exc)))
                elif time.monotonic() - last_completion > timeout:
                    timed_out: list[str] = []
                    for f in pending:
                        pid = future_to_id[f]
                        timed_out.append(pid)
                        f.cancel()
                        errors.append((pid, f"timeout (no progress for {timeout}s)"))
                    done_count += len(pending)
                    print(
                        f"\n  TIMEOUT: {len(timed_out)} files aborted: "
                        f"{', '.join(timed_out)}",
                        file=sys.stderr,
                    )
                    break
                else:
                    time.sleep(_WORKER_POLL_INTERVAL)
            pool.shutdown(wait=False, cancel_futures=True)
    else:
        for i, payload in enumerate(worker_payloads, 1):
            pid = payload["paper_id"]
            _emit_progress(i, total, pid, t0)
            d = _check_one_from_paths(payload)
            _accumulate(d, results, skipped, errors)

    wall = time.monotonic() - t0
    avg = wall / total if total else 0.0
    print(
        f"\n  Finished in {wall/60:.1f} minutes ({avg:.1f}s/file avg)\n",
        file=sys.stderr,
    )

    _print_report(results, skipped, errors)

    if json_path is not None:
        _write_json(json_path, results)


def _emit_progress(done: int, total: int, pid: str, t0: float) -> None:
    elapsed = time.monotonic() - t0
    rate = done / elapsed if elapsed > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    print(
        f"\r  [{done}/{total}] {pid:<40} {rate:.1f} files/s  ETA {eta/60:.0f}m",
        end="",
        file=sys.stderr,
    )
    sys.stderr.flush()


def _accumulate(
    d: dict,
    results: list[ContentCheckResult],
    skipped: list[tuple[str, str]],
    errors: list[tuple[str, str]],
) -> None:
    if d["status"] == "ok":
        results.append(_result_from_dict(d["result"]))
    elif d["status"] == "skipped":
        skipped.append((d["paper_id"], d["reason"]))
    else:
        errors.append((d["paper_id"], d.get("error", "unknown error")))


def _result_from_dict(d: dict) -> ContentCheckResult:
    return ContentCheckResult(
        paper_id=d["paper_id"],
        source_format=d["source_format"],
        coverage=d["coverage"],
        drift=d["drift"],
        source_token_count=d["source_token_count"],
        markdown_token_count=d["markdown_token_count"],
        missing_regions=tuple(
            MisalignedRegion(**r) for r in d.get("missing_regions", [])
        ),
        extra_regions=tuple(
            MisalignedRegion(**r) for r in d.get("extra_regions", [])
        ),
    )


# -- Stdout / JSON output ----------------------------------------------------

_REPORT_PREAMBLE = (
    "Coverage measures the share of source-text tokens that align with\n"
    "tokens in the produced markdown. Perfect coverage (1.00) is not\n"
    "achievable: tomd intentionally strips headers, footers, page\n"
    "numbers, and tables of contents, all of which appear in the source\n"
    "extraction but cannot appear in the markdown. Expect clean papers\n"
    "to land between 0.90 and 0.97."
)


def _print_report(
    results: list[ContentCheckResult],
    skipped: list[tuple[str, str]],
    errors: list[tuple[str, str]],
) -> None:
    total = len(results)
    print(f"\ntomd Content-Check Report: {total} files")
    print("=" * 40)
    print()
    print(_REPORT_PREAMBLE)
    print()

    if total == 0:
        print("No papers were scored.")
        if skipped:
            print(f"Skipped: {len(skipped)}")
        if errors:
            print(f"Errors: {len(errors)}")
        return

    high, mid, low = _COVERAGE_BUCKETS
    buckets = {
        f"{high:.2f}+":             sum(1 for r in results if r.coverage >= high),
        f"{mid:.2f}-{high:.2f}":    sum(1 for r in results if mid <= r.coverage < high),
        f"{low:.2f}-{mid:.2f}":     sum(1 for r in results if low <= r.coverage < mid),
        f"<{low:.2f}":              sum(1 for r in results if r.coverage < low),
    }

    print("Coverage Distribution:")
    for label, count in buckets.items():
        pct = 100 * count / total if total else 0.0
        print(f"  {label:<14} {count:>6}  ({pct:.1f}%)")

    if _COVERAGE_NEEDS_REVIEW is not None:
        threshold = _COVERAGE_NEEDS_REVIEW
        needs_review = sum(1 for r in results if r.coverage < threshold)
        print()
        print(f"Files needing review (coverage < {threshold:.2f}): {needs_review}")
        print(
            f"Files probably OK   (coverage >= {threshold:.2f}): "
            f"{total - needs_review}"
        )

    worst = sorted(results, key=lambda r: r.coverage)[:_WORST_FILES_DISPLAY_LIMIT]
    print()
    print(f"Worst {len(worst)} files (lowest coverage):")
    print(f"  {'Cov':>5}  {'Drift':>5}  {'File':<12}  Top missing region")
    print(f"  {'-' * 5}  {'-' * 5}  {'-' * 12}  {'-' * 41}")
    for r in worst:
        sample = ""
        if r.missing_regions:
            top = r.missing_regions[0]
            page = f"p.{top.page}: " if top.page else ""
            sample = f'"{page}{top.sample}"'
        print(
            f"  {r.coverage:>5.2f}  {r.drift:>5.2f}  "
            f"{r.paper_id:<12}  {sample}"
        )

    if skipped:
        print(f"\nSkipped: {len(skipped)} (no source or no markdown)")
    if errors:
        print(f"\nErrors: {len(errors)}")
        for pid, msg in errors[:10]:
            print(f"  {pid}: {msg}")


def _write_json(json_path: Path, results: list[ContentCheckResult]) -> None:
    """Atomic JSON dump matching the pattern in qa.py's run_qa_report."""
    payload = {
        "schema_version": _JSON_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "constants": {
            "shingle_width": _SHINGLE_WIDTH,
            "local_window_tokens": _LOCAL_WINDOW_TOKENS,
            "local_coverage_floor": _LOCAL_COVERAGE_FLOOR,
            "min_region_tokens": _MIN_REGION_TOKENS,
            "coverage_buckets": list(_COVERAGE_BUCKETS),
            "coverage_needs_review": _COVERAGE_NEEDS_REVIEW,
            "repeat_ratio": _REPEAT_RATIO,
        },
        "papers": [_result_to_dict(r) for r in results],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_bytes = json.dumps(payload, indent=2).encode("utf-8")
    tmp_fd, tmp_path = tempfile.mkstemp(dir=json_path.parent, suffix=".tmp")
    try:
        os.write(tmp_fd, json_bytes)
        os.close(tmp_fd)
        os.replace(tmp_path, json_path)
    except BaseException:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    print(f"\nDetailed metrics written to {json_path}")
