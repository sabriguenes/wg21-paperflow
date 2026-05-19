#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Public tomd API: convert a staged paper source to markdown.

``convert_paper(paper_id, source_path, meta)`` is the only supported
entry point. The function is pure: it reads the source file from disk,
converts to markdown, and returns the markdown plus any optional LLM
reconcile prompts. Persisting the result is the caller's job, done
through a :class:`paperstore.StorageBackend` so non-filesystem backends
work without changes here.

YAML front-matter fallback lives here: whatever tomd could not extract
from the source paper is filled in from the mailing-index row for the
paper. Fields already present in the paper's front matter win.

The returned markdown's front matter is always emitted in the strict
canonical key order ``title, document, date, intent, audience,
reply-to`` (unknown keys after ``audience``, ``reply-to`` always last),
regardless of the source format or the order in which fallback fields
were merged. The ``_normalize_front_matter`` pass enforces this
invariant.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from tomd.lib import (
    format_front_matter,
    sanitize_metadata,
    strip_freeform_metadata_lines,
    strip_leading_h1,
    EMAIL_RE,
)
from tomd.lib.html import convert_html
from tomd.lib.pdf import convert_pdf

__all__ = ["convert_paper"]

logger = logging.getLogger(__name__)

_TOC_MAX_LINES = 300
_TOC_RE = re.compile(
    r"(?m)^(?:#{1,3}\s*)?(?:Table of )?Contents\s*$\r?\n?"
    r"(.*?)"
    r"(?=\r?\n#{1,3}\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_FALLBACK_KEY_MAP = {
    "title": "title",
    "paper_id": "document",
    "document_date": "date",
    "subgroup": "audience",       # mailing row key
    "target_group": "audience",   # DB row key (SqliteBackend)
    "authors": "reply-to",
}

# Keys where mailing metadata is authoritative and should override
# whatever the source document contains. SD-7 mandates that D-numbers
# (draft circulation) never appear in published mailings; the mailing
# paper_id (P/N-number) is the canonical identifier.
_OVERRIDE_KEYS = {"document"}

_FRONT_MATTER_RE = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---\s*\n?", re.DOTALL
)


def _strip_toc_replace(m: re.Match[str]) -> str:
    span = m.group(0)
    if span.count("\n") > _TOC_MAX_LINES:
        return span
    return "\n"


def _strip_toc(text: str) -> str:
    """Remove Table of Contents sections that produce phantom findings."""
    return _TOC_RE.sub(_strip_toc_replace, text)


_LIST_ITEM_RE = re.compile(r"^\s+-\s+(.*)$")


def _unquote_yaml_scalar(s: str) -> str:
    """Strip surrounding double-quotes and resolve YAML backslash escapes."""
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    inner = s[1:-1]
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            nxt = inner[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt in ('"', "\\"):
                out.append(nxt)
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_front_matter_body(body: str) -> dict:
    """Parse a YAML front-matter body into a dict.

    Recognizes the two shapes tomd emits: ``key: value`` (optionally
    double-quoted) and ``key:`` followed by indented ``- "item"`` lines.
    Anything else is dropped. Sufficient for round-tripping tomd's own
    front matter through ``format_front_matter``.
    """
    parsed: dict = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line.startswith((" ", "\t", "-")):
            i += 1
            continue
        head, sep, tail = line.partition(":")
        if not sep:
            i += 1
            continue
        key = head.strip()
        value = tail.strip()
        if value:
            parsed[key] = _unquote_yaml_scalar(value)
            i += 1
            continue
        items: list[str] = []
        j = i + 1
        while j < len(lines):
            item_line = lines[j]
            if not item_line.strip():
                j += 1
                continue
            m = _LIST_ITEM_RE.match(item_line)
            if not m:
                break
            items.append(_unquote_yaml_scalar(m.group(1).strip()))
            j += 1
        if items:
            parsed[key] = items
            i = j
        else:
            i += 1
    return parsed


def _normalize_front_matter(md: str, mailing_meta: dict | None) -> str:
    """Parse front matter once, sanitize, apply mailing fallback, and re-emit.

    Replaces the former three-pass sequence of ``_sanitize_front_matter``,
    ``_apply_metadata_fallback``, and ``_canonicalize_front_matter``.
    """
    match = _FRONT_MATTER_RE.match(md)
    if match:
        parsed = _parse_front_matter_body(match.group("body"))
        rest = md[match.end():]
    else:
        parsed = {}
        rest = md

    if not parsed and not mailing_meta:
        return md

    # Sanitize title and reply-to values.
    parsed = sanitize_metadata(parsed)

    # Inject missing fields (or override authoritative ones) from mailing.
    if mailing_meta:
        present = set(parsed)
        # Bare-name-only reply-to (no emails) should not block the
        # mailing fallback: the mailing may have richer author+email data.
        rt = parsed.get("reply-to")
        if (
            isinstance(rt, list) and rt
            and not any(EMAIL_RE.search(e) for e in rt)
        ):
            present.discard("reply-to")
        added_yaml_keys: set[str] = set()
        for src_key, yaml_key in _FALLBACK_KEY_MAP.items():
            if yaml_key in added_yaml_keys:
                continue
            is_override = yaml_key in _OVERRIDE_KEYS
            if yaml_key in present and not is_override:
                continue
            val = (
                mailing_meta.get(src_key)
                if isinstance(mailing_meta, dict)
                else getattr(mailing_meta, src_key, None)
            )
            if val in (None, "", []):
                continue
            if yaml_key == "date":
                logger.debug(
                    "Date fallback from mailing (%s=%r): source had no date",
                    src_key, val,
                )
            parsed[yaml_key] = val
            added_yaml_keys.add(yaml_key)

    if not parsed:
        return md

    new_block = format_front_matter(parsed)
    if not new_block:
        return md

    return f"{new_block}\n\n{rest.lstrip()}"


_METADATA_TABLE_LINE_RE = re.compile(
    r"^\|\s*(?:Doc(?:ument)?\.?\s*(?:No\.?|Number|#)|Date|Reply[- ]?to|"
    r"Audience|Author|Editor|Project|Email|Subgroup)\s*",
    re.IGNORECASE,
)

_PIPE_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_PIPE_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$")

_METADATA_TABLE_SCAN_DEPTH = 30
_MIN_LABEL_ROWS_FOR_TABLE_STRIP = 2


def _strip_body_metadata_text(md: str) -> str:
    """Remove metadata pipe tables from the body (after front matter).

    Scans the first ~20 non-blank lines of the body for pipe tables whose
    rows contain metadata labels (Doc No, Date, Author, etc.). Removes
    complete tables (including separator rows) when >=2 label rows are found.
    """
    match = _FRONT_MATTER_RE.match(md)
    if not match:
        return md

    front = md[:match.end()]
    body = md[match.end():]

    lines = body.split("\n")
    to_remove: set[int] = set()
    i = 0
    scan_limit = min(len(lines), _METADATA_TABLE_SCAN_DEPTH)

    while i < scan_limit:
        if not lines[i].strip():
            i += 1
            continue

        if _PIPE_TABLE_ROW_RE.match(lines[i].strip()):
            table_start = i
            table_end = i
            label_count = 0

            while table_end < len(lines) and (
                _PIPE_TABLE_ROW_RE.match(lines[table_end].strip())
                or _PIPE_SEPARATOR_RE.match(lines[table_end].strip())
            ):
                if _METADATA_TABLE_LINE_RE.match(lines[table_end].strip()):
                    label_count += 1
                table_end += 1

            if label_count >= _MIN_LABEL_ROWS_FOR_TABLE_STRIP:
                for j in range(table_start, table_end):
                    to_remove.add(j)
                i = table_end
                continue

        if lines[i].strip().startswith("#"):
            break

        i += 1

    if not to_remove:
        return md

    new_lines = [ln for j, ln in enumerate(lines) if j not in to_remove]
    return front + "\n".join(new_lines)


def _convert_with_tomd(path: Path) -> tuple[str, list[str] | None]:
    """Dispatch to the appropriate tomd converter by file suffix."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return convert_pdf(path)
    if suffix in (".html", ".htm"):
        return convert_html(path)
    raise ValueError(
        f"Unsupported source format {suffix!r} for {path.name}; "
        f"expected .pdf, .html, or .htm"
    )


_INTENT_LINE_RE = re.compile(r"^intent\s*:\s*(\S+)", re.MULTILINE)


def _extract_intent_from_front_matter(md: str) -> str:
    """Return the ``intent`` value from the markdown YAML front matter, or ``""``."""
    front_matter_match = _FRONT_MATTER_RE.match(md)
    if not front_matter_match:
        return ""
    body = front_matter_match.group("body")
    intent_match = _INTENT_LINE_RE.search(body)
    if not intent_match:
        return ""
    return intent_match.group(1).strip().strip('"\'')


def convert_paper(
    paper_id: str,
    source_path: Path,
    meta: dict,
) -> tuple[str, list[str] | None, str]:
    """Convert a staged source file to markdown. Pure: no database or
    filesystem writes.

    All inputs are pre-fetched by the caller. Reads ``source_path`` from
    disk, runs the appropriate converter, applies YAML front-matter
    fallback from ``meta``, canonicalizes front-matter key order, strips
    TOC blocks, and returns the result. The caller is responsible for
    persisting the markdown (and optional prompts) through the storage
    backend.

    The returned markdown's YAML front matter is guaranteed to use the
    strict canonical key order ``title, document, date, intent,
    audience, reply-to``. Missing keys are skipped; unknown keys are
    placed after ``audience`` so ``reply-to`` is always last.

    Returns ``(markdown, prompts, extracted_intent)``:

    * ``markdown`` is the converted markdown text.
    * ``prompts`` is the JSON-serializable list of LLM reconcile prompts
      tomd produced for uncertain regions, or ``None`` when there are
      none.
    * ``extracted_intent`` is the ``intent`` value from the markdown's
      YAML front matter (``""`` if absent).

    Raises:
        RuntimeError: tomd produced no usable markdown.
    """
    md, prompts = _convert_with_tomd(source_path)

    if prompts:
        logger.warning(
            "tomd [%s] flagged %d uncertain region(s)",
            paper_id, len(prompts),
        )

    if not md or not md.strip():
        raise RuntimeError(
            f"tomd produced empty markdown for {paper_id} (slide deck, "
            f"standards draft, or unreadable source)."
        )

    md = _normalize_front_matter(md, meta)
    md = _strip_body_metadata_text(md)
    md = strip_freeform_metadata_lines(md)

    # Re-run H1 stripping: leaked metadata before the H1 may have
    # blocked strip_leading_h1 in the emit layer.
    title_m = re.search(r'^title:\s*"?(.+?)"?\s*$', md, re.MULTILINE)
    if title_m and md.startswith("---"):
        fm_close = md.find("\n---", 3)
        if fm_close >= 0:
            body_start = md.find("\n", fm_close + 1)
            if body_start >= 0:
                body_start += 1
                body = md[body_start:]
                body = strip_leading_h1(body, title_m.group(1))
                md = md[:body_start] + body

    md = _strip_toc(md)

    extracted_intent = _extract_intent_from_front_matter(md)
    return md, prompts, extracted_intent
