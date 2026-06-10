# Copyright (c) 2026 C++ Alliance, Inc. (https://cppalliance.org)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Unit tests for new table detection passes added in PR #109."""

from tomd.lib.pdf.types import Span, Line, Block
from tomd.lib.pdf.table import (
    _gap_asymmetry_reject,
    _block_horizontal_row_relaxed,
)


def _line(text: str, x0: float, y0: float, x1: float, y1: float) -> Line:
    """Create a Line with explicit bbox."""
    return Line(
        spans=[Span(text=text, font_size=10.0)],
        bbox=(x0, y0, x1, y1),
    )


def _block_from_lines(lines: list[Line]) -> Block:
    """Create a Block from pre-built lines."""
    return Block(lines=lines, page_num=0)


class TestGapAsymmetryReject:
    """Tests for _gap_asymmetry_reject: filters false-positive table rows."""

    def test_fewer_than_3_lines_accepted(self):
        lines = [
            _line("A", 10, 100, 50, 110),
            _line("B", 100, 100, 150, 110),
        ]
        assert not _gap_asymmetry_reject(lines)

    def test_uniform_gaps_accepted(self):
        """Three cells with roughly equal spacing should pass."""
        lines = [
            _line("Col1", 10, 100, 60, 110),
            _line("Col2", 80, 100, 130, 110),
            _line("Col3", 150, 100, 200, 110),
        ]
        assert not _gap_asymmetry_reject(lines)

    def test_extreme_gap_asymmetry_rejected(self):
        """WG21 heading pattern: '4  General  [general]' with wildly uneven gaps."""
        lines = [
            _line("4", 72, 100, 82, 110),
            _line("General", 120, 100, 180, 110),
            _line("[general]", 400, 100, 470, 110),
        ]
        assert _gap_asymmetry_reject(lines)

    def test_moderate_gap_extreme_width_3cells_rejected(self):
        """Reference list pattern: tiny marker + long description.

        Gap between cell 1-2 = 5, gap between cell 2-3 = 20, ratio = 4.0 (> 3).
        Width of cell 1 = 23, cell 2 = 10, cell 3 = 380, ratio = 38 (> 10).
        """
        lines = [
            _line("(1.1)", 72, 100, 95, 110),
            _line("—", 100, 100, 110, 110),
            _line("IEC Electropedia: Very long description text", 130, 100, 510, 110),
        ]
        assert _gap_asymmetry_reject(lines)

    def test_4plus_cells_uniform_gaps_accepted(self):
        """4 cells with roughly uniform gaps should pass (real table)."""
        lines = [
            _line("A", 10, 100, 50, 110),
            _line("B", 70, 100, 110, 110),
            _line("C", 130, 100, 170, 110),
            _line("D", 190, 100, 230, 110),
        ]
        assert not _gap_asymmetry_reject(lines)

    def test_overlapping_gaps_not_counted(self):
        """When gaps are negative (overlapping), fewer than 2 positive gaps -> accept."""
        lines = [
            _line("A", 10, 100, 60, 110),
            _line("B", 50, 100, 110, 110),
            _line("C", 120, 100, 170, 110),
        ]
        assert not _gap_asymmetry_reject(lines)


class TestBlockHorizontalRowRelaxed:
    """Tests for _block_horizontal_row_relaxed: relaxed row detection."""

    def test_returns_none_below_min_cells(self):
        block = _block_from_lines([
            _line("Only one", 10, 100, 100, 110),
        ])
        assert _block_horizontal_row_relaxed(block, min_cells=2) is None

    def test_horizontal_row_detected(self):
        """Two cells on the same y-band should be detected."""
        block = _block_from_lines([
            _line("Col1", 10, 100, 60, 110),
            _line("Col2", 100, 100, 160, 110),
        ])
        result = _block_horizontal_row_relaxed(block, min_cells=2)
        assert result is not None
        assert len(result) == 2

    def test_vertical_stack_rejected(self):
        """Lines stacked vertically (different y) should be rejected."""
        block = _block_from_lines([
            _line("Line1", 10, 100, 100, 110),
            _line("Line2", 10, 150, 100, 160),
        ])
        assert _block_horizontal_row_relaxed(block, min_cells=2) is None

    def test_gap_asymmetry_rejects_heading_pattern(self):
        """Section heading pattern should be rejected by gap asymmetry guard."""
        block = _block_from_lines([
            _line("4", 72, 100, 82, 110),
            _line("General", 120, 100, 180, 110),
            _line("[general]", 400, 100, 470, 110),
        ])
        assert _block_horizontal_row_relaxed(block, min_cells=2) is None

    def test_wide_tolerance_non_overlapping(self):
        """Lines within wide Y tolerance, non-overlapping in x."""
        block = _block_from_lines([
            _line("Col1", 10, 100, 60, 110),
            _line("Col2", 100, 105, 160, 115),
        ])
        result = _block_horizontal_row_relaxed(block, min_cells=2)
        assert result is not None

    def test_overlapping_x_rejected(self):
        """Lines within wide Y tolerance but overlapping in x should be rejected."""
        block = _block_from_lines([
            _line("Overlap1", 10, 100, 120, 110),
            _line("Overlap2", 100, 105, 200, 115),
        ])
        assert _block_horizontal_row_relaxed(block, min_cells=2) is None
