"""Span normalization - snap style boundaries to word edges.

When a bold or italic boundary falls inside a word (no whitespace
between adjacent spans with different styles), extend the styled
region to the word boundary. Monospace boundaries are exempt
because code boundaries are intentional.
"""

from dataclasses import replace

from .glyphs import GLYPH_FONT_SENTINEL
from .types import Block, Line, Span


def _is_style_span(span: Span) -> tuple[bool, bool]:
    """Return (bold, italic) flags for style-boundary comparison.

    Monospace spans are skipped by the caller, not by this helper.
    """
    return (span.bold, span.italic)


def _spans_touch(prev: Span, cur: Span) -> bool:
    """Check if two spans are adjacent with no whitespace between them."""
    if not prev.text or not cur.text:
        return False
    return not prev.text[-1].isspace() and not cur.text[0].isspace()


def _find_word_boundary_left(text: str) -> int:
    """Find the start of the last word in text (index of first char of word)."""
    i = len(text) - 1
    while i >= 0 and not text[i].isspace():
        i -= 1
    return i + 1


def _find_word_boundary_right(text: str) -> int:
    """Find the end of the first word in text (index after last char of word)."""
    i = 0
    while i < len(text) and not text[i].isspace():
        i += 1
    return i


def _try_left_merge(result: list[Span], i: int) -> bool:
    """Try to snap a right-side style boundary leftward to a word edge.

    Moves trailing text of result[i-1] onto result[i] when the two spans
    touch mid-word with different styles. Returns True if result was mutated
    and the caller should NOT advance i (an element may have been removed).
    """
    prev = result[i - 1]
    cur = result[i]

    if prev.monospace or cur.monospace:
        return False
    if (prev.font_name == GLYPH_FONT_SENTINEL
            or cur.font_name == GLYPH_FONT_SENTINEL):
        # A glyph placeholder is inert: never restyled, never a style
        # boundary anchor, so an adjacent styled run is not extended
        # across it. The span stays in place.
        return False
    if _is_style_span(prev) == _is_style_span(cur):
        return False
    if not _spans_touch(prev, cur):
        return False

    wb = _find_word_boundary_left(prev.text)
    if wb < len(prev.text) and wb > 0:
        fragment = prev.text[wb:]
        result[i - 1] = replace(prev, text=prev.text[:wb])
        result[i] = replace(cur, text=fragment + cur.text)
        if not result[i - 1].text:
            result.pop(i - 1)
            return True

    return False


def _try_right_merge(result: list[Span], i: int) -> bool:
    """Try to snap a left-side style boundary rightward to a word edge.

    Moves leading text of result[i+1] onto result[i] when the two spans
    touch mid-word with different styles. Returns True if result was mutated
    and the caller should NOT advance i (an element may have been removed).
    """
    cur = result[i]
    nxt = result[i + 1]

    if cur.monospace or nxt.monospace:
        return False
    if (cur.font_name == GLYPH_FONT_SENTINEL
            or nxt.font_name == GLYPH_FONT_SENTINEL):
        return False
    if _is_style_span(cur) == _is_style_span(nxt):
        return False
    if not _spans_touch(cur, nxt):
        return False

    wb = _find_word_boundary_right(nxt.text)
    if wb > 0 and wb < len(nxt.text):
        fragment = nxt.text[:wb]
        result[i] = replace(cur, text=cur.text + fragment)
        result[i + 1] = replace(nxt, text=nxt.text[wb:])
        if not result[i + 1].text:
            result.pop(i + 1)
            return True

    return False


def _normalize_line_spans(spans: list[Span]) -> list[Span]:
    """Normalize style boundaries within a single line's spans.

    If a style change (bold/italic) falls mid-word between two
    non-monospace spans, move text across the boundary so the style
    change aligns with a word edge.
    """
    if len(spans) < 2:
        return spans

    result = list(spans)

    i = 1
    while i < len(result):
        if not _try_left_merge(result, i):
            i += 1

    i = 0
    while i < len(result) - 1:
        if not _try_right_merge(result, i):
            i += 1

    return result


def normalize_spans(blocks: list[Block]) -> list[Block]:
    """Snap bold/italic style boundaries to word edges across all blocks.

    Monospace boundaries are exempt (code boundaries are intentional).
    """
    result = []
    for block in blocks:
        new_lines = []
        for line in block.lines:
            new_spans = _normalize_line_spans(line.spans)
            new_lines.append(Line(
                spans=new_spans,
                bbox=line.bbox,
                page_num=line.page_num,
            ))
        result.append(Block(
            lines=new_lines,
            bbox=block.bbox,
            page_num=block.page_num,
        ))
    return result
