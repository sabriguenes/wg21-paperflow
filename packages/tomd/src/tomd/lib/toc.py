"""Table of Contents detection.

Format-agnostic: operates on plain strings and returns indices.
No dependency on PDF or HTML converter types.
"""

import logging
import re

from . import SECTION_NUM_PREFIX_RE
from .similarity import similar

_log = logging.getLogger(__name__)

_TRAILING_PAGE_NUM_RE = re.compile(r"\s+\d{1,4}\s*$")
_DOT_LEADER_RE = re.compile(r"\s*[.·]{2,}[\s.·]*")
_SPACED_DOT_LEADER_RE = re.compile(r"(?:\. ){2,}\.")
# Tighter threshold for TOC *detection* (vs stripping in normalization).
# 5+ consecutive dots avoids false positives on ellipsis (...) and
# C++ variadic syntax (Args...) that appear heavily in WG21 papers.
_DOT_LEADER_DETECT_RE = re.compile(r"[.·]{5,}")

_TOC_LABELS = frozenset({
    "table of contents",
    "table of content",
    "contents",
})

_WHITESPACE_RE = re.compile(r"\s+")

_MIN_TOC_RUN = 3
_MAX_GAP = 3
_MAX_FUZZY_HEADINGS = 200


def _first_line(text: str) -> str:
    """Extract and strip the first line of a multi-line string."""
    return text.split("\n")[0].strip()


def has_dot_leader(text: str) -> bool:
    """Check for dot leaders in any form (compact or spaced).

    Uses _DOT_LEADER_DETECT_RE (5+ dots) instead of _DOT_LEADER_RE (2+)
    to avoid false positives on ellipsis and C++ variadic syntax.
    """
    return bool(_DOT_LEADER_DETECT_RE.search(text) or _SPACED_DOT_LEADER_RE.search(text))


def _normalize_toc_entry(text: str) -> str:
    """Normalize text for TOC comparison.

    Strips trailing page numbers, dot leaders, section number prefixes.
    Collapses whitespace, lowercases.
    """
    text = _first_line(text)
    text = _DOT_LEADER_RE.sub(" ", text)
    text = _SPACED_DOT_LEADER_RE.sub(" ", text)
    text = _TRAILING_PAGE_NUM_RE.sub("", text)
    text = SECTION_NUM_PREFIX_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip().lower()
    return text


def _is_toc_label(text: str) -> bool:
    """Check if text is a TOC heading label."""
    normalized = text.strip().lower()
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized in _TOC_LABELS


def find_toc_indices(
    texts: list[str],
    headings: set[str],
    structural_hints: list[bool] | None = None,
    full_texts: list[str] | None = None,
) -> set[int]:
    """Return indices of entries that form a Table of Contents.

    texts: ordered list of section first-line texts from the document
    headings: set of known heading texts to match against
    structural_hints: optional per-section booleans marking entries that
        look like TOC entries by structure (standalone page number on the
        second line at a consistent x position). Used as a fallback when
        headings is empty, e.g. in headingless wording-only papers.
    full_texts: optional full section texts (multi-line). When provided,
        dot-leader detection checks the full text, catching leaders on
        lines beyond the first.

    Both texts and headings are normalized before comparison. Detects runs
    of 3+ consecutive matches. Also includes any "Table of Contents" label
    immediately preceding a run.
    """
    if not texts:
        return set()
    if not headings and not structural_hints:
        return set()

    norm_headings = {_normalize_toc_entry(h) for h in headings}
    norm_headings.discard("")

    # Fast exact-match set covers the common case; fuzzy matching only
    # runs on the residual. Without this, O(sections * headings) calls
    # to similar() make large documents (2000+ pages) hang.
    _exact_set = frozenset(norm_headings)

    def _matches_heading(text: str) -> bool:
        norm = _normalize_toc_entry(text)
        if not norm:
            return False
        if norm in _exact_set:
            return True
        if len(norm_headings) > _MAX_FUZZY_HEADINGS:
            return False
        for h in norm_headings:
            if similar(norm, h):
                return True
        return False

    # Bare section-number line: digits/dots or single uppercase letter.
    # When the first line is just a number, try joining with line 2
    # for heading comparison (handles multi-line TOC entries where
    # MuPDF splits section number and title onto separate lines).
    _BARE_NUM_RE = re.compile(r"^(?:[A-Z]|\d+(?:\.\d+)*\.?)$")

    def _multi_line_match(ft_text: str) -> bool:
        lines = ft_text.split("\n")
        if len(lines) < 2:
            return False
        first = lines[0].strip()
        if not _BARE_NUM_RE.match(first):
            return False
        joined = first + " " + lines[1].strip()
        return _matches_heading(joined)

    matches = []
    for i, text in enumerate(texts):
        ft = full_texts[i] if full_texts else text
        # Check dot-leaders per line to avoid false positives on body
        # paragraphs that happen to contain 5+ dots (ASCII art, code).
        has_dot = any(has_dot_leader(ln) for ln in ft.split("\n"))
        if norm_headings:
            first_match_ok = _matches_heading(_first_line(text))
            if not first_match_ok and not has_dot:
                first_match_ok = _multi_line_match(ft)
            matches.append(has_dot or first_match_ok)
        else:
            matches.append(
                has_dot
                or bool(structural_hints
                        and i < len(structural_hints)
                        and structural_hints[i])
            )

    # Find the first match - everything before it is pre-TOC (title, metadata)
    first_match = -1
    for i, m in enumerate(matches):
        if m:
            first_match = i
            break

    if first_match < 0:
        return set()

    run_indices: list[int] = []
    seen_first_lines: set[str] = set()
    gap = 0

    for i in range(first_match, len(matches)):
        if matches[i]:
            first_line = _first_line(texts[i]).lower()
            if first_line in seen_first_lines:
                break
            seen_first_lines.add(first_line)
            if gap > 0:
                for g in range(i - gap, i):
                    run_indices.append(g)
            gap = 0
            run_indices.append(i)
        else:
            gap += 1
            if gap > _MAX_GAP:
                break

    match_count = sum(1 for i in run_indices if matches[i])
    toc_indices: set[int] = set()
    if match_count >= _MIN_TOC_RUN:
        toc_indices = set(run_indices)
        _log.debug("TOC block: %d entries (%d matched)",
                    len(run_indices), match_count)

    # Include "Table of Contents" / "Contents" label before the block
    if toc_indices:
        first = min(toc_indices)
        prev = first - 1
        if prev >= 0 and _is_toc_label(_first_line(texts[prev])):
            toc_indices.add(prev)
            _log.debug("TOC label at index %d", prev)

    if toc_indices:
        _log.info("Detected TOC: %d entries detected", len(toc_indices))

    return toc_indices
