"""PDF to Markdown converter - pipeline entry point."""

import fitz
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .cleanup import (get_edge_items, detect_repeating, strip_repeating,
                      cleanup_text, find_hidden_regions, strip_hidden_blocks)
from .extract import extract_mupdf, extract_spatial, collect_links, attach_links
from .images import (
    ExtractedImage,
    VectorUncertaintyStats,
    _VectorExtractionStats,
    extract_page_images,
    finalize_extraction,
)
from .vector_images import extract_page_vector_images
from .glyphs import (
    GlyphPassStats,
    collect_glyph_candidates,
    collect_text_emoji_bboxes,
    drop_glyphs_in_code_and_tables,
    filter_coincident,
    inject_glyph_spans,
)
from .mono import propagate_monospace
from .figures import detect_figure_regions
from .wording import classify_wording, collect_line_drawings
from .spans import normalize_spans
from .structure import (compare_extractions, structure_body,
                        _is_known_section, _TITLE_PID_PREFIX_RE)
from ..metadata_yaml.extract import (
    extract_metadata as _extract_metadata_yaml,
    apply_pdf_metadata_fallbacks as _apply_pdf_metadata_fallbacks,
)
from .table import detect_tables, exclude_table_regions
from .wg21 import extract_metadata_from_blocks
from .emit import emit_markdown, emit_prompts
from .types import (
    Confidence,
    Section,
    SectionKind,
    SkipReason,
    is_readable,
)
from ..toc import find_toc_indices, has_dot_leader, _is_toc_label
from ..metadata_yaml.strip import (
    strip_metadata_headings as _strip_metadata_headings_new,
    strip_pre_heading_fragments as _strip_pre_heading_fragments,
    strip_pre_content_paragraphs as _strip_pre_content_paragraphs,
)
from ..body.abstract import dedup_abstract as _dedup_abstract_new
from ..body.abstract import promote_abstract_from_uncertain as _promote_abstract_from_uncertain
from ..body.abstract import reorder_abstract_in_uncertain as _reorder_abstract_in_uncertain
from ..body.abstract import rescue_stranded_abstract_body as _rescue_stranded_abstract_body
from ..body.abstract import strip_metadata_from_uncertain as _strip_metadata_from_uncertain

from .docling_backend import (
    docling_available as _docling_available,
    extract_docling_tables as _extract_docling_tables,
    enrich_tables_with_docling as _enrich_tables_with_docling,
    absorb_cross_page_spec_rows as _absorb_cross_page_spec_rows,
    discover_tables_with_docling as _discover_tables_with_docling,
)

__all__ = ["run_pipeline", "PipelineResult", "ExtractedImage"]

_log = logging.getLogger(__name__)

_STANDALONE_PAGE_RE = re.compile(r'^\d{1,4}$')
_SECTION_NUM_START_RE = re.compile(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+)(?:\s|$)")
_TOC_X_TOLERANCE = 5.0
_TOC_BODY_PROTECT_MIN_WORDS = 10
_NUMBERED_LINE_RE = re.compile(r"^\s*\d+[\.\)]\s+\S")
_BARE_PAGE_NUM_RE = re.compile(r"^\s*\d{1,3}\s*$")
_LABEL_TOC_MIN_NUMBERED_LINES = 3
_TOC_LABEL_MAX_WORDS = 4

# Two-column detection: minimum gap (points) between the right edge of left-
# column blocks and the left edge of right-column blocks to declare two
# columns.  Typical column gutters are 15-30pt; single-column text with
# indented code can have 10pt shifts.  20pt sits comfortably between.
_COLUMN_GAP_MIN = 20.0

# Minimum fraction of total blocks that the smaller "column" must have to
# accept a two-column split.  Right-aligned decorative labels (e.g.
# "ABSTRACT", "CONTENTS") create a tiny cluster of 2-3 blocks that triggers
# a false column split against 20+ body blocks on the other side.
_COLUMN_MIN_FRACTION = 0.20



def _toc_structural_hints(sections) -> list[bool]:
    """Mark sections that structurally resemble TOC entries.

    A section qualifies when its second non-empty text line is a bare page
    number AND its x coordinate clusters with other such candidates (the
    right-aligned page-number column). Used as a fallback for headingless
    wording papers where find_toc_indices would otherwise get an empty
    headings set.
    """
    candidates: list[tuple[int, float | None]] = []
    for i, sec in enumerate(sections):
        lines = [ln.strip() for ln in sec.text.split("\n") if ln.strip()]
        if len(lines) >= 2 and _STANDALONE_PAGE_RE.match(lines[1]):
            x = None
            non_empty = [ln for ln in sec.lines if ln.text.strip()]
            if len(non_empty) >= 2 and non_empty[1].spans:
                x = non_empty[1].spans[0].bbox[0]
            candidates.append((i, x))

    if not candidates:
        return [False] * len(sections)

    xs = sorted(x for _, x in candidates if x is not None)
    med_x = xs[len(xs) // 2] if xs else None

    result = [False] * len(sections)
    for i, x in candidates:
        if med_x is None:
            if x is None:
                result[i] = True
        elif x is None or abs(x - med_x) <= _TOC_X_TOLERANCE:
            result[i] = True
    return result


def _detect_column_split(blocks: list, page_width: float) -> float | None:
    """Find the x-coordinate that separates two text columns on a page.

    Returns the split point (midpoint of the inter-column gap) when two
    distinct column clusters exist, or None for single-column pages.
    Blocks whose x-midpoint falls left of the split are column 0 (left);
    those right of it are column 1 (right).
    """
    if len(blocks) < 2:
        return None
    x_mids = sorted((b.bbox[0] + b.bbox[2]) / 2 for b in blocks)
    # Find the largest gap between consecutive x-midpoints.
    best_gap = 0.0
    best_split = 0.0
    for i in range(len(x_mids) - 1):
        gap = x_mids[i + 1] - x_mids[i]
        if gap > best_gap:
            best_gap = gap
            best_split = (x_mids[i] + x_mids[i + 1]) / 2
    if best_gap < _COLUMN_GAP_MIN:
        return None
    # Sanity: both sides of the split must have blocks, and the split
    # should be roughly in the middle third of the page.
    left_count = sum(1 for x in x_mids if x < best_split)
    right_count = len(x_mids) - left_count
    if left_count < 2 or right_count < 2:
        return None
    if min(left_count, right_count) / (left_count + right_count) < _COLUMN_MIN_FRACTION:
        return None
    if best_split < page_width * 0.25 or best_split > page_width * 0.75:
        return None
    # Validate with left-edge (x0) clustering: in genuine two-column
    # layouts the right column's left edges sit near the page center,
    # not near the left margin.  Short single-column lines produce low
    # x-midpoints but keep their x0 near the left margin, so an x0
    # check rejects those false positives.
    left_x0s = [b.bbox[0] for b in blocks if (b.bbox[0] + b.bbox[2]) / 2 < best_split]
    right_x0s = [b.bbox[0] for b in blocks if (b.bbox[0] + b.bbox[2]) / 2 >= best_split]
    if left_x0s and right_x0s:
        max_left_x0 = max(left_x0s)
        min_right_x0 = min(right_x0s)
        if min_right_x0 - max_left_x0 < _COLUMN_GAP_MIN:
            return None
    return best_split


def _column_aware_sort(blocks: list, page_widths: dict[int, float]) -> None:
    """Sort blocks by reading order: page, then column (if two-column), then y.

    For two-column pages the left column is emitted entirely before the
    right column, preserving within-column y-order.  Single-column pages
    fall back to simple y-midpoint sorting (the P3625R1 fix).
    """
    page_blocks: dict[int, list] = {}
    for b in blocks:
        page_blocks.setdefault(b.page_num, []).append(b)

    splits: dict[int, float | None] = {}
    for pg, pblocks in page_blocks.items():
        pw = page_widths.get(pg, 612.0)
        splits[pg] = _detect_column_split(pblocks, pw)

    def sort_key(b):
        pg = b.page_num
        y_mid = (b.bbox[1] + b.bbox[3]) / 2
        split = splits.get(pg)
        if split is not None:
            x_mid = (b.bbox[0] + b.bbox[2]) / 2
            col = 0 if x_mid < split else 1
            return (pg, col, y_mid)
        return (pg, 0, y_mid)

    blocks.sort(key=sort_key)



def _get_page0_text_colors(page) -> dict[float, float]:
    """Map y-positions to text lightness using texttrace space-color proxy.

    Type 3 fonts report black for all glyphs. Space characters (type=0)
    leak the true graphics-state fill color. Returns {rounded_y: lightness}
    where lightness is 0.0 (black) to 1.0 (white).
    """
    colors: dict[float, float] = {}
    for span in page.get_texttrace():
        if span.get("type") != 0:
            continue
        color = span.get("color")
        if color is None:
            continue
        chars = span.get("chars", [])
        if not chars:
            continue
        y = round(chars[0][2][1])
        if isinstance(color, (tuple, list)) and len(color) >= 3:
            lightness = sum(color[:3]) / 3.0
        elif isinstance(color, (int, float)):
            lightness = float(color)
        else:
            continue
        colors[y] = lightness
    return colors


_SLIDE_DECK_MAX_WIDTH = 600
_SLIDE_DECK_LANDSCAPE_FRACTION = 0.8
_STANDARDS_DRAFT_MIN_PAGES = 200


def _is_slide_deck(doc) -> bool:
    """Detect presentation / slide-deck PDFs from page geometry.

    Two rules, either is sufficient:

    1. At least :data:`_SLIDE_DECK_LANDSCAPE_FRACTION` of pages are
       landscape AND narrower than :data:`_SLIDE_DECK_MAX_WIDTH`. This
       catches classic Beamer / Keynote 4:3 decks where the small
       page width is itself diagnostic (a portrait title page is
       fine).
    2. Every page in the document is landscape. This catches modern
       widescreen decks (720x405, 960x540, 1024x768, 1280x720,
       1920x1080) that exceed the width cap of rule 1. Strict
       all-landscape avoids false positives on mixed-orientation
       technical documents (e.g. N5028 - 105/114 landscape A4 pages,
       but the 9 portrait pages keep it out of the deck bucket).
    """
    if doc.page_count == 0:
        return False
    # Rule 1: small-width landscape dominant.
    small_landscape_count = 0
    all_landscape = True
    for pg_num in range(doc.page_count):
        r = doc[pg_num].rect
        if r.width <= r.height:
            all_landscape = False
        elif r.width < _SLIDE_DECK_MAX_WIDTH:
            small_landscape_count += 1
    if small_landscape_count / doc.page_count >= _SLIDE_DECK_LANDSCAPE_FRACTION:
        return True
    # Rule 2: every page is landscape (any width).
    return all_landscape


def _is_standards_draft(doc) -> bool:
    """Detect standards drafts by page count (>= 200 pages)."""
    return doc.page_count >= _STANDARDS_DRAFT_MIN_PAGES


def _make_image_section(img: ExtractedImage) -> Section:
    """Build a :class:`SectionKind.IMAGE` Section from one extraction record.

    Section.text and the per-path text fields are intentionally empty
    (plan N4): an image has no text content, and Section.text drives
    consumers like ``qa.compute_metrics`` whose word counts would be
    inflated by alt text. The canonical alt-text source is
    ``image_ref.suggested_alt``, read by the emit step.

    Confidence keys off ``img.source``: raster XObjects carry HIGH
    (the bytes are unambiguously a figure), vector clusters carry
    MEDIUM (the heuristic that grouped path operators into a figure
    is single-signal and uncertain - see the vector-extraction plan
    section 1.2).
    """
    confidence = Confidence.MEDIUM if img.source == "vector" else Confidence.HIGH
    return Section(
        kind=SectionKind.IMAGE,
        text="",
        confidence=confidence,
        page_num=img.page - 1,    # Section.page_num is 0-based
        image_ref=img,
    )


# Threshold for the structural-overlap filter (see
# :func:`_filter_vector_images_against_structural`). A vector
# ExtractedImage whose bbox overlaps a TABLE or CODE section by at
# least this fraction of the image area is treated as a duplicate of
# the structural representation and dropped. 0.5 is conservative
# enough that a real figure with a stray code line at one edge stays
# kept, while catching the calibrated false-positive cases:
#
# - P4003R1 page 8 comparison table (4 per-column vector PNGs
#   each ~100% overlapping the detected TABLE bbox).
# - P4003R1 pages 67 and 69 code-block backgrounds (vector PNGs
#   ~100% overlapping the CODE section the text path produces).
_STRUCTURAL_OVERLAP_THRESHOLD = 0.5

# Thresholds for the vector-image dedup filter (see
# :func:`_filter_overlapping_vector_images`). A small vector image
# whose bbox overlaps a larger vector image's bbox by at least this
# fraction of the small image's area AND whose area is at most
# :data:`_OVERLAPPING_VECTOR_AREA_RATIO` of the larger image's area
# is treated as a detail crop of content already in the larger
# image and dropped. Calibrated against P4003R1 page 13's fig13-2
# (87x90pt small image, 40% inside fig13-1 at 481x256pt, area
# ratio 6.3% - clearly a redundant detail crop).
#
# The area-ratio guard prevents two genuinely adjacent figures of
# similar size (e.g. side-by-side panels) from being deduped just
# because their bboxes happen to overlap.
_OVERLAPPING_VECTOR_THRESHOLD = 0.30
_OVERLAPPING_VECTOR_AREA_RATIO = 0.20

# Threshold for the text-inside-vector dedup filter (see
# :func:`_filter_sections_inside_vector_images`). A non-structural
# section whose bbox is at least this fraction inside a surviving
# vector image's bbox is treated as duplicate content (the same
# text is already rasterised into the PNG) and dropped from the
# section list. TABLE, CODE, and IMAGE sections are never filtered
# - they are structural representations that stay regardless.
_SECTION_INSIDE_VECTOR_THRESHOLD = 0.5


def _section_bbox(
    sec: Section,
) -> tuple[float, float, float, float] | None:
    """Return the union bbox of a section's lines, or None if it has none."""
    if not sec.lines:
        return None
    bbox = sec.lines[0].bbox
    for line in sec.lines[1:]:
        bbox = (
            min(bbox[0], line.bbox[0]),
            min(bbox[1], line.bbox[1]),
            max(bbox[2], line.bbox[2]),
            max(bbox[3], line.bbox[3]),
        )
    return bbox


def _bbox_overlap_fraction(
    image_bbox: tuple[float, float, float, float],
    section_bbox: tuple[float, float, float, float],
) -> float:
    """Intersection area divided by ``image_bbox`` area.

    Returns 0.0 when image_bbox has non-positive area. Used to decide
    whether a vector image is "mostly inside" a structural section.
    """
    ix0, iy0, ix1, iy1 = image_bbox
    sx0, sy0, sx1, sy1 = section_bbox
    overlap_w = max(0.0, min(ix1, sx1) - max(ix0, sx0))
    overlap_h = max(0.0, min(iy1, sy1) - max(iy0, sy0))
    image_area = (ix1 - ix0) * (iy1 - iy0)
    if image_area <= 0:
        return 0.0
    return (overlap_w * overlap_h) / image_area


def _filter_vector_images_against_structural(
    images: list[ExtractedImage],
    sections: list[Section],
    *,
    threshold: float = _STRUCTURAL_OVERLAP_THRESHOLD,
) -> tuple[list[ExtractedImage], list[Section], int]:
    """Drop vector ExtractedImage records (and their IMAGE sections)
    that overlap a TABLE or CODE section by more than ``threshold``.

    A vector PNG that lands on top of a structural section (TABLE or
    CODE) duplicates content the markdown already renders structurally
    (as a markdown table or fenced code block). Removing the vector
    duplicate keeps the output clean and resolves the calibrated
    false-positive classes documented in:

    - bug-p4003r1-pg8-table-extraction.md (per-column vector PNGs of
      a comparison table).
    - improvements.md section 4.7 (vector PNGs duplicating code-block
      content on P4003R1 pages 67 and 69).

    Raster images are not filtered; an embedded raster image that
    overlaps a code block or table is presumed intentional (annotated
    diagram, embedded screenshot).

    Returns ``(filtered_images, filtered_sections, dropped_count)``.
    The dropped count is used to adjust the per-paper vector
    uncertainty marker's ``kept`` value so its disclosure matches the
    actual markdown content.
    """
    # Per-page body x-range from non-table, non-image content. Used to
    # inflate TABLE bboxes for the overlap test: table Section bboxes are
    # the union of cell-text bboxes, which are tight to glyphs and ignore
    # column padding. A vector cluster sitting in a table's column-padding
    # whitespace (canonical case: P4003R1 page 72 right column, vector at
    # x=326-538 vs table text x_end=409) would otherwise overlap below
    # the 0.5 threshold and survive. Stretching to the body x-range
    # restores the full visual column extent.
    body_x_by_page: dict[int, tuple[float, float]] = {}
    for sec in sections:
        if sec.kind in (SectionKind.TABLE, SectionKind.IMAGE):
            continue
        page = sec.page_num + 1
        for line in sec.lines:
            bx0, bx1 = line.bbox[0], line.bbox[2]
            prev = body_x_by_page.get(page)
            if prev is None:
                body_x_by_page[page] = (bx0, bx1)
            else:
                body_x_by_page[page] = (min(prev[0], bx0), max(prev[1], bx1))

    structural_bboxes_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    for sec in sections:
        if sec.kind not in (SectionKind.TABLE, SectionKind.CODE):
            continue
        bbox = _section_bbox(sec)
        if bbox is None:
            continue
        # ExtractedImage.page is 1-based; Section.page_num is 0-based.
        page = sec.page_num + 1
        if sec.kind == SectionKind.TABLE:
            body_x = body_x_by_page.get(page)
            if body_x is not None:
                bbox = (min(bbox[0], body_x[0]), bbox[1],
                        max(bbox[2], body_x[1]), bbox[3])
        structural_bboxes_by_page.setdefault(page, []).append(bbox)

    if not structural_bboxes_by_page:
        return images, sections, 0

    dropped_ids: set[int] = set()
    kept_images: list[ExtractedImage] = []
    for im in images:
        if im.source != "vector":
            kept_images.append(im)
            continue
        page_bboxes = structural_bboxes_by_page.get(im.page, ())
        if any(
            _bbox_overlap_fraction(im.bbox, b) >= threshold
            for b in page_bboxes
        ):
            dropped_ids.add(id(im))
            continue
        kept_images.append(im)

    if not dropped_ids:
        return images, sections, 0

    kept_sections = [
        s for s in sections
        if not (
            s.kind == SectionKind.IMAGE
            and s.image_ref is not None
            and id(s.image_ref) in dropped_ids
        )
    ]
    return kept_images, kept_sections, len(dropped_ids)


def _filter_overlapping_vector_images(
    images: list[ExtractedImage],
    sections: list[Section],
    *,
    overlap_threshold: float = _OVERLAPPING_VECTOR_THRESHOLD,
    area_ratio: float = _OVERLAPPING_VECTOR_AREA_RATIO,
) -> tuple[list[ExtractedImage], list[Section], int]:
    """Drop vector images that are detail crops of larger vector images.

    A vector image A is treated as a redundant detail crop of a
    larger vector image B when:

    - A and B are on the same page,
    - A's bbox overlaps B's bbox by at least ``overlap_threshold``
      of A's area, AND
    - A's area is at most ``area_ratio`` of B's area (i.e. A is
      MUCH smaller than B - the area-ratio guard distinguishes a
      detail crop from a genuinely adjacent similar-sized figure).

    Resolves the calibrated false-positive case where a small
    cluster ends up adjacent to a much larger merged figure on the
    same page and shows duplicate content (P4003R1 page 13's
    fig13-2 "run_async legend" box overlapping fig13-1 main diagram
    at ~40% of fig13-2's area, area ratio 6.3%).

    The corresponding IMAGE section in ``sections`` (placed by
    :func:`_insert_image_sections`) is also removed so the markdown
    doesn't reference a dropped image.

    Returns ``(filtered_images, filtered_sections, dropped_count)``.
    """
    # Group vector images by page; sort each page's list by area
    # descending so we test smaller-vs-larger pairs.
    vectors_by_page: dict[int, list[int]] = {}
    for i, im in enumerate(images):
        if im.source != "vector":
            continue
        vectors_by_page.setdefault(im.page, []).append(i)

    def _area(im: ExtractedImage) -> float:
        return (im.bbox[2] - im.bbox[0]) * (im.bbox[3] - im.bbox[1])

    dropped_ids: set[int] = set()
    for page_indices in vectors_by_page.values():
        # Sort descending by area so the largest is considered first.
        sorted_idx = sorted(
            page_indices, key=lambda i: _area(images[i]), reverse=True,
        )
        # Larger images are "potential containers"; smaller images may
        # be detail crops. Compare each smaller against each kept
        # larger one. ``kept_local`` is the surviving subset on this
        # page that smaller images get tested against.
        kept_local: list[int] = []
        for i in sorted_idx:
            im = images[i]
            im_area = _area(im)
            is_dup = False
            for j in kept_local:
                larger = images[j]
                larger_area = _area(larger)
                if im_area > area_ratio * larger_area:
                    # Not "much smaller" than larger - keep both.
                    continue
                if _bbox_overlap_fraction(im.bbox, larger.bbox) >= overlap_threshold:
                    is_dup = True
                    break
            if is_dup:
                dropped_ids.add(id(im))
            else:
                kept_local.append(i)

    if not dropped_ids:
        return images, sections, 0

    kept_images = [im for im in images if id(im) not in dropped_ids]
    kept_sections = [
        s for s in sections
        if not (
            s.kind == SectionKind.IMAGE
            and s.image_ref is not None
            and id(s.image_ref) in dropped_ids
        )
    ]
    return kept_images, kept_sections, len(dropped_ids)


def _filter_sections_inside_vector_images(
    images: list[ExtractedImage],
    sections: list[Section],
    *,
    threshold: float = _SECTION_INSIDE_VECTOR_THRESHOLD,
) -> list[Section]:
    """Drop lines inside surviving vector image bboxes from non-structural sections.

    The vector image already shows the line's text content as
    rasterised pixels (a label rendered inside a diagram is baked
    into the PNG by ``page.get_pixmap``), so re-emitting the same
    line as body markdown produces visible duplication. P4003R1
    page 13's diagram labels ("I/O operation child task parent task
    run_async / handle() / set_environment(env) / ...") are the
    calibrated case.

    Operates line-by-line rather than section-by-section because the
    structure pipeline often joins a diagram's leaked labels with
    surrounding prose into one PARAGRAPH section. A whole-section
    filter would over-drop the prose too. Per-line filtering keeps
    bullet text adjacent to a figure while dropping the figure's own
    labels.

    Sections whose lines are ALL filtered out are removed entirely.
    Sections that lose SOME lines get a rebuilt ``text`` (simple
    newline-join of surviving lines' text); if every line in the
    section survives, the section is returned unchanged.

    TABLE, CODE, and IMAGE sections are always kept verbatim - they
    are structural representations that should stay regardless of
    overlap (and the structural-overlap filter at
    :func:`_filter_vector_images_against_structural` has already
    handled the reverse case of dropping vectors that duplicate
    structural sections).
    """
    image_bboxes_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    for im in images:
        if im.source != "vector":
            continue
        image_bboxes_by_page.setdefault(im.page, []).append(im.bbox)
    if not image_bboxes_by_page:
        return sections

    from dataclasses import replace
    structural_kinds = {SectionKind.TABLE, SectionKind.CODE, SectionKind.IMAGE}
    kept: list[Section] = []
    for sec in sections:
        if sec.kind in structural_kinds:
            kept.append(sec)
            continue
        if not sec.lines:
            kept.append(sec)
            continue
        page = sec.page_num + 1  # 1-based to match ExtractedImage.page
        page_bboxes = image_bboxes_by_page.get(page, ())
        if not page_bboxes:
            kept.append(sec)
            continue

        kept_lines = []
        for line in sec.lines:
            line_bbox = line.bbox
            if line_bbox == (0, 0, 0, 0):
                # No bbox info - can't decide, keep.
                kept_lines.append(line)
                continue
            if any(
                _bbox_overlap_fraction(line_bbox, ib) >= threshold
                for ib in page_bboxes
            ):
                continue
            kept_lines.append(line)

        if len(kept_lines) == len(sec.lines):
            kept.append(sec)
            continue
        if not kept_lines:
            continue  # all lines dropped - section gone
        new_text = "\n".join(line.text for line in kept_lines)
        kept.append(replace(sec, lines=kept_lines, text=new_text))
    return kept


def _insert_image_sections(
    sections: list[Section],
    images: list[ExtractedImage],
) -> list[Section]:
    """Insert IMAGE sections into a sorted section list at the right y-position.

    Mirrors the table-insertion logic: walk the existing sections by
    ``(page_num, first_line.bbox[1])`` and slot each IMAGE in just
    before the first section that comes after it. Appends if the
    image is at end-of-document.
    """
    out = list(sections)
    for img in images:
        img_sec = _make_image_section(img)
        inserted = False
        for i, sec in enumerate(out):
            if sec.page_num > img_sec.page_num:
                out.insert(i, img_sec)
                inserted = True
                break
            if (sec.page_num == img_sec.page_num
                    and sec.lines
                    and sec.lines[0].bbox[1] > img.bbox[1]):
                out.insert(i, img_sec)
                inserted = True
                break
        if not inserted:
            out.append(img_sec)
    return out


@dataclass
class PipelineResult:
    """Full output of the PDF conversion pipeline, used for QA scoring.

    Image fields:

    - ``images``: up to ``_MAX_IMAGES_PER_PAPER`` :class:`ExtractedImage`
      records, sorted by ``(page, bbox.y0, bbox.x0)``. The CLI consumes
      this list to persist bytes via ``backend.write_paper_image``.
      Always empty when ``skipped`` is True - early-exit paths discard
      any partial extraction state to avoid orphan PNGs on disk without
      a referencing markdown.
    - ``source_image_count``: unique-xref total for the source,
      regardless of the cap. Drives the CLI's "kept M of N" line.
    - ``images_truncated``: True iff ``source_image_count`` exceeded
      the cap and the emit step appended the truncation HTML comment.
    """
    md: str = ""
    prompts: list[str] | None = None
    sections: list[Section] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    page_count: int = 0
    nesting_corrections: int = 0
    readable: bool = True
    skipped: bool = False
    skip_reason: SkipReason | None = None
    images: list[ExtractedImage] = field(default_factory=list)
    source_image_count: int = 0
    images_truncated: bool = False
    vector_uncertainty: VectorUncertaintyStats | None = None
    glyph_stats: GlyphPassStats | None = None

    @classmethod
    def for_skip(
        cls,
        reason: SkipReason,
        *,
        page_count: int = 0,
        prompts: list[str] | None = None,
        readable: bool = True,
    ) -> "PipelineResult":
        """Construct a complete skip result.

        Single source of truth for exit-condition fields on early-exit
        paths; bypasses partial-build mutation. Named ``for_skip`` (not
        ``skipped``) to avoid shadowing the ``skipped`` dataclass field.
        """
        return cls(
            md="",
            prompts=prompts,
            page_count=page_count,
            readable=readable,
            skipped=True,
            skip_reason=reason,
            images=[],
        )


def _enforce_skip_contract(result: PipelineResult) -> PipelineResult:
    """Validate skip invariants before returning from :func:`run_pipeline`."""
    empty_md = not result.md.strip()
    if empty_md or not result.readable:
        if not result.skipped:
            _log.error(
                "PipelineResult skip contract violated: skipped=False "
                "readable=%s md_empty=%s",
                result.readable,
                empty_md,
            )
            raise AssertionError(
                "PipelineResult must set skipped=True when markdown is empty "
                "or readable is False"
            )
        if not isinstance(result.skip_reason, SkipReason):
            _log.error(
                "PipelineResult skip contract violated: invalid skip_reason=%r",
                result.skip_reason,
            )
            raise AssertionError(
                f"PipelineResult skip_reason must be a SkipReason member, "
                f"got {result.skip_reason!r}"
            )
    if result.skipped:
        if result.skip_reason is None:
            _log.error("PipelineResult skip contract violated: missing skip_reason")
            raise AssertionError(
                "PipelineResult skip_reason must be set when skipped=True"
            )
        if result.images:
            _log.error(
                "PipelineResult skip contract violated: skipped with %d images",
                len(result.images),
            )
            raise AssertionError(
                "PipelineResult images must be empty when skipped=True"
            )
        if result.md.strip():
            _log.error("PipelineResult skip contract violated: skipped with non-empty md")
            raise AssertionError(
                "PipelineResult md must be empty when skipped=True"
            )
    return result


def _parse_pdf_info_date(raw: str) -> str:
    """Parse a PDF info-dict date (``D:YYYYMMDDHHmmSS...``) into ``YYYY-MM-DD``."""
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("D:"):
        raw = raw[2:]
    if len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def run_pipeline(
    path: Path,
    *,
    ml_tables: bool = False,
    visualize: bool = False,
    extract_vector: bool = False,
    whiteout_text: bool = False,
) -> PipelineResult:
    """Run the full PDF conversion pipeline, returning all intermediate data.

    ``extract_vector`` opts in to vector-figure extraction (v2.0 default
    off; see :mod:`tomd.lib.pdf.vector_images` for the heuristic and
    :mod:`packages.tomd.improvements` for the layout-aware successor).
    When False, the per-page driver is not called and per-page candidate
    lists carry only the raster path's output - byte-identical to the
    pre-v2 behaviour.

    ``whiteout_text`` is forwarded to the vector driver and has no
    effect when ``extract_vector`` is False.
    labels inside vector figures render as pixels alongside body text.
    """
    path = Path(path)
    result = PipelineResult()
    doc = None
    vector_stats = _VectorExtractionStats() if extract_vector else None
    try:
        doc = fitz.open(str(path))
        page_count = doc.page_count
        if page_count == 0:
            return _enforce_skip_contract(
                PipelineResult.for_skip(SkipReason.EMPTY_PDF, page_count=0)
            )

        if _is_slide_deck(doc):
            _log.info("Detected slide deck (%d pages), skipping conversion",
                       page_count)
            return _enforce_skip_contract(PipelineResult.for_skip(
                SkipReason.SLIDE_DECK,
                page_count=page_count,
                prompts=["# tomd - Slide Deck Detected\n\n"
                    "This PDF appears to be a presentation / slide deck. "
                    "tomd does not convert slide decks to Markdown.\n"],
            ))

        if _is_standards_draft(doc):
            _log.info("Detected standards draft (%d pages), skipping conversion",
                       page_count)
            return _enforce_skip_contract(PipelineResult.for_skip(
                SkipReason.STANDARDS_DRAFT,
                page_count=page_count,
                prompts=["# tomd - Standards Draft Detected\n\n"
                    f"This PDF has {page_count} pages and appears to be "
                    "a standards draft. tomd is designed for technical papers.\n"],
            ))

        result.page_count = page_count

        all_mupdf_blocks = []
        all_spatial_blocks = []
        all_edge_items = []
        page_widths: dict[int, float] = {}
        per_page_image_candidates: list = []
        # Sub-threshold raster glyphs (font-replacement emoji) and the
        # text-layer emoji bboxes used to skip coincident positions.
        # Both gathered while the doc is open; injected after the
        # readability gate so unreadable PDFs discard glyph state too.
        glyph_candidates: list = []
        text_emoji_by_page: dict[int, list] = {}

        for pg_num in range(result.page_count):
            page = doc[pg_num]
            page_widths[pg_num] = page.rect.width

            mupdf_blocks = extract_mupdf(page, pg_num)
            spatial_blocks = extract_spatial(page, pg_num)

            edge_items = (
                get_edge_items(mupdf_blocks, pg_num)
                + get_edge_items(spatial_blocks, pg_num)
            )
            all_edge_items.append(edge_items)

            links = collect_links(page)
            attach_links(mupdf_blocks, links)
            attach_links(spatial_blocks, links)

            raster_candidates = extract_page_images(page, spatial_blocks)
            if extract_vector:
                vector_candidates, page_vector_stats = extract_page_vector_images(
                    page, spatial_blocks, whiteout_text=whiteout_text,
                )
                vector_stats = _VectorExtractionStats.combine(
                    vector_stats, page_vector_stats,
                )
                per_page_image_candidates.append(
                    raster_candidates + vector_candidates
                )
            else:
                per_page_image_candidates.append(raster_candidates)

            page_glyphs = collect_glyph_candidates(page)
            if page_glyphs:
                glyph_candidates.extend(page_glyphs)
                # Only pages carrying glyphs need the coincidence check,
                # so the rawdict emoji scan is scoped to them.
                text_emoji_by_page[pg_num + 1] = collect_text_emoji_bboxes(page)

            all_mupdf_blocks.extend(mupdf_blocks)
            all_spatial_blocks.extend(spatial_blocks)

        # Detect two-column pages from raw (pre-stripping) blocks.
        two_column_pages: frozenset[int] = frozenset(
            pg for pg in page_widths
            if _detect_column_split(
                [b for b in all_mupdf_blocks if b.page_num == pg],
                page_widths[pg],
            ) is not None
        )
        if two_column_pages:
            _log.debug("Two-column pages: %s", sorted(two_column_pages))

        font_counts: Counter[str] = Counter()
        for b in all_mupdf_blocks:
            for ln in b.lines:
                for s in ln.spans:
                    if s.text.strip():
                        font_counts[s.font_name.lower()] += len(s.text)
        body_fonts = {f for f, _ in font_counts.most_common(5)}

        all_hidden: dict[int, set[tuple[float, float, float, float]]] = {}
        for pg_num in range(result.page_count):
            page = doc[pg_num]
            pg_hidden = find_hidden_regions(page, body_fonts)
            if pg_hidden:
                all_hidden[pg_num] = pg_hidden

        page0_colors = _get_page0_text_colors(doc[0]) if result.page_count > 0 else {}

        page_drawings: dict[int, list] = {}
        page_mupdf_tables: dict[int, list[dict]] = {}
        all_figure_regions = []
        for pg_num in range(result.page_count):
            page = doc[pg_num]
            drawings = collect_line_drawings(page)
            if drawings:
                page_drawings[pg_num] = drawings

            raw_drawings = page.get_drawings()
            page_figures = detect_figure_regions(
                raw_drawings, pg_num, page.rect.width)
            all_figure_regions.extend(page_figures)

            try:
                ft = page.find_tables()
                if ft.tables:
                    page_mupdf_tables[pg_num] = [
                        {"bbox": tuple(t.bbox),
                         "row_count": t.row_count,
                         "col_count": t.col_count,
                         "cells": [tuple(c) if c else None for c in t.cells],
                         "header_names": t.header.names if t.header else None,
                         "extract": t.extract()}
                        for t in ft.tables
                    ]
            except Exception:
                _log.debug("find_tables() failed on page %d", pg_num,
                           exc_info=True)

        if all_figure_regions:
            _log.info("Detected %d figure region(s)", len(all_figure_regions))

        pdf_info_date = _parse_pdf_info_date(doc.metadata.get("creationDate", ""))
        pdf_info_title = (doc.metadata.get("title") or "").strip()
        doc_metadata = dict(doc.metadata)
    finally:
        if doc is not None:
            doc.close()

    if all_hidden:
        total_hidden = sum(len(v) for v in all_hidden.values())
        _log.info("Stripping text hidden by %d covered regions on %d pages",
                  total_hidden, len(all_hidden))
        all_mupdf_blocks = strip_hidden_blocks(all_mupdf_blocks, all_hidden)
        all_spatial_blocks = strip_hidden_blocks(all_spatial_blocks, all_hidden)

    mupdf_text = "\n".join(b.text for b in all_mupdf_blocks)
    if not is_readable(mupdf_text):
        _log.warning("Extracted text is not readable (encrypted/scanned PDF?)")
        return _enforce_skip_contract(PipelineResult.for_skip(
            SkipReason.UNREADABLE,
            page_count=result.page_count,
            readable=False,
        ))

    extraction_result = finalize_extraction(
        per_page_image_candidates, path.stem.lower(),
        vector_stats=vector_stats,
    )
    result.images = extraction_result.images
    result.source_image_count = extraction_result.source_image_count
    result.images_truncated = extraction_result.images_truncated
    result.vector_uncertainty = extraction_result.vector_uncertainty

    # Inject U+FFFD placeholders for sub-threshold raster glyphs (emoji
    # the figure path drops) into both extraction paths, skipping rects
    # already covered by a text-layer emoji codepoint. Runs after the
    # readability gate (so unreadable PDFs discard glyph state) and
    # after the font_counts snapshot above (so the synthetic font never
    # enters body_fonts/dominant_font), but before cleanup/structure so
    # each placeholder is treated as ordinary body text. Placement runs
    # independently per path; compare_extractions is page-level word
    # multiset, so the same token added to both paths stays balanced.
    if glyph_candidates:
        orphans, skipped_coincident = filter_coincident(
            glyph_candidates, text_emoji_by_page,
        )
        glyph_stats = inject_glyph_spans(all_mupdf_blocks, orphans)
        inject_glyph_spans(all_spatial_blocks, orphans)
        result.glyph_stats = GlyphPassStats(
            injected=glyph_stats.injected,
            skipped_coincident=skipped_coincident,
            free_standing=glyph_stats.free_standing,
        )
        if result.glyph_stats.fired:
            _log.info("Glyph placeholders: injected=%d skipped_coincident=%d "
                      "free_standing=%d", result.glyph_stats.injected,
                      result.glyph_stats.skipped_coincident,
                      result.glyph_stats.free_standing)

    repeating = detect_repeating(all_edge_items, result.page_count)
    if repeating:
        _log.info("Stripping %d repeating header/footer patterns", len(repeating))
        all_mupdf_blocks = strip_repeating(all_mupdf_blocks, repeating)
        all_spatial_blocks = strip_repeating(all_spatial_blocks, repeating)

    dominant_font = font_counts.most_common(1)[0][0] if font_counts else ""
    propagate_monospace(all_mupdf_blocks, all_spatial_blocks, dominant_font)

    wording_problems = classify_wording(all_mupdf_blocks, page_drawings)

    # Sort blocks into reading order BEFORE cleanup_text, which contains
    # _join_cross_page.  That function merges the first block on page N+1
    # with the last block on page N; if blocks are still in MuPDF's
    # arbitrary extraction order (e.g. code blocks extracted after body
    # text despite higher y-positions) the merge target is wrong and
    # continuation text lands on the wrong block.  Sorting first ensures
    # "last block on the page" means visually bottom-most.
    _column_aware_sort(all_mupdf_blocks, page_widths)
    _column_aware_sort(all_spatial_blocks, page_widths)

    all_mupdf_blocks = cleanup_text(all_mupdf_blocks)
    all_spatial_blocks = cleanup_text(all_spatial_blocks)

    all_mupdf_blocks = normalize_spans(all_mupdf_blocks)
    all_spatial_blocks = normalize_spans(all_spatial_blocks)

    wg21_metadata, _ = extract_metadata_from_blocks(all_mupdf_blocks,
                                                     text_colors=page0_colors)

    # Snapshot for Docling enrichment: detect_tables consumes blocks,
    # but the enrichment needs access to ALL page spans (including
    # those consumed but dropped from sec.lines by the rule-based
    # detector) to correctly populate Docling cell grids.
    pre_detect_blocks = list(all_mupdf_blocks) if ml_tables else []

    table_sections, all_mupdf_blocks = detect_tables(
        all_mupdf_blocks, page_mupdf_tables=page_mupdf_tables,
        two_column_pages=two_column_pages)
    if table_sections:
        _log.info("Detected %d table(s)", len(table_sections))
        all_spatial_blocks = exclude_table_regions(
            all_spatial_blocks, table_sections)

    # --- Optional Docling ML table processing ---
    # When ml_tables=True and Docling is available:
    #   1. Enrich existing rule-based tables with Docling cell grids.
    #   2. Discover new tables that rule-based detection missed
    #      (borderless tables), consuming their blocks so they
    #      don't become duplicate paragraphs.
    if ml_tables and _docling_available():
        docling_tables = _extract_docling_tables(path)
        if docling_tables:
            if table_sections:
                n = _enrich_tables_with_docling(
                    table_sections, docling_tables, pre_detect_blocks)
                if n:
                    _log.info("Docling enriched %d/%d table(s)",
                              n, len(table_sections))

            all_mupdf_blocks = _absorb_cross_page_spec_rows(
                table_sections, all_mupdf_blocks, docling_tables)

            new_tables, all_mupdf_blocks = _discover_tables_with_docling(
                docling_tables, all_mupdf_blocks, table_sections)
            if new_tables:
                table_sections.extend(new_tables)
                all_spatial_blocks = exclude_table_regions(
                    all_spatial_blocks, new_tables)
                _log.info("Docling discovered %d new table(s)",
                          len(new_tables))

    sections = compare_extractions(all_mupdf_blocks, all_spatial_blocks)

    for ts in table_sections:
        inserted = False
        data_on_label_page = (
            ts.lines and ts.lines[0].page_num == ts.page_num
        )
        for i, sec in enumerate(sections):
            if sec.page_num > ts.page_num:
                sections.insert(i, ts)
                inserted = True
                break
            if (data_on_label_page
                    and sec.page_num == ts.page_num and sec.lines
                    and ts.lines
                    and sec.lines[0].bbox[1] > ts.lines[0].bbox[1]):
                sections.insert(i, ts)
                inserted = True
                break
        if not inserted:
            sections.append(ts)

    if result.images:
        _log.info("Extracted %d image(s) (cap %s)",
                   len(result.images),
                   "tripped" if result.images_truncated else "ok")
        sections = _insert_image_sections(sections, result.images)

    has_title = "title" in wg21_metadata

    # --- Phase 1: Metadata extraction ---
    # Two pathways, merged in precedence order (last wins):
    #   1. metadata_yaml.extract.extract_metadata - PDF section line scan (lowest)
    #   2. wg21.extract_metadata_from_blocks - PDF block-level scan (wins)
    structure_metadata, sections = _extract_metadata_yaml(sections)
    metadata = {**structure_metadata, **wg21_metadata}

    # --- Phase 1b: Body structuring (may detect title for metadata) ---
    body_metadata, sections, nesting_corrections = structure_body(
        sections, has_title=has_title,
        figure_regions=all_figure_regions or None)
    for k, v in body_metadata.items():
        if k not in metadata:
            metadata[k] = v

    # Glyph placeholders that attached (before structure ran) to a line
    # now classified CODE or folded into a TABLE are removed: a U+FFFD in
    # a fenced block reads as a syntax error, and tables render
    # structurally. The marker discloses the count so the loss is not
    # silent. Runs after structure has assigned section kinds.
    if result.glyph_stats is not None:
        result.glyph_stats.skipped_code_section = (
            drop_glyphs_in_code_and_tables(sections)
        )

    # Vector-image post-processing
    if result.images:
        total_dropped = 0
        result.images, sections, dropped_a = (
            _filter_vector_images_against_structural(result.images, sections)
        )
        total_dropped += dropped_a
        result.images, sections, dropped_b = (
            _filter_overlapping_vector_images(result.images, sections)
        )
        total_dropped += dropped_b
        sections = _filter_sections_inside_vector_images(
            result.images, sections,
        )
        if total_dropped and result.vector_uncertainty is not None:
            from dataclasses import replace
            new_kept = sum(1 for im in result.images if im.source == "vector")
            result.vector_uncertainty = replace(
                result.vector_uncertainty, kept=new_kept,
            )

    if "document" not in metadata:
        from .. import DOC_NUM_RE
        stem_match = DOC_NUM_RE.search(path.stem)
        if stem_match:
            metadata["document"] = stem_match.group(1).upper()

    # --- Phase 1c: Metadata fallbacks & enrichment (metadata_yaml) ---
    _apply_pdf_metadata_fallbacks(
        metadata, path, pdf_info_date, pdf_info_title,
        doc_metadata, sections, all_mupdf_blocks,
        _TITLE_PID_PREFIX_RE)

    # --- Phase 1d: Metadata stripping from body sections (metadata_yaml) ---
    _strip_pre_heading_fragments(sections)
    _strip_metadata_headings_new(sections, metadata)
    _promote_abstract_from_uncertain(sections)
    _strip_pre_content_paragraphs(sections)

    # Demote TABLE sections with dot-leaders back to PARAGRAPH so
    # TOC detection can recognize them.  Horizontal-row table detection
    # can misclassify TOC entries (section number + title + page number
    # on the same y-line) as table rows; reverting them here lets the
    # existing find_toc_indices / label-anchored logic strip them.
    for sec in sections:
        if sec.kind == SectionKind.TABLE and has_dot_leader(sec.text):
            sec.kind = SectionKind.PARAGRAPH
            sec.table_kind = None
            sec.table_strategy = None
            sec.columns = None

    texts = [sec.text.split("\n")[0].strip() for sec in sections]
    full_texts = [sec.text for sec in sections]
    heading_texts = {sec.text.split("\n")[0].strip()
                     for sec in sections if sec.kind == SectionKind.HEADING}
    structural_hints = _toc_structural_hints(sections) if not heading_texts else None
    toc_indices = find_toc_indices(texts, heading_texts, structural_hints,
                                   full_texts=full_texts)

    # Plausibility guard: reject phantom TOC detection.
    # A valid TOC must have at least one confirming signal:
    #   (a) dot leaders in at least one section,
    #   (b) a "Contents" / "Table of Contents" label in the document, or
    #   (c) a heading inside the detected block also exists outside it
    #       (the inside copy is a TOC reference, the outside is the real heading).
    # Without any signal the "TOC" is a phantom from heading self-matching,
    # common in short dense papers where the gap between headings <= _MAX_GAP.
    if toc_indices:
        # IMAGE sections are never TOC content.
        toc_indices = {
            i for i in toc_indices
            if sections[i].kind is not SectionKind.IMAGE
        }

    if toc_indices:
        _has_dot = any(has_dot_leader(sections[i].text) for i in toc_indices)
        _has_label = any(
            _is_toc_label(s.text.split("\n")[0].strip()) for s in sections
        )
        if not _has_dot and not _has_label:
            _inside = set()
            for i in toc_indices:
                if sections[i].kind == SectionKind.HEADING:
                    _n = sections[i].text.split("\n")[0].strip().lower().rstrip(":")
                    if _n:
                        _inside.add(_n)
            _outside = set()
            for i, s in enumerate(sections):
                if i not in toc_indices and s.kind == SectionKind.HEADING:
                    _n = s.text.split("\n")[0].strip().lower().rstrip(":")
                    if _n:
                        _outside.add(_n)
            if not (_inside & _outside):
                _log.info("Rejected phantom TOC (%d entries, no confirming signal)",
                          len(toc_indices))
                toc_indices = set()

    for li, sec in enumerate(sections):
        if li in toc_indices:
            continue
        fl = next(
            (ln.strip() for ln in sec.text.split("\n") if ln.strip()),
            "",
        )
        if not _is_toc_label(fl):
            continue
        candidate = {li}
        numbered = 0
        for j in range(li + 1, min(li + 40, len(sections))):
            if j in toc_indices:
                continue
            sec_j = sections[j]
            jfl = sec_j.text.split("\n")[0].strip()
            has_numbered = False
            j_dot = any(has_dot_leader(ln) for ln in sec_j.text.split("\n"))
            for line in sec_j.text.split("\n"):
                stripped = line.strip()
                if (_NUMBERED_LINE_RE.match(stripped)
                        or _BARE_PAGE_NUM_RE.match(stripped)):
                    has_numbered = True
                    numbered += 1
            if j_dot or has_numbered:
                candidate.add(j)
                if j_dot and not has_numbered:
                    numbered += 1
            elif jfl.lower() in ('ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii'):
                candidate.add(j)
            elif sec_j.kind == SectionKind.TABLE and not j_dot:
                candidate.add(j)
            elif sec_j.kind == SectionKind.HEADING and not has_numbered and not j_dot:
                break
            elif jfl.strip() == '':
                continue
            else:
                break
        if len(candidate) > 1 and numbered >= _LABEL_TOC_MIN_NUMBERED_LINES:
            toc_indices |= candidate
            _log.info("Label-anchored TOC: %d entries after '%s'",
                      len(candidate), fl)

    if toc_indices:
        non_toc_known: set[str] = set()
        for i, sec in enumerate(sections):
            if i not in toc_indices and sec.kind == SectionKind.HEADING:
                fl = sec.text.split("\n")[0].strip()
                if _is_known_section(fl):
                    non_toc_known.add(fl.lower().rstrip(":"))

        protected = set()
        for idx in sorted(toc_indices):
            sec = sections[idx]
            if sec.kind == SectionKind.HEADING:
                fl = sec.text.split("\n")[0].strip()
                if _is_known_section(fl):
                    if has_dot_leader(sec.text):
                        continue
                    if fl.lower().rstrip(":") in non_toc_known:
                        continue
                    protected.add(idx)
                    is_abstract_heading = (
                        fl.lower().rstrip(":") == "abstract"
                    )
                    first_body_confirmed = False
                    for nxt in range(idx + 1, len(sections)):
                        if nxt not in toc_indices:
                            break
                        nxt_sec = sections[nxt]
                        if nxt_sec.kind == SectionKind.HEADING:
                            if (is_abstract_heading
                                    and not first_body_confirmed
                                    and nxt_sec.confidence == Confidence.LOW):
                                nxt_sec.kind = SectionKind.PARAGRAPH
                                nxt_sec.heading_level = 0
                                first_body_confirmed = True
                                protected.add(nxt)
                                continue
                            parent_level = sections[idx].heading_level
                            if (parent_level > 0
                                    and nxt_sec.heading_level > parent_level):
                                protected.add(nxt)
                                first_body_confirmed = True
                                continue
                            break
                        if nxt_sec.kind == SectionKind.TABLE:
                            if first_body_confirmed:
                                protected.add(nxt)
                                continue
                            break
                        nxt_fl = nxt_sec.text.split("\n")[0].strip()
                        if has_dot_leader(nxt_fl):
                            break
                        if not first_body_confirmed and _SECTION_NUM_START_RE.match(nxt_fl):
                            break
                        nxt_words = len(nxt_sec.text.split())
                        if not first_body_confirmed:
                            if not is_abstract_heading and nxt_words < _TOC_BODY_PROTECT_MIN_WORDS:
                                break
                            first_body_confirmed = True
                        protected.add(nxt)
        if protected:
            _log.debug("Protecting %d section heading(s) from TOC removal: %s",
                        len(protected),
                        [sections[i].text.split("\n")[0].strip()[:60] for i in sorted(protected)])
            toc_indices -= protected
        if toc_indices:
            sections[:] = [s for i, s in enumerate(sections) if i not in toc_indices]

    sections[:] = [
        s for s in sections
        if not (s.kind == SectionKind.PARAGRAPH
                and _is_toc_label(s.text.split("\n")[0].strip())
                and len(s.text.split("\n")[0].strip().split()) <= _TOC_LABEL_MAX_WORDS)
    ]

    _dedup_abstract_new(sections)
    _rescue_stranded_abstract_body(sections)

    _strip_metadata_from_uncertain(sections, metadata)
    _reorder_abstract_in_uncertain(sections)

    md = emit_markdown(
        metadata,
        sections,
        images_truncated=result.images_truncated,
        source_image_count=result.source_image_count,
        vector_uncertainty=result.vector_uncertainty,
        glyph_stats=result.glyph_stats,
    )
    prompts = emit_prompts(sections)

    if wording_problems:
        wording_prompts = [
            (
                "The PDF wording-detection pass flagged the following issue. "
                "Review and correct the affected region in the converted "
                "Markdown.\n\n"
                f"{problem}"
            )
            for problem in wording_problems
        ]
        prompts = (prompts or []) + wording_prompts

    result.md = md
    result.prompts = prompts
    result.sections = sections
    result.metadata = metadata
    result.nesting_corrections = nesting_corrections
    return _enforce_skip_contract(result)
