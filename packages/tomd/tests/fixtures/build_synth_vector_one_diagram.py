#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Build the synth_vector_one_diagram.pdf golden fixture.

Run from the repo root:

    uv run --package tomd python \\
        packages/tomd/tests/fixtures/build_synth_vector_one_diagram.py

The output PDF is one page with:

  - Enough body text to satisfy ``is_readable``.
  - A single cluster of vector strokes forming a small diagram (one
    rectangle + four diagonal strokes inside it). 11 drawing items
    total - above ``_MIN_CLUSTER_ITEM_COUNT`` (8), and at 105pt tall
    by 95pt wide it clears ``_MIN_CLUSTER_DIM_PT`` (60) on both axes.
  - A "Figure 1: Sample diagram" caption line 30pt below the cluster
    so the alt-text test exercises the shared caption heuristic.

Reproducible: anyone can rerun this script to rebuild the PDF if a
pymupdf minor-version bump changes its rasterisation output. The PNG
fixture comparison test is perceptual (aHash + dimension check), not
byte-equal, so PDF rebuilds are usually safe; if the comparison
floor regresses, refresh the recorded aHash in the test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf


_FIXTURES_DIR = Path(__file__).resolve().parent
_OUT_PATH = _FIXTURES_DIR / "synth_vector_one_diagram.pdf"


def build() -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    # Body text: a short paragraph so the readability heuristic passes.
    # The text is intentionally generic and doesn't trip any of the
    # WG21-specific metadata extractors.
    #
    # Each line goes through a separate insert_text call because
    # pymupdf.Page.insert_text does NOT word-wrap; text past the right
    # page edge gets clipped from the PDF entirely and downstream
    # extraction would see truncated sentences. Lines stay under
    # ~85 chars to fit in the 540pt usable width at 11pt helv.
    page.insert_text(
        pymupdf.Point(72, 80),
        "Synthetic Vector Diagram Fixture",
        fontsize=16,
        fontname="helv",
    )
    body_lines = [
        "This is a synthetic test fixture used by the tomd test suite.",
        "It contains exactly one vector diagram drawn as PDF path",
        "operators below this paragraph.",
        "",
        "The extraction pipeline should detect the diagram as one image",
        "and emit a single image reference in the output markdown.",
    ]
    for i, line in enumerate(body_lines):
        if not line:
            continue
        page.insert_text(
            pymupdf.Point(72, 110 + i * 14),
            line,
            fontsize=11,
            fontname="helv",
        )

    # Diagram region: 100pt-wide x 105pt-tall cluster at (200, 200) to
    # (300, 305). Bounding rectangle plus four diagonal strokes -> 5
    # drawings, 5 items. We need at least _MIN_CLUSTER_ITEM_COUNT = 8,
    # so add four additional internal strokes for a total of 9 items.
    diagram_origin = (200, 200)
    diagram_size = (100, 105)
    box = pymupdf.Rect(
        diagram_origin[0], diagram_origin[1],
        diagram_origin[0] + diagram_size[0],
        diagram_origin[1] + diagram_size[1],
    )
    page.draw_rect(box, color=(0, 0, 0), width=0.8)

    # Inner detail strokes: diagonals and crossbars.
    page.draw_line(pymupdf.Point(box.x0, box.y0),
                   pymupdf.Point(box.x1, box.y1),
                   color=(0, 0, 0), width=0.5)
    page.draw_line(pymupdf.Point(box.x1, box.y0),
                   pymupdf.Point(box.x0, box.y1),
                   color=(0, 0, 0), width=0.5)
    midx = (box.x0 + box.x1) / 2
    midy = (box.y0 + box.y1) / 2
    page.draw_line(pymupdf.Point(box.x0, midy),
                   pymupdf.Point(box.x1, midy),
                   color=(0, 0, 0), width=0.5)
    page.draw_line(pymupdf.Point(midx, box.y0),
                   pymupdf.Point(midx, box.y1),
                   color=(0, 0, 0), width=0.5)

    # Four more short strokes inside the box so the cluster comfortably
    # exceeds _MIN_CLUSTER_ITEM_COUNT.
    for i in range(4):
        y = box.y0 + 15 + i * 18
        page.draw_line(
            pymupdf.Point(box.x0 + 10, y),
            pymupdf.Point(box.x0 + 30, y),
            color=(0, 0, 0), width=0.4,
        )

    # Caption line, 30pt below the diagram's bottom edge.
    page.insert_text(
        pymupdf.Point(diagram_origin[0], box.y1 + 25),
        "Figure 1: Sample diagram",
        fontsize=10,
        fontname="helv",
    )

    # Trailing body text after the caption. Same line-wrap discipline
    # as the leading paragraph.
    trailing_lines = [
        "The diagram above is rendered as a vector image in the source",
        "PDF and is extracted by the vector-extraction pipeline.",
    ]
    for i, line in enumerate(trailing_lines):
        page.insert_text(
            pymupdf.Point(72, box.y1 + 60 + i * 14),
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
