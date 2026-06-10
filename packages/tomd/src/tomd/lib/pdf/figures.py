# Copyright (c) 2026 C++ Alliance, Inc. (https://cppalliance.org)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Detect inline figure regions (flow diagrams, box-arrow figures) from PDF
vector graphics.

Uses page.get_drawings() to find bordered rectangles (boxes with stroke color)
that appear 2+ times in proximity, indicating a diagram rather than body text.

Design B (conservative): detection only produces figure region bboxes.
No blocks are excluded from extraction.  The heading guard in structure.py
uses these bboxes to re-classify diagram text as SectionKind.FIGURE instead
of HEADING.
"""

import logging
import math
from .types import FigureRegion, FigureGraph, FigureNode, FigureEdge

_log = logging.getLogger(__name__)

_BOX_MIN_WIDTH = 15.0
_BOX_MIN_HEIGHT = 8.0
_BOX_MAX_PAGE_WIDTH_RATIO = 0.6
_BOX_GROUP_Y_TOLERANCE = 80.0
_BOX_GROUP_X_TOLERANCE = 300.0
_MIN_BOXES_FOR_FIGURE = 2
_FIGURE_BBOX_MARGIN = 5.0
_BRIDGE_TOLERANCE = 25.0
_CONNECTOR_THIN_SIDE_MAX = 3.0
_CONNECTOR_MIN_HORIZ_DX = 5.0
_CONNECTOR_REGION_MARGIN = 15.0

_ARROWHEAD_MAX_SIZE = 20.0
_ARROWHEAD_MIN_ITEMS = 2
_ARROWHEAD_MAX_ITEMS = 4
_CONNECTOR_MIN_LENGTH = 5.0
_ENDPOINT_TOLERANCE = 12.0


def _is_bordered_box(path: dict, page_width: float) -> tuple | None:
    """Return the rect tuple if *path* is a bordered box suitable for
    figure detection, else None.

    A bordered box has a non-None stroke color AND a non-None fill,
    with dimensions above minimum thresholds and below page-width ratio.
    This distinguishes diagram boxes (colored border + fill) from
    table cells (fill only, no border) and decorative rules.
    """
    color = path.get("color")
    fill = path.get("fill")
    if color is None or fill is None:
        return None

    rect = path.get("rect")
    if rect is None:
        return None

    w, h = rect.width, rect.height
    if w < _BOX_MIN_WIDTH or h < _BOX_MIN_HEIGHT:
        return None
    if w > page_width * _BOX_MAX_PAGE_WIDTH_RATIO:
        return None

    items = path.get("items", [])
    if not items:
        return None

    types = [it[0] for it in items]
    has_re = "re" in types
    has_lines = "l" in types
    if not (has_re or has_lines):
        return None

    return (rect.x0, rect.y0, rect.x1, rect.y1)


def _group_boxes(boxes: list[tuple]) -> list[list[tuple]]:
    """Group bordered boxes by spatial proximity.

    Boxes within _BOX_GROUP_Y_TOLERANCE vertically and
    _BOX_GROUP_X_TOLERANCE horizontally are placed in the same group.
    Simple single-pass greedy merge.
    """
    if not boxes:
        return []

    sorted_boxes = sorted(boxes, key=lambda b: (b[1], b[0]))
    groups: list[list[tuple]] = [[sorted_boxes[0]]]

    for box in sorted_boxes[1:]:
        merged = False
        for group in groups:
            for member in group:
                y_close = abs(box[1] - member[1]) < _BOX_GROUP_Y_TOLERANCE
                x_close = abs(box[0] - member[0]) < _BOX_GROUP_X_TOLERANCE
                if y_close and x_close:
                    group.append(box)
                    merged = True
                    break
            if merged:
                break
        if not merged:
            groups.append([box])

    return groups


def _merge_connected_groups(
    groups: list[list[tuple]],
    connectors: list[tuple],
) -> list[list[tuple]]:
    """Merge box groups that are bridged by connectors.

    A connector bridges two groups when one endpoint is near a box in
    group A and the other endpoint is near a box in group B.  This
    handles sequence diagrams and other large diagrams whose box rows
    are separated by more than ``_BOX_GROUP_Y_TOLERANCE``.
    """
    if len(groups) <= 1 or not connectors:
        return groups

    def _pt_near_any_box(pt: tuple[float, float],
                         boxes: list[tuple]) -> bool:
        for b in boxes:
            if _point_to_box_edge_dist(pt, b) < _BRIDGE_TOLERANCE:
                return True
        return False

    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for cs, ce in connectors:
        src_group = None
        tgt_group = None
        for gi, grp in enumerate(groups):
            if src_group is None and _pt_near_any_box(cs, grp):
                src_group = gi
            if tgt_group is None and _pt_near_any_box(ce, grp):
                tgt_group = gi
            if src_group is not None and tgt_group is not None:
                break
        if (src_group is not None and tgt_group is not None
                and src_group != tgt_group):
            union(src_group, tgt_group)

    merged: dict[int, list[tuple]] = {}
    for gi, grp in enumerate(groups):
        root = find(gi)
        merged.setdefault(root, []).extend(grp)

    return list(merged.values())


def _group_bbox(boxes: list[tuple]) -> tuple[float, float, float, float]:
    """Compute the bounding box enclosing all boxes, with a small margin."""
    m = _FIGURE_BBOX_MARGIN
    return (
        min(b[0] for b in boxes) - m,
        min(b[1] for b in boxes) - m,
        max(b[2] for b in boxes) + m,
        max(b[3] for b in boxes) + m,
    )


def _is_arrowhead(path: dict) -> tuple | None:
    """Return ``(centroid, pointy_vertex, base_midpoint)`` if *path* looks
    like a small filled triangle used as an arrowhead, else ``None``.

    Arrowheads in PDFs are typically closed, filled paths with 3 line
    segments forming a triangle smaller than ``_ARROWHEAD_MAX_SIZE``.
    Also accepts open triangles (closePath=False) when both fill and
    color are present, common for dashed return arrow arrowheads.
    """
    is_closed = path.get("closePath", False)
    has_fill = path.get("fill") is not None
    has_color = path.get("color") is not None
    if not has_fill:
        return None
    if not is_closed and not has_color:
        return None

    rect = path.get("rect")
    if rect is None:
        return None
    if rect.width > _ARROWHEAD_MAX_SIZE or rect.height > _ARROWHEAD_MAX_SIZE:
        return None

    items = path.get("items", [])
    if not (_ARROWHEAD_MIN_ITEMS <= len(items) <= _ARROWHEAD_MAX_ITEMS):
        return None

    pts: list[tuple[float, float]] = []
    for it in items:
        if it[0] == "l":
            p1, p2 = it[1], it[2]
            if not pts or (pts[-1][0] != p1.x or pts[-1][1] != p1.y):
                pts.append((p1.x, p1.y))
            pts.append((p2.x, p2.y))
        elif it[0] == "c":
            p1, p4 = it[1], it[4]
            if not pts or (pts[-1][0] != p1.x or pts[-1][1] != p1.y):
                pts.append((p1.x, p1.y))
            pts.append((p4.x, p4.y))

    unique: list[tuple[float, float]] = []
    for p in pts:
        if not unique or (abs(p[0] - unique[-1][0]) > 0.5
                          or abs(p[1] - unique[-1][1]) > 0.5):
            unique.append(p)
    if len(unique) >= 2 and (abs(unique[0][0] - unique[-1][0]) < 0.5
                              and abs(unique[0][1] - unique[-1][1]) < 0.5):
        unique.pop()

    if len(unique) < 3:
        return None

    cx = sum(p[0] for p in unique) / len(unique)
    cy = sum(p[1] for p in unique) / len(unique)

    dists = [(math.hypot(p[0] - cx, p[1] - cy), i) for i, p in enumerate(unique)]
    dists.sort(reverse=True)
    pointy_idx = dists[0][1]
    pointy = unique[pointy_idx]

    others = [unique[i] for i in range(len(unique)) if i != pointy_idx]
    base_mid = (sum(p[0] for p in others) / len(others),
                sum(p[1] for p in others) / len(others))

    return ((cx, cy), pointy, base_mid)


def _is_connector(path: dict) -> tuple | None:
    """Return ``(start_point, end_point)`` if *path* is a connector line
    (arrow shaft), else ``None``.

    Connectors are open stroked paths (no fill) made of line segments
    or curves, with total span above ``_CONNECTOR_MIN_LENGTH``.
    Axis-aligned short segments that look like table grid lines are rejected.
    """
    if path.get("closePath", False):
        return None
    if path.get("fill") is not None:
        return None
    if path.get("color") is None:
        return None

    items = path.get("items", [])
    if not items:
        return None
    if not all(it[0] in ("l", "c") for it in items):
        return None

    first = items[0]
    last = items[-1]
    if first[0] == "l":
        start = (first[1].x, first[1].y)
    elif first[0] == "c":
        start = (first[1].x, first[1].y)
    else:
        return None

    if last[0] == "l":
        end = (last[2].x, last[2].y)
    elif last[0] == "c":
        end = (last[4].x, last[4].y)
    else:
        return None

    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length < _CONNECTOR_MIN_LENGTH:
        return None

    return (start, end)


def _is_dashed_connector(path: dict) -> tuple | None:
    """Return ``(start_point, end_point)`` if *path* is a dashed line
    rendered as many tiny filled segments (common for UML return arrows).

    These paths have fill but no stroke color, many line items, and
    a very small height (< 3pt) indicating a near-horizontal or
    near-vertical dashed line.
    """
    if path.get("color") is not None:
        return None
    if path.get("fill") is None:
        return None

    items = path.get("items", [])
    if len(items) < 20:
        return None
    if not all(it[0] == "l" for it in items):
        return None

    rect = path.get("rect")
    if rect is None:
        return None

    w, h = rect.width, rect.height
    if min(w, h) > _CONNECTOR_THIN_SIDE_MAX:
        return None
    if max(w, h) < _CONNECTOR_MIN_LENGTH:
        return None

    mid_y = (rect.y0 + rect.y1) / 2
    start = (rect.x1, mid_y)
    end = (rect.x0, mid_y)
    if h > w:
        mid_x = (rect.x0 + rect.x1) / 2
        start = (mid_x, rect.y1)
        end = (mid_x, rect.y0)

    return (start, end)


def _point_to_box_edge_dist(
    pt: tuple[float, float],
    box: tuple[float, float, float, float],
) -> float:
    """Minimum distance from a point to the nearest edge of a box rect."""
    x, y = pt
    x0, y0, x1, y1 = box
    cx = max(x0, min(x, x1))
    cy = max(y0, min(y, y1))
    return math.hypot(x - cx, y - cy)


_COLUMN_X_TOLERANCE = 20.0


def _project_through_box(
    pointy: tuple[float, float],
    base: tuple[float, float],
    box_idx: int,
    boxes: list[tuple[float, float, float, float]],
) -> int:
    """If an arrowhead's tip is inside a box, project the arrow direction
    to find the actual target box further along.

    Returns *box_idx* unchanged when the tip is on the box edge or when
    no further box is found in the arrow direction.
    """
    box = boxes[box_idx]
    px, py = pointy
    if not (box[0] < px < box[2] and box[1] < py < box[3]):
        return box_idx

    dx = pointy[0] - base[0]
    if abs(dx) < 0.1:
        return box_idx

    best_idx = None
    best_dist = float("inf")
    for i, b in enumerate(boxes):
        if i == box_idx:
            continue
        cx = (b[0] + b[2]) / 2
        if dx < 0 and cx >= box[0]:
            continue
        if dx > 0 and cx <= box[2]:
            continue
        d = abs(cx - px)
        if d < best_dist:
            best_dist = d
            best_idx = i

    return best_idx if best_idx is not None else box_idx


def _detect_sequence_diagram(
    boxes: list[tuple[float, float, float, float]],
    arrowheads: list[tuple],
    connectors: list[tuple],
    dashed_indices: set[int] | None = None,
) -> FigureGraph | None:
    """Detect a UML-style sequence diagram and build a collapsed graph.

    Sequence diagrams have duplicate boxes at the same x-position (top
    and bottom participant headers) with horizontal arrows between
    vertical lifelines.  This function collapses duplicate-x boxes into
    single logical nodes and creates edges from horizontal
    connectors/arrowheads, ordered by y-position.

    Returns None if the box layout does not match the sequence pattern.
    """
    if len(boxes) < 4:
        return None

    columns: dict[int, list[tuple]] = {}
    for b in boxes:
        cx = round(((b[0] + b[2]) / 2) / _COLUMN_X_TOLERANCE)
        columns.setdefault(cx, []).append(b)

    multi_row_cols = [col for col in columns.values() if len(col) >= 2]
    if len(multi_row_cols) < 2:
        return None

    logical: list[tuple[float, float, float, float]] = []
    col_order = sorted(columns.keys())
    for ck in col_order:
        col_boxes = columns[ck]
        x0 = min(b[0] for b in col_boxes)
        y0 = min(b[1] for b in col_boxes)
        x1 = max(b[2] for b in col_boxes)
        y1 = max(b[3] for b in col_boxes)
        logical.append((x0, y0, x1, y1))

    nodes = [FigureNode(text="", bbox=b) for b in logical]

    arrow_events: list[tuple[float, int, int, bool]] = []

    for ah_centroid, ah_pointy, ah_base in arrowheads:
        tgt_idx = _nearest_box(ah_pointy, logical)
        if tgt_idx is None:
            continue

        src_idx = None
        is_dashed_arrow = False
        for ci, (cs, ce) in enumerate(connectors):
            d0 = math.hypot(ah_base[0] - cs[0], ah_base[1] - cs[1])
            d1 = math.hypot(ah_base[0] - ce[0], ah_base[1] - ce[1])
            if d0 < _ENDPOINT_TOLERANCE:
                candidate = _nearest_box(ce, logical)
                if candidate is not None and candidate != tgt_idx:
                    src_idx = candidate
                    is_dashed_arrow = (dashed_indices is not None
                                       and ci in dashed_indices)
                    break
            elif d1 < _ENDPOINT_TOLERANCE:
                candidate = _nearest_box(cs, logical)
                if candidate is not None and candidate != tgt_idx:
                    src_idx = candidate
                    is_dashed_arrow = (dashed_indices is not None
                                       and ci in dashed_indices)
                    break

        if src_idx is None:
            src_idx = _nearest_box(ah_base, logical)

        if (is_dashed_arrow and src_idx is not None
                and src_idx != tgt_idx):
            projected = _project_through_box(
                ah_pointy, ah_base, tgt_idx, logical,
            )
            if projected == 0 or projected == len(logical) - 1:
                tgt_idx = projected

        if src_idx is None or src_idx == tgt_idx:
            continue
        y_pos = ah_centroid[1]
        arrow_events.append((y_pos, src_idx, tgt_idx, is_dashed_arrow))

    if not arrow_events and connectors:
        for ci, (cs, ce) in enumerate(connectors):
            dx = abs(ce[0] - cs[0])
            if dx < _CONNECTOR_MIN_HORIZ_DX:
                continue
            si = _nearest_box(cs, logical)
            ti = _nearest_box(ce, logical)
            if si is not None and ti is not None and si != ti:
                y_pos = (cs[1] + ce[1]) / 2
                is_dashed = (dashed_indices is not None
                             and ci in dashed_indices)
                arrow_events.append((y_pos, si, ti, is_dashed))

    if not arrow_events:
        return None

    arrow_events.sort(key=lambda ev: ev[0])

    edges: list[FigureEdge] = []
    for y_pos, si, ti, is_dashed in arrow_events:
        edges.append(FigureEdge(source_idx=si, target_idx=ti,
                                dashed=is_dashed, y_position=y_pos))

    _log.debug(
        "Sequence diagram detected: %d participants, %d arrows",
        len(nodes), len(edges),
    )
    graph = FigureGraph(nodes=nodes, edges=edges)
    graph._is_sequence = True  # type: ignore[attr-defined]
    return graph


def _match_topology(
    boxes: list[tuple[float, float, float, float]],
    arrowheads: list[tuple],
    connectors: list[tuple],
    dashed_indices: set[int] | None = None,
) -> FigureGraph | None:
    """Build a directed graph from detected boxes, arrowheads, and connectors.

    First attempts sequence diagram detection (duplicate-x columns).
    Falls back to the general arrowhead/connector matching approach.
    Returns None if no edges can be resolved.
    """
    if not boxes:
        return None

    seq = _detect_sequence_diagram(boxes, arrowheads, connectors,
                                   dashed_indices)
    if seq is not None:
        return seq

    nodes = [FigureNode(text="", bbox=b) for b in boxes]
    edges: list[FigureEdge] = []
    used_connectors: set[int] = set()

    for ah_centroid, ah_pointy, ah_base in arrowheads:
        target_idx = _nearest_box(ah_pointy, boxes)
        if target_idx is None:
            continue

        best_conn = _nearest_connector_endpoint(ah_base, connectors,
                                                 used_connectors)
        if best_conn is None:
            continue

        conn_idx, matched_end_idx = best_conn
        conn_start, conn_end = connectors[conn_idx]
        other_end = conn_start if matched_end_idx == 1 else conn_end

        source_idx = _nearest_box(other_end, boxes)
        if source_idx is None or source_idx == target_idx:
            continue

        existing = _find_edge(edges, source_idx, target_idx)
        if existing is not None:
            continue
        reverse = _find_edge(edges, target_idx, source_idx)
        if reverse is not None:
            reverse.bidirectional = True
        else:
            edges.append(FigureEdge(source_idx=source_idx,
                                     target_idx=target_idx))
        used_connectors.add(conn_idx)

    if not edges and connectors and len(boxes) >= 2:
        for ci, (cs, ce) in enumerate(connectors):
            si = _nearest_box(cs, boxes)
            ti = _nearest_box(ce, boxes)
            if si is not None and ti is not None and si != ti:
                if _find_edge(edges, si, ti) is None:
                    edges.append(FigureEdge(source_idx=si, target_idx=ti))

    if not edges:
        return None

    return FigureGraph(nodes=nodes, edges=edges)


def _nearest_box(
    pt: tuple[float, float],
    boxes: list[tuple[float, float, float, float]],
) -> int | None:
    """Index of the box whose edge is closest to *pt*, within tolerance."""
    best_dist = _ENDPOINT_TOLERANCE
    best_idx = None
    for i, box in enumerate(boxes):
        d = _point_to_box_edge_dist(pt, box)
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def _nearest_connector_endpoint(
    pt: tuple[float, float],
    connectors: list[tuple],
    used: set[int],
) -> tuple[int, int] | None:
    """Return ``(connector_index, endpoint_index)`` for the connector
    endpoint closest to *pt*, skipping already-used connectors.
    ``endpoint_index`` is 0 for start, 1 for end.
    """
    best_dist = _ENDPOINT_TOLERANCE
    best: tuple[int, int] | None = None
    for i, (cs, ce) in enumerate(connectors):
        if i in used:
            continue
        d0 = math.hypot(pt[0] - cs[0], pt[1] - cs[1])
        d1 = math.hypot(pt[0] - ce[0], pt[1] - ce[1])
        d, ei = (d0, 0) if d0 < d1 else (d1, 1)
        if d < best_dist:
            best_dist = d
            best = (i, ei)
    return best


def _find_edge(edges: list[FigureEdge], src: int, tgt: int) -> FigureEdge | None:
    """Find an existing edge between src and tgt."""
    for e in edges:
        if e.source_idx == src and e.target_idx == tgt:
            return e
    return None


def detect_figure_regions(
    drawings: list[dict],
    page_num: int,
    page_width: float,
) -> list[FigureRegion]:
    """Detect figure regions on a single page from its vector drawings.

    Returns a list of FigureRegion for regions that contain 2+ bordered
    boxes in close proximity (indicating a flow diagram or similar figure).

    Args:
        drawings: output of page.get_drawings()
        page_num: 0-based page number
        page_width: page width in points (for filtering page-wide rects)
    """
    bordered_boxes = []
    arrowheads = []
    connectors = []
    dashed_indices: set[int] = set()

    for path in drawings:
        box = _is_bordered_box(path, page_width)
        if box is not None:
            bordered_boxes.append(box)
            continue

        ah = _is_arrowhead(path)
        if ah is not None:
            arrowheads.append(ah)
            continue

        conn = _is_connector(path)
        if conn is not None:
            connectors.append(conn)
            continue

        dashed = _is_dashed_connector(path)
        if dashed is not None:
            dashed_indices.add(len(connectors))
            connectors.append(dashed)

    if len(bordered_boxes) < _MIN_BOXES_FOR_FIGURE:
        return []

    groups = _group_boxes(bordered_boxes)
    groups = _merge_connected_groups(groups, connectors)

    regions = []
    for group in groups:
        if len(group) < _MIN_BOXES_FOR_FIGURE:
            continue
        bbox = _group_bbox(group)

        region_ah = [a for a in arrowheads
                     if bbox[0] - 10 <= a[0][0] <= bbox[2] + 10
                     and bbox[1] - 10 <= a[0][1] <= bbox[3] + 10]
        region_conn = []
        region_dashed: set[int] = set()
        for ci, c in enumerate(connectors):
            if _connector_in_region(c, bbox):
                if ci in dashed_indices:
                    region_dashed.add(len(region_conn))
                region_conn.append(c)

        graph = _match_topology(group, region_ah, region_conn,
                                region_dashed)
        if graph:
            _log.debug(
                "Figure graph on page %d: %d nodes, %d edges",
                page_num, len(graph.nodes), len(graph.edges),
            )

        region = FigureRegion(page_num=page_num, bbox=bbox, graph=graph)
        _log.debug(
            "Figure region on page %d: bbox=(%.0f,%.0f,%.0f,%.0f) "
            "boxes=%d",
            page_num, bbox[0], bbox[1], bbox[2], bbox[3], len(group),
        )
        regions.append(region)

    return regions


def _connector_in_region(
    conn: tuple[tuple[float, float], tuple[float, float]],
    bbox: tuple[float, float, float, float],
) -> bool:
    """True if at least one endpoint of the connector is inside the region."""
    margin = _CONNECTOR_REGION_MARGIN
    x0, y0, x1, y1 = bbox
    for pt in conn:
        if (x0 - margin <= pt[0] <= x1 + margin
                and y0 - margin <= pt[1] <= y1 + margin):
            return True
    return False
