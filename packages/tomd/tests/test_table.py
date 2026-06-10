"""Tests for lib.pdf.table."""

from conftest import make_span
from tomd.lib.pdf.types import Block, Line, Section, SectionKind, Confidence
from tomd.lib.pdf.table import (
    detect_tables, exclude_table_regions,
    _find_column_xs, _is_column_aligned_orphan,
)


def _col_line(text, x_start, y_start, page_num=0):
    """Create a Line positioned at a specific x-start for column layout."""
    span = make_span(text)
    return Line(
        spans=[span],
        bbox=(x_start, y_start, x_start + 100, y_start + 12),
        page_num=page_num,
    )


def _col_block(col_texts, col_xs, y_start, page_num=0):
    """Create a Block with lines at specific x-positions (one per column)."""
    lines = [
        _col_line(text, x, y_start, page_num)
        for text, x in zip(col_texts, col_xs)
    ]
    x0 = col_xs[0]
    x1 = col_xs[-1] + 100
    return Block(
        lines=lines,
        bbox=(x0, y_start, x1, y_start + 12),
        page_num=page_num,
    )


class TestDetectColumnar:
    def test_two_column_block_detected(self):
        """A block with 2 lines whose x-starts differ by >50 is columnar."""
        blocks = [
            _col_block(["Name", "Value"], [50, 300], y_start=100),
            _col_block(["Alpha", "100"], [50, 300], y_start=120),
        ]
        tables, remaining = detect_tables(blocks)
        assert len(tables) == 1
        assert tables[0].kind == SectionKind.TABLE

    def test_non_columnar_not_detected(self):
        """Lines all starting at the same x should not be columnar."""
        blocks = [
            _col_block(["Line one", "Line two"], [50, 60], y_start=100),
            _col_block(["Line three", "Line four"], [50, 60], y_start=120),
        ]
        tables, remaining = detect_tables(blocks)
        assert len(tables) == 0
        assert len(remaining) == 2


class TestDetectTables:
    def test_consecutive_columnar_blocks_form_table(self):
        """Two consecutive columnar blocks with matching columns form a table."""
        xs = [50, 300]
        blocks = [
            _col_block(["Header A", "Header B"], xs, y_start=100),
            _col_block(["Row 1 A", "Row 1 B"], xs, y_start=120),
            _col_block(["Row 2 A", "Row 2 B"], xs, y_start=140),
        ]
        tables, remaining = detect_tables(blocks)
        assert len(tables) == 1
        assert tables[0].confidence == Confidence.HIGH
        assert len(remaining) == 0

    def test_single_columnar_block_no_table(self):
        """A lone columnar block does not form a table (minimum 2 rows)."""
        blocks = [
            _col_block(["Only", "Row"], [50, 300], y_start=100),
        ]
        tables, remaining = detect_tables(blocks)
        assert len(tables) == 0
        assert len(remaining) == 1

    def test_mismatched_columns_separate(self):
        """Columnar blocks with different column counts don't group."""
        blocks = [
            _col_block(["A", "B"], [50, 300], y_start=100),
            _col_block(["C", "D", "E"], [50, 200, 400], y_start=120),
        ]
        tables, remaining = detect_tables(blocks)
        assert len(tables) == 0
        assert len(remaining) == 2

    def test_table_text_pipe_separated(self):
        """Table section text uses pipe-separated columns."""
        xs = [50, 300]
        blocks = [
            _col_block(["Name", "Score"], xs, y_start=100),
            _col_block(["Alice", "95"], xs, y_start=120),
        ]
        tables, _ = detect_tables(blocks)
        assert "Name | Score" in tables[0].text
        assert "Alice | 95" in tables[0].text

    def test_non_columnar_non_column_x_interleaved(self):
        """A non-columnar single-line block at a non-column x breaks the run."""
        xs = [50, 300]
        # Plain block at x=20 - clearly not a table column (far from x=50)
        plain_line = _col_line("Just a paragraph.", 20, 150)
        plain = Block(lines=[plain_line], bbox=(20, 150, 400, 162), page_num=0)
        blocks = [
            _col_block(["A", "B"], xs, y_start=100),
            plain,
            _col_block(["C", "D"], xs, y_start=200),
        ]
        tables, remaining = detect_tables(blocks)
        # Plain block is not at a column x, so it breaks the run
        assert any(b is plain for b in remaining)


def _single_line_block(text, x, y, page_num=0):
    """Single-line block - potential orphan."""
    line = _col_line(text, x, y, page_num)
    return Block(lines=[line], bbox=(x, y, x + 100, y + 12), page_num=page_num)


class TestFindColumnXs:
    def test_columnar_blocks_produce_column_xs(self):
        """Lines sharing y-bands with other x values are identified as columns."""
        xs = [60, 160, 270]
        blocks = [
            _col_block(["H1", "H2", "H3"], xs, y_start=100),
            _col_block(["A",  "B",  "C"],  xs, y_start=130),
            _col_block(["D",  "E",  "F"],  xs, y_start=160),
        ]
        col_xs = _find_column_xs(blocks)
        for x in xs:
            assert any(abs(x - cx) <= 5 for cx in col_xs), f"x={x} not in column_xs"

    def test_body_text_excluded_from_column_xs(self):
        """Prose lines alone in their y-band are not identified as columns."""
        # Each block has one line at x=57 - the body margin, alone per y-band
        blocks = [
            _single_line_block("First paragraph line.", 57, y)
            for y in range(100, 400, 15)
        ]
        col_xs = _find_column_xs(blocks)
        assert all(abs(57 - cx) > 5 for cx in col_xs), \
            "body margin should not be a column"

    def test_empty_blocks_return_empty(self):
        assert _find_column_xs([]) == frozenset()

    def test_single_column_block_not_a_table_column(self):
        """A block with all lines at the same x is not a column (no co-occurrence)."""
        blocks = [_single_line_block("text", 60, y) for y in range(100, 200, 15)]
        col_xs = _find_column_xs(blocks)
        assert all(abs(60 - cx) > 5 for cx in col_xs)


class TestIsColumnAlignedOrphan:
    def test_single_line_at_column_x_is_orphan(self):
        block = _single_line_block("std::ranges::fold_left /", 60, 150)
        assert _is_column_aligned_orphan(block, frozenset({60.0, 160.0, 270.0}))

    def test_multi_line_block_not_orphan(self):
        block = _col_block(["A", "B"], [60, 160], y_start=150)
        assert not _is_column_aligned_orphan(block, frozenset({60.0, 160.0}))

    def test_single_line_not_at_column_x_not_orphan(self):
        # x=20 is clearly not within 5pt of any column (100.0, 200.0)
        block = _single_line_block("prose", 20, 150)
        assert not _is_column_aligned_orphan(block, frozenset({100.0, 200.0}))


class TestOrphanAbsorption:
    def _table_blocks(self):
        """Three 5-column table rows with an orphan before the second row."""
        xs = [60, 160, 270, 360, 450]
        header = _col_block(["Facility", "Spec", "Order", "Paren", "Unique"], xs, y_start=100)
        row1   = _col_block(["accumulate", "seq fold", "fixed", "fixed", "yes"],  xs, y_start=130)
        orphan = _single_line_block("fold_left /", 60, 160)  # wrapped first line
        row2   = _col_block(["fold_right", "seq fold", "fixed", "fixed", "yes"], xs, y_start=175)
        row3   = _col_block(["inclusive_scan", "gncs", "fixed", "unspec", "no"], xs, y_start=205)
        return [header, row1, orphan, row2, row3]

    def test_orphan_absorbed_all_blocks_form_one_table(self):
        tables, remaining = detect_tables(self._table_blocks())
        assert len(tables) == 1
        assert len(remaining) == 0

    def test_orphan_merged_into_next_row_first_cell(self):
        tables, _ = detect_tables(self._table_blocks())
        # The row with the orphan + fold_right should be one row
        cols = tables[0].columns
        # Find the merged row: first cell should contain both "fold_left /" and "fold_right"
        merged_row = next(
            row for row in cols
            if any("fold_left" in s.text for s in row[0])
        )
        first_cell_text = "".join(s.text for s in merged_row[0])
        assert "fold_left" in first_cell_text
        assert "fold_right" in first_cell_text

    def test_orphan_putback_when_next_block_not_table(self):
        """Orphan followed by a prose block: putback to remaining, table stops."""
        xs = [60, 160, 270]
        header   = _col_block(["A", "B", "C"], xs, y_start=100)
        row1     = _col_block(["1", "2", "3"], xs, y_start=130)
        orphan   = _single_line_block("orphan text", 60, 160)
        prose    = _single_line_block("A paragraph follows.", 57, 175)  # non-table

        tables, remaining = detect_tables([header, row1, orphan, prose])
        assert len(tables) == 1
        # The orphan and prose should both be in remaining
        remaining_texts = [b.lines[0].text if b.lines else "" for b in remaining]
        assert any("orphan" in t for t in remaining_texts)
        assert any("paragraph" in t for t in remaining_texts)

    def test_multiline_non_columnar_not_absorbed(self):
        """A 2-line non-columnar block between table rows is NOT absorbed."""
        xs = [60, 160]
        header   = _col_block(["A", "B"], xs, y_start=100)
        row1     = _col_block(["1", "2"], xs, y_start=130)
        two_line = _col_block(["prose line 1", "prose line 2"], [60, 70], y_start=155)
        row2     = _col_block(["3", "4"], xs, y_start=185)

        tables, remaining = detect_tables([header, row1, two_line, row2])
        # two_line has 2 lines (not a 1-line orphan) and columns don't match
        assert any(b is two_line for b in remaining), "2-line block must stay in remaining"

    def test_existing_table_detection_unchanged(self):
        """Clean consecutive table rows still work exactly as before."""
        xs = [60, 160, 270]
        blocks = [
            _col_block(["H1", "H2", "H3"], xs, y_start=100),
            _col_block(["A",  "B",  "C"],  xs, y_start=130),
            _col_block(["D",  "E",  "F"],  xs, y_start=160),
        ]
        tables, remaining = detect_tables(blocks)
        assert len(tables) == 1
        assert len(remaining) == 0
        assert len(tables[0].columns) == 3


class TestExcludeTableRegions:
    def test_blocks_inside_table_removed(self):
        """Blocks whose y-center falls within a table range are excluded."""
        table_line = _col_line("x", 50, 100)
        table_line2 = _col_line("y", 50, 140)
        table_sec = Section(
            kind=SectionKind.TABLE,
            text="table",
            confidence=Confidence.HIGH,
            lines=[table_line, table_line2],
            page_num=0,
        )

        inside = Block(lines=[], bbox=(50, 110, 400, 130), page_num=0)
        outside = Block(lines=[], bbox=(50, 300, 400, 320), page_num=0)

        result = exclude_table_regions([inside, outside], [table_sec])
        assert len(result) == 1
        assert result[0] is outside

    def test_different_page_not_excluded(self):
        """Blocks on a different page from the table are kept."""
        table_line = _col_line("x", 50, 100, page_num=0)
        table_sec = Section(
            kind=SectionKind.TABLE,
            text="table",
            confidence=Confidence.HIGH,
            lines=[table_line],
            page_num=0,
        )

        block = Block(lines=[], bbox=(50, 100, 400, 112), page_num=1)
        result = exclude_table_regions([block], [table_sec])
        assert len(result) == 1

    def test_empty_tables_returns_all(self):
        """No table sections means all blocks are returned."""
        blocks = [
            Block(lines=[], bbox=(50, 100, 400, 112), page_num=0),
            Block(lines=[], bbox=(50, 200, 400, 212), page_num=0),
        ]
        result = exclude_table_regions(blocks, [])
        assert len(result) == 2


# ---- Right-aligned-column matching (Fix B for P4003R1 page 8) -------------


def _right_aligned_line(text, x_end, y_start, line_width, page_num=0):
    """Create a Line whose right edge sits at x_end (variable x_start by width)."""
    x_start = x_end - line_width
    span = make_span(text)
    return Line(
        spans=[span],
        bbox=(x_start, y_start, x_end, y_start + 12),
        page_num=page_num,
    )


def _right_aligned_row_block(col1_text, col1_xstart, col4_text, col4_xend,
                              col4_width, y_start, page_num=0):
    """A 4-line block with col 1 left-aligned at col1_xstart and col 4
    right-aligned at col4_xend. Cols 2, 3 use placeholder positions."""
    line1 = _col_line(col1_text, col1_xstart, y_start, page_num)
    line2 = _col_line("col2", 200, y_start, page_num)
    line3 = _col_line("col3", 350, y_start, page_num)
    line4 = _right_aligned_line(col4_text, col4_xend, y_start, col4_width, page_num)
    return Block(
        lines=[line1, line2, line3, line4],
        bbox=(col1_xstart, y_start, col4_xend, y_start + 12),
        page_num=page_num,
    )


class TestRightAlignedColumnMatch:
    """Fix B (calibrated against P4003R1 page 8): a column whose x-start
    varies row-to-row by more than _COLUMN_X_TOLERANCE (10pt) but whose
    x-end is essentially exact across all rows should still detect as
    the same table column. The bug class: numeric columns where short
    values like "-" sit at higher x-start than longer values like
    "3.10x", but all right-align to the same x-end."""

    def test_right_aligned_column_with_variable_xstart_detects_table(self):
        # 3 rows; col 4 right-aligned to x_end=500. Cell widths vary:
        # row 1 width 30 -> x_start 470; row 2 width 50 -> 450; row 3
        # width 10 -> 490. Variance of x_start is 40pt - well beyond
        # _COLUMN_X_TOLERANCE. Without Fix B, the x-end signal carries
        # the column match.
        blocks = [
            _right_aligned_row_block("Platform", 50, "3.10x", 500.0, 30, 100),
            _right_aligned_row_block("MSVC",     50, "1.55x", 500.0, 50, 120),
            _right_aligned_row_block("Apple",    50, "-",     500.0, 10, 140),
        ]
        tables, _remaining = detect_tables(blocks)
        assert len(tables) == 1
        assert tables[0].kind == SectionKind.TABLE
        # All three rows captured.
        assert tables[0].text.count("\n") + 1 == 3

    def test_xend_drift_outside_strict_tolerance_does_not_match(self):
        """The right-aligned shortcut is gated by a strict <1.0pt
        tolerance on x-end. Variable-text columns (e.g. TOC section
        names) drift by 1+ pt at the right edge, which doesn't
        trigger the fallback. Without this guard the TOC of
        p0533r9 would be over-merged into one table absorbing all
        section names; the regression manifested before the strict
        threshold landed."""
        # Three blocks with very different x-starts AND x-ends that
        # drift more than 1pt apart. The strict end tolerance bars
        # the fallback; the loose start tolerance is already
        # blown -> no match.
        line1 = _col_line("section name A", 60.0, 100)
        line1 = Line(spans=line1.spans, bbox=(60.0, 100, 152.0, 112), page_num=0)
        line2 = _col_line("page", 290.0, 100)
        b1 = Block(lines=[line1, line2], bbox=(60, 100, 390, 112), page_num=0)

        line1 = _col_line("section name B", 80.0, 200)
        line1 = Line(spans=line1.spans, bbox=(80.0, 200, 158.0, 212), page_num=0)
        line2 = _col_line("page", 290.0, 200)
        b2 = Block(lines=[line1, line2], bbox=(80, 200, 390, 212), page_num=0)
        # Col 1 starts diff = 20 > tolerance, ends diff = 6 >> 1.0 strict.
        # No match expected.
        tables, _remaining = detect_tables([b1, b2])
        assert len(tables) == 0
