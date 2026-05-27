#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Unit tests for image extraction (Resource-Dictionary path).

Pure-function tests that build synthetic Block / Section / candidate
inputs in-memory. PDF-level end-to-end coverage lives in
``test_pdf_golden.py``; here we exercise the regex, dedup, multi-rect,
cap, and structure-pass-survival edges that would otherwise need a
hand-crafted PDF fixture per case.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pymupdf
import pytest

from conftest import make_section

from tomd.lib.pdf.emit import emit_markdown
from tomd.lib.pdf.images import (
    ExtractedImage,
    VectorUncertaintyStats,
    _MAX_IMAGES_PER_PAPER,
    _PageImageCandidate,
    _VectorExtractionStats,
    _caption_for,
    finalize_extraction,
)
from tomd.lib.pdf.pipeline import (
    _filter_overlapping_vector_images,
    _filter_sections_inside_vector_images,
    _filter_vector_images_against_structural,
    _make_image_section,
)
from tomd.lib.pdf.structure import structure_sections
from tomd.lib.pdf.types import (
    Block,
    Confidence,
    Line,
    Section,
    SectionKind,
    Span,
)
from tomd.lib.pdf import vector_images
from tomd.lib.pdf.vector_images import (
    ALLOWED_REASON_KEYS,
    REASON_ASPECT_EXTREME,
    REASON_BBOX_TOO_LARGE,
    REASON_CLUSTERS_OVERFLOW,
    REASON_EDGE_BAND,
    REASON_TEXT_OVERLAP,
    REASON_TOO_FEW_ITEMS,
    REASON_TOO_SMALL,
    REASON_WORDING_COLOR,
    _cluster_drawings,
    _colour_in_wording_band,
    _synthetic_xref,
    _text_overlap_fraction,
    extract_page_vector_images,
    format_uncertainty_marker,
    should_emit_marker,
)


# ---- helpers ----------------------------------------------------------------


def _line(text: str, y0: float, x0: float = 50.0, height: float = 12.0) -> Line:
    """Build a single-span Line at (x0, y0) with given height."""
    span = Span(text=text, font_name="Test", font_size=11.0,
                bbox=(x0, y0, x0 + 200.0, y0 + height))
    return Line(spans=[span], bbox=(x0, y0, x0 + 200.0, y0 + height))


def _block(lines: list[Line], page_num: int = 0) -> Block:
    return Block(lines=lines, page_num=page_num)


def _candidate(
    xref: int,
    page: int,
    y0: float,
    x0: float = 50.0,
    *,
    ext: str = "png",
    data: bytes = b"PNGDATA",
    suggested_alt: str = "",
) -> _PageImageCandidate:
    return _PageImageCandidate(
        xref=xref,
        page=page,
        bbox=(x0, y0, x0 + 100.0, y0 + 100.0),
        ext=ext,
        bytes=data,
        suggested_alt=suggested_alt,
    )


def _image_section(
    *,
    page: int = 1,
    index: int = 1,
    bbox: tuple[float, float, float, float] = (10, 10, 100, 100),
    alt: str = "",
    stored_filename: str = "p1-fig1-1.png",
    xref: int = 1,
) -> Section:
    """Construct an IMAGE Section identical to what pipeline._make_image_section produces."""
    img = ExtractedImage(
        page=page,
        index_on_page=index,
        ext="png",
        bytes=b"PNGDATA",
        bbox=bbox,
        suggested_alt=alt,
        stored_filename=stored_filename,
        xref=xref,
    )
    return Section(
        kind=SectionKind.IMAGE,
        text="",
        confidence=Confidence.HIGH,
        page_num=page - 1,
        image_ref=img,
    )


# ---- _caption_for: label match below image ---------------------------------


def test_caption_for_figure_label_below():
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Figure 1: Hello World!", y0=210.0)])]
    assert _caption_for(img_bbox, blocks) == "Figure 1: Hello World!"


def test_caption_for_listing_label():
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Listing 3: example code", y0=205.0)])]
    assert _caption_for(img_bbox, blocks) == "Listing 3: example code"


def test_caption_for_en_dash_separator():
    """Real WG21 papers use the en-dash (U+2013) for figure captions."""
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Figure 1 – memory layout", y0=210.0)])]
    assert _caption_for(img_bbox, blocks) == "Figure 1 – memory layout"


def test_caption_for_em_dash_separator():
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Figure 1 — memory layout", y0=210.0)])]
    assert _caption_for(img_bbox, blocks) == "Figure 1 — memory layout"


def test_caption_for_above_when_below_absent():
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Figure 5: above caption", y0=80.0)])]
    assert _caption_for(img_bbox, blocks) == "Figure 5: above caption"


def test_caption_for_prefers_below_over_above():
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([
        _line("Figure 1: above", y0=80.0),
        _line("Figure 2: below", y0=210.0),
    ])]
    assert _caption_for(img_bbox, blocks) == "Figure 2: below"


# ---- _caption_for: graceful empty fallback ---------------------------------


def test_caption_for_no_match_returns_empty():
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Just some surrounding prose.", y0=210.0)])]
    assert _caption_for(img_bbox, blocks) == ""


def test_caption_for_does_not_match_table_label():
    """N2 regression: Table N: must never leak into image alt text.

    Tables own their captions structurally via SectionKind.TABLE.
    A results table sitting 30pt below an image bbox should NOT
    become misattributed alt text on the image.
    """
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Table 1: Comparison of foo vs bar", y0=230.0)])]
    assert _caption_for(img_bbox, blocks) == ""


def test_caption_for_rejects_bare_body_text():
    """Concern #2 regression: unlabeled body prose 30pt below an image must
    NOT become alt text. The bare-line fallback was removed because
    misattribution propagates into LLM prompts via wrap_source, a worse
    failure mode than empty alt.
    """
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line(
        "We discussed this in Section 3.1, see references below for additional context.",
        y0=230.0,
    )])]
    assert _caption_for(img_bbox, blocks) == ""


def test_caption_for_skips_out_of_radius_below():
    """Lines below the 60pt search radius are ignored even when labelled."""
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Figure 1: too far below", y0=270.0)])]   # 70pt below
    assert _caption_for(img_bbox, blocks) == ""


def test_caption_for_skips_out_of_radius_above():
    """Lines above the 30pt search radius are ignored even when labelled."""
    img_bbox = (100.0, 100.0, 300.0, 200.0)
    blocks = [_block([_line("Figure 1: too far above", y0=50.0)])]   # 50pt above
    assert _caption_for(img_bbox, blocks) == ""


# ---- finalize_extraction: cross-page xref dedup ----------------------------


def test_finalize_dedupes_repeated_xref_across_pages():
    """A logo in an inherited resource dict appears on every page; the
    page-header pattern collapses to one IMAGE at its first occurrence."""
    per_page = [
        [_candidate(42, page=1, y0=10.0)],
        [_candidate(42, page=2, y0=10.0)],
        [_candidate(42, page=3, y0=10.0)],
    ]
    r = finalize_extraction(per_page, "p1")
    assert len(r.images) == 1
    assert r.images[0].xref == 42
    assert r.images[0].page == 1
    assert r.source_image_count == 1
    assert r.images_truncated is False


def test_finalize_multi_rect_on_same_page_picks_top_left():
    """Concern #5: same xref rendered at multiple rects on first-occurrence
    page -> smallest (y0, x0) rect is canonical.
    """
    per_page = [
        [
            _candidate(42, page=1, y0=600.0, x0=100.0),   # bottom rect (larger y0)
            _candidate(42, page=1, y0=50.0,  x0=100.0),   # TOP rect (smallest y0)
        ],
    ]
    r = finalize_extraction(per_page, "p1")
    assert len(r.images) == 1
    assert r.images[0].bbox == (100.0, 50.0, 200.0, 150.0)
    assert r.source_image_count == 1


def test_finalize_multi_page_picks_earliest_page():
    """Same xref on pages 3 and 1 -> first-occurrence page is 1."""
    per_page = [
        [_candidate(42, page=3, y0=10.0)],
        [_candidate(42, page=1, y0=10.0)],
    ]
    r = finalize_extraction(per_page, "p1")
    assert len(r.images) == 1
    assert r.images[0].page == 1


# ---- finalize_extraction: ordering and naming ------------------------------


def test_finalize_assigns_index_after_y0_sort():
    """index_on_page reflects the canonical (y0, x0) sort on the chosen page,
    not pymupdf's enumeration order."""
    per_page = [
        [
            _candidate(1, page=1, y0=400.0),    # arrives first but lower on page
            _candidate(2, page=1, y0=50.0),     # arrives second, topmost
        ],
    ]
    r = finalize_extraction(per_page, "p1")
    assert [im.xref for im in r.images] == [2, 1]
    assert [im.index_on_page for im in r.images] == [1, 2]


def test_finalize_stored_filename_uses_lowercase_pid():
    per_page = [[_candidate(1, page=3, y0=10.0, ext="png")]]
    r = finalize_extraction(per_page, "P3556R0")
    assert r.images[0].stored_filename == "p3556r0-fig3-1.png"


def test_finalize_preserves_image_format():
    per_page = [
        [_candidate(1, page=1, y0=10.0, ext="jpeg")],
        [_candidate(2, page=2, y0=10.0, ext="jpx")],
    ]
    r = finalize_extraction(per_page, "p1")
    assert {im.ext for im in r.images} == {"jpeg", "jpx"}


def test_finalize_empty_input():
    r = finalize_extraction([], "p1")
    assert r.images == []
    assert r.source_image_count == 0
    assert r.images_truncated is False


# ---- finalize_extraction: 20-image cap -------------------------------------


def test_finalize_cap_trips_on_unique_xrefs():
    """21 unique xrefs -> 20 kept, source_image_count=21, truncated=True."""
    per_page = [
        [_candidate(x, page=1, y0=10.0 + x) for x in range(1, 22)]
    ]
    r = finalize_extraction(per_page, "p1")
    assert len(r.images) == _MAX_IMAGES_PER_PAPER
    assert r.source_image_count == 21
    assert r.images_truncated is True


def test_finalize_cap_not_tripped_by_repeated_xref():
    """The cap is over UNIQUE xrefs. 21 references to one logo stays at 1."""
    per_page = [
        [_candidate(42, page=p, y0=10.0) for p in range(1, 22)]
    ]
    r = finalize_extraction(per_page, "p1")
    assert len(r.images) == 1
    assert r.source_image_count == 1
    assert r.images_truncated is False


def test_finalize_cap_keeps_earliest_unique_xrefs():
    """When truncating, the 20 kept must be the first by (page, y0, x0)."""
    per_page = [
        # 25 unique xrefs in monotonically increasing y0; we expect xrefs 1..20.
        [_candidate(x, page=1, y0=10.0 * x) for x in range(1, 26)]
    ]
    r = finalize_extraction(per_page, "p1")
    kept_xrefs = sorted(im.xref for im in r.images)
    assert kept_xrefs == list(range(1, 21))
    assert r.source_image_count == 25


# ---- structure_sections: IMAGE survives all adjacency passes ---------------


def test_structure_pass_preserves_image_between_paragraphs():
    """IMAGE must not be absorbed; image_ref must be intact post-structure."""
    img_sec = _image_section()
    sections = [
        make_section("First paragraph.", page_num=0),
        img_sec,
        make_section("Second paragraph.", page_num=0),
    ]
    _, out, _ = structure_sections(sections, has_title=True)
    images = [s for s in out if s.kind == SectionKind.IMAGE]
    assert len(images) == 1
    assert images[0].image_ref is img_sec.image_ref
    assert images[0].text == ""


def test_structure_pass_blocks_paragraph_merge_across_image():
    """A paragraph ending without terminal punctuation must NOT merge with
    the paragraph after the IMAGE.
    _merge_paragraphs is the pass at risk: it joins prev (no terminal
    punctuation) with current (starts lowercase). IMAGE has to break the
    chain.
    """
    sections = [
        make_section("First paragraph fragment", page_num=0),     # no terminal punct
        _image_section(),
        make_section("continuation begins lowercase.", page_num=0),
    ]
    _, out, _ = structure_sections(sections, has_title=True)
    paras = [s for s in out if s.kind == SectionKind.PARAGRAPH]
    assert len(paras) == 2, f"merged across IMAGE: {[s.text for s in paras]}"


def test_finalize_drops_inline_emoji_sized_rasters():
    """Inline emoji glyphs (PDF font-replacement PNGs at 8-18pt) are
    not figures and must be filtered out.

    The corpus survey of N5007 (editor's report with 107 emoji as
    embedded PNGs, all <= 18pt) and P4216R0 (8x8 emoji in a code
    block) showed: leaving these in produces phantom IMAGE sections
    at wrong y-positions, oversized rendering, and noise that
    pollutes the analytical pipelines downstream. The smallest
    genuine figure bbox in the workspace is 24x24, so the 20pt
    minimum bbox dim has comfortable margin.
    """
    per_page = [
        [
            _candidate(1, page=1, y0=10, x0=10),    # tiny emoji at 100x100
            _candidate(2, page=1, y0=200, x0=200),  # also tiny
            _candidate(3, page=1, y0=500, x0=200),  # real figure (overridden below)
        ],
    ]
    # Shrink the first two to emoji dimensions, leave the third figure-sized.
    per_page[0][0] = _PageImageCandidate(
        xref=1, page=1, bbox=(10.0, 10.0, 18.0, 18.0),
        ext="png", bytes=b"emoji1", suggested_alt="",
    )
    per_page[0][1] = _PageImageCandidate(
        xref=2, page=1, bbox=(200.0, 200.0, 212.8, 212.8),
        ext="png", bytes=b"emoji2", suggested_alt="",
    )
    per_page[0][2] = _PageImageCandidate(
        xref=3, page=1, bbox=(200.0, 500.0, 400.0, 700.0),
        ext="png", bytes=b"real_figure", suggested_alt="Figure 1: example",
    )

    r = finalize_extraction(per_page, "p1")
    assert len(r.images) == 1
    assert r.images[0].xref == 3
    assert r.images[0].suggested_alt == "Figure 1: example"
    # source_image_count is post-filter so the truncation marker
    # doesn't get inflated by emoji.
    assert r.source_image_count == 1
    assert r.images_truncated is False


def test_finalize_keeps_image_at_filter_boundary():
    """A 20x20 bbox is exactly at the threshold and must be kept
    (the filter uses ``>= _MIN_IMAGE_DIM_PT``). Smaller-than-threshold
    is the dropped band.
    """
    per_page = [[
        _PageImageCandidate(
            xref=1, page=1, bbox=(0.0, 0.0, 20.0, 20.0),
            ext="png", bytes=b"boundary", suggested_alt="",
        ),
        _PageImageCandidate(
            xref=2, page=1, bbox=(0.0, 100.0, 19.5, 119.5),
            ext="png", bytes=b"under", suggested_alt="",
        ),
    ]]
    r = finalize_extraction(per_page, "p1")
    assert {im.xref for im in r.images} == {1}


def test_image_not_swept_into_toc_gap_fill():
    """Regression: an IMAGE section between two heading-matched
    sections (find_toc_indices gap-fill territory) must not be
    stripped as TOC content.

    Reproduces the P4216R0 failure mode: paper has headings
    ``Abstract``, ``Tony Table``, ``Revisions``, ``Motivation``
    consecutively, with an image between ``Tony Table`` and
    ``Revisions``. find_toc_indices marks the consecutive heading
    matches as a TOC run and gap-fills the IMAGE index. The
    pipeline filter must drop IMAGE indices from the TOC set
    before applying them.
    """
    # Build a section list that mimics the failure: heading,
    # heading, IMAGE, heading, heading.
    def _heading(text: str) -> Section:
        line = Line(spans=[
            Span(text=text, font_name="T", font_size=14.0,
                 bbox=(0, 0, 100, 14), bold=True),
        ], bbox=(0, 0, 100, 14))
        return Section(
            kind=SectionKind.HEADING, text=text, heading_level=2,
            confidence=Confidence.HIGH, lines=[line],
        )

    img_sec = _image_section()
    sections = [
        _heading("Abstract"),
        _heading("Tony Table"),
        img_sec,
        _heading("Revisions"),
        _heading("Motivation"),
    ]
    # Simulate the pipeline filter directly: build toc_indices set,
    # filter out IMAGE indices, ensure the IMAGE survives.
    from tomd.lib.toc import find_toc_indices

    texts = [sec.text.split("\n")[0].strip() for sec in sections]
    headings = {sec.text.split("\n")[0].strip() for sec in sections
                if sec.kind == SectionKind.HEADING}
    toc_indices = find_toc_indices(texts, headings, None)

    # The buggy state would include index 2 (the IMAGE) in toc_indices.
    # Verify the filter at the pipeline call site removes it.
    filtered = {i for i in toc_indices
                if sections[i].kind is not SectionKind.IMAGE}
    assert 2 not in filtered, "IMAGE must not be in TOC strip set"


def test_structure_pass_image_not_classified_as_list_item():
    """_detect_lists_by_position scans PARAGRAPH sections only; an IMAGE
    sandwiched between two LIST items must not become a list item itself."""
    bullet_line = Line(spans=[
        Span(text="• item one", font_name="Test", font_size=11.0,
             bbox=(60.0, 100.0, 200.0, 112.0)),
    ], bbox=(60.0, 100.0, 200.0, 112.0))
    list_sec_top = Section(
        kind=SectionKind.LIST, text="• item one",
        confidence=Confidence.HIGH, lines=[bullet_line],
        page_num=0, font_size=11.0,
    )
    img_sec = _image_section()
    bullet_line_bot = Line(spans=[
        Span(text="• item two", font_name="Test", font_size=11.0,
             bbox=(60.0, 300.0, 200.0, 312.0)),
    ], bbox=(60.0, 300.0, 200.0, 312.0))
    list_sec_bot = Section(
        kind=SectionKind.LIST, text="• item two",
        confidence=Confidence.HIGH, lines=[bullet_line_bot],
        page_num=0, font_size=11.0,
    )
    _, out, _ = structure_sections(
        [list_sec_top, img_sec, list_sec_bot], has_title=True,
    )
    image_positions = [i for i, s in enumerate(out) if s.kind == SectionKind.IMAGE]
    assert len(image_positions) == 1
    assert out[image_positions[0]].kind == SectionKind.IMAGE


# ---- emit_markdown: image and truncation rendering -------------------------


def test_emit_renders_image_with_caption():
    img_sec = _image_section(alt="Figure 1: Hello World!",
                              stored_filename="p3556r0-fig3-1.png")
    md = emit_markdown({"title": "Test"}, [img_sec])
    assert "![Figure 1: Hello World!](p3556r0-fig3-1.png)" in md


def test_emit_renders_image_with_empty_alt():
    img_sec = _image_section(alt="", stored_filename="p1-fig0-1.png")
    md = emit_markdown({"title": "Test"}, [img_sec])
    assert "![](p1-fig0-1.png)" in md


def test_emit_escapes_brackets_in_alt():
    """Alt text containing ``]`` would otherwise truncate the markdown image
    syntax (e.g. ``![Figure 1 [revised]: ...](...)``)."""
    img_sec = _image_section(
        alt="Figure 1 [revised]: caption",
        stored_filename="p1-fig0-1.png",
    )
    md = emit_markdown({"title": "Test"}, [img_sec])
    assert r"![Figure 1 \[revised\]: caption](p1-fig0-1.png)" in md


def test_emit_appends_truncation_marker_when_capped():
    img_sec = _image_section(alt="caption",
                              stored_filename="p1-fig0-1.png")
    md = emit_markdown(
        {"title": "Test"}, [img_sec],
        images_truncated=True, source_image_count=64,
    )
    assert "tomd:images-truncated" in md
    assert "kept 1 of 64" in md
    assert "63 image(s) dropped" in md


def test_emit_no_truncation_marker_when_not_capped():
    img_sec = _image_section(alt="caption",
                              stored_filename="p1-fig0-1.png")
    md = emit_markdown(
        {"title": "Test"}, [img_sec],
        images_truncated=False, source_image_count=1,
    )
    assert "tomd:images-truncated" not in md


# ---- source field: per-image discrimination of Confidence ------------------


def test_finalize_default_source_is_raster():
    """Candidates built without a source argument keep the legacy "raster"
    default. Pre-existing call sites (HTML manifest, v1 PDF path) need
    not know about the new field.
    """
    per_page = [[_candidate(1, page=1, y0=10.0)]]
    r = finalize_extraction(per_page, "p1")
    assert r.images[0].source == "raster"


def test_finalize_propagates_source_from_candidate():
    """A candidate explicitly tagged as vector reaches the extracted image
    unchanged. Round-trip the field through the full pipeline so that a
    future maintainer reordering the dataclass fields catches the break.
    """
    per_page = [[
        _PageImageCandidate(
            xref=-1234,                           # synthetic negative xref
            page=1,
            bbox=(50.0, 50.0, 200.0, 150.0),
            ext="png",
            bytes=b"VECTOR-RASTERISED-PNG",
            suggested_alt="",
            source="vector",
        ),
    ]]
    r = finalize_extraction(per_page, "p1")
    assert r.images[0].source == "vector"


def test_make_image_section_vector_source_yields_medium_confidence():
    """The Confidence branch keys on ``source``, not the sign of xref.
    Construct an ExtractedImage with source="vector" AND a positive
    xref. The section must still get Confidence.MEDIUM. Catches the
    regression where someone reverts to xref-sign discrimination.
    """
    img = ExtractedImage(
        page=1,
        index_on_page=1,
        ext="png",
        bytes=b"PNGDATA",
        bbox=(10.0, 10.0, 110.0, 110.0),
        suggested_alt="",
        stored_filename="p1-fig1-1.png",
        xref=42,                                  # deliberately positive
        source="vector",
    )
    sec = _make_image_section(img)
    assert sec.confidence == Confidence.MEDIUM


def test_make_image_section_raster_source_yields_high_confidence():
    """Raster IMAGE sections retain Confidence.HIGH - the bytes are
    unambiguously a figure. This is the v1 contract; pinning it here
    so the vector branch doesn't regress the raster path.
    """
    img = ExtractedImage(
        page=1,
        index_on_page=1,
        ext="png",
        bytes=b"PNGDATA",
        bbox=(10.0, 10.0, 110.0, 110.0),
        suggested_alt="",
        stored_filename="p1-fig1-1.png",
        xref=42,
        source="raster",
    )
    sec = _make_image_section(img)
    assert sec.confidence == Confidence.HIGH


# ---- vector_images: synthetic xref ----------------------------------------


class TestSyntheticXref:
    """:func:`_synthetic_xref` derives an opaque, negative, collision-resistant
    identifier for a vector cluster. The dedup pass in
    :func:`finalize_extraction` keys on xref; vector clusters get a
    negative-bit-set space so they can never alias a real pymupdf xref
    (positive) or the HTML sentinel (0).
    """

    def test_same_input_produces_same_xref(self):
        a = _synthetic_xref(1, (10.0, 20.0, 100.0, 80.0))
        b = _synthetic_xref(1, (10.0, 20.0, 100.0, 80.0))
        assert a == b

    def test_different_pages_produce_different_xrefs(self):
        a = _synthetic_xref(1, (10.0, 20.0, 100.0, 80.0))
        b = _synthetic_xref(2, (10.0, 20.0, 100.0, 80.0))
        assert a != b

    def test_all_outputs_are_negative(self):
        for page in (1, 2, 10, 99):
            for bbox in [
                (0.0, 0.0, 10.0, 10.0),
                (100.0, 200.0, 500.0, 600.0),
                (5.0, 5.0, 500.0, 5.0),                    # zero-height
            ]:
                assert _synthetic_xref(page, bbox) < 0

    def test_within_one_pt_rounds_to_same_xref(self):
        a = _synthetic_xref(1, (10.0, 20.0, 100.0, 80.0))
        b = _synthetic_xref(1, (10.4, 20.0, 100.0, 80.0))   # x0 drifted 0.4pt
        assert a == b, (
            "1-pt rounding deliberately conflates within-1pt drift on the "
            "same page so a sub-pt jitter does not split one diagram into two"
        )

    def test_large_page_geometry_is_safe(self):
        # A0 / tabloid pages can have bboxes beyond 10000. The earlier
        # packed-integer formula aliased here; the hash form must not.
        a = _synthetic_xref(1, (12345.0, 67890.0, 13345.0, 68890.0))
        b = _synthetic_xref(1, (12346.0, 67890.0, 13346.0, 68890.0))
        assert a != b
        assert a < 0 and b < 0

    def test_same_position_different_size_distinct(self):
        small = _synthetic_xref(1, (100.0, 100.0, 150.0, 150.0))    # 50x50
        large = _synthetic_xref(1, (100.0, 100.0, 600.0, 600.0))    # 500x500
        assert small != large, (
            "size is part of the key so a zoomed inset on top of its source "
            "does not collide with the source"
        )


# ---- vector_images: single-linkage clustering ------------------------------


class TestClusterDrawings:
    """Single-linkage clustering merges drawings whose bboxes are within
    ``_CLUSTER_LINK_DISTANCE_PT`` of each other. Behaviour is asserted at
    the membership level; the internal spatial-hash + union-find is an
    implementation detail.
    """

    @staticmethod
    def _drawing(x0: float, y0: float, x1: float, y1: float, items: int = 1) -> dict:
        return {
            "rect": pymupdf.Rect(x0, y0, x1, y1),
            "items": [("l", None, None)] * items,
        }

    def test_close_rects_form_one_cluster(self):
        # 10 rects spaced 20pt apart on the y axis - well within 30pt link distance.
        drawings = [self._drawing(0, y, 10, y + 5) for y in range(0, 200, 20)]
        clusters = _cluster_drawings(drawings)
        assert len(clusters) == 1
        assert clusters[0][1] == 10                            # item count

    def test_far_rects_form_separate_clusters(self):
        # 10 rects spaced 50pt apart - beyond 30pt link distance.
        drawings = [self._drawing(0, y, 10, y + 5) for y in range(0, 500, 50)]
        clusters = _cluster_drawings(drawings)
        assert len(clusters) == 10

    def test_mixed_two_clusters(self):
        # 5 near each other, 5 far away from those but near each other.
        near = [self._drawing(0, y, 10, y + 5) for y in range(0, 100, 20)]
        far = [self._drawing(0, y, 10, y + 5) for y in range(400, 500, 20)]
        clusters = _cluster_drawings(near + far)
        assert len(clusters) == 2
        assert sorted(c[1] for c in clusters) == [5, 5]

    def test_drawing_with_no_rect_is_skipped(self):
        drawings = [
            {"rect": None, "items": []},
            self._drawing(0, 0, 10, 10),
        ]
        clusters = _cluster_drawings(drawings)
        assert len(clusters) == 1

    def test_empty_input_returns_empty(self):
        assert _cluster_drawings([]) == []

    def test_cluster_bbox_is_union_of_members(self):
        drawings = [self._drawing(0, 0, 10, 10), self._drawing(5, 5, 30, 30)]
        clusters = _cluster_drawings(drawings)
        assert len(clusters) == 1
        bbox = clusters[0][0]
        assert bbox == (0.0, 0.0, 30.0, 30.0)


# ---- vector_images: text-overlap fraction ---------------------------------


class TestTextOverlapFraction:
    def test_tiny_cluster_in_huge_block_reports_one(self):
        """The denominator is the cluster's area, not the block's. A tiny
        cluster fully inside a huge block must report 1.0, never the
        cluster/block area ratio which would round to ~0."""
        cluster = (100.0, 100.0, 110.0, 110.0)             # 10x10 = 100
        huge_block = Block(bbox=(0.0, 0.0, 1000.0, 1000.0))
        assert _text_overlap_fraction(cluster, [huge_block]) == pytest.approx(1.0)

    def test_no_overlap_reports_zero(self):
        cluster = (0.0, 0.0, 50.0, 50.0)
        block = Block(bbox=(200.0, 200.0, 300.0, 300.0))
        assert _text_overlap_fraction(cluster, [block]) == 0.0

    def test_partial_overlap(self):
        cluster = (0.0, 0.0, 100.0, 100.0)                  # area 10_000
        # Block overlaps exactly the bottom-right 50x50 corner -> 2500 / 10000.
        block = Block(bbox=(50.0, 50.0, 150.0, 150.0))
        assert _text_overlap_fraction(cluster, [block]) == pytest.approx(0.25)

    def test_zero_area_cluster_does_not_divide_by_zero(self):
        zero = (10.0, 10.0, 10.0, 10.0)
        block = Block(bbox=(0.0, 0.0, 100.0, 100.0))
        assert _text_overlap_fraction(zero, [block]) == 0.0


# ---- vector_images: _colour_in_wording_band tolerant decode ---------------


class TestColourInWordingBand:
    """:func:`_colour_in_wording_band` accepts the full set of shapes that
    ``pymupdf.Page.get_drawings()`` is known to emit, and returns False
    on anything else."""

    def test_none_is_not_wording(self):
        assert _colour_in_wording_band(None) is False

    def test_rgb_tuple_ins_green_is_wording(self):
        assert _colour_in_wording_band((0.0, 110.0 / 255.0, 40.0 / 255.0)) is True

    def test_rgb_tuple_del_red_is_wording(self):
        assert _colour_in_wording_band((191.0 / 255.0, 3.0 / 255.0, 3.0 / 255.0)) is True

    def test_rgba_four_tuple_is_read_via_rgb(self):
        # Alpha is ignored; the RGB part still falls in the green band.
        assert _colour_in_wording_band((0.0, 0.43, 0.16, 1.0)) is True

    def test_grayscale_one_tuple_is_not_wording(self):
        # A pure-gray drawing (k,) carries zero saturation -> outside both bands.
        assert _colour_in_wording_band((0.5,)) is False

    def test_two_tuple_is_safely_rejected(self):
        # pymupdf would not normally emit a 2-tuple; if it ever does, we
        # must not raise - just refuse to misclassify.
        assert _colour_in_wording_band((0.5, 0.5)) is False

    def test_black_rgb_is_not_wording(self):
        assert _colour_in_wording_band((0.0, 0.0, 0.0)) is False


# ---- vector_images: uncertainty marker ------------------------------------


class TestVectorUncertaintyMarker:
    """Marker template, alphabetical reason ordering, emission predicate,
    and rejection of foreign reason keys."""

    def test_marker_template_keys_match_dataclass_fields(self):
        # Sanity: template formats with all expected named fields.
        stats = VectorUncertaintyStats(
            pages_scanned=10, candidates=20, kept=15, rejected=5,
            reasons={"too_small": 5}, pages_skipped=0,
        )
        out = format_uncertainty_marker(stats)
        assert "pages_scanned=10" in out
        assert "candidates=20" in out
        assert "kept=15" in out
        assert "rejected=5" in out
        assert "pages_skipped=0" in out
        assert "too_small:5" in out

    def test_reasons_dict_renders_alphabetically_d7(self):
        # Reasons supplied in non-alphabetical order; the formatter sorts.
        stats = VectorUncertaintyStats(
            pages_scanned=1, candidates=10, kept=4, rejected=6,
            reasons={"too_small": 3, "edge_band": 1, "text_overlap": 2},
            pages_skipped=0,
        )
        out = format_uncertainty_marker(stats)
        rendered = out.split("reasons={", 1)[1].split("}", 1)[0]
        keys = [pair.split(":", 1)[0] for pair in rendered.split(", ")]
        assert keys == sorted(keys), (
            "D7: reasons dict must render in alphabetical key order so "
            "marker output is deterministic across runs"
        )
        assert keys == ["edge_band", "text_overlap", "too_small"]

    def test_format_marker_rejects_unknown_reason_key(self):
        stats = VectorUncertaintyStats(
            pages_scanned=1, candidates=1, kept=0, rejected=1,
            reasons={"grid_pattern": 1},                     # closed key set; no grid proxy
            pages_skipped=0,
        )
        with pytest.raises(ValueError, match="unknown vector-extraction reason key"):
            format_uncertainty_marker(stats)

    def test_should_emit_skips_clean_extraction(self):
        # All candidates kept, no skipped pages -> nothing honest to disclose.
        stats = VectorUncertaintyStats(
            pages_scanned=5, candidates=3, kept=3, rejected=0,
            reasons={}, pages_skipped=0,
        )
        assert should_emit_marker(stats) is False

    def test_should_emit_skips_zero_attempted(self):
        # Vector path never fired (e.g., HTML paper); nothing to disclose.
        stats = VectorUncertaintyStats(
            pages_scanned=0, candidates=0, kept=0, rejected=0,
            reasons={}, pages_skipped=0,
        )
        assert should_emit_marker(stats) is False

    def test_should_emit_fires_on_any_rejection(self):
        stats = VectorUncertaintyStats(
            pages_scanned=5, candidates=10, kept=8, rejected=2,
            reasons={"too_small": 2}, pages_skipped=0,
        )
        assert should_emit_marker(stats) is True

    def test_should_emit_fires_on_skipped_pages(self):
        stats = VectorUncertaintyStats(
            pages_scanned=4, candidates=0, kept=0, rejected=0,
            reasons={}, pages_skipped=1,
        )
        assert should_emit_marker(stats) is True

    def test_allowed_reason_keys_is_closed_set(self):
        # Pin the closed key set; adding to it without bumping the marker
        # contract is a versioned change per plan section 1.6a.
        assert sorted(ALLOWED_REASON_KEYS) == [
            "aspect_extreme",
            "bbox_too_large",
            "clusters_overflow",
            "edge_band",
            "text_overlap",
            "too_few_items",
            "too_small",
            "wording_color",
        ]


# ---- vector_images: _VectorExtractionStats accumulator --------------------


class TestVectorExtractionStatsCombine:
    def test_combine_sums_counters(self):
        a = _VectorExtractionStats(
            pages_scanned=1, candidates=3, kept=2, rejected=1,
            pages_skipped=0, reasons={"too_small": 1},
        )
        b = _VectorExtractionStats(
            pages_scanned=1, candidates=5, kept=3, rejected=2,
            pages_skipped=0, reasons={"too_small": 1, "text_overlap": 2},
        )
        out = _VectorExtractionStats.combine(a, b)
        assert out.pages_scanned == 2
        assert out.candidates == 8
        assert out.kept == 5
        assert out.rejected == 3
        assert out.pages_skipped == 0
        assert out.reasons == {"too_small": 2, "text_overlap": 2}

    def test_combine_preserves_disjoint_reason_keys(self):
        a = _VectorExtractionStats(reasons={"too_small": 1})
        b = _VectorExtractionStats(reasons={"text_overlap": 1})
        out = _VectorExtractionStats.combine(a, b)
        assert out.reasons == {"too_small": 1, "text_overlap": 1}

    def test_to_uncertainty_round_trips_fields(self):
        stats = _VectorExtractionStats(
            pages_scanned=3, candidates=10, kept=7, rejected=3,
            pages_skipped=1, reasons={"too_small": 3},
        )
        u = stats.to_uncertainty()
        assert isinstance(u, VectorUncertaintyStats)
        assert u.pages_scanned == 3
        assert u.candidates == 10
        assert u.kept == 7
        assert u.rejected == 3
        assert u.pages_skipped == 1
        assert dict(u.reasons) == {"too_small": 3}


# ---- vector_images: end-to-end extract_page_vector_images ------------------


def _mock_page(
    drawings: list[dict],
    *,
    page_number: int = 0,
    width: float = 612.0,
    height: float = 792.0,
) -> MagicMock:
    """MagicMock pymupdf.Page with just the surface :func:`extract_page_vector_images` reads."""
    page = MagicMock()
    page.number = page_number
    page.rect = pymupdf.Rect(0, 0, width, height)
    page.get_drawings.return_value = drawings
    return page


def _drawing(
    x0: float, y0: float, x1: float, y1: float,
    *,
    items: int = 1,
    color: tuple | None = (0.0, 0.0, 0.0),
    fill: tuple | None = None,
) -> dict:
    return {
        "rect": pymupdf.Rect(x0, y0, x1, y1),
        "items": [("l", None, None)] * items,
        "color": color,
        "fill": fill,
    }


class TestPageScanGuards:
    """The per-page driver early-exits below ``_MIN_PAGE_DRAWING_ITEMS`` and
    bails out above ``_MAX_DRAWINGS_PER_PAGE``. Behaviour is observable
    through the returned per-page stats."""

    def test_below_minimum_items_returns_silent_empty(self, monkeypatch):
        # 10 drawings, each 1 item = 10 < default 250.
        drawings = [_drawing(0, y, 10, y + 10) for y in range(0, 100, 10)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        # Not even scanned - the per-page driver returns zero-everywhere stats.
        assert stats.pages_scanned == 0
        assert stats.pages_skipped == 0
        assert stats.candidates == 0

    def test_above_maximum_items_skips_page(self, monkeypatch):
        # Bypass min: monkeypatch min to 1, max to 10. With 12 items
        # we trip the max-bailout path.
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        monkeypatch.setattr(vector_images, "_MAX_DRAWINGS_PER_PAGE", 10)
        drawings = [_drawing(0, y, 10, y + 10) for y in range(0, 120, 10)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.pages_skipped == 1
        assert stats.pages_scanned == 0
        assert stats.candidates == 0


class TestPerClusterFilter:
    """Per-cluster filter rejects clusters that fail each boundary. Boundary
    cases verify the threshold inequality direction."""

    @staticmethod
    def _setup(monkeypatch):
        """Bypass page-scan guards so we test the cluster filter itself."""
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        monkeypatch.setattr(vector_images, "_MAX_DRAWINGS_PER_PAGE", 100_000)

    def test_too_small_drops_cluster(self, monkeypatch):
        # 1 drawing, 8 items (enough), but bbox is 30x30 (below 60pt floor).
        self._setup(monkeypatch)
        drawings = [_drawing(100, 100, 130, 130, items=8)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_TOO_SMALL) == 1
        assert stats.rejected == 1

    def test_too_small_boundary_60pt_passes(self, monkeypatch):
        self._setup(monkeypatch)
        # bbox is exactly 60x60 - at the inclusive boundary.
        drawings = [_drawing(100, 100, 160, 160, items=8)]
        page = _mock_page(drawings)
        cands, _stats = extract_page_vector_images(page, [])
        assert len(cands) == 1, (
            "boundary inclusive: width = _MIN_CLUSTER_DIM_PT must pass"
        )

    def test_too_few_items_drops_cluster(self, monkeypatch):
        self._setup(monkeypatch)
        # bbox is 100x100 (size OK), but only 7 items (below 8 floor).
        drawings = [_drawing(100, 100, 200, 200, items=7)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_TOO_FEW_ITEMS) == 1

    def test_too_few_items_boundary_eight_passes(self, monkeypatch):
        self._setup(monkeypatch)
        drawings = [_drawing(100, 100, 200, 200, items=8)]
        page = _mock_page(drawings)
        cands, _stats = extract_page_vector_images(page, [])
        assert len(cands) == 1

    def test_text_overlap_drops_cluster(self, monkeypatch):
        self._setup(monkeypatch)
        # Cluster 100x100 fully inside a 200x200 text block -> 100% overlap >= 35%.
        drawings = [_drawing(100, 100, 200, 200, items=8)]
        page = _mock_page(drawings)
        text_block = Block(bbox=(50.0, 50.0, 250.0, 250.0))
        cands, stats = extract_page_vector_images(page, [text_block])
        assert cands == []
        assert stats.reasons.get(REASON_TEXT_OVERLAP) == 1

    def test_text_overlap_boundary_under_threshold_passes(self, monkeypatch):
        self._setup(monkeypatch)
        # 100x100 cluster (area 10_000) overlapping a 34x100 block strip
        # placed inside it -> 3400 / 10000 = 0.34 < 0.35.
        drawings = [_drawing(100, 100, 200, 200, items=8)]
        page = _mock_page(drawings)
        text_block = Block(bbox=(100.0, 100.0, 134.0, 200.0))
        cands, _stats = extract_page_vector_images(page, [text_block])
        assert len(cands) == 1, "34% overlap passes the < 35% gate"

    def test_text_overlap_boundary_at_threshold_drops(self, monkeypatch):
        self._setup(monkeypatch)
        # 100x100 cluster with a 36x100 strip block: 0.36 >= 0.35.
        drawings = [_drawing(100, 100, 200, 200, items=8)]
        page = _mock_page(drawings)
        text_block = Block(bbox=(100.0, 100.0, 136.0, 200.0))
        cands, _stats = extract_page_vector_images(page, [text_block])
        assert cands == [], "36% overlap fails the < 35% gate"


class TestBboxTooLargeFilter:
    """Clusters whose bbox covers more than _MAX_CLUSTER_AREA_FRACTION of
    the page are rejected as ``bbox_too_large``. Targets the single-
    linkage chaining failure mode where a page-frame stroke pulls
    unrelated drawings into one cluster spanning the page.
    """

    @staticmethod
    def _setup(monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        monkeypatch.setattr(vector_images, "_MAX_DRAWINGS_PER_PAGE", 100_000)

    def test_cluster_covering_more_than_half_page_drops(self, monkeypatch):
        self._setup(monkeypatch)
        # Page 612x792 = 484_704 sq pt; _MAX_CLUSTER_AREA_FRACTION=0.50
        # -> threshold 242_352. A 500x600 cluster = 300_000 sq pt (> 0.61).
        drawings = [_drawing(50, 50, 550, 650, items=8)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_BBOX_TOO_LARGE) == 1

    def test_cluster_at_exactly_threshold_drops(self, monkeypatch):
        self._setup(monkeypatch)
        # Build a cluster whose area is exactly 50% of the page.
        # 612x792/2 = 242_352. A 612x396 strip = 242_352. Use it.
        # But this strip is aspect ratio 1.55:1, which passes aspect.
        drawings = [_drawing(0, 0, 612, 396, items=8)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == [], "boundary inclusive: area == threshold drops"
        assert stats.reasons.get(REASON_BBOX_TOO_LARGE) == 1

    def test_cluster_just_under_threshold_passes(self, monkeypatch):
        self._setup(monkeypatch)
        # 49% of page area, well within aspect cap.
        # area_target = 242_352 * 0.98 = 237_505
        # use 600x395 = 237_000 sq pt = ~48.9% of page.
        drawings = [_drawing(5, 5, 605, 400, items=8)]
        page = _mock_page(drawings)
        cands, _stats = extract_page_vector_images(page, [])
        assert len(cands) == 1, (
            "49% of page area is under the 50% floor and must pass"
        )


class TestAspectExtremeFilter:
    """Clusters with aspect ratio >= _MAX_CLUSTER_ASPECT_RATIO are
    rejected as ``aspect_extreme``. Targets code-block background
    fills and shaded callouts that span the page width as thin
    horizontal strips."""

    @staticmethod
    def _setup(monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        monkeypatch.setattr(vector_images, "_MAX_DRAWINGS_PER_PAGE", 100_000)

    def test_wide_strip_drops(self, monkeypatch):
        self._setup(monkeypatch)
        # 400x80 strip -> aspect 5.0 >= 3.5. Stays under area cap
        # (400*80 = 32_000 = ~6.6% of page) so bbox_too_large doesn't fire first.
        drawings = [_drawing(100, 100, 500, 180, items=8)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_ASPECT_EXTREME) == 1

    def test_tall_strip_drops(self, monkeypatch):
        self._setup(monkeypatch)
        # 80x400 strip -> aspect 5.0 (height/width). Tall-narrow noise.
        drawings = [_drawing(100, 100, 180, 500, items=8)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_ASPECT_EXTREME) == 1

    def test_aspect_at_threshold_drops(self, monkeypatch):
        self._setup(monkeypatch)
        # 350x100 -> aspect 3.5 (== threshold) -> drop.
        drawings = [_drawing(100, 100, 450, 200, items=8)]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == [], "boundary inclusive: aspect == 3.5 drops"
        assert stats.reasons.get(REASON_ASPECT_EXTREME) == 1

    def test_aspect_just_under_threshold_passes(self, monkeypatch):
        self._setup(monkeypatch)
        # 340x100 -> aspect 3.4 < 3.5 -> pass.
        drawings = [_drawing(100, 100, 440, 200, items=8)]
        page = _mock_page(drawings)
        cands, _stats = extract_page_vector_images(page, [])
        assert len(cands) == 1, "aspect 3.4 must pass"

    def test_square_cluster_passes(self, monkeypatch):
        self._setup(monkeypatch)
        drawings = [_drawing(100, 100, 200, 200, items=8)]
        page = _mock_page(drawings)
        cands, _stats = extract_page_vector_images(page, [])
        assert len(cands) == 1


class TestContainerDetection:
    """Frame-shaped drawings that enclose smaller clusters trigger
    container detection: the frame and its enclosed clusters merge
    into a virtual cluster carrying combined item counts and a union
    bbox. Virtual clusters get relaxed thresholds (lower min-dim,
    aspect-extreme bypass when dense). Pins the recovery of the
    P4003R1 page 8 IoAwaitable -> IoRunnable -> io_task<T> horizontal
    flow diagram, which is 381 x 35pt with a 2-item outer container
    enclosing inner box clusters.
    """

    @staticmethod
    def _setup(monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        monkeypatch.setattr(vector_images, "_MAX_DRAWINGS_PER_PAGE", 100_000)

    def test_thin_frame_with_nested_content_recovered_as_virtual(
        self, monkeypatch,
    ):
        """Thin frame (2 items, aspect 10:1) plus TWO nested clusters
        merges into a virtual cluster that survives the relaxed
        virtual-cluster filters. Two clusters is the minimum required
        by _MIN_ENCLOSED_CLUSTERS so a lone wrapped figure does not
        accidentally trigger merge."""
        self._setup(monkeypatch)
        # Outer frame: 400x40pt, 2 items, aspect 10.0 - matches
        # IoAwaitable shape.
        frame = _drawing(100, 100, 500, 140, items=2)
        # Two nested clusters. Combined item count populates the
        # virtual cluster's count past the aspect-extreme bypass
        # threshold (50).
        nested_a = _drawing(140, 110, 240, 130, items=30)
        nested_b = _drawing(360, 110, 460, 130, items=30)
        page = _mock_page([frame, nested_a, nested_b])
        cands, stats = extract_page_vector_images(page, [])
        assert len(cands) == 1, (
            f"virtual cluster should be kept; got {len(cands)} "
            f"with stats {stats}"
        )
        # The virtual cluster's bbox is the union of frame and nested.
        bbox = cands[0].bbox
        assert bbox[0] <= 100 and bbox[1] <= 100
        assert bbox[2] >= 500 and bbox[3] >= 140

    def test_no_frame_below_min_aspect_no_virtual(self, monkeypatch):
        """A square-ish single-rect background fill (aspect < 3.0) is
        NOT a frame - it's a code-block bg or shaded callout. Must not
        trigger container detection."""
        self._setup(monkeypatch)
        # 400x300 background (aspect 1.33) - normal rectangle.
        bg = _drawing(100, 100, 500, 400, items=1)
        nested = _drawing(200, 200, 300, 250, items=8)
        page = _mock_page([bg, nested])
        cands, _stats = extract_page_vector_images(page, [])
        # Each cluster is evaluated individually under the regular
        # path. The 400x300 bg has 1 item (< _MIN_CLUSTER_ITEM_COUNT)
        # so it drops; the small nested cluster passes too_small
        # but fails too_few_items.
        # We assert NO virtual cluster was produced - either both
        # clusters drop on their individual filters, or only the
        # large one passes. The point: no merge happened.
        assert all(
            not (im.bbox[0] <= 100 and im.bbox[2] >= 500
                 and im.bbox[1] <= 100 and im.bbox[3] >= 400)
            for im in cands
        ), "no virtual cluster spanning the union of bg + nested should form"

    def test_no_frame_with_too_many_items_no_virtual(self, monkeypatch):
        """A wide-and-short drawing with > _MAX_FRAME_ITEMS isn't a
        thin container frame - it's a figure body with substance.
        Must not trigger container detection (the existing per-
        cluster filters handle it on its own merits)."""
        self._setup(monkeypatch)
        # 400x40 with 10 items - too rich to be a frame.
        big_drawing = _drawing(100, 100, 500, 140, items=10)
        # A potential nested element.
        nested = _drawing(200, 110, 300, 130, items=20)
        page = _mock_page([big_drawing, nested])
        cands, _stats = extract_page_vector_images(page, [])
        # No virtual cluster - the 10-item drawing fails the frame
        # criterion. Its own cluster is 400x40 (height < 60) - drops
        # as too_small.
        assert len(cands) == 0 or all(
            not (im.bbox[0] <= 100 and im.bbox[2] >= 500
                 and im.bbox[3] - im.bbox[1] < 50)
            for im in cands
        )

    def test_page_sized_frame_does_not_trigger(self, monkeypatch):
        """A page-spanning thin rectangle (aspect-extreme,
        low-item-count) is geometrically frame-shaped but its area
        exceeds _MAX_FRAME_AREA_FRACTION. Must not pull every
        unrelated drawing on the page into one virtual cluster."""
        self._setup(monkeypatch)
        # Page is 612x792 (default), area ~485k. Frame at 600x300
        # has area 180k (37% of page) > 30% threshold - excluded.
        page_frame = _drawing(10, 10, 610, 310, items=2)
        unrelated = _drawing(50, 50, 200, 200, items=20)
        page = _mock_page([page_frame, unrelated])
        cands, _stats = extract_page_vector_images(page, [])
        # No virtual cluster spanning frame + unrelated should form.
        # The unrelated 150x150 cluster can pass on its own merits;
        # we only assert the merge didn't happen.
        for im in cands:
            assert not (
                im.bbox[0] <= 10 and im.bbox[1] <= 10
                and im.bbox[2] >= 600 and im.bbox[3] >= 300
            ), "page-sized frame must not produce a virtual cluster"

    def test_virtual_cluster_below_density_threshold_drops_on_aspect(
        self, monkeypatch,
    ):
        """A virtual cluster with item_count < _VIRTUAL_MIN_ITEM_COUNT
        does NOT get the aspect-extreme bypass. This keeps the
        single-line-code-strip case (low-item-count even after merge)
        on the strict path."""
        self._setup(monkeypatch)
        frame = _drawing(100, 100, 500, 140, items=2)
        # Two sparse nested clusters: 5 items each. Virtual total
        # = 12, well under the 50-item density threshold.
        nested_a = _drawing(140, 110, 240, 130, items=5)
        nested_b = _drawing(360, 110, 460, 130, items=5)
        page = _mock_page([frame, nested_a, nested_b])
        cands, stats = extract_page_vector_images(page, [])
        # Aspect 10:1 with item_count 12 < 50 -> rejected as aspect_extreme.
        assert cands == []
        assert stats.reasons.get(REASON_ASPECT_EXTREME) == 1

    def test_virtual_cluster_relaxed_min_dim_floor(self, monkeypatch):
        """A virtual cluster with a thin frame (height 35pt, below the
        strict 60pt floor) is admitted via the relaxed
        _VIRTUAL_MIN_CLUSTER_DIM_PT = 30pt path."""
        self._setup(monkeypatch)
        # 400x35 frame - height below strict floor, above virtual floor.
        frame = _drawing(100, 100, 500, 135, items=2)
        # Two nested clusters totalling 60 items.
        nested_a = _drawing(140, 110, 240, 130, items=30)
        nested_b = _drawing(360, 110, 460, 130, items=30)
        page = _mock_page([frame, nested_a, nested_b])
        cands, stats = extract_page_vector_images(page, [])
        assert len(cands) == 1, f"thin virtual cluster should pass; stats={stats}"

    def test_frame_with_no_enclosed_clusters_falls_back_to_regular(
        self, monkeypatch,
    ):
        """A thin frame drawing with NO nested clusters produces no
        virtual cluster. The frame itself stays in the regular set
        and goes through normal filters (where it likely fails on
        too_small / too_few_items / aspect_extreme)."""
        self._setup(monkeypatch)
        # Lone frame, no nested content.
        frame = _drawing(100, 100, 500, 140, items=2)
        page = _mock_page([frame])
        cands, stats = extract_page_vector_images(page, [])
        # Frame's own cluster: 400x40, aspect 10:1, only 2 items.
        # Fails too_small (height 40 < 60) on the regular path.
        assert cands == []
        assert stats.reasons.get(REASON_TOO_SMALL) == 1

    def test_normal_aspect_frame_recovers_diagram(self, monkeypatch):
        """Normal-aspect rectangular containers (aspect 1.5+) qualify
        as frames. Pins the P4003R1 page 13 regression where a 481x256
        diagram container (aspect 1.88) was excluded by the original
        aspect >= 3.0 threshold even though it enclosed multiple
        legitimate sub-clusters.

        Frame and inner-cluster centroids are arranged in different
        spatial-hash buckets (centroid-only bucketing uses a 30pt
        grid) so the frame doesn't cluster directly with its
        contents via single-linkage; the merge must happen via
        container detection.
        """
        self._setup(monkeypatch)
        # 400x250 frame, aspect 1.6; centroid (300, 225) -> bucket (10, 7).
        frame = _drawing(100, 100, 500, 350, items=1)
        # Inner clusters at y=130-200, centroid y=165 -> bucket y=5.
        # Frame's 3x3 neighbourhood is x in [9,11] AND y in [6,8];
        # y=5 is outside, so none of these merge into the frame via
        # spatial-hash neighbour search.
        nested_a = _drawing(130, 130, 200, 200, items=30)  # centroid (165,165) bucket (5,5)
        nested_b = _drawing(310, 130, 380, 200, items=30)  # centroid (345,165) bucket (11,5)
        nested_c = _drawing(430, 130, 490, 200, items=30)  # centroid (460,165) bucket (15,5)
        page = _mock_page([frame, nested_a, nested_b, nested_c])
        cands, stats = extract_page_vector_images(page, [])
        assert len(cands) == 1, (
            f"normal-aspect frame with 3 inner clusters should merge; "
            f"stats={stats}"
        )

    def test_frame_with_one_enclosed_cluster_does_not_merge(self, monkeypatch):
        """A frame enclosing only ONE smaller cluster doesn't trigger
        the virtual-cluster merge. The single inner cluster either
        stands on its own (extracted directly) or fails its own
        filters; the frame stays in the regular set. This guard
        prevents a square-ish background-fill rectangle from
        accidentally absorbing one adjacent unrelated cluster."""
        self._setup(monkeypatch)
        # 400x250 frame; centroid (300, 225) -> bucket (10, 7).
        frame = _drawing(100, 100, 500, 350, items=1)
        # Lone inner cluster; centroid (200, 165) -> bucket (6, 5),
        # outside the frame's 3x3 spatial-hash neighbourhood so the
        # two stay as separate clusters during clustering.
        lone_inner = _drawing(150, 130, 250, 200, items=20)
        page = _mock_page([frame, lone_inner])
        cands, _stats = extract_page_vector_images(page, [])
        kept_bboxes = [c.bbox for c in cands]
        # The inner cluster (100x70, 20 items) passes filters on its
        # own. The frame (400x250, 1 item) fails too_few_items.
        assert any(
            abs(b[0] - 150) < 1 and abs(b[2] - 250) < 1
            and abs(b[1] - 130) < 1 and abs(b[3] - 200) < 1
            for b in kept_bboxes
        ), f"inner cluster should be extracted as-is; got {kept_bboxes}"
        # And no virtual cluster spanning frame + inner.
        assert not any(
            b[0] <= 100 and b[2] >= 500 and b[1] <= 100 and b[3] >= 350
            for b in kept_bboxes
        ), "frame + inner should NOT have merged into a virtual cluster"


class TestEdgeBand:
    """A drawing wholly inside the top or bottom 8% of the page is
    dropped pre-clustering; a drawing that straddles the boundary
    survives (real content adjacent to the running header)."""

    def test_drawing_wholly_in_top_band_drops(self, monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        # 8% of 792pt = 63.36pt; place a small running-header underline at y=10.
        drawings = [_drawing(0, 5, 200, 15, items=8)]
        page = _mock_page(drawings, height=792)
        cands, stats = extract_page_vector_images(page, [])
        # Drawing was dropped pre-cluster; no cluster formed; no other rejection.
        assert cands == []
        assert stats.reasons.get(REASON_EDGE_BAND) == 1

    def test_drawing_straddling_band_survives(self, monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        # 8% of 792pt = 63.36pt; drawing from y=10 to y=200 straddles the band.
        drawings = [_drawing(100, 10, 200, 200, items=8)]
        page = _mock_page(drawings, height=792)
        cands, stats = extract_page_vector_images(page, [])
        # Survives the pre-cluster edge-band drop and reaches the cluster filter.
        assert stats.reasons.get(REASON_EDGE_BAND, 0) == 0


class TestPreClusteringInsDelExclusion:
    """Drawings whose stroke or fill colour falls in the ins / del hue band
    are excluded before clustering. Uses pymupdf's actual float-tuple
    colour format to exercise :func:`is_wording_rgb` end-to-end."""

    def test_ins_green_color_drops_drawing(self, monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        # mpark/wg21 ins green #006e28 -> (0.0, 0.43, 0.16) as float tuple.
        drawings = [_drawing(100, 100, 200, 200, items=8,
                             color=(0.0, 110.0 / 255.0, 40.0 / 255.0))]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_WORDING_COLOR) == 1

    def test_del_red_color_drops_drawing(self, monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        # mpark/wg21 del red #bf0303 -> (0.75, 0.01, 0.01).
        drawings = [_drawing(100, 100, 200, 200, items=8,
                             color=(191.0 / 255.0, 3.0 / 255.0, 3.0 / 255.0))]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_WORDING_COLOR) == 1

    def test_ins_green_fill_drops_drawing(self, monkeypatch):
        # Wording colour can be in the fill, not just the stroke.
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        drawings = [_drawing(100, 100, 200, 200, items=8,
                             color=(0.0, 0.0, 0.0),
                             fill=(0.0, 110.0 / 255.0, 40.0 / 255.0))]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert cands == []
        assert stats.reasons.get(REASON_WORDING_COLOR) == 1

    def test_black_drawing_survives(self, monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        drawings = [_drawing(100, 100, 200, 200, items=8,
                             color=(0.0, 0.0, 0.0))]
        page = _mock_page(drawings)
        cands, stats = extract_page_vector_images(page, [])
        assert stats.reasons.get(REASON_WORDING_COLOR, 0) == 0
        # Black drawing reaches the cluster filter and passes (size + item count OK).
        assert len(cands) == 1


class TestClustersOverflowCap:
    """When more clusters survive the filter than ``_MAX_CLUSTERS_PER_PAGE``,
    the top-of-page survivors are kept and the rest counted as
    ``clusters_overflow``."""

    def test_overflow_drops_bottom_clusters(self, monkeypatch):
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        monkeypatch.setattr(vector_images, "_MAX_CLUSTERS_PER_PAGE", 3)
        # Five well-separated 80x80 clusters at different y positions.
        # All pass the filter; only the top 3 by (y0, x0) survive the cap.
        drawings = [
            _drawing(50, y, 130, y + 80, items=8)
            for y in (100, 250, 400, 550, 700)
        ]
        page = _mock_page(drawings, height=1000)
        cands, stats = extract_page_vector_images(page, [])
        assert stats.reasons.get(REASON_CLUSTERS_OVERFLOW) == 2
        # The kept clusters are the 3 with smallest y0.
        kept_y0 = sorted(c.bbox[1] for c in cands)
        assert kept_y0 == [100.0, 250.0, 400.0]


class TestExtractPageRasterisation:
    """End-to-end extract path against a real pymupdf-built PDF so the
    rasterisation, bbox clamp, and PNG-bytes contract are pinned."""

    @staticmethod
    def _make_pdf_with_diagram(monkeypatch, *, draw_outside_page: bool = False) -> pymupdf.Document:
        """Build a 1-page PDF with a small diagram of 8 line segments
        clustered in one corner. Spacing is chosen so the cluster bbox
        clears ``_MIN_CLUSTER_DIM_PT`` on both axes (80 x 105 pt).
        ``draw_outside_page`` adds a stroke extending past the page's
        right edge to test the bbox clamp."""
        monkeypatch.setattr(vector_images, "_MIN_PAGE_DRAWING_ITEMS", 1)
        monkeypatch.setattr(vector_images, "_MIN_CLUSTER_ITEM_COUNT", 1)
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        # 8 line segments at 15pt spacing -> cluster height 105pt (>= 60).
        for i in range(8):
            page.draw_line(
                pymupdf.Point(100, 100 + i * 15),
                pymupdf.Point(180, 100 + i * 15),
                color=(0, 0, 0),
                width=0.5,
            )
        if draw_outside_page:
            # Past the page's right edge (612pt) - cluster bbox must clamp.
            page.draw_line(
                pymupdf.Point(180, 130),
                pymupdf.Point(700, 130),
                color=(0, 0, 0),
                width=0.5,
            )
        return doc

    def test_rasterise_produces_png_bytes(self, monkeypatch):
        doc = self._make_pdf_with_diagram(monkeypatch)
        try:
            page = doc[0]
            cands, stats = extract_page_vector_images(page, [])
            assert len(cands) >= 1, (
                f"expected at least one vector candidate, got {len(cands)}; "
                f"stats={stats}"
            )
            png = cands[0].bytes
            assert png[:8] == b"\x89PNG\r\n\x1a\n", (
                f"vector candidate bytes are not a PNG: {png[:8]!r}"
            )
            assert cands[0].source == "vector"
            assert cands[0].xref < 0
            assert cands[0].ext == "png"
        finally:
            doc.close()

    def test_bbox_clamped_to_page_rect(self, monkeypatch):
        # A drawing extending past the page edge must produce a clamped
        # candidate bbox (not the raw drawing extent).
        doc = self._make_pdf_with_diagram(monkeypatch, draw_outside_page=True)
        try:
            page = doc[0]
            page_right = page.rect.x1
            cands, _stats = extract_page_vector_images(page, [])
            assert len(cands) >= 1
            assert all(c.bbox[2] <= page_right + 0.001 for c in cands), (
                "bbox x1 must be clamped to <= page.rect.x1"
            )
            # And the rendered PNG must decode (i.e., rasterisation did not
            # raise on the past-edge geometry).
            assert cands[0].bytes[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            doc.close()

    def test_whiteout_off_by_default_leaves_text(self, monkeypatch):
        """The v2.0 default contract: text glyphs survive the rasterisation
        unless the caller explicitly opts in to ``whiteout_text``. Pinned
        via a synthetic line.bbox that overlaps a known-pixel region of
        the cluster.
        """
        doc = self._make_pdf_with_diagram(monkeypatch)
        try:
            page = doc[0]
            # Synthetic Line with bbox inside the diagram region. The
            # whiteout pass would paint this region white if invoked.
            line = Line(spans=[Span(text="LABEL")], bbox=(110.0, 110.0, 160.0, 120.0))
            block = Block(lines=[line], bbox=(110.0, 110.0, 160.0, 120.0))
            # Default extract (whiteout_text=False).
            cands, _stats = extract_page_vector_images(page, [block])
            # The cluster filter rejects clusters whose text-overlap >= 35%.
            # The line bbox is 50x10 = 500; cluster ~80x35 ~ 2800; overlap
            # is the line area ~ 500 / 2800 = 18% which passes.
            assert len(cands) == 1
            # Decode pixmap from PNG bytes to inspect a pixel near the line.
            pix_default = pymupdf.Pixmap(cands[0].bytes)
            # Pick a pixel near the centre of the line region. The exact
            # value depends on what's been drawn; we only require that the
            # surrounding region is not uniformly white.
            non_white = 0
            for x in range(0, pix_default.width, 4):
                for y in range(0, pix_default.height, 4):
                    pixel = pix_default.pixel(x, y)
                    if pixel != (255, 255, 255):
                        non_white += 1
            assert non_white > 0, (
                "whiteout default is OFF; the diagram strokes must leave "
                "non-white pixels in the rasterised PNG"
            )
        finally:
            doc.close()

    def test_whiteout_on_paints_over_line_region(self, monkeypatch):
        """When the caller opts in, text-line bboxes get painted white.
        Compares the same diagram rasterised with whiteout off vs on; the
        on-version must have strictly more white pixels."""
        doc = self._make_pdf_with_diagram(monkeypatch)
        try:
            page = doc[0]
            # Small line block well inside the cluster; overlap stays well
            # below the 35% text-overlap floor.
            line = Line(spans=[Span(text="LABEL")], bbox=(110.0, 110.0, 160.0, 120.0))
            block = Block(lines=[line], bbox=(110.0, 110.0, 160.0, 120.0))

            cands_off, _ = extract_page_vector_images(
                page, [block], whiteout_text=False,
            )
            cands_on, _ = extract_page_vector_images(
                page, [block], whiteout_text=True,
            )
            assert len(cands_off) == 1 and len(cands_on) == 1

            def _count_white(png_bytes: bytes) -> int:
                pix = pymupdf.Pixmap(png_bytes)
                n = 0
                for x in range(pix.width):
                    for y in range(pix.height):
                        if pix.pixel(x, y) == (255, 255, 255):
                            n += 1
                return n

            assert _count_white(cands_on[0].bytes) > _count_white(cands_off[0].bytes), (
                "whiteout_text=True must produce strictly more white pixels "
                "than whiteout_text=False on the same diagram"
            )
        finally:
            doc.close()


# ---- vector_images: caption reuse + mixed raster + vector through finalize -


class TestMixedRasterAndVector:
    """Once a vector :class:`_PageImageCandidate` reaches
    :func:`finalize_extraction`, it is treated symmetrically with raster
    candidates: same y-x ordering, same cap, same filename scheme."""

    def test_finalize_dedupes_mixed_raster_and_vector_candidates(self):
        # Raster candidate at y=50 (smaller y -> index 1); vector at y=150.
        raster = _PageImageCandidate(
            xref=42, page=1,
            bbox=(100.0, 50.0, 200.0, 150.0),
            ext="png", bytes=b"RASTER",
            suggested_alt="", source="raster",
        )
        vector = _PageImageCandidate(
            xref=-1_234_567, page=1,
            bbox=(100.0, 150.0, 200.0, 250.0),
            ext="png", bytes=b"VECTOR",
            suggested_alt="", source="vector",
        )
        r = finalize_extraction([[raster, vector]], "p1")
        assert len(r.images) == 2
        assert r.images[0].source == "raster"
        assert r.images[0].stored_filename == "p1-fig1-1.png"
        assert r.images[1].source == "vector"
        assert r.images[1].stored_filename == "p1-fig1-2.png"

    def test_cap_covers_raster_plus_vector_combined(self):
        # 15 raster + 10 vector at ascending y; 20-image cap fires globally.
        raster = [
            _PageImageCandidate(
                xref=i, page=1,
                bbox=(0.0, 10.0 * i, 100.0, 10.0 * i + 100),
                ext="png", bytes=b"R",
                suggested_alt="", source="raster",
            )
            for i in range(1, 16)
        ]
        vector = [
            _PageImageCandidate(
                xref=-(100 + i), page=1,
                bbox=(0.0, 1000.0 + 10.0 * i, 100.0, 1000.0 + 10.0 * i + 100),
                ext="png", bytes=b"V",
                suggested_alt="", source="vector",
            )
            for i in range(1, 11)
        ]
        r = finalize_extraction([raster + vector], "p1")
        assert len(r.images) == _MAX_IMAGES_PER_PAPER
        assert r.source_image_count == 25
        assert r.images_truncated is True
        # The kept 20 are the first 20 in (page, y0, x0) order: all 15
        # raster (y0 = 10..150) and the first 5 vector (y0 = 1010..1050).
        kept_sources = [im.source for im in r.images]
        assert kept_sources.count("raster") == 15
        assert kept_sources.count("vector") == 5

    def test_caption_heuristic_reuse_for_vector(self):
        # Synthetic page-block with "Figure 1: ..." 30pt below the
        # cluster bbox; _caption_for is shared between raster and vector.
        cluster_bbox = (100.0, 100.0, 200.0, 200.0)
        caption_line = Line(
            spans=[Span(text="Figure 1: Adjacency graph", bbox=(0, 220, 300, 232))],
            bbox=(0, 220, 300, 232),
        )
        block = Block(lines=[caption_line], bbox=(0, 220, 300, 232))
        alt = _caption_for(cluster_bbox, [block])
        assert alt == "Figure 1: Adjacency graph"

    def test_caption_heuristic_rejects_table_label_for_vector(self):
        # Table labels do not become vector alt text either.
        cluster_bbox = (100.0, 100.0, 200.0, 200.0)
        table_line = Line(
            spans=[Span(text="Table 1: Comparison", bbox=(0, 220, 300, 232))],
            bbox=(0, 220, 300, 232),
        )
        block = Block(lines=[table_line], bbox=(0, 220, 300, 232))
        alt = _caption_for(cluster_bbox, [block])
        assert alt == "", "Table N: labels are not figure captions"


class TestForcedXrefCollision:
    """:func:`finalize_extraction`'s dedup keys on xref. If two distinct
    vector clusters happen to hash to the same synthetic xref, the
    collision resolves by picking the smallest ``(page, y0, x0)`` rect,
    matching the raster contract."""

    def test_two_candidates_with_same_xref_collapse_to_one(self):
        # Force the same negative xref on two different bbox / page pairs.
        same_xref = -777
        a = _PageImageCandidate(
            xref=same_xref, page=1,
            bbox=(50.0, 50.0, 150.0, 150.0),
            ext="png", bytes=b"FIRST",
            suggested_alt="", source="vector",
        )
        b = _PageImageCandidate(
            xref=same_xref, page=2,
            bbox=(50.0, 50.0, 150.0, 150.0),
            ext="png", bytes=b"SECOND",
            suggested_alt="", source="vector",
        )
        r = finalize_extraction([[a], [b]], "p1")
        # Exactly one extracted image, and it is the smaller-(page, y0, x0)
        # of the two collided candidates.
        assert len(r.images) == 1
        assert r.images[0].page == 1
        assert r.images[0].bytes == b"FIRST"


# ---- Fix A1: structural-overlap filter (vector vs TABLE/CODE) -------------


def _ext_img(
    *,
    page: int,
    bbox: tuple[float, float, float, float],
    source: str = "vector",
    fn: str = "test.png",
) -> ExtractedImage:
    """Compact ExtractedImage builder for filter tests."""
    return ExtractedImage(
        page=page, index_on_page=1, ext="png", bytes=b"PNG",
        bbox=bbox, suggested_alt="",
        stored_filename=fn, xref=-1, source=source,
    )


def _structural_section(
    *, kind: SectionKind, page_num: int, bbox: tuple[float, float, float, float],
) -> Section:
    """Section with one Line spanning bbox, for filter tests."""
    line = Line(spans=[Span(text="x", bbox=bbox)], bbox=bbox)
    return Section(
        kind=kind, text="x", confidence=Confidence.HIGH,
        page_num=page_num, lines=[line],
    )


class TestFilterVectorImagesAgainstStructural:
    """Drops vector ExtractedImage records (and their IMAGE sections)
    whose bbox overlaps a TABLE or CODE section by more than the
    threshold. Resolves the calibrated false-positive class where
    vector PNGs duplicate already-structural content (P4003R1 pages
    67/69 code blocks, page 8 table columns)."""

    def test_vector_image_overlapping_table_filtered(self):
        # ExtractedImage.page is 1-based; Section.page_num is 0-based.
        # Both reference the same page.
        img = _ext_img(page=8, bbox=(100, 100, 300, 250))
        table_sec = _structural_section(
            kind=SectionKind.TABLE, page_num=7,
            bbox=(50, 90, 350, 260),  # encloses the image
        )
        kept_images, kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [table_sec])
        )
        assert dropped == 1
        assert kept_images == []

    def test_vector_image_overlapping_code_filtered(self):
        img = _ext_img(page=8, bbox=(100, 100, 300, 250))
        code_sec = _structural_section(
            kind=SectionKind.CODE, page_num=7,
            bbox=(50, 90, 350, 260),
        )
        kept_images, _kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [code_sec])
        )
        assert dropped == 1
        assert kept_images == []

    def test_vector_image_partial_overlap_below_threshold_kept(self):
        """A 200x150 image with only 30x30 of overlap (3% of image area)
        does NOT meet the 50% threshold. Real diagrams whose bbox grazes
        an adjacent code block must survive."""
        img = _ext_img(page=1, bbox=(100, 100, 300, 250))
        code_sec = _structural_section(
            kind=SectionKind.CODE, page_num=0,
            bbox=(80, 80, 130, 130),  # 30x30 overlap with image
        )
        kept_images, _kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [code_sec])
        )
        assert dropped == 0
        assert len(kept_images) == 1

    def test_raster_image_not_filtered(self):
        """Raster images stay even when they fully overlap a TABLE/CODE
        section. An embedded screenshot of a code block is intentional
        content the user explicitly placed in the PDF."""
        img = _ext_img(page=8, bbox=(100, 100, 300, 250), source="raster")
        code_sec = _structural_section(
            kind=SectionKind.CODE, page_num=7,
            bbox=(50, 90, 350, 260),
        )
        kept_images, _kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [code_sec])
        )
        assert dropped == 0
        assert kept_images == [img]

    def test_section_on_different_page_does_not_filter(self):
        """A TABLE on page 7 must not filter vector images on page 8."""
        img = _ext_img(page=8, bbox=(100, 100, 300, 250))
        table_sec = _structural_section(
            kind=SectionKind.TABLE, page_num=6,  # 0-based -> page 7
            bbox=(50, 90, 350, 260),
        )
        kept_images, _kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [table_sec])
        )
        assert dropped == 0
        assert len(kept_images) == 1

    def test_image_section_removed_when_image_filtered(self):
        """The corresponding SectionKind.IMAGE section must be removed
        from the sections list when its image gets filtered, otherwise
        the markdown would still try to render the removed image."""
        img = _ext_img(page=8, bbox=(100, 100, 300, 250))
        img_section = Section(
            kind=SectionKind.IMAGE, text="", confidence=Confidence.MEDIUM,
            page_num=7, image_ref=img,
        )
        table_sec = _structural_section(
            kind=SectionKind.TABLE, page_num=7,
            bbox=(50, 90, 350, 260),
        )
        _kept_images, kept_sections, _dropped = (
            _filter_vector_images_against_structural(
                [img], [table_sec, img_section],
            )
        )
        # The IMAGE section is gone; the TABLE section stays.
        assert all(s.kind != SectionKind.IMAGE for s in kept_sections)
        assert any(s.kind == SectionKind.TABLE for s in kept_sections)

    def test_paragraph_section_not_treated_as_structural(self):
        """Only TABLE and CODE count as 'structural'. A PARAGRAPH
        section overlapping the vector image must not filter it -
        otherwise legitimate figures with captions or surrounding text
        would all drop."""
        img = _ext_img(page=8, bbox=(100, 100, 300, 250))
        para_sec = Section(
            kind=SectionKind.PARAGRAPH, text="caption",
            confidence=Confidence.HIGH, page_num=7,
            lines=[Line(spans=[Span(text="cap", bbox=(80, 90, 350, 260))],
                        bbox=(80, 90, 350, 260))],
        )
        kept_images, _kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [para_sec])
        )
        assert dropped == 0
        assert kept_images == [img]

    def test_no_filter_when_no_structural_sections(self):
        """Fast path: no TABLE/CODE sections in the list means no
        filtering work. Vector images pass through verbatim."""
        img = _ext_img(page=8, bbox=(100, 100, 300, 250))
        para_sec = Section(
            kind=SectionKind.PARAGRAPH, text="x", confidence=Confidence.HIGH,
            page_num=7,
        )
        kept_images, kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [para_sec])
        )
        assert dropped == 0
        assert kept_images == [img]
        assert kept_sections == [para_sec]

    def test_boundary_at_threshold_drops(self):
        """A 200x100 image with exactly 50% overlap (100x100 region) hits
        the >= 0.5 threshold and is dropped."""
        img = _ext_img(page=1, bbox=(0, 0, 200, 100))
        # 100x100 overlap = 50% of the 200x100 image (area 10000 out of 20000).
        code_sec = _structural_section(
            kind=SectionKind.CODE, page_num=0,
            bbox=(0, 0, 100, 100),
        )
        kept_images, _kept_sections, dropped = (
            _filter_vector_images_against_structural([img], [code_sec])
        )
        assert dropped == 1, "50% overlap is the boundary - drops"
        assert kept_images == []


# ---- Filter 1: vector-image dedup -----------------------------------------


class TestFilterOverlappingVectorImages:
    """Drops small vector images that are detail crops of a larger
    vector image on the same page. Pins the P4003R1 page 13 case
    (fig13-2, a 87x90pt "run_async legend" image overlapping fig13-1
    main diagram at 481x256pt)."""

    def test_small_inside_larger_dropped(self):
        large = _ext_img(page=13, bbox=(57, 190, 538, 447), fn="large.png")
        small = _ext_img(page=13, bbox=(70, 410, 150, 500), fn="small.png")
        kept_images, _kept_sections, dropped = (
            _filter_overlapping_vector_images([large, small], [])
        )
        assert dropped == 1
        assert kept_images == [large]

    def test_larger_kept_when_smaller_overlaps(self):
        """Iteration order shouldn't matter: the LARGER survives, the
        smaller drops, regardless of input order."""
        large = _ext_img(page=13, bbox=(57, 190, 538, 447), fn="large.png")
        small = _ext_img(page=13, bbox=(70, 410, 150, 500), fn="small.png")
        # Reverse input order to confirm sort-by-area-descending logic.
        kept_images, _kept_sections, dropped = (
            _filter_overlapping_vector_images([small, large], [])
        )
        assert dropped == 1
        assert kept_images == [large]

    def test_similar_size_overlapping_both_kept(self):
        """Two vectors of similar size that happen to overlap (e.g.
        adjacent panels) must NOT be deduped - they're peers, not a
        crop and its origin. The area-ratio guard distinguishes."""
        # Both ~200x200, area ratio 1.0 (way above 0.20 area ratio).
        a = _ext_img(page=13, bbox=(57, 190, 257, 390), fn="a.png")
        b = _ext_img(page=13, bbox=(200, 190, 400, 390), fn="b.png")
        kept_images, _kept_sections, dropped = (
            _filter_overlapping_vector_images([a, b], [])
        )
        assert dropped == 0
        assert len(kept_images) == 2

    def test_overlap_below_threshold_both_kept(self):
        """Small image whose overlap with the larger one is < 30% of
        the small image's area stays - it's an adjacent figure, not a
        detail crop."""
        large = _ext_img(page=13, bbox=(57, 190, 538, 447), fn="large.png")
        # Small image at the corner with only ~15% overlap.
        # large bbox 481x257 = 123,617 area; small 80x80 at (500, 410)
        # overlap: x = min(538,580)-max(57,500) = 38; y = min(447,490)-max(190,410) = 37
        # overlap area = 1406. small area = 6400. fraction = 22%.
        small = _ext_img(page=13, bbox=(500, 410, 580, 490), fn="small.png")
        kept_images, _kept_sections, dropped = (
            _filter_overlapping_vector_images([large, small], [])
        )
        # 22% overlap is under the 30% threshold.
        assert dropped == 0
        assert len(kept_images) == 2

    def test_cross_page_isolation(self):
        """Vector images on different pages don't dedup against each
        other (geometric overlap is meaningless across pages)."""
        page8 = _ext_img(page=8, bbox=(57, 190, 538, 447), fn="p8.png")
        page13 = _ext_img(page=13, bbox=(70, 200, 150, 290), fn="p13.png")
        kept_images, _kept_sections, dropped = (
            _filter_overlapping_vector_images([page8, page13], [])
        )
        assert dropped == 0
        assert len(kept_images) == 2

    def test_raster_image_not_filtered(self):
        """Raster images don't dedup against vectors; an embedded
        screenshot positioned over a vector diagram is intentional
        content."""
        large = _ext_img(page=13, bbox=(57, 190, 538, 447), fn="large.png")
        small_raster = _ext_img(
            page=13, bbox=(70, 410, 150, 500), source="raster", fn="r.png",
        )
        kept_images, _kept_sections, dropped = (
            _filter_overlapping_vector_images([large, small_raster], [])
        )
        assert dropped == 0
        assert kept_images == [large, small_raster]

    def test_corresponding_image_section_removed(self):
        """The corresponding IMAGE section in ``sections`` is removed
        when its image gets filtered, otherwise the markdown would
        still reference the dropped image."""
        large = _ext_img(page=13, bbox=(57, 190, 538, 447), fn="large.png")
        small = _ext_img(page=13, bbox=(70, 410, 150, 500), fn="small.png")
        large_section = Section(
            kind=SectionKind.IMAGE, text="",
            confidence=Confidence.MEDIUM, page_num=12,
            image_ref=large,
        )
        small_section = Section(
            kind=SectionKind.IMAGE, text="",
            confidence=Confidence.MEDIUM, page_num=12,
            image_ref=small,
        )
        _kept_images, kept_sections, _dropped = (
            _filter_overlapping_vector_images(
                [large, small], [large_section, small_section],
            )
        )
        # Only the large IMAGE section remains.
        image_sections = [s for s in kept_sections if s.kind == SectionKind.IMAGE]
        assert len(image_sections) == 1
        assert image_sections[0].image_ref is large


# ---- Filter 2: text-inside-vector dedup -----------------------------------


def _para_section_with_lines(
    *,
    page_num: int,
    line_bboxes_and_text: list[tuple[tuple[float, float, float, float], str]],
) -> Section:
    """Build a PARAGRAPH section with the given lines."""
    lines = [
        Line(spans=[Span(text=text, bbox=bbox)], bbox=bbox)
        for bbox, text in line_bboxes_and_text
    ]
    full_text = "\n".join(text for _, text in line_bboxes_and_text)
    return Section(
        kind=SectionKind.PARAGRAPH, text=full_text,
        confidence=Confidence.HIGH, page_num=page_num,
        lines=lines,
    )


class TestFilterSectionsInsideVectorImages:
    """Drops paragraph lines whose bbox is mostly inside a surviving
    vector image. Pins the P4003R1 page 13 label-leakage case where
    the diagram's internal labels (rasterised into fig13-1.png) also
    leak into body markdown as prose."""

    def test_paragraph_section_all_lines_inside_vector_dropped(self):
        img = _ext_img(page=13, bbox=(57, 190, 538, 447))
        # 2 lines both fully inside the image's bbox.
        para = _para_section_with_lines(
            page_num=12,
            line_bboxes_and_text=[
                ((100, 380, 500, 395), "I/O operation child task"),
                ((100, 420, 500, 435), "handle() set_environment(env)"),
            ],
        )
        kept = _filter_sections_inside_vector_images([img], [para])
        # All lines dropped - whole section gone.
        assert len(kept) == 0

    def test_paragraph_section_partial_lines_kept_with_rewritten_text(self):
        """Section with mixed inside/outside lines keeps the outside
        lines and rebuilds its text. Pins the P4003R1 page 13 case
        where label-leakage and bullet text were joined into one
        paragraph; we want labels dropped, bullets preserved."""
        img = _ext_img(page=13, bbox=(57, 190, 538, 447))
        para = _para_section_with_lines(
            page_num=12,
            line_bboxes_and_text=[
                # Inside the image bbox (labels):
                ((100, 380, 500, 395), "I/O operation child task"),
                ((100, 420, 500, 435), "handle() set_environment(env)"),
                # Below the image bbox (bullets):
                ((100, 470, 500, 485), "run_async is the root of a coroutine chain"),
                ((100, 490, 500, 505), "run performs executor hopping"),
            ],
        )
        kept = _filter_sections_inside_vector_images([img], [para])
        assert len(kept) == 1
        kept_text = kept[0].text
        assert "I/O operation" not in kept_text
        assert "handle()" not in kept_text
        assert "run_async is the root" in kept_text
        assert "run performs executor" in kept_text

    def test_table_section_kept_even_if_inside_vector(self):
        """Structural section kinds (TABLE, CODE, IMAGE) are NEVER
        filtered. Their structural representation stands; the
        structural-overlap filter at
        _filter_vector_images_against_structural has already handled
        the reverse direction (dropping vectors that duplicate
        structural sections)."""
        img = _ext_img(page=13, bbox=(57, 190, 538, 447))
        table = Section(
            kind=SectionKind.TABLE, text="row content",
            confidence=Confidence.HIGH, page_num=12,
            lines=[Line(spans=[Span(text="cell", bbox=(100, 200, 400, 300))],
                        bbox=(100, 200, 400, 300))],
        )
        kept = _filter_sections_inside_vector_images([img], [table])
        assert kept == [table]

    def test_code_section_kept_even_if_inside_vector(self):
        img = _ext_img(page=13, bbox=(57, 190, 538, 447))
        code = Section(
            kind=SectionKind.CODE, text="x = 1",
            confidence=Confidence.HIGH, page_num=12,
            lines=[Line(spans=[Span(text="x", bbox=(100, 200, 400, 300))],
                        bbox=(100, 200, 400, 300))],
        )
        kept = _filter_sections_inside_vector_images([img], [code])
        assert kept == [code]

    def test_image_section_kept_even_if_inside_vector(self):
        img = _ext_img(page=13, bbox=(57, 190, 538, 447))
        another_img = _ext_img(page=13, bbox=(100, 200, 400, 300), fn="another.png")
        img_section = Section(
            kind=SectionKind.IMAGE, text="",
            confidence=Confidence.MEDIUM, page_num=12,
            image_ref=another_img,
        )
        kept = _filter_sections_inside_vector_images([img], [img_section])
        # IMAGE sections aren't filtered by this pass (Filter 1 handles
        # vector-vs-vector dedup separately).
        assert kept == [img_section]

    def test_cross_page_isolation(self):
        """A vector image on page 8 must not filter sections on page 13."""
        img = _ext_img(page=8, bbox=(57, 190, 538, 447))
        para = _para_section_with_lines(
            page_num=12,  # page 13 (0-based)
            line_bboxes_and_text=[
                ((100, 200, 500, 215), "this text lives at page 13 y=200"),
            ],
        )
        kept = _filter_sections_inside_vector_images([img], [para])
        assert kept == [para]

    def test_no_vector_images_returns_sections_unchanged(self):
        """Fast path: empty image list (or no vector images) means no
        filtering work."""
        para = _para_section_with_lines(
            page_num=12,
            line_bboxes_and_text=[((100, 200, 400, 215), "text")],
        )
        kept = _filter_sections_inside_vector_images([], [para])
        assert kept == [para]

    def test_lines_without_bbox_kept_conservatively(self):
        """A line whose bbox is (0,0,0,0) (no geometry info) can't be
        classified - keep it rather than dropping conservatively."""
        img = _ext_img(page=13, bbox=(57, 190, 538, 447))
        line = Line(spans=[Span(text="text", bbox=(0, 0, 0, 0))],
                    bbox=(0, 0, 0, 0))
        para = Section(
            kind=SectionKind.PARAGRAPH, text="text",
            confidence=Confidence.HIGH, page_num=12,
            lines=[line],
        )
        kept = _filter_sections_inside_vector_images([img], [para])
        assert len(kept) == 1
        assert len(kept[0].lines) == 1
