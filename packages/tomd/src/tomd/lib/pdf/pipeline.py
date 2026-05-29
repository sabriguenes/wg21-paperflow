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
from .wording import classify_wording, collect_line_drawings
from .spans import normalize_spans
from .structure import compare_extractions, structure_sections
from .table import detect_tables, exclude_table_regions
from .wg21 import extract_metadata_from_blocks
from .emit import emit_markdown, emit_prompts
from .types import KNOWN_SECTIONS, Confidence, Section, SectionKind, is_readable
from ..toc import find_toc_indices

__all__ = ["convert_pdf", "run_pipeline", "PipelineResult", "ExtractedImage"]

_log = logging.getLogger(__name__)

_STANDALONE_PAGE_RE = re.compile(r'^\d{1,4}$')
_TOC_X_TOLERANCE = 5.0

_PID_BASE_RE = re.compile(r"([DPN])(\d{3,5})(?:R(\d+))?", re.IGNORECASE)


def _override_revision_from_filename(metadata: dict, path: Path) -> None:
    """Override document revision from filename when the base paper number
    matches but revisions differ. Skip when the extracted document has a
    D-prefix (draft), since D/P mismatches are expected WG21 workflow."""
    if "document" not in metadata:
        return
    doc_m = _PID_BASE_RE.search(metadata["document"])
    stem_m = _PID_BASE_RE.search(path.stem)
    if not doc_m or not stem_m:
        return
    if doc_m.group(1).upper() == "D":
        return
    if doc_m.group(2) != stem_m.group(2):
        return
    stem_rev = stem_m.group(3)
    doc_rev = doc_m.group(3)
    if stem_rev is not None and stem_rev != doc_rev:
        prefix = stem_m.group(1).upper()
        number = stem_m.group(2)
        metadata["document"] = f"{prefix}{number}R{stem_rev}"
        _log.debug("Overrode document revision from filename: %s -> %s",
                   f"{doc_m.group(0)}", metadata["document"])


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
    skip_reason: str = ""
    images: list[ExtractedImage] = field(default_factory=list)
    source_image_count: int = 0
    images_truncated: bool = False
    vector_uncertainty: VectorUncertaintyStats | None = None
    glyph_stats: GlyphPassStats | None = None


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


def _enrich_pdf_reply_to(
    metadata: dict, blocks: list, *, max_lines: int = 30
) -> None:
    """Safety-net post-pass: scan page 0 for emails missed by labeled extractors.

    Mirrors the HTML _enrich_reply_to pattern. Runs after wg21/structure merge.
    """
    if not isinstance(metadata.get("reply-to"), list):
        metadata["reply-to"] = []
    from .. import EMAIL_RE

    page0_lines: list[str] = []
    for b in blocks:
        if b.page_num != 0:
            continue
        for ln in b.lines:
            page0_lines.append(ln.text.strip())
            if len(page0_lines) >= max_lines:
                break
        if len(page0_lines) >= max_lines:
            break

    existing = metadata.get("reply-to", [])
    existing_joined = " ".join(existing)
    existing_emails = {e.lower() for e in EMAIL_RE.findall(existing_joined)}

    page0_text = "\n".join(page0_lines)
    page0_emails = EMAIL_RE.findall(page0_text)
    missing = [e for e in page0_emails if e.lower() not in existing_emails]
    if not missing:
        return

    _NAMED_EMAIL_RE = re.compile(
        r"([A-Z][A-Za-z.''\- ]+?)\s*[<(](" + EMAIL_RE.pattern + r")[)>]"
    )
    _BARE_EMAIL_RE = re.compile(
        r"^\s*[<(]?(" + EMAIL_RE.pattern + r")[)>]?\s*$"
    )
    line_map: dict[str, str] = {}
    for idx, line in enumerate(page0_lines):
        for m in _NAMED_EMAIL_RE.finditer(line):
            name = m.group(1).strip().rstrip(",/;")
            line_map[m.group(2).lower()] = name
        m = _BARE_EMAIL_RE.match(line)
        if m and m.group(1).lower() not in line_map:
            if idx > 0:
                prev = page0_lines[idx - 1].strip().rstrip(":")
                if prev and "@" not in prev and "<" not in prev:
                    line_map[m.group(1).lower()] = prev

    paired: set[str] = set()
    for email in missing:
        name = line_map.get(email.lower(), "")
        if name:
            for idx, entry in enumerate(existing):
                if entry == name or (
                    "<" not in entry and "@" not in entry
                    and name.lower().startswith(entry.lower())
                ):
                    existing[idx] = f"{entry} <{email}>"
                    paired.add(email.lower())
                    break

    for email in missing:
        if email.lower() in paired:
            continue
        name = line_map.get(email.lower(), "")
        if name:
            existing.append(f"{name} <{email}>")
        else:
            existing.append(f"<{email}>")
    metadata["reply-to"] = existing


def run_pipeline(
    path: Path,
    *,
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
        result.page_count = doc.page_count
        if result.page_count == 0:
            return result

        if _is_slide_deck(doc):
            _log.info("Detected slide deck (%d pages), skipping conversion",
                       result.page_count)
            result.skipped = True
            result.skip_reason = "slide deck"
            result.prompts = ["# tomd - Slide Deck Detected\n\n"
                "This PDF appears to be a presentation / slide deck. "
                "tomd does not convert slide decks to Markdown.\n"]
            return result

        if _is_standards_draft(doc):
            _log.info("Detected standards draft (%d pages), skipping conversion",
                       result.page_count)
            result.skipped = True
            result.skip_reason = "standards draft"
            result.prompts = ["# tomd - Standards Draft Detected\n\n"
                f"This PDF has {result.page_count} pages and appears to be "
                "a standards draft. tomd is designed for technical papers.\n"]
            return result

        all_mupdf_blocks = []
        all_spatial_blocks = []
        all_edge_items = []
        # Per-page image candidates accumulated while the document is
        # open. Finalized (deduped + capped) after the readability
        # check passes, so unreadable PDFs produce result.images = [].
        per_page_image_candidates: list = []
        # Sub-threshold raster glyphs (font-replacement emoji) and the
        # text-layer emoji bboxes used to skip coincident positions.
        # Both gathered while the doc is open; injected after the
        # readability gate so unreadable PDFs discard glyph state too.
        glyph_candidates: list = []
        text_emoji_by_page: dict[int, list] = {}

        for pg_num in range(result.page_count):
            page = doc[pg_num]

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

        font_counts: Counter[str] = Counter()
        for b in all_mupdf_blocks:
            for ln in b.lines:
                for s in ln.spans:
                    if s.text.strip():
                        font_counts[s.font_name.lower()] += len(s.text)
        body_fonts = {f for f, _ in font_counts.most_common(5)}

        all_hidden: set[tuple[float, float, float, float]] = set()
        for pg_num in range(result.page_count):
            page = doc[pg_num]
            all_hidden |= find_hidden_regions(page, body_fonts)

        page0_colors = _get_page0_text_colors(doc[0]) if result.page_count > 0 else {}

        page_drawings: dict[int, list] = {}
        for pg_num in range(result.page_count):
            drawings = collect_line_drawings(doc[pg_num])
            if drawings:
                page_drawings[pg_num] = drawings

        pdf_info_date = _parse_pdf_info_date(doc.metadata.get("creationDate", ""))
        pdf_info_title = (doc.metadata.get("title") or "").strip()
        doc_metadata = dict(doc.metadata)
    finally:
        if doc is not None:
            doc.close()

    if all_hidden:
        _log.info("Stripping text hidden by %d covered regions", len(all_hidden))
        all_mupdf_blocks = strip_hidden_blocks(all_mupdf_blocks, all_hidden)
        all_spatial_blocks = strip_hidden_blocks(all_spatial_blocks, all_hidden)

    mupdf_text = "\n".join(b.text for b in all_mupdf_blocks)
    if not is_readable(mupdf_text):
        _log.warning("Extracted text is not readable (encrypted/scanned PDF?)")
        result.readable = False
        return result

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

    all_mupdf_blocks = cleanup_text(all_mupdf_blocks)
    all_spatial_blocks = cleanup_text(all_spatial_blocks)

    all_mupdf_blocks = normalize_spans(all_mupdf_blocks)
    all_spatial_blocks = normalize_spans(all_spatial_blocks)

    wg21_metadata, _ = extract_metadata_from_blocks(all_mupdf_blocks,
                                                     text_colors=page0_colors)

    table_sections, all_mupdf_blocks = detect_tables(all_mupdf_blocks)
    if table_sections:
        _log.info("Detected %d table(s)", len(table_sections))
        all_spatial_blocks = exclude_table_regions(
            all_spatial_blocks, table_sections)

    sections = compare_extractions(all_mupdf_blocks, all_spatial_blocks)

    for ts in table_sections:
        inserted = False
        for i, sec in enumerate(sections):
            if sec.page_num > ts.page_num:
                sections.insert(i, ts)
                inserted = True
                break
            if (sec.page_num == ts.page_num and sec.lines
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
    # Three metadata pathways, merged here in precedence order (last wins):
    #   1. structure._extract_metadata  - PDF section line scan (lowest precedence)
    #   2. wg21.extract_metadata_from_blocks - PDF block-level scan (wins on conflict)
    # HTML conversion uses a third pathway: html.extract.extract_metadata (DOM scan).
    structure_metadata, sections, nesting_corrections = structure_sections(
        sections, has_title=has_title)
    metadata = {**structure_metadata, **wg21_metadata}

    # Glyph placeholders that attached (before structure ran) to a line
    # now classified CODE or folded into a TABLE are removed: a U+FFFD in
    # a fenced block reads as a syntax error, and tables render
    # structurally. The marker discloses the count so the loss is not
    # silent. Runs after structure has assigned section kinds.
    if result.glyph_stats is not None:
        result.glyph_stats.skipped_code_section = (
            drop_glyphs_in_code_and_tables(sections)
        )

    # Vector-image post-processing, in three passes:
    #   1. Defer to structural sections: drop vector PNGs that
    #      duplicate a TABLE or CODE region the structure pass
    #      already produced (the structural representation is
    #      canonical).
    #   2. Dedup overlapping vectors: drop small vector PNGs that
    #      are detail crops of larger surviving ones (the larger
    #      image already shows the content).
    #   3. Drop body-text sections that fall mostly inside a
    #      surviving vector PNG (the same text is already
    #      rasterised into the image; emitting it as prose creates
    #      duplicated content).
    # All three are skipped when result.images is empty. The
    # vector_uncertainty marker's ``kept`` count is recomputed once
    # at the end so its disclosure matches what actually lands in
    # the markdown.
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

    if "date" not in metadata and pdf_info_date:
        metadata["date"] = pdf_info_date

    _override_revision_from_filename(metadata, path)

    if not metadata.get("title"):
        for sec in sections:
            if sec.kind == SectionKind.HEADING:
                first_line = sec.text.split("\n")[0].strip().lstrip("# ").strip()
                if (first_line
                        and first_line.lower().rstrip(":") not in KNOWN_SECTIONS):
                    metadata["title"] = first_line
                    break

    if not metadata.get("title") and pdf_info_title:
        _TITLE_BOILERPLATE_RE = re.compile(
            r"^(?:Microsoft\s+Word|Document\d|Untitled|"
            r"[DPN]\d{3,5}(?:R\d+)?|Presentation\d?)$",
            re.IGNORECASE,
        )
        if not _TITLE_BOILERPLATE_RE.match(pdf_info_title):
            metadata["title"] = pdf_info_title

    # Strip leading paper-ID prefix from titles regardless of extraction
    # pathway (wg21, structure, heading fallback, PDF info). Import from
    # structure where the regex is defined to keep a single source of truth.
    if metadata.get("title"):
        from .structure import _TITLE_PID_PREFIX_RE
        stripped = _TITLE_PID_PREFIX_RE.sub("", metadata["title"]).strip()
        if stripped:
            metadata["title"] = stripped

    if "reply-to" not in metadata:
        pdf_info_author = (doc_metadata.get("author") or "").strip()
        if pdf_info_author and len(pdf_info_author) >= 4:
            _AUTHOR_BOILERPLATE_RE = re.compile(
                r"^(?:Admin|Scanner|Unknown|Default|User|Owner|"
                r"Microsoft|Adobe|LaTeX|TeX|MiKTeX|pdfTeX|dvips|"
                r"Acrobat|LibreOffice|OpenOffice|Google|Apple|"
                r"[a-z0-9._-]+\.(?:pdf|doc|docx|tex))$",
                re.IGNORECASE,
            )
            if not _AUTHOR_BOILERPLATE_RE.match(pdf_info_author):
                metadata["reply-to"] = [pdf_info_author]

    _enrich_pdf_reply_to(metadata, all_mupdf_blocks)

    texts = [sec.text.split("\n")[0].strip() for sec in sections]
    heading_texts = {sec.text.split("\n")[0].strip()
                     for sec in sections if sec.kind == SectionKind.HEADING}
    structural_hints = _toc_structural_hints(sections) if not heading_texts else None
    toc_indices = find_toc_indices(texts, heading_texts, structural_hints)
    if toc_indices:
        # IMAGE sections are never TOC content. find_toc_indices walks
        # by section text and includes any "gap" index between matches
        # as a gap-fill TOC entry; without this filter a figure that
        # happens to sit between two heading-matched sections gets
        # swept up and disappears from the markdown.
        toc_indices = {
            i for i in toc_indices
            if sections[i].kind is not SectionKind.IMAGE
        }
    if toc_indices:
        sections = [s for i, s in enumerate(sections) if i not in toc_indices]

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
    return result


def convert_pdf(
    path: Path,
    *,
    extract_vector: bool = False,
    whiteout_text: bool = False,
) -> tuple[str, list[str] | None]:
    """Convert a PDF file to Markdown.

    Returns ``(markdown_text, prompts_or_none)`` where ``prompts_or_none``
    is a list of self-contained LLM reconcile prompts (one per uncertain
    region, plus one per flagged wording issue) or ``None`` when the
    converter is fully confident. Returns ``("", None)`` for empty or
    unreadable PDFs. Raises fitz exceptions for corrupt or inaccessible
    files.

    ``extract_vector`` opts in to vector-figure extraction. See
    :func:`run_pipeline` for the contract.
    """
    r = run_pipeline(path, extract_vector=extract_vector, whiteout_text=whiteout_text)
    return r.md, r.prompts
