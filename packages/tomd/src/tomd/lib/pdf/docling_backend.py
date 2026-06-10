# Copyright (c) 2026 C++ Alliance, Inc. (https://cppalliance.org)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Docling ML backend for table enrichment and discovery.

Provides table structure detection via Docling's TableFormer model,
combined with PyMuPDF spans for font-rich cell content. Docling is an
optional dependency (requires ``pip install docling``); all functions
degrade gracefully when it is unavailable.

Architecture: "Enrichment + Discovery"
  - tomd's rule-based detect_tables() remains the master for table
    detection AND positioning (page_num, lines, insertion order).
  - Docling runs separately to detect table cell grids.
  - enrich_tables_with_docling() matches rule-based Sections to
    Docling-detected tables by page + bbox overlap, then replaces
    the rule-based columns grid with Docling's cell structure
    (populated with PyMuPDF spans for font metadata preservation).
  - discover_tables_with_docling() finds Docling tables that have
    no rule-based counterpart (borderless tables the heuristic
    detector missed), builds TABLE sections from remaining blocks,
    and removes consumed blocks so they don't become paragraphs.
  - Sections that don't match any Docling table are left unchanged.
"""

import logging
from pathlib import Path

from .types import Block, Line, Section, SectionKind, Span, Confidence

_log = logging.getLogger(__name__)

# Tolerance (points) when matching span origins to cell bboxes.
_BBOX_TOLERANCE = 2.0

# Y-gap (points) between span origins that triggers a newline sentinel
# inside a single cell. Typical PDF line spacing is 10-14pt.
_Y_LINE_GAP = 3.0

# Minimum IoU (intersection-over-union by y-range) to consider a
# rule-based section and a Docling table as matching the same table.
_MATCH_Y_IOU_THRESHOLD = 0.3

# Minimum fraction of Docling cells that must have matching PyMuPDF
# spans for the enrichment to be accepted.  Below this, the bbox
# coordinate systems are likely misaligned and enrichment is skipped.
_MIN_CELL_COVERAGE = 0.3

# Higher coverage threshold for discovery (vs enrichment).  Discovery
# creates NEW table sections from scratch, so we require stronger
# evidence that the Docling detection is valid, not a false positive
# from list items or code blocks.
_MIN_DISCOVERY_COVERAGE = 0.6


def docling_available() -> bool:
    """Return True if the docling package is importable."""
    try:
        import docling  # noqa: F401
        return True
    except ImportError:
        return False


def _collect_spans_for_cell(
    cell_bbox: tuple[float, float, float, float],
    page_spans: list[Span],
    y_line_gap: float = _Y_LINE_GAP,
) -> list[Span]:
    """Collect PyMuPDF Spans whose origin falls within *cell_bbox*.

    Sorts hits by reading order (y, x) and inserts ``\\n`` sentinel
    Spans between visual lines (y-gap > *y_line_gap*).  Returns a
    single empty Span when no hits are found.
    """
    x0, y0, x1, y1 = cell_bbox
    tol = _BBOX_TOLERANCE
    hits = [
        s for s in page_spans
        if (x0 - tol <= s.origin[0] <= x1 + tol
            and y0 - tol <= s.origin[1] <= y1 + tol)
    ]

    if not hits:
        return [Span(text="")]

    hits.sort(key=lambda s: (s.origin[1], s.origin[0]))

    result: list[Span] = []
    prev_y: float | None = None
    for span in hits:
        if prev_y is not None and abs(span.origin[1] - prev_y) > y_line_gap:
            result.append(Span(text="\n"))
        result.append(span)
        prev_y = span.origin[1]

    return result


def _flat_spans_for_page(blocks: list[Block], page_num: int) -> list[Span]:
    """Flatten all Spans from *blocks* on a single page."""
    return [
        span
        for b in blocks if b.page_num == page_num
        for line in b.lines
        for span in line.spans
    ]


def _flat_spans_from_section(sec: Section) -> list[Span]:
    """Flatten all Spans from a section's lines.

    After detect_tables(), table blocks are consumed into Section.lines
    and removed from the main block list.  This function retrieves
    spans from the section itself for Docling cell population.
    """
    return [
        span
        for line in sec.lines
        for span in line.spans
    ]


def _normalize_table_bbox(
    bbox, page_height: float,
) -> tuple[float, float, float, float]:
    """Convert a table-level prov bbox to top-left (x0, y_top, x1, y_bot).

    Docling table prov bboxes use PDF-native bottom-left origin
    (y=0 at page bottom).  We flip y using *page_height* so the
    result matches PyMuPDF's top-left coordinate system.
    """
    raw_l, raw_t, raw_r, raw_b = (
        float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b))
    y_top = page_height - max(raw_t, raw_b)
    y_bot = page_height - min(raw_t, raw_b)
    return (raw_l, y_top, raw_r, y_bot)


def extract_docling_tables(
    pdf_path: Path,
) -> dict[int, list[dict]]:
    """Run Docling on *pdf_path* and return per-page table structures.

    Returns ``{page_num: [table_info, ...]}`` where each *table_info*
    is a dict with keys:

    - ``bbox``: normalized (x0, y0, x1, y1) of the full table
    - ``num_rows``: int
    - ``num_cols``: int
    - ``cells``: list of dicts with ``row``, ``col``, ``row_span``,
      ``col_span``, ``bbox``, ``text``

    Returns an empty dict on failure or when Docling is unavailable.
    """
    if not docling_available():
        return {}

    try:
        from docling.document_converter import DocumentConverter
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import PdfFormatOption

        pipeline_opts = PdfPipelineOptions(
            do_table_structure=True,
            do_ocr=False,
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts),
            }
        )

        _log.info("Running Docling table extraction on %s", pdf_path.name)
        docling_result = converter.convert(str(pdf_path))
        doc = docling_result.document

        # Docling table-level prov bboxes use PDF-native bottom-left
        # origin; cell-level bboxes are already in top-left origin.
        # Collect page heights to flip table bboxes to top-left.
        import fitz as _fitz
        _pdf_doc = _fitz.open(str(pdf_path))
        try:
            page_heights: dict[int, float] = {
                pg: _pdf_doc[pg].rect.height
                for pg in range(_pdf_doc.page_count)
            }
        finally:
            _pdf_doc.close()

        page_tables: dict[int, list[dict]] = {}

        for table in doc.tables:
            if not table.prov or not table.prov[0].bbox:
                continue

            page_num = table.prov[0].page_no - 1  # Docling is 1-based
            pg_h = page_heights.get(page_num, 842.0)
            table_bbox = _normalize_table_bbox(table.prov[0].bbox, pg_h)

            cells_info: list[dict] = []
            tg = table.data
            if tg is None:
                continue

            num_rows = tg.num_rows
            num_cols = tg.num_cols

            for cell in tg.table_cells:
                if cell.bbox:
                    raw_l = float(cell.bbox.l)
                    raw_t = float(cell.bbox.t)
                    raw_r = float(cell.bbox.r)
                    raw_b = float(cell.bbox.b)
                    y_top = min(raw_t, raw_b)
                    y_bot = max(raw_t, raw_b)
                    cell_bbox = (raw_l, y_top, raw_r, y_bot)
                else:
                    cell_bbox = (0.0, 0.0, 0.0, 0.0)
                cells_info.append({
                    "row": cell.start_row_offset_idx,
                    "col": cell.start_col_offset_idx,
                    "row_span": cell.end_row_offset_idx - cell.start_row_offset_idx,
                    "col_span": cell.end_col_offset_idx - cell.start_col_offset_idx,
                    "bbox": cell_bbox,
                    "text": cell.text,
                })

            page_tables.setdefault(page_num, []).append({
                "bbox": table_bbox,
                "num_rows": num_rows,
                "num_cols": num_cols,
                "cells": cells_info,
            })

        _log.info("Docling found %d table(s) across %d page(s)",
                  sum(len(v) for v in page_tables.values()),
                  len(page_tables))
        return page_tables

    except Exception:  # Docling is optional enrichment; any failure falls back to rule-based tables
        _log.warning("Docling table extraction failed", exc_info=True)
        return {}


def _section_y_range(sec: Section) -> tuple[float, float] | None:
    """Return (y_min, y_max) for a section from its lines."""
    if not sec.lines:
        return None
    y_min = min(ln.bbox[1] for ln in sec.lines)
    y_max = max(ln.bbox[3] for ln in sec.lines)
    return (y_min, y_max)


def _y_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Intersection-over-union of two y-ranges."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    if union <= 0:
        return 0.0
    return inter / union


def enrich_tables_with_docling(
    table_sections: list[Section],
    docling_tables: dict[int, list[dict]],
    remaining_blocks: list[Block] | None = None,
) -> int:
    """Enrich rule-based table sections with Docling cell grids.

    For each rule-based TABLE section, finds the best-matching Docling
    table on the same page (by y-range IoU).  When matched, replaces
    the section's ``columns`` grid with Docling's cell structure,
    populated with PyMuPDF spans (preserving font metadata).

    Spans are collected from the section's own lines AND from
    *remaining_blocks* on the same page that fall within the Docling
    table bbox.  This ensures that header rows or boundary rows
    excluded by the rule-based detector (but included in Docling's
    grid) still get populated with PyMuPDF spans.

    The section's ``kind``, ``page_num``, ``lines``, and positioning
    are NOT changed -- only the cell content grid is replaced.

    Mutates *table_sections* in place.  Returns the number of sections
    that were enriched.
    """
    if not docling_tables:
        return 0

    from .table import _classify_and_annotate

    enriched = 0

    for sec in table_sections:
        if sec.kind != SectionKind.TABLE:
            continue

        sec_range = _section_y_range(sec)
        if sec_range is None:
            continue

        page_tbls = docling_tables.get(sec.page_num, [])
        if not page_tbls:
            continue

        # Find best matching Docling table by y-range IoU.
        best_iou = 0.0
        best_tbl: dict | None = None
        for tbl in page_tbls:
            tbl_range = (tbl["bbox"][1], tbl["bbox"][3])
            iou = _y_iou(sec_range, tbl_range)
            if iou > best_iou:
                best_iou = iou
                best_tbl = tbl

        if best_tbl is None or best_iou < _MATCH_Y_IOU_THRESHOLD:
            continue

        num_rows = best_tbl["num_rows"]
        num_cols = best_tbl["num_cols"]
        cells = best_tbl["cells"]

        if num_rows < 2 or num_cols < 1:
            continue

        # Collect spans from the section's own lines AND from
        # remaining blocks.  Cross-page join (cleanup_text) may
        # relocate header lines from page N to a block on page N-1,
        # so we also scan page N-1 blocks -- but only for lines
        # whose bbox falls in the header row's y-range to avoid
        # picking up unrelated text.
        #
        # The section may contain lines above the Docling table
        # (consumed by the rule-based detector but not part of the
        # table).  Compute the tightest top-y across all row-0 cells
        # and exclude spans above it.
        row0_y_values = [c["bbox"][1] for c in cells if c["row"] == 0]
        content_y0 = max(row0_y_values) if row0_y_values else best_tbl["bbox"][1]
        page_spans = [
            sp for sp in _flat_spans_from_section(sec)
            if sp.origin[1] >= content_y0 - _BBOX_TOLERANCE
        ]
        if remaining_blocks:
            seen_ids = {id(sp) for sp in page_spans}
            tbl_bbox = best_tbl["bbox"]

            # Same-page: search full table bbox.
            extra_blocks, _ = _blocks_in_bbox(
                remaining_blocks, sec.page_num, tbl_bbox)
            for blk in extra_blocks:
                for line in blk.lines:
                    for sp in line.spans:
                        if id(sp) not in seen_ids:
                            page_spans.append(sp)
                            seen_ids.add(id(sp))

            # Previous page: only search for header-row lines that
            # were relocated by _join_cross_page.  Use the tight
            # content_y0 cutoff (max of row-0 cell y-starts) to
            # exclude non-table text sitting above the header.
            prev_blocks, _ = _blocks_in_bbox(
                remaining_blocks, sec.page_num - 1, tbl_bbox)
            for blk in prev_blocks:
                for line in blk.lines:
                    if line.bbox[1] < content_y0 - _BBOX_TOLERANCE:
                        continue
                    for sp in line.spans:
                        if (id(sp) not in seen_ids
                                and sp.origin[1] >= content_y0 - _BBOX_TOLERANCE):
                            page_spans.append(sp)
                            seen_ids.add(id(sp))
        if not page_spans:
            continue

        # Build Docling grid populated with PyMuPDF spans.
        grid: list[list[list[Span]]] = [
            [[] for _ in range(num_cols)]
            for _ in range(num_rows)
        ]

        for cell in cells:
            r, c = cell["row"], cell["col"]
            cbbox = cell["bbox"]
            if r < num_rows and c < num_cols:
                spans = _collect_spans_for_cell(cbbox, page_spans)
                grid[r][c] = spans

        # Coverage check: skip if too few cells got matching spans.
        total_cells = num_rows * num_cols
        populated = sum(
            1 for row in grid for cell_spans in row
            if any(s.text.strip() for s in cell_spans)
        )
        if total_cells > 0 and populated / total_cells < _MIN_CELL_COVERAGE:
            _log.debug(
                "Docling enrichment skipped for table on page %d: "
                "only %d/%d cells populated (%.0f%%)",
                sec.page_num, populated, total_cells,
                100 * populated / total_cells)
            continue

        # Re-classify with the new grid to get updated kind/strategy.
        kind_val, strategy_val, grid = _classify_and_annotate(grid)

        sec.columns = grid
        sec.table_kind = kind_val
        sec.table_strategy = strategy_val
        sec.table_source = "docling"

        enriched += 1
        _log.info(
            "Docling enriched table on page %d: %d rows x %d cols "
            "[kind=%s, strategy=%s, iou=%.2f]",
            sec.page_num, num_rows, num_cols,
            kind_val, strategy_val, best_iou)

    return enriched


_PAGE_BOTTOM_CUTOFF = 790.0


def absorb_cross_page_spec_rows(
    table_sections: list[Section],
    remaining_blocks: list[Block],
    docling_tables: dict[int, list[dict]],
) -> list[Block]:
    """Absorb orphaned data rows from the previous page into SPEC_TABLE sections.

    When a spec table spans two pages, the first page may contain the
    repeated header and one or more data rows that were not detected as a
    table (e.g. because MuPDF's phantom-table guard rejected the
    full-page bbox).  This function finds such orphaned rows, builds
    grid entries, and inserts them into the enriched SPEC_TABLE grid.

    Returns the updated *remaining_blocks* with consumed blocks removed.
    """
    consumed_indices: set[int] = set()

    for sec in table_sections:
        if sec.table_kind != "spec_table":
            continue
        if not sec.columns or len(sec.columns) < 2 or len(sec.columns[0]) < 3:
            continue

        dtbls = docling_tables.get(sec.page_num, [])
        if not dtbls:
            continue
        dtbl = dtbls[0]
        cells = dtbl["cells"]
        num_cols = dtbl["num_cols"]
        if num_cols < 2:
            continue

        # Compute column x-boundaries from Docling cell bboxes.
        col_x_min: dict[int, float] = {}
        col_x_max: dict[int, float] = {}
        for c in cells:
            ci = c["col"]
            x0, _, x1, _ = c["bbox"]
            col_x_min[ci] = min(col_x_min.get(ci, x0), x0)
            col_x_max[ci] = max(col_x_max.get(ci, x1), x1)
        if len(col_x_max) < num_cols:
            continue

        bounds: list[float] = []
        for i in range(num_cols - 1):
            bounds.append((col_x_max[i] + col_x_min[i + 1]) / 2)

        # Header text from the first column (e.g. "expression").
        hdr_c0 = "".join(s.text for s in sec.columns[0][0]).strip().lower()
        if not hdr_c0:
            continue

        prev_page = sec.page_num - 1
        prev_blocks = [
            (i, b) for i, b in enumerate(remaining_blocks)
            if b.page_num == prev_page and i not in consumed_indices
        ]
        if not prev_blocks:
            continue

        # Locate the header anchor on the previous page.  Search
        # bottom-up: cross-page join may merge page-N header lines
        # into a page-(N-1) block at a low y, so we want the LAST
        # (lowest y) match, which is the genuine previous-page header.
        header_y: float | None = None
        for idx, blk in reversed(prev_blocks):
            for ln in reversed(blk.lines):
                txt = " ".join(s.text for s in ln.spans).strip().lower()
                if hdr_c0 in txt:
                    header_y = ln.bbox[1]
                    break
            if header_y is not None:
                break
        if header_y is None:
            continue

        def _col_for_x(x: float) -> int:
            for i, b in enumerate(bounds):
                if x < b:
                    return i
            return num_cols - 1

        # Collect table-area lines on the previous page.
        col_lines: dict[int, list[tuple[float, Line, int]]] = {
            i: [] for i in range(num_cols)
        }
        block_indices: set[int] = set()
        for idx, blk in prev_blocks:
            for ln in blk.lines:
                y_mid = (ln.bbox[1] + ln.bbox[3]) / 2
                if y_mid < header_y - 5 or y_mid > _PAGE_BOTTOM_CUTOFF:
                    continue
                ci = _col_for_x(ln.bbox[0])
                col_lines[ci].append((y_mid, ln, idx))
                block_indices.add(idx)

        for ci in col_lines:
            col_lines[ci].sort(key=lambda t: t[0])

        # Determine rows by clustering y-values in shorter columns (0..N-2).
        anchor_ys: list[float] = []
        for ci in range(num_cols - 1):
            for y, _, _ in col_lines[ci]:
                if not any(abs(y - ay) < 10 for ay in anchor_ys):
                    anchor_ys.append(y)
        anchor_ys.sort()

        if not anchor_ys:
            continue

        # Cluster anchors into distinct rows.
        row_centers: list[float] = []
        cluster = [anchor_ys[0]]
        for ay in anchor_ys[1:]:
            if ay - cluster[-1] > 25:
                row_centers.append(sum(cluster) / len(cluster))
                cluster = [ay]
            else:
                cluster.append(ay)
        row_centers.append(sum(cluster) / len(cluster))

        if len(row_centers) < 2:
            # Only the header, no data rows on the previous page.
            consumed_indices.update(block_indices)
            continue

        header_center = row_centers[0]
        data_row_centers = row_centers[1:]

        # Row boundaries: midpoint between consecutive row centers.
        row_boundaries: list[tuple[float, float]] = []
        for i, drc in enumerate(data_row_centers):
            if i == 0:
                y_start = (header_center + drc) / 2
            else:
                y_start = (data_row_centers[i - 1] + drc) / 2
            if i == len(data_row_centers) - 1:
                y_end = _PAGE_BOTTOM_CUTOFF
            else:
                y_end = (drc + data_row_centers[i + 1]) / 2
            row_boundaries.append((y_start, y_end))

        new_rows: list[list[list[Span]]] = []
        for y_start, y_end in row_boundaries:
            row: list[list[Span]] = [[] for _ in range(num_cols)]
            for ci in range(num_cols):
                cell_lines = [
                    (y, ln) for y, ln, _ in col_lines[ci]
                    if y_start <= y <= y_end
                ]
                cell_lines.sort(key=lambda t: t[0])
                for _, ln in cell_lines:
                    if row[ci]:
                        row[ci].append(Span(text="\n"))
                    row[ci].extend(ln.spans)
            populated_cols = sum(1 for cell in row if cell)
            if populated_cols >= 2:
                new_rows.append(row)

        if new_rows:
            for i, row in enumerate(new_rows):
                sec.columns.insert(1 + i, row)
            consumed_indices.update(block_indices)
            _log.info(
                "Absorbed %d orphaned row(s) from page %d into "
                "spec_table on page %d",
                len(new_rows), prev_page, sec.page_num)

    if consumed_indices:
        remaining_blocks = [
            b for i, b in enumerate(remaining_blocks)
            if i not in consumed_indices
        ]
    return remaining_blocks


def _blocks_in_bbox(
    blocks: list[Block],
    page_num: int,
    bbox: tuple[float, float, float, float],
) -> tuple[list[Block], list[int]]:
    """Return blocks on *page_num* whose y-midpoint falls within *bbox*.

    Returns ``(matching_blocks, matching_indices)`` where indices
    refer to positions in the original *blocks* list.
    """
    x0, y0, x1, y1 = bbox
    matched: list[Block] = []
    indices: list[int] = []
    for idx, blk in enumerate(blocks):
        if blk.page_num != page_num:
            continue
        if not blk.lines:
            continue
        blk_y_mid = (blk.lines[0].bbox[1] + blk.lines[-1].bbox[3]) / 2.0
        if y0 - _BBOX_TOLERANCE <= blk_y_mid <= y1 + _BBOX_TOLERANCE:
            matched.append(blk)
            indices.append(idx)
    return matched, indices


def _render_table_text(rows: list[list[list[Span]]]) -> str:
    """Render a grid of Span lists to pipe-delimited text summary."""
    parts: list[str] = []
    for row in rows:
        cells = []
        for cell_spans in row:
            cells.append("".join(s.text for s in cell_spans).strip())
        parts.append(" | ".join(cells))
    return "\n".join(parts)


def discover_tables_with_docling(
    docling_tables: dict[int, list[dict]],
    remaining_blocks: list[Block],
    existing_table_sections: list[Section],
) -> tuple[list[Section], list[Block]]:
    """Discover tables that only Docling detected (rule-based missed).

    For each Docling table that does NOT overlap any existing rule-based
    TABLE section, collects PyMuPDF blocks within the table bbox, builds
    a cell grid populated with spans, classifies, and creates a new
    ``Section(kind=TABLE)``.

    Returns ``(new_table_sections, updated_remaining_blocks)`` where
    consumed blocks have been removed from the remaining list.
    """
    if not docling_tables:
        return [], remaining_blocks

    from .table import _classify_and_annotate

    new_sections: list[Section] = []
    consumed_indices: set[int] = set()

    for page_num in sorted(docling_tables):
        for tbl in docling_tables[page_num]:
            tbl_bbox = tbl["bbox"]
            tbl_y_range = (tbl_bbox[1], tbl_bbox[3])

            # Skip if this Docling table overlaps an existing rule-based table.
            overlaps = False
            for sec in existing_table_sections:
                if sec.page_num != page_num:
                    continue
                sec_range = _section_y_range(sec)
                if sec_range and _y_iou(sec_range, tbl_y_range) >= _MATCH_Y_IOU_THRESHOLD:
                    overlaps = True
                    break
            if overlaps:
                continue

            num_rows = tbl["num_rows"]
            num_cols = tbl["num_cols"]
            cells = tbl["cells"]

            if num_rows < 2 or num_cols < 1:
                continue

            # Collect blocks within the Docling table bbox.
            matched_blocks, matched_indices = _blocks_in_bbox(
                remaining_blocks, page_num, tbl_bbox)
            if not matched_blocks:
                continue

            page_spans = [
                span
                for blk in matched_blocks
                for line in blk.lines
                for span in line.spans
            ]
            if not page_spans:
                continue

            # Build cell grid from Docling structure + PyMuPDF spans.
            grid: list[list[list[Span]]] = [
                [[] for _ in range(num_cols)]
                for _ in range(num_rows)
            ]

            for cell in cells:
                r, c = cell["row"], cell["col"]
                cbbox = cell["bbox"]
                if r < num_rows and c < num_cols:
                    grid[r][c] = _collect_spans_for_cell(cbbox, page_spans)

            # Coverage guard.
            total_cells = num_rows * num_cols
            populated = sum(
                1 for row in grid for cell_spans in row
                if any(s.text.strip() for s in cell_spans)
            )
            if total_cells > 0 and populated / total_cells < _MIN_DISCOVERY_COVERAGE:
                _log.debug(
                    "Docling discovery skipped table on page %d: "
                    "only %d/%d cells populated (%.0f%%)",
                    page_num, populated, total_cells,
                    100 * populated / total_cells)
                continue

            kind_val, strategy_val, grid = _classify_and_annotate(grid)

            if kind_val == "false_positive":
                continue

            all_lines = [
                line
                for blk in matched_blocks
                for line in blk.lines
            ]
            text = _render_table_text(grid)

            new_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=page_num,
                columns=grid,
                table_kind=kind_val,
                table_strategy=strategy_val,
                table_source="docling_discovered",
            ))

            consumed_indices.update(matched_indices)
            _log.info(
                "Docling discovered new table on page %d: %d rows x %d cols "
                "[kind=%s, strategy=%s, %d blocks consumed]",
                page_num, num_rows, num_cols,
                kind_val, strategy_val, len(matched_blocks))

    if consumed_indices:
        remaining_blocks = [
            b for i, b in enumerate(remaining_blocks)
            if i not in consumed_indices
        ]

    return new_sections, remaining_blocks
