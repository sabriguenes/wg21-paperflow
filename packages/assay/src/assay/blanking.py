#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Non-prose blanking for paper markdown.

Blanks YAML frontmatter, revision history, references, and
acknowledgments with empty lines in one pass through the document.
Line numbers are preserved (lines are blanked, not removed) so
downstream references stay valid.

Package-private. Called by the pipeline before analytical processing.
"""

from __future__ import annotations

import re
from enum import Enum, auto

_YAML_FENCE_RE = re.compile(r"^---\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s+")


class _HeadingKind(Enum):
    YES = auto()
    NO = auto()
    UNKNOWN = auto()


# ---------------------------------------------------------------------------
# Revision history
# ---------------------------------------------------------------------------

_REVISION_HEADING_RES: list[re.Pattern[str]] = [
    re.compile(r"^#{1,6}\s+.*revision\s+history", re.IGNORECASE),
    re.compile(r"^#{1,6}\s+.*change\s*log", re.IGNORECASE),
    re.compile(r"^#{1,6}\s+.*document\s+history", re.IGNORECASE),
    re.compile(
        r"^#{1,6}\s+(?:\d+[\.\d]*\s+)?changes\s+(?:since|from)\s+"
        r"(?:R\d|revision|the\s+previous|[PD]\d+R\d+|v\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^#{1,6}\s+.*changes\s+in\s+this\s+(?:revision|paper)",
        re.IGNORECASE,
    ),
    re.compile(r"^#{1,6}\s+R\d+(?!\.\s*[A-Za-z])\b", re.IGNORECASE),
    re.compile(r"^#{1,6}\s+Revision\s+\d+", re.IGNORECASE),
    re.compile(
        r"^#{1,6}\s+(?:\d+[\.\d]*\s+)?changes\s+in\s+(?:R\d|revision\s+\d)",
        re.IGNORECASE,
    ),
]

_BOLD_REVISION_RES: list[re.Pattern[str]] = [
    re.compile(r"^\*\*\s*Revision\s+History\s*\*\*\s*$", re.IGNORECASE),
    re.compile(r"^\*\*\s*Changelog\s*\*\*\s*$", re.IGNORECASE),
    re.compile(r"^\*\*\s*Document\s+history\s*\*\*\s*$", re.IGNORECASE),
]

_PAPER_OVERRIDES: dict[str, set[str]] = {
    "P0260": {"old revision history"},
}

_STEM_RE = re.compile(r"[PN]\d+", re.IGNORECASE)

# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

_REFERENCE_HEADING_RES: list[re.Pattern[str]] = [
    re.compile(
        r"^#{1,6}\s+(?:[\divxlcdm]+[\.\)]\s*)?\*?"
        r"(?:informative|normative)?\s*references\s*\*?"
        r"\s*[:{]?\s*(?:\{[^}]*\})?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^#{1,6}\s+(?:[\divxlcdm]+[\.\)]\s*)?\*?"
        r"bibliography\*?\s*(?:\{[^}]*\})?\s*$",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Acknowledgments
# ---------------------------------------------------------------------------

_ACKNOWLEDGMENT_HEADING_RE = re.compile(
    r"^#{1,6}\s+(?:[\divxlcdm]+[\.\)]\s*)?\*?"
    r"acknowledg[e]?ments?\s*\*?"
    r"\s*[:{]?\s*(?:\{[^}]*\})?\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------


def _get_overrides(paper_id: str | None) -> set[str]:
    if not paper_id:
        return set()
    m = _STEM_RE.match(paper_id)
    if not m:
        return set()
    return _PAPER_OVERRIDES.get(m.group(0).upper(), set())


def _is_revision_heading(
    line: str, overrides: set[str] = frozenset(),
) -> _HeadingKind:
    """YES: heading and revision history. NO: heading, not revision.
    UNKNOWN: not a heading. Bold standalone lines count as headings."""
    stripped = line.lstrip()

    if _HEADING_RE.match(stripped):
        for rx in _REVISION_HEADING_RES:
            if rx.match(stripped):
                return _HeadingKind.YES
        if overrides:
            low = stripped.lower()
            for token in overrides:
                if token in low:
                    return _HeadingKind.YES
        return _HeadingKind.NO

    for rx in _BOLD_REVISION_RES:
        if rx.match(stripped):
            return _HeadingKind.YES

    return _HeadingKind.UNKNOWN


def _is_reference_heading(line: str) -> _HeadingKind:
    """YES: heading that starts a references/bibliography section.
    NO: heading, not references. UNKNOWN: not a heading."""
    stripped = line.lstrip()
    if not _HEADING_RE.match(stripped):
        return _HeadingKind.UNKNOWN
    for rx in _REFERENCE_HEADING_RES:
        if rx.match(stripped):
            return _HeadingKind.YES
    return _HeadingKind.NO


def _is_acknowledgment_heading(line: str) -> _HeadingKind:
    """YES: heading that starts an acknowledgment section.
    NO: heading, not acknowledgment. UNKNOWN: not a heading."""
    stripped = line.lstrip()
    if not _HEADING_RE.match(stripped):
        return _HeadingKind.UNKNOWN
    if _ACKNOWLEDGMENT_HEADING_RE.match(stripped):
        return _HeadingKind.YES
    return _HeadingKind.NO


# ---------------------------------------------------------------------------
# Blanking
# ---------------------------------------------------------------------------


def _blank_section(
    lines: list[str],
    classifier,
) -> None:
    """Blank all lines belonging to sections identified by *classifier*.

    Multi-pass: after exiting a block on a NO heading, scanning continues
    from PRE back to IN, so split or interrupted sections are all caught.
    """
    in_section = False
    for i in range(len(lines)):
        kind = classifier(lines[i])
        if in_section:
            if kind is _HeadingKind.NO:
                in_section = False
            else:
                lines[i] = "\n"
        elif kind is _HeadingKind.YES:
            lines[i] = "\n"
            in_section = True


def blank_paper(source: str, paper_id: str | None = None) -> str:
    """Blank frontmatter, revision history, references, and acknowledgments.

    YAML frontmatter is always blanked. Non-prose sections (revision
    history, references, acknowledgments) are detected via tri-bool
    heading classifiers and blanked in independent passes so ordering
    within the document does not matter.
    """
    lines = source.splitlines(keepends=True)
    overrides = _get_overrides(paper_id)

    # Pass 1: blank YAML frontmatter
    saw_open = False
    for i in range(len(lines)):
        stripped = lines[i].lstrip()
        if _YAML_FENCE_RE.match(stripped):
            lines[i] = "\n"
            if saw_open:
                break
            saw_open = True
        elif not saw_open and stripped:
            break
        elif saw_open:
            lines[i] = "\n"

    # Pass 2: blank revision history (with paper-specific overrides)
    _blank_section(lines, lambda line: _is_revision_heading(line, overrides))

    # Pass 3: blank references
    _blank_section(lines, _is_reference_heading)

    # Pass 4: blank acknowledgments
    _blank_section(lines, _is_acknowledgment_heading)

    return "".join(lines)
