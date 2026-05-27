#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Vector-figure extraction via path-operator clustering.

Independent of :mod:`tomd.lib.pdf.images`, which only sees raster
XObjects pulled from each page's resource dictionary. This module
enumerates a page's vector drawing operations via
``pymupdf.Page.get_drawings()``, clusters spatially adjacent strokes
into figure candidates, filters clusters that don't look like figures
(too small, too text-overlapped, in the page edge band,
ins/del-coloured), and rasterises each surviving cluster to PNG via
``page.get_pixmap(clip=cluster_bbox, dpi=_RASTERISE_DPI)``.

The heuristic is intentional. Each constant is documented inline with
its calibration rationale, and the per-paper uncertainty marker
(:data:`VECTOR_UNCERTAINTY_MARKER_TEMPLATE`) surfaces every drop so a
reader can see why a diagram was missed.

Outputs feed :func:`tomd.lib.pdf.images.finalize_extraction` via
:class:`_PageImageCandidate` records with ``source="vector"`` and a
synthetic negative ``xref`` from :func:`_synthetic_xref` so the cap
and dedup paths handle raster + vector symmetrically.

What we deliberately do NOT do here:

- No grid-pattern proxy. An earlier draft of the plan proposed one to
  distinguish tables from diagrams, but it false-rejected legitimate
  swim-lane diagrams, sequence diagrams with three or more lifelines,
  and comparison matrices drawn as aligned rectangles. The text-
  overlap filter handles the table case structurally (tables have
  text in their cells; diagrams generally do not).
- No per-drawing-item colour walk in :func:`_is_ins_del_coloured`.
  ``drawing["items"]`` tuples carry Point and Rect coordinates whose
  values could land in a wording hue band by coincidence and silently
  exclude real diagrams.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import pymupdf

from .images import (
    _PageImageCandidate,
    _VectorExtractionStats,
    VectorUncertaintyStats,
    _caption_for,
)
from .wording import is_wording_rgb

if TYPE_CHECKING:
    from .types import Block

_log = logging.getLogger(__name__)


# ---- Tunable thresholds (CLAUDE.md: named module-level constants) ----------

# A page must produce at least this many drawing items before we treat
# it as potentially containing vector figures. The floor is an early-
# exit optimisation; the real noise rejection happens in the per-cluster
# filters (_MIN_CLUSTER_ITEM_COUNT, _MIN_CLUSTER_DIM_PT,
# _MAX_TEXT_OVERLAP_FRACTION, _MAX_CLUSTER_AREA_FRACTION) and the
# pipeline-level structural-overlap filter that drops vectors covering
# TABLE/CODE regions. Real diagrams can be quite sparse: P3556R0's
# page-3 flowchart clocks only ~115 items (boxes + arrowheads + labels).
# 100 admits sparse flowcharts while still skipping pages whose entire
# vector content is page chrome (running rules, header underlines).
_MIN_PAGE_DRAWING_ITEMS = 100

# Single-linkage clustering distance: drawings whose bboxes are within
# this many pt of each other are merged. Calibrated against the corpus
# (gap between graph nodes ~ 15-25pt; gap between separate diagrams
# typically > 80pt). At 30pt we keep one diagram together without
# absorbing a neighbouring code block.
_CLUSTER_LINK_DISTANCE_PT = 30.0

# Minimum cluster bbox dimensions (width AND height). Below this we
# treat the cluster as decoration (horizontal rules, bullet glyphs,
# checkbox icons, page borders). v1 already uses 20pt for raster
# emoji; vector decorations are typically thinner so we set a higher
# floor here. Smallest genuine diagram in the survey is ~80pt tall
# (P3127R1's adjacency graph).
#
# Virtual clusters produced by container detection (see
# :func:`_detect_frame_drawings`) get a relaxed floor of
# :data:`_VIRTUAL_MIN_CLUSTER_DIM_PT` to admit horizontal flow
# diagrams whose container is intentionally short and wide
# (P4003R1's IoAwaitable diagram is 381 x 35 pt).
_MIN_CLUSTER_DIM_PT = 60.0

# Maximum overlap (intersection-over-cluster-area) with text block
# bboxes before we reject the cluster as text decoration. Tables,
# code-block backgrounds, and wording highlights all sit beneath
# text and would otherwise survive every other filter. 0.35 keeps
# diagrams whose caption label happens to bleed slightly into the
# cluster's bottom edge but rejects clusters that are mostly
# coincident with a text region. Clusters that look like real
# diagrams - either dense-with-internal-labels or large-and-sparse -
# bypass this check via the diagram thresholds below.
_MAX_TEXT_OVERLAP_FRACTION = 0.35

# Diagram-bypass for the text-overlap check. The bypass exists because
# the spatial extraction path bundles scattered diagram labels into a
# single tall bbox; that bbox overlaps the vector cluster and inflates
# the text-overlap measure beyond _MAX_TEXT_OVERLAP_FRACTION even when
# the cluster is a real diagram whose own boxes/nodes enclose the
# labels.
#
# Two paths, both requiring _DIAGRAM_MIN_AREA_PT2 to rule out small
# annotation boxes:
#
# 1. Dense path (P3556R0 page 3 "Process" flowchart style, where a
#    flowchart packs many strokes into its bbox and labels inside the
#    boxes drive overlap into the 0.5-0.65 band):
#      items >= _DIAGRAM_DENSE_MIN_ITEMS
#      AND density >= _DIAGRAM_DENSE_MIN_DENSITY
#      AND overlap < _DIAGRAM_DENSE_MAX_OVERLAP
#
# 2. Sparse path (P3127R1 page 6 network-graph style: an adjacency
#    or node-and-link diagram with few strokes per area, where set-
#    description text near the diagram inflates overlap into the
#    0.35-0.50 band):
#      items >= _DIAGRAM_SPARSE_MIN_ITEMS
#      AND overlap < _DIAGRAM_SPARSE_MAX_OVERLAP
#
# Calibration: against the full P4003R1 + P3556R0 + P3127R1 corpus,
# the dense path admits P3556R0 Fig 2 (114 items / 39kpt^2 / d=0.0029
# / ov=0.56) and the sparse path admits P3127R1 Fig 1 (58 items /
# 78kpt^2 / d=0.00074 / ov=0.46) while neither admits the P4003R1
# code-block-background false positives (typically items < 50 once
# area >= 30kpt^2, or overlap > 0.8). Loosen any threshold only
# after re-validating against the calibration corpus (see
# notes/preview-tool-abstract-images-vector-images-plan.md §7.1).
_DIAGRAM_MIN_AREA_PT2 = 30_000.0
_DIAGRAM_DENSE_MIN_DENSITY = 0.0010
_DIAGRAM_DENSE_MIN_ITEMS = 100
_DIAGRAM_DENSE_MAX_OVERLAP = 0.70
_DIAGRAM_SPARSE_MIN_ITEMS = 50
_DIAGRAM_SPARSE_MAX_OVERLAP = 0.50

# Per-constituent thresholds for the post-clustering merge pass
# (:func:`_merge_close_clusters`). Both clusters being merged must
# individually carry at least this many items and area; otherwise the
# pair is skipped. Calibrated against P3127R1 Fig 1's two sibling
# sub-diagrams (Fig 1a: 58 items / 78kpt^2; Fig 1b: 100 items /
# 39kpt^2 - both well above the gate) vs. P4003R1's code-block
# decoration fragments (typically 20-40 items / <10kpt^2 each).
_MERGE_MIN_ITEMS = 30
_MERGE_MIN_AREA_PT2 = 20_000.0

# Minimum number of drawing items inside a surviving cluster. Single
# items are almost always rules or one-stroke decorations. Real
# diagrams compose dozens to hundreds of items.
_MIN_CLUSTER_ITEM_COUNT = 8

# Rasterisation DPI. 150 produces ~2x the on-page pixel density which
# renders crisply in the preview iframe without exploding the
# data-URL size for a 4-inch figure.
_RASTERISE_DPI = 150

# Header/footer exclusion band, expressed as a fraction of page height
# from each edge. Drawings whose bbox sits ENTIRELY within these bands
# are dropped before clustering (running-header underlines, page-
# number boxes). Drawings that straddle the band boundary are kept -
# real content adjacent to the running header would otherwise lose its
# top-edge strokes and shrink the cluster bbox unhelpfully.
_PAGE_EDGE_BAND_FRACTION = 0.08

# Cap on the number of *vector* candidate clusters considered per
# page after the per-cluster filter survives. Defends against
# pathological pages with hundreds of plausible-figure clusters; in
# practice this is extremely rare in the corpus (max observed ~40),
# but the explicit accounting ensures dropped clusters surface in the
# uncertainty marker rather than disappearing silently.
_MAX_CLUSTERS_PER_PAGE = 200

# Maximum cluster bbox area as a fraction of the page area. Clusters
# whose bbox covers more than this much of the page are almost
# always single-linkage chaining noise: a page-frame stroke or
# margin marker pulled an unrelated drawing into the cluster and
# the bbox now spans the page. The text-overlap filter cannot catch
# this case alone because code-heavy WG21 pages commonly have
# 17-30% text coverage, so a page-spanning cluster's overlap
# fraction stays under _MAX_TEXT_OVERLAP_FRACTION (0.35) even
# though it visually swallows everything. Real figures, even the
# largest in the calibration corpus (P4003R1 page 35 at 288x400pt),
# stay under 25% of page area; the 50% threshold leaves ample
# headroom while catching the failure cleanly.
_MAX_CLUSTER_AREA_FRACTION = 0.50

# ---- Container detection (virtual-cluster merge) ---------------------------
#
# A "frame" drawing is a thin outer container (typically 2 horizontal
# lines or 1 wide-thin rectangle) that visually encloses a row of
# smaller diagram elements - the canonical horizontal-flow-diagram
# shape "A -> B -> C". Single-linkage clustering at the bbox level
# leaves such frames as their own 2-item cluster and treats the
# enclosed boxes as separate clusters, so size and item-count
# filters individually reject each piece. Container detection
# explicitly merges a frame with the clusters it encloses into a
# single "virtual cluster" that carries the combined item count and
# union bbox; virtual clusters get relaxed thresholds in the
# downstream filters.

# Maximum item count for a drawing to qualify as a frame. Real frames
# are 1 ('re' rectangle) or 2 ('l' top + 'l' bottom); a few corner
# decorations push it to 4. Anything richer is a figure body, not a
# container.
_MAX_FRAME_ITEMS = 4

# A frame's bbox area must stay under this fraction of the page area.
# Caps page frames, full-page background fills, and margin markers
# from triggering container detection.
_MAX_FRAME_AREA_FRACTION = 0.30

# A frame must extend at least this far in its longest dimension.
# Excludes tiny outline boxes (e.g. checkbox icons) that aren't
# structural containers.
_MIN_FRAME_DIM_PT = 80.0

# A frame must have at least this aspect ratio (long-side / short-
# side). Both thin horizontal-strip containers (like P4003R1 page
# 8's IoAwaitable, aspect 10.81) and normal-aspect rectangular
# containers (P4003R1 page 13's coroutine flow diagram, aspect 1.88)
# qualify; only near-perfect squares (aspect < 1.5) are excluded.
# The :data:`_MIN_ENCLOSED_CLUSTERS` requirement below guards against
# a square-ish background-fill rectangle that happens to enclose
# one unrelated cluster from accidentally triggering a merge.
_MIN_FRAME_ASPECT_RATIO = 1.5

# A frame must enclose at least this many smaller clusters before
# its merge fires. Two is the natural floor: container detection
# exists to consolidate a frame with multiple inner parts (the
# IoAwaitable diagram's three labelled boxes, the page 13 coroutine
# flow's three timeline columns). A frame with a single inner
# cluster doesn't need consolidation and merging it would risk
# absorbing unrelated content (a background rect that happens to
# overlap one adjacent figure).
_MIN_ENCLOSED_CLUSTERS = 2

# For a cluster to be considered "inside" a frame, its bbox must
# overlap the frame's bbox by at least this fraction (intersection
# over cluster area). 0.80 catches the common case of an inner box
# fully nested plus a small tolerance for sub-pt extraction drift.
_MIN_NESTING_FRACTION = 0.80

# Relaxed min-dim floor for virtual clusters. The container's
# intentional thinness (e.g. 35pt tall for a labels-in-one-row flow
# diagram) would otherwise fail the strict 60pt floor.
_VIRTUAL_MIN_CLUSTER_DIM_PT = 30.0

# Virtual clusters with at least this many drawing items bypass the
# aspect-extreme filter. Dense content inside a thin frame is the
# characteristic structure of a populated flow diagram (P4003R1's
# IoAwaitable has 301 items); thin frames containing little (<= 50
# items) are usually decoration that survived clustering for other
# reasons and should stay rejected.
_VIRTUAL_MIN_ITEM_COUNT = 50

# Maximum aspect ratio (long-side / short-side) for a cluster bbox.
# Clusters with extreme aspect ratios are almost always code-block
# background fills or shaded callout strips that span the page
# width as a thin horizontal band. The calibration corpus's
# tightest legitimate figure is 3.0:1 (a narrow flowchart column);
# 3.5 leaves margin while catching strip noise that runs 3.9:1 to
# 8.0:1 in the wild.
_MAX_CLUSTER_ASPECT_RATIO = 3.5

# Hard upper bound on summed drawing-item count BEFORE clustering.
# Single-linkage clustering on N items is O(N^2) naively; even with
# the spatial-hash bucketed implementation below, pathological pages
# (slide-deck rasters, scanned page rules) can drive the constant
# factor high. When a page exceeds this, vector extraction bails out
# for that page entirely and the page contributes to ``pages_skipped``
# in the uncertainty marker. Corpus maximum is P3926R0 page 13 with
# ~5500 items; the limit sits comfortably above so legitimate
# diagrams are not clipped, but bounded so a 100,000-item page does
# not hang the run.
_MAX_DRAWINGS_PER_PAGE = 10_000


# ---- Synthetic-xref space (see :func:`_synthetic_xref`) ---------------------

# Cluster identity for finalize_extraction's dedup pass. Vector
# figures have no resource-dictionary xref. We assign a negative
# synthetic xref derived from a stable hash of the cluster's
# (page, rounded bbox) tuple - opaque, collision-resistant, and
# large-page-safe.
#
# Negative-valued masking ensures vector xrefs never collide with
# real pymupdf xrefs (>= 1) or HTML's sentinel (== 0). The mask sets
# the high bit; the low 31 bits carry the truncated hash. An earlier
# draft used a packed integer formula but that aliased on tabloid /
# A0 pages whose coordinates exceed 10_000.
_VECTOR_XREF_MASK = -(1 << 31)
_VECTOR_XREF_RANGE = (1 << 31) - 1


# ---- Rejection-reason key set (D7-ordered when formatted) -------------------

REASON_ASPECT_EXTREME = "aspect_extreme"
REASON_BBOX_TOO_LARGE = "bbox_too_large"
REASON_CLUSTERS_OVERFLOW = "clusters_overflow"
REASON_EDGE_BAND = "edge_band"
REASON_TEXT_OVERLAP = "text_overlap"
REASON_TOO_FEW_ITEMS = "too_few_items"
REASON_TOO_SMALL = "too_small"
REASON_WORDING_COLOR = "wording_color"

# The closed set of valid reason keys. Adding a new key is a versioned
# change to the marker contract (see plan section 1.6a "Marker
# stability"). The tuple is the source of truth for tests that pin
# the contract.
ALLOWED_REASON_KEYS: tuple[str, ...] = (
    REASON_ASPECT_EXTREME,
    REASON_BBOX_TOO_LARGE,
    REASON_CLUSTERS_OVERFLOW,
    REASON_EDGE_BAND,
    REASON_TEXT_OVERLAP,
    REASON_TOO_FEW_ITEMS,
    REASON_TOO_SMALL,
    REASON_WORDING_COLOR,
)


# ---- Marker template --------------------------------------------------------

VECTOR_UNCERTAINTY_MARKER_TEMPLATE = (
    "<!-- tomd:vector-extraction-uncertain: pages_scanned={pages_scanned} "
    "candidates={candidates} kept={kept} rejected={rejected} "
    "reasons={{{reasons}}} pages_skipped={pages_skipped}. "
    "Vector extraction is heuristic; missed diagrams and false positives "
    "are both possible. See vector_images.py constants for tuning surface. -->"
)


# ---- Per-page stats type alias ---------------------------------------------

# Per-page stats and run-level accumulators have identical shape and
# identical combine semantics. The alias preserves the plan's
# vocabulary while keeping the dataclass code in one place
# (tomd.lib.pdf.images).
_PageVectorStats = _VectorExtractionStats


# ---- Synthetic-xref --------------------------------------------------------


def _synthetic_xref(
    page_num: int,
    bbox: tuple[float, float, float, float],
) -> int:
    """Stable hash-derived negative xref for a vector cluster.

    1-pt rounding deliberately conflates "the same diagram drawn twice
    on the same page within a pt" (extremely rare; if it happens we
    want one IMAGE, not two). bbox dimensions (width, height) are part
    of the key so two clusters at the same (page, y0, x0) but radically
    different sizes - a zoomed inset vs its source - dedupe as
    distinct.

    The Python ``hash`` builtin's per-run randomness (PYTHONHASHSEED)
    is acceptable here: the xref is opaque to everything outside
    finalize_extraction's dedup loop and never surfaces in golden
    fixtures, on-disk artefacts, or downstream consumers. Within a
    run, the same cluster produces the same xref every time.
    """
    key = (
        page_num,
        int(round(bbox[0])),
        int(round(bbox[1])),
        int(round(bbox[2] - bbox[0])),
        int(round(bbox[3] - bbox[1])),
    )
    return _VECTOR_XREF_MASK | (hash(key) & _VECTOR_XREF_RANGE)


# ---- Geometry helpers -------------------------------------------------------


def _drawing_bbox(drawing: dict) -> tuple[float, float, float, float] | None:
    """Return (x0, y0, x1, y1) for a drawing dict, or None if unbounded."""
    rect = drawing.get("rect")
    if rect is None:
        return None
    return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))


def _bbox_union(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _rect_distance(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Minimum euclidean distance between two axis-aligned rects.

    Zero if the rects overlap. Used as the single-linkage merge metric.
    """
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def _intersects(
    bbox: tuple[float, float, float, float],
    rect: "pymupdf.Rect",
) -> bool:
    """True if axis-aligned bbox (x0, y0, x1, y1) overlaps ``rect``."""
    return not (bbox[2] < rect.x0 or bbox[0] > rect.x1
                or bbox[3] < rect.y0 or bbox[1] > rect.y1)


def _pdf_pt_to_pixel_irect(
    bbox: tuple[float, float, float, float],
    dpi: int,
) -> "pymupdf.IRect":
    """Convert a PDF-pt bbox to a page-pixel IRect.

    Pixmaps produced by ``page.get_pixmap(clip=...)`` carry their
    ``irect`` in *page-pixel* coordinates (not clip-relative), so
    :meth:`Pixmap.set_rect` expects an IRect in that same space.
    Multiplying PDF-pt coordinates by ``dpi/72`` yields exactly that
    space; no clip-origin shift is needed (or correct).
    """
    sx = dpi / 72.0
    x0 = int(round(bbox[0] * sx))
    y0 = int(round(bbox[1] * sx))
    x1 = int(round(bbox[2] * sx))
    y1 = int(round(bbox[3] * sx))
    return pymupdf.IRect(x0, y0, x1, y1)


def _bbox_fully_in_edge_band(
    bbox: tuple[float, float, float, float],
    page_rect: "pymupdf.Rect",
) -> bool:
    """True if ``bbox`` sits entirely in the top or bottom edge band.

    Drawings whose bbox straddles a band boundary survive; only those
    wholly inside the band are dropped. This preserves content
    adjacent to the running header (figure top edges, etc.) while
    still rejecting running-header underlines and page-number boxes.
    """
    band_h = page_rect.height * _PAGE_EDGE_BAND_FRACTION
    top_band_bottom = page_rect.y0 + band_h
    bottom_band_top = page_rect.y1 - band_h
    if bbox[3] <= top_band_bottom:
        return True
    if bbox[1] >= bottom_band_top:
        return True
    return False


def _bbox_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    *,
    threshold: float = _MIN_NESTING_FRACTION,
) -> bool:
    """True if ``inner`` overlaps ``outer`` by at least ``threshold`` of its area.

    Used to decide whether a candidate cluster is "inside" a frame
    drawing for container detection. A fully nested inner cluster
    has intersection = inner_area, fraction = 1.0; the threshold is
    a tolerance for sub-pt drift and clusters whose bboxes nudge
    over a frame edge.
    """
    ix0 = max(outer[0], inner[0])
    iy0 = max(outer[1], inner[1])
    ix1 = min(outer[2], inner[2])
    iy1 = min(outer[3], inner[3])
    if ix0 >= ix1 or iy0 >= iy1:
        return False
    inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if inner_area <= 0:
        return False
    intersection = (ix1 - ix0) * (iy1 - iy0)
    return intersection / inner_area >= threshold


def _detect_frame_drawings(
    drawings: Sequence[dict],
    page_area: float,
) -> list[tuple[tuple[float, float, float, float], int]]:
    """Identify "container frame" drawings: thin outlines that enclose
    figure content.

    Returns a list of ``(frame_bbox, item_count)`` tuples for drawings
    matching all of:

    - item count <= :data:`_MAX_FRAME_ITEMS`,
    - area < :data:`_MAX_FRAME_AREA_FRACTION` of the page area,
    - longest dimension >= :data:`_MIN_FRAME_DIM_PT`,
    - aspect ratio >= :data:`_MIN_FRAME_ASPECT_RATIO` (excludes
      square-ish background fills).

    A diagram drawn as "outer thin rectangle + inner labelled boxes"
    leaves its outer rectangle as a 2-item drawing with extreme
    aspect; centroid-based clustering can't merge it with the inner
    elements because the centroids land in distant buckets. Detecting
    these frames explicitly and merging their enclosed clusters in a
    post-clustering pass (see :func:`_merge_clusters_into_frames`)
    recovers the figure as one unit while leaving unrelated nearby
    drawings untouched.
    """
    if page_area <= 0:
        return []
    frames: list[tuple[tuple[float, float, float, float], int]] = []
    for d in drawings:
        items = d.get("items") or ()
        if len(items) > _MAX_FRAME_ITEMS:
            continue
        bbox = _drawing_bbox(d)
        if bbox is None:
            continue
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= 0 or height <= 0:
            continue
        if width * height >= _MAX_FRAME_AREA_FRACTION * page_area:
            continue
        if max(width, height) < _MIN_FRAME_DIM_PT:
            continue
        aspect = max(width / height, height / width)
        if aspect < _MIN_FRAME_ASPECT_RATIO:
            continue
        frames.append((bbox, len(items)))
    return frames


def _merge_clusters_into_frames(
    frames: Sequence[tuple[tuple[float, float, float, float], int]],
    clusters: Sequence[tuple[tuple[float, float, float, float], int]],
) -> list[tuple[tuple[float, float, float, float], int, bool]]:
    """Merge clusters enclosed by frame drawings into "virtual" clusters.

    Returns a list of ``(bbox, item_count, is_virtual)`` tuples.
    Virtual clusters (``is_virtual=True``) carry the union bbox of the
    frame plus all its enclosed clusters and the summed item count.
    Regular (non-virtual) clusters pass through unchanged.

    A frame with no enclosed clusters produces nothing - the frame
    drawing itself was already its own cluster and stays in the
    regular set.
    """
    cluster_list = list(clusters)
    used = set()
    virtual: list[tuple[tuple[float, float, float, float], int, bool]] = []
    # Threshold: a cluster qualifies as "enclosed" only if it is
    # strictly smaller than the frame. Skips the frame's OWN cluster
    # (the frame drawing always forms a cluster whose bbox equals the
    # frame's, which would otherwise self-match and double-count
    # items), while still admitting nearly-as-large inner content
    # with a small allowance for sub-pt drift.
    _ENCLOSED_AREA_CEILING = 0.95
    for frame_bbox, frame_items in frames:
        frame_area = ((frame_bbox[2] - frame_bbox[0])
                      * (frame_bbox[3] - frame_bbox[1]))
        enclosed_idx: list[int] = []
        for i, (c_bbox, _c_items) in enumerate(cluster_list):
            if i in used:
                continue
            c_area = (c_bbox[2] - c_bbox[0]) * (c_bbox[3] - c_bbox[1])
            if c_area >= _ENCLOSED_AREA_CEILING * frame_area:
                # Same-size cluster - that's the frame drawing's own
                # cluster. Skip; we don't want to merge a frame "into
                # itself".
                continue
            if _bbox_contains(frame_bbox, c_bbox):
                enclosed_idx.append(i)
        if len(enclosed_idx) < _MIN_ENCLOSED_CLUSTERS:
            # Lone-frame guard: a frame that doesn't enclose multiple
            # parts isn't a structural container - either it's a
            # decorative box around a single cluster (no consolidation
            # needed) or it's a background fill that accidentally
            # overlaps something unrelated.
            continue
        v_bbox = frame_bbox
        v_items = frame_items
        for i in enclosed_idx:
            c_bbox, c_items = cluster_list[i]
            v_bbox = _bbox_union(v_bbox, c_bbox)
            v_items += c_items
            used.add(i)
        virtual.append((v_bbox, v_items, True))

    out: list[tuple[tuple[float, float, float, float], int, bool]] = []
    for i, (c_bbox, c_items) in enumerate(cluster_list):
        if i not in used:
            out.append((c_bbox, c_items, False))
    out.extend(virtual)
    return out


def _text_overlap_fraction(
    cluster_bbox: tuple[float, float, float, float],
    page_blocks: Sequence["Block"],
) -> float:
    """Fraction of ``cluster_bbox`` area covered by any text block.

    Denominator is the cluster's own area, not the text block's. A
    tiny cluster fully inside a huge text block reports 1.0 (cluster
    fully covered) - the test pins this in case a future maintainer
    accidentally normalises by the wrong area.

    Result is clamped to 1.0; overlapping text blocks could otherwise
    double-count their intersections with the cluster.
    """
    cluster_area = (cluster_bbox[2] - cluster_bbox[0]) * (cluster_bbox[3] - cluster_bbox[1])
    if cluster_area <= 0:
        return 0.0
    total_overlap = 0.0
    for block in page_blocks:
        bb = block.bbox
        ix0 = max(cluster_bbox[0], bb[0])
        iy0 = max(cluster_bbox[1], bb[1])
        ix1 = min(cluster_bbox[2], bb[2])
        iy1 = min(cluster_bbox[3], bb[3])
        if ix0 >= ix1 or iy0 >= iy1:
            continue
        total_overlap += (ix1 - ix0) * (iy1 - iy0)
    return min(total_overlap / cluster_area, 1.0)


# ---- Pre-clustering ins/del-coloured drop ---------------------------------


def _colour_in_wording_band(colour) -> bool:
    """Tolerant accessor over pymupdf's ``color`` / ``fill`` value shapes.

    ``pymupdf.Page.get_drawings()`` returns drawing dicts whose
    ``color`` / ``fill`` keys can be:

      - ``None`` (no stroke or fill on this drawing),
      - an ``(r, g, b)`` float tuple in [0, 1],
      - an ``(r, g, b, a)`` 4-tuple, or
      - a ``(k,)`` grayscale 1-tuple

    depending on the pymupdf version and the source PDF's colour
    space. False on any non-conforming value rather than raising,
    because pymupdf drops surprising shapes through this API.
    """
    if not colour:
        return False
    if len(colour) == 1:
        k = colour[0]
        r, g, b = k, k, k
    elif len(colour) >= 3:
        r, g, b = colour[0], colour[1], colour[2]
    else:
        return False
    return is_wording_rgb(r, g, b)


def _is_ins_del_coloured(drawing: dict) -> bool:
    """Drawing's stroke or fill colour is in the ins (green) or del (red) hue band.

    Not a proxy for :mod:`tomd.lib.pdf.wording`'s full multi-signal
    classification (block-level contamination, line-level majority,
    strikethrough-overlap, document-wide >=5-ins gate). A standalone
    drop heuristic catching the common case where a paper's ins/del
    decorations would otherwise be aggregated as a "figure" by the
    cluster filter.

    We deliberately do NOT walk ``drawing["items"]`` looking for
    per-item colours. An earlier draft did so to "catch matplotlib /
    TikZ per-path colouring," but those producers emit separate
    drawing dicts per stroke and set color / fill on each outer dict;
    per-item colours don't surface through this API. Probing item
    tuples would mis-interpret path-coordinate tuples (Point, Rect)
    as colours and silently exclude real diagrams whose coordinates
    happen to land in a wording hue band.
    """
    if _colour_in_wording_band(drawing.get("color")):
        return True
    if _colour_in_wording_band(drawing.get("fill")):
        return True
    return False


# ---- Clustering (union-find over spatial-hash buckets) ---------------------


def _cluster_drawings(
    drawings: Sequence[dict],
) -> list[tuple[tuple[float, float, float, float], int]]:
    """Single-linkage cluster ``drawings`` by bbox proximity.

    Returns a list of ``(cluster_bbox, item_count)`` tuples. Item
    count is the sum of ``len(drawing["items"])`` across the cluster's
    member drawings; cluster_bbox is the union of member bboxes.

    Spatial-hash bucketed for amortised O(N) total work: each drawing
    is bucketed by its centroid into a ``_CLUSTER_LINK_DISTANCE_PT``-
    sized grid; merge candidates are restricted to the centroid bucket
    plus its 8 neighbours. Drawings whose ``rect`` is None are
    skipped.

    Behaviour (cluster membership) is asserted by tests, not
    implementation choices. A future O(N log N) implementation can
    swap in unchanged.
    """
    bboxes: list[tuple[float, float, float, float]] = []
    item_counts: list[int] = []
    for d in drawings:
        bbox = _drawing_bbox(d)
        if bbox is None:
            continue
        items = d.get("items") or ()
        bboxes.append(bbox)
        item_counts.append(len(items))

    n = len(bboxes)
    if n == 0:
        return []

    bucket: dict[tuple[int, int], list[int]] = {}
    for i, bbox in enumerate(bboxes):
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        key = (
            int(cx // _CLUSTER_LINK_DISTANCE_PT),
            int(cy // _CLUSTER_LINK_DISTANCE_PT),
        )
        bucket.setdefault(key, []).append(i)

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, bbox in enumerate(bboxes):
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        bx = int(cx // _CLUSTER_LINK_DISTANCE_PT)
        by = int(cy // _CLUSTER_LINK_DISTANCE_PT)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbours = bucket.get((bx + dx, by + dy))
                if not neighbours:
                    continue
                for j in neighbours:
                    if j <= i:
                        continue
                    if _rect_distance(bbox, bboxes[j]) <= _CLUSTER_LINK_DISTANCE_PT:
                        union(i, j)

    grouped: dict[int, list[int]] = {}
    for i in range(n):
        grouped.setdefault(find(i), []).append(i)

    clusters: list[tuple[tuple[float, float, float, float], int]] = []
    for members in grouped.values():
        cluster_bbox = bboxes[members[0]]
        total_items = 0
        for m in members:
            cluster_bbox = _bbox_union(cluster_bbox, bboxes[m])
            total_items += item_counts[m]
        clusters.append((cluster_bbox, total_items))

    return clusters


def _merge_close_clusters(
    clusters: list[tuple[tuple[float, float, float, float], int]],
    *,
    max_merged_area: float,
) -> list[tuple[tuple[float, float, float, float], int]]:
    """Merge cluster pairs whose bboxes lie within
    ``_CLUSTER_LINK_DISTANCE_PT`` of each other AND where both
    constituents are individually substantial.

    The centroid-bucket optimisation in :func:`_cluster_drawings` can
    miss a valid merge when two clusters have large bboxes whose
    centroids land more than one bucket apart while their nearest
    edges are within ``_CLUSTER_LINK_DISTANCE_PT``. Canonical case:
    P3127R1 page 6, where Fig 1a (y=-39..155, centroid y~58) and Fig
    1b (y=185..284, centroid y~234) sit 29.5pt apart but their
    centroid buckets are 6 rows apart, so they're never compared
    during the bucket pass.

    The substantiality gate (``items >= _MERGE_MIN_ITEMS`` AND
    ``area >= _MERGE_MIN_AREA_PT2`` for each constituent) prevents
    single-linkage chaining on code-block-heavy pages, where dozens
    of small decoration / table-cell clusters would otherwise chain
    into one page-spanning false-positive cluster (P4003R1 pages 36,
    44, 52, 56 each have 5-20 such fragments stacked vertically
    along the body column). Real sibling figures (P3127R1 Fig 1's
    two sub-diagrams) sit comfortably above the gate; chrome-sized
    fragments sit below.

    ``max_merged_area`` caps the resulting union area; merges that
    would produce a bbox covering most of the page are silently
    skipped so the per-cluster ``_MAX_CLUSTER_AREA_FRACTION`` check
    downstream doesn't fire on a false-positive page-spanning cluster.

    O(k^2) over the typically <50 surviving clusters per page.
    """
    if len(clusters) < 2:
        return clusters
    work = list(clusters)
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(work):
            j = i + 1
            bb_i, ic_i = work[i]
            area_i = (bb_i[2] - bb_i[0]) * (bb_i[3] - bb_i[1])
            i_substantial = ic_i >= _MERGE_MIN_ITEMS and area_i >= _MERGE_MIN_AREA_PT2
            while j < len(work):
                bb_j, ic_j = work[j]
                area_j = (bb_j[2] - bb_j[0]) * (bb_j[3] - bb_j[1])
                j_substantial = ic_j >= _MERGE_MIN_ITEMS and area_j >= _MERGE_MIN_AREA_PT2
                if (i_substantial and j_substantial
                        and _rect_distance(bb_i, bb_j) <= _CLUSTER_LINK_DISTANCE_PT):
                    new_bbox = _bbox_union(bb_i, bb_j)
                    new_area = (new_bbox[2] - new_bbox[0]) * (new_bbox[3] - new_bbox[1])
                    if new_area <= max_merged_area:
                        work[i] = (new_bbox, ic_i + ic_j)
                        work.pop(j)
                        bb_i, ic_i = work[i]
                        area_i = new_area
                        changed = True
                        continue
                j += 1
            i += 1
    return work


# ---- Rasterisation + whiteout helpers --------------------------------------


def _whiteout_text_in_pixmap(
    pix: "pymupdf.Pixmap",
    clip_rect: "pymupdf.Rect",
    page_blocks: Sequence["Block"],
    dpi: int,
) -> None:
    """Paint white rectangles over text-line regions in ``pix``, in place.

    Called only when the caller sets ``whiteout_text=True``. Otherwise
    the extractor calls ``pix.tobytes("png")`` directly.

    Granularity is line-level (``Line.bbox``), not block-level. Block
    bboxes are the union of all lines and are often substantially
    larger than the inked region; painting a block bbox can erase
    chart axes. Line bboxes match glyph extents closely.
    """
    pix_irect = pix.irect
    for block in page_blocks:
        if not _intersects(block.bbox, clip_rect):
            continue
        for line in block.lines:
            if not _intersects(line.bbox, clip_rect):
                continue
            irect = _pdf_pt_to_pixel_irect(line.bbox, dpi) & pix_irect
            if irect.is_empty:
                continue
            pix.set_rect(irect, (255, 255, 255))


# ---- Uncertainty-marker formatter ------------------------------------------


def format_uncertainty_marker(stats: VectorUncertaintyStats) -> str:
    """Format the per-paper uncertainty marker.

    Reasons dict is iterated in alphabetical key order (D7). Foreign
    keys (outside :data:`ALLOWED_REASON_KEYS`) raise ``ValueError`` so
    a maintainer who adds a new filter without updating the closed
    key set fails loudly at test time rather than emitting a marker
    consumers can't parse.
    """
    for key in stats.reasons:
        if key not in ALLOWED_REASON_KEYS:
            raise ValueError(
                f"unknown vector-extraction reason key: {key!r}; "
                f"allowed: {ALLOWED_REASON_KEYS}"
            )
    reasons_str = ", ".join(
        f"{k}:{stats.reasons[k]}" for k in sorted(stats.reasons.keys())
    )
    return VECTOR_UNCERTAINTY_MARKER_TEMPLATE.format(
        pages_scanned=stats.pages_scanned,
        candidates=stats.candidates,
        kept=stats.kept,
        rejected=stats.rejected,
        reasons=reasons_str,
        pages_skipped=stats.pages_skipped,
    )


def should_emit_marker(stats: VectorUncertaintyStats) -> bool:
    """True when there's something honest to disclose.

    Marker is emitted when extraction was attempted (pages_scanned > 0
    OR pages_skipped > 0) AND something was rejected or skipped
    (rejected > 0 OR pages_skipped > 0). On HTML papers, where vector
    extraction never runs, the marker is absent by construction
    (stats is None upstream); this predicate handles only the
    PDF-stats-present case.
    """
    if stats.pages_scanned == 0 and stats.pages_skipped == 0:
        return False
    return stats.rejected > 0 or stats.pages_skipped > 0


# ---- Per-page driver -------------------------------------------------------


def extract_page_vector_images(
    page: "pymupdf.Page",
    page_blocks: Sequence["Block"],
    *,
    whiteout_text: bool = False,
) -> tuple[list[_PageImageCandidate], _PageVectorStats]:
    """Per-page vector-figure extraction.

    Returns ``(candidates, per_page_stats)``. Stats bubble up via
    tuple return rather than a mutable accumulator parameter so the
    function stays library-pure (no shared mutable state across
    callers). The pipeline's per-page loop sums the per-page stats
    into the run-level :class:`_VectorExtractionStats` accumulator
    that :func:`tomd.lib.pdf.images.finalize_extraction` eventually
    surfaces via :class:`VectorUncertaintyStats`.

    Called from step 1 of the pipeline, alongside
    :func:`tomd.lib.pdf.images.extract_page_images` (the raster path).
    The document must still be open because the rasterisation call
    needs the live page.

    Pipeline (matches plan section 1.1):

      1. Collect drawings via ``page.get_drawings()``; early-exit on
         ``_MIN_PAGE_DRAWING_ITEMS``.
      2. Hard cap on per-page item count via ``_MAX_DRAWINGS_PER_PAGE``;
         the page is skipped entirely if exceeded, contributing to
         ``pages_skipped``.
      3. Drop drawings whose stroke / fill colour is in the wording
         hue band (counted as ``wording_color``).
      4. Drop drawings whose bbox sits entirely inside the header /
         footer edge band (counted as ``edge_band``).
      5. Cluster surviving drawings by single-linkage on bbox
         distance (``_CLUSTER_LINK_DISTANCE_PT``).
      6. Per-cluster filter: ``_MIN_CLUSTER_DIM_PT`` on both axes,
         ``_MIN_CLUSTER_ITEM_COUNT``, ``_MAX_TEXT_OVERLAP_FRACTION``,
         and the page-cluster cap ``_MAX_CLUSTERS_PER_PAGE``.
      7. Rasterise each surviving cluster via ``page.get_pixmap``,
         optionally painting over text lines if ``whiteout_text``.
      8. Run :func:`tomd.lib.pdf.images._caption_for` against the
         cluster bbox.
      9. Return one :class:`_PageImageCandidate` per cluster with
         ``source="vector"`` and a synthetic negative ``xref``.
    """
    page_num = page.number + 1
    candidates: list[_PageImageCandidate] = []
    reasons: dict[str, int] = {}

    try:
        drawings = page.get_drawings()
    except Exception:
        _log.warning("page %d: get_drawings failed", page_num, exc_info=True)
        return [], _PageVectorStats()

    total_items = sum(len(d.get("items") or ()) for d in drawings)

    if total_items < _MIN_PAGE_DRAWING_ITEMS:
        # Below the noise floor - silently skip (not even "scanned").
        return [], _PageVectorStats()

    if total_items >= _MAX_DRAWINGS_PER_PAGE:
        # Page too busy; bail out and surface in pages_skipped.
        return [], _PageVectorStats(pages_skipped=1)

    # Pre-clustering wording-colour drop.
    survivors: list[dict] = []
    for d in drawings:
        if _is_ins_del_coloured(d):
            reasons[REASON_WORDING_COLOR] = reasons.get(REASON_WORDING_COLOR, 0) + 1
            continue
        survivors.append(d)

    # Pre-clustering edge-band drop (per-drawing, not per-cluster, so a
    # cluster that straddles the band still forms from its body-side
    # members).
    page_rect = page.rect
    after_edge: list[dict] = []
    for d in survivors:
        bbox = _drawing_bbox(d)
        if bbox is not None and _bbox_fully_in_edge_band(bbox, page_rect):
            reasons[REASON_EDGE_BAND] = reasons.get(REASON_EDGE_BAND, 0) + 1
            continue
        after_edge.append(d)

    page_area = page_rect.width * page_rect.height
    clusters = _cluster_drawings(after_edge)
    # Repair the centroid-bucket miss for tall-bbox neighbours (see
    # _merge_close_clusters docstring for the canonical P3127R1 page-6
    # case). The max-area cap prevents single-linkage chaining on
    # code-block-heavy pages.
    clusters = _merge_close_clusters(
        clusters, max_merged_area=_MAX_CLUSTER_AREA_FRACTION * page_area,
    )

    # Container detection: identify thin frame drawings that enclose
    # smaller clusters (canonical horizontal-flow-diagram shape) and
    # merge their enclosed clusters into "virtual" clusters with the
    # union bbox and summed item count. Virtual clusters get relaxed
    # thresholds in the filter loop below.
    frames = _detect_frame_drawings(after_edge, page_area)
    annotated = _merge_clusters_into_frames(frames, clusters)
    candidates_count = len(annotated)

    # Per-cluster shape + text-overlap filter.
    surviving_clusters: list[tuple[tuple[float, float, float, float], int]] = []
    for cluster_bbox, item_count, is_virtual in annotated:
        width = cluster_bbox[2] - cluster_bbox[0]
        height = cluster_bbox[3] - cluster_bbox[1]
        # Min-dim floor: virtual clusters get the lower
        # _VIRTUAL_MIN_CLUSTER_DIM_PT so an intentionally thin
        # container (e.g. a labels-in-one-row flow diagram) survives.
        min_dim = _VIRTUAL_MIN_CLUSTER_DIM_PT if is_virtual else _MIN_CLUSTER_DIM_PT
        if width < min_dim or height < min_dim:
            reasons[REASON_TOO_SMALL] = reasons.get(REASON_TOO_SMALL, 0) + 1
            continue
        if page_area > 0 and width * height >= _MAX_CLUSTER_AREA_FRACTION * page_area:
            # Single-linkage chaining symptom: a page-frame stroke or
            # margin marker pulled the cluster bbox out to span the
            # page. text_overlap can't catch this on whitespace-heavy
            # pages where coverage stays under 35% even at full-page
            # extent.
            reasons[REASON_BBOX_TOO_LARGE] = reasons.get(REASON_BBOX_TOO_LARGE, 0) + 1
            continue
        if max(width / height, height / width) >= _MAX_CLUSTER_ASPECT_RATIO:
            # Strip-shaped cluster - code-block background fill or
            # shaded callout spanning the page width. Real figures
            # stay under 3.5:1 in the calibration corpus.
            # Virtual clusters with dense content (>= _VIRTUAL_MIN_ITEM_COUNT
            # items) bypass: they represent a populated flow diagram
            # whose container is intentionally extreme-aspect.
            if not (is_virtual and item_count >= _VIRTUAL_MIN_ITEM_COUNT):
                reasons[REASON_ASPECT_EXTREME] = reasons.get(REASON_ASPECT_EXTREME, 0) + 1
                continue
        if item_count < _MIN_CLUSTER_ITEM_COUNT:
            reasons[REASON_TOO_FEW_ITEMS] = reasons.get(REASON_TOO_FEW_ITEMS, 0) + 1
            continue
        cluster_area = width * height
        overlap = _text_overlap_fraction(cluster_bbox, page_blocks)
        is_diagram = cluster_area >= _DIAGRAM_MIN_AREA_PT2 and (
            (
                item_count >= _DIAGRAM_DENSE_MIN_ITEMS
                and item_count / cluster_area >= _DIAGRAM_DENSE_MIN_DENSITY
                and overlap < _DIAGRAM_DENSE_MAX_OVERLAP
            )
            or (
                item_count >= _DIAGRAM_SPARSE_MIN_ITEMS
                and overlap < _DIAGRAM_SPARSE_MAX_OVERLAP
            )
        )
        if not is_diagram and overlap >= _MAX_TEXT_OVERLAP_FRACTION:
            reasons[REASON_TEXT_OVERLAP] = reasons.get(REASON_TEXT_OVERLAP, 0) + 1
            continue
        surviving_clusters.append((cluster_bbox, item_count))

    # Page-cluster cap: keep top-of-page survivors, drop the rest.
    surviving_clusters.sort(key=lambda c: (c[0][1], c[0][0]))
    if len(surviving_clusters) > _MAX_CLUSTERS_PER_PAGE:
        overflow = len(surviving_clusters) - _MAX_CLUSTERS_PER_PAGE
        reasons[REASON_CLUSTERS_OVERFLOW] = reasons.get(REASON_CLUSTERS_OVERFLOW, 0) + overflow
        surviving_clusters = surviving_clusters[:_MAX_CLUSTERS_PER_PAGE]

    # Rasterise + caption each surviving cluster.
    for cluster_bbox, _item_count in surviving_clusters:
        clip = pymupdf.Rect(*cluster_bbox) & page.rect
        if clip.is_empty:
            continue
        try:
            pix = page.get_pixmap(
                clip=clip,
                dpi=_RASTERISE_DPI,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
        except Exception:
            _log.warning("page %d: get_pixmap failed for cluster %s",
                         page_num, cluster_bbox, exc_info=True)
            continue
        if whiteout_text:
            _whiteout_text_in_pixmap(pix, clip, page_blocks, _RASTERISE_DPI)
        try:
            png_bytes = pix.tobytes("png")
        except Exception:
            _log.warning("page %d: pix.tobytes failed for cluster %s",
                         page_num, cluster_bbox, exc_info=True)
            continue
        clamped_bbox = (clip.x0, clip.y0, clip.x1, clip.y1)
        suggested_alt = _caption_for(clamped_bbox, page_blocks)
        candidates.append(_PageImageCandidate(
            xref=_synthetic_xref(page_num, clamped_bbox),
            page=page_num,
            bbox=clamped_bbox,
            ext="png",
            bytes=png_bytes,
            suggested_alt=suggested_alt,
            source="vector",
        ))

    kept = len(candidates)
    rejected = candidates_count - kept

    stats = _PageVectorStats(
        pages_scanned=1,
        candidates=candidates_count,
        kept=kept,
        rejected=rejected,
        pages_skipped=0,
        reasons=reasons,
    )
    return candidates, stats
