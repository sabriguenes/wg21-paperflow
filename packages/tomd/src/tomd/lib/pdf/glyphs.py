#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Inline glyph-placeholder pass: sub-threshold raster glyphs -> U+FFFD.

Some PDFs embed emoji (and other symbols the chosen font cannot
represent) as tiny raster image XObjects, typically 8-18pt. The figure
path drops anything below :data:`tomd.lib.pdf.images.MIN_IMAGE_DIM_PT`
because such glyphs are not figures. When the emoji exists *only* as the
raster (no text-layer codepoint), it would vanish from the output
silently. This pass records each such glyph by injecting the Unicode
REPLACEMENT CHARACTER U+FFFD (``UNKNOWN_GLYPH``) into the body-text
stream at the glyph's reading-order position.

It does **not** try to recover *which* emoji it was: that is the
forward-compatible recovery feature (see
``notes/emoji-glyph-utf8-recovery-plan.md``), which swaps
:func:`glyph_to_char` for a Noto hash-match with ``UNKNOWN_GLYPH`` as
its fallback. The placement machinery here is reused unchanged.

Coincidence filter: a few papers ship both a text-layer emoji codepoint
*and* a raster glyph of the same emoji at the same on-page position. The
codepoint is already in the markdown, so injecting a placeholder there
would duplicate content (``X`` -> ``X<placeholder>``). Such rects are
detected by position against text-layer emoji (Unicode
``Emoji_Presentation`` property, plus VS16-qualified text-default emoji)
and skipped.

Library code returns/mutates data; the CLI owns persistence. This module
mutates the block lists passed to :func:`inject_glyph_spans` in place.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .emoji_data import EMOJI_PRESENTATION_RANGES
from .images import MIN_IMAGE_DIM_PT
from .types import Block, Line, Section, SectionKind, Span, compute_bbox

if TYPE_CHECKING:
    import pymupdf

_log = logging.getLogger(__name__)

# The character emitted for an unrecoverable embedded glyph. U+FFFD
# REPLACEMENT CHARACTER is Unicode's canonical "a character that could
# not be represented."
UNKNOWN_GLYPH = "�"

# Synthetic font name marking an injected glyph span. Shared with the
# recovery feature: mono.py / spans.py / structure.py key their
# inert-span short-circuits off this exact constant, so it must be one
# name, not a per-feature literal.
GLYPH_FONT_SENTINEL = "tomd-glyph"

# Combining codepoints that are not emoji themselves and must not
# contribute their own character bbox to the text-emoji set.
_VARIATION_SELECTOR_16 = 0xFE0F
_ZERO_WIDTH_JOINER = 0x200D

# y-distance at which a glyph is considered to belong to a text line
# (its centre within this many points of the line's y-range). Calibrated
# against N5007 where every glyph centre falls within ~1pt of a line.
_GLYPH_LINE_Y_TOLERANCE_PT = 3.0

# Centre-to-centre distance below which a glyph rect is treated as
# coincident with a text-layer emoji codepoint. Calibrated against
# P3786R1 (raster glyph centred within ~0.5pt of its text codepoint);
# 3pt covers font-metric rounding without colliding with neighbours.
_GLYPH_COINCIDENT_TOLERANCE_PT = 3.0

# Emitted at end-of-body when the pass fired. ``placeholders`` is the
# count of U+FFFD actually present in the emitted body (computed by
# emit.py after duplicate-paragraph collapse), so a reader grepping the
# file finds a matching number. ``skipped_coincident`` is the count of
# sub-threshold rects suppressed because a text-layer emoji codepoint
# already covered the position - those carry no body character, so they
# are reported here for traceability rather than counted in the body.
# ``skipped_code_section`` counts placeholders removed post-structure
# because the line they attached to became part of a CODE or TABLE
# section (a U+FFFD inside a fenced code block reads as a syntax error;
# a table renders structurally). Those carry no body character either.
GLYPH_PLACEHOLDER_MARKER_TEMPLATE = (
    "<!-- tomd:glyph-placeholders: placeholders={placeholders} "
    "skipped_coincident={skipped_coincident} "
    "skipped_code_section={skipped_code_section} -->"
)


@dataclass(frozen=True)
class GlyphCandidate:
    """One sub-threshold raster rect, before coincidence filtering.

    ``page`` is 1-based (matching ``pymupdf`` page numbering and
    :class:`tomd.lib.pdf.images.ExtractedImage.page`); the recovery
    feature extends this dataclass with a perceptual-hash field.
    """

    page: int
    bbox: tuple[float, float, float, float]


@dataclass
class GlyphPassStats:
    """Per-paper accounting for the glyph-placeholder pass.

    ``injected`` counts placeholders actually placed (orphan rects);
    ``skipped_coincident`` counts rects dropped because a text-layer
    emoji codepoint already covered the position; ``free_standing``
    counts injected placeholders that fell back to a synthesised
    single-line block (a subset of ``injected``); ``skipped_code_section``
    counts placeholders removed post-structure because the line they
    attached to became a CODE or TABLE section (a subset of the
    originally ``injected`` count).
    """

    injected: int = 0
    skipped_coincident: int = 0
    free_standing: int = 0
    skipped_code_section: int = 0

    @property
    def fired(self) -> bool:
        """True if the pass produced anything worth disclosing."""
        return bool(self.injected or self.skipped_coincident
                    or self.free_standing or self.skipped_code_section)


def _is_emoji_presentation(codepoint: int) -> bool:
    """True if the codepoint defaults to emoji (colour-glyph) rendering."""
    for start, end in EMOJI_PRESENTATION_RANGES:
        if start <= codepoint <= end:
            return True
        if codepoint < start:
            break  # ranges are sorted
    return False


def _bbox_centre(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def collect_glyph_candidates(page: "pymupdf.Page") -> list[GlyphCandidate]:
    """Return one :class:`GlyphCandidate` per sub-threshold image rect.

    Enumerates ``page.get_images(full=True)`` and, for each xref, every
    rect from ``page.get_image_rects(xref)`` whose smaller dimension is
    below :data:`MIN_IMAGE_DIM_PT` - the same threshold the figure path
    uses to drop these.

    Dedup is **by on-page position, not by xref**: one visual glyph is
    one placeholder. A single emoji xref drawn at N *distinct* positions
    yields N candidates (unlike :func:`finalize_extraction`, which would
    collapse them to one), but exact-duplicate rects at the *same*
    position - which ``get_image_rects`` emits repeatedly when an xref
    recurs in the resource dict - collapse to one. Two distinct glyphs
    cannot share an identical rect, so position dedup is lossless.
    """
    page_num = page.number + 1
    try:
        image_records = page.get_images(full=True)
    except Exception:  # pymupdf raises on malformed resource dicts
        _log.warning("page %d: get_images failed (glyph pass)",
                     page_num, exc_info=True)
        return []

    seen: set[tuple[float, float, float, float]] = set()
    candidates: list[GlyphCandidate] = []
    for record in image_records:
        xref = record[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            _log.warning("page %d xref %d: get_image_rects failed (glyph pass)",
                         page_num, xref, exc_info=True)
            continue
        for rect in rects:
            if min(rect.width, rect.height) >= MIN_IMAGE_DIM_PT:
                continue
            bbox = (float(rect.x0), float(rect.y0),
                    float(rect.x1), float(rect.y1))
            key = tuple(round(v, 1) for v in bbox)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(GlyphCandidate(page=page_num, bbox=bbox))
    return candidates


def collect_text_emoji_bboxes(
    page: "pymupdf.Page",
) -> list[tuple[float, float, float, float]]:
    """Return per-character bboxes of text-layer emoji on the page.

    Reads ``page.get_text("rawdict")`` (the only ``get_text`` shape that
    exposes per-character bboxes via the ``chars`` key; the plain
    ``dict`` shape gives span-level bboxes only). A character is a
    text-layer emoji when its codepoint has ``Emoji_Presentation=Yes``,
    or when it is immediately followed by U+FE0F (VS16), which forces
    emoji presentation of a text-default base. U+FE0F and U+200D (ZWJ)
    never contribute their own bbox.
    """
    try:
        raw = page.get_text("rawdict")
    except Exception:
        _log.warning("page %d: rawdict failed (glyph pass)",
                     page.number + 1, exc_info=True)
        return []

    bboxes: list[tuple[float, float, float, float]] = []
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            # Flatten the line's characters so a VS16 in a following span
            # still qualifies the preceding base character.
            chars: list[tuple[int, tuple]] = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    text = ch.get("c", "")
                    if not text:
                        continue
                    chars.append((ord(text), tuple(ch["bbox"])))
            for i, (codepoint, bbox) in enumerate(chars):
                if codepoint in (_VARIATION_SELECTOR_16, _ZERO_WIDTH_JOINER):
                    continue
                next_is_vs16 = (
                    i + 1 < len(chars)
                    and chars[i + 1][0] == _VARIATION_SELECTOR_16
                )
                if _is_emoji_presentation(codepoint) or next_is_vs16:
                    bboxes.append(bbox)
    return bboxes


def filter_coincident(
    candidates: list[GlyphCandidate],
    text_emoji_by_page: dict[int, list[tuple[float, float, float, float]]],
    *,
    tolerance: float = _GLYPH_COINCIDENT_TOLERANCE_PT,
) -> tuple[list[GlyphCandidate], int]:
    """Split candidates into (orphans, skipped_coincident_count).

    A candidate is coincident when its centre lies within ``tolerance``
    of any text-layer emoji centre **on the same page** - the text
    codepoint already carries the content, so a placeholder there would
    duplicate it. Coincidence is per-position, not per-paper: text emoji
    elsewhere on the page do not suppress an orphan glyph.
    """
    centres_by_page: dict[int, list[tuple[float, float]]] = {
        page: [_bbox_centre(b) for b in bboxes]
        for page, bboxes in text_emoji_by_page.items()
    }
    orphans: list[GlyphCandidate] = []
    skipped = 0
    for cand in candidates:
        cx, cy = _bbox_centre(cand.bbox)
        centres = centres_by_page.get(cand.page, ())
        if any(math.hypot(cx - ex, cy - ey) <= tolerance
               for ex, ey in centres):
            skipped += 1
        else:
            orphans.append(cand)
    return orphans, skipped


def glyph_to_char(cand: GlyphCandidate) -> str:
    """Return the character to emit for a glyph candidate.

    This is the swap seam for the recovery feature. Today every glyph
    becomes the ``UNKNOWN_GLYPH`` placeholder.
    """
    # TODO(emoji-recovery): replace with a Noto perceptual-hash match,
    # falling back to UNKNOWN_GLYPH when no confident match exists.
    # See notes/emoji-glyph-utf8-recovery-plan.md.
    return UNKNOWN_GLYPH


def _make_glyph_span(cand: GlyphCandidate, text: str) -> Span:
    """Build the synthetic span for one glyph placeholder.

    ``color=0`` (black) keeps wording.py's green/red HSV bands from ever
    classifying it as ins/del; ``font_name`` is the sentinel so mono /
    spans / structure passes treat it as inert.
    """
    x0, y0, x1, y1 = cand.bbox
    return Span(
        text=text,
        font_name=GLYPH_FONT_SENTINEL,
        font_size=max(0.0, y1 - y0),
        bold=False,
        italic=False,
        monospace=False,
        bbox=cand.bbox,
        origin=(x0, y1),
        color=0,
        link_url=None,
        wording_role=None,
    )


def _recompute_line_bbox(line: Line) -> None:
    boxes = [s.bbox for s in line.spans if any(s.bbox)]
    if boxes:
        line.bbox = compute_bbox(boxes)


def _recompute_block_bbox(block: Block) -> None:
    boxes = [ln.bbox for ln in block.lines if any(ln.bbox)]
    if boxes:
        block.bbox = compute_bbox(boxes)


def inject_glyph_spans(
    blocks: list[Block],
    candidates: list[GlyphCandidate],
    *,
    y_tolerance: float = _GLYPH_LINE_Y_TOLERANCE_PT,
) -> GlyphPassStats:
    """Inject one placeholder span per candidate into ``blocks`` in place.

    For each candidate (processed in ``(page, y0, x0)`` order) the line
    whose y-range contains the glyph centre receives a synthetic span at
    its x-ordered position; the containing :class:`Line` and
    :class:`Block` bboxes are recomputed. When several lines qualify on
    the y axis (multi-column layouts put two columns at the same y), the
    line whose **x-range covers the glyph centre** wins, so a right-column
    glyph attaches to the right-column line; ties (and the no-x-cover
    case) break on smallest y-distance. A candidate with no line within
    ``y_tolerance`` falls back to a synthesised single-line block appended
    to ``blocks`` (counted as ``free_standing``).

    Returns a :class:`GlyphPassStats` with ``injected`` and
    ``free_standing`` populated; ``skipped_coincident`` is the caller's
    to set from :func:`filter_coincident`. The caller runs this once per
    extraction path with the same orphan list; the two paths hold
    independent block objects, so mutating one never aliases the other.
    """
    stats = GlyphPassStats()
    by_page: dict[int, list[Block]] = {}
    for block in blocks:
        by_page.setdefault(block.page_num, []).append(block)

    ordered = sorted(candidates, key=lambda c: (c.page, c.bbox[1], c.bbox[0]))
    for cand in ordered:
        page0 = cand.page - 1  # Block.page_num is 0-based
        cx, cy = _bbox_centre(cand.bbox)
        text = glyph_to_char(cand)

        # Rank candidate lines by (x-covers-centre, y-distance): prefer a
        # line whose x-range contains the glyph centre (correct column in
        # a multi-column layout), then the smallest y-distance. Sort key
        # is (not x_covers, y_dist) so x-covering, then nearest, sorts first.
        best: tuple[bool, float, Block, Line] | None = None
        for block in by_page.get(page0, ()):
            for line in block.lines:
                top, bottom = line.bbox[1], line.bbox[3]
                if not (top - y_tolerance <= cy <= bottom + y_tolerance):
                    continue
                x_covers = line.bbox[0] <= cx <= line.bbox[2]
                dist = abs(cy - (top + bottom) / 2.0)
                key = (not x_covers, dist)
                if best is None or key < (not best[0], best[1]):
                    best = (x_covers, dist, block, line)

        span = _make_glyph_span(cand, text)
        if best is not None:
            _x_covers, _dist, block, line = best
            insert_at = len(line.spans)
            for idx, existing in enumerate(line.spans):
                if existing.bbox[0] > cand.bbox[0]:
                    insert_at = idx
                    break
            line.spans.insert(insert_at, span)
            _recompute_line_bbox(line)
            _recompute_block_bbox(block)
            stats.injected += 1
        else:
            line = Line(spans=[span], bbox=cand.bbox, page_num=page0)
            block = Block(lines=[line], bbox=cand.bbox, page_num=page0)
            blocks.append(block)
            by_page.setdefault(page0, []).append(block)
            stats.injected += 1
            stats.free_standing += 1

    return stats


def is_glyph_only_block(block: Block) -> bool:
    """True if every non-empty span in the block is a glyph placeholder.

    Used by structure.py to keep a synthesised free-standing placeholder
    block out of position-based list detection.
    """
    spans = [s for ln in block.lines for s in ln.spans if s.text.strip()]
    return bool(spans) and all(s.font_name == GLYPH_FONT_SENTINEL
                               for s in spans)


def drop_glyphs_in_code_and_tables(sections: list[Section]) -> int:
    """Remove placeholder spans that landed inside CODE/TABLE sections.

    Injection runs before structure, so a glyph may attach to a line that
    structure later classifies as CODE (rendered in a fenced block, where
    a U+FFFD reads as a syntax error) or that table detection folds into a
    TABLE (rendered structurally from ``columns``). Such placeholders are
    removed here in place and the count returned, so the caller can record
    it in ``GlyphPassStats.skipped_code_section`` and the marker can
    disclose the loss rather than letting a `�` corrupt code/table output.

    Membership-based: a glyph is "in" a section iff it attached to one of
    that section's lines or table cells. A glyph belongs to exactly one
    line, hence one section, so there is no bbox-overlap "straddle" and no
    overlap threshold applies (this differs from the vector-image
    structural filter, which tests bbox overlap because vectors are not
    line-anchored).
    """
    removed = 0

    def _strip(spans: list[Span]) -> list[Span]:
        nonlocal removed
        kept = [s for s in spans if s.font_name != GLYPH_FONT_SENTINEL]
        removed += len(spans) - len(kept)
        return kept

    for sec in sections:
        if sec.kind == SectionKind.CODE:
            for line in sec.lines:
                line.spans = _strip(line.spans)
        elif sec.kind == SectionKind.TABLE:
            # Table content renders from `columns` (rows of cells of
            # spans); strip there, and from any `lines` the table kept.
            for row in sec.columns:
                for cell in row:
                    cell[:] = _strip(cell)
            for line in sec.lines:
                line.spans = _strip(line.spans)
    return removed
