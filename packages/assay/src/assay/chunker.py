#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Heading-based recursive paper chunker.

Splits paper markdown into sections by heading structure and splits
oversized sections on bold-numbered subsection patterns. All size
parameters are in characters; the caller converts from tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_BOLD_SUBSECTION_RE = re.compile(r"^\*\*\d+(?:\.\d+)+\*\*")


@dataclass
class Section:
    """A leaf section produced by heading-based recursive chunking."""

    heading: str
    level: int
    start_line: int
    end_line: int
    char_count: int


@dataclass
class _TreeSection:
    heading: str
    level: int
    start_line: int
    end_line: int
    char_count: int
    children: list[_TreeSection] = field(default_factory=list)


def chunk_paper(
    source: str,
    *,
    max_chars: int = 6500,
) -> list[Section]:
    """Chunk paper markdown into leaf sections by heading structure.

    Parameters:
        source: Full paper markdown text (already blanked by the caller).
        max_chars: Maximum characters per chunk before recursing into
            children. Sections exceeding this that have no heading-based
            children will attempt a bold-subsection split.

    All size parameters are in characters. Use ``pipeline.tokens_to_chars()``
    to convert from a token budget.
    """
    lines = source.splitlines()
    headings = _parse_headings(lines)

    if not headings:
        return [Section(
            heading="(untitled)",
            level=1,
            start_line=1,
            end_line=len(lines),
            char_count=len(source),
        )]

    top_level = min(lv for _, lv, _ in headings)
    tree = _build_tree(lines, headings, 0, len(lines), top_level - 1)
    leaves = _flatten(tree, max_chars, lines)
    leaves = _coalesce(leaves, max_chars, lines)

    return [
        Section(
            heading=s.heading,
            level=s.level,
            start_line=s.start_line,
            end_line=s.end_line,
            char_count=s.char_count,
        )
        for s in leaves
    ]


def _parse_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Extract (line_index, level, title) for all markdown headings."""
    headings: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2).strip()))
    return headings


def _char_count(lines: list[str], start: int, end: int) -> int:
    """Character count for a line range (including newlines)."""
    return sum(len(lines[i]) + 1 for i in range(start, min(end, len(lines))))


def _build_tree(
    lines: list[str],
    headings: list[tuple[int, int, str]],
    start: int,
    end: int,
    parent_level: int,
) -> list[_TreeSection]:
    children_hdgs = [
        (ln, lv, t)
        for ln, lv, t in headings
        if start < ln < end and lv == parent_level + 1
    ]
    if not children_hdgs:
        return []

    sections: list[_TreeSection] = []

    first_child_line = children_hdgs[0][0]
    prefix_chars = _char_count(lines, start, first_child_line)
    non_blank_prefix = sum(1 for i in range(start, first_child_line) if lines[i].strip())
    if non_blank_prefix > 0 and prefix_chars > 0:
        prefix_heading = next(
            (t for ln, lv, t in headings if ln == start),
            "(preamble)",
        )
        sections.append(_TreeSection(
            heading=prefix_heading,
            level=parent_level + 1,
            start_line=start + 1,
            end_line=first_child_line,
            char_count=prefix_chars,
            children=[],
        ))

    for idx, (ln, lv, title) in enumerate(children_hdgs):
        child_end = children_hdgs[idx + 1][0] if idx + 1 < len(children_hdgs) else end
        sec = _TreeSection(
            heading=title,
            level=lv,
            start_line=ln + 1,
            end_line=child_end,
            char_count=_char_count(lines, ln, child_end),
            children=_build_tree(lines, headings, ln, child_end, lv),
        )
        sections.append(sec)
    return sections


def _flatten(
    sections: list[_TreeSection], max_chars: int, lines: list[str]
) -> list[_TreeSection]:
    """Flatten tree to leaves, recursing into children when oversized."""
    leaves: list[_TreeSection] = []
    for sec in sections:
        if sec.char_count <= max_chars or not sec.children:
            if sec.char_count > max_chars and not sec.children:
                split = _split_bold_subsections(sec, lines)
                if split:
                    leaves.extend(split)
                    continue
            leaves.append(_TreeSection(
                heading=sec.heading,
                level=sec.level,
                start_line=sec.start_line,
                end_line=sec.end_line,
                char_count=sec.char_count,
                children=[],
            ))
        else:
            leaves.extend(_flatten(sec.children, max_chars, lines))
    return leaves


def _coalesce(
    leaves: list[_TreeSection], max_chars: int, lines: list[str]
) -> list[_TreeSection]:
    """Fold small adjacent sections into predecessor if combined stays under max_chars."""
    if not leaves:
        return leaves

    result: list[_TreeSection] = [_TreeSection(
        heading=leaves[0].heading,
        level=leaves[0].level,
        start_line=leaves[0].start_line,
        end_line=leaves[0].end_line,
        char_count=leaves[0].char_count,
    )]
    for leaf in leaves[1:]:
        prev = result[-1]
        combined = _char_count(lines, prev.start_line - 1, leaf.end_line)
        if combined <= max_chars:
            prev.end_line = leaf.end_line
            prev.char_count = combined
            prev.heading = prev.heading + " + " + leaf.heading
        else:
            result.append(_TreeSection(
                heading=leaf.heading,
                level=leaf.level,
                start_line=leaf.start_line,
                end_line=leaf.end_line,
                char_count=leaf.char_count,
            ))
    return result


def _split_bold_subsections(
    sec: _TreeSection, lines: list[str]
) -> list[_TreeSection] | None:
    """Split a section on bold-numbered subsection patterns.

    Detects lines like **3.5.1** **Title** and splits there.
    Returns None if no such patterns found.
    """
    start_idx = sec.start_line - 1
    end_idx = sec.end_line

    split_points: list[tuple[int, str]] = []
    for i in range(start_idx + 1, end_idx):
        if i < len(lines) and _BOLD_SUBSECTION_RE.match(lines[i]):
            title = lines[i].replace("**", "").strip()
            split_points.append((i, title))

    if not split_points:
        return None

    result: list[_TreeSection] = []

    first_end = split_points[0][0]
    if first_end > start_idx + 1:
        result.append(_TreeSection(
            heading=sec.heading,
            level=sec.level,
            start_line=sec.start_line,
            end_line=first_end,
            char_count=_char_count(lines, start_idx, first_end),
            children=[],
        ))

    for idx, (ln, title) in enumerate(split_points):
        sub_end = split_points[idx + 1][0] if idx + 1 < len(split_points) else end_idx
        result.append(_TreeSection(
            heading=title,
            level=sec.level + 1,
            start_line=ln + 1,
            end_line=sub_end,
            char_count=_char_count(lines, ln, sub_end),
            children=[],
        ))

    return result if len(result) > 1 else None


