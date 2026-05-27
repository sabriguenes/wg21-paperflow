#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Build the synth_noise_no_diagram.pdf golden fixture.

Run from the repo root:

    uv run --package tomd python \\
        packages/tomd/tests/fixtures/build_synth_noise_no_diagram.py

The output PDF is one page with:

  - Enough body text to satisfy ``is_readable``.
  - Table borders and horizontal rules. NO real diagram. The
    extractor must produce zero vector image candidates and the
    uncertainty marker must be present (text-overlap or too-small
    rejections > 0) so a reader sees that the extractor ran and
    rejected everything.

Pins the false-positive floor: a paper-shaped page with no diagrams
must produce no spurious figures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf


_FIXTURES_DIR = Path(__file__).resolve().parent
_OUT_PATH = _FIXTURES_DIR / "synth_noise_no_diagram.pdf"


def build() -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    # Each body line stays under ~85 chars to fit in the 540pt usable
    # width at 11pt helv; pymupdf.Page.insert_text does NOT word-wrap
    # so anything past the page edge would be clipped from the PDF.
    page.insert_text(
        pymupdf.Point(72, 80),
        "Synthetic Noise Fixture",
        fontsize=16,
        fontname="helv",
    )
    intro_lines = [
        "This synthetic fixture contains only table borders and",
        "horizontal rules. The vector-extraction pipeline must produce",
        "zero image candidates from this page.",
    ]
    for i, line in enumerate(intro_lines):
        page.insert_text(
            pymupdf.Point(72, 110 + i * 14),
            line,
            fontsize=11,
            fontname="helv",
        )

    # A 4-row, 3-column table around y=160 to y=300. Each cell has its
    # own text label so the text-overlap filter will reject any cluster
    # the borders form.
    table_top = 160
    table_left = 100
    cell_w = 130
    cell_h = 30
    cols = ["Column A", "Column B", "Column C"]
    rows = ["Header", "Row 1 data", "Row 2 data", "Row 3 data"]
    for r_idx, row_label in enumerate(rows):
        y = table_top + r_idx * cell_h
        # Horizontal rule above each row.
        page.draw_line(
            pymupdf.Point(table_left, y),
            pymupdf.Point(table_left + 3 * cell_w, y),
            color=(0, 0, 0), width=0.5,
        )
        for c_idx, col_label in enumerate(cols):
            x = table_left + c_idx * cell_w
            # Vertical rule between columns.
            page.draw_line(
                pymupdf.Point(x, y),
                pymupdf.Point(x, y + cell_h),
                color=(0, 0, 0), width=0.5,
            )
            text = row_label if c_idx == 0 else f"{col_label} cell"
            page.insert_text(
                pymupdf.Point(x + 5, y + 18),
                text,
                fontsize=10,
                fontname="helv",
            )
    # Right edge + bottom edge of the table.
    page.draw_line(
        pymupdf.Point(table_left + 3 * cell_w, table_top),
        pymupdf.Point(table_left + 3 * cell_w, table_top + len(rows) * cell_h),
        color=(0, 0, 0), width=0.5,
    )
    page.draw_line(
        pymupdf.Point(table_left, table_top + len(rows) * cell_h),
        pymupdf.Point(table_left + 3 * cell_w, table_top + len(rows) * cell_h),
        color=(0, 0, 0), width=0.5,
    )

    # A few horizontal rules elsewhere on the page (running-header
    # underlines and section separators) - decoration the filter must
    # reject as "too small" or "edge band".
    page.draw_line(
        pymupdf.Point(72, 50),
        pymupdf.Point(540, 50),
        color=(0, 0, 0), width=0.5,
    )
    page.draw_line(
        pymupdf.Point(72, 750),
        pymupdf.Point(540, 750),
        color=(0, 0, 0), width=0.5,
    )

    # A short section separator between two paragraphs.
    page.draw_line(
        pymupdf.Point(72, 360),
        pymupdf.Point(540, 360),
        color=(0, 0, 0), width=0.3,
    )

    closing_lines = [
        "The text above is wrapped in a table; the lines on the page",
        "are borders and running-header rules, not diagrams.",
    ]
    for i, line in enumerate(closing_lines):
        page.insert_text(
            pymupdf.Point(72, 400 + i * 14),
            line,
            fontsize=11,
            fontname="helv",
        )

    doc.save(str(_OUT_PATH))
    doc.close()
    return _OUT_PATH


def main() -> int:
    out = build()
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
