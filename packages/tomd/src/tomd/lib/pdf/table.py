"""Table detection from MuPDF block/line structure.

Two-signal table detection:
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
"""

import logging
from collections import Counter, defaultdict

from .types import Block, Section, SectionKind, Confidence

_log = logging.getLogger(__name__)

_COLUMN_GAP_THRESHOLD = 50.0
_MIN_TABLE_ROWS = 2
_COLUMN_X_TOLERANCE = 10.0
# A strict right-edge tolerance used as a secondary signal in
# :func:`_columns_match` for right-aligned columns whose x-start
# varies row to row with cell text length but whose x-end is exactly
# fixed by the layout engine. Calibrated against P4003R1 page 8 col 4
# (max row-to-row x-end drift 0.004pt) and p0533r9 page 1 TOC col 1
# (min consecutive-row x-end drift 1.32pt - the latter is variable
# section-name text endings, NOT a stable right-aligned column).
# 1.0pt sits comfortably between the two populations.
_COLUMN_X_END_TOLERANCE = 1.0
_TABLE_Y_OVERLAP_MARGIN = 5.0

_COLUMN_X_BUCKET = 5.0    # bucket size for x-position clustering
_Y_BAND_HEIGHT   = 15.0   # bucket size for y-position clustering
_MIN_SHARED_YBANDS = 2    # x must co-occur with other columns in 2+ y-bands


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


def _block_column_positions(block: Block) -> tuple[list[float], list[float]] | None:
    """Return ``(x_starts, x_ends)`` for a columnar block, or None.

    A block is columnar if it has 2+ lines where every line after
    the first starts significantly to the right of the first line's
    x-start position. The x_ends are returned alongside x_starts so
    :func:`_columns_match` can fall back to right-edge alignment for
    right-aligned columns (numeric data, status indicators) whose
    x-start varies row to row with cell text length while their
    x-end stays fixed.
    """
    if len(block.lines) < 2:
        return None

    x_starts: list[float] = []
    x_ends: list[float] = []
    for line in block.lines:
        if not line.spans:
            return None
        x_starts.append(line.bbox[0])
        x_ends.append(line.bbox[2])

    for i in range(1, len(x_starts)):
        if x_starts[i] - x_starts[0] < _COLUMN_GAP_THRESHOLD:
            return None

    return x_starts, x_ends


def _columns_match(
    cols_a: tuple[list[float], list[float]],
    cols_b: tuple[list[float], list[float]],
) -> bool:
    """Check if two column position records represent the same table structure.

    Each input is ``(x_starts, x_ends)`` from
    :func:`_block_column_positions`. A column matches when EITHER:

    - its x-start aligns within :data:`_COLUMN_X_TOLERANCE` (the
      ordinary left-aligned-column case), OR
    - its x-end aligns within :data:`_COLUMN_X_END_TOLERANCE` (a much
      tighter threshold that only fires when the right edges are
      essentially exact, the characteristic shape of a right-aligned
      numeric column: x-start varies row to row with cell text length
      but x-end is fixed by the layout engine).

    The strict x-end threshold prevents spurious matches in
    table-of-contents-style layouts where section-name text endings
    happen to fall near each other by coincidence (drift ~1-2pt) but
    are not actually a right-aligned column.
    """
    starts_a, ends_a = cols_a
    starts_b, ends_b = cols_b
    if len(starts_a) != len(starts_b):
        return False
    for sa, sb, ea, eb in zip(starts_a, starts_b, ends_a, ends_b):
        start_aligned = abs(sa - sb) < _COLUMN_X_TOLERANCE
        end_aligned = abs(ea - eb) < _COLUMN_X_END_TOLERANCE
        if not (start_aligned or end_aligned):
            return False
    return True


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
        j = i + 1
        while j < len(blocks):
            next_cols = _block_column_positions(blocks[j])
            if next_cols is not None and _columns_match(cols, next_cols):
                table_blocks.append(blocks[j])
                j += 1
            elif (_is_column_aligned_orphan(blocks[j], column_xs)
                  and j + 1 < len(blocks)
                  and blocks[j].page_num == table_blocks[-1].page_num):
                # One-block lookahead: absorb only when the following block
                # confirms the table continues. The orphan must be on the same
                # page as the last table row to prevent cross-page false matches.
                # Putback is free - j stays here so the outer loop adds
                # blocks[j] to remaining if we break.
                peek_cols = _block_column_positions(blocks[j + 1])
                if (peek_cols is not None
                        and _columns_match(cols, peek_cols)
                        and blocks[j + 1].page_num == blocks[j].page_num):
                    table_blocks.append(blocks[j])
                    j += 1
                else:
                    break
            else:
                break

        if len(table_blocks) >= _MIN_TABLE_ROWS:
            col_starts, _col_ends = cols
            num_cols = len(col_starts)
            rows: list[list[list]] = []
            all_lines = []

            for blk in table_blocks:
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
                        key=lambda ci: abs(line_x - col_starts[ci]),
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

            text = "\n".join(
                " | ".join(
                    "".join(s.text for s in cell).strip()
                    for cell in row
                )
                for row in rows
            )

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
