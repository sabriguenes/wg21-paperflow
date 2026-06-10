# Copyright (c) 2026 C++ Alliance, Inc. (https://cppalliance.org)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Tests for tomd.lib.pdf.figures -- figure region detection."""

from tomd.lib.pdf.figures import (
    detect_figure_regions,
    _is_bordered_box,
    _is_arrowhead,
    _is_connector,
    _is_dashed_connector,
    _match_topology,
    _group_boxes,
    _merge_connected_groups,
    _detect_sequence_diagram,
)
from tomd.lib.pdf.emit import _render_figure_placeholder
from tomd.lib.pdf.types import (
    Section, SectionKind, Line, Span, Confidence,
    FigureGraph, FigureNode, FigureEdge,
)


class _FakeRect:
    """Minimal rect proxy for testing."""
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.width = x1 - x0
        self.height = y1 - y0


class _FakePoint:
    """Minimal point proxy for testing."""
    def __init__(self, x, y):
        self.x, self.y = x, y


def _make_path(rect, color=None, fill=None, item_type="re", width=None):
    r = _FakeRect(*rect)
    items = [(item_type, r)] if item_type == "re" else [("l", None, None)]
    return {"rect": r, "color": color, "fill": fill, "items": items, "width": width}


def _make_arrowhead_path(vertices, fill=(0, 0, 0)):
    """Build a triangular arrowhead path from 3 (x,y) vertices."""
    pts = [_FakePoint(*v) for v in vertices]
    items = [
        ("l", pts[0], pts[1]),
        ("l", pts[1], pts[2]),
        ("l", pts[2], pts[0]),
    ]
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    r = _FakeRect(min(xs), min(ys), max(xs), max(ys))
    return {"rect": r, "color": None, "fill": fill, "items": items,
            "closePath": True}


def _make_connector_path(start, end, color=(0, 0, 0)):
    """Build a simple line connector path."""
    p1 = _FakePoint(*start)
    p2 = _FakePoint(*end)
    xs = [start[0], end[0]]
    ys = [start[1], end[1]]
    r = _FakeRect(min(xs), min(ys), max(xs), max(ys))
    return {"rect": r, "color": color, "fill": None, "items": [("l", p1, p2)],
            "closePath": False}


class TestIsBorderedBox:
    def test_box_with_color_and_fill(self):
        path = _make_path((100, 100, 200, 130), color=(0.5, 0.3, 0.8), fill=(0.9, 0.9, 1.0))
        assert _is_bordered_box(path, 600.0) is not None

    def test_no_color_rejected(self):
        path = _make_path((100, 100, 200, 130), color=None, fill=(0.9, 0.9, 0.9))
        assert _is_bordered_box(path, 600.0) is None

    def test_no_fill_rejected(self):
        path = _make_path((100, 100, 200, 130), color=(0, 0, 0), fill=None)
        assert _is_bordered_box(path, 600.0) is None

    def test_too_small_rejected(self):
        path = _make_path((100, 100, 110, 105), color=(0, 0, 0), fill=(1, 1, 1))
        assert _is_bordered_box(path, 600.0) is None

    def test_too_wide_rejected(self):
        path = _make_path((10, 100, 500, 130), color=(0, 0, 0), fill=(1, 1, 1))
        assert _is_bordered_box(path, 600.0) is None

    def test_rounded_rect_accepted(self):
        r = _FakeRect(100, 100, 200, 130)
        path = {
            "rect": r,
            "color": (0.5, 0.3, 0.8),
            "fill": (0.9, 0.9, 1.0),
            "items": [("l", None, None), ("c", None, None, None, None)] * 4,
        }
        assert _is_bordered_box(path, 600.0) is not None


class TestGroupBoxes:
    def test_single_box(self):
        groups = _group_boxes([(100, 100, 200, 130)])
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_two_close_boxes(self):
        groups = _group_boxes([
            (100, 100, 200, 130),
            (250, 100, 350, 130),
        ])
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_two_distant_boxes(self):
        groups = _group_boxes([
            (100, 100, 200, 130),
            (100, 500, 200, 530),
        ])
        assert len(groups) == 2

    def test_empty(self):
        assert _group_boxes([]) == []


class TestDetectFigureRegions:
    def test_no_drawings(self):
        assert detect_figure_regions([], 0, 600.0) == []

    def test_single_box_no_figure(self):
        paths = [_make_path((100, 100, 200, 130), color=(0, 0, 0), fill=(1, 1, 1))]
        assert detect_figure_regions(paths, 0, 600.0) == []

    def test_two_nearby_boxes_detected(self):
        paths = [
            _make_path((63, 566, 128, 590), color=(0.5, 0.4, 0.9), fill=(0.9, 0.9, 1.0)),
            _make_path((212, 566, 277, 590), color=(0.5, 0.4, 0.9), fill=(0.9, 0.9, 1.0)),
            _make_path((369, 566, 432, 590), color=(0.5, 0.4, 0.9), fill=(0.9, 0.9, 1.0)),
        ]
        regions = detect_figure_regions(paths, 7, 595.0)
        assert len(regions) == 1
        assert regions[0].page_num == 7
        x0, y0, x1, y1 = regions[0].bbox
        assert x0 < 65
        assert y0 < 568
        assert x1 > 430
        assert y1 > 588

    def test_table_cells_no_border_not_detected(self):
        paths = [
            _make_path((57, 57, 176, 84), color=None, fill=(0.87, 0.87, 0.87)),
            _make_path((176, 57, 332, 84), color=None, fill=(0.87, 0.87, 0.87)),
            _make_path((332, 57, 440, 84), color=None, fill=(0.87, 0.87, 0.87)),
        ]
        assert detect_figure_regions(paths, 0, 595.0) == []

    def test_page_wide_rect_ignored(self):
        paths = [
            _make_path((57, 57, 539, 785), color=(0, 0, 0), fill=(1, 1, 1)),
            _make_path((57, 100, 539, 130), color=(0, 0, 0), fill=(1, 1, 1)),
        ]
        assert detect_figure_regions(paths, 0, 595.0) == []


def _make_figure_section(line_data: list[tuple[str, tuple]]) -> Section:
    """Build a FIGURE section with lines carrying bbox metadata."""
    lines = []
    for text, bbox in line_data:
        sp = Span(text=text, bbox=bbox)
        lines.append(Line(spans=[sp], bbox=bbox))
    joined = "\n".join(t for t, _ in line_data)
    return Section(kind=SectionKind.FIGURE, text=joined,
                   confidence=Confidence.HIGH, lines=lines)


class TestRenderFigurePlaceholder:
    def test_horizontal_chain_sorted_by_x(self):
        sec = _make_figure_section([
            ("refined by", (147, 572, 193, 584)),
            ("modeled by", (296, 572, 350, 584)),
            ("IoAwaitable", (69, 572, 123, 584)),
            ("IoRunnable", (218, 572, 271, 584)),
            ("io_task<T>", (374, 572, 426, 584)),
        ])
        result = _render_figure_placeholder(sec)
        assert "Concept Chain" in result
        assert "IoAwaitable -> refined by -> IoRunnable -> modeled by -> io_task<T>" in result

    def test_vertical_flow_numbered(self):
        sec = _make_figure_section([
            ("step A", (100, 70, 300, 82)),
            ("step B", (100, 130, 300, 142)),
            ("step C", (100, 190, 300, 202)),
        ])
        result = _render_figure_placeholder(sec)
        assert "Flow Diagram" in result
        assert "> 1. step A" in result
        assert "> 2. step B" in result
        assert "> 3. step C" in result

    def test_no_lines_fallback(self):
        sec = Section(kind=SectionKind.FIGURE, text="some text",
                      confidence=Confidence.HIGH, lines=[])
        result = _render_figure_placeholder(sec)
        assert "[Figure]" in result
        assert "some text" in result

    def test_empty_text_fallback(self):
        sec = Section(kind=SectionKind.FIGURE, text="",
                      confidence=Confidence.HIGH, lines=[])
        result = _render_figure_placeholder(sec)
        assert result == "> **[Figure]**"

    def test_graph_horizontal_chain_with_labels(self):
        graph = FigureGraph(
            nodes=[
                FigureNode(text="", bbox=(63, 566, 128, 590)),
                FigureNode(text="", bbox=(212, 566, 277, 590)),
                FigureNode(text="", bbox=(369, 566, 432, 590)),
            ],
            edges=[
                FigureEdge(source_idx=0, target_idx=1),
                FigureEdge(source_idx=1, target_idx=2),
            ],
        )
        sec = _make_figure_section([
            ("IoAwaitable", (69, 572, 123, 584)),
            ("refined by", (147, 572, 193, 584)),
            ("IoRunnable", (218, 572, 271, 584)),
            ("modeled by", (296, 572, 350, 584)),
            ("io_task<T>", (374, 572, 426, 584)),
        ])
        sec.figure_graph = graph
        result = _render_figure_placeholder(sec)
        assert "Concept Chain" in result
        assert "IoAwaitable -> refined by -> IoRunnable -> modeled by -> io_task<T>" in result

    def test_graph_vertical_flow_numbered(self):
        graph = FigureGraph(
            nodes=[
                FigureNode(text="", bbox=(95, 63, 307, 86)),
                FigureNode(text="", bbox=(153, 124, 249, 147)),
                FigureNode(text="", bbox=(63, 184, 339, 208)),
            ],
            edges=[
                FigureEdge(source_idx=0, target_idx=1),
                FigureEdge(source_idx=1, target_idx=2),
            ],
        )
        sec = _make_figure_section([
            ("step A", (100, 70, 300, 82)),
            ("step B", (160, 130, 240, 142)),
            ("step C", (70, 190, 330, 202)),
        ])
        sec.figure_graph = graph
        result = _render_figure_placeholder(sec)
        assert "Flow Diagram" in result
        assert "> 1. step A" in result
        assert "> 2. step B" in result
        assert "> 3. step C" in result

    def test_graph_bidirectional(self):
        graph = FigureGraph(
            nodes=[
                FigureNode(text="A", bbox=(50, 50, 150, 80)),
                FigureNode(text="B", bbox=(250, 50, 350, 80)),
            ],
            edges=[
                FigureEdge(source_idx=0, target_idx=1, bidirectional=True),
            ],
        )
        sec = _make_figure_section([
            ("A", (60, 55, 140, 75)),
            ("B", (260, 55, 340, 75)),
        ])
        sec.figure_graph = graph
        result = _render_figure_placeholder(sec)
        assert "bidirectional" in result
        assert "<->" in result


class TestIsArrowhead:
    def test_small_filled_triangle(self):
        result = _is_arrowhead(_make_arrowhead_path(
            [(200, 575), (210, 570), (210, 580)]))
        assert result is not None
        centroid, pointy, base = result
        assert abs(pointy[0] - 200) < 2

    def test_large_shape_rejected(self):
        result = _is_arrowhead(_make_arrowhead_path(
            [(100, 100), (200, 100), (150, 200)]))
        assert result is None

    def test_open_path_rejected(self):
        p = _make_arrowhead_path([(200, 575), (210, 570), (210, 580)])
        p["closePath"] = False
        assert _is_arrowhead(p) is None

    def test_no_fill_rejected(self):
        p = _make_arrowhead_path([(200, 575), (210, 570), (210, 580)])
        p["fill"] = None
        assert _is_arrowhead(p) is None


class TestIsConnector:
    def test_simple_line(self):
        result = _is_connector(_make_connector_path((128, 578), (208, 578)))
        assert result is not None
        start, end = result
        assert abs(start[0] - 128) < 1
        assert abs(end[0] - 208) < 1

    def test_closed_path_rejected(self):
        p = _make_connector_path((128, 578), (208, 578))
        p["closePath"] = True
        assert _is_connector(p) is None

    def test_filled_path_rejected(self):
        p = _make_connector_path((128, 578), (208, 578))
        p["fill"] = (1, 1, 1)
        assert _is_connector(p) is None

    def test_too_short_rejected(self):
        result = _is_connector(_make_connector_path((100, 100), (102, 100)))
        assert result is None


class TestMatchTopology:
    def test_linear_chain_via_connectors(self):
        boxes = [
            (63, 566, 128, 590),
            (212, 566, 277, 590),
            (369, 566, 432, 590),
        ]
        connectors = [
            ((128, 578), (212, 578)),
            ((277, 578), (369, 578)),
        ]
        graph = _match_topology(boxes, [], connectors)
        assert graph is not None
        assert len(graph.edges) == 2
        assert graph.is_linear

    def test_with_arrowheads(self):
        boxes = [
            (50, 50, 150, 80),
            (250, 50, 350, 80),
        ]
        arrowheads = [
            ((245, 65), (250, 65), (240, 65)),
        ]
        connectors = [
            ((150, 65), (240, 65)),
        ]
        graph = _match_topology(boxes, arrowheads, connectors)
        assert graph is not None
        assert len(graph.edges) == 1
        assert graph.edges[0].source_idx == 0
        assert graph.edges[0].target_idx == 1

    def test_no_boxes_returns_none(self):
        assert _match_topology([], [], []) is None

    def test_no_connections_returns_none(self):
        boxes = [(50, 50, 150, 80), (250, 50, 350, 80)]
        assert _match_topology(boxes, [], []) is None


class TestMergeConnectedGroups:
    def test_no_merge_without_connectors(self):
        g1 = [(50, 50, 150, 80), (200, 50, 300, 80)]
        g2 = [(50, 300, 150, 330), (200, 300, 300, 330)]
        result = _merge_connected_groups([g1, g2], [])
        assert len(result) == 2

    def test_merge_groups_bridged_by_connector(self):
        g1 = [(50, 50, 150, 80), (200, 50, 300, 80)]
        g2 = [(50, 300, 150, 330), (200, 300, 300, 330)]
        connectors = [((100, 80), (100, 300))]
        result = _merge_connected_groups([g1, g2], connectors)
        assert len(result) == 1
        assert len(result[0]) == 4

    def test_single_group_unchanged(self):
        g = [(50, 50, 150, 80)]
        result = _merge_connected_groups([g], [((0, 0), (500, 500))])
        assert len(result) == 1
        assert result[0] is g

    def test_three_groups_chain_merge(self):
        g1 = [(50, 50, 150, 80)]
        g2 = [(50, 200, 150, 230)]
        g3 = [(50, 400, 150, 430)]
        connectors = [
            ((100, 80), (100, 200)),
            ((100, 230), (100, 400)),
        ]
        result = _merge_connected_groups([g1, g2, g3], connectors)
        assert len(result) == 1
        assert len(result[0]) == 3


class TestIsDashedConnector:
    def _make_dashed_path(self, x0, y0, x1, y1, n_items=30):
        pts = []
        for i in range(n_items):
            sx = x0 + (x1 - x0) * i / n_items
            ex = x0 + (x1 - x0) * (i + 0.5) / n_items
            pts.append(("l", _FakePoint(sx, y0), _FakePoint(ex, y0)))
        return {
            "rect": _FakeRect(x0, y0, x1, y1),
            "items": pts,
            "fill": (0.2, 0.2, 0.2),
            "color": None,
            "closePath": None,
        }

    def test_horizontal_dashed_detected(self):
        p = self._make_dashed_path(100, 300, 400, 301)
        result = _is_dashed_connector(p)
        assert result is not None
        start, end = result
        assert start[0] > end[0]

    def test_too_few_items_rejected(self):
        p = self._make_dashed_path(100, 300, 400, 301, n_items=5)
        assert _is_dashed_connector(p) is None

    def test_too_thick_rejected(self):
        p = self._make_dashed_path(100, 300, 400, 310)
        assert _is_dashed_connector(p) is None

    def test_with_color_rejected(self):
        p = self._make_dashed_path(100, 300, 400, 301)
        p["color"] = (0, 0, 0)
        assert _is_dashed_connector(p) is None


class TestDetectSequenceDiagram:
    def _make_seq_boxes(self):
        """4 participants, top and bottom rows."""
        return [
            (81, 195, 153, 226), (203, 195, 275, 226),
            (340, 195, 412, 226), (442, 195, 514, 226),
            (81, 411, 153, 442), (203, 411, 275, 442),
            (340, 411, 412, 442), (442, 411, 514, 442),
        ]

    def _make_seq_connectors(self):
        """6 horizontal arrows + 4 vertical lifelines."""
        return [
            ((117, 198), (117, 411)),
            ((239, 198), (239, 411)),
            ((376, 198), (376, 411)),
            ((478, 198), (478, 411)),
            ((117, 248), (237, 248)),
            ((117, 270), (237, 270)),
            ((117, 292), (237, 292)),
            ((117, 314), (237, 314)),
            ((240, 336), (374, 336)),
            ((376, 358), (476, 358)),
        ]

    def test_detects_sequence_pattern(self):
        boxes = self._make_seq_boxes()
        connectors = self._make_seq_connectors()
        graph = _detect_sequence_diagram(boxes, [], connectors)
        assert graph is not None
        assert len(graph.nodes) == 4
        assert len(graph.edges) >= 6
        assert getattr(graph, "_is_sequence", False) is True

    def test_rejects_non_sequence_layout(self):
        boxes = [
            (50, 50, 150, 80), (200, 50, 300, 80),
            (350, 50, 450, 80),
        ]
        connectors = [((150, 65), (200, 65)), ((300, 65), (350, 65))]
        result = _detect_sequence_diagram(boxes, [], connectors)
        assert result is None

    def test_too_few_boxes_rejected(self):
        boxes = [(50, 50, 150, 80), (50, 200, 150, 230)]
        connectors = [((100, 80), (100, 200))]
        result = _detect_sequence_diagram(boxes, [], connectors)
        assert result is None


class TestRenderSequenceDiagram:
    def test_sequence_rendering(self):
        nodes = [
            FigureNode(text="", bbox=(81, 195, 153, 442)),
            FigureNode(text="", bbox=(203, 195, 275, 442)),
            FigureNode(text="", bbox=(340, 195, 412, 442)),
        ]
        edges = [
            FigureEdge(source_idx=0, target_idx=1, y_position=248),
            FigureEdge(source_idx=1, target_idx=2, y_position=336),
        ]
        graph = FigureGraph(nodes=nodes, edges=edges)
        graph._is_sequence = True

        lines = [
            Line(spans=[Span(text="A")], bbox=(99, 207, 134, 215)),
            Line(spans=[Span(text="B")], bbox=(220, 207, 258, 215)),
            Line(spans=[Span(text="C")], bbox=(360, 207, 392, 215)),
            Line(spans=[Span(text="A")], bbox=(99, 422, 134, 431)),
            Line(spans=[Span(text="B")], bbox=(220, 422, 258, 431)),
            Line(spans=[Span(text="C")], bbox=(360, 422, 392, 431)),
            Line(spans=[Span(text="call()")], bbox=(150, 236, 200, 245)),
            Line(spans=[Span(text="forward()")], bbox=(280, 324, 350, 333)),
        ]

        sec = Section(
            kind=SectionKind.FIGURE,
            text="",
            lines=lines,
            figure_graph=graph,
        )

        result = _render_figure_placeholder(sec)
        assert result.startswith("```mermaid")
        assert "sequenceDiagram" in result
        assert "participant A" in result
        assert "participant B" in result
        assert "participant C" in result
        assert "A->>B: call()" in result
        assert "B->>C: forward()" in result
