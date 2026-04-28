"""Tests for lib.pdf.mono."""

from conftest import make_span, make_line, make_block
from tomd.lib.pdf.mono import classify_monospace, propagate_monospace
from tomd.lib.pdf.types import Span, Line, Block


def test_keyword_courier():
    assert classify_monospace("Courier")


def test_keyword_menlo():
    assert classify_monospace("Menlo-Regular")


def test_keyword_consolas():
    assert classify_monospace("Consolas")


def test_keyword_source_code_pro():
    assert classify_monospace("SourceCodePro")


def test_keyword_inconsolata():
    assert classify_monospace("Inconsolata-Regular")


def test_keyword_iosevka():
    assert classify_monospace("Iosevka")


def test_keyword_hack():
    assert classify_monospace("Hack-Bold")


def test_keyword_lmtt():
    assert classify_monospace("LMTT10")


def test_keyword_lmtt_variant():
    assert classify_monospace("LMTT8")


def test_keyword_fira():
    assert classify_monospace("FiraMono-Regular")


def test_no_keyword_no_data():
    assert not classify_monospace("Arial")


def test_no_keyword_no_data_unnamed():
    assert not classify_monospace("Unnamed-T3")


def test_uniform_widths_and_spacings():
    widths = [10.0] * 10
    origins = [float(i * 10) for i in range(10)]
    assert classify_monospace("UnknownFont", widths, origins)


def test_non_uniform_widths():
    widths = [5.0, 15.0, 5.0, 15.0, 5.0]
    origins = [0.0, 5.0, 20.0, 25.0, 40.0]
    assert not classify_monospace("UnknownFont", widths, origins)


def test_proportional_advance_ratio_rejects():
    """Proportional font: M advances much further than i -> reject."""
    chars = ["M", "i", "M", "i"]
    origins = [0.0, 15.0, 20.0, 35.0]  # M=15 units, i=5 units
    widths = [14.0, 3.0, 14.0, 3.0]
    assert not classify_monospace("UnknownFont", widths, origins, chars=chars)


def test_monospace_advance_ratio_accepts():
    """Monospace font: all chars advance equally regardless of glyph body width."""
    chars = ["M", "i", "M", "i"]
    origins = [0.0, 10.0, 20.0, 30.0]  # All advance 10 units
    widths = [14.0, 3.0, 14.0, 3.0]    # Bbox widths vary but that's fine
    assert classify_monospace("UnknownFont", widths, origins, chars=chars)


def test_fat_thin_not_checked_without_origins():
    """Fat/thin check is bypassed when x-origins are absent; font name decides."""
    widths = [14.0, 3.0, 14.0, 3.0]  # M wide, i narrow (proportional-looking bboxes)
    chars = ["M", "i", "M", "i"]
    # No x_origins -> fat/thin skipped; Courier font name accepted via signal 1
    assert classify_monospace("Courier", widths, None, chars=chars)
    # Without a monospace name and without origins, signals 1 and 3 are both False
    assert not classify_monospace("Arial", widths, None, chars=chars)


def test_propagate_sets_monospace():
    m_block = make_block(["hello world"], page_num=0)
    m_block.lines[0].spans[0].font_name = "Menlo-Regular"
    m_block.lines[0].spans[0].monospace = False

    s_block = make_block(["hello world"], page_num=0)
    s_block.lines[0].spans[0].font_name = "Menlo-Regular"
    s_block.lines[0].spans[0].monospace = True

    propagate_monospace([m_block], [s_block], "arial")
    assert m_block.lines[0].spans[0].monospace is True


def test_propagate_excludes_dominant_proportional():
    """Dominant font is discarded if its name is not a monospace family."""
    m_block = make_block(["hello world"], page_num=0)
    m_block.lines[0].spans[0].font_name = "Arial"
    m_block.lines[0].spans[0].monospace = False

    s_block = make_block(["hello world"], page_num=0)
    s_block.lines[0].spans[0].font_name = "Arial"
    s_block.lines[0].spans[0].monospace = True  # spatial path false-positive

    propagate_monospace([m_block], [s_block], "arial")
    assert m_block.lines[0].spans[0].monospace is False


def test_propagate_keeps_dominant_monospace():
    """Dominant monospace font is NOT discarded - code-heavy papers need this."""
    m_block = make_block(["code code code"], page_num=0)
    m_block.lines[0].spans[0].font_name = "Courier"
    m_block.lines[0].spans[0].monospace = False

    s_block = make_block(["code code code"], page_num=0)
    s_block.lines[0].spans[0].font_name = "Courier"
    s_block.lines[0].spans[0].monospace = True

    propagate_monospace([m_block], [s_block], "courier")
    assert m_block.lines[0].spans[0].monospace is True


# ---------------------------------------------------------------------------
# Regression coverage for PR #9 propagate_monospace majority filter.
# ---------------------------------------------------------------------------


def test_propagate_minority_mono_not_adopted():
    """A prose font with <50% monospace-classified chars is NOT propagated.

    Short spans like "3.1" or "42" can false-positive the per-glyph signal,
    but they represent a small minority of the font's characters. Requiring
    a majority keeps proportional text fonts out of the mono set.
    """
    # Lato-Light appears in ~100 chars total, only 3 of which are mono.
    prose_lines = [
        Line(spans=[Span(text="A" * 97, font_name="Lato-Light",
                          monospace=False)]),
        Line(spans=[Span(text="3.1", font_name="Lato-Light",
                          monospace=True)]),  # false-positive
    ]
    spatial = [Block(lines=prose_lines)]

    mupdf_line = Line(spans=[Span(text="more prose", font_name="Lato-Light",
                                    monospace=False)])
    mupdf = [Block(lines=[mupdf_line])]

    propagate_monospace(mupdf, spatial, "some-dominant-font")
    assert mupdf[0].lines[0].spans[0].monospace is False, (
        "minority mono classification must not contaminate a prose font"
    )


def test_propagate_majority_mono_is_adopted():
    """A font with >=50% monospace-classified chars IS propagated."""
    code_lines = [
        Line(spans=[Span(text="int x = 0;", font_name="MyCodeFont",
                          monospace=True)]),
        Line(spans=[Span(text="y", font_name="MyCodeFont",
                          monospace=False)]),  # minority non-mono
    ]
    spatial = [Block(lines=code_lines)]

    mupdf_line = Line(spans=[Span(text="return x;", font_name="MyCodeFont",
                                    monospace=False)])
    mupdf = [Block(lines=[mupdf_line])]

    propagate_monospace(mupdf, spatial, "arial")
    assert mupdf[0].lines[0].spans[0].monospace is True, (
        "majority-monospace font should propagate to MuPDF spans"
    )


def test_propagate_exactly_at_threshold_is_adopted():
    """50%% exactly meets the majority threshold (inclusive)."""
    # 5 mono chars, 5 non-mono chars -> ratio 0.5 (meets threshold).
    lines = [
        Line(spans=[Span(text="aaaaa", font_name="EdgeFont",
                          monospace=True)]),
        Line(spans=[Span(text="bbbbb", font_name="EdgeFont",
                          monospace=False)]),
    ]
    spatial = [Block(lines=lines)]

    mupdf_line = Line(spans=[Span(text="target", font_name="EdgeFont",
                                    monospace=False)])
    mupdf = [Block(lines=[mupdf_line])]

    propagate_monospace(mupdf, spatial, "arial")
    assert mupdf[0].lines[0].spans[0].monospace is True
