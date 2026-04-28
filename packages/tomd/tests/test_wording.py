"""Tests for lib.pdf.wording."""

from unittest.mock import MagicMock
from conftest import make_span, make_line, make_block
from tomd.lib.pdf.types import Span, Line, Block
from tomd.lib.pdf.wording import classify_wording, collect_line_drawings


def _color(r, g, b):
    """Convert 0-255 RGB to MuPDF integer color."""
    return (r << 16) | (g << 8) | b


_GREEN = _color(0, 133, 71)
_RED = _color(204, 0, 0)
_BLUE = _color(5, 85, 193)
_PURPLE = _color(128, 0, 128)


def _green_block(n=6, text="added text", bbox=(10, 50, 200, 60)):
    """Block of n lines, each a single green span with explicit bbox."""
    spans = [Span(text=f"{text} {i}", color=_GREEN, bbox=bbox) for i in range(n)]
    lines = [Line(spans=[s]) for s in spans]
    return Block(lines=lines, page_num=0)


def _red_block(n=6, text="removed text", bbox=(10, 50, 200, 60)):
    """Block of n lines, each a single red span with explicit bbox."""
    spans = [Span(text=f"{text} {i}", color=_RED, bbox=bbox) for i in range(n)]
    lines = [Line(spans=[s]) for s in spans]
    return Block(lines=lines, page_num=0)


def _strikethrough_at(y, x0=10, x1=200):
    """Drawings dict with a single strikethrough line."""
    return {0: [(y, x0, x1, (0.8, 0, 0))]}


class TestMatchStrikethrough:
    def test_del_classified_with_matching_drawing(self):
        block = _red_block()
        # Strikethrough at span vertical center: (50+60)/2 = 55
        problems = classify_wording([block], _strikethrough_at(55))
        assert block.lines[0].spans[0].wording_role == "del"
        assert len(problems) == 0

    def test_del_not_classified_without_drawing(self):
        block = _red_block()
        problems = classify_wording([block], {})
        assert all(s.wording_role is None for ln in block.lines for s in ln.spans)

    def test_del_not_classified_when_drawing_out_of_range(self):
        block = _red_block()
        # y=48, center=55, distance=7 > _STRIKETHROUGH_Y_TOLERANCE=2.0
        problems = classify_wording([block], _strikethrough_at(48))
        assert all(s.wording_role is None for ln in block.lines for s in ln.spans)

    def test_del_not_classified_when_drawing_too_narrow(self):
        # Drawing width=5, span width=190 -> overlap fraction 5/190 < 0.3
        block = _red_block(bbox=(10, 50, 200, 60))
        narrow_drawings = {0: [(55, 10, 15, (0.8, 0, 0))]}
        problems = classify_wording([block], narrow_drawings)
        assert all(s.wording_role is None for ln in block.lines for s in ln.spans)


class TestInsClassification:
    def test_ins_classified_without_drawing(self):
        # Green spans are classified as ins even without underline drawing
        block = _green_block()
        problems = classify_wording([block], {})
        assert block.lines[0].spans[0].wording_role == "ins"
        assert len(problems) == 0

    def test_ins_classified_with_drawing(self):
        block = _green_block()
        underline = {0: [(60.5, 10, 200, (0, 0.5, 0))]}
        problems = classify_wording([block], underline)
        assert block.lines[0].spans[0].wording_role == "ins"
        assert len(problems) == 0

    def test_ins_high_confidence_no_problems(self):
        block = _green_block(n=10)
        problems = classify_wording([block], {})
        assert len(problems) == 0


class TestMajorityFilter:
    def test_minority_red_not_classified(self):
        """A few red words on an otherwise black line are not del."""
        black = Span(text="normal text normal text normal text", color=0,
                     bbox=(0, 50, 350, 60))
        red_link = Span(text="link", color=_RED, bbox=(350, 50, 390, 60))
        line = Line(spans=[black, red_link])
        block = Block(lines=[line] * 6, page_num=0)
        classify_wording([block], _strikethrough_at(55))
        assert all(s.wording_role is None for ln in block.lines for s in ln.spans)

    def test_minority_green_on_black_classified(self):
        """Green span on an otherwise-black line is wording (partial-line pattern)."""
        black = Span(text="the function returns the value", color=0,
                     bbox=(0, 50, 300, 60))
        green_code = Span(text="T", color=_GREEN, bbox=(300, 50, 315, 60))
        line = Line(spans=[black, green_code])
        block = Block(lines=[line] * 6, page_num=0)
        classify_wording([block], {})
        assert any(s.wording_role == "ins" for ln in block.lines for s in ln.spans)

    def test_majority_green_line_classified(self):
        """When most of a line is green, it's wording ins."""
        green = Span(text="void f(int x) { return x; }", color=_GREEN,
                     bbox=(10, 50, 250, 60))
        black = Span(text=" //", color=0, bbox=(250, 50, 280, 60))
        line = Line(spans=[green, black])
        block = Block(lines=[line] * 6, page_num=0)
        classify_wording([block], {})
        assert green.wording_role == "ins"


class TestForeignColorFilter:
    def test_purple_span_disqualifies_block(self):
        """Block with purple text (syntax highlighting) is skipped."""
        green = Span(text="added text", color=_GREEN, bbox=(10, 50, 200, 60))
        purple = Span(text="keyword", color=_PURPLE, bbox=(200, 50, 270, 60))
        line = Line(spans=[green, purple])
        block = Block(lines=[line] * 6, page_num=0)
        classify_wording([block], {})
        assert all(s.wording_role is None for ln in block.lines for s in ln.spans)

    def test_blue_link_does_not_disqualify_block(self):
        """Blue hyperlinks (link_url set) do not contaminate the block check."""
        green = Span(text="added text added text", color=_GREEN, bbox=(10, 50, 200, 60))
        blue_link = Span(text="[dcl.type]", color=_BLUE, bbox=(200, 50, 270, 60),
                         link_url="https://eel.is/c++draft/dcl.type")
        line = Line(spans=[green, blue_link])
        block = Block(lines=[line] * 6, page_num=0)
        classify_wording([block], {})
        assert green.wording_role == "ins"

    def test_blue_non_link_does_not_disqualify(self):
        """Blue non-link text is allowed (cross-references without link annotation)."""
        green = Span(text="added text added text", color=_GREEN, bbox=(10, 50, 200, 60))
        blue = Span(text="[dcl.type]", color=_BLUE, bbox=(200, 50, 270, 60))
        line = Line(spans=[green, blue])
        block = Block(lines=[line] * 6, page_num=0)
        classify_wording([block], {})
        assert green.wording_role == "ins"


class TestThreshold:
    def test_below_min_spans_no_classification(self):
        """Fewer than _MIN_WORDING_SPANS ins/del spans → nothing classified."""
        block = _green_block(n=3)
        classify_wording([block], {})
        assert all(s.wording_role is None for ln in block.lines for s in ln.spans)

    def test_black_spans_not_classified(self):
        blocks = [make_block(["normal text"] * 10, page_num=0)]
        classify_wording(blocks, {})
        assert all(s.wording_role is None
                   for ln in blocks[0].lines for s in ln.spans)

    def test_blue_spans_not_classified(self):
        block = Block(
            lines=[Line(spans=[Span(text="link text", color=_BLUE,
                                   bbox=(10, 50, 200, 60))]) for _ in range(10)],
            page_num=0,
        )
        classify_wording([block], {})
        assert all(s.wording_role is None for ln in block.lines for s in ln.spans)


class TestCollectLineDrawings:
    def _make_page(self, drawings):
        page = MagicMock()
        page.get_drawings.return_value = drawings
        return page

    def _make_drawing(self, x0, y0, x1, y1, color=(0.0, 0.5, 0.0)):
        """Build a minimal drawing dict with a single 'l' (line) item."""
        p1 = MagicMock()
        p1.x, p1.y = x0, y0
        p2 = MagicMock()
        p2.x, p2.y = x1, y1
        return {"items": [("l", p1, p2)], "color": color}

    def test_horizontal_line_collected(self):
        drawing = self._make_drawing(10, 50, 200, 50)
        page = self._make_page([drawing])
        result = collect_line_drawings(page)
        assert len(result) == 1
        y, x0, x1, color = result[0]
        assert abs(y - 50) < 0.1
        assert x0 == 10
        assert x1 == 200

    def test_short_line_filtered_out(self):
        """Lines <= 5 px wide are discarded."""
        drawing = self._make_drawing(10, 50, 14, 50)
        page = self._make_page([drawing])
        assert collect_line_drawings(page) == []

    def test_diagonal_line_filtered_out(self):
        """Lines with |dy| >= 1 are not horizontal and are discarded."""
        drawing = self._make_drawing(10, 50, 200, 52)
        page = self._make_page([drawing])
        assert collect_line_drawings(page) == []

    def test_get_drawings_exception_returns_empty(self):
        """If get_drawings() raises, degrade gracefully and return []."""
        page = MagicMock()
        page.get_drawings.side_effect = RuntimeError("MuPDF internal error")
        assert collect_line_drawings(page) == []

    def test_no_color_drawing_skipped(self):
        """Drawings without a color tuple are skipped."""
        p1 = MagicMock()
        p1.x, p1.y = 10, 50
        p2 = MagicMock()
        p2.x, p2.y = 200, 50
        drawing = {"items": [("l", p1, p2)], "color": None}
        page = self._make_page([drawing])
        assert collect_line_drawings(page) == []


class TestTwoPassDeletion:
    """Two-pass del classification: red without strikethrough promoted when ins present."""

    def test_del_classified_without_strikethrough_when_ins_present(self):
        """Red spans without strikethrough are promoted to del when enough green ins exist."""
        green_spans = [Span(text=f"added {i}", color=_GREEN, bbox=(10, 50, 200, 60))
                       for i in range(6)]
        red_spans = [Span(text=f"removed {i}", color=_RED, bbox=(10, 70, 200, 80))
                     for i in range(3)]
        green_lines = [Line(spans=[s]) for s in green_spans]
        red_lines = [Line(spans=[s]) for s in red_spans]
        block = Block(lines=green_lines + red_lines, page_num=0)

        classify_wording([block], {})

        for s in green_spans:
            assert s.wording_role == "ins"
        for s in red_spans:
            assert s.wording_role == "del", (
                "red without strikethrough should be promoted to del "
                "when sufficient ins context exists"
            )

    def test_del_not_classified_without_strikethrough_when_no_ins(self):
        """Red spans without strikethrough are dropped when no green ins exist."""
        red_spans = [Span(text=f"removed {i}", color=_RED, bbox=(10, 50, 200, 60))
                     for i in range(6)]
        lines = [Line(spans=[s]) for s in red_spans]
        block = Block(lines=lines, page_num=0)

        classify_wording([block], {})

        for s in red_spans:
            assert s.wording_role is None, (
                "red without strikethrough should NOT be classified "
                "when no ins context exists"
            )
