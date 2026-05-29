#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for the glyph-placeholder pass (sub-threshold raster glyph -> U+FFFD)."""

from pathlib import Path
import pymupdf
import re

from tomd.lib.html import convert_html
from tomd.lib.pdf.glyphs import (
    _GLYPH_COINCIDENT_TOLERANCE_PT as TOL,
    GLYPH_FONT_SENTINEL,
    UNKNOWN_GLYPH,
    GlyphCandidate,
    GlyphPassStats,
    _is_emoji_presentation,
    collect_glyph_candidates,
    collect_text_emoji_bboxes,
    drop_glyphs_in_code_and_tables,
    filter_coincident,
    glyph_to_char,
    inject_glyph_spans,
    is_glyph_only_block,
)
from tomd.lib.pdf.pipeline import run_pipeline
from tomd.lib.pdf.types import Block, Confidence, Line, Section, SectionKind, Span
from tomd.lib.pdf.structure import compare_extractions, structure_sections

from tomd.lib.check_content import _normalize
from tomd.lib.pdf.structure import _detect_lists_by_position
from tomd.lib.pdf.types import Section, SectionKind
from tomd.lib.pdf.spans import normalize_spans
from tomd.lib.pdf.mono import classify_monospace
from tomd.lib.pdf.structure import _block_words
from tomd.lib.pdf.qa import compute_metrics
from tomd.lib.pdf.cleanup import _collapse_spaces, cleanup_text
from tomd.lib.pdf.emit import _render_wording_section, emit_markdown
from tomd.lib.pdf.mono import propagate_monospace

# -- emoji property detection -------------------------------------------------


def test_emoji_presentation_covers_scattered_codepoints():
    # U+23E9 (one of N5007's own glyphs) is outside the naive
    # 0x1F300-/0x2600- ranges but is Emoji_Presentation.
    assert _is_emoji_presentation(0x23E9)
    assert _is_emoji_presentation(0x1F64F)  # folded hands
    assert _is_emoji_presentation(0x2B50)  # star
    assert _is_emoji_presentation(0x231A)  # watch


def test_emoji_presentation_excludes_text_default():
    # Text-default codepoints (emoji only when VS16-qualified) and plain
    # ASCII must not be in the base set.
    assert not _is_emoji_presentation(0x25B6)  # play (text default)
    assert not _is_emoji_presentation(0x0023)  # '#'
    assert not _is_emoji_presentation(ord("A"))


def test_glyph_to_char_is_placeholder():
    assert glyph_to_char(GlyphCandidate(page=1, bbox=(0, 0, 5, 5))) == UNKNOWN_GLYPH


# -- coincidence filter -------------------------------------------------------


def test_filter_coincident_drops_overlapping_rect():
    # A glyph centred on a text-emoji bbox is coincident -> skipped.
    cand = GlyphCandidate(page=1, bbox=(100.0, 200.0, 112.0, 212.0))  # centre 106,206
    text_emoji = {1: [(101.0, 201.0, 111.0, 211.0)]}  # centre 106,206
    orphans, skipped = filter_coincident([cand], text_emoji)
    assert orphans == []
    assert skipped == 1


def test_filter_coincident_keeps_rect_past_tolerance():
    cand = GlyphCandidate(page=1, bbox=(100.0, 200.0, 112.0, 212.0))  # centre 106,206
    text_emoji = {1: [(140.0, 200.0, 152.0, 212.0)]}  # centre 146,206
    orphans, skipped = filter_coincident([cand], text_emoji)
    assert orphans == [cand]
    assert skipped == 0


def test_filter_coincident_is_per_page():
    # A text emoji on a different page must not suppress the glyph.
    cand = GlyphCandidate(page=2, bbox=(100.0, 200.0, 112.0, 212.0))
    text_emoji = {1: [(100.0, 200.0, 112.0, 212.0)]}  # same position, page 1
    orphans, skipped = filter_coincident([cand], text_emoji)
    assert orphans == [cand]
    assert skipped == 0


# -- placement ----------------------------------------------------------------


def _line(text, bbox):
    return Line(
        spans=[Span(text=text, font_name="Body", bbox=bbox)], bbox=bbox, page_num=0
    )


def _block(line):
    return Block(lines=[line], bbox=line.bbox, page_num=0)


def test_inject_leading_and_recomputes_bbox():
    line = _line("hello", (10.0, 100.0, 50.0, 112.0))
    block = _block(line)
    cand = GlyphCandidate(page=1, bbox=(2.0, 100.0, 8.0, 112.0))  # left of text
    stats = inject_glyph_spans([block], [cand])
    assert stats.injected == 1 and stats.free_standing == 0
    assert line.text == UNKNOWN_GLYPH + "hello"
    # Line and block bbox grew leftward to include the glyph rect.
    assert line.bbox[0] == 2.0
    assert block.bbox[0] == 2.0


def test_inject_trailing_keeps_x_order():
    line = _line("hello", (10.0, 100.0, 50.0, 112.0))
    block = _block(line)
    cand = GlyphCandidate(page=1, bbox=(52.0, 100.0, 64.0, 112.0))  # right of text
    inject_glyph_spans([block], [cand])
    assert line.text == "hello" + UNKNOWN_GLYPH
    assert line.bbox[2] == 64.0


def test_inject_two_on_one_line_sorted_by_x():
    line = _line("hello", (10.0, 100.0, 50.0, 112.0))
    block = _block(line)
    cands = [
        GlyphCandidate(page=1, bbox=(2.0, 100.0, 8.0, 112.0)),  # leading
        GlyphCandidate(page=1, bbox=(52.0, 100.0, 64.0, 112.0)),  # trailing
    ]
    stats = inject_glyph_spans([block], cands)
    assert stats.injected == 2
    assert line.text == UNKNOWN_GLYPH + "hello" + UNKNOWN_GLYPH


def test_inject_no_candidates_leaves_blocks_untouched():
    line = _line("hello", (10.0, 100.0, 50.0, 112.0))
    block = _block(line)
    stats = inject_glyph_spans([block], [])
    assert stats == GlyphPassStats()
    assert line.text == "hello"


def test_inject_free_standing_creates_block():
    line = _line("hello", (10.0, 100.0, 50.0, 112.0))
    block = _block(line)
    blocks = [block]
    # Far from any line's y-range -> free-standing fallback.
    cand = GlyphCandidate(page=1, bbox=(10.0, 500.0, 22.0, 512.0))
    stats = inject_glyph_spans(blocks, [cand])
    assert stats.injected == 1 and stats.free_standing == 1
    assert len(blocks) == 2
    new_block = blocks[-1]
    assert is_glyph_only_block(new_block)
    assert new_block.lines[0].text == UNKNOWN_GLYPH


def test_inject_independent_per_path():
    # The same orphan list injected into two independent block lists must
    # not alias: mutating one leaves the other's count correct.
    line_a = _line("abc", (10.0, 100.0, 40.0, 112.0))
    line_b = _line("abc", (10.0, 100.0, 40.0, 112.0))
    blocks_a = [_block(line_a)]
    blocks_b = [_block(line_b)]
    cand = GlyphCandidate(page=1, bbox=(42.0, 100.0, 54.0, 112.0))
    sa = inject_glyph_spans(blocks_a, [cand])
    sb = inject_glyph_spans(blocks_b, [cand])
    assert sa.injected == sb.injected == 1
    assert line_a.text == line_b.text == "abc" + UNKNOWN_GLYPH


def test_is_glyph_only_block():
    glyph = Span(text=UNKNOWN_GLYPH, font_name=GLYPH_FONT_SENTINEL, bbox=(0, 0, 5, 5))
    body = Span(text="hi", font_name="Body", bbox=(0, 0, 5, 5))
    assert is_glyph_only_block(Block(lines=[Line(spans=[glyph])]))
    assert not is_glyph_only_block(Block(lines=[Line(spans=[glyph, body])]))
    assert not is_glyph_only_block(Block(lines=[Line(spans=[body])]))


# -- PDF-level extraction (synthetic, in-memory) ------------------------------


def _tiny_png(dim_px=8):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, dim_px, dim_px), False)
    pix.clear_with(128)
    return pix.tobytes("png")


def test_collect_glyph_candidates_keeps_subthreshold_only():
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=300)
        png = _tiny_png()
        page.insert_image(pymupdf.Rect(50, 50, 62, 62), stream=png)  # 12pt: glyph
        page.insert_image(pymupdf.Rect(50, 150, 90, 190), stream=png)  # 40pt: figure
        cands = collect_glyph_candidates(page)
        assert len(cands) == 1
        assert (
            min(
                cands[0].bbox[2] - cands[0].bbox[0], cands[0].bbox[3] - cands[0].bbox[1]
            )
            < 20.0
        )
        assert cands[0].page == 1
    finally:
        doc.close()


def test_collect_glyph_candidates_dedups_by_position():
    # The same tiny image drawn at the same rect must yield ONE candidate
    # (get_image_rects can report a recurring xref's rect repeatedly).
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=300)
        png = _tiny_png()
        rect = pymupdf.Rect(50, 50, 62, 62)
        page.insert_image(rect, stream=png)
        page.insert_image(rect, stream=png)
        cands = collect_glyph_candidates(page)
        assert len(cands) == 1
    finally:
        doc.close()


def test_collect_glyph_candidates_distinct_positions_kept():
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=300)
        png = _tiny_png()
        page.insert_image(pymupdf.Rect(50, 50, 62, 62), stream=png)
        page.insert_image(pymupdf.Rect(50, 100, 62, 112), stream=png)
        cands = collect_glyph_candidates(page)
        assert len(cands) == 2
    finally:
        doc.close()


class _FakePage:
    """Minimal page exposing get_text('rawdict') for emoji-bbox tests.

    Synthetic PDFs can't carry emoji in their text layer (no portable
    emoji font), so the detection logic is tested against a crafted
    rawdict instead. ``chars`` is a list of (char, bbox) pairs forming
    one line in one span.
    """

    def __init__(self, chars):
        self.number = 0
        self._chars = chars

    def get_text(self, _kind):
        return {
            "blocks": [
                {
                    "lines": [
                        {
                            "spans": [
                                {
                                    "chars": [
                                        {"c": c, "bbox": bbox}
                                        for c, bbox in self._chars
                                    ],
                                }
                            ]
                        }
                    ]
                }
            ]
        }


def _bb(i):
    return (float(i), 0.0, float(i) + 1.0, 1.0)


def test_collect_text_emoji_bboxes_finds_emoji_ignores_prose():
    chars = [("h", _bb(0)), ("i", _bb(1)), ("\U0001f64f", _bb(2)), ("x", _bb(3))]
    bboxes = collect_text_emoji_bboxes(_FakePage(chars))
    assert bboxes == [_bb(2)]  # only the emoji character


def test_collect_text_emoji_bboxes_vs16_qualified_text_default():
    # U+25B6 is text-default (Emoji_Presentation=No) but VS16 forces emoji.
    chars = [("▶", _bb(0)), ("️", _bb(1)), ("a", _bb(2))]
    bboxes = collect_text_emoji_bboxes(_FakePage(chars))
    assert bboxes == [_bb(0)]  # base char's bbox, not VS16's


def test_collect_text_emoji_bboxes_ignores_bare_combining_marks():
    # A lone VS16 or ZWJ contributes no bbox of its own.
    chars = [("️", _bb(0)), ("‍", _bb(1)), ("a", _bb(2))]
    assert collect_text_emoji_bboxes(_FakePage(chars)) == []


# -- downstream-pass inertness of the sentinel span ---------------------------


def test_classify_monospace_rejects_sentinel():
    # Even with metrics that would otherwise pass, the sentinel is non-mono.
    assert classify_monospace(GLYPH_FONT_SENTINEL) is False
    assert (
        classify_monospace(
            GLYPH_FONT_SENTINEL,
            char_widths=[6.0, 6.0, 6.0],
            char_x_origins=[0.0, 6.0, 12.0],
            chars=["a", "b", "c"],
        )
        is False
    )


def test_propagate_monospace_never_flips_sentinel():
    mono_span = Span(text="code", font_name="Courier", monospace=True)
    glyph = Span(text=UNKNOWN_GLYPH, font_name=GLYPH_FONT_SENTINEL, monospace=False)
    spatial = [Block(lines=[Line(spans=[mono_span])])]
    mupdf = [
        Block(
            lines=[
                Line(
                    spans=[
                        Span(text="code", font_name="Courier", monospace=False),
                        glyph,
                    ]
                )
            ]
        )
    ]
    propagate_monospace(mupdf, spatial, dominant_font="Body")
    flipped = mupdf[0].lines[0].spans
    assert flipped[0].monospace is True  # real Courier span propagated
    assert flipped[1].monospace is False  # sentinel left alone


def test_normalize_spans_leaves_sentinel_in_place():
    spans = [
        Span(text="foo", font_name="Body", bold=True, bbox=(0, 0, 10, 10)),
        Span(text=UNKNOWN_GLYPH, font_name=GLYPH_FONT_SENTINEL, bbox=(10, 0, 11, 10)),
        Span(text="bar", font_name="Body", bold=False, bbox=(11, 0, 20, 10)),
    ]
    out = normalize_spans([Block(lines=[Line(spans=spans)])])
    result_spans = out[0].lines[0].spans
    # Sentinel survives; the bold run is not extended across it.
    assert UNKNOWN_GLYPH in "".join(s.text for s in result_spans)
    sentinel = [s for s in result_spans if s.font_name == GLYPH_FONT_SENTINEL]
    assert len(sentinel) == 1
    assert sentinel[0].bold is False


def test_block_words_strips_placeholder():
    with_glyph = Block(
        lines=[
            Line(
                spans=[
                    Span(text="alpha"),
                    Span(text=UNKNOWN_GLYPH),
                    Span(text=" beta"),
                ]
            )
        ]
    )
    without = Block(lines=[Line(spans=[Span(text="alpha beta")])])
    assert _block_words([with_glyph]) == _block_words([without]) == ["alpha", "beta"]


def test_glyph_only_section_not_listified():
    glyph_line = Line(
        spans=[
            Span(
                text=UNKNOWN_GLYPH,
                font_name=GLYPH_FONT_SENTINEL,
                bbox=(120, 100, 132, 112),
            )
        ],
        bbox=(120, 100, 132, 112),
        page_num=0,
    )
    sec = Section(
        kind=SectionKind.PARAGRAPH, text=UNKNOWN_GLYPH, lines=[glyph_line], page_num=0
    )
    out = _detect_lists_by_position([sec])
    assert len(out) == 1
    assert out[0].kind == SectionKind.PARAGRAPH


# -- coverage / QA neutrality -------------------------------------------------


def test_check_content_normalize_strips_placeholder():
    assert _normalize("consensus � increased") == _normalize("consensus increased")


def test_qa_does_not_flag_placeholders_as_mojibake():
    # With the glyph-placeholder marker present, the intentional U+FFFD
    # placeholders must not be scored as decode-failure mojibake.
    clean = (
        "---\ntitle: T\ndocument: P1R0\n---\n\n## H\n\n"
        "Some prose about contracts and consensus.\n"
    )
    marked = clean.replace("contracts", "contracts " + UNKNOWN_GLYPH * 5) + (
        "\n<!-- tomd:glyph-placeholders: placeholders=5 skipped_coincident=0 -->\n"
    )
    assert compute_metrics(marked).mojibake_count == 0
    assert compute_metrics(marked).score == compute_metrics(clean).score


def test_qa_still_flags_mojibake_without_marker():
    # Without the marker, a stray U+FFFD is still treated as mojibake
    # (the suppression is scoped to the glyph-placeholder case).
    body = (
        "---\ntitle: T\ndocument: P1R0\n---\n\n## H\n\n"
        "Some prose " + UNKNOWN_GLYPH + " here.\n"
    )
    assert compute_metrics(body).mojibake_count >= 1


# -- always-on end-to-end (no flag) -------------------------------------------


def test_always_on_emits_marker_without_flags(tmp_path):
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=400, height=520)  # portrait, not a slide
        prose = (
            "This paragraph exists so the page passes the readability "
            "gate, which requires a meaningful amount of real text "
            "before any structural classification or glyph injection "
            "runs in the conversion pipeline. " * 3
        )
        # insert_textbox wraps within the rect (insert_text would run a
        # single long line off-page and lose most of the text).
        page.insert_textbox(pymupdf.Rect(40, 40, 320, 200), prose, fontsize=10)
        # One sub-threshold raster glyph (12pt) the figure path drops,
        # placed inline at the right margin within the prose y-range so
        # it attaches to a body line rather than going free-standing.
        page.insert_image(pymupdf.Rect(322, 120, 334, 132), stream=_tiny_png())
        path = tmp_path / "glyphpaper.pdf"
        doc.save(str(path))
    finally:
        doc.close()

    result = run_pipeline(path)  # no flags: always-on
    assert result.glyph_stats is not None
    assert result.glyph_stats.injected == 1
    assert result.glyph_stats.free_standing == 0  # attached to a body line
    assert UNKNOWN_GLYPH in result.md
    # Marker is present and its count matches the U+FFFD actually in the
    # body (the honesty invariant), regardless of how many times tomd's
    # structure/metadata passes echo the line carrying the glyph.
    m = re.search(r"tomd:glyph-placeholders: placeholders=(\d+)", result.md)
    assert m is not None
    assert int(m.group(1)) == result.md.count(UNKNOWN_GLYPH)


def _glyph_span(x0, bbox_y=(100.0, 112.0)):
    return Span(
        text=UNKNOWN_GLYPH,
        font_name=GLYPH_FONT_SENTINEL,
        bbox=(x0, bbox_y[0], x0 + 12, bbox_y[1]),
    )


# -- #1 multi-column placement (algorithm fix) --------------------------------


def test_multicolumn_glyph_attaches_to_correct_column():
    left = Line(
        spans=[Span(text="left col", font_name="Body", bbox=(60, 196, 280, 208))],
        bbox=(60, 196, 280, 208),
        page_num=0,
    )
    right = Line(
        spans=[Span(text="right col", font_name="Body", bbox=(310, 196, 530, 208))],
        bbox=(310, 196, 530, 208),
        page_num=0,
    )
    block = Block(lines=[left, right], bbox=(60, 196, 530, 208), page_num=0)
    # Glyph centred at x=344 -> right column.
    cand = GlyphCandidate(page=1, bbox=(340, 196, 348, 204))
    inject_glyph_spans([block], [cand])
    assert UNKNOWN_GLYPH in right.text
    assert left.text == "left col"  # untouched


# -- #2 code / table section skip (algorithm fix) -----------------------------


def test_drop_glyph_in_code_section():
    code = Section(
        kind=SectionKind.CODE,
        text="x",
        confidence=Confidence.MEDIUM,
        lines=[
            Line(
                spans=[
                    Span(text="int x;", font_name="Mono", monospace=True),
                    _glyph_span(60),
                ]
            )
        ],
    )
    removed = drop_glyphs_in_code_and_tables([code])
    assert removed == 1
    assert all(s.font_name != GLYPH_FONT_SENTINEL for s in code.lines[0].spans)
    assert code.lines[0].text == "int x;"


def test_drop_glyph_in_table_section():
    table = Section(
        kind=SectionKind.TABLE,
        text="",
        confidence=Confidence.HIGH,
        columns=[
            [
                [Span(text="a", font_name="Body"), _glyph_span(60)],
                [Span(text="b", font_name="Body")],
            ]
        ],
    )
    removed = drop_glyphs_in_code_and_tables([table])
    assert removed == 1
    assert all(s.font_name != GLYPH_FONT_SENTINEL for s in table.columns[0][0])


def test_drop_glyph_leaves_paragraph_glyphs_alone():
    # Membership-based: a glyph in a PARAGRAPH (not CODE/TABLE) is NOT
    # removed. (The plan's bbox-"straddle" boundary does not apply: a glyph
    # belongs to exactly one line, hence one section.)
    para = Section(
        kind=SectionKind.PARAGRAPH,
        text="hi",
        lines=[Line(spans=[Span(text="hi", font_name="Body"), _glyph_span(60)])],
    )
    assert drop_glyphs_in_code_and_tables([para]) == 0
    assert any(s.font_name == GLYPH_FONT_SENTINEL for s in para.lines[0].spans)


# -- #3 wording-section interaction -------------------------------------------


def test_wording_section_markup_survives_glyph():
    spans = [
        Span(
            text="added", font_name="Body", wording_role="ins", bbox=(10, 100, 50, 112)
        ),
        Span(text=" ", font_name="Body", bbox=(50, 100, 54, 112)),
        _glyph_span(54),
        Span(
            text=" more", font_name="Body", wording_role="ins", bbox=(66, 100, 100, 112)
        ),
    ]
    sec = Section(kind=SectionKind.WORDING_ADD, text="", lines=[Line(spans=spans)])
    out = _render_wording_section(sec)
    assert out.count("<ins>") == out.count("</ins>")  # balanced, not broken
    assert out.count("<ins>") >= 1
    assert UNKNOWN_GLYPH in out


# -- #4 pre-existing U+FFFD in source text ------------------------------------


def test_preexisting_source_glyph_not_double_counted_by_pass():
    # The pass acts only on the raster glyph; a U+FFFD already in the text
    # layer is left where it is and the pass's own action count stays 1.
    body_glyph = Span(text="weird " + UNKNOWN_GLYPH + " char", font_name="Body")
    sec = Section(
        kind=SectionKind.PARAGRAPH,
        text=body_glyph.text,
        lines=[Line(spans=[body_glyph, _glyph_span(200)])],
    )
    stats = GlyphPassStats(injected=1)  # the pass injected exactly one
    md = emit_markdown({}, [sec], glyph_stats=stats)
    assert stats.injected == 1  # pass action count unchanged
    # Marker reports the body count (2 here: source + injected), each
    # counted once - no double counting.
    m = re.search(r"placeholders=(\d+)", md)
    assert int(m.group(1)) == md.count(UNKNOWN_GLYPH)


# -- #5 dehyphenation interaction ---------------------------------------------


def test_dehyphenation_does_not_consume_glyph():
    # "imple-" then glyph on line 0; "mentation" on line 1. The last span on
    # line 0 is the glyph (not "-"), so dehyphenation does not fire and the
    # glyph survives.
    line0 = Line(
        spans=[
            Span(text="imple-", font_name="Body", bbox=(10, 100, 50, 112)),
            _glyph_span(50),
        ],
        bbox=(10, 100, 62, 112),
        page_num=0,
    )
    line1 = Line(
        spans=[Span(text="mentation", font_name="Body", bbox=(10, 114, 70, 126))],
        bbox=(10, 114, 70, 126),
        page_num=0,
    )
    block = Block(lines=[line0, line1], bbox=(10, 100, 70, 126), page_num=0)
    out = cleanup_text([block])
    text = "\n".join(ln.text for b in out for ln in b.lines)
    assert UNKNOWN_GLYPH in text
    assert "implementation" not in text  # not merged across the glyph


# -- #6 cross-page-join interaction -------------------------------------------


def test_cross_page_join_preserves_glyph():
    # Page 0 last block ends without terminal punctuation (glyph last);
    # page 1 first block starts lowercase -> join, glyph preserved.
    b0 = Block(
        lines=[
            Line(
                spans=[
                    Span(text="trailing", font_name="Body", bbox=(10, 100, 60, 112)),
                    _glyph_span(60),
                ],
                bbox=(10, 100, 72, 112),
                page_num=0,
            )
        ],
        bbox=(10, 100, 72, 112),
        page_num=0,
    )
    b1 = Block(
        lines=[
            Line(
                spans=[
                    Span(
                        text="continuation here",
                        font_name="Body",
                        bbox=(10, 50, 120, 62),
                    )
                ],
                bbox=(10, 50, 120, 62),
                page_num=1,
            )
        ],
        bbox=(10, 50, 120, 62),
        page_num=1,
    )
    out = cleanup_text([b0, b1])
    text = " ".join(ln.text for b in out for ln in b.lines)
    assert UNKNOWN_GLYPH in text
    assert "continuation" in text


# -- #7 multi-space collapse --------------------------------------------------


def test_multi_space_collapse_preserves_glyph():
    assert (
        _collapse_spaces("foo  " + UNKNOWN_GLYPH + "  bar")
        == "foo " + UNKNOWN_GLYPH + " bar"
    )


# -- #8 NBSP replacement ------------------------------------------------------


def test_nbsp_replacement_preserves_glyph():
    line = Line(
        spans=[
            Span(
                text="foo " + UNKNOWN_GLYPH + " bar",
                font_name="Body",
                bbox=(10, 100, 80, 112),
            )
        ],
        bbox=(10, 100, 80, 112),
        page_num=0,
    )
    out = cleanup_text([Block(lines=[line], bbox=line.bbox, page_num=0)])
    text = out[0].lines[0].text
    assert " " not in text  # NBSP replaced
    assert UNKNOWN_GLYPH in text  # glyph survives


# -- #10 input-order independence ---------------------------------------------


def _three_line_block():
    lines = [
        Line(
            spans=[Span(text="L1", font_name="Body", bbox=(10, 100, 40, 112))],
            bbox=(10, 100, 40, 112),
            page_num=0,
        ),
        Line(
            spans=[Span(text="L2", font_name="Body", bbox=(10, 120, 40, 132))],
            bbox=(10, 120, 40, 132),
            page_num=0,
        ),
    ]
    return Block(lines=lines, bbox=(10, 100, 40, 132), page_num=0)


def test_injection_order_independent():
    cands = [
        GlyphCandidate(page=1, bbox=(42, 120, 54, 132)),
        GlyphCandidate(page=1, bbox=(42, 100, 54, 112)),
    ]
    b1 = _three_line_block()
    inject_glyph_spans([b1], cands)  # scrambled order
    b2 = _three_line_block()
    inject_glyph_spans(
        [b2], sorted(cands, key=lambda c: (c.page, c.bbox[1], c.bbox[0]))
    )  # pre-sorted
    assert [ln.text for ln in b1.lines] == [ln.text for ln in b2.lines]


# -- #11 empty-input edge cases -----------------------------------------------


def test_empty_inputs():
    assert collect_text_emoji_bboxes(_FakePage([])) == []
    assert filter_coincident([], {1: [(0, 0, 5, 5)]}) == ([], 0)
    cand = GlyphCandidate(page=1, bbox=(0, 0, 5, 5))
    assert filter_coincident([cand], {}) == ([cand], 0)


# -- #12 defensive rawdict-empty case -----------------------------------------


class _EmptyRawdictPage:
    number = 0

    def get_text(self, _kind):
        return {"blocks": []}


def test_collect_text_emoji_bboxes_empty_rawdict():
    assert collect_text_emoji_bboxes(_EmptyRawdictPage()) == []


# -- #13 marker absence at zero counts ----------------------------------------


def test_marker_absent_when_nothing_happened():
    from tomd.lib.pdf.emit import emit_markdown

    sec = Section(
        kind=SectionKind.PARAGRAPH,
        text="plain prose",
        lines=[Line(spans=[Span(text="plain prose", font_name="Body")])],
    )
    md = emit_markdown({}, [sec], glyph_stats=GlyphPassStats())
    assert "tomd:glyph-placeholders" not in md
    md_none = emit_markdown({}, [sec], glyph_stats=None)
    assert "tomd:glyph-placeholders" not in md_none


# -- #18 multi-character span: per-character bbox ------------------------------


def test_emoji_bbox_is_per_character_not_span_level():
    # One span "C 😬 x": collect must return the emoji's narrow per-char
    # bbox, not the wide span-level bbox.
    emoji_bb = (20.0, 0.0, 32.0, 12.0)
    chars = [
        ("C", (0.0, 0.0, 8.0, 12.0)),
        (" ", (8.0, 0.0, 12.0, 12.0)),
        ("\U0001f62c", emoji_bb),
        (" ", (32.0, 0.0, 36.0, 12.0)),
        ("x", (36.0, 0.0, 44.0, 12.0)),
    ]
    out = collect_text_emoji_bboxes(_FakePage(chars))
    assert out == [emoji_bb]


# -- #19 VS16-qualified text-default emoji ------------------------------------


def test_bare_text_default_emoji_not_detected():
    # Bare U+25B6 without VS16 is text-default -> no bbox.
    assert collect_text_emoji_bboxes(_FakePage([("▶", _bb(0))])) == []


# -- #21 GlyphCandidate sort ordering -----------------------------------------


def test_candidate_sort_order():
    cands = [
        GlyphCandidate(page=2, bbox=(10, 10, 20, 20)),
        GlyphCandidate(page=1, bbox=(50, 30, 60, 40)),
        GlyphCandidate(page=1, bbox=(10, 30, 20, 40)),
        GlyphCandidate(page=1, bbox=(10, 10, 20, 20)),
    ]
    ordered = sorted(cands, key=lambda c: (c.page, c.bbox[1], c.bbox[0]))
    assert [(c.page, c.bbox[1], c.bbox[0]) for c in ordered] == [
        (1, 10, 10),
        (1, 30, 10),
        (1, 30, 50),
        (2, 10, 10),
    ]


# -- #22 free-standing fallback structural exemption (heading + code) ---------


def test_free_standing_glyph_block_is_plain_paragraph():
    body = Section(
        kind=SectionKind.PARAGRAPH,
        text="ordinary body text here",
        lines=[
            Line(
                spans=[
                    Span(
                        text="ordinary body text here",
                        font_name="Body",
                        font_size=10.0,
                        bbox=(10, 50, 200, 62),
                    )
                ],
                bbox=(10, 50, 200, 62),
                page_num=0,
            )
        ],
        font_size=10.0,
    )
    glyph_sec = Section(
        kind=SectionKind.PARAGRAPH,
        text=UNKNOWN_GLYPH,
        lines=[
            Line(
                spans=[_glyph_span(10, (500, 512))], bbox=(10, 500, 22, 512), page_num=0
            )
        ],
        font_size=12.0,
        page_num=0,
    )
    _meta, out, _n = structure_sections([body, glyph_sec])
    glyph_out = [
        s
        for s in out
        if any(sp.font_name == GLYPH_FONT_SENTINEL for ln in s.lines for sp in ln.spans)
    ]
    assert len(glyph_out) == 1
    assert glyph_out[0].kind == SectionKind.PARAGRAPH  # not HEADING/CODE/LIST


# -- #23 compare_extractions neutralization -----------------------------------


def test_compare_extractions_neutral_to_glyph_position():
    def _page(glyph_x):
        return [
            Block(
                lines=[
                    Line(
                        spans=[
                            Span(
                                text="consensus on contracts increased",
                                font_name="Body",
                                bbox=(10, 100, 200, 112),
                            ),
                            _glyph_span(glyph_x),
                        ],
                        bbox=(10, 100, 200, 112),
                        page_num=0,
                    )
                ],
                page_num=0,
            )
        ]

    # Same words; glyph at different x in each path.
    secs = compare_extractions(_page(205), _page(8))
    assert secs
    assert all(s.kind != SectionKind.UNCERTAIN for s in secs)


# -- #24 no-cap (volume option 1) ---------------------------------------------


def test_no_per_page_cap_injects_all():
    # 50 candidates all on one page; option 1 places every one (no cap).
    block = Block(
        lines=[
            Line(
                spans=[
                    Span(
                        text=f"r{i}",
                        font_name="Body",
                        bbox=(10, 100 + i * 14, 40, 110 + i * 14),
                    )
                ],
                bbox=(10, 100 + i * 14, 40, 110 + i * 14),
                page_num=0,
            )
            for i in range(50)
        ],
        bbox=(10, 100, 40, 800),
        page_num=0,
    )
    cands = [
        GlyphCandidate(page=1, bbox=(42, 100 + i * 14, 54, 110 + i * 14))
        for i in range(50)
    ]
    stats = inject_glyph_spans([block], cands)
    assert stats.injected == 50


# -- #25 coincidence-tolerance boundary inclusivity ---------------------------


def test_coincidence_tolerance_is_inclusive():
    # Glyph centre exactly TOL away from a text-emoji centre -> treated as
    # coincident (inclusive `<=`), so it is skipped.
    cand = GlyphCandidate(page=1, bbox=(100.0, 200.0, 112.0, 212.0))  # centre 106,206
    emoji_centre_x = 106.0 + TOL
    text_emoji = {1: [(emoji_centre_x - 5, 201.0, emoji_centre_x + 5, 211.0)]}
    orphans, skipped = filter_coincident([cand], text_emoji)
    assert (orphans, skipped) == ([], 1)


# ===========================================================================
# PDF/HTML-level tests (synthetic, in-memory)
# ===========================================================================

_PROSE = (
    "This paragraph exists so the page passes the readability gate, "
    "which requires a meaningful amount of real text before structural "
    "classification or glyph injection runs in the pipeline. " * 3
)


def _fig_png(shade, dim_px=32):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, dim_px, dim_px), False)
    pix.clear_with(shade)  # distinct shade -> distinct stream -> distinct xref
    return pix.tobytes("png")


def _save(doc, tmp_path, name):
    path = tmp_path / name
    doc.save(str(path))
    return path


# -- #9 byte-identical positive case ------------------------------------------


def test_byte_identical_with_glyphs(tmp_path):
    def _make():
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=520)
        page.insert_textbox(pymupdf.Rect(40, 40, 320, 200), _PROSE, fontsize=10)
        page.insert_image(pymupdf.Rect(322, 120, 334, 132), stream=_tiny_png())
        page.insert_image(pymupdf.Rect(322, 150, 334, 162), stream=_tiny_png())
        p = _save(doc, tmp_path, "bytetest.pdf")
        doc.close()
        return p

    md1 = run_pipeline(_make()).md
    md2 = run_pipeline(_make()).md
    assert md1 == md2
    assert "tomd:glyph-placeholders" in md1


# -- #14 coexistence with vector extraction -----------------------------------


def test_coexists_with_extract_vector(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=520)
    page.insert_textbox(pymupdf.Rect(40, 40, 320, 200), _PROSE, fontsize=10)
    page.insert_image(pymupdf.Rect(322, 120, 334, 132), stream=_tiny_png())
    path = _save(doc, tmp_path, "vectortest.pdf")
    doc.close()

    off = run_pipeline(path)
    on = run_pipeline(path, extract_vector=True)
    assert off.glyph_stats.injected == on.glyph_stats.injected == 1
    assert off.md.count(UNKNOWN_GLYPH) == on.md.count(UNKNOWN_GLYPH)
    # No vector content in this PDF -> no vector marker either way.
    assert "tomd:vector-extraction-uncertain" not in on.md


# -- #15 image cap excludes glyphs --------------------------------------------


def test_image_cap_excludes_glyphs(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=1600)
    page.insert_textbox(pymupdf.Rect(40, 40, 360, 360), _PROSE, fontsize=10)
    # 5 sub-threshold glyphs near the prose lines (within the text column).
    for i in range(5):
        y = 60 + i * 14
        page.insert_image(pymupdf.Rect(330, y, 342, y + 12), stream=_tiny_png())
    # Exactly 20 distinct figures (>= MIN_IMAGE_DIM_PT) below the prose ->
    # at the cap, not over.
    for i in range(20):
        y = 420 + i * 55
        page.insert_image(pymupdf.Rect(40, y, 72, y + 32), stream=_fig_png(i + 1))
    path = _save(doc, tmp_path, "captest.pdf")
    doc.close()

    r = run_pipeline(path)
    assert r.glyph_stats.injected == 5  # all glyphs placed
    assert not r.images_truncated  # 20 figures == cap, not over
    assert "tomd:images-truncated" not in r.md
    assert "tomd:glyph-placeholders" in r.md


# -- #16 slide-deck and standards-draft early-exit dominance ------------------


def test_slide_deck_skips_glyph_pass(tmp_path):
    doc = pymupdf.open()
    for _ in range(3):  # landscape pages -> slide deck
        page = doc.new_page(width=720, height=540)
        page.insert_textbox(pymupdf.Rect(40, 40, 680, 300), _PROSE, fontsize=10)
        page.insert_image(pymupdf.Rect(60, 60, 72, 72), stream=_tiny_png())
    path = _save(doc, tmp_path, "slides.pdf")
    doc.close()

    r = run_pipeline(path)
    assert r.skipped and r.skip_reason == "slide deck"
    assert r.glyph_stats is None
    assert "tomd:glyph-placeholders" not in r.md


def test_standards_draft_skips_glyph_pass(tmp_path):
    doc = pymupdf.open()
    for i in range(200):  # >= 200 pages -> standards draft
        page = doc.new_page(width=400, height=520)
        if i == 0:
            page.insert_textbox(pymupdf.Rect(40, 40, 320, 200), _PROSE, fontsize=10)
            page.insert_image(pymupdf.Rect(322, 120, 334, 132), stream=_tiny_png())
    path = _save(doc, tmp_path, "draft.pdf")
    doc.close()

    r = run_pipeline(path)
    assert r.skipped and r.skip_reason == "standards draft"
    assert r.glyph_stats is None
    assert "tomd:glyph-placeholders" not in r.md


# -- #17 HTML conversion no-op ------------------------------------------------


def test_html_conversion_has_no_glyph_marker():
    fixtures = Path(__file__).parent / "fixtures" / "html"
    sample = fixtures / "bikeshed_sample.html"
    md, _prompts = convert_html(sample)
    assert "tomd:glyph-placeholders" not in md
    assert UNKNOWN_GLYPH not in md
