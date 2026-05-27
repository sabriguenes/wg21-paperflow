#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Build the synth_table_with_cells.pdf golden fixture.

Run from the repo root:

    uv run --package tomd python \\
        packages/tomd/tests/fixtures/build_synth_table_with_cells.py

The output PDF is one page with:

  - Enough body text to satisfy ``is_readable``.
  - A 4-column, 5-row comparison table laid out the same way P4003R1
    page 8 does it: each cell has its own light-gray background
    rectangle, the columns are NOT linked by any horizontal rules
    spanning the table, and the rightmost column is right-aligned
    numeric data with variable cell widths.

The shape is the calibrated false-positive case for vector
extraction (see ``bug-p4003r1-pg8-table-extraction.md``): each
column's per-cell backgrounds form a tight cluster, and with
inter-column gaps wider than the link distance and no horizontal
spanning element to bridge them, naively each column would emit as
its own vector PNG. The structural-overlap filter in
``pipeline.py`` (``_filter_vector_images_against_structural``) is
expected to drop these clusters because they overlap the detected
TABLE section. The right-edge-fallback in ``table.py``'s
``_columns_match`` is also expected to fire so the table-detector
actually finds all 5 rows (the right-aligned numeric column has
variable x-starts but a fixed x-end).

Lines stay under ~85 chars to fit in the 540pt usable width at 11pt
helv (same constraint as the other synth fixtures).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf


_FIXTURES_DIR = Path(__file__).resolve().parent
_OUT_PATH = _FIXTURES_DIR / "synth_table_with_cells.pdf"


def build() -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    page.insert_text(
        pymupdf.Point(72, 80),
        "Synthetic Table Fixture",
        fontsize=16,
        fontname="helv",
    )
    intro = [
        "This synthetic fixture exercises the structural-overlap filter:",
        "the comparison table below is rendered with per-cell background",
        "rectangles and no inter-column spanning rules, the canonical",
        "false-positive shape for vector extraction. Each column would",
        "naively cluster as its own vector image; the filter must drop",
        "them when the table detector identifies the region as a TABLE",
        "section.",
    ]
    for i, line in enumerate(intro):
        page.insert_text(
            pymupdf.Point(72, 110 + i * 14),
            line,
            fontsize=11,
            fontname="helv",
        )

    # Table layout. Four columns, five rows (header + 4 data rows).
    # Cell bounds in PDF points. Origin (x, y) is top-left.
    table_top = 220
    row_height = 24
    col_specs = [
        # (x0, x1, is_right_aligned)
        ( 72, 192, False),  # Platform (left-aligned)
        (192, 332, False),  # Mode (left-aligned)
        (332, 442, True),   # Time (right-aligned numeric)
        (442, 540, True),   # Score (right-aligned numeric)
    ]
    # 10 rows total - need >= 8 rows so each column's per-cell-background
    # cluster clears _MIN_CLUSTER_ITEM_COUNT (8) and reaches the
    # structural-overlap filter under test.
    cells = [
        # Header row
        ["Platform", "Mode", "Time (ms)", "Score"],
        # Data rows; values vary in length so the right-aligned columns
        # have variable x-starts but fixed x-ends.
        ["LinuxX64",   "release",  "1234.5", "9.10x"],
        ["LinuxARM",   "release",   "987.1", "10.40x"],
        ["macOSX64",   "debug",    "2456.7", "-"],
        ["WindowsX64", "release",  "1532.0", "8.21x"],
        ["FreeBSDX64", "release",  "1801.2", "7.55x"],
        ["LinuxRISCV", "release",  "3104.8", "5.20x"],
        ["macOSARM",   "release",   "856.4", "11.92x"],
        ["WindowsARM", "debug",    "4291.3", "-"],
        ["LinuxX64",   "debug",    "2103.0", "6.80x"],
    ]

    # Draw per-cell background rectangles (very light gray) and
    # then place the text. The backgrounds are the vector drawings
    # that would otherwise cluster into per-column PNGs.
    bg_color = (0.93, 0.93, 0.93)
    for row_idx, row in enumerate(cells):
        y0 = table_top + row_idx * row_height
        y1 = y0 + row_height
        for col_idx, (cell_text, (cx0, cx1, right_aligned)) in enumerate(
            zip(row, col_specs)
        ):
            # Cell background rectangle. The lack of an outline keeps
            # the drawing item count modest (1 'f' fill per cell) -
            # the realistic case.
            page.draw_rect(
                pymupdf.Rect(cx0, y0, cx1, y1),
                color=None,
                fill=bg_color,
                width=0,
            )
            # Text. Right-aligned columns use insert_textbox with
            # an align argument so the renderer right-aligns the
            # text; left-aligned columns just use insert_text.
            if right_aligned:
                page.insert_textbox(
                    pymupdf.Rect(cx0, y0 + 5, cx1 - 6, y1),
                    cell_text,
                    fontsize=10,
                    fontname="helv",
                    align=pymupdf.TEXT_ALIGN_RIGHT,
                )
            else:
                page.insert_text(
                    pymupdf.Point(cx0 + 6, y0 + 16),
                    cell_text,
                    fontsize=10,
                    fontname="helv",
                )

    # Trailing body text after the table.
    trailing = [
        "The result above shows that even when the markdown contains",
        "a clean rendered table, the converter must not emit duplicate",
        "vector PNGs of each column.",
    ]
    table_bottom = table_top + len(cells) * row_height
    for i, line in enumerate(trailing):
        page.insert_text(
            pymupdf.Point(72, table_bottom + 30 + i * 14),
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
