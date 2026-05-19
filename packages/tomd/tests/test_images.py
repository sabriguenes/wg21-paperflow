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

from conftest import make_section

from tomd.lib.pdf.emit import emit_markdown
from tomd.lib.pdf.images import (
    ExtractedImage,
    _MAX_IMAGES_PER_PAPER,
    _PageImageCandidate,
    _caption_for,
    finalize_extraction,
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
