#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Golden-fixture tests for vector-figure extraction.

Drives ``run_pipeline`` against the two committed synthetic PDFs in
``tests/fixtures/``:

  - ``synth_vector_one_diagram.pdf`` - one cluster of vector strokes
    forming a small diagram, plus a "Figure 1:" caption line. The
    pipeline must produce exactly one vector image with the matched
    caption as alt text.
  - ``synth_noise_no_diagram.pdf`` - table borders and horizontal
    rules only, no real diagram. The pipeline must produce zero
    vector images and emit the per-paper uncertainty marker.

A third test exercises the v2.0 opt-in default: without
``extract_vector=True`` the diagram fixture's converted markdown is
byte-identical to the raster-only path, with no marker and no image
ref.

PNG fixture comparison is perceptual (8x8 average-hash within
Hamming distance 4, plus pixel-dimension match within 1 px), not
byte-equal. Anti-aliasing, zlib compression level, and ICC profile
defaults all drift across pymupdf minor releases; a byte-equal
assertion would force a fixture refresh on every dep bump. The
docstring on the aHash helper names the algorithm so a future
reader does not reverse-engineer the encoding.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from tomd.lib.pdf import vector_images
from tomd.lib.pdf.pipeline import run_pipeline


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DIAGRAM_PDF = _FIXTURES / "synth_vector_one_diagram.pdf"
_DIAGRAM_GOLDEN = _FIXTURES / "synth_vector_one_diagram.golden.md"
_NOISE_PDF = _FIXTURES / "synth_noise_no_diagram.pdf"
_NOISE_GOLDEN = _FIXTURES / "synth_noise_no_diagram.golden.md"
_TABLE_PDF = _FIXTURES / "synth_table_with_cells.pdf"
_TABLE_GOLDEN = _FIXTURES / "synth_table_with_cells.golden.md"

# Recorded aHash of the diagram fixture's rasterised PNG. Stable
# across pymupdf 1.27.x; refresh if a perceptual delta sneaks past
# the Hamming-4 tolerance (in which case investigate first).
_DIAGRAM_AHASH_BASELINE = 0x66fffb6464fbfd64

# Expected PNG dimensions at 150 DPI for a 100x105pt cluster.
# pymupdf rounds the pixel extents UP from the floor of bbox*sx;
# allow a small per-axis drift so a different rounding rule does
# not break the test on a future minor-version bump.
_DIAGRAM_BBOX_PT = (100, 105)
_RASTERISE_DPI = 150
_PIXEL_TOLERANCE = 2


def _ahash_8x8(png_bytes: bytes) -> int:
    """8x8 average-hash of a PNG.

    Pillow-only implementation (no numpy / scipy / imagehash). pHash was
    considered and rejected: it requires a 2-D DCT that Pillow does not
    provide and the additional dependency was not worth the marginal
    accuracy gain on diagrams that are mostly line art.

    Algorithm:
      1. Decode PNG -> 8-bit grayscale.
      2. Resize to 8x8 with LANCZOS resampling (anti-alias robust).
      3. Threshold each pixel against the mean of the 8x8 grid.
      4. Emit 64 bits in row-major order (LSB = pixel 0,0).
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize((8, 8), Image.LANCZOS)
    # Pillow 12 deprecated Image.getdata in favour of get_flattened_data;
    # fall back to getdata for older Pillow installs.
    pixels = list(
        img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()
    )
    mean = sum(pixels) / 64.0
    bits = 0
    for i, value in enumerate(pixels):
        if value >= mean:
            bits |= (1 << i)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _run_with_vector_extract(pdf: Path):
    """Run the full pipeline with the page-scan floor lifted so our small
    synthetic fixtures pass ``_MIN_PAGE_DRAWING_ITEMS`` even though they
    have only dozens of drawing items rather than the 250+ that a real
    WG21 page produces. The threshold itself is unit-tested separately."""
    from unittest.mock import patch

    with patch.object(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1):
        return run_pipeline(pdf, extract_vector=True)


class TestSynthVectorOneDiagram:
    """End-to-end against the diagram fixture: one figure, captioned,
    PNG decodes, dimensions plausible, perceptual hash stable."""

    def test_markdown_matches_golden(self):
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        assert result.md == _DIAGRAM_GOLDEN.read_text(), (
            "synth_vector_one_diagram.golden.md is the source of truth. "
            "If the PDF or pipeline behaviour deliberately changed, "
            "rebuild the golden via the builder script and re-record."
        )

    def test_exactly_one_vector_image_extracted(self):
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        vector_images_ = [im for im in result.images if im.source == "vector"]
        assert len(vector_images_) == 1
        raster_images = [im for im in result.images if im.source == "raster"]
        assert raster_images == []

    def test_image_carries_figure_caption_as_alt(self):
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        assert result.images[0].suggested_alt == "Figure 1: Sample diagram"

    def test_image_bytes_decode_as_png(self):
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        png = result.images[0].bytes
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(png))
        # Sanity: decoder produced a non-empty image.
        assert img.width > 0 and img.height > 0

    def test_png_dimensions_match_expected(self):
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        png = result.images[0].bytes
        img = Image.open(io.BytesIO(png))
        expected_w = int(round(_DIAGRAM_BBOX_PT[0] * _RASTERISE_DPI / 72))
        expected_h = int(round(_DIAGRAM_BBOX_PT[1] * _RASTERISE_DPI / 72))
        assert abs(img.width - expected_w) <= _PIXEL_TOLERANCE, (
            f"width drift: got {img.width}, expected {expected_w} +/- {_PIXEL_TOLERANCE}"
        )
        assert abs(img.height - expected_h) <= _PIXEL_TOLERANCE

    def test_perceptual_ahash_within_hamming_4_of_baseline(self):
        """8x8 average-hash, Hamming distance 4 tolerance.

        Recorded baseline drifts if pymupdf changes its rasterisation
        anti-aliasing or compression defaults. If this fails, look at
        the rasterised PNG visually before bumping the baseline -
        a genuinely different image is a regression worth catching.
        """
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        actual = _ahash_8x8(result.images[0].bytes)
        dist = _hamming(actual, _DIAGRAM_AHASH_BASELINE)
        assert dist <= 4, (
            f"aHash Hamming distance {dist} exceeds tolerance 4. "
            f"actual=0x{actual:016x} baseline=0x{_DIAGRAM_AHASH_BASELINE:016x}"
        )

    def test_uncertainty_marker_absent_on_clean_extraction(self):
        """All candidates kept, no skipped pages -> no marker emitted
        (the plan's "only present when there's something honest to
        disclose" contract)."""
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        assert "tomd:vector-extraction-uncertain" not in result.md

    def test_pipeline_result_carries_vector_uncertainty(self):
        """The accumulator surfaces on PipelineResult even when the
        marker is suppressed; downstream consumers (CLI log line in
        commit 3) can still read counts."""
        result = _run_with_vector_extract(_DIAGRAM_PDF)
        assert result.vector_uncertainty is not None
        assert result.vector_uncertainty.pages_scanned == 1
        assert result.vector_uncertainty.candidates == 1
        assert result.vector_uncertainty.kept == 1
        assert result.vector_uncertainty.rejected == 0


class TestSynthNoiseNoDiagram:
    """Negative golden: table borders + rules, no diagram. Zero images,
    uncertainty marker present, marker fields match the per-filter
    rejection counts."""

    def test_markdown_matches_golden(self):
        result = _run_with_vector_extract(_NOISE_PDF)
        assert result.md == _NOISE_GOLDEN.read_text()

    def test_zero_vector_images_produced(self):
        result = _run_with_vector_extract(_NOISE_PDF)
        assert all(im.source != "vector" for im in result.images)

    def test_marker_present_with_kept_zero_rejected_positive(self):
        result = _run_with_vector_extract(_NOISE_PDF)
        assert "tomd:vector-extraction-uncertain" in result.md
        assert "kept=0" in result.md
        # rejected must be > 0 - the marker fires because something was
        # rejected, not just because the page was scanned.
        assert " rejected=0 " not in result.md

    def test_marker_reasons_are_alphabetical(self):
        """D7 contract pinned at the end-to-end layer (the formatter's
        unit test pins it at the format-string layer)."""
        result = _run_with_vector_extract(_NOISE_PDF)
        # Parse the marker's reasons body and assert keys are sorted.
        marker_line = next(
            ln for ln in result.md.splitlines()
            if "tomd:vector-extraction-uncertain" in ln
        )
        body = marker_line.split("reasons={", 1)[1].split("}", 1)[0]
        keys = [pair.split(":", 1)[0] for pair in body.split(", ")]
        assert keys == sorted(keys)


class TestSynthTableWithCells:
    """End-to-end coverage for the structural-overlap / table-detector
    fix class (calibrated against P4003R1 page 8, full report in
    ``bug-p4003r1-pg8-table-extraction.md``).

    The fixture is a comparison table rendered with per-cell background
    rectangles and no inter-column spanning rules - the canonical
    false-positive shape that caused page 8's 4 column-images. The
    test asserts the converter produces ZERO phantom vector images
    from this layout AND a properly-formatted markdown table covering
    all rows.

    The right-edge-fallback in ``table.py::_columns_match`` is also
    exercised because the rightmost numeric column has variable
    x-starts (cell text length varies: "9.10x" vs "11.92x" vs "-")
    but a fixed x-end.
    """

    def test_markdown_matches_golden(self):
        result = _run_with_vector_extract(_TABLE_PDF)
        assert result.md == _TABLE_GOLDEN.read_text()

    def test_zero_vector_images_produced(self):
        """No matter which filter does the work (text_overlap or
        structural-overlap), the converter must emit zero phantom
        column-images. The user-visible contract is "table is a
        table, not 4 stacked column-images"."""
        result = _run_with_vector_extract(_TABLE_PDF)
        assert all(im.source != "vector" for im in result.images)

    def test_markdown_table_has_all_rows(self):
        """The right-edge-fallback in _columns_match must fire so the
        table detector finds all 10 rows (1 header + 9 data). Counted
        by markdown pipe-delimited row markers; the separator row
        ``| --- | --- | --- | --- |`` and 10 content rows = 11 lines
        with leading ``|``."""
        result = _run_with_vector_extract(_TABLE_PDF)
        table_lines = [ln for ln in result.md.splitlines() if ln.startswith("|")]
        # 1 header line + 1 separator + 9 data rows = 11 pipe-prefixed lines.
        assert len(table_lines) == 11, (
            f"expected 11 pipe-prefixed lines (header + sep + 9 data), "
            f"got {len(table_lines)}: {table_lines}"
        )

    def test_right_aligned_column_values_present(self):
        """The variable-length right-aligned values must all appear in
        the markdown table. If _columns_match's x-end fallback failed,
        the table detector would split the rows and some values would
        end up as prose."""
        result = _run_with_vector_extract(_TABLE_PDF)
        # Spot-check a value from each x-start variation: short, mid,
        # long, and the dash-only cell.
        for value in ("9.10x", "11.92x", "10.40x", "-"):
            assert f"| {value} |" in result.md, (
                f"value {value!r} should appear in a markdown table cell"
            )

    def test_uncertainty_marker_explains_rejections(self):
        """When the vector path catches the column-clusters (by any
        filter), the marker must disclose the rejection count so a
        reader sees that something was filtered."""
        result = _run_with_vector_extract(_TABLE_PDF)
        assert "tomd:vector-extraction-uncertain" in result.md
        # 4 column-clusters formed (one per column), all rejected.
        assert "candidates=4" in result.md
        assert "kept=0" in result.md
        assert "rejected=4" in result.md


class TestVectorExtractionIsOptIn:
    """v2.0 default: ``extract_vector=False`` skips the vector path
    entirely. paper.md matches the raster-only path byte-for-byte and
    the uncertainty marker is absent."""

    def test_default_run_skips_vector_path(self):
        # Note: no monkeypatch needed - default-off doesn't touch the
        # threshold either way.
        result = run_pipeline(_DIAGRAM_PDF)
        assert result.vector_uncertainty is None
        assert all(im.source != "vector" for im in result.images)
        assert "tomd:vector-extraction-uncertain" not in result.md

    def test_default_run_output_is_stable_across_runs(self):
        """Same default-off invocation twice produces identical
        markdown - the vector path's per-run synthetic-xref hash
        randomness must not leak into raster-only output."""
        first = run_pipeline(_DIAGRAM_PDF).md
        second = run_pipeline(_DIAGRAM_PDF).md
        assert first == second


class TestVectorExtractionIdempotency:
    """Same fixture, same ``extract_vector=True`` invocation, twice:
    the produced markdown is identical, and the PNG bytes are
    perceptually identical (aHash equal). Catches non-determinism in
    the cluster ordering, the rasterisation pass, or the marker."""

    def test_idempotent_markdown(self):
        first = _run_with_vector_extract(_DIAGRAM_PDF)
        second = _run_with_vector_extract(_DIAGRAM_PDF)
        assert first.md == second.md

    def test_idempotent_png_perceptual(self):
        first = _run_with_vector_extract(_DIAGRAM_PDF)
        second = _run_with_vector_extract(_DIAGRAM_PDF)
        h1 = _ahash_8x8(first.images[0].bytes)
        h2 = _ahash_8x8(second.images[0].bytes)
        assert h1 == h2


class TestVectorCorpusAcceptanceCsvShape:
    """Pin the shape of the committed expectations CSV so a future
    maintainer adding rows for real-corpus papers doesn't accidentally
    break the column contract."""

    @pytest.fixture(scope="class")
    def rows(self):
        import csv
        csv_path = _FIXTURES / "vector-corpus-acceptance-expectations.csv"
        with csv_path.open() as f:
            return list(csv.DictReader(f))

    def test_csv_has_required_columns(self, rows):
        assert rows, "expectations CSV must not be empty"
        required = {
            "pid", "expected_vector_count", "allowed_min", "allowed_max",
            "expect_captioned", "produced_by", "produced_at", "notes",
        }
        assert required.issubset(set(rows[0].keys()))

    def test_synth_fixtures_listed(self, rows):
        pids = {row["pid"] for row in rows}
        assert "SYNTH_VECTOR_ONE_DIAGRAM" in pids
        assert "SYNTH_NOISE_NO_DIAGRAM" in pids

    def test_synth_diagram_expects_one_image(self, rows):
        row = next(r for r in rows if r["pid"] == "SYNTH_VECTOR_ONE_DIAGRAM")
        assert int(row["expected_vector_count"]) == 1
        assert int(row["allowed_min"]) <= 1 <= int(row["allowed_max"])

    def test_synth_noise_expects_zero_images(self, rows):
        row = next(r for r in rows if r["pid"] == "SYNTH_NOISE_NO_DIAGRAM")
        assert int(row["expected_vector_count"]) == 0
        assert int(row["allowed_max"]) == 0, (
            "noise row's allowed_max must be 0 - a single accidental "
            "false positive on a pure-noise page is a regression worth catching"
        )
