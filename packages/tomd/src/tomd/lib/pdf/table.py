"""Table detection from MuPDF block/line structure.

Three detection passes:

Pass 1 (inline-column tables):
  Signal 1 (block structure): a block is columnar when it has 2+ lines whose
    x-starts have gaps > _COLUMN_GAP_THRESHOLD. Consecutive matching-column
    blocks form a table run.
  Signal 2 (geometric column profile): x positions that co-occur with other
    x positions in the same y-band across 2+ rows are confirmed table columns.
    Body text is always alone in its y-band and never qualifies.

  Orphan absorption: single-line blocks whose x0 matches a confirmed column are
  "orphans" - the first physical line of a wrapped table cell. They are absorbed
  into the table run when the block following them is a confirmed table row
  (one-block lookahead). Absorbed orphans are merged into the next row's first
  cell so multi-line cells produce a single cell string.

  Known gap: absorption is same-page only. A wrapped cell whose continuation
  line is the first block on the next page is not absorbed - the table stops
  at the last same-page row and the orphan appears in an uncertain region.

Pass 2 (side-by-side block tables):
  Detects tables where each cell is a separate MuPDF block placed beside
  other blocks at a different x-position (e.g. Tony Tables with multi-line
  code cells). A columnar header block signals a table start; subsequent
  blocks that align with confirmed column x-positions and overlap vertically
  form table rows. A new row begins when a new leftmost-column block appears.

Pass 3 (geometric column grouping):
  Catches tables missed by Passes 1-2 where MuPDF distributes columns across
  separate single-column blocks (e.g. schedule tables with dates in left-column
  blocks and descriptions in right-column blocks, or multi-column comparison
  tables where each cell is its own block).  Uses the geometric column profile
  from _find_column_xs to identify confirmed column positions, then groups
  remaining blocks into rows by y-overlap. Requires 2+ columns and 2+ rows.
"""

import logging
import re
from collections import Counter, defaultdict

from .types import Block, Section, SectionKind, Confidence

_log = logging.getLogger(__name__)

_COLUMN_GAP_THRESHOLD = 50.0
_MIN_TABLE_ROWS = 2
_COLUMN_X_TOLERANCE = 10.0
_TABLE_Y_OVERLAP_MARGIN = 5.0

_COLUMN_X_BUCKET = 5.0    # bucket size for x-position clustering
_Y_BAND_HEIGHT   = 15.0   # bucket size for y-position clustering
_MIN_SHARED_YBANDS = 2    # x must co-occur with other columns in 2+ y-bands

# Side-by-side table constants
_SBS_MAX_SCAN_GAP = 30.0  # max y-gap before stopping body scan

# Guard: bare section number on line 0 of a 2-line block.
# Prevents misclassifying heading blocks (large bold number + right-aligned
# title) as tables. Kretz-style LaTeX papers (P3948, P3844, P4012) produce
# blocks where the section number sits at x=73 and the ALL-CAPS title at
# x=440+, a gap of ~370pt that far exceeds _COLUMN_GAP_THRESHOLD.
_BARE_HEADING_NUM_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*|[A-Z]+|[IVXLCDM]+)\.?\s*$"
)
_HEADING_NUM_MAX_WORDS = 8


def _render_table_text(rows: list[list[list]]) -> str:
    """Render table rows (list of cells, each cell a list of Spans) to pipe-delimited text."""
    return "\n".join(
        " | ".join(
            "".join(s.text for s in cell).strip()
            for cell in row
        )
        for row in rows
    )


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

    x_starts = []
    for line in block.lines:
        if not line.spans:
            return None
        x_starts.append(line.bbox[0])

    for i in range(1, len(x_starts)):
        if x_starts[i] - x_starts[0] < _COLUMN_GAP_THRESHOLD:
            return None

    return x_starts


def _columns_match(cols_a: list[float], cols_b: list[float]) -> bool:
    """Check if two column position lists represent the same table structure."""
    if len(cols_a) != len(cols_b):
        return False
    return all(abs(a - b) < _COLUMN_X_TOLERANCE for a, b in zip(cols_a, cols_b))


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


def _detect_side_by_side_tables(
    blocks: list[Block],
) -> tuple[list[Section], set[int]]:
    """Detect tables where each cell is a separate side-by-side block.

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

        # Group candidates into rows.
        # A new row starts when a block in column 0 (leftmost) appears
        # and column 0 was already populated in the current row.
        rows: list[list[tuple[int, Block]]] = []
        current_row: list[tuple[int, Block]] = []
        has_col0 = False
        last_bottom = h_bottom

        for idx, blk in body_candidates:
            col = _nearest_column(blk.bbox[0], col_xs)
            if blk.bbox[1] - last_bottom > _SBS_MAX_SCAN_GAP:
                if current_row:
                    rows.append(current_row)
                break
            if col == 0:
                if has_col0:
                    rows.append(current_row)
                    current_row = [(idx, blk)]
                    has_col0 = True
                else:
                    current_row.append((idx, blk))
                    has_col0 = True
            else:
                current_row.append((idx, blk))
            last_bottom = max(last_bottom, blk.bbox[3])

        if current_row and current_row not in rows:
            rows.append(current_row)

        # Validate: each row must have blocks in 2+ columns
        valid_rows: list[list[tuple[int, Block]]] = []
        for row in rows:
            row_cols = set(_nearest_column(b.bbox[0], col_xs) for _, b in row)
            if len(row_cols) >= 2:
                valid_rows.append(row)
            else:
                break

        if len(valid_rows) < _MIN_TABLE_ROWS:
            i += 1
            continue

        num_cols = len(col_xs)

        # Build header row from the columnar header block
        header_cells: list[list] = []
        for ln in header.lines[:num_cols]:
            header_cells.append(list(ln.spans))
        while len(header_cells) < num_cols:
            header_cells.append([])

        all_rows_data: list[list[list]] = [header_cells]
        all_lines = list(header.lines)

        for row in valid_rows:
            col_spans: dict[int, list] = defaultdict(list)
            for _, blk in row:
                ci = _nearest_column(blk.bbox[0], col_xs)
                for ln in blk.lines:
                    col_spans[ci].extend(ln.spans)
                    all_lines.append(ln)
            table_row = [col_spans.get(ci, []) for ci in range(num_cols)]
            all_rows_data.append(table_row)

        text = _render_table_text(all_rows_data)

        table_sections.append(Section(
            kind=SectionKind.TABLE,
            text=text,
            confidence=Confidence.HIGH,
            lines=all_lines,
            page_num=page,
            columns=all_rows_data,
        ))
        _log.debug("Side-by-side table: %d rows x %d cols on page %d",
                    len(all_rows_data), num_cols, page)

        used.add(i)
        for row in valid_rows:
            for idx, _ in row:
                used.add(idx)

        i = j

    return table_sections, used


_GEO_Y_OVERLAP_MIN = 0.3     # fraction of smaller block's height that must overlap
_GEO_MAX_ROW_GAP = 25.0      # max y-gap between consecutive row bands
_GEO_MIN_BLOCK_WORD_LIMIT = 40  # blocks with more words are likely prose, not cells



def _y_overlap(a_y0: float, a_y1: float, b_y0: float, b_y1: float) -> bool:
    """True if two vertical ranges overlap significantly."""
    overlap = min(a_y1, b_y1) - max(a_y0, b_y0)
    if overlap <= 0:
        return False
    smaller = min(a_y1 - a_y0, b_y1 - b_y0)
    if smaller <= 0:
        return False
    return overlap / smaller >= _GEO_Y_OVERLAP_MIN


def _line_column(line, col_xs_sorted: list[float]) -> int | None:
    """Return the column index for a line, or None if it does not align."""
    if not line.spans or not line.text.strip():
        return None
    x0 = line.bbox[0]
    for ci, cx in enumerate(col_xs_sorted):
        if abs(x0 - cx) <= _COLUMN_X_BUCKET:
            return ci
    return None


def _detect_geometric_tables(
    blocks: list[Block],
    column_xs: frozenset[float],
) -> tuple[list[Section], set[int]]:
    """Detect tables by grouping column-aligned blocks into rows via y-overlap.

    Two phases:
    Phase A: block-level grouping finds table runs from single-column blocks
             at confirmed column positions.
    Phase B: expansion absorbs neighboring mixed-column blocks (those with
             lines at 2+ table column positions) by splitting at line level.

    Returns (table_sections, used_block_indices).
    """
    table_sections: list[Section] = []
    used: set[int] = set()

    if len(column_xs) < 2:
        return table_sections, used

    col_xs_sorted = sorted(column_xs)

    # Group blocks by page
    page_blocks: dict[int, list[tuple[int, Block]]] = defaultdict(list)
    for idx, block in enumerate(blocks):
        page_blocks[block.page_num].append((idx, block))

    for page_num in sorted(page_blocks):
        pblocks = page_blocks[page_num]

        # --- Phase A: block-level detection ---
        aligned: list[tuple[int, Block, int]] = []
        for idx, block in pblocks:
            if not block.lines or not block.lines[0].spans:
                continue
            if not block.lines[0].text.strip():
                continue
            word_count = sum(len(ln.text.split()) for ln in block.lines)
            if word_count > _GEO_MIN_BLOCK_WORD_LIMIT:
                continue
            x0 = block.lines[0].bbox[0]
            col_idx = _line_column(block.lines[0], col_xs_sorted)
            if col_idx is not None:
                aligned.append((idx, block, col_idx))

        if len(aligned) < 2:
            continue

        # Group aligned blocks into rows by y-overlap.
        rows: list[list[tuple[int, Block, int]]] = []
        for entry in aligned:
            _, blk, _ = entry
            by0, by1 = blk.bbox[1], blk.bbox[3]
            placed = False
            for row in rows:
                for _, rblk, _ in row:
                    if _y_overlap(by0, by1, rblk.bbox[1], rblk.bbox[3]):
                        row.append(entry)
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                rows.append([entry])

        rows.sort(key=lambda r: min(b.bbox[1] for _, b, _ in r))

        multi_col_rows: list[list[tuple[int, Block, int]]] = []
        for row in rows:
            cols_in_row = set(ci for _, _, ci in row)
            if len(cols_in_row) >= 2:
                multi_col_rows.append(row)

        if len(multi_col_rows) < _MIN_TABLE_ROWS:
            continue

        # Find contiguous runs of multi-column rows (no large y-gap)
        runs: list[list[list[tuple[int, Block, int]]]] = []
        current_run: list[list[tuple[int, Block, int]]] = [multi_col_rows[0]]
        for ri in range(1, len(multi_col_rows)):
            prev_row = current_run[-1]
            curr_row = multi_col_rows[ri]
            prev_bottom = max(b.bbox[3] for _, b, _ in prev_row)
            curr_top = min(b.bbox[1] for _, b, _ in curr_row)
            if curr_top - prev_bottom > _GEO_MAX_ROW_GAP:
                if len(current_run) >= _MIN_TABLE_ROWS:
                    runs.append(current_run)
                current_run = [curr_row]
            else:
                current_run.append(curr_row)
        if len(current_run) >= _MIN_TABLE_ROWS:
            runs.append(current_run)

        # --- Phase B: expand runs by absorbing adjacent mixed blocks ---
        _expand_runs_from_mixed_blocks(
            runs, pblocks, col_xs_sorted, used)

        # Build table sections from runs
        for run in runs:
            run_col_indices = set()
            for row in run:
                for _, _, ci in row:
                    run_col_indices.add(ci)
            run_cols = sorted(run_col_indices)
            num_cols = len(run_cols)
            col_remap = {ci: i for i, ci in enumerate(run_cols)}

            all_rows_data: list[list[list]] = []
            all_lines = []

            for row in run:
                col_spans: dict[int, list] = defaultdict(list)
                for _, blk, ci in row:
                    mapped = col_remap[ci]
                    for ln in blk.lines:
                        col_spans[mapped].extend(ln.spans)
                        all_lines.append(ln)
                table_row = [col_spans.get(ci, []) for ci in range(num_cols)]
                all_rows_data.append(table_row)

            text = _render_table_text(all_rows_data)

            table_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=page_num,
                columns=all_rows_data,
            ))
            _log.debug("Geometric table: %d rows x %d cols on page %d",
                        len(all_rows_data), num_cols, page_num)

            for row in run:
                for idx, _, _ in row:
                    used.add(idx)

    return table_sections, used


_GEO_EXPAND_Y_MARGIN = 50.0  # max y-distance from table edge to absorb a block


def _expand_runs_from_mixed_blocks(
    runs: list[list[list[tuple[int, Block, int]]]],
    pblocks: list[tuple[int, Block]],
    col_xs_sorted: list[float],
    already_used: set[int],
) -> None:
    """Expand table runs by absorbing lines from adjacent mixed-column blocks.

    A mixed-column block has lines at 2+ of the table's column positions
    but was not detected as a single-column table block because its first
    line maps to one column while other lines map to another.

    This function groups the block's lines into rows (via y-overlap within
    the block), creates a virtual Block per cell, and inserts complete
    multi-column rows into the run.

    Iterates until no new blocks are absorbed, so a newly absorbed block
    can extend the table's y-range and pull in further neighbors.

    Mutates ``runs`` in place.
    """
    for run in runs:
        if not run:
            continue

        changed = True
        while changed:
            changed = False

            table_cols = set()
            for row in run:
                for _, _, ci in row:
                    table_cols.add(ci)
            if len(table_cols) < 2:
                break

            run_y_min = min(b.bbox[1] for row in run for _, b, _ in row)
            run_y_max = max(b.bbox[3] for row in run for _, b, _ in row)
            run_block_ids = set(idx for row in run for idx, _, _ in row)

            for blk_idx, block in pblocks:
                if blk_idx in run_block_ids or blk_idx in already_used:
                    continue
                if not block.lines:
                    continue
                word_count = sum(len(ln.text.split()) for ln in block.lines)
                if word_count > _GEO_MIN_BLOCK_WORD_LIMIT:
                    continue

                blk_y_mid = (block.bbox[1] + block.bbox[3]) / 2.0
                if (blk_y_mid < run_y_min - _GEO_EXPAND_Y_MARGIN
                        or blk_y_mid > run_y_max + _GEO_EXPAND_Y_MARGIN):
                    continue

                # Classify each line by column
                tagged: list[tuple[int, int]] = []
                for li, line in enumerate(block.lines):
                    ci = _line_column(line, col_xs_sorted)
                    if ci is not None and ci in table_cols:
                        tagged.append((li, ci))

                unique_cols = set(ci for _, ci in tagged)
                if len(unique_cols) < 2:
                    # Single-column block: absorb if it sits inside the
                    # table's y-range (gap row with one empty cell).
                    if (tagged
                            and block.bbox[1] >= run_y_min - _COLUMN_X_BUCKET
                            and block.bbox[3] <= run_y_max + _COLUMN_X_BUCKET):
                        for li, ci in tagged:
                            ln = block.lines[li]
                            vb = Block(
                                lines=[ln], page_num=block.page_num,
                                bbox=ln.bbox,
                            )
                            _insert_entry_into_run(run, (blk_idx, vb, ci))
                        run_block_ids.add(blk_idx)
                        already_used.add(blk_idx)
                        changed = True
                    continue

                # Group tagged lines into rows by y-overlap within the block
                line_rows: list[list[tuple[int, int]]] = []
                for entry in tagged:
                    li, ci = entry
                    ln = block.lines[li]
                    ly0, ly1 = ln.bbox[1], ln.bbox[3]
                    placed = False
                    for lr in line_rows:
                        for eli, _ in lr:
                            eln = block.lines[eli]
                            if _y_overlap(ly0, ly1, eln.bbox[1], eln.bbox[3]):
                                lr.append(entry)
                                placed = True
                                break
                        if placed:
                            break
                    if not placed:
                        line_rows.append([entry])

                # Build virtual rows (only keep rows with 2+ columns)
                for lr in line_rows:
                    lr_cols = set(ci for _, ci in lr)
                    if len(lr_cols) < 2:
                        # Single-column: try to attach to existing run row
                        if len(lr) == 1:
                            li, ci = lr[0]
                            ln = block.lines[li]
                            vb = Block(
                                lines=[ln], page_num=block.page_num,
                                bbox=ln.bbox,
                            )
                            _insert_entry_into_run(
                                run, (blk_idx, vb, ci))
                        continue

                    # Multi-column row: create virtual blocks per column
                    col_lines: dict[int, list] = defaultdict(list)
                    for li, ci in lr:
                        col_lines[ci].append(block.lines[li])

                    new_row_entries: list[tuple[int, Block, int]] = []
                    for ci, lines in col_lines.items():
                        vb = Block(
                            lines=lines,
                            bbox=(
                                min(ln.bbox[0] for ln in lines),
                                min(ln.bbox[1] for ln in lines),
                                max(ln.bbox[2] for ln in lines),
                                max(ln.bbox[3] for ln in lines),
                            ),
                            page_num=block.page_num,
                        )
                        new_row_entries.append((blk_idx, vb, ci))

                    _insert_row_into_run(run, new_row_entries)

                run_block_ids.add(blk_idx)
                already_used.add(blk_idx)
                changed = True


def _insert_entry_into_run(
    run: list[list[tuple[int, Block, int]]],
    entry: tuple[int, Block, int],
) -> None:
    """Insert a single virtual entry into an existing row or create a new one."""
    _, vblk, _ = entry
    vy0, vy1 = vblk.bbox[1], vblk.bbox[3]
    for row in run:
        for _, rblk, _ in row:
            if _y_overlap(vy0, vy1, rblk.bbox[1], rblk.bbox[3]):
                row.append(entry)
                return
    # No overlap: create new row in sorted position
    new_row = [entry]
    insert_at = len(run)
    for ri, row in enumerate(run):
        row_top = min(b.bbox[1] for _, b, _ in row)
        if vy0 < row_top:
            insert_at = ri
            break
    run.insert(insert_at, new_row)


def _insert_row_into_run(
    run: list[list[tuple[int, Block, int]]],
    entries: list[tuple[int, Block, int]],
) -> None:
    """Insert a multi-column row into the run, merging with an existing row
    if y-overlap exists, otherwise creating a new row."""
    if not entries:
        return
    # Use the first entry to find y-position
    _, ref_blk, _ = entries[0]
    vy0, vy1 = ref_blk.bbox[1], ref_blk.bbox[3]
    for row in run:
        for _, rblk, _ in row:
            if _y_overlap(vy0, vy1, rblk.bbox[1], rblk.bbox[3]):
                row.extend(entries)
                return
    # No overlap: insert new row in sorted position
    new_row = list(entries)
    insert_at = len(run)
    for ri, row in enumerate(run):
        row_top = min(b.bbox[1] for _, b, _ in row)
        if vy0 < row_top:
            insert_at = ri
            break
    run.insert(insert_at, new_row)


_HORIZONTAL_ROW_Y_TOLERANCE = 3.0
_HORIZONTAL_ROW_MIN_CELLS = 3


def _block_horizontal_row(block: Block) -> list[float] | None:
    """Detect a block whose lines sit side-by-side at the same y-level.

    Returns x-start positions when the block has 3+ lines sharing
    the same y-band (within _HORIZONTAL_ROW_Y_TOLERANCE). This
    catches narrow poll/vote tables where column gaps are too small
    for _block_column_positions.
    """
    if len(block.lines) < _HORIZONTAL_ROW_MIN_CELLS:
        return None
    y_centers = [(ln.bbox[1] + ln.bbox[3]) / 2 for ln in block.lines]
    if max(y_centers) - min(y_centers) > _HORIZONTAL_ROW_Y_TOLERANCE:
        return None
    return [ln.bbox[0] for ln in block.lines]


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
        ncols = len(cols)
        j = i + 1
        while j < len(blocks):
            nxt_cols = _block_horizontal_row(blocks[j])
            if (nxt_cols is not None
                    and len(nxt_cols) == ncols
                    and blocks[j].page_num == blocks[i].page_num):
                run.append(j)
                j += 1
            else:
                break

        if len(run) >= _MIN_TABLE_ROWS:
            rows: list[list[list]] = []
            all_lines = []
            for idx in run:
                blk = blocks[idx]
                row = []
                for ln in blk.lines[:ncols]:
                    row.append(list(ln.spans))
                    all_lines.append(ln)
                while len(row) < ncols:
                    row.append([])
                rows.append(row)

            text = _render_table_text(rows)

            table_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=blocks[run[0]].page_num,
                columns=rows,
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


def detect_tables(blocks: list[Block]) -> tuple[list[Section], list[Block]]:
    """Detect table regions from MuPDF block structure.

    Returns (table_sections, remaining_blocks).
    Table sections have kind=TABLE with high confidence.
    Remaining blocks are the non-table blocks for normal processing.
    """
    column_xs = _find_column_xs(blocks)  # geometric second signal

    table_sections: list[Section] = []
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
        j = i + 1
        while j < len(blocks):
            next_cols = _block_column_positions(blocks[j])
            if next_cols is not None and _columns_match(ref_cols, next_cols):
                table_blocks.append(blocks[j])
                j += 1
            elif (next_cols is not None
                  and _columns_count_match(
                      ref_cols, next_cols,
                      table_blocks[-1].bbox[3], blocks[j].bbox[1],
                      blocks[j].page_num == table_blocks[-1].page_num)):
                # Relaxed match: same column count, close proximity.
                # Adopt new positions as reference (data rows are more
                # consistent than centered headers).
                table_blocks.append(blocks[j])
                ref_cols = next_cols
                j += 1
            elif (_is_column_aligned_orphan(blocks[j], column_xs)
                  and j + 1 < len(blocks)
                  and blocks[j].page_num == table_blocks[-1].page_num):
                peek_cols = _block_column_positions(blocks[j + 1])
                if (peek_cols is not None
                        and _columns_match(ref_cols, peek_cols)
                        and blocks[j + 1].page_num == blocks[j].page_num):
                    table_blocks.append(blocks[j])
                    j += 1
                else:
                    break
            else:
                break

        if len(table_blocks) >= _MIN_TABLE_ROWS:
            num_cols = len(ref_cols)
            rows: list[list[list]] = []
            all_lines = []

            for blk in table_blocks:
                blk_cols = _block_column_positions(blk) or ref_cols
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
                    row[best_col].extend(line.spans)
                rows.append(row)

            # Merge orphan partial rows (single populated first cell) into the
            # following row so wrapped cell text becomes one cell string.
            merged: list[list[list]] = []
            k = 0
            while k < len(rows):
                row = rows[k]
                if (k + 1 < len(rows)
                        and bool(row[0])
                        and all(not cell for cell in row[1:])
                        and bool(rows[k + 1][0])):
                    merged.append([row[0] + rows[k + 1][0]] + rows[k + 1][1:])
                    k += 2
                else:
                    merged.append(row)
                    k += 1
            rows = merged

            text = _render_table_text(rows)

            table_sections.append(Section(
                kind=SectionKind.TABLE,
                text=text,
                confidence=Confidence.HIGH,
                lines=all_lines,
                page_num=table_blocks[0].page_num,
                columns=rows,
            ))
            _log.debug("Table detected: %d rows x %d cols on page %d",
                        len(rows), num_cols, table_blocks[0].page_num)
            i = j
        else:
            remaining.append(blocks[i])
            i += 1

    # Pass 2: side-by-side block tables on remaining blocks
    sbs_tables, sbs_used = _detect_side_by_side_tables(remaining)
    if sbs_tables:
        table_sections.extend(sbs_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in sbs_used]

    # Pass 3: geometric column grouping on remaining blocks
    geo_tables, geo_used = _detect_geometric_tables(remaining, column_xs)
    if geo_tables:
        table_sections.extend(geo_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in geo_used]

    # Pass 4: narrow horizontal-row tables (e.g. vote/poll grids)
    hr_tables, hr_used = _detect_horizontal_row_tables(remaining)
    if hr_tables:
        table_sections.extend(hr_tables)
        remaining = [b for idx, b in enumerate(remaining)
                     if idx not in hr_used]

    return table_sections, remaining


def exclude_table_regions(blocks: list[Block],
                          table_sections: list[Section]) -> list[Block]:
    """Remove blocks whose vertical midpoint falls within a detected table region."""
    if not table_sections:
        return blocks

    table_ranges = []
    for sec in table_sections:
        if not sec.lines:
            continue
        y_min = min(ln.bbox[1] for ln in sec.lines)
        y_max = max(ln.bbox[3] for ln in sec.lines)
        table_ranges.append((sec.page_num, y_min, y_max))

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
        if not in_table:
            result.append(block)
    return result
