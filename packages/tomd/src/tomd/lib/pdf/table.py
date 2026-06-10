"""Table detection, classification, and rendering strategy from MuPDF blocks.

Table Family (6 kinds, corpus-validated against 768 tables from 124 WG21 PDFs):

  CLEAN_MATRIX   Short-text cells (<15 words).  Pipe table.
                  Schedule grids, vote tallies, feature comparisons.
  PROSE_TABLE    Any cell >15 words.  Pipe table (HTML if cells have newlines).
                  Rationale tables, design-alternative comparisons.
  CODE_COMPARISON  "Tony Tables".  High monospace ratio, few cols/rows.  HTML
                  table with <pre> blocks.  Side-by-side before/after code.
  SPEC_TABLE     WG21 requirement tables.  3 columns, header matches
                  "expression|operation" + "return|type".  HTML table.
                  Concept requirement tables (io_awaitable, executor).
  KEY_VALUE      2-column tables with short field labels in col-0 (<=8 words)
                  and longer descriptive values in col-1 (>15 words).  Pipe
                  table.  Platform/compiler schema tables (Field | Value).
                  Single-orphan continuation blocks at col-1 x-position are
                  backward-merged into the previous row via partial_absorbed.
  FALSE_POSITIVE  >50% empty cells or ragged columns.  Skipped.

Five detection passes (run in order, each consumes matched blocks):

  Pass 1 (inline-column): blocks with 2+ lines whose x-starts have gaps
    > _COLUMN_GAP_THRESHOLD.  Orphan absorption for wrapped cell first-lines.
  Pass 2 (side-by-side blocks): each cell is a separate MuPDF block at a
    different x-position (Tony Tables with multi-line code cells).
  Pass 3 (horizontal-row): narrow poll/vote grids with small column gaps.
  Pass 4 (column-aligned): borderless tables where MuPDF distributes columns
    across separate single-column blocks.  Span-level x-position clustering.
  Pass 4b (spec-label): WG21 requirement tables anchored by a "Table N - ..."
    caption.  Collects all blocks (including monospace expression cells) in
    the spatial region below the label.  Cross-page continuation supported.
  Pass 5 (MuPDF native): fallback using MuPDF find_tables() on remaining blocks.

Classification flow:
  detect_tables() -> _compute_table_signals() -> _classify_table()
  -> _STRATEGY_MAP -> emit.py rendering dispatch.

Optional Docling enrichment (ml_tables=True):
  Docling re-grids cells via ML, then _classify_and_annotate re-classifies.
  Can upgrade PROSE_TABLE to SPEC_TABLE when Docling provides the header row.
"""

import logging
import re
from collections import Counter, defaultdict
from dataclasses import replace
from enum import Enum
from typing import NamedTuple, Optional

from .types import Block, Line, Span, Section, SectionKind, Confidence

_log = logging.getLogger(__name__)


_COLUMN_GAP_THRESHOLD = 50.0
_MIN_TABLE_ROWS = 2
_COLUMN_X_TOLERANCE = 10.0
_COLUMN_X_END_TOLERANCE = 1.0
_TABLE_Y_OVERLAP_MARGIN = 5.0

_COLUMN_X_BUCKET = 5.0    # bucket size for x-position clustering
_Y_BAND_HEIGHT   = 15.0   # bucket size for y-position clustering
_MIN_SHARED_YBANDS = 2    # x must co-occur with other columns in 2+ y-bands

# Partial-row absorption: a columnar block with fewer columns than the
# table header may be a row whose rightmost columns were split into
# separate blocks by MuPDF.  Accept it when all its x-positions are a
# subset of the table's reference columns and the y-gap is small.
_PARTIAL_ROW_MAX_Y_GAP = 25.0

# Side-by-side table constants
_SBS_MAX_SCAN_GAP = 30.0  # max y-gap before stopping body scan
_SBS_MAX_SCAN_GAP_ALIGNED = 50.0  # larger tolerance for column-aligned blocks
_SBS_COL_ALIGN_TOL = 25.0  # max x-distance to count as column-aligned
_SBS_ROW_Y_BAND = 10.0    # max y-gap between col-0 blocks in the same row
_ATOMIZED_HDR_MAX_HEIGHT = 60.0  # max header region height for recovery
_ATOMIZED_HDR_MAX_CONSEC_SINGLE = 3  # stop after N consecutive 1-col rows

# Guard: bare section number on line 0 of a 2-line block.
# Prevents misclassifying heading blocks (large bold number + right-aligned
# title) as tables. Kretz-style LaTeX papers (P3948, P3844, P4012) produce
# blocks where the section number sits at x=73 and the ALL-CAPS title at
# x=440+, a gap of ~370pt that far exceeds _COLUMN_GAP_THRESHOLD.
_BARE_HEADING_NUM_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*|[A-Z]+|[IVXLCDM]+)\.?\s*$"
)
_HEADING_NUM_MAX_WORDS = 8

# WG21 stable name in brackets: [basic.memobj], [intro.object], etc.
# These appear as bold right-aligned text on WG21 section heading lines.
_STABLE_NAME_RE = re.compile(
    r"^\[[\w.]+\]$"
)


# ---------------------------------------------------------------------------
# Table classification (integrated from table_analyzer.py)
# Thresholds corpus-validated against 768 tables from 124 WG21 PDFs.
# ---------------------------------------------------------------------------

class TableKind(Enum):
    """What kind of table this section represents."""
    CLEAN_MATRIX = "clean_matrix"
    INLINE_GRID = "inline_grid"
    PROSE_TABLE = "prose_table"
    CODE_COMPARISON = "code_comparison"
    SPEC_TABLE = "spec_table"
    KEY_VALUE = "key_value"
    BIBLIOGRAPHY = "bibliography"
    NB_BALLOT = "nb_ballot"
    FALSE_POSITIVE = "false_positive"


class TableStrategy(Enum):
    """How to render this table in markdown."""
    PIPE_TABLE = "pipe_table"
    CODE_BLOCKS = "code_blocks"
    HTML_TABLE = "html_table"
    SKIP = "skip"


_STRATEGY_MAP = {
    TableKind.CLEAN_MATRIX: TableStrategy.PIPE_TABLE,
    TableKind.PROSE_TABLE: TableStrategy.PIPE_TABLE,
    TableKind.CODE_COMPARISON: TableStrategy.HTML_TABLE,
    TableKind.SPEC_TABLE: TableStrategy.HTML_TABLE,
    TableKind.KEY_VALUE: TableStrategy.PIPE_TABLE,
    TableKind.BIBLIOGRAPHY: TableStrategy.PIPE_TABLE,
    TableKind.NB_BALLOT: TableStrategy.HTML_TABLE,
    TableKind.FALSE_POSITIVE: TableStrategy.SKIP,
}

_EMPTY_RATIO_THRESHOLD = 0.50
_MONO_RATIO_THRESHOLD = 0.70
_PROSE_WORD_THRESHOLD = 15
_KV_COL0_MAX_WORDS = 8

# Bibliography: col-0 cells are bracketed reference labels.
_BIBLIOGRAPHY_LABEL_RE = re.compile(r"^\[[\w\d.+\-]+\]$")
_BIBLIOGRAPHY_LABEL_RATIO = 0.60

# NB-Ballot: col-0 cells are national body comment IDs like [ES-047]
# or bare country codes like [SE], [FI]. Also matches the header "NB number".
_NB_BALLOT_ID_RE = re.compile(
    r"^\[(?:[A-Z]{2}(?:[-\s]\d{2,3})?)\]$|^NB\s+number$"
)


class _MatchResult(NamedTuple):
    """Result of a Pass 1 inner-loop decision branch."""
    advance_to: int
    new_ref_cols: Optional[list] = None
    absorbed_ids: frozenset = frozenset()
    multi_orphan: bool = False


def _render_table_text(rows: list[list[list]]) -> str:
    """Render table rows (list of cells, each cell a list of Spans) to pipe-delimited text."""
    return "\n".join(
        " | ".join(
            "".join(s.text for s in cell).strip()
            for cell in row
        )
        for row in rows
    )


def _header_dedup_start(
    prev_columns: list[list[list]],
    cur_rows: list[list[list]],
) -> int:
    """Return row index to start appending from cur_rows.

    If the first row of cur_rows textually matches the header (first
    row) of prev_columns, return 1 to skip the duplicate header.
    Otherwise return 0 (append all rows).
    """
    if cur_rows and prev_columns:
        hdr_prev = [
            "".join(s.text for s in cell).strip()
            for cell in prev_columns[0]
        ]
        hdr_cur = [
            "".join(s.text for s in cell).strip()
            for cell in cur_rows[0]
        ]
        if hdr_prev == hdr_cur:
            return 1
    return 0


def _find_column_xs(blocks: list[Block]) -> frozenset[float]:
    """Return x-start positions that are genuine table columns.

    Uses the shared-y-band approach: an x position qualifies only when it
    co-occurs in the same y-band with at least one other distinct x position,
    across at least _MIN_SHARED_YBANDS such y-bands. Body text at the left
    margin is alone in every y-band and therefore never qualifies.

    Y-bands are scoped per page so that two lines on different pages at the
    same absolute y coordinate are not treated as sharing a row.
    """
    yband_to_xs: dict[tuple[int, int], set[int]] = defaultdict(set)
    for block in blocks:
        for line in block.lines:
            if not line.spans or not line.text.strip():
                continue
            x_key = round(line.bbox[0] / _COLUMN_X_BUCKET)
            y_key = round(((line.bbox[1] + line.bbox[3]) / 2.0) / _Y_BAND_HEIGHT)
            yband_to_xs[(block.page_num, y_key)].add(x_key)

    shared_counts: Counter[int] = Counter()
    for xs in yband_to_xs.values():
        if len(xs) >= 2:
            for x_key in xs:
                shared_counts[x_key] += 1

    return frozenset(
        x_key * _COLUMN_X_BUCKET
        for x_key, count in shared_counts.items()
        if count >= _MIN_SHARED_YBANDS
    )


def _is_column_aligned_orphan(block: Block, column_xs: frozenset[float]) -> bool:
    """True if block is a single-line block whose x0 aligns with a known column.

    Only single-line blocks qualify. Multi-line non-columnar blocks are genuine
    prose or captions and must not be absorbed into a table run.
    """
    if len(block.lines) != 1 or not block.lines[0].spans:
        return False
    x0 = block.lines[0].bbox[0]
    return any(abs(x0 - cx) <= _COLUMN_X_BUCKET for cx in column_xs)


def _block_is_monospace(block: Block) -> bool:
    """True if most of the block's text spans are monospace.

    Used to prevent the single-header multi-orphan scan from absorbing
    code comparison blocks that belong to side-by-side tables.
    """
    text_spans = [s for ln in block.lines for s in ln.spans
                  if s.text.strip()]
    if not text_spans:
        return False
    return sum(1 for s in text_spans if s.monospace) >= len(text_spans) / 2


def _is_partial_row(block: Block, ref_cols: list[float],
                    prev_bottom: float, same_page: bool) -> bool:
    """True if block is a columnar row with fewer columns than ref_cols.

    Matches when ALL of the block's column x-positions align with a subset
    of the reference columns, the block is on the same page, and the
    vertical gap is within _PARTIAL_ROW_MAX_Y_GAP.
    """
    cols = _block_column_positions(block)
    if cols is None or len(cols) >= len(ref_cols):
        return False
    if not same_page:
        return False
    y_gap = block.bbox[1] - prev_bottom
    if y_gap > _PARTIAL_ROW_MAX_Y_GAP or y_gap < 0:
        return False
    for x in cols:
        if not any(abs(x - rx) < _COLUMN_X_TOLERANCE for rx in ref_cols):
            return False
    return True


def _is_trailing_continuation(block: Block, ref_cols: list[float],
                               prev_bottom: float, same_page: bool) -> bool:
    """True if block is a single-line block continuing a non-first column.

    Trailing continuations appear after the last full row when a cell's
    text wraps across multiple PDF lines delivered as separate blocks.
    The non-first-column guard distinguishes them from new paragraphs
    which start at the left margin (column 0).
    """
    if len(block.lines) != 1 or not block.lines[0].spans:
        return False
    if not same_page:
        return False
    y_gap = block.bbox[1] - prev_bottom
    if y_gap > _PARTIAL_ROW_MAX_Y_GAP or y_gap < 0:
        return False
    x0 = block.lines[0].bbox[0]
    return any(abs(x0 - ref_cols[ci]) <= _COLUMN_X_TOLERANCE
               for ci in range(1, len(ref_cols)))


def _block_column_positions(block: Block) -> list[float] | None:
    """Return the x-start positions of columns in a block, or None.

    A block is columnar if it has 2+ lines where every line after
    the first starts significantly to the right of the first line's
    x-start position.
    """
    if len(block.lines) < 2:
        return None

    # Guard: 2-line blocks where line 0 is a bold bare section number in a
    # heading-sized font are section headings, not table rows. The title text
    # on line 1 is right-aligned or centered, creating a large x-gap that
    # would otherwise trigger columnar detection.
    if len(block.lines) == 2:
        line0 = block.lines[0]
        line1 = block.lines[1]
        if (line0.is_bold
                and _BARE_HEADING_NUM_RE.match(line0.text.strip())
                and line0.font_size > line1.font_size
                and len(line1.text.split()) <= _HEADING_NUM_MAX_WORDS):
            return None

    # Guard: WG21 section headings where line 0 is the section title
    # (e.g. "6.8 Memory and objects") and line 1 is a bold stable name
    # in brackets (e.g. "[basic.memobj]") far to the right.
    if len(block.lines) == 2:
        line1 = block.lines[1]
        if (line1.is_bold
                and _STABLE_NAME_RE.match(line1.text.strip())):
            return None

    x_starts = []
    for line in block.lines:
        if not line.spans:
            return None
        x_starts.append(line.bbox[0])

    for i in range(1, len(x_starts)):
        if x_starts[i] - x_starts[0] < _COLUMN_GAP_THRESHOLD:
            return None

    return x_starts


def _columns_match(
    cols_a: list[float],
    cols_b: list[float],
    *,
    block_a: Block | None = None,
    block_b: Block | None = None,
) -> bool:
    """Check if two column position lists represent the same table structure.

    When both *block_a* and *block_b* are provided, a secondary x-end
    check fires for right-aligned columns whose x-start varies with
    cell text length but whose x-end is fixed by the layout engine.
    """
    if len(cols_a) != len(cols_b):
        return False
    if block_a is not None and block_b is not None:
        ends_a = [ln.bbox[2] for ln in block_a.lines]
        ends_b = [ln.bbox[2] for ln in block_b.lines]
        for sa, sb, ea, eb in zip(cols_a, cols_b, ends_a, ends_b):
            start_ok = abs(sa - sb) < _COLUMN_X_TOLERANCE
            end_ok = abs(ea - eb) < _COLUMN_X_END_TOLERANCE
            if not (start_ok or end_ok):
                return False
        return True
    return all(abs(a - b) < _COLUMN_X_TOLERANCE for a, b in zip(cols_a, cols_b))


def _is_subset_columns(cols: list[float],
                       ref_cols: list[float]) -> bool:
    """True when *cols* is a strict subset of *ref_cols*.

    Every x-position in *cols* must match a distinct column in
    *ref_cols* within tolerance, and *cols* must have fewer columns.
    Used by the multi-orphan lookahead to confirm a table continues
    via a block that covers only some columns (e.g. col1+col2 of a
    3-column table).
    """
    if len(cols) >= len(ref_cols):
        return False
    return all(
        any(abs(px - rx) < _COLUMN_X_TOLERANCE for rx in ref_cols)
        for px in cols)


# Max y-gap between consecutive columnar blocks to consider them part of the
# same table when they share a column count but differ in x-positions (e.g.
# centered header row + left-aligned data rows).
_RELAXED_MATCH_MAX_Y_GAP = 40.0


def _columns_count_match(cols_a: list[float], cols_b: list[float],
                         block_a_bottom: float, block_b_top: float,
                         same_page: bool) -> bool:
    """Relaxed match: same column count, same page, close y-proximity.

    Handles PDF tables where header cells are centered differently from
    data cells.  The column count is identical but x-positions differ by
    more than _COLUMN_X_TOLERANCE.
    """
    if not same_page:
        return False
    if len(cols_a) != len(cols_b):
        return False
    if len(cols_a) < 2:
        return False
    if block_b_top - block_a_bottom > _RELAXED_MATCH_MAX_Y_GAP:
        return False
    return True


def _cluster_x_positions(x_vals: list[float],
                         gap: float = _COLUMN_GAP_THRESHOLD) -> list[float]:
    """Cluster nearby x values, return sorted representative per cluster."""
    if not x_vals:
        return []
    xs = sorted(set(round(x, 1) for x in x_vals))
    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] < gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [min(c) for c in clusters]


def _nearest_column(x: float, col_xs: list[float]) -> int:
    """Return index of nearest column in col_xs for position x."""
    return min(range(len(col_xs)), key=lambda ci: abs(x - col_xs[ci]))


_SBS_MUPDF_DEFER_MIN_ROWS = 5

def _detect_side_by_side_tables(
    blocks: list[Block],
    *,
    atomized_only: bool = False,
    page_mupdf_tables: dict[int, list[dict]] | None = None,
) -> tuple[list[Section], set[int]]:
    """Detect tables where each cell is a separate side-by-side block.

    When *atomized_only* is True, only tables whose headers required
    atomized-header recovery are emitted; regular side-by-side tables
    are skipped so that Pass 1 (horizontal rows) gets first dibs.

    When *page_mupdf_tables* is provided, candidate table regions that
    overlap a MuPDF ``find_tables()`` bbox with >= ``_SBS_MUPDF_DEFER_MIN_ROWS``
    rows are skipped so MuPDF Native (Pass 5) can handle them intact.

    Returns (table_sections, used_block_indices).
    """
    table_sections: list[Section] = []
    used: set[int] = set()
    i = 0

    while i < len(blocks):
        cols = _block_column_positions(blocks[i])
        if cols is None or len(cols) < 2:
            i += 1
            continue

        header = blocks[i]
        page = header.page_num
        h_bottom = header.bbox[3]

        # Gather body candidates: same page, below header
        body_candidates: list[tuple[int, Block]] = []
        j = i + 1
        while j < len(blocks):
            b = blocks[j]
            if b.page_num != page:
                break
            if b.bbox[1] >= h_bottom - _TABLE_Y_OVERLAP_MARGIN:
                body_candidates.append((j, b))
            j += 1

        if not body_candidates:
            i += 1
            continue

        # Determine column structure from body block x-positions
        body_xs = [b.bbox[0] for _, b in body_candidates]
        col_xs = _cluster_x_positions(body_xs)

        if len(col_xs) < 2:
            i += 1
            continue

        # Cross-page extension: when the table reaches very close to
        # the page bottom, extend body_candidates to include column-
        # aligned blocks from the top of the next page.  Guards:
        #  - Table bottom must be within ~90pt of the page edge (700+).
        #  - Continuation blocks must start near the page top (y < 160).
        #  - At least 2 distinct columns must be represented.
        #  - Stop at numbered headings or misaligned blocks.
        _SBS_PAGE_BOTTOM_THRESH = 660.0
        _SBS_CROSS_PAGE_COL_TOLERANCE = 30.0
        _SBS_CROSS_PAGE_Y_MAX = 160.0
        last_body_y1 = max(b.bbox[3] for _, b in body_candidates)
        if last_body_y1 > _SBS_PAGE_BOTTOM_THRESH and len(col_xs) >= 2:
            next_page = page + 1
            cross_candidates: list[tuple[int, Block]] = []
            cross_cols_seen: set[int] = set()
            for k in range(j, len(blocks)):
                nb = blocks[k]
                if nb.page_num > next_page:
                    break
                if nb.page_num != next_page:
                    continue
                if nb.bbox[1] > _SBS_CROSS_PAGE_Y_MAX:
                    break
                nb_text = "".join(
                    s.text for ln in nb.lines for s in ln.spans).strip()
                if not nb_text:
                    continue
                if (_SPEC_HEADING_NUM_RE.match(nb_text)
                        or _SPEC_TABLE_LABEL_RE.match(nb_text)):
                    break
                nearest = _nearest_column(nb.bbox[0], col_xs)
                dist = abs(nb.bbox[0] - col_xs[nearest])
                if dist > _SBS_CROSS_PAGE_COL_TOLERANCE:
                    if cross_candidates:
                        break
                    continue
                cross_candidates.append((k, nb))
                cross_cols_seen.add(nearest)
            if len(cross_cols_seen) >= 2:
                body_candidates.extend(cross_candidates)
                _log.debug(
                    "Side-by-side cross-page: extended to page %d "
                    "(%d total body candidates, %d cols)",
                    next_page, len(body_candidates),
                    len(cross_cols_seen))

        # Guard: body suggests more columns than the header declares.
        # A non-table block (caption, label) sitting at a third x-position
        # inflates col_xs. Reject so Pass 4 (MuPDF native) handles it.
        #
        # Exception: atomized headers where each column heading is a
        # separate Block. Collect all blocks in the header region and
        # cluster their x-positions; if the cluster matches col_xs the
        # table is accepted with a multi-block header.
        atomized_hdr: set[int] | None = None
        if len(col_xs) > len(cols):
            hdr_y0 = header.bbox[1]
            hdr_block_set: set[int] = {i}
            for k in range(i + 1, j):
                nb = blocks[k]
                if nb.page_num != page:
                    break
                if nb.bbox[1] - hdr_y0 > _ATOMIZED_HDR_MAX_HEIGHT:
                    break
                hdr_block_set.add(k)
            ext_xs = _cluster_x_positions(
                [blocks[k].bbox[0] for k in sorted(hdr_block_set)])
            if len(ext_xs) >= len(col_xs):
                atomized_hdr = hdr_block_set
                _log.debug("Atomized header recovery: %d blocks, "
                           "%d cols on page %d", len(hdr_block_set),
                           len(ext_xs), page)
            else:
                i += 1
                continue
        elif atomized_only:
            i += 1
            continue

        # When we recovered an atomized header, exclude header blocks
        # from body_candidates and reset h_bottom.
        if atomized_hdr is not None:
            effective_h_bottom = max(
                blocks[k].bbox[3] for k in atomized_hdr)
            body_candidates = [
                (idx, b) for idx, b in body_candidates
                if idx not in atomized_hdr
                and b.bbox[1] >= effective_h_bottom - _TABLE_Y_OVERLAP_MARGIN
            ]
            if not body_candidates:
                i += 1
                continue
            h_bottom = effective_h_bottom

        # Fix ordering: _column_aware_sort may place a non-col-0 block
        # slightly before a col-0 block that starts the next row (when
        # the non-col-0 block has marginally lower y).  Swap adjacent
        # pairs so col-0 blocks come first within the same y-band.
        _SBS_COL0_SWAP_BAND = 10.0
        body_sorted = list(body_candidates)
        si = 0
        while si < len(body_sorted) - 1:
            _, b_cur = body_sorted[si]
            _, b_nxt = body_sorted[si + 1]
            c_cur = _nearest_column(b_cur.bbox[0], col_xs)
            c_nxt = _nearest_column(b_nxt.bbox[0], col_xs)
            if (c_cur != 0 and c_nxt == 0
                    and b_cur.page_num == b_nxt.page_num
                    and 0 < b_nxt.bbox[1] - b_cur.bbox[1]
                            < _SBS_COL0_SWAP_BAND):
                body_sorted[si], body_sorted[si + 1] = (
                    body_sorted[si + 1], body_sorted[si])
                si += 2
            else:
                si += 1

        # Group candidates into rows.
        # A new row starts when a col-0 block appears and the
        # whitespace gap (y0 of new block minus y1 of last col-0
        # block) exceeds _SBS_ROW_Y_BAND.  Atomized single-line
        # blocks within the band belong to the same logical row.
        # A page boundary always forces a new row.
        rows: list[list[tuple[int, Block]]] = []
        current_row: list[tuple[int, Block]] = []
        has_col0 = False
        last_col0_y1 = h_bottom
        last_col0_page = -1
        last_bottom = h_bottom

        for idx, blk in body_sorted:
            col = _nearest_column(blk.bbox[0], col_xs)
            gap = blk.bbox[1] - last_bottom
            if gap > _SBS_MAX_SCAN_GAP:
                col_dist = abs(blk.bbox[0] - col_xs[col])
                if col_dist > _SBS_COL_ALIGN_TOL or gap > _SBS_MAX_SCAN_GAP_ALIGNED:
                    if current_row:
                        rows.append(current_row)
                    break
            if col == 0:
                new_page = blk.page_num != last_col0_page and last_col0_page >= 0
                gap_exceeds = blk.bbox[1] - last_col0_y1 > _SBS_ROW_Y_BAND
                if has_col0 and (new_page or gap_exceeds):
                    rows.append(current_row)
                    current_row = [(idx, blk)]
                    has_col0 = True
                else:
                    current_row.append((idx, blk))
                    has_col0 = True
                last_col0_y1 = blk.bbox[3]
                last_col0_page = blk.page_num
            else:
                current_row.append((idx, blk))
            last_bottom = max(last_bottom, blk.bbox[3])

        if current_row and current_row not in rows:
            rows.append(current_row)

        # Validate: each row must span 2+ columns.  Check line-level
        # x-positions (not just block x0) because single-block rows can
        # contain multiple internal columns (Phase 15 per-line logic).
        valid_rows: list[list[tuple[int, Block]]] = []
        consec_single = 0
        scanned_rows: list[tuple[list[tuple[int, Block]], bool]] = []
        for row in rows:
            row_cols: set[int] = set()
            for _, b in row:
                for ln in b.lines:
                    row_cols.add(_nearest_column(ln.bbox[0], col_xs))
            if len(row_cols) >= 2:
                valid_rows.append(row)
                scanned_rows.append((row, True))
                consec_single = 0
            else:
                if atomized_hdr is not None:
                    scanned_rows.append((row, False))
                    consec_single += 1
                    if consec_single >= _ATOMIZED_HDR_MAX_CONSEC_SINGLE:
                        break
                else:
                    break

        # Build consumed_rows: include all rows up to and including the
        # last valid row.  Skipped single-column rows BETWEEN valid rows
        # are internal sub-headers and must be consumed.  Skipped rows
        # AFTER the last valid row are post-table prose and must NOT be
        # consumed.
        last_valid_idx = -1
        for si in range(len(scanned_rows) - 1, -1, -1):
            if scanned_rows[si][1]:
                last_valid_idx = si
                break
        consumed_rows: list[list[tuple[int, Block]]] = [
            row for ri, (row, _) in enumerate(scanned_rows)
            if ri <= last_valid_idx
        ]

        min_rows = _MIN_TABLE_ROWS
        if atomized_only and atomized_hdr is not None:
            min_rows = max(min_rows, 5)
        if len(valid_rows) < min_rows:
            i += 1
            continue

        num_cols = len(col_xs)

        # Build header row.
        if atomized_hdr is not None:
            header_cells = [[] for _ in range(num_cols)]
            all_lines: list = []
            for k in sorted(atomized_hdr):
                blk_k = blocks[k]
                ci = _nearest_column(blk_k.bbox[0], col_xs)
                for ln in blk_k.lines:
                    if header_cells[ci]:
                        header_cells[ci].append(Span(text="\n"))
                    header_cells[ci].extend(ln.spans)
                    all_lines.append(ln)
        else:
            header_cells = []
            for ln in header.lines[:num_cols]:
                header_cells.append(list(ln.spans))
            while len(header_cells) < num_cols:
                header_cells.append([])
            all_lines = list(header.lines)

        all_rows_data: list[list[list]] = [header_cells]

        for row in valid_rows:
            col_spans: dict[int, list] = defaultdict(list)
            for _, blk in row:
                line_cols = [_nearest_column(ln.bbox[0], col_xs)
                             for ln in blk.lines]
                multi_col = len(set(line_cols)) > 1
                blk_ci = _nearest_column(blk.bbox[0], col_xs)
                for ln, lc in zip(blk.lines, line_cols):
                    ci = lc if multi_col else blk_ci
                    if col_spans[ci] and ln.spans:
                        col_spans[ci].append(Span(text="\n"))
                    col_spans[ci].extend(ln.spans)
                    all_lines.append(ln)
            table_row = [col_spans.get(ci, []) for ci in range(num_cols)]
            all_rows_data.append(table_row)

        # MuPDF-overlap guard: if the body blocks overlap a substantial
        # MuPDF find_tables() region, defer to MuPDF Native (Pass 5) which
        # handles bordered tables with proper row/cell extraction.
        # Skip deferral for predominantly monospace tables (code
        # comparisons) where SBS/Pass 1 produces better results.
        if page_mupdf_tables:
            sbs_mono = 0
            sbs_total = 0
            for ln in all_lines:
                for sp in ln.spans:
                    if sp.text.strip():
                        sbs_total += 1
                        if sp.monospace:
                            sbs_mono += 1
            sbs_mono_ratio = sbs_mono / sbs_total if sbs_total else 0
            if sbs_mono_ratio < _MONO_RATIO_THRESHOLD:
                body_block_set = {idx for row in consumed_rows for idx, _ in row}
                body_y0 = min(blocks[idx].bbox[1] for idx in body_block_set
                              if idx < len(blocks))
                body_y1 = max(blocks[idx].bbox[3] for idx in body_block_set
                              if idx < len(blocks))
                body_x0 = min(blocks[idx].bbox[0] for idx in body_block_set
                              if idx < len(blocks))
                body_x1 = max(blocks[idx].bbox[2] for idx in body_block_set
                              if idx < len(blocks))
                deferred = False
                for tbl in page_mupdf_tables.get(page, []):
                    tb = tbl["bbox"]
                    if tbl.get("row_count", 0) < _SBS_MUPDF_DEFER_MIN_ROWS:
                        continue
                    overlap_x = max(0, min(body_x1, tb[2]) - max(body_x0, tb[0]))
                    overlap_y = max(0, min(body_y1, tb[3]) - max(body_y0, tb[1]))
                    if overlap_x > 0 and overlap_y > 0:
                        body_area = max((body_x1 - body_x0) * (body_y1 - body_y0), 1)
                        overlap_ratio = (overlap_x * overlap_y) / body_area
                        if overlap_ratio > 0.30:
                            _log.debug(
                                "SBS deferred to MuPDF Native: page %d, "
                                "overlap=%.0f%%, MuPDF rows=%d",
                                page, overlap_ratio * 100,
                                tbl.get("row_count", 0))
                            deferred = True
                            break
                if deferred:
                    i = j
                    continue

        kind_val, strategy_val, all_rows_data = _classify_and_annotate(
            all_rows_data)

        # Bibliography: not a real table, skip so prose pipeline handles it.
        if kind_val == TableKind.BIBLIOGRAPHY.value:
            _log.debug("SBS bibliography bypass: page %d", page)
            i = j
            continue

        text = _render_table_text(all_rows_data)

        table_sections.append(Section(
            kind=SectionKind.TABLE,
            text=text,
            confidence=Confidence.HIGH,
            lines=all_lines,
            page_num=page,
            columns=all_rows_data,
            table_kind=kind_val,
            table_strategy=strategy_val,
        ))
        _log.debug("Side-by-side table: %d rows x %d cols on page %d",
                    len(all_rows_data), num_cols, page)

        used.add(i)
        if atomized_hdr is not None:
            used.update(atomized_hdr)
        for row in consumed_rows:
            for idx, _ in row:
                used.add(idx)

        i = j

    return table_sections, used


# ---------------------------------------------------------------------------
# Inline-grid tables: a single block whose lines alternate between N fixed
# x-positions, forming an implicit grid.  MuPDF delivers bordered tables
# with short cells as one block where e.g. lines 0,2,4 are at x=105 and
# lines 1,3,5 are at x=184.  Each pair at the same y-level is one row.
# ---------------------------------------------------------------------------
_INLINE_GRID_MIN_LINES = 4        # at least 2 rows x 2 cols
_INLINE_GRID_COL_GAP = 30.0       # min x-gap to count as distinct column
_INLINE_GRID_Y_BAND = 5.0         # max y-difference for same-row lines
_INLINE_GRID_VALID_ROW_RATIO = 0.75  # fraction of rows that must span 2+ cols
_INLINE_GRID_MIN_ROWS = 2


def _detect_inline_grid_tables(
    blocks: list[Block],
    page_mupdf_tables: dict[int, list[dict]] | None = None,
) -> tuple[list[Section], set[int]]:
    """Detect tables encoded as alternating-x lines in a single block.

    Pattern: one MuPDF block contains 2*N or more lines that cycle through
    K distinct x-positions (K >= 2).  Lines at the same y-level belong to
    the same table row.  Only fires when MuPDF find_tables() independently
    confirms a table at the same location (bordered table confirmation).

    Returns (table_sections, used_block_indices).
    """
    table_sections: list[Section] = []
    used: set[int] = set()

    # Pre-build set of (page, y_mid) ranges from MuPDF tables for fast lookup.
    mupdf_ranges: list[tuple[int, float, float, float, float]] = []
    for pg, tbls in (page_mupdf_tables or {}).items():
        for tbl in tbls:
            bbox = tbl["bbox"]
            mupdf_ranges.append((pg, bbox[0], bbox[1], bbox[2], bbox[3]))

    for bi, block in enumerate(blocks):
        if len(block.lines) < _INLINE_GRID_MIN_LINES:
            continue

        # Gate: block must overlap a MuPDF-detected table region.
        # This ensures we only catch bordered tables, not code listings
        # or prose blocks with indented lines.
        bmid_y = (block.bbox[1] + block.bbox[3]) / 2.0
        bmid_x = (block.bbox[0] + block.bbox[2]) / 2.0
        margin = 10.0
        has_mupdf = False
        for pg, mx0, my0, mx1, my1 in mupdf_ranges:
            if (block.page_num == pg
                    and mx0 - margin <= bmid_x <= mx1 + margin
                    and my0 - margin <= bmid_y <= my1 + margin):
                has_mupdf = True
                break
        if not has_mupdf:
            continue

        # Cluster line x0 positions into columns.
        x_positions: list[float] = [ln.bbox[0] for ln in block.lines]
        col_xs = _cluster_x_positions(x_positions)
        if len(col_xs) < 2:
            continue

        # Verify the gap between the two nearest columns is large enough.
        sorted_xs = sorted(col_xs)
        min_gap = min(sorted_xs[i + 1] - sorted_xs[i]
                      for i in range(len(sorted_xs) - 1))
        if min_gap < _INLINE_GRID_COL_GAP:
            continue

        # Group lines into rows by y-position.
        rows: list[list[Line]] = []
        for ln in block.lines:
            placed = False
            for row in rows:
                if abs(ln.bbox[1] - row[0].bbox[1]) <= _INLINE_GRID_Y_BAND:
                    row.append(ln)
                    placed = True
                    break
            if not placed:
                rows.append([ln])

        if len(rows) < _INLINE_GRID_MIN_ROWS:
            continue

        # Each row must span 2+ columns.
        num_cols = len(col_xs)
        valid_rows = 0
        for row in rows:
            row_col_set = set()
            for ln in row:
                ci = _nearest_column(ln.bbox[0], col_xs)
                row_col_set.add(ci)
            if len(row_col_set) >= 2:
                valid_rows += 1

        if valid_rows < _INLINE_GRID_MIN_ROWS:
            continue

        # Strict guard: at least 75% of rows must be multi-column.
        if valid_rows < len(rows) * _INLINE_GRID_VALID_ROW_RATIO:
            continue

        # Build cell data: rows x cols.
        all_rows_data: list[list[list]] = []
        all_lines: list[Line] = []
        for row_lines in rows:
            cell_spans: dict[int, list] = defaultdict(list)
            for ln in row_lines:
                ci = _nearest_column(ln.bbox[0], col_xs)
                if cell_spans[ci] and ln.spans:
                    cell_spans[ci].append(Span(text="\n"))
                cell_spans[ci].extend(ln.spans)
                all_lines.append(ln)
            table_row = [cell_spans.get(ci, []) for ci in range(num_cols)]
            all_rows_data.append(table_row)

        text = _render_table_text(all_rows_data)

        strategy = TableStrategy.PIPE_TABLE
        for row in all_rows_data:
            for cell_spans in row:
                if any("\n" in s.text for s in cell_spans):
                    strategy = TableStrategy.HTML_TABLE
                    break
            if strategy == TableStrategy.HTML_TABLE:
                break

        table_sections.append(Section(
            kind=SectionKind.TABLE,
            text=text,
            confidence=Confidence.HIGH,
            lines=all_lines,
            page_num=block.page_num,
            columns=all_rows_data,
            table_kind=TableKind.INLINE_GRID.value,
            table_strategy=strategy.value,
        ))
        used.add(bi)
        _log.debug("Inline-grid table: %d rows x %d cols on page %d",
                    len(all_rows_data), num_cols, block.page_num)

    return table_sections, used


_MUPDF_TABLE_MIN_BBOX_SIZE = 50.0  # minimum width AND height for a real table

# Phantom-table guard for find_tables(). When the table bbox covers
# most of the page AND a single cell occupies a disproportionate
# fraction of the table height, find_tables() has likely merged a
# real (small) table with surrounding prose/headings into one
# full-page "table". The block-based detector handles the real
# table; the phantom must be rejected.
_MUPDF_TABLE_MAX_PAGE_COVERAGE = 0.80
_MUPDF_TABLE_MAX_CELL_FRACTION = 0.40

# Cross-page merge thresholds for MuPDF native tables.
# Used both in the per-page pre-classify absorb and the post-loop merge.
_CROSS_PAGE_BOTTOM_Y = 600.0
_CROSS_PAGE_TOP_Y = 200.0
_CROSS_PAGE_MAX_GAP = 2


_LABEL_MAX_WORDS = 3  # column-0 cells with more words are not labels


def _maybe_transpose_label_table(
    rows: list[list[list]],
) -> list[list[list]]:
    """Transpose a table whose first column contains short row labels.

    Detects tables where column 0 has short non-monospace labels
    (e.g. "Before", "After") at certain rows, with remaining rows
    holding data or continuation content. Transposes so the labels
    become column headers, and all content from each label group
    merges into one data cell per label.

    Handles both simple (2-row) and multi-row (4+ row) tables
    uniformly. Continuation rows between labels have their content
    from all columns merged into the preceding label's data cell.
    """
    if len(rows) < 2 or len(rows[0]) < 2:
        return rows

    num_cols = len(rows[0])
    if num_cols != 2:
        return rows

    # Scan column 0 for short non-monospace labels.
    labels: list[str] = []
    label_indices: list[int] = []
    for ri, row in enumerate(rows):
        cell0_text = "".join(s.text for s in row[0]).strip()
        if not cell0_text:
            continue
        words = cell0_text.split()
        is_mono = any(s.monospace for s in row[0] if s.text.strip())
        if len(words) <= _LABEL_MAX_WORDS and not is_mono:
            labels.append(cell0_text)
            label_indices.append(ri)

    if len(labels) < 2:
        return rows

    # Build transposed table: labels become column headers.
    new_num_cols = len(labels)
    header_row: list[list] = [list(rows[ri][0]) for ri in label_indices]

    # For each label, collect ALL content from its group rows
    # (label row through next label - 1) across all columns,
    # excluding the label text in column 0 of the label row.
    data_cells: list[list] = [[] for _ in range(new_num_cols)]
    for li, start_ri in enumerate(label_indices):
        end_ri = (label_indices[li + 1]
                  if li + 1 < len(label_indices) else len(rows))
        for ri in range(start_ri, end_ri):
            for ci in range(num_cols):
                # Skip the label cell itself (col 0 of the label row).
                if ri == start_ri and ci == 0:
                    continue
                cell_spans = rows[ri][ci]
                if cell_spans:
                    if data_cells[li]:
                        data_cells[li].append(Span(text="\n"))
                    data_cells[li].extend(cell_spans)

    return [header_row, data_cells]


def _detect_mupdf_native_tables(
    blocks: list[Block],
    page_mupdf_tables: dict[int, list[dict]],
) -> tuple[list[Section], set[int]]:
    """Detect tables using MuPDF's native find_tables() results.

    Maps pre-collected find_tables() data (cell bboxes) back to the
    existing Block/Line/Span objects so that classification and
    rendering can use monospace and font information.

    Returns (table_sections, used_block_indices).
    """
    table_sections: list[Section] = []
    used: set[int] = set()

    if not page_mupdf_tables:
        return table_sections, used

    for page_num in sorted(page_mupdf_tables):
        for tbl_info in page_mupdf_tables[page_num]:
            bbox = tbl_info["bbox"]
            row_count = tbl_info["row_count"]
            col_count = tbl_info["col_count"]
            cells = tbl_info["cells"]

            if row_count < 2 or col_count < 1:
                continue
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            if w < _MUPDF_TABLE_MIN_BBOX_SIZE or h < _MUPDF_TABLE_MIN_BBOX_SIZE:
                continue

            # Phantom-table guard: reject find_tables() results that
            # span most of the page with a disproportionately tall cell.
            valid_cells_for_guard = [c for c in cells if c is not None]
            if valid_cells_for_guard:
                page_height = 842.0  # A4 default; US Letter is 792
                page_coverage = h / page_height
                max_cell_h = max((c[3] - c[1]) for c in valid_cells_for_guard)
                max_cell_frac = max_cell_h / h if h > 0 else 0
                if (page_coverage > _MUPDF_TABLE_MAX_PAGE_COVERAGE
                        and max_cell_frac > _MUPDF_TABLE_MAX_CELL_FRACTION):
                    _log.debug(
                        "Rejected phantom find_tables() on page %d: "
                        "page_coverage=%.0f%%, max_cell_fraction=%.0f%%",
                        page_num, page_coverage * 100, max_cell_frac * 100)
                    continue

            # Build cell grid by clustering cell bboxes by y-position
            # (rows) and x-position (columns). find_tables() may return
            # cells in column-major order for some table layouts, so
            # relying on ri*col_count+ci is unreliable. Instead, group
            # cells spatially.
            valid_cells = [c for c in cells if c is not None]
            if len(valid_cells) < 2:
                continue

            # Cluster y-top positions into rows. Using y_top (c[1])
            # instead of y_mid avoids misclassification of merged cells
            # that span multiple logical rows (their tall y_mid drifts
            # into a separate cluster from the smaller sibling cells).
            y_tops = sorted(set(
                round(c[1], 1) for c in valid_cells))
            y_clusters: list[float] = []
            for yt in y_tops:
                if not y_clusters or abs(yt - y_clusters[-1]) > 10.0:
                    y_clusters.append(yt)
                else:
                    y_clusters[-1] = (y_clusters[-1] + yt) / 2.0

            # Cluster x-midpoints into columns.
            x_mids = sorted(set(
                round((c[0] + c[2]) / 2.0, 1) for c in valid_cells))
            x_clusters: list[float] = []
            for xm in x_mids:
                if not x_clusters or abs(xm - x_clusters[-1]) > 10.0:
                    x_clusters.append(xm)
                else:
                    x_clusters[-1] = (x_clusters[-1] + xm) / 2.0

            actual_rows = len(y_clusters)
            actual_cols = len(x_clusters)
            if actual_rows < 2 or actual_cols < 1:
                continue

            # Assign each cell to (row_idx, col_idx) by closest cluster.
            # Use y_top for row assignment (matches y_top clustering).
            cell_grid: list[list[tuple[float, float, float, float] | None]] = [
                [None] * actual_cols for _ in range(actual_rows)
            ]
            for c in valid_cells:
                cy = c[1]  # y_top
                cx = (c[0] + c[2]) / 2.0
                ri = min(range(actual_rows),
                         key=lambda r: abs(cy - y_clusters[r]))
                ci = min(range(actual_cols),
                         key=lambda c_: abs(cx - x_clusters[c_]))
                cell_grid[ri][ci] = c

            row_count = actual_rows
            col_count = actual_cols

            # Collect blocks that fall within the table bbox (with margin).
            margin = 5.0
            table_block_indices: list[int] = []
            for idx, blk in enumerate(blocks):
                if idx in used:
                    continue
                if blk.page_num != page_num:
                    continue
                bmid_y = (blk.bbox[1] + blk.bbox[3]) / 2.0
                bmid_x = (blk.bbox[0] + blk.bbox[2]) / 2.0
                if (bbox[0] - margin <= bmid_x <= bbox[2] + margin
                        and bbox[1] - margin <= bmid_y <= bbox[3] + margin):
                    table_block_indices.append(idx)

            if not table_block_indices:
                continue

            # Map each line from collected blocks into the cell grid.
            all_rows_data: list[list[list]] = [
                [[] for _ in range(col_count)] for _ in range(row_count)
            ]
            all_lines = []

            for idx in table_block_indices:
                blk = blocks[idx]
                for ln in blk.lines:
                    lmid_x = (ln.bbox[0] + ln.bbox[2]) / 2.0
                    lmid_y = (ln.bbox[1] + ln.bbox[3]) / 2.0

                    best_r, best_c = -1, -1
                    best_score = float("inf")
                    for ri in range(row_count):
                        for ci in range(col_count):
                            cb = cell_grid[ri][ci]
                            if cb is None:
                                continue
                            if (cb[0] - margin <= lmid_x <= cb[2] + margin
                                    and cb[1] - margin <= lmid_y <= cb[3] + margin):
                                cw = max(cb[2] - cb[0], 1.0)
                                ch = max(cb[3] - cb[1], 1.0)
                                nx = abs(lmid_x - (cb[0] + cb[2]) / 2.0) / cw
                                ny = abs(lmid_y - (cb[1] + cb[3]) / 2.0) / ch
                                score = nx + ny
                                if score < best_score:
                                    best_score = score
                                    best_r, best_c = ri, ci

                    if best_r >= 0:
                        cell = all_rows_data[best_r][best_c]
                        if cell and ln.spans:
                            cell.append(Span(text="\n"))
                        cell.extend(ln.spans)
                    # Always retain the line so its spans are available
                    # for Docling enrichment via _flat_spans_from_section.
                    all_lines.append(ln)

            # Cross-page continuation: when the table bbox extends
            # close to the page bottom, absorb code blocks from the
            # top of the next page that fall within the table's
            # x-range. Only monospace blocks qualify; stop at the
            # first non-monospace block (heading, caption, body text).
            _PAGE_BOTTOM_THRESH = 650.0
            next_page = page_num + 1
            if bbox[3] > _PAGE_BOTTOM_THRESH and col_count >= 2:
                cont_row: list[list] = [[] for _ in range(col_count)]
                table_mid = (bbox[0] + bbox[2]) / 2.0
                for idx, blk in enumerate(blocks):
                    if idx in used:
                        continue
                    if blk.page_num != next_page:
                        continue
                    if blk.bbox[1] > 200.0:
                        break
                    if not all(ln.is_monospace for ln in blk.lines):
                        break
                    bmid_x = (blk.bbox[0] + blk.bbox[2]) / 2.0
                    if bbox[0] - margin <= bmid_x <= bbox[2] + margin:
                        for ln in blk.lines:
                            lmid_x = (ln.bbox[0] + ln.bbox[2]) / 2.0
                            best_c = (0 if lmid_x < table_mid
                                      else col_count - 1)
                            cell = cont_row[best_c]
                            if cell and ln.spans:
                                cell.append(Span(text="\n"))
                            cell.extend(ln.spans)
                            all_lines.append(ln)
                        table_block_indices.append(idx)
                        used.add(idx)
                if any(cell for cell in cont_row):
                    all_rows_data.append(cont_row)

            # Drop rows that are completely empty.
            non_empty_rows = [
                r for r in all_rows_data
                if any(cell for cell in r)
            ]
            if len(non_empty_rows) < 2:
                continue

            # Transpose comparison tables: when column 0 contains only
            # short labels (e.g. "Before", "After") and column 1+ has
            # longer content, restructure so the labels become column
            # headers and corresponding data fills the columns below.
            non_empty_rows = _maybe_transpose_label_table(non_empty_rows)

            text = _render_table_text(non_empty_rows)

            # Exclude a short header row from classification signals
            # so non-monospace labels don't dilute mono_ratio.
            classify_rows = non_empty_rows
            header_excluded = False
            if (len(non_empty_rows) > 1
                    and all(len(cell) <= 1 for cell in non_empty_rows[0])
                    and sum(len("".join(s.text for s in cell).split())
                            for cell in non_empty_rows[0]) <= col_count):
                classify_rows = non_empty_rows[1:]
                header_excluded = True

            kind_val, strategy_val, classify_rows = (
                _classify_and_annotate(classify_rows))

            # Cross-page absorption: before rejecting a table as
            # false_positive or bibliography, check if it continues
            # the immediately preceding table section.  Small tail
            # fragments (e.g. 2 data rows + footer junk) often get
            # misclassified because the signal-to-noise ratio is low,
            # but structurally they belong to the previous table.
            # Use the MuPDF-native col count (tbl_info) because
            # spatial clustering may inflate actual_cols.
            mupdf_col_count = tbl_info["col_count"]
            if (table_sections
                    and (kind_val == TableKind.FALSE_POSITIVE.value
                         or kind_val == TableKind.BIBLIOGRAPHY.value)):
                prev = table_sections[-1]
                prev_cols = (len(prev.columns[0])
                             if prev.columns else 0)
                prev_last_page = prev.page_num
                if prev.lines:
                    ppages = {ln.page_num for ln in prev.lines
                              if hasattr(ln, 'page_num') and ln.page_num is not None}
                    if ppages:
                        prev_last_page = max(ppages)
                page_gap = page_num - prev_last_page
                if (prev_cols == mupdf_col_count
                        and 1 <= page_gap <= 2):
                    prev_max_y = max(
                        (ln.bbox[3] for ln in prev.lines), default=0)
                    cur_min_y = min(
                        (ln.bbox[1] for ln in all_lines), default=999)
                    if (prev_max_y > _CROSS_PAGE_BOTTOM_Y
                            and cur_min_y < _CROSS_PAGE_TOP_Y):
                        # Build rows from MuPDF-native extract data
                        # (correct column count) instead of the
                        # spatially-clustered non_empty_rows which may
                        # have inflated column count.
                        extract = tbl_info["extract"]
                        native_rows: list[list[list]] = []
                        for raw_row in extract:
                            cells = [
                                [Span(text=(c or ""))]
                                for c in raw_row
                            ]
                            filled = sum(
                                1 for c in raw_row
                                if c and c.strip()
                            )
                            if filled >= 2:
                                native_rows.append(cells)
                        if not native_rows:
                            continue
                        start = _header_dedup_start(
                            prev.columns, native_rows)
                        prev.columns.extend(native_rows[start:])
                        prev.lines.extend(all_lines)
                        prev.text = _render_table_text(prev.columns)
                        used.update(table_block_indices)
                        _log.debug(
                            "Cross-page absorb (pre-classify): "
                            "page %d into page %d table, now %d rows",
                            page_num, prev.page_num,
                            len(prev.columns))
                        continue

            # Bibliography: not a real table, skip so prose pipeline handles it.
            # Do NOT mark blocks as used so they stay in remaining.
            if kind_val == TableKind.BIBLIOGRAPHY.value:
                _log.debug("MuPDF native bibliography bypass: page %d",
                            page_num)
                continue

            # False positive: classification rejected the table.
            # Do NOT mark blocks as used so they stay in remaining
            # for the prose pipeline.
            if kind_val == TableKind.FALSE_POSITIVE.value:
                _log.debug(
                    "MuPDF native table: %d rows x %d cols on page %d "
                    "(false_positive, skipped)",
                    len(non_empty_rows), col_count, page_num)
                continue

            used.update(table_block_indices)

            if header_excluded:
                non_empty_rows = [non_empty_rows[0]] + classify_rows
            else:
                non_empty_rows = classify_rows

            table_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=page_num,
                columns=non_empty_rows,
                table_kind=kind_val,
                table_strategy=strategy_val,
            ))
            _log.debug(
                "MuPDF native table: %d rows x %d cols on page %d (%s)",
                len(non_empty_rows), col_count, page_num, kind_val,
            )

    # Cross-page merge: consecutive MuPDF-native table sections with the
    # same column count that straddle a page break are fragments of one
    # logical table.  Merge by appending continuation rows (skipping any
    # duplicate header) to the first fragment and dropping the rest.
    #
    # Guards (all must hold):
    #   - Same column count (structural identity).
    #   - First fragment ends near page bottom (y > 600).
    #   - Second fragment starts near page top (y < 200).
    #   - Pages within 2 of each other (allows blank separator pages).
    #
    # table_kind is intentionally NOT checked: the same logical table
    # gets different kinds per page because _classify_and_annotate runs
    # independently per fragment.  Column count is the structural signal.
    #
    # Iterates until stable so A+B+C+D collapses in one pass sequence.
    if len(table_sections) >= 2:
        changed = True
        while changed:
            changed = False
            merged_indices: set[int] = set()
            for si in range(len(table_sections) - 1):
                if si in merged_indices:
                    continue
                sec_a = table_sections[si]
                sec_b = table_sections[si + 1]
                if not (sec_a.columns and sec_b.columns):
                    continue
                if len(sec_a.columns[0]) != len(sec_b.columns[0]):
                    continue
                # Use max page from lines for adjacency (page_num stays
                # at the first fragment's page after a merge).
                a_last_page = sec_a.page_num
                if sec_a.lines:
                    pages_in_a = {
                        ln.page_num for ln in sec_a.lines
                        if hasattr(ln, 'page_num') and ln.page_num is not None
                    }
                    if pages_in_a:
                        a_last_page = max(pages_in_a)
                page_gap = sec_b.page_num - a_last_page
                if page_gap < 1 or page_gap > _CROSS_PAGE_MAX_GAP:
                    continue
                a_max_y = max(
                    (ln.bbox[3] for ln in sec_a.lines), default=0)
                b_min_y = min(
                    (ln.bbox[1] for ln in sec_b.lines), default=999)
                if not (a_max_y > _CROSS_PAGE_BOTTOM_Y
                        and b_min_y < _CROSS_PAGE_TOP_Y):
                    continue
                start = _header_dedup_start(
                    sec_a.columns, sec_b.columns)
                sec_a.columns.extend(sec_b.columns[start:])
                sec_a.lines.extend(sec_b.lines)
                sec_a.text = _render_table_text(sec_a.columns)
                merged_indices.add(si + 1)
                changed = True
                _log.debug(
                    "Cross-page merge: page %d + %d (cols=%d), "
                    "now %d rows",
                    sec_a.page_num, sec_b.page_num,
                    len(sec_a.columns[0]), len(sec_a.columns))
            if merged_indices:
                table_sections = [
                    s for i, s in enumerate(table_sections)
                    if i not in merged_indices
                ]

    return table_sections, used


_HORIZONTAL_ROW_Y_TOLERANCE = 3.0
_HORIZONTAL_ROW_Y_TOLERANCE_WIDE = 12.0
_HORIZONTAL_ROW_WIDE_X_SPREAD = 150.0
_HORIZONTAL_ROW_MIN_CELLS = 3
# Gap-asymmetry guard: reject blocks where the ratio of largest to
# smallest inter-cell gap exceeds this threshold.  Real table rows
# have roughly uniform spacing (ratio 1-3); WG21 section headings
# like "4  General  [general]" have extreme asymmetry (ratio 5-13)
# because the stable name sits far to the right.
_HORIZONTAL_ROW_MAX_GAP_RATIO = 4.0

# Spanning-header absorption: when a valid run is found, the
# immediately preceding block may be a multi-column header with
# fewer columns (e.g. 2-col header over 3-col data table).
_SPANNING_HEADER_X_TOLERANCE = 40.0
_SPANNING_HEADER_Y_GAP_MAX = 30.0
_SPANNING_HEADER_MIN_COLS = 2

# Trailing horizontal-row split: blocks where MuPDF merged a table
# header into the preceding paragraph (happens when data cells are
# empty, e.g. P4012R0 §2.2 "Suggested Polls").
_TRAILING_HR_MIN_CELLS = 3
_TRAILING_HR_MAX_CELL_LEN = 4
_TRAILING_HR_Y_GAP = 8.0


def _block_horizontal_row(block: Block) -> list[float] | None:
    """Detect a block whose lines sit side-by-side at the same y-level.

    Returns x-start positions when the block has 3+ lines sharing
    the same y-band (within _HORIZONTAL_ROW_Y_TOLERANCE). This
    catches narrow poll/vote tables where column gaps are too small
    for _block_column_positions.

    When the strict tolerance fails, a relaxed check fires: if the
    lines span a wide horizontal range (>150pt) and the y-spread is
    within _HORIZONTAL_ROW_Y_TOLERANCE_WIDE, the block still qualifies.
    MuPDF sometimes reports slightly different y-positions for cells
    in the same visual row (observed 8pt spread on P4003R1 §9.4).
    """
    if len(block.lines) < _HORIZONTAL_ROW_MIN_CELLS:
        return None
    y_centers = [(ln.bbox[1] + ln.bbox[3]) / 2 for ln in block.lines]
    if max(y_centers) - min(y_centers) <= _HORIZONTAL_ROW_Y_TOLERANCE:
        cols = [ln.bbox[0] for ln in block.lines]
        if _gap_asymmetry_reject(block.lines):
            return None
        return cols

    # Relaxed: non-overlapping x-ranges (true side-by-side cells)
    # with moderate y jitter.  MuPDF sometimes reports slightly
    # different y-positions for cells in the same visual row
    # (observed 8pt spread on P4003R1 §9.4).  Only fires when
    # each line occupies a distinct horizontal lane: sorted by x0,
    # each line's x0 must exceed the previous line's x2 (right edge).
    y_spread = max(y_centers) - min(y_centers)
    if y_spread <= _HORIZONTAL_ROW_Y_TOLERANCE_WIDE:
        by_x = sorted(block.lines, key=lambda ln: ln.bbox[0])
        non_overlapping = True
        for k in range(len(by_x) - 1):
            if by_x[k + 1].bbox[0] < by_x[k].bbox[2]:
                non_overlapping = False
                break
        if non_overlapping:
            if _gap_asymmetry_reject(by_x):
                return None
            return [ln.bbox[0] for ln in by_x]

    return None


def _gap_asymmetry_reject(lines: list) -> bool:
    """Reject blocks with extreme gap asymmetry between cells.

    Two independent guards, either triggers rejection:

    1. Pure gap asymmetry: gap_ratio > _HORIZONTAL_ROW_MAX_GAP_RATIO.
       Real table rows have roughly uniform spacing (ratio 1-3); WG21
       section headings like "4  General  [general]" reach 5-13.

    2. Combined gap + width asymmetry: gap_ratio > 3 AND the widest
       cell is >10x the narrowest.  Catches reference-list patterns
       like "(1.1) — IEC Electropedia: ..." where a tiny marker sits
       next to a long description (width ratio 27, gap ratio 3.6).
       Real tables with extreme width ratios have uniform gaps (≤1.4)
       and vice versa; the combination is unique to list-marker blocks.
    """
    if len(lines) < 3:
        return False
    by_x = sorted(lines, key=lambda ln: ln.bbox[0])
    gaps: list[float] = []
    for k in range(len(by_x) - 1):
        gap = by_x[k + 1].bbox[0] - by_x[k].bbox[2]
        gaps.append(gap)
    positive = [g for g in gaps if g > 0]
    if len(positive) < 2:
        return False
    gap_ratio = max(positive) / min(positive)

    # Guard 1: pure gap asymmetry
    if gap_ratio > _HORIZONTAL_ROW_MAX_GAP_RATIO:
        return True

    # Guard 2: moderate gap asymmetry + extreme cell-width asymmetry,
    # restricted to exactly 3 cells.  The pattern is reference-list
    # markers "(1.1) — Long description..." which always decompose
    # into exactly 3 lines.  4+ cell rows are real tables even when
    # one cell is tiny (e.g. a "-" score column).
    _GAP_MODERATE = 3.0
    _WIDTH_EXTREME = 10.0
    if len(by_x) == 3 and gap_ratio > _GAP_MODERATE:
        widths = [ln.bbox[2] - ln.bbox[0] for ln in by_x]
        pos_w = [w for w in widths if w > 0]
        if len(pos_w) >= 2:
            width_ratio = max(pos_w) / min(pos_w)
            if width_ratio > _WIDTH_EXTREME:
                return True

    return False


def _block_horizontal_row_relaxed(
    block: Block, min_cells: int,
) -> list[float] | None:
    """Like _block_horizontal_row but with a caller-supplied min_cells.

    Used for spanning-header detection where the header may have only
    2 lines (below the default _HORIZONTAL_ROW_MIN_CELLS=3).
    """
    if len(block.lines) < min_cells:
        return None
    y_centers = [(ln.bbox[1] + ln.bbox[3]) / 2 for ln in block.lines]
    if max(y_centers) - min(y_centers) <= _HORIZONTAL_ROW_Y_TOLERANCE:
        if _gap_asymmetry_reject(block.lines):
            return None
        return [ln.bbox[0] for ln in block.lines]
    y_spread = max(y_centers) - min(y_centers)
    if y_spread <= _HORIZONTAL_ROW_Y_TOLERANCE_WIDE:
        by_x = sorted(block.lines, key=lambda ln: ln.bbox[0])
        non_overlapping = True
        for k in range(len(by_x) - 1):
            if by_x[k + 1].bbox[0] < by_x[k].bbox[2]:
                non_overlapping = False
                break
        if non_overlapping:
            if _gap_asymmetry_reject(by_x):
                return None
            return [ln.bbox[0] for ln in by_x]
    return None


def _split_trailing_horizontal_rows(
    blocks: list[Block],
) -> tuple[list[Section], list[Block]]:
    """Split blocks where trailing lines form a merged table header.

    MuPDF sometimes merges table header cells into the preceding
    paragraph block when the data cells are empty.  Detects trailing
    lines that are (a) on the same y-band, (b) very short text,
    (c) separated by a y-gap from the paragraph text, and (d)
    non-overlapping in x.  Creates a header-only TABLE section and
    returns the shortened paragraph block.
    """
    table_sections: list[Section] = []
    result_blocks: list[Block] = []

    for block in blocks:
        if len(block.lines) < _TRAILING_HR_MIN_CELLS + 1:
            result_blocks.append(block)
            continue

        best_start = None
        for start in range(max(1, len(block.lines) - 10),
                           len(block.lines) - _TRAILING_HR_MIN_CELLS + 1):
            trailing = block.lines[start:]
            if len(trailing) < _TRAILING_HR_MIN_CELLS:
                continue
            y_centers = [(ln.bbox[1] + ln.bbox[3]) / 2 for ln in trailing]
            if max(y_centers) - min(y_centers) > _HORIZONTAL_ROW_Y_TOLERANCE:
                continue
            if not all(len(ln.text.strip()) <= _TRAILING_HR_MAX_CELL_LEN
                       for ln in trailing):
                continue
            prev_y_c = (block.lines[start - 1].bbox[1]
                        + block.lines[start - 1].bbox[3]) / 2
            gap = min(y_centers) - prev_y_c
            if gap < _TRAILING_HR_Y_GAP:
                continue
            by_x = sorted(trailing, key=lambda ln: ln.bbox[0])
            if any(by_x[k + 1].bbox[0] < by_x[k].bbox[2]
                   for k in range(len(by_x) - 1)):
                continue
            best_start = start
            break

        if best_start is None:
            result_blocks.append(block)
            continue

        para_lines = block.lines[:best_start]
        header_lines = block.lines[best_start:]

        para_block = Block(
            lines=para_lines,
            bbox=(block.bbox[0], block.bbox[1],
                  block.bbox[2], para_lines[-1].bbox[3]),
            page_num=block.page_num,
        )
        result_blocks.append(para_block)

        header_row = [list(ln.spans) for ln in header_lines]
        empty_row: list[list] = [[] for _ in header_lines]
        text = " | ".join(ln.text.strip() for ln in header_lines)

        kind_val, strategy_val, rows = _classify_and_annotate(
            [header_row, empty_row])

        table_sections.append(Section(
            kind=SectionKind.TABLE,
            text=text,
            confidence=Confidence.MEDIUM,
            lines=list(header_lines),
            page_num=block.page_num,
            columns=[header_row, empty_row],
            table_kind=kind_val,
            table_strategy=strategy_val,
        ))
        _log.debug(
            "Trailing horizontal-row split: page %d, %d header cells",
            block.page_num, len(header_lines))

    return table_sections, result_blocks


def _detect_horizontal_row_tables(
    blocks: list[Block],
) -> tuple[list[Section], set[int]]:
    """Detect tables formed by consecutive horizontal-row blocks.

    Two or more adjacent blocks on the same page, each with 3+ lines
    at identical y-level and matching cell count, form a table.
    """
    table_sections: list[Section] = []
    used: set[int] = set()
    i = 0

    while i < len(blocks):
        cols = _block_horizontal_row(blocks[i])
        if cols is None:
            i += 1
            continue

        run = [i]
        continuation_blocks: set[int] = set()
        ncols = len(cols)
        j = i + 1
        while j < len(blocks):
            nxt_cols = _block_horizontal_row(blocks[j])
            if (nxt_cols is not None
                    and len(nxt_cols) == ncols
                    and blocks[j].page_num == blocks[i].page_num):
                run.append(j)
                j += 1
            elif (blocks[j].page_num == blocks[i].page_num
                  and len(blocks[j].lines) == 1
                  and blocks[j].lines[0].text.strip()):
                # Single-line block that is a wrapped parenthetical
                # from the previous row's cell (e.g. "(kqueue)"
                # continuing "macOS" above).  Only absorb when the
                # text starts with '(' and x-aligns with a column.
                txt = blocks[j].lines[0].text.strip()
                if txt.startswith("("):
                    ln_x = blocks[j].lines[0].bbox[0]
                    col_dist = min(abs(ln_x - c) for c in cols)
                    if col_dist < 20.0:
                        run.append(j)
                        continuation_blocks.add(j)
                        j += 1
                        continue
                break
            else:
                break

        if len(run) >= _MIN_TABLE_ROWS:
            rows: list[list[list]] = []
            all_lines = []
            for idx in run:
                blk = blocks[idx]

                if idx in continuation_blocks:
                    # Merge this single-line block into the
                    # previous row's nearest column cell.
                    if rows:
                        ln = blk.lines[0]
                        all_lines.append(ln)
                        best_col = min(
                            range(ncols),
                            key=lambda ci: abs(ln.bbox[0] - cols[ci]),
                        )
                        prev = rows[-1]
                        if prev[best_col]:
                            prev[best_col].append(Span(text=" "))
                        prev[best_col].extend(ln.spans)
                    continue

                row = []
                for ln in blk.lines[:ncols]:
                    row.append(list(ln.spans))
                    all_lines.append(ln)
                while len(row) < ncols:
                    row.append([])
                # Merge extra lines (beyond ncols) into nearest cell.
                for ln in blk.lines[ncols:]:
                    all_lines.append(ln)
                    ln_x = ln.bbox[0]
                    best_col = min(
                        range(ncols),
                        key=lambda ci: abs(ln_x - cols[ci]),
                    )
                    if row[best_col]:
                        row[best_col].append(Span(text=" "))
                    row[best_col].extend(ln.spans)
                rows.append(row)

            kind_val, strategy_val, rows = _classify_and_annotate(rows)
            text = _render_table_text(rows)

            table_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=blocks[run[0]].page_num,
                columns=rows,
                table_kind=kind_val,
                table_strategy=strategy_val,
            ))
            _log.debug(
                "Horizontal-row table: %d rows x %d cols on page %d",
                len(rows), ncols, blocks[run[0]].page_num,
            )
            used.update(run)
            i = j
        else:
            i += 1

    return table_sections, used


# ---------------------------------------------------------------------------
# Classification helpers (integrated from table_analyzer.py)
# ---------------------------------------------------------------------------

def _compute_table_signals(rows: list[list[list]]) -> dict:
    """Compute classification signals from table rows (list of cell-span-lists)."""
    if not rows:
        return {"empty": True}

    total_cells = 0
    empty_cells = 0
    monospace_cells = 0
    max_word_count = 0
    col_counts = []
    total_cell_length = 0
    total_spans = 0

    for row in rows:
        col_counts.append(len(row))
        for cell_spans in row:
            total_cells += 1
            total_spans += len(cell_spans)
            cell_text = "".join(s.text for s in cell_spans).strip()
            if not cell_text:
                empty_cells += 1
                continue

            total_cell_length += len(cell_text)
            words = cell_text.split()
            if len(words) > max_word_count:
                max_word_count = len(words)

            text_spans = [s for s in cell_spans if s.text.strip()]
            if text_spans and all(s.monospace for s in text_spans):
                monospace_cells += 1

    non_empty = total_cells - empty_cells
    num_cols = max(col_counts) if col_counts else 0

    # Per-column word count for key-value classification.
    col0_max_words = 0
    for row in rows:
        if row and row[0]:
            text = "".join(s.text for s in row[0]).strip()
            wc = len(text.split()) if text else 0
            if wc > col0_max_words:
                col0_max_words = wc

    # WG21 spec table header detection: 3-column tables whose header row
    # matches the "expression | return type | assertion/note" pattern
    # (or close variants like "operation | type | semantics").
    header_matches_spec = False
    if num_cols == 3 and rows:
        hdr = [
            "".join(s.text for s in cell).lower().strip()
            for cell in rows[0]
        ]
        if len(hdr) == 3:
            col0_spec = any(k in hdr[0] for k in ("expression", "operation"))
            col1_spec = any(k in hdr[1] for k in ("return", "type"))
            header_matches_spec = col0_spec and col1_spec

    # Bibliography signal: fraction of col-0 cells matching [Label] pattern.
    # Multi-ID cells (newline-separated) count as a match when every line
    # individually matches the bracket pattern.
    col0_bracket_count = 0
    col0_non_empty = 0
    for row in rows:
        if row and row[0]:
            text = "".join(s.text for s in row[0]).strip()
            if text:
                col0_non_empty += 1
                sub_lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
                if sub_lines and all(
                    _BIBLIOGRAPHY_LABEL_RE.match(sl) for sl in sub_lines
                ):
                    col0_bracket_count += 1
    col0_bracket_ratio = (col0_bracket_count / col0_non_empty
                          if col0_non_empty else 0.0)

    # NB-ballot signal: fraction of col-0 cells matching [CC-NNN] pattern.
    col0_ballot_count = 0
    for row in rows:
        if row and row[0]:
            text = "".join(s.text for s in row[0]).strip()
            if text and _NB_BALLOT_ID_RE.match(text):
                col0_ballot_count += 1
    col0_ballot_ratio = (col0_ballot_count / col0_non_empty
                         if col0_non_empty else 0.0)

    return {
        "empty": False,
        "empty_ratio": empty_cells / total_cells if total_cells > 0 else 0,
        "mono_ratio": monospace_cells / non_empty if non_empty > 0 else 0,
        "max_word_count": max_word_count,
        "col_count_consistent": len(set(col_counts)) <= 2,
        "num_cols": num_cols,
        "num_rows": len(rows),
        "avg_cell_length": total_cell_length / non_empty if non_empty > 0 else 0,
        "avg_spans_per_cell": total_spans / total_cells if total_cells > 0 else 0,
        "col0_max_words": col0_max_words,
        "header_matches_spec": header_matches_spec,
        "col0_bracket_ratio": col0_bracket_ratio,
        "col0_ballot_ratio": col0_ballot_ratio,
    }


def _classify_table(signals: dict) -> TableKind:
    """Classify a table based on its computed signals."""
    if signals.get("empty"):
        return TableKind.FALSE_POSITIVE

    if signals["empty_ratio"] > _EMPTY_RATIO_THRESHOLD:
        return TableKind.FALSE_POSITIVE

    if not signals["col_count_consistent"]:
        return TableKind.FALSE_POSITIVE

    # Tony Tables: few rows, many spans per cell (multi-line code packed
    # into each cell by MuPDF).
    if (signals["mono_ratio"] >= _MONO_RATIO_THRESHOLD
            and signals.get("num_cols", 0) <= 3
            and signals.get("num_rows", 0) <= 5
            and signals.get("avg_spans_per_cell", 0) > 10):
        return TableKind.CODE_COMPARISON

    # Per-line code tables: many rows where each cell is a single code
    # line (e.g. side-by-side struct definitions).  Distinguished from
    # code-declaration tables by high mono_ratio combined with very low
    # avg_spans_per_cell (each cell = one code token/line, not a
    # labelled signature with mixed fonts).
    if (signals["mono_ratio"] >= 0.85
            and signals.get("num_cols", 0) <= 3
            and signals.get("avg_spans_per_cell", 0) <= 3
            and signals.get("num_rows", 0) >= 4):
        return TableKind.CODE_COMPARISON

    # WG21 spec tables: 3-column requirement tables with known header
    # pattern (expression/return type/assertion).  Checked before the
    # prose-word fallback so they get html_table rendering regardless
    # of cell length.
    if signals.get("header_matches_spec"):
        return TableKind.SPEC_TABLE

    # NB-ballot tables: >= 50% of col-0 cells are national body comment IDs
    # like [ES-047], [FI-071], [SE]. Checked before bibliography because
    # short country-code labels also match the bibliography bracket regex.
    if (signals.get("col0_ballot_ratio", 0) >= 0.50
            and signals.get("num_rows", 0) >= 3):
        return TableKind.NB_BALLOT

    # Bibliography tables: >= 60% of col-0 cells are bracketed references
    # like [CodeQL], [P2900R14], [Das16]. Checked before KEY_VALUE because
    # 2-column bibliographies also match the key-value pattern.
    if (signals.get("col0_bracket_ratio", 0) >= _BIBLIOGRAPHY_LABEL_RATIO
            and signals.get("num_rows", 0) >= 2):
        return TableKind.BIBLIOGRAPHY

    # Key-value tables: exactly 2 columns where col-0 holds short field
    # labels and col-1 holds longer descriptive values.
    if (signals.get("num_cols") == 2
            and signals.get("col0_max_words", 99) <= _KV_COL0_MAX_WORDS
            and signals["max_word_count"] > _PROSE_WORD_THRESHOLD):
        return TableKind.KEY_VALUE

    if signals["max_word_count"] > _PROSE_WORD_THRESHOLD:
        return TableKind.PROSE_TABLE

    return TableKind.CLEAN_MATRIX


def _merge_code_rows(rows: list[list[list]]) -> list[list[list]]:
    """Merge consecutive per-line code rows into multi-line cells.

    When Pass 2 detects a side-by-side code table, MuPDF may deliver
    each code line as a separate block, producing many single-line rows.
    This merges them into one data row with newline-joined cells so the
    HTML renderer produces proper ``<pre>`` blocks.

    Only merges rows where every non-empty cell is monospace and
    contains no existing newlines (already multi-line cells stay as-is).
    The first row (header) is never merged into.
    """
    if len(rows) <= 2:
        return rows

    def _is_single_line_code_row(row: list[list]) -> bool:
        """True if every non-empty cell is monospace with no newlines."""
        for cell_spans in row:
            text = "".join(s.text for s in cell_spans).strip()
            if not text:
                continue
            text_spans = [s for s in cell_spans if s.text.strip()]
            if not text_spans or not all(s.monospace for s in text_spans):
                return False
            if any("\n" in s.text for s in cell_spans):
                return False
        return True

    def _is_code_accumulator(row: list[list]) -> bool:
        """True if every non-empty cell is all-monospace (may have newlines)."""
        for cell_spans in row:
            text = "".join(s.text for s in cell_spans).strip()
            if not text:
                continue
            text_spans = [s for s in cell_spans if s.text.strip()]
            if not text_spans or not all(s.monospace for s in text_spans):
                return False
        return True

    merged: list[list[list]] = [rows[0]]
    for row in rows[1:]:
        if (_is_single_line_code_row(row)
                and len(merged) >= 2
                and _is_code_accumulator(merged[-1])):
            target = merged[-1]
            for ci in range(len(row)):
                if ci >= len(target):
                    break
                if row[ci]:
                    if target[ci]:
                        target[ci].append(Span(text="\n"))
                    target[ci].extend(row[ci])
        else:
            merged.append(row)
    return merged


def _classify_and_annotate(
    rows: list[list[list]],
) -> tuple[str, str, list[list[list]]]:
    """Classify rows, optionally merge, return (kind, strategy, rows)."""
    signals = _compute_table_signals(rows)
    kind = _classify_table(signals)
    strategy = _STRATEGY_MAP[kind]

    if kind == TableKind.CODE_COMPARISON:
        rows = _merge_code_rows(rows)

    # Pipe tables cannot represent multi-line cell content. If any cell
    # contains a newline, force HTML table rendering regardless of kind.
    if strategy == TableStrategy.PIPE_TABLE:
        for row in rows:
            for cell_spans in row:
                if any("\n" in s.text for s in cell_spans):
                    strategy = TableStrategy.HTML_TABLE
                    break
            if strategy == TableStrategy.HTML_TABLE:
                break

    return kind.value, strategy.value, rows


def _detect_header_block(blocks: list[Block], table_start_idx: int,
                         ref_cols: list[float], num_cols: int
                         ) -> list[list] | None:
    """Check block preceding a table for column headers (e.g. Before | After).

    Returns a header row (list of cell-span-lists) if found, None otherwise.
    """
    if table_start_idx == 0:
        return None

    prev_blk = blocks[table_start_idx - 1]
    first_table_blk = blocks[table_start_idx]

    # Must be same page and close vertically
    if prev_blk.page_num != first_table_blk.page_num:
        return None
    y_gap = first_table_blk.lines[0].bbox[1] - prev_blk.lines[-1].bbox[3]
    if y_gap > 30.0 or y_gap < 0:
        return None

    # Must have same number of lines as columns (one label per column)
    if len(prev_blk.lines) != num_cols:
        return None

    # Check x-positions match the table columns
    for li, line in enumerate(prev_blk.lines):
        if abs(line.bbox[0] - ref_cols[li]) > _COLUMN_X_TOLERANCE * 2:
            return None

    # Must be non-monospace short text (headers, not code)
    for line in prev_blk.lines:
        text = "".join(s.text for s in line.spans).strip()
        if len(text) > 30:
            return None
        if any(s.monospace for s in line.spans if s.text.strip()):
            return None

    # Build header row
    header_row = []
    for line in prev_blk.lines:
        header_row.append(list(line.spans))
    while len(header_row) < num_cols:
        header_row.append([])
    return header_row


# ---------------------------------------------------------------------------
# Pass 3: geometric column grouping (borderless tables)
# ---------------------------------------------------------------------------

# Minimum distinct x-columns in a y-band for it to count as a table row.
# Set to 4 to avoid false positives from numbered lists (2-3 columns)
# and function signature blocks (3 columns).
_GEO_MIN_COLS_PER_BAND = 4

# Maximum distinct x-columns allowed.  Code blocks produce 20+ span
# x-positions per line; real borderless tables have 3-8 columns.
_GEO_MAX_COLS = 8

# Minimum number of multi-column y-bands to form a table region.
_GEO_MIN_TABLE_BANDS = 3

# Maximum gap (in y-bands) between two multi-column bands before the
# run is considered broken.  A band is _Y_BAND_HEIGHT (15pt), so a gap
# of 8 bands ≈ 120pt, accommodating multi-line cells whose wrapped
# lines only populate 1-2 columns instead of all 4.
_GEO_MAX_BAND_GAP = 8

# Maximum fraction of monospace spans in a candidate region.
# Code blocks are predominantly monospace; tables are not.
_GEO_MAX_MONO_RATIO = 0.50


def _detect_column_aligned_tables(
    blocks: list[Block],
    *,
    two_column_pages: frozenset[int] = frozenset(),
) -> tuple[list[Section], set[int]]:
    """Detect borderless tables via span-level x-position clustering.

    Implements the "Pass 3 (geometric column grouping)" described in the
    module docstring.  Works on blocks where each row is a separate
    single-line block with spans at multiple x-positions.

    Algorithm:
    1. For each page, bucket every non-empty span into (page, y_band)
       groups and collect distinct x-position buckets per y-band.
    2. Find y-bands with 3+ distinct x-columns (multi-column signal).
    3. Find contiguous runs of such y-bands (allowing small gaps for
       multi-line cells).
    4. For each run: collect the union of column x-positions, assign
       spans to columns, merge visual lines into logical table rows
       (a new row starts when the leftmost column has content).
    5. Classify and emit Section(kind=TABLE).

    Returns (table_sections, used_block_indices).
    """
    table_sections: list[Section] = []
    used: set[int] = set()

    # Index blocks by page.
    page_blocks: dict[int, list[tuple[int, Block]]] = defaultdict(list)
    for idx, blk in enumerate(blocks):
        page_blocks[blk.page_num].append((idx, blk))

    for page_num in sorted(page_blocks):
        idx_blocks = page_blocks[page_num]

        # Skip pages with two-column paper layout.
        if page_num in two_column_pages:
            continue

        # Collect span x-positions per y-band.
        yband_xs: dict[int, set[int]] = defaultdict(set)
        yband_spans: dict[int, list[Span]] = defaultdict(list)
        yband_block_indices: dict[int, set[int]] = defaultdict(set)

        for idx, blk in idx_blocks:
            for line in blk.lines:
                if not line.spans or not line.text.strip():
                    continue
                # Use LINE-level x-position (line.bbox[0]), not
                # span-level.  Prose with inline code has all lines
                # at x=left-margin; real column tables have separate
                # blocks at distinct x-positions.
                x_key = round(line.bbox[0] / _COLUMN_X_BUCKET)
                y_key = round(
                    ((line.bbox[1] + line.bbox[3]) / 2.0)
                    / _Y_BAND_HEIGHT
                )
                yband_xs[y_key].add(x_key)
                yband_spans[y_key].extend(line.spans)
                yband_block_indices[y_key].add(idx)

        # Skip pages with two-column paper layout.
        if page_num in two_column_pages:
            continue

        # Find y-bands with enough distinct columns.
        multi_col_bands = sorted(
            yk for yk, xs in yband_xs.items()
            if len(xs) >= _GEO_MIN_COLS_PER_BAND
        )
        if len(multi_col_bands) < _GEO_MIN_TABLE_BANDS:
            continue

        # Find contiguous runs of multi-column bands.
        runs: list[list[int]] = []
        current_run = [multi_col_bands[0]]
        for i in range(1, len(multi_col_bands)):
            gap = multi_col_bands[i] - multi_col_bands[i - 1]
            if gap <= _GEO_MAX_BAND_GAP:
                current_run.append(multi_col_bands[i])
            else:
                if len(current_run) >= _GEO_MIN_TABLE_BANDS:
                    runs.append(current_run)
                current_run = [multi_col_bands[i]]
        if len(current_run) >= _GEO_MIN_TABLE_BANDS:
            runs.append(current_run)

        for run in runs:
            # Find column x-positions that recur across multiple
            # y-bands.  Font-change fragments (ligatures, bold/italic
            # transitions) produce noise x-positions that appear in
            # only 1 band; real columns repeat across many bands.
            x_band_counts: Counter[int] = Counter()
            for yk in run:
                for xk in yband_xs[yk]:
                    x_band_counts[xk] += 1
            stable_x_keys = {
                xk for xk, cnt in x_band_counts.items()
                if cnt >= _MIN_SHARED_YBANDS
            }
            raw_positions = sorted(x_key * _COLUMN_X_BUCKET
                                   for x_key in stable_x_keys)
            if len(raw_positions) < _GEO_MIN_COLS_PER_BAND:
                continue

            # Merge columns that are too close together.  Inline
            # code and font changes produce clusters of x-positions
            # within a single logical column.
            # Use a tighter merge threshold than _COLUMN_GAP_THRESHOLD
            # (50pt) because real table columns can be as close as 30pt
            # (e.g. row-number column at x=65, text column at x=95).
            _GEO_MERGE_THRESHOLD = 25.0
            col_positions = [raw_positions[0]]
            for xp in raw_positions[1:]:
                if xp - col_positions[-1] < _GEO_MERGE_THRESHOLD:
                    pass  # absorbed into previous column
                else:
                    col_positions.append(xp)
            # After merging, require at least 3 distinct columns.
            # The pre-merge check already required 4+ raw columns
            # per y-band; the merge step collapses close neighbors.
            if len(col_positions) < 3:
                continue
            if len(col_positions) > _GEO_MAX_COLS:
                continue

            # Monospace guard: skip regions dominated by code.
            region_spans = []
            for yk in run:
                region_spans.extend(yband_spans[yk])
            if region_spans:
                mono_count = sum(1 for s in region_spans if s.monospace)
                if mono_count / len(region_spans) > _GEO_MAX_MONO_RATIO:
                    continue

            # Also include intermediate y-bands (those with fewer
            # columns that fall between multi-column bands, e.g.
            # continuation lines of multi-line cells).
            y_min_band = run[0]
            y_max_band = run[-1]

            # Extend the range to capture trailing continuation lines
            # (multi-line cells whose last line falls just below
            # y_max_band and has fewer than _GEO_MIN_COLS_PER_BAND
            # distinct columns).
            col_x_set = {round(cp / _COLUMN_X_BUCKET) for cp in col_positions}
            for candidate_yk in sorted(yband_spans):
                if candidate_yk <= y_max_band:
                    continue
                if candidate_yk > y_max_band + _GEO_MAX_BAND_GAP:
                    break
                cand_xs = yband_xs.get(candidate_yk, set())
                if cand_xs and cand_xs <= col_x_set:
                    y_max_band = candidate_yk
                else:
                    break

            all_ybands = sorted(
                yk for yk in yband_spans
                if y_min_band <= yk <= y_max_band
            )

            # Filter out the page-number band (typically a lone centered
            # span at the very bottom of the page).
            filtered_ybands = []
            for yk in all_ybands:
                spans = yband_spans[yk]
                texts = [s.text.strip() for s in spans if s.text.strip()]
                if len(texts) == 1 and texts[0].isdigit() and len(texts[0]) <= 3:
                    continue
                filtered_ybands.append(yk)
            all_ybands = filtered_ybands

            if not all_ybands:
                continue

            # Assign spans to columns.
            def _assign_col(span_x: float) -> int:
                best = 0
                best_d = abs(span_x - col_positions[0])
                for ci, cx in enumerate(col_positions):
                    d = abs(span_x - cx)
                    if d < best_d:
                        best_d = d
                        best = ci
                return best

            # Build visual rows: group lines by y-band, assign to
            # columns based on their line.bbox[0].  All spans within
            # a line go into the same column cell.  Track which MuPDF
            # block indices contribute to each visual row so that
            # multi-line cells (lines from the same block spanning
            # multiple y-bands) can be detected during row-merge.
            num_cols = len(col_positions)
            visual_rows: list[tuple[int, list[list[Span]]]] = []
            _vrow_blk_ids: list[set[int]] = []

            for yk in all_ybands:
                cells: list[list[Span]] = [[] for _ in range(num_cols)]
                blk_ids: set[int] = set()
                # Re-collect lines for this y-band (not just spans).
                for idx, blk in idx_blocks:
                    for line in blk.lines:
                        if not line.spans or not line.text.strip():
                            continue
                        line_yk = round(
                            ((line.bbox[1] + line.bbox[3]) / 2.0)
                            / _Y_BAND_HEIGHT
                        )
                        if line_yk != yk:
                            continue
                        ci = _assign_col(line.bbox[0])
                        cells[ci].extend(line.spans)
                        blk_ids.add(idx)
                visual_rows.append((yk, cells))
                _vrow_blk_ids.append(blk_ids)

            # Merge visual rows into logical rows.  A new logical row
            # starts when the leftmost column (col 0) has non-empty
            # content, indicating a new table entry.  A col-0 value
            # that looks like a wrapped continuation of the previous
            # cell (starts with '(' or ',') is merged instead of
            # starting a new row — fixes P4003R1 §9.4 where "(kqueue)"
            # wraps from the previous cell's "macOS" line.
            #
            # Block-sharing continuation: when two consecutive visual
            # rows share a contributing MuPDF block, the lines come
            # from the same multi-line cell and must be merged even
            # if col 0 has content (P1000R7 wrapped prose table).
            logical_rows: list[list[list[Span]]] = []
            current_logical: list[list[Span]] | None = None

            for vi, (_, cells) in enumerate(visual_rows):
                col0_text = "".join(s.text for s in cells[0]).strip()
                shares_block = (
                    vi > 0
                    and current_logical is not None
                    and bool(_vrow_blk_ids[vi] & _vrow_blk_ids[vi - 1])
                )
                is_continuation = (
                    shares_block
                    or (col0_text
                        and current_logical is not None
                        and col0_text[0] in "(,;")
                )
                if col0_text and not is_continuation:
                    if current_logical is not None:
                        logical_rows.append(current_logical)
                    current_logical = [list(c) for c in cells]
                else:
                    if current_logical is None:
                        current_logical = [list(c) for c in cells]
                    else:
                        for ci in range(num_cols):
                            if cells[ci]:
                                if current_logical[ci] and any(
                                    s.text.strip()
                                    for s in current_logical[ci]
                                ):
                                    current_logical[ci].append(
                                        Span(text="\n"))
                                current_logical[ci].extend(cells[ci])
            if current_logical is not None:
                logical_rows.append(current_logical)

            # Header-split guard: the row-merge heuristic (new row when
            # col 0 is non-empty) can absorb data lines into the header
            # when the first data line starts on a y-band where col 0 is
            # still empty.  Detect this by checking for \n Span
            # separators in logical_rows[0]: if the text before the
            # first \n is a short label (<=5 words) in most cells, split
            # row 0 into a clean header and a spillover data fragment.
            if logical_rows:
                row0 = logical_rows[0]
                cells_with_nl = 0
                short_pre_nl = 0
                for cell_spans in row0:
                    nl_idx = next(
                        (i for i, s in enumerate(cell_spans)
                         if s.text == "\n"), None)
                    if nl_idx is not None:
                        cells_with_nl += 1
                        pre_text = "".join(
                            s.text for s in cell_spans[:nl_idx]).strip()
                        if len(pre_text.split()) <= _COLALIGN_HEADER_MAX_WORDS:
                            short_pre_nl += 1

                if cells_with_nl >= 2 and short_pre_nl == cells_with_nl:
                    header_row: list[list[Span]] = []
                    spill_row: list[list[Span]] = []
                    for cell_spans in row0:
                        nl_idx = next(
                            (i for i, s in enumerate(cell_spans)
                             if s.text == "\n"), None)
                        if nl_idx is not None:
                            header_row.append(list(cell_spans[:nl_idx]))
                            spill_row.append(list(cell_spans[nl_idx + 1:]))
                        else:
                            header_row.append(list(cell_spans))
                            spill_row.append([])

                    logical_rows[0] = header_row
                    # Merge spillover into row 1 if it exists; otherwise
                    # insert as a new row.
                    if len(logical_rows) > 1:
                        for ci in range(num_cols):
                            if spill_row[ci]:
                                if logical_rows[1][ci] and any(
                                    s.text.strip()
                                    for s in logical_rows[1][ci]
                                ):
                                    logical_rows[1][ci] = (
                                        spill_row[ci]
                                        + [Span(text="\n")]
                                        + logical_rows[1][ci]
                                    )
                                else:
                                    logical_rows[1][ci] = spill_row[ci]
                    else:
                        has_content = any(
                            any(s.text.strip() for s in c)
                            for c in spill_row
                        )
                        if has_content:
                            logical_rows.insert(1, spill_row)

            if len(logical_rows) < _MIN_TABLE_ROWS:
                continue

            # Cross-column merge guard: if the table columns span
            # both left (<280pt) and right (>320pt) halves AND the
            # majority of cells are empty, this is likely two
            # separate page columns merged into one table.
            _MERGE_LEFT = 280.0
            _MERGE_RIGHT = 320.0
            has_left_col = any(x < _MERGE_LEFT for x in col_positions)
            has_right_col = any(x > _MERGE_RIGHT for x in col_positions)
            if has_left_col and has_right_col:
                total_cells = sum(
                    len(row) for row in logical_rows)
                empty_cells = sum(
                    1 for row in logical_rows
                    for cell in row
                    if not "".join(s.text for s in cell).strip()
                )
                if total_cells > 0 and empty_cells / total_cells > 0.15:
                    continue

            # Bullet/dash list guard: if column 0 is predominantly
            # bullet markers, this is a list, not a table.
            _BULLET_CHARS = frozenset("-\u2022\u2013\u2014")
            col0_texts = [
                "".join(s.text for s in row[0]).strip()
                for row in logical_rows
            ]
            bullet_count = sum(
                1 for t in col0_texts
                if t and all(ch in _BULLET_CHARS for ch in t)
            )
            if len(col0_texts) > 0 and bullet_count / len(col0_texts) > 0.5:
                continue

            # Classify the table.
            kind_val, strategy_val, logical_rows = _classify_and_annotate(
                logical_rows)
            if kind_val == "false_positive":
                continue

            # Collect consumed blocks and lines.
            consumed_indices: set[int] = set()
            for yk in all_ybands:
                consumed_indices |= yband_block_indices.get(yk, set())
            used |= consumed_indices

            all_lines = []
            for ci in consumed_indices:
                blk = blocks[ci]
                all_lines.extend(blk.lines)
            all_lines.sort(key=lambda ln: (ln.bbox[1], ln.bbox[0]))

            text = _render_table_text(logical_rows)

            table_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=page_num,
                columns=logical_rows,
                table_kind=kind_val,
                table_strategy=strategy_val,
                table_source="column_aligned",
            ))
            _log.info(
                "Column-aligned table on page %d: %d rows x %d cols "
                "[kind=%s, strategy=%s]",
                page_num, len(logical_rows), num_cols,
                kind_val, strategy_val)

    # Cross-page continuation: when consecutive tables on adjacent
    # pages share the same column count and the second table's header
    # row is textually identical, strip the duplicate header from the
    # continuation so the emit phase can render them as one visual
    # table.  We do NOT merge Sections (that breaks page-based
    # y-position sorting); we just remove the repeated header row.
    if len(table_sections) >= 2:
        for i in range(len(table_sections) - 1):
            t1 = table_sections[i]
            t2 = table_sections[i + 1]
            if (t2.page_num - t1.page_num) not in (0, 1):
                continue
            if not t1.columns or not t2.columns:
                continue
            if len(t1.columns[0]) != len(t2.columns[0]):
                continue
            h1 = tuple(
                "".join(s.text for s in cell).strip()
                for cell in t1.columns[0])
            h2 = tuple(
                "".join(s.text for s in cell).strip()
                for cell in t2.columns[0])
            if h1 == h2 and len(t2.columns) > 1:
                t2.columns = t2.columns[1:]
                t2.table_continuation = True
                t2.text = _render_table_text(t2.columns)
                _log.info(
                    "Stripped duplicate header from column-aligned "
                    "table continuation on page %d",
                    t2.page_num)

    return table_sections, used


# ---------------------------------------------------------------------------
# Pass 4b: label-anchored spec table detection
# ---------------------------------------------------------------------------
# WG21 spec tables carry a "Table N - XYZ requirements" caption above the
# grid.  MuPDF distributes each column's cells into separate Block objects
# at distinct x-positions, so existing heuristic passes (which look for
# multi-column lines *within* a single block) often miss them.  The phantom
# guard in Pass 5 then rejects the MuPDF-native fallback because the tables
# span most of the page.
#
# This pass uses the label as an anchor, collects ALL blocks (including
# monospace) in the spatial region below the label until the next heading
# or table label, clusters them by x-position to recover columns, and
# merges visual lines into logical rows.
#
# Crucially, monospace blocks inside the table region are NOT skipped:
# the "expression" column of spec tables contains code identifiers like
# `a.await_suspend(h, env)`.  Standalone code blocks (concept definitions,
# struct declarations) sit above the table label and are never touched.
# ---------------------------------------------------------------------------

_SPEC_TABLE_LABEL_RE = re.compile(r"^Table\s+(\d+)\s*[\u2014\u2013\-]")

# Y-coordinate below which blocks are page footers (page numbers).
# A4 = 842pt, US Letter = 792pt.  790 catches both.
_SPEC_TABLE_FOOTER_Y = 790.0

# Minimum gap between x-position buckets to count as separate columns.
_SPEC_TABLE_X_MERGE = 25.0

# Bucket size for x-position clustering.
_SPEC_TABLE_X_BUCKET = 10.0

# y-band height for row grouping.
_SPEC_TABLE_Y_BAND = 15.0

# Section-number pattern for heading detection (stop boundary).
_SPEC_HEADING_NUM_RE = re.compile(r"^\d+(?:\.\d+)*\s")

_SPEC_HEADING_SUBSECTION_RE = re.compile(r"^\d+\.\d+")

_DATA_MARKERS_RE = re.compile(
    r"^(Returns|Effects|Preconditions|Postconditions|"
    r"Synchronization|Shall|Same|Requires|Remarks)\b",
    re.IGNORECASE)

_SPEC_COL_NAMES_RE = re.compile(
    r"\b(expression|return\s+type|assertion|pre/post|"
    r"preconditions?|postconditions?|requirements?)\b",
    re.IGNORECASE)

_COL_HEADER_RE = re.compile(
    r"^(expression|return\s+type|assertion|conditions|"
    r"pre/post-conditions|assertion/note\s+pre/post-?)$",
    re.IGNORECASE,
)

_COLALIGN_HEADER_MAX_WORDS = 5
_SPEC_HEADER_MAX_WORDS = 4


def _is_spec_heading_block(block: Block) -> bool:
    """True if *block* looks like a section heading (stop boundary).

    Detects numbered headings (e.g. "11.3.2 Concept io_runnable")
    that delimit where a table region ends.  Accepts both bold
    headings at any size > 10 and non-bold subsection headings
    (font_size >= 10.5 with dotted numbering like ``11.3.3``),
    because P4003R1 renders some subsection headings without bold.
    """
    if not block.lines:
        return False
    first = block.lines[0]
    text = first.text.strip()
    if not _SPEC_HEADING_NUM_RE.match(text):
        return False
    if first.is_bold and first.font_size > 10:
        return True
    if first.font_size >= 10.5 and _SPEC_HEADING_SUBSECTION_RE.match(text):
        return True
    return False


def _detect_spec_tables_by_label(
    blocks: list[Block],
) -> tuple[list[Section], set[int]]:
    """Detect WG21 spec tables anchored by 'Table N - ...' caption blocks.

    Algorithm:
      1. Scan blocks for label pattern ``Table <N> - <description>``.
      2. For each label, define the collection region: same page, from
         label y to the next heading, next table label, or page footer.
      3. Extend into the next page when the first blocks there are not
         headings and have x-positions matching table columns (cross-page
         continuation).
      4. Cluster collected blocks by x-position to identify columns.
      5. Group by y-band, assign to columns, merge into logical rows.
      6. Classify and emit as SPEC_TABLE Section.

    Returns (table_sections, used_block_indices).
    """
    table_sections: list[Section] = []
    used: set[int] = set()

    # Build (global_index, block) list and locate label blocks.
    labels: list[tuple[int, int, Block]] = []  # (global_idx, table_num, block)
    for i, blk in enumerate(blocks):
        m = _SPEC_TABLE_LABEL_RE.match(blk.text.strip())
        if m:
            labels.append((i, int(m.group(1)), blk))

    if not labels:
        return table_sections, used

    for label_gi, table_num, label_blk in labels:
        label_page = label_blk.page_num
        label_y = label_blk.bbox[1]

        # Determine y-end boundary on the label's page: stop at the next
        # heading, next table label, or numbered note, whichever comes
        # first.
        y_end = _SPEC_TABLE_FOOTER_Y
        for j in range(label_gi + 1, len(blocks)):
            b = blocks[j]
            if b.page_num != label_page:
                break
            by = b.bbox[1]
            if by >= _SPEC_TABLE_FOOTER_Y:
                continue
            if by < label_y - 5:
                continue
            txt = b.text.strip()
            if _SPEC_TABLE_LABEL_RE.match(txt):
                y_end = by - 5
                break
            if _is_spec_heading_block(b):
                y_end = by - 5
                break
            if re.match(r"^\d+\s*\[", txt):
                y_end = by - 5
                break
            if (re.match(r"^\d+\s", txt)
                    and b.bbox[2] - b.bbox[0] > 300):
                y_end = by - 5
                break

        # Collect all blocks in the region [label_y, y_end) on label_page,
        # skipping page footers.  MuPDF may return blocks out of
        # y-order (e.g. a code listing at y=170 after the label at
        # y=715), so explicitly skip blocks above the label.
        collected: list[tuple[int, Block]] = []
        next_page_start = -1  # index where next page begins

        for j in range(label_gi, len(blocks)):
            b = blocks[j]
            if b.page_num == label_page:
                if b.bbox[1] >= _SPEC_TABLE_FOOTER_Y:
                    continue
                if b.bbox[1] >= y_end and j != label_gi:
                    break
                if b.bbox[1] < label_y - 5 and j != label_gi:
                    continue
                collected.append((j, b))
            elif b.page_num > label_page:
                next_page_start = j
                break

        # Cross-page extension: spec tables often span 2-3 pages.
        # Continue collecting on subsequent pages until a heading,
        # a numbered paragraph (e.g. "3 [ Note: ..."), or a block
        # whose x-position is far from any table column is hit.
        # Note: len(collected) >= 1 (not 2) because a table label
        # can sit at the bottom of a page with the entire body on
        # the next page (e.g. Table 3 in P4003R1 at pg 55 y=715).
        if next_page_start > 0 and len(collected) >= 1:
            # Compute column x-buckets from same-page blocks for
            # matching cross-page content.  When the label sits alone
            # at the bottom of a page (no same-page data blocks), we
            # accept all blocks on the next page until we accumulate
            # enough column positions to discriminate.
            same_page_xs: set[int] = set()
            for _, cb in collected[1:]:  # skip label itself
                for ln in cb.lines:
                    same_page_xs.add(round(ln.bbox[0] / _SPEC_TABLE_X_BUCKET))
            # _join_cross_page may have merged header lines from the
            # next page into the label block.  Seed column positions
            # from those merged lines so the collection loop knows
            # all three column x-positions before it starts.
            lbl_blk_0 = collected[0][1]
            for ln in lbl_blk_0.lines:
                if ln.page_num != label_page:
                    same_page_xs.add(
                        round(ln.bbox[0] / _SPEC_TABLE_X_BUCKET))

            current_page = label_page + 1
            seen_stop = False
            for j in range(next_page_start, len(blocks)):
                b = blocks[j]
                if b.page_num != current_page:
                    if b.page_num == current_page + 1:
                        current_page = b.page_num
                        seen_stop = False
                    else:
                        break
                if b.bbox[1] >= _SPEC_TABLE_FOOTER_Y:
                    continue
                if _is_spec_heading_block(b):
                    if b.bbox[0] > 250:
                        continue
                    break
                if _SPEC_TABLE_LABEL_RE.match(b.text.strip()):
                    break
                text = b.text.strip()
                first_line = b.lines[0].text.strip() if b.lines else text
                is_page_num_prefix = (
                    first_line.isdigit() and len(first_line) <= 3
                )
                # Numbered note/paragraph at the left margin signals
                # end of table.  MuPDF may deliver blocks out of
                # y-order: table-column blocks (x > 250) can follow
                # stop blocks in the list even though they are above
                # them on the page.  Set a flag and skip left-margin
                # blocks, but keep collecting right-column blocks
                # that belong to the table.
                is_stop_pattern = (
                    re.match(r"^\d+\s*\[", text)
                    or (not is_page_num_prefix
                        and re.match(r"^\d+\s", text)
                        and b.bbox[2] - b.bbox[0] > 300)
                )
                if is_stop_pattern:
                    seen_stop = True
                    continue
                bx = round(b.bbox[0] / _SPEC_TABLE_X_BUCKET)
                bootstrapping = len(same_page_xs) < 2
                in_column = (bootstrapping
                             or bx in same_page_xs
                             or b.bbox[0] > 250)
                if in_column:
                    collected.append((j, b))
                    for ln in b.lines:
                        same_page_xs.add(
                            round(ln.bbox[0] / _SPEC_TABLE_X_BUCKET))
                elif seen_stop:
                    # After a stop pattern, skip non-column blocks
                    # but keep scanning: MuPDF may deliver column
                    # blocks later in the list (out of y-order).
                    continue
                else:
                    break

        # Salvage pass: MuPDF sometimes delivers table-column blocks
        # after headings or notes in the block list even though they
        # are visually above them on the page.  Scan remaining blocks
        # on collected pages and pick up any that sit in a known
        # column x-position and below the header/footer threshold.
        collected_idxs = {gi for gi, _ in collected}
        if collected:
            collected_pages = {cb.page_num for _, cb in collected}
            max_page = max(collected_pages)
            salvaged: list[tuple[int, Block]] = []
            for j in range(next_page_start, len(blocks)):
                b = blocks[j]
                if b.page_num not in collected_pages:
                    if b.page_num > max_page:
                        break
                    continue
                if j in collected_idxs:
                    continue
                if b.bbox[1] >= _SPEC_TABLE_FOOTER_Y:
                    continue
                bx = round(b.bbox[0] / _SPEC_TABLE_X_BUCKET)
                if bx in same_page_xs or b.bbox[0] > 250:
                    salvaged.append((j, b))
            if salvaged:
                collected.extend(salvaged)

        # _join_cross_page may have merged the column-header block from
        # the next page into the label block (e.g. Table 3 label at the
        # bottom of page 55 + header row at top of page 56).  Detect
        # this by checking for lines whose page_num differs from the
        # label page, split them out, and fix the label block so the
        # header text does not leak as prose.
        if collected:
            lbl_gi, lbl_blk = collected[0]
            merged_lines = [
                ln for ln in lbl_blk.lines if ln.page_num != label_page
            ]
            if merged_lines:
                label_only = [
                    ln for ln in lbl_blk.lines if ln.page_num == label_page
                ]
                cleaned = replace(lbl_blk, lines=label_only)
                blocks[lbl_gi] = cleaned
                collected[0] = (lbl_gi, cleaned)
                synth_bbox = (
                    min(ln.bbox[0] for ln in merged_lines),
                    min(ln.bbox[1] for ln in merged_lines),
                    max(ln.bbox[2] for ln in merged_lines),
                    max(ln.bbox[3] for ln in merged_lines),
                )
                synth = replace(
                    lbl_blk,
                    lines=merged_lines,
                    page_num=merged_lines[0].page_num,
                    bbox=synth_bbox,
                )
                collected.insert(1, (-1, synth))

        if len(collected) < 3:
            # Need at least label + header + one data row
            continue

        _log.debug(
            "Spec table label 'Table %d' on page %d: "
            "collected %d blocks (y=[%.0f, %.0f])",
            table_num, label_page, len(collected),
            label_y,
            max(cb.bbox[3] for _, cb in collected))

        # ----- Spatial clustering: identify column x-positions -----
        # Collect all line x-positions (excluding the label block itself)
        # and cluster them.
        x_counts: Counter[int] = Counter()
        for ci, (gi, cb) in enumerate(collected):
            if ci == 0:
                continue  # skip label
            for ln in cb.lines:
                if ln.text.strip():
                    x_counts[round(ln.bbox[0] / _SPEC_TABLE_X_BUCKET)] += 1

        if not x_counts:
            continue

        # Keep x-positions that appear in at least 2 lines (noise filter).
        stable_xs = sorted(
            xk * _SPEC_TABLE_X_BUCKET
            for xk, cnt in x_counts.items()
            if cnt >= 2
        )
        if len(stable_xs) < 2:
            # Fallback: use all observed positions
            stable_xs = sorted(
                xk * _SPEC_TABLE_X_BUCKET for xk in x_counts)

        # Merge close positions into logical columns.
        col_positions: list[float] = [stable_xs[0]]
        for xp in stable_xs[1:]:
            if xp - col_positions[-1] >= _SPEC_TABLE_X_MERGE:
                col_positions.append(xp)

        if len(col_positions) < 2:
            continue

        num_cols = len(col_positions)

        def assign_col(x: float) -> int:
            """Map an x-coordinate to the nearest column index."""
            best = 0
            best_d = abs(x - col_positions[0])
            for ci, cx in enumerate(col_positions):
                d = abs(x - cx)
                if d < best_d:
                    best_d = d
                    best = ci
            return best

        # ----- Build visual rows by y-band -----
        # Each y-band groups lines at similar vertical positions.
        # Lines are assigned to columns by their x-position.
        yband_cells: dict[int, list[list[Span]]] = {}  # yk -> cells[num_cols]

        for ci, (gi, cb) in enumerate(collected):
            if ci == 0:
                continue  # skip label
            for ln in cb.lines:
                if not ln.text.strip():
                    continue
                # Page-scoped y-band key: lines on different pages
                # never share a y-band, even if their y-coordinates
                # happen to be similar.
                yk = ln.page_num * 1000 + round(
                    ((ln.bbox[1] + ln.bbox[3]) / 2.0)
                    / _SPEC_TABLE_Y_BAND
                )
                if yk not in yband_cells:
                    yband_cells[yk] = [[] for _ in range(num_cols)]
                col_idx = assign_col(ln.bbox[0])
                yband_cells[yk][col_idx].extend(ln.spans)

        if not yband_cells:
            continue

        sorted_ybands = sorted(yband_cells)
        yband_index = {yk: i for i, yk in enumerate(sorted_ybands)}

        # ----- Remove repeated headers (cross-page) -----
        # PDFs repeat table headers at the top of each new page.
        # The first y-band with col-0 content is the real header.
        # Any later y-band whose col-0 text matches the header's
        # col-0 text is a repeat and is dropped, along with the
        # immediately following continuation y-band (e.g. the
        # "conditions" line wrapping from "assertion/note pre/post-").
        first_col0_yk = None
        header_col0_text = ""
        for yk in sorted_ybands:
            col0_t = "".join(
                s.text for s in yband_cells[yk][0]
            ).strip().lower()
            if col0_t:
                first_col0_yk = yk
                header_col0_text = col0_t
                break

        if header_col0_text:
            drop_yks: set[int] = set()
            for yk in sorted_ybands:
                if yk == first_col0_yk:
                    continue
                col0_t = "".join(
                    s.text for s in yband_cells[yk][0]
                ).strip().lower()
                if col0_t == header_col0_text:
                    drop_yks.add(yk)
                    # Also drop the next y-band if it is a header
                    # continuation (col 0 empty, short text in
                    # last column only).
                    idx_in_sorted = yband_index[yk]
                    if idx_in_sorted + 1 < len(sorted_ybands):
                        nxt = sorted_ybands[idx_in_sorted + 1]
                        nxt_col0 = "".join(
                            s.text for s in yband_cells[nxt][0]
                        ).strip()
                        if not nxt_col0:
                            nxt_all = "".join(
                                s.text
                                for c in yband_cells[nxt]
                                for s in c
                            ).strip()
                            if len(nxt_all.split()) <= 3:
                                drop_yks.add(nxt)

            if drop_yks:
                sorted_ybands = [
                    yk for yk in sorted_ybands if yk not in drop_yks
                ]
                _log.debug(
                    "Spec table: dropped %d repeated-header y-bands",
                    len(drop_yks))

        # ----- Merge visual rows into logical rows -----
        # A new logical row starts when column 0 (expression) has
        # content.  Continuation y-bands (col 0 empty) append to
        # the current row -- UNLESS a later y-band on the SAME page
        # has col-0 content: in that case, the continuation belongs
        # to the upcoming row (assertion text that starts above the
        # vertically-centred expression name in the PDF).
        #
        # Cross-page continuations (no col-0 on the same page ahead)
        # are still appended to the current row as before.
        logical_rows: list[list[list[Span]]] = []
        current_row: list[list[Span]] | None = None

        # Pre-compute next col-0 y-band for each position.
        _next_col0: dict[int, int | None] = {}
        _last_c0: int | None = None
        for yk in reversed(sorted_ybands):
            c0t = "".join(s.text for s in yband_cells[yk][0]).strip()
            if c0t:
                _last_c0 = yk
            _next_col0[yk] = _last_c0

        deferred: list[list[list[Span]]] = []

        def _append_continuation(
            row: list[list[Span]], cells: list[list[Span]]
        ) -> None:
            for ci in range(num_cols):
                if cells[ci]:
                    if row[ci] and any(
                        s.text.strip() for s in row[ci]
                    ):
                        row[ci].append(Span(text="\n"))
                    row[ci].extend(cells[ci])

        last_col0_page: int | None = None
        in_deferred_zone = False
        prev_y_max: float | None = None

        for yk in sorted_ybands:
            cells = yband_cells[yk]
            col0_text = "".join(s.text for s in cells[0]).strip()
            current_page = yk // 1000

            # Compute actual y-extent from span bboxes.
            y_min_cur: float | None = None
            y_max_cur: float | None = None
            for cell in cells:
                for s in cell:
                    if s.text.strip() and s.bbox != (0, 0, 0, 0):
                        if y_min_cur is None or s.bbox[1] < y_min_cur:
                            y_min_cur = s.bbox[1]
                        if y_max_cur is None or s.bbox[3] > y_max_cur:
                            y_max_cur = s.bbox[3]

            if col0_text:
                in_deferred_zone = False
                last_col0_page = current_page
                if current_row is not None:
                    logical_rows.append(current_row)
                # Prepend deferred continuation cells (assertion
                # text that sits above the expression name on the
                # same page) BEFORE the col-0 y-band's own cells.
                if deferred:
                    current_row = [[] for _ in range(num_cols)]
                    for d_cells in deferred:
                        _append_continuation(current_row, d_cells)
                    _append_continuation(current_row, cells)
                    deferred.clear()
                else:
                    current_row = [list(c) for c in cells]
            elif current_row is None:
                current_row = [list(c) for c in cells]
            else:
                # Detect row boundaries via y-gap analysis.
                # Within a table cell, consecutive lines are spaced
                # ~7-9 px apart.  Between cells (row boundaries),
                # the gap is ~18-19 px.  _SPEC_TABLE_Y_BAND (~15)
                # cleanly separates the two regimes.
                if not in_deferred_zone:
                    if current_page != last_col0_page:
                        nxt = _next_col0.get(yk)
                        if (nxt is not None and nxt != yk
                                and (nxt // 1000) == current_page):
                            in_deferred_zone = True
                    elif (prev_y_max is not None
                          and y_min_cur is not None
                          and y_min_cur - prev_y_max
                              > _SPEC_TABLE_Y_BAND):
                        nxt = _next_col0.get(yk)
                        if (nxt is not None and nxt != yk
                                and (nxt // 1000) == current_page):
                            in_deferred_zone = True

                if in_deferred_zone:
                    deferred.append([list(c) for c in cells])
                else:
                    _append_continuation(current_row, cells)

            if y_max_cur is not None:
                prev_y_max = y_max_cur

        if current_row is not None:
            logical_rows.append(current_row)

        # ----- Header-split guard -----
        # The col-0 merge absorbs all y-bands (including multi-line
        # data content in the assertion column) into the header row
        # until the first expression entry.  Split row 0 into a clean
        # header and a data spillover fragment.
        #
        # Strategy: for each cell in row 0, walk the \n-separated
        # segments.  Header label segments are short (<=5 words).
        # The split point is the FIRST segment that looks like data:
        # long (>5 words) or starts with a known data marker like
        # "Returns:", "Effects:", "Preconditions:".
        def _find_header_split(cell_spans: list[Span]) -> int | None:
            """Return span index where header ends and data begins."""
            nl_indices = [
                i for i, s in enumerate(cell_spans) if s.text == "\n"
            ]
            if not nl_indices:
                return None
            # Walk each \n and check if the post-\n text is data.
            for nl_idx in nl_indices:
                post_text = "".join(
                    s.text for s in cell_spans[nl_idx + 1:]
                ).strip()
                if not post_text:
                    continue
                first_segment = post_text.split("\n")[0].strip()
                if (len(first_segment.split()) > _SPEC_HEADER_MAX_WORDS
                        or _DATA_MARKERS_RE.match(first_segment)):
                    return nl_idx
            return None

        if logical_rows:
            row0 = logical_rows[0]
            # Find the best split point across all cells.
            split_indices: list[int | None] = [
                _find_header_split(cell) for cell in row0
            ]
            has_split = any(si is not None for si in split_indices)

            if has_split:
                header_row: list[list[Span]] = []
                spill_row: list[list[Span]] = []
                for ci, cell_spans in enumerate(row0):
                    si = split_indices[ci]
                    if si is not None:
                        header_row.append(list(cell_spans[:si]))
                        spill_row.append(list(cell_spans[si + 1:]))
                    else:
                        header_row.append(list(cell_spans))
                        spill_row.append([])

                logical_rows[0] = header_row
                has_spill = any(
                    any(s.text.strip() for s in c) for c in spill_row
                )
                if has_spill:
                    if len(logical_rows) > 1:
                        for ci in range(num_cols):
                            if spill_row[ci]:
                                if logical_rows[1][ci] and any(
                                    s.text.strip()
                                    for s in logical_rows[1][ci]
                                ):
                                    logical_rows[1][ci] = (
                                        spill_row[ci]
                                        + [Span(text="\n")]
                                        + logical_rows[1][ci]
                                    )
                                else:
                                    logical_rows[1][ci] = spill_row[ci]
                    else:
                        logical_rows.insert(1, spill_row)

        if len(logical_rows) < 2:
            continue

        # Cell density guard: a genuine multi-column table has content
        # in most cells.  A table label followed by prose paragraphs
        # produces rows where only column 0 is populated and the rest
        # are empty.  Require that at least 40% of cells are non-empty
        # (excluding the header row, which always has all cells filled).
        total_cells = 0
        non_empty_cells = 0
        for row in logical_rows:
            for cell in row:
                total_cells += 1
                if "".join(s.text for s in cell).strip():
                    non_empty_cells += 1
        cell_density = non_empty_cells / total_cells if total_cells else 0
        if cell_density < 0.40:
            _log.debug(
                "Spec table 'Table %d' rejected: cell density %.0f%% "
                "(%d/%d) below threshold",
                table_num, cell_density * 100,
                non_empty_cells, total_cells)
            continue

        # Header row check: the first logical row (header) should have
        # content in at least 2 columns.  If the header is incomplete,
        # this is likely not a real table.
        header_filled = sum(
            1 for cell in logical_rows[0]
            if "".join(s.text for s in cell).strip()
        )
        if header_filled < 2:
            _log.debug(
                "Spec table 'Table %d' rejected: header has only %d "
                "filled cells (need >= 2)", table_num, header_filled)
            continue

        # WG21 spec-header guard: the header row must contain at least
        # one column name characteristic of WG21 requirement tables.
        # This prevents the pass from consuming general "Table N"
        # labels in non-spec papers (e.g. side-by-side code figures).
        
        header_text = " ".join(
            "".join(s.text for s in cell).strip()
            for cell in logical_rows[0]
        )
        if not _SPEC_COL_NAMES_RE.search(header_text):
            _log.debug(
                "Spec table 'Table %d' rejected: header '%s' lacks "
                "WG21 spec column names", table_num,
                header_text[:80])
            continue

        # ----- Classify and emit -----
        kind_val, strategy_val, logical_rows = _classify_and_annotate(
            logical_rows)
        # Override: these are known spec tables regardless of signal heuristics.
        if kind_val == "false_positive":
            kind_val = TableKind.SPEC_TABLE.value
            strategy_val = TableStrategy.HTML_TABLE.value

        # Collect consumed block indices and lines.  The label block
        # (first in collected) is NOT consumed: it stays in remaining
        # blocks so the structure phase emits it as a paragraph caption
        # above the table HTML.
        consumed_indices: set[int] = set()
        all_lines = []
        for ci_idx, (gi, cb) in enumerate(collected):
            if ci_idx == 0:
                continue  # skip label block
            consumed_indices.add(gi)
            all_lines.extend(cb.lines)
        all_lines.sort(key=lambda ln: (ln.page_num, ln.bbox[1], ln.bbox[0]))

        # Consume stray column-header blocks that appear BEFORE the
        # label in MuPDF reading order (e.g. "expression", "return
        # type", "assertion/note pre/post-conditions" as separate
        # blocks above the Table label).  Without this, they leak
        # as plain-text paragraphs in the output.
        
        for j in range(label_gi - 1, max(label_gi - 6, -1), -1):
            if j < 0:
                break
            bk = blocks[j]
            if bk.page_num != label_page:
                break
            if _COL_HEADER_RE.match(bk.text.strip()):
                consumed_indices.add(j)

        used |= consumed_indices

        text = _render_table_text(logical_rows)

        table_sections.append(Section(
            kind=SectionKind.TABLE,
            text=text,
            confidence=Confidence.HIGH,
            lines=all_lines,
            page_num=label_page,
            columns=logical_rows,
            table_kind=kind_val,
            table_strategy=strategy_val,
            table_source="spec_label",
        ))
        _log.info(
            "Spec table 'Table %d' on page %d: %d rows x %d cols "
            "[kind=%s, strategy=%s, source=spec_label]",
            table_num, label_page, len(logical_rows), num_cols,
            kind_val, strategy_val)

    # Cross-page continuation header dedup: when a table spans pages,
    # MuPDF repeats the header row on each page.  If consecutive
    # spec-label sections share the same header text AND are on
    # adjacent pages (gap <= 1), strip the duplicate from the
    # continuation.  Tables separated by 2+ pages are independent.
    if len(table_sections) >= 2:
        for i in range(len(table_sections) - 1):
            t1 = table_sections[i]
            t2 = table_sections[i + 1]
            if not t1.columns or not t2.columns:
                continue
            if abs(t2.page_num - t1.page_num) > 1:
                continue
            if len(t1.columns[0]) != len(t2.columns[0]):
                continue
            h1 = tuple(
                "".join(s.text for s in cell).strip()
                for cell in t1.columns[0])
            h2 = tuple(
                "".join(s.text for s in cell).strip()
                for cell in t2.columns[0])
            if h1 == h2 and len(t2.columns) > 1:
                t2.columns = t2.columns[1:]
                t2.table_continuation = True
                t2.text = _render_table_text(t2.columns)
                _log.info(
                    "Stripped duplicate header from spec table "
                    "continuation on page %d", t2.page_num)

    return table_sections, used


def _filter_overlapping_mupdf_tables(
    page_mupdf_tables: dict[int, list[dict]],
    existing_sections: list[Section],
) -> dict[int, list[dict]]:
    """Remove find_tables entries that overlap with already-detected tables."""
    if not existing_sections:
        return page_mupdf_tables

    existing_ranges: list[tuple[int, float, float]] = []
    for sec in existing_sections:
        if not sec.lines:
            continue
        y_min = min(ln.bbox[1] for ln in sec.lines)
        y_max = max(ln.bbox[3] for ln in sec.lines)
        existing_ranges.append((sec.page_num, y_min, y_max))

    result: dict[int, list[dict]] = {}
    for pg, tables in page_mupdf_tables.items():
        kept = []
        for tbl in tables:
            bbox = tbl["bbox"]
            overlaps = False
            for epg, ey0, ey1 in existing_ranges:
                if pg != epg:
                    continue
                overlap = min(bbox[3], ey1) - max(bbox[1], ey0)
                if overlap > 0:
                    overlaps = True
                    break
            if not overlaps:
                kept.append(tbl)
        if kept:
            result[pg] = kept
    return result


# ---------------------------------------------------------------------------
# Pass 1 inner-loop decision helpers (extracted for testability).
# Each function evaluates one decision branch and returns a _MatchResult
# or None (= this branch does not apply).
# ---------------------------------------------------------------------------


def _try_strict_match(
    blocks: list[Block],
    j: int,
    ref_cols: list[float],
) -> Optional[_MatchResult]:
    """Branch 1: next block's columns match ref_cols exactly."""
    next_cols = _block_column_positions(blocks[j])
    if next_cols is not None and _columns_match(ref_cols, next_cols):
        return _MatchResult(advance_to=j + 1)
    return None


def _try_relaxed_match(
    blocks: list[Block],
    j: int,
    ref_cols: list[float],
    table_blocks: list[Block],
) -> Optional[_MatchResult]:
    """Branch 2: same column count, close proximity. Adopts new positions."""
    next_cols = _block_column_positions(blocks[j])
    if (next_cols is not None
            and _columns_count_match(
                ref_cols, next_cols,
                table_blocks[-1].bbox[3], blocks[j].bbox[1],
                blocks[j].page_num == table_blocks[-1].page_num)):
        return _MatchResult(advance_to=j + 1, new_ref_cols=next_cols)
    return None


def _try_subset_columns_absorb(
    blocks: list[Block],
    j: int,
    ref_cols: list[float],
    table_blocks: list[Block],
) -> Optional[_MatchResult]:
    """Branch 3: fewer columns, all align to ref_cols. Marks as partial."""
    next_cols = _block_column_positions(blocks[j])
    if (next_cols is not None
            and _is_subset_columns(next_cols, ref_cols)
            and not _block_is_monospace(blocks[j])
            and not _block_is_monospace(table_blocks[0])
            and blocks[j].page_num == table_blocks[-1].page_num):
        return _MatchResult(
            advance_to=j + 1,
            absorbed_ids=frozenset({id(blocks[j])}),
            multi_orphan=True,
        )
    return None


def _try_single_orphan(
    blocks: list[Block],
    j: int,
    ref_cols: list[float],
    column_xs: frozenset[float],
) -> Optional[_MatchResult]:
    """Sub-branch 4a: orphan at j, next block (j+1) is a full column match.

    Precondition: j + 1 < len(blocks) (caller must verify).
    """
    peek_cols = _block_column_positions(blocks[j + 1])
    if not (peek_cols is not None
            and blocks[j + 1].page_num == blocks[j].page_num
            and _columns_match(ref_cols, peek_cols)):
        return None
    absorbed = set()
    if len(ref_cols) == 2 and blocks[j].lines:
        orphan_x0 = blocks[j].lines[0].bbox[0]
        orphan_spans = [
            s for s in blocks[j].lines[0].spans
            if s.text.strip()]
        is_mono = (orphan_spans
                   and all(s.monospace for s in orphan_spans))
        if (orphan_spans
                and not is_mono
                and any(abs(orphan_x0 - ref_cols[ci])
                        <= _COLUMN_X_TOLERANCE
                        for ci in range(1, len(ref_cols)))):
            absorbed.add(id(blocks[j]))
    return _MatchResult(
        advance_to=j + 1,
        absorbed_ids=frozenset(absorbed),
    )


def _scan_for_confirmer(
    blocks: list[Block],
    j: int,
    ref_cols: list[float],
    column_xs: frozenset[float],
) -> tuple[int, int, bool]:
    """Scan ahead past orphans/subsets to find a full-column confirmer.

    Returns (scan_pos, absorbed_end, found_full).
    """
    table_page = blocks[j].page_num
    max_scan = 3 * len(ref_cols)
    scan = j
    absorbed_end = j
    found_full = False
    while (scan < len(blocks)
           and blocks[scan].page_num == table_page
           and scan - j < max_scan):
        if _is_column_aligned_orphan(blocks[scan], column_xs):
            scan += 1
            continue
        sc = _block_column_positions(blocks[scan])
        if sc is not None and _columns_match(ref_cols, sc):
            absorbed_end = scan
            found_full = True
            break
        if sc is not None and _is_subset_columns(sc, ref_cols):
            scan += 1
            continue
        break
    return scan, absorbed_end, found_full


def _check_collective_coverage(
    blocks: list[Block],
    j: int,
    scan: int,
    ref_cols: list[float],
    column_xs: frozenset[float],
    table_blocks: list[Block],
    partial_absorbed: set[int],
) -> Optional[_MatchResult]:
    """Fix B: check if orphan blocks collectively cover all header columns.

    Extends scan past max_scan limit since borderless tables have each
    cell as a separate block.  Returns _MatchResult if coverage passes.
    """
    if len(ref_cols) < 3 or scan <= j:
        return None

    table_page = blocks[j].page_num
    while (scan < len(blocks)
           and blocks[scan].page_num == table_page
           and _is_column_aligned_orphan(blocks[scan], column_xs)):
        bx0 = (blocks[scan].lines[0].bbox[0]
               if blocks[scan].lines
               else blocks[scan].bbox[0])
        if not any(abs(bx0 - rc) <= _COLUMN_X_BUCKET for rc in ref_cols):
            break
        scan += 1

    orphan_run = [
        blocks[k] for k in range(j, scan)
        if _is_column_aligned_orphan(blocks[k], column_xs)]

    col_ybands: dict[int, set[int]] = {}
    for ob in orphan_run:
        for oln in ob.lines:
            if not oln.spans or not oln.text.strip():
                continue
            x0 = oln.bbox[0]
            best_ci = min(
                range(len(ref_cols)),
                key=lambda ci: abs(x0 - ref_cols[ci]))
            if abs(x0 - ref_cols[best_ci]) <= _COLUMN_X_TOLERANCE:
                ym = (oln.bbox[1] + oln.bbox[3]) / 2.0
                col_ybands.setdefault(best_ci, set()).add(
                    round(ym / _Y_BAND_HEIGHT))

    # Include columns from subset blocks already absorbed.
    for tb in table_blocks:
        if id(tb) not in partial_absorbed:
            continue
        tb_cp = _block_column_positions(tb)
        if tb_cp is None or not _is_subset_columns(tb_cp, ref_cols):
            continue
        for tln in tb.lines:
            if not tln.spans or not tln.text.strip():
                continue
            tx0 = tln.bbox[0]
            tci = min(
                range(len(ref_cols)),
                key=lambda ci: abs(tx0 - ref_cols[ci]))
            if abs(tx0 - ref_cols[tci]) <= _COLUMN_X_TOLERANCE:
                tym = (tln.bbox[1] + tln.bbox[3]) / 2.0
                col_ybands.setdefault(tci, set()).add(
                    round(tym / _Y_BAND_HEIGHT))

    all_ybs: set[int] = set()
    for ybs in col_ybands.values():
        all_ybs.update(ybs)
    mc_bands = sum(
        1 for yk in all_ybs
        if sum(1 for ci, ybs in col_ybands.items() if yk in ybs) >= 2)

    if len(col_ybands) >= len(ref_cols) and mc_bands >= 2:
        absorbed_ids = frozenset(id(blocks[k]) for k in range(j, scan))
        return _MatchResult(
            advance_to=scan,
            absorbed_ids=absorbed_ids,
            multi_orphan=True,
        )
    return None


def _try_orphan_lookahead(
    blocks: list[Block],
    j: int,
    ref_cols: list[float],
    column_xs: frozenset[float],
    table_blocks: list[Block],
    partial_absorbed: set[int],
) -> Optional[_MatchResult]:
    """Branch 4: single-line block aligned to a column, lookahead to confirm."""
    if not (_is_column_aligned_orphan(blocks[j], column_xs)
            and j + 1 < len(blocks)
            and blocks[j].page_num == table_blocks[-1].page_num):
        return None

    # 4a: Single-orphan case
    result = _try_single_orphan(blocks, j, ref_cols, column_xs)
    if result is not None:
        return result

    # 4b: Multi-step lookahead
    peek_cols = _block_column_positions(blocks[j + 1])
    if not (len(table_blocks) >= 1
            and ((peek_cols is not None
                  and blocks[j + 1].page_num == blocks[j].page_num
                  and _is_subset_columns(peek_cols, ref_cols))
                 or (_is_column_aligned_orphan(blocks[j + 1], column_xs)
                     and blocks[j + 1].page_num == blocks[j].page_num))
            and (len(table_blocks) >= 2
                 or not _block_is_monospace(blocks[j]))):
        return None

    scan, absorbed_end, found_full = _scan_for_confirmer(
        blocks, j, ref_cols, column_xs)

    if found_full:
        absorbed_ids = frozenset(id(blocks[k]) for k in range(j, absorbed_end))
        return _MatchResult(
            advance_to=absorbed_end,
            absorbed_ids=absorbed_ids,
            multi_orphan=True,
        )

    # Fix B: Collective orphan coverage
    return _check_collective_coverage(
        blocks, j, scan, ref_cols, column_xs,
        table_blocks, partial_absorbed)


def _try_partial_row(
    blocks: list[Block],
    j: int,
    ref_cols: list[float],
    column_xs: frozenset[float],
    table_blocks: list[Block],
) -> Optional[_MatchResult]:
    """Branch 5: fewer columns but all x-positions match known columns."""
    if not (len(table_blocks) >= 2
            and _is_partial_row(blocks[j], ref_cols,
                                table_blocks[-1].bbox[3],
                                blocks[j].page_num
                                == table_blocks[-1].page_num)):
        return None

    # Absorb trailing orphans after the partial row.
    end = j + 1
    absorbed = set()
    while (end < len(blocks)
           and _is_column_aligned_orphan(blocks[end], column_xs)
           and blocks[end].page_num == table_blocks[-1].page_num):
        absorbed.add(id(blocks[end]))
        end += 1
    return _MatchResult(
        advance_to=end,
        absorbed_ids=frozenset(absorbed),
    )


def _build_rows_ybanded(
    table_blocks: list[Block],
    ref_cols: list[float],
    num_cols: int,
) -> tuple[list[list[list]], list[Line]]:
    """Build rows using y-band grouping for multi-orphan tables.

    Returns (rows, all_lines).
    """
    all_lines: list[Line] = []
    tagged: list[tuple[float, int, Line]] = []
    for blk in table_blocks:
        for line in blk.lines:
            all_lines.append(line)
            y_mid = (line.bbox[1] + line.bbox[3]) / 2
            best_col = min(
                range(num_cols),
                key=lambda ci: abs(line.bbox[0] - ref_cols[ci]),
            )
            tagged.append((y_mid, best_col, line))
    tagged.sort(key=lambda t: t[0])

    # Adaptive band gap from sorted y-midpoints.
    y_mids = sorted({t[0] for t in tagged})
    gaps = [y_mids[i + 1] - y_mids[i] for i in range(len(y_mids) - 1)]
    real_gaps = sorted({g for g in gaps if g > 1.0})
    if len(real_gaps) >= 3:
        max_jump = 0.0
        split_idx = 0
        for gi in range(len(real_gaps) - 1):
            jump = real_gaps[gi + 1] - real_gaps[gi]
            if jump > max_jump:
                max_jump = jump
                split_idx = gi
        band_gap = (real_gaps[split_idx] + real_gaps[split_idx + 1]) / 2
    elif len(real_gaps) >= 2:
        band_gap = (real_gaps[0] + real_gaps[1]) / 2
    else:
        band_gap = _SPEC_TABLE_Y_BAND

    bands: list[list[tuple[int, Line]]] = [[]]
    prev_y = tagged[0][0] if tagged else 0.0
    for y_mid, col_idx, line in tagged:
        if bands[-1] and y_mid - prev_y > band_gap:
            bands.append([])
        bands[-1].append((col_idx, line))
        prev_y = y_mid

    rows: list[list[list]] = []
    for band in bands:
        row: list[list] = [[] for _ in range(num_cols)]
        for col_idx, line in band:
            if row[col_idx] and line.spans:
                row[col_idx].append(Span(text=" "))
            row[col_idx].extend(line.spans)
        rows.append(row)

    # Multi-line cell merge: bands with col-0 filled are "row anchors";
    # bands with col-0 empty are continuations merged by y-distance.
    if len(rows) > 1 and len(bands) > 1:
        band_ymids = [
            sum(ln.bbox[1] for _, ln in band) / len(band)
            if band else 0.0
            for band in bands]
        anchor_idxs = []
        non_anchor_idxs = []
        for ri, row in enumerate(rows):
            col0_text = "".join(s.text for s in row[0]).strip()
            if col0_text:
                anchor_idxs.append(ri)
            else:
                any_other = any(
                    "".join(s.text for s in row[ci]).strip()
                    for ci in range(1, num_cols))
                if any_other:
                    non_anchor_idxs.append(ri)
                else:
                    anchor_idxs.append(ri)
        if non_anchor_idxs and anchor_idxs:
            merge_map: dict[int, int] = {}
            for ni in non_anchor_idxs:
                ny = band_ymids[ni]
                best_ai = min(
                    anchor_idxs,
                    key=lambda ai: abs(band_ymids[ai] - ny))
                merge_map[ni] = best_ai
            merged_rows: dict[int, list[list]] = {}
            for ri, row in enumerate(rows):
                target_ri = merge_map.get(ri, ri)
                if target_ri not in merged_rows:
                    merged_rows[target_ri] = [
                        list(cell) for cell in rows[target_ri]]
                if ri != target_ri:
                    target = merged_rows[target_ri]
                    prepend = (band_ymids[ri] < band_ymids[target_ri])
                    for ci in range(num_cols):
                        ct = "".join(s.text for s in row[ci]).strip()
                        if ct:
                            if prepend:
                                ins = list(row[ci])
                                if target[ci]:
                                    ins.append(Span(text=" "))
                                ins.extend(target[ci])
                                target[ci] = ins
                            else:
                                if target[ci]:
                                    target[ci].append(Span(text=" "))
                                target[ci].extend(row[ci])
            rows = [merged_rows[ai] for ai in sorted(merged_rows.keys())]

    return rows, all_lines


def _build_rows_sequential(
    table_blocks: list[Block],
    ref_cols: list[float],
    num_cols: int,
    partial_absorbed: set[int],
) -> tuple[list[list[list]], list[Line]]:
    """Build rows using sequential block-to-row mapping.

    Returns (rows, all_lines).
    """
    all_lines: list[Line] = []
    rows: list[list[list]] = []
    continuation_row_indices: set[int] = set()

    for blk in table_blocks:
        from_partial = id(blk) in partial_absorbed
        blk_cols = _block_column_positions(blk)
        if blk_cols is None:
            blk_cols = ref_cols

        if from_partial:
            row: list[list] = [[] for _ in range(num_cols)]
            for line in blk.lines:
                all_lines.append(line)
                best_col = min(
                    range(num_cols),
                    key=lambda ci: abs(line.bbox[0] - ref_cols[ci]),
                )
                if row[best_col] and line.spans:
                    row[best_col].append(Span(text=" "))
                row[best_col].extend(line.spans)
            continuation_row_indices.add(len(rows))
        else:
            row = []
            for line in blk.lines[:num_cols]:
                row.append(list(line.spans))
                all_lines.append(line)
            while len(row) < num_cols:
                row.append([])
            for line in blk.lines[num_cols:]:
                all_lines.append(line)
                line_x = line.bbox[0]
                best_col = min(
                    range(num_cols),
                    key=lambda ci: abs(line_x - blk_cols[ci]),
                )
                if row[best_col] and line.spans:
                    row[best_col].append(Span(text="\n"))
                row[best_col].extend(line.spans)
        rows.append(row)

    if continuation_row_indices:
        tmp: list[list[list]] = []
        for ri, row in enumerate(rows):
            if ri in continuation_row_indices and tmp:
                target = tmp[-1]
                for ci in range(num_cols):
                    if not row[ci]:
                        continue
                    if target[ci]:
                        target[ci].append(Span(text=" "))
                    target[ci].extend(row[ci])
            else:
                tmp.append(row)
        rows = tmp

    return rows, all_lines


def detect_tables(
    blocks: list[Block],
    *,
    page_mupdf_tables: dict[int, list[dict]] | None = None,
    two_column_pages: frozenset[int] = frozenset(),
) -> tuple[list[Section], list[Block]]:
    """Detect table regions from MuPDF block structure.

    Returns (table_sections, remaining_blocks).
    Table sections have kind=TABLE with high confidence.
    Remaining blocks are the non-table blocks for normal processing.
    """
    column_xs = _find_column_xs(blocks)  # geometric second signal

    table_sections: list[Section] = []

    # Pass 0: label-anchored spec tables ("Table N - XYZ requirements").
    # Runs FIRST because the label regex is highly distinctive (near-zero
    # false positives) and later passes (especially side-by-side in Pass 2)
    # consume the scattered table-cell blocks before this pass can see them.
    spec_tables, spec_used = _detect_spec_tables_by_label(blocks)
    if spec_tables:
        table_sections.extend(spec_tables)
    # Remove consumed blocks; renumber for subsequent passes.
    remaining_after_spec: list[Block] = [
        b for idx, b in enumerate(blocks) if idx not in spec_used
    ]

    blocks = remaining_after_spec  # Post-spec-label blocks

    # Pre-pass: atomized-header side-by-side tables only.  Must run
    # before Pass 1 (horizontal rows) because Pass 1 consumes single-
    # block columnar rows as independent mini-tables, stealing body
    # blocks that belong to the larger atomized-header table.
    sbs_pre_tables, sbs_pre_used = _detect_side_by_side_tables(
        blocks, atomized_only=True,
        page_mupdf_tables=page_mupdf_tables)
    if sbs_pre_tables:
        table_sections.extend(sbs_pre_tables)
        blocks = [b for idx, b in enumerate(blocks)
                  if idx not in sbs_pre_used]

    # Pass 1: horizontal-row tables
    remaining: list[Block] = []
    i = 0

    while i < len(blocks):
        cols = _block_column_positions(blocks[i])
        if cols is None:
            remaining.append(blocks[i])
            i += 1
            continue

        table_blocks = [blocks[i]]
        # Track the "reference" column positions used for strict matching.
        # When a relaxed (column-count) match fires, adopt the new block's
        # positions as reference so subsequent data rows can strict-match
        # against each other.
        ref_cols = cols
        partial_absorbed: set[int] = set()  # id(block) for partial-row path
        multi_orphan_used = False
        j = i + 1
        while j < len(blocks):
            # Branch 1: strict column match
            result = _try_strict_match(blocks, j, ref_cols)
            if result is not None:
                table_blocks.append(blocks[j])
                j = result.advance_to
                continue

            # Branch 2: relaxed match (same count, close proximity)
            result = _try_relaxed_match(blocks, j, ref_cols, table_blocks)
            if result is not None:
                table_blocks.append(blocks[j])
                ref_cols = result.new_ref_cols
                j = result.advance_to
                continue

            # Branch 3: subset columns (partial absorption)
            result = _try_subset_columns_absorb(
                blocks, j, ref_cols, table_blocks)
            if result is not None:
                table_blocks.append(blocks[j])
                partial_absorbed.update(result.absorbed_ids)
                multi_orphan_used = True
                j = result.advance_to
                continue

            # Branch 4: orphan lookahead (single/multi-step)
            result = _try_orphan_lookahead(
                blocks, j, ref_cols, column_xs,
                table_blocks, partial_absorbed)
            if result is not None:
                for k in range(j, result.advance_to):
                    table_blocks.append(blocks[k])
                partial_absorbed.update(result.absorbed_ids)
                if result.multi_orphan:
                    multi_orphan_used = True
                j = result.advance_to
                continue

            # Branch 4 failure falls through here. Today this is safe because
            # _is_column_aligned_orphan and _is_partial_row are mutually exclusive
            # (orphan blocks are single-line; partial row rejects single-line).
            # Branch 5: partial row
            result = _try_partial_row(
                blocks, j, ref_cols, column_xs, table_blocks)
            if result is not None:
                table_blocks.append(blocks[j])
                for k in range(j + 1, result.advance_to):
                    table_blocks.append(blocks[k])
                partial_absorbed.update(result.absorbed_ids)
                j = result.advance_to
                continue

            # No branch matched: end table
            break

        # Absorb trailing continuations of the last row's non-first
        # columns.  These are single-line blocks whose x0 matches
        # column 1+ but have no following full-column row to trigger
        # the lookahead-based orphan absorption above.
        # Guard: only extend tables that are already well-established
        # (header + data rows) to avoid grabbing code-comparison content.
        if len(table_blocks) >= _MIN_TABLE_ROWS:
            while (j < len(blocks)
                   and _is_trailing_continuation(
                       blocks[j], ref_cols,
                       table_blocks[-1].bbox[3],
                       blocks[j].page_num == table_blocks[-1].page_num)):
                partial_absorbed.add(id(blocks[j]))
                table_blocks.append(blocks[j])
                j += 1

        if len(table_blocks) >= _MIN_TABLE_ROWS:
            num_cols = len(ref_cols)
            rows: list[list[list]] = []
            all_lines = []

            # Header detection: check block before table for column headers
            header_row = _detect_header_block(blocks, i, ref_cols, num_cols)
            if header_row is not None:
                rows.append(header_row)

            # When multi-orphan lookahead absorbed blocks, MuPDF
            # delivers column fragments in non-y-sorted order.
            # Use y-band grouping to reconstruct logical rows
            # instead of sequential block-to-row mapping.
            if multi_orphan_used:
                ybanded_rows, all_lines = _build_rows_ybanded(
                    table_blocks, ref_cols, num_cols)
                rows.extend(ybanded_rows)
            else:
                seq_rows, all_lines = _build_rows_sequential(
                    table_blocks, ref_cols, num_cols, partial_absorbed)
                rows.extend(seq_rows)

            # Pass B: merge forward-orphans (single populated first
            # cell) into the following row.  This handles wrapped cell
            # first lines absorbed by the existing orphan-lookahead.
            merged: list[list[list]] = []
            k = 0
            while k < len(rows):
                row = rows[k]
                if (k + 1 < len(rows)
                        and bool(row[0])
                        and all(not cell for cell in row[1:])
                        and bool(rows[k + 1][0])):
                    sep = [Span(text="\n")]
                    merged.append(
                        [row[0] + sep + rows[k + 1][0]]
                        + rows[k + 1][1:])
                    k += 2
                else:
                    merged.append(row)
                    k += 1
            rows = merged

            # Dedup repeated headers from cross-page tables.
            # PDFs that span multiple pages often repeat the
            # header row at the top of each continuation page.
            if len(rows) >= 3:
                hdr_text = tuple(
                    "".join(s.text for s in cell).strip()
                    for cell in rows[0])
                deduped: list[list[list]] = [rows[0]]
                for row in rows[1:]:
                    row_text = tuple(
                        "".join(s.text for s in cell).strip()
                        for cell in row)
                    if row_text != hdr_text:
                        deduped.append(row)
                rows = deduped

            # MuPDF-overlap guard (Pass 1): if the table blocks overlap a
            # substantial MuPDF find_tables() region, defer to Pass 5.
            # Skip deferral for predominantly monospace tables (code
            # comparisons) where Pass 1 produces better results.
            p1_page = table_blocks[0].page_num
            p1_deferred = False
            if page_mupdf_tables:
                mono_spans = 0
                total_spans = 0
                for blk in table_blocks:
                    for ln in blk.lines:
                        for sp in ln.spans:
                            if sp.text.strip():
                                total_spans += 1
                                if sp.monospace:
                                    mono_spans += 1
                p1_mono_ratio = mono_spans / total_spans if total_spans else 0
                if p1_mono_ratio < _MONO_RATIO_THRESHOLD:
                    # Use only blocks on p1_page for bounding box.
                    # Cross-page tables mix y-coordinates from
                    # different pages, inflating p1_h and defeating
                    # the phantom guard.
                    same_pg = [b for b in table_blocks
                               if b.page_num == p1_page]
                    p1_y0 = min(b.bbox[1] for b in same_pg)
                    p1_y1 = max(b.bbox[3] for b in same_pg)
                    p1_x0 = min(b.bbox[0] for b in same_pg)
                    p1_x1 = max(b.bbox[2] for b in same_pg)
                    for tbl in page_mupdf_tables.get(p1_page, []):
                        tb = tbl["bbox"]
                        if tbl.get("row_count", 0) < _SBS_MUPDF_DEFER_MIN_ROWS:
                            continue
                        # Phantom guard: if MuPDF's table is much
                        # taller than what Pass 1 detected, MuPDF has
                        # merged prose with the real table. Keep the
                        # Pass 1 detection.
                        mupdf_h = tb[3] - tb[1]
                        p1_h = max(p1_y1 - p1_y0, 1.0)
                        if mupdf_h > p1_h * 3.0:
                            continue
                        ov_x = max(0, min(p1_x1, tb[2]) - max(p1_x0, tb[0]))
                        ov_y = max(0, min(p1_y1, tb[3]) - max(p1_y0, tb[1]))
                        if ov_x > 0 and ov_y > 0:
                            p1_area = max(
                                (p1_x1 - p1_x0) * (p1_y1 - p1_y0), 1)
                            if (ov_x * ov_y) / p1_area > 0.30:
                                _log.debug(
                                    "Pass 1 deferred to MuPDF Native: "
                                    "page %d, MuPDF rows=%d",
                                    p1_page, tbl.get("row_count", 0))
                                p1_deferred = True
                                break
            if p1_deferred:
                for blk in table_blocks:
                    remaining.append(blk)
                i = j
                continue

            kind_val, strategy_val, rows = _classify_and_annotate(rows)

            # Bibliography: not a real table, return blocks to prose pipeline.
            if kind_val == TableKind.BIBLIOGRAPHY.value:
                _log.debug("Pass 1 bibliography bypass: %d blocks on page %d",
                            len(table_blocks), p1_page)
                for blk in table_blocks:
                    remaining.append(blk)
                i = j
                continue

            text = _render_table_text(rows)

            table_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=p1_page,
                columns=rows,
                table_kind=kind_val,
                table_strategy=strategy_val,
            ))
            _log.debug("Table detected: %d rows x %d cols on page %d",
                        len(rows), num_cols, p1_page)
            i = j
        else:
            remaining.append(blocks[i])
            i += 1

    # Pass 2: side-by-side block tables (non-atomized) on remaining blocks
    sbs_tables, sbs_used = _detect_side_by_side_tables(
        remaining, page_mupdf_tables=page_mupdf_tables)
    if sbs_tables:
        table_sections.extend(sbs_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in sbs_used]

    # Cross-page continuation for side-by-side tables: when tables on
    # adjacent pages share identical headers, strip the duplicate header
    # so the emit phase folds them into one visual table.  Operates on
    # all side-by-side tables (pre-pass + regular pass) sorted by page.
    sbs_all = [s for s in table_sections
               if s.table_kind == "clean_matrix"
               and s.table_strategy == "html_table"]
    sbs_all.sort(key=lambda s: s.page_num)
    for k in range(len(sbs_all) - 1):
        t1 = sbs_all[k]
        t2 = sbs_all[k + 1]
        if (t2.page_num - t1.page_num) not in (0, 1):
            continue
        if not t1.columns or not t2.columns:
            continue
        if len(t1.columns[0]) != len(t2.columns[0]):
            continue
        h1 = tuple(
            "".join(s.text for s in cell).strip()
            for cell in t1.columns[0])
        h2 = tuple(
            "".join(s.text for s in cell).strip()
            for cell in t2.columns[0])
        if h1 == h2 and len(t2.columns) > 1:
            t2.columns = t2.columns[1:]
            t2.table_continuation = True
            t2.text = _render_table_text(t2.columns)
            _log.info(
                "Stripped duplicate header from side-by-side "
                "table continuation on page %d", t2.page_num)

    # Pass 2b: inline-grid tables (alternating-x lines in one block).
    # Runs before MuPDF native (Pass 5) which would mishandle them
    # via _maybe_transpose_label_table.  Requires MuPDF confirmation
    # to avoid false positives on code listings and prose.
    ig_tables, ig_used = _detect_inline_grid_tables(
        remaining, page_mupdf_tables=page_mupdf_tables)
    if ig_tables:
        table_sections.extend(ig_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in ig_used]

    # Pass 3a: split blocks with trailing horizontal-row headers
    # (MuPDF merges table headers into paragraph blocks when cells
    # are empty, e.g. P4012R0 §2.2 "Suggested Polls").
    thr_tables, remaining = _split_trailing_horizontal_rows(remaining)
    if thr_tables:
        table_sections.extend(thr_tables)

    # Pass 3: narrow horizontal-row tables (e.g. vote/poll grids)
    hr_tables, hr_used = _detect_horizontal_row_tables(remaining)
    if hr_tables:
        table_sections.extend(hr_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in hr_used]

    # Pass 4: geometric column grouping (borderless tables)
    geo_tables, geo_used = _detect_column_aligned_tables(
        remaining, two_column_pages=two_column_pages)
    if geo_tables:
        table_sections.extend(geo_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in geo_used]

    # Pass 5: MuPDF native find_tables() on remaining blocks.
    # Runs last so it only catches tables missed by all heuristic passes.
    filtered_mupdf_tables = _filter_overlapping_mupdf_tables(
        page_mupdf_tables or {}, table_sections)
    mupdf_tables, mupdf_used = _detect_mupdf_native_tables(
        remaining, filtered_mupdf_tables)
    if mupdf_tables:
        table_sections.extend(mupdf_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in mupdf_used]

    # Post-pass: spanning-header absorption.  After all passes,
    # check whether any remaining block sits directly above a
    # detected TABLE section and looks like a multi-column header
    # with fewer columns (e.g. "status quo | Section 6.3" above a
    # 3-col data table).  Pass-agnostic: works regardless of
    # which pass produced the table.
    absorbed: set[int] = set()
    for ts in table_sections:
        if not ts.columns or len(ts.columns) < 2:
            continue
        ncols = len(ts.columns[0])
        ts_y_top = min(ln.bbox[1] for ln in ts.lines) if ts.lines else 0
        # Get column x-positions from the first data row's spans.
        first_row = ts.columns[0]
        col_xs: list[float] = []
        for cell in first_row:
            xs = [sp.bbox[0] for sp in cell
                  if hasattr(sp, "bbox") and any(sp.bbox)]
            col_xs.append(min(xs) if xs else 999.0)
        if all(x >= 999 for x in col_xs):
            continue

        for bi, blk in enumerate(remaining):
            if bi in absorbed:
                continue
            if blk.page_num != ts.page_num:
                continue
            y_gap = ts_y_top - blk.bbox[3]
            if not (0 <= y_gap <= _SPANNING_HEADER_Y_GAP_MAX):
                continue
            blk_cols = _block_horizontal_row_relaxed(
                blk, _SPANNING_HEADER_MIN_COLS)
            if blk_cols is None or len(blk_cols) > ncols:
                continue
            # Map each header col to its nearest table col.
            mapping = [
                min(range(ncols),
                    key=lambda ci, hx=hx: abs(hx - col_xs[ci]))
                for hx in blk_cols
            ]
            dists = [
                min(abs(hx - col_xs[ci]) for ci in range(ncols))
                for hx in blk_cols
            ]
            if (len(set(mapping)) != len(mapping)
                    or not all(d < _SPANNING_HEADER_X_TOLERANCE
                               for d in dists)):
                continue
            # Absorb: prepend header row to table.
            header_row: list[list] = [[] for _ in range(ncols)]
            for li, ln in enumerate(blk.lines):
                if li < len(mapping):
                    header_row[mapping[li]] = list(ln.spans)
                ts.lines.insert(0, ln)
            ts.columns.insert(0, header_row)
            ts.text = _render_table_text(ts.columns)
            absorbed.add(bi)
            _log.debug(
                "Spanning header absorbed: page %d, "
                "%d-col header over %d-col table",
                blk.page_num, len(blk_cols), ncols)
            break  # one header per table

    if absorbed:
        remaining = [b for bi, b in enumerate(remaining)
                     if bi not in absorbed]

    if table_sections:
        kinds = {}
        for sec in table_sections:
            kinds[sec.table_kind] = kinds.get(sec.table_kind, 0) + 1
        _log.info("Table classification: %s", dict(sorted(kinds.items())))

    return table_sections, remaining


def exclude_table_regions(blocks: list[Block],
                          table_sections: list[Section]) -> list[Block]:
    """Remove blocks whose vertical midpoint falls within a detected table region.

    For multi-page tables, builds per-page y-ranges from individual
    lines so that content on continuation pages is also excluded.
    """
    if not table_sections:
        return blocks

    # Build per-table (page_num, y_min, y_max) ranges.  Each table
    # section gets its own range(s) so disjoint tables on the same
    # page do not merge into one mega-range that swallows prose between
    # them (which would skew compare_extractions similarity scores).
    table_ranges: list[tuple[int, float, float]] = []
    for sec in table_sections:
        if not sec.lines:
            continue
        sec_ranges: dict[int, tuple[float, float]] = {}
        for ln in sec.lines:
            pg = ln.page_num
            if pg in sec_ranges:
                old_min, old_max = sec_ranges[pg]
                sec_ranges[pg] = (
                    min(old_min, ln.bbox[1]),
                    max(old_max, ln.bbox[3]),
                )
            else:
                sec_ranges[pg] = (ln.bbox[1], ln.bbox[3])
        table_ranges.extend(
            (pg, y_min, y_max)
            for pg, (y_min, y_max) in sec_ranges.items()
        )

    result = []
    for block in blocks:
        in_table = False
        by = (block.bbox[1] + block.bbox[3]) / 2.0
        for pg, y_min, y_max in table_ranges:
            if (block.page_num == pg
                    and y_min - _TABLE_Y_OVERLAP_MARGIN <= by
                    <= y_max + _TABLE_Y_OVERLAP_MARGIN):
                in_table = True
                break
        # Bridge check: a spatial block that fully encloses 2+
        # disjoint tables is table-spanning content (code/prose
        # between tables that MuPDF merged into one block).
        if not in_table:
            contained = sum(
                1 for pg, y_min, y_max in table_ranges
                if block.page_num == pg
                and block.bbox[1] <= y_min
                and block.bbox[3] >= y_max
            )
            if contained >= 2:
                in_table = True
        if not in_table:
            result.append(block)
    return result
