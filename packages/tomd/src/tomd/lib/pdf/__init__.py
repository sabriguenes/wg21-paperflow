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
from .structure import compare_extractions, structure_sections
from .table import detect_tables, exclude_table_regions
from .wg21 import extract_metadata_from_blocks
from .emit import emit_markdown, emit_prompts
from .types import Section, SectionKind, is_readable
from ..toc import find_toc_indices

__all__ = ["convert_pdf", "PipelineResult"]

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
    from .types import Section as _Section  # local to avoid circular at module level

    candidates: list[tuple[int, float | None]] = []
    for i, sec in enumerate(sections):
        lines = [l.strip() for l in sec.text.split("\n") if l.strip()]
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
        if med_x is None or x is None or abs(x - med_x) <= _TOC_X_TOLERANCE:
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


def _enrich_pdf_reply_to(
    metadata: dict, blocks: list, *, max_lines: int = 30
) -> None:
    """Safety-net post-pass: scan page 0 for emails missed by labeled extractors.

    Mirrors the HTML _enrich_reply_to pattern. Runs after wg21/structure merge.
    """
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

    if existing_emails:
        return

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


def _run_pipeline(path: Path) -> PipelineResult:
    """Run the full PDF conversion pipeline, returning all intermediate data."""
    import fitz

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
            result.prompts = "# tomd - Slide Deck Detected\n\n" \
                "This PDF appears to be a presentation / slide deck. " \
                "tomd does not convert slide decks to Markdown.\n"
            return result

        if _is_standards_draft(doc):
            _log.info("Detected standards draft (%d pages), skipping conversion",
                       result.page_count)
            result.skipped = True
            result.skip_reason = "standards draft"
            result.prompts = "# tomd - Standards Draft Detected\n\n" \
                f"This PDF has {result.page_count} pages and appears to be " \
                "a standards draft. tomd is designed for technical papers.\n"
            return result

        all_mupdf_blocks = []
        all_spatial_blocks = []
        all_edge_items = []

        for pg_num in range(result.page_count):
            page = doc[pg_num]
            page_height = page.rect.height

            mupdf_blocks = extract_mupdf(page, pg_num)
            spatial_blocks = extract_spatial(page, pg_num)

            edge_items = (
                get_edge_items(mupdf_blocks, pg_num, page_height)
                + get_edge_items(spatial_blocks, pg_num, page_height)
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

    has_title = "title" in wg21_metadata
    # Three metadata pathways, merged here in precedence order (last wins):
    #   1. structure._extract_metadata  - PDF section line scan (lowest precedence)
    #   2. wg21.extract_metadata_from_blocks - PDF block-level scan (wins on conflict)
    # HTML conversion uses a third pathway: html.extract.extract_metadata (DOM scan).
    structure_metadata, sections, nesting_corrections = structure_sections(
        sections, has_title=has_title)
    metadata = {**structure_metadata, **wg21_metadata}

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
                candidate = " ".join(
                    ln.strip().lstrip("# ").strip()
                    for ln in sec.text.split("\n") if ln.strip()
                )
                if candidate:
                    metadata["title"] = candidate
                    break

    if not metadata.get("title") and pdf_info_title:
        _TITLE_BOILERPLATE_RE = re.compile(
            r"^(?:Microsoft\s+Word|Document\d|Untitled|"
            r"[DPN]\d{3,5}(?:R\d+)?|Presentation\d?)$",
            re.IGNORECASE,
        )
        if not _TITLE_BOILERPLATE_RE.match(pdf_info_title):
            metadata["title"] = pdf_info_title

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
        sections = [s for i, s in enumerate(sections) if i not in toc_indices]

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
