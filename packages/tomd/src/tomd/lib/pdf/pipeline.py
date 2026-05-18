"""PDF to Markdown converter - pipeline entry point."""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .cleanup import (get_edge_items, detect_repeating, strip_repeating,
                      cleanup_text, find_hidden_regions, strip_hidden_blocks)
from .extract import extract_mupdf, extract_spatial, collect_links, attach_links
from .mono import propagate_monospace
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
from .types import KNOWN_SECTIONS, Confidence, Section, SectionKind, is_readable
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

__all__ = ["convert_pdf", "PipelineResult"]

_log = logging.getLogger(__name__)

_STANDALONE_PAGE_RE = re.compile(r'^\d{1,4}$')
_SECTION_NUM_START_RE = re.compile(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+)(?:\s|$)")
_TOC_X_TOLERANCE = 5.0
_TOC_BODY_PROTECT_MIN_WORDS = 10
_NUMBERED_LINE_RE = re.compile(r"^\s*\d+[\.\)]\s+\S")
_BARE_PAGE_NUM_RE = re.compile(r"^\s*\d{1,3}\s*$")
_LABEL_TOC_MIN_NUMBERED_LINES = 3

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

    A PDF is a slide deck when most pages are landscape and smaller
    than standard paper sizes (width < 600pt ≈ 8.3in).
    """
    if doc.page_count == 0:
        return False
    landscape_count = 0
    for pg_num in range(doc.page_count):
        r = doc[pg_num].rect
        if r.width > r.height and r.width < _SLIDE_DECK_MAX_WIDTH:
            landscape_count += 1
    return landscape_count / doc.page_count >= _SLIDE_DECK_LANDSCAPE_FRACTION


def _is_standards_draft(doc) -> bool:
    """Detect standards drafts by page count (>= 200 pages)."""
    return doc.page_count >= _STANDARDS_DRAFT_MIN_PAGES


@dataclass
class PipelineResult:
    """Full output of the PDF conversion pipeline, used for QA scoring."""
    md: str = ""
    prompts: list[str] | None = None
    sections: list[Section] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    page_count: int = 0
    nesting_corrections: int = 0
    readable: bool = True
    skipped: bool = False
    skip_reason: str = ""


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


def _run_pipeline(path: Path) -> PipelineResult:
    """Run the full PDF conversion pipeline, returning all intermediate data."""
    import fitz  # lazy: PyMuPDF not required for HTML-only paths

    path = Path(path)
    result = PipelineResult()
    doc = None
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
        page_widths: dict[int, float] = {}

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

            all_mupdf_blocks.extend(mupdf_blocks)
            all_spatial_blocks.extend(spatial_blocks)

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
        total_hidden = sum(len(v) for v in all_hidden.values())
        _log.info("Stripping text hidden by %d covered regions on %d pages",
                  total_hidden, len(all_hidden))
        all_mupdf_blocks = strip_hidden_blocks(all_mupdf_blocks, all_hidden)
        all_spatial_blocks = strip_hidden_blocks(all_spatial_blocks, all_hidden)

    mupdf_text = "\n".join(b.text for b in all_mupdf_blocks)
    if not is_readable(mupdf_text):
        _log.warning("Extracted text is not readable (encrypted/scanned PDF?)")
        result.readable = False
        return result

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

    # Sort blocks by reading order.  For two-column pages (detected via
    # x-midpoint gap analysis) the left column is placed before the right
    # column, each sorted internally by y.  Single-column pages use plain
    # y-midpoint sorting (the P3625R1 fix for out-of-order MuPDF blocks).
    _column_aware_sort(all_mupdf_blocks, page_widths)
    _column_aware_sort(all_spatial_blocks, page_widths)

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

    has_title = "title" in wg21_metadata

    # --- Phase 1: Metadata extraction ---
    # Two pathways, merged in precedence order (last wins):
    #   1. metadata_yaml.extract.extract_metadata - PDF section line scan (lowest)
    #   2. wg21.extract_metadata_from_blocks - PDF block-level scan (wins)
    structure_metadata, sections = _extract_metadata_yaml(sections)
    metadata = {**structure_metadata, **wg21_metadata}

    # --- Phase 1b: Body structuring (may detect title for metadata) ---
    body_metadata, sections, nesting_corrections = structure_body(
        sections, has_title=has_title)
    for k, v in body_metadata.items():
        if k not in metadata:
            metadata[k] = v

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

    # Fallback: label-anchored TOC detection. When find_toc_indices found
    # nothing but a "Contents" / "Table of Contents" label exists, scan
    # forward for numbered entries and mark as TOC. This handles PDFs
    # where TOC entry titles differ from actual headings. The label may
    # itself be classified as HEADING (e.g. bold "Table of Contents").
    if not toc_indices:
        for li, sec in enumerate(sections):
            fl = next(
                (ln.strip() for ln in sec.text.split("\n") if ln.strip()),
                "",
            )
            if _is_toc_label(fl):
                candidate = {li}
                numbered = 0
                for j in range(li + 1, min(li + 30, len(sections))):
                    sec_j = sections[j]
                    has_numbered = False
                    for line in sec_j.text.split("\n"):
                        stripped = line.strip()
                        if (_NUMBERED_LINE_RE.match(stripped)
                                or _BARE_PAGE_NUM_RE.match(stripped)):
                            has_numbered = True
                            numbered += 1
                    if sec_j.kind == SectionKind.HEADING and not has_numbered:
                        break
                    candidate.add(j)
                if numbered >= _LABEL_TOC_MIN_NUMBERED_LINES:
                    toc_indices = candidate
                    _log.info("Label-anchored TOC: %d entries after '%s'",
                              len(candidate), fl)
                break

    if toc_indices:
        # Map KNOWN_SECTIONS heading names that exist OUTSIDE the TOC
        # range. When a duplicate exists outside, the inside copy is a
        # TOC artifact and should not be protected (Ticket H).
        # Uses _is_known_section to also match numbered prefixes
        # like "1. Introduction" -> "introduction".
        non_toc_known: set[str] = set()
        for i, sec in enumerate(sections):
            if i not in toc_indices and sec.kind == SectionKind.HEADING:
                fl = sec.text.split("\n")[0].strip()
                if _is_known_section(fl):
                    non_toc_known.add(fl.lower().rstrip(":"))

        protected = set()
        for idx in toc_indices:
            sec = sections[idx]
            if sec.kind == SectionKind.HEADING:
                fl = sec.text.split("\n")[0].strip()
                if _is_known_section(fl):
                    # Only protect real section headings, not TOC entries.
                    # TOC entries have dot-leaders in their text.
                    if has_dot_leader(sec.text):
                        continue
                    # If the same heading exists outside the TOC range,
                    # this copy is a TOC artifact -- do not protect.
                    if fl.lower().rstrip(":") in non_toc_known:
                        continue
                    protected.add(idx)
                    # Protect body paragraphs immediately after the
                    # heading that were swept in as TOC gap fillers.
                    # A heading like "Abstract" may have multiple body
                    # paragraphs before the next section heading.
                    # The first paragraph must be long enough to prove
                    # this is a real section (not a TOC entry). Once
                    # confirmed, all subsequent paragraphs are protected
                    # regardless of length.
                    is_abstract_heading = (
                        fl.lower().rstrip(":") == "abstract"
                    )
                    first_body_confirmed = False
                    for nxt in range(idx + 1, len(sections)):
                        if nxt not in toc_indices:
                            break
                        nxt_sec = sections[nxt]
                        if nxt_sec.kind == SectionKind.HEADING:
                            # LOW-confidence headings right after "Abstract"
                            # are likely misclassified body text (wrong
                            # body_size in wording-heavy papers). Reclassify
                            # as paragraph and protect from TOC stripping.
                            if (is_abstract_heading
                                    and not first_body_confirmed
                                    and nxt_sec.confidence == Confidence.LOW):
                                nxt_sec.kind = SectionKind.PARAGRAPH
                                nxt_sec.heading_level = 0
                                first_body_confirmed = True
                                protected.add(nxt)
                                continue
                            # Sub-headings (deeper level) stay protected
                            # under the parent section heading.
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

    # Strip orphaned TOC labels that survived TOC detection.
    # Catches standalone "Table of Content(s)" / "Contents" paragraphs
    # that sit far from the actual TOC entries (e.g. P2000R5 page 0).
    _TOC_LABEL_MAX_WORDS = 4
    sections[:] = [
        s for s in sections
        if not (s.kind == SectionKind.PARAGRAPH
                and _is_toc_label(s.text.split("\n")[0].strip())
                and len(s.text.split("\n")[0].strip().split()) <= _TOC_LABEL_MAX_WORDS)
    ]

    _dedup_abstract_new(sections)
    _rescue_stranded_abstract_body(sections)

    # --- Phase 2b: Strip metadata echoes from UNCERTAIN body sections ---
    _strip_metadata_from_uncertain(sections, metadata)
    _reorder_abstract_in_uncertain(sections)

    md = emit_markdown(metadata, sections)
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


def convert_pdf(path: Path) -> tuple[str, list[str] | None]:
    """Convert a PDF file to Markdown.

    Returns ``(markdown_text, prompts_or_none)`` where ``prompts_or_none``
    is a list of self-contained LLM reconcile prompts (one per uncertain
    region, plus one per flagged wording issue) or ``None`` when the
    converter is fully confident. Returns ``("", None)`` for empty or
    unreadable PDFs. Raises fitz exceptions for corrupt or inaccessible
    files.
    """
    r = _run_pipeline(path)
    return r.md, r.prompts
