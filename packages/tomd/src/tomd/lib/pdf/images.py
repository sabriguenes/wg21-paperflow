#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Embedded raster image extraction (Resource-Dictionary path).

Independent of the MuPDF dict and spatial rawdict text-extraction paths.
This module reads each page's resource dictionary via
``page.get_images(full=True)`` to enumerate the raster image XObjects
referenced by the page, looks up their on-page rectangles via
``page.get_image_rects(xref)``, and pulls the raw bytes via
``doc.extract_image(xref)``. Captions are recovered by a regex over
spatial-path text blocks near each image's bbox.

What this path sees: every embedded JPEG / PNG / JPX, regardless of
rendering.

What this path does NOT see, by design:

- Vector drawings (paths and lines, e.g. flowcharts and graph diagrams).
  ``pymupdf`` does not expose them as images. Page-region rasterisation
  is a future feature; see ``packages/tomd/improvements.md`` section 4
  for the layout-aware extraction path that would unlock this.
- Scanned-page PDFs whose body is one image per page. They trip the
  ``_MAX_IMAGES_PER_PAPER`` cap; see the same reference.

Outputs feed :class:`SectionKind.IMAGE` sections. Library code returns
data and never writes to disk; the CLI orchestration in ``cli.convert``
persists ``ExtractedImage.bytes`` via ``backend.write_paper_image``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf

    from .types import Block

_log = logging.getLogger(__name__)


# Tunable thresholds (CLAUDE.md: "named module-level constants").
_MAX_IMAGES_PER_PAPER = 20

# Minimum bbox dimension (smaller of width, height) for an embedded
# raster to be treated as a figure. Anything smaller is assumed to be
# a font-replacement glyph - PDF renderers embed inline emoji as tiny
# PNGs (typically 8-18pt) when the font can't represent the codepoint.
# Such glyphs are not figures: they have no caption, no standalone
# meaning, and their natural pixel resolution (often 64x64+) does
# not match their on-page rendered size. Promoting them to IMAGE
# sections (a) inserts phantom figures at wrong y-positions in the
# markdown, (b) produces over-sized renderings in the preview, and
# (c) inflates the cap (corpus survey N5007: 107 inline emoji, all
# under 18pt). 20pt is calibrated to survey: smallest genuine figure
# bbox in the workspace is 24x24, comfortably above the threshold.
#
# Trade-off documented: papers where MuPDF's text path *also*
# extracts the emoji as a Unicode character (the common case)
# render the emoji correctly inline and the phantom IMAGE goes
# away. Papers where the emoji exists *only* as the embedded
# raster (N5007's editor's report) lose the emoji from the output
# entirely. The current behavior would render 20 of 107 misplaced
# oversized emoji; the filtered behavior produces clean markdown
# without that visual noise.
_MIN_IMAGE_DIM_PT = 20.0

# End-of-body HTML comment appended when the cap fires. Shared between
# the PDF emit path (tomd.lib.pdf.emit) and the HTML emit path
# (tomd.lib.html.convert) so the marker shape stays identical across
# source formats. Invisible to the rendered HTML; visible to anyone
# grepping the raw markdown for "truncated".
TRUNCATION_MARKER_TEMPLATE = (
    "<!-- tomd:images-truncated: kept {kept} of {total} images. "
    "{dropped} image(s) dropped to stay under the {kept}-image cap. "
    "See _MAX_IMAGES_PER_PAPER in tomd/lib/pdf/images.py. -->"
)

# Caption-proximity search radii, measured in PDF points relative to
# the image's bbox. 60pt below catches the common "Figure N: caption"
# placement; 30pt above catches the occasional "above caption" style.
# Note: no min/max line-length constants - the bare-line fallback was
# removed because at 60pt below a typical page it caught body prose
# like "We discussed this in section 3.1, ..." and misattributed it
# as alt text. Misattribution propagates into LLM prompts via
# pipeline.tools.wrap_source, which is strictly worse than empty alt.
_CAPTION_SEARCH_RADIUS_BELOW_PT = 60.0
_CAPTION_SEARCH_RADIUS_ABOVE_PT = 30.0

# Table is intentionally NOT in this label list. tomd has its own
# SectionKind.TABLE extracted from MuPDF block positions in step 14;
# if we matched "Table N:" here, a results table sitting 30pt below
# an image bbox would silently become misattributed alt text on the
# image. Tables own their captions structurally.
#
# Separator class covers ASCII ``:``, ``.``, ``-`` plus the Unicode
# en-dash (U+2013) and em-dash (U+2014) commonly used in WG21 figure
# captions (e.g. p0957r8's "Figure 1 - Expected memory layout"). The
# plan's literal in section 1.1 was ASCII-only; widening it is a
# straight recall improvement caught by the corpus fixture pass.
_CAPTION_LABEL_RE = re.compile(
    r"^\s*(Figure|Fig\.?|Listing|Diagram|Image|Source\s+code)"
    r"\s+\d+\s*[:.\-–—]\s*(.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedImage:
    """One embedded raster image kept for emission to markdown.

    All fields except ``index_on_page`` and ``stored_filename`` are
    derived during the per-page extraction phase; the index and the
    filename are assigned by :func:`finalize_extraction` after global
    xref deduplication, so that the position numbering reflects the
    final canonical (page, y0, x0) order rather than ``pymupdf``'s
    enumeration.

    Field semantics across the two producer paths:

    - **PDF (this module)**: every field is populated. ``bytes`` carries
      the raw embedded-raster bytes pulled via ``doc.extract_image``.
      The CLI convert orchestration persists those bytes via
      ``backend.write_paper_image``.
    - **HTML (tomd.lib.html.images)**: built from the
      ``HtmlImagesManifest`` sidecar that mailing already wrote to
      disk. ``bytes`` is the empty bytes sentinel ``b""`` (the bytes
      are already on disk; no re-write needed), ``bbox`` is
      ``(0.0, 0.0, 0.0, 0.0)`` (HTML has no spatial concept), and
      ``xref`` is ``0`` (not applicable). ``page`` is ``0`` (the
      paperstore-wide "no page concept" sentinel). The CLI
      orchestration in ``pipeline.process._stage_convert`` checks
      ``bytes`` truthiness to decide whether to call
      ``write_paper_image``.
    """

    page: int                                       # 1-based for PDF, 0 for HTML
    index_on_page: int                              # 1-based, after y/x sort
    ext: str                                        # "png" | "jpeg" | "jpx" | ...
    bytes: bytes                                    # empty for HTML
    bbox: tuple[float, float, float, float]         # (x0, y0, x1, y1); zeros for HTML
    suggested_alt: str
    stored_filename: str                            # "<pid>-fig{page}-{index}.{ext}"
    xref: int                                       # PDF xref; 0 for HTML


@dataclass(frozen=True)
class ExtractionResult:
    """Output of :func:`finalize_extraction`.

    - ``images``: up to :data:`_MAX_IMAGES_PER_PAPER` entries, sorted
      by ``(page, bbox.y0, bbox.x0)``. Empty iff the document has no
      embedded raster images.
    - ``source_image_count``: number of unique xrefs in the source,
      regardless of cap. The CLI logs ``kept M of N`` from this field.
    - ``images_truncated``: True iff ``source_image_count`` exceeded
      the cap.
    """

    images: list[ExtractedImage]
    source_image_count: int
    images_truncated: bool


@dataclass(frozen=True)
class _PageImageCandidate:
    """Per-rect record from a single page, before global dedup.

    Held as an internal type because ``ExtractedImage.index_on_page``
    is only meaningful after :func:`finalize_extraction` has chosen
    each xref's canonical occurrence and re-sorted within the chosen
    page. Promoting a candidate to :class:`ExtractedImage` happens in
    that finalize step.
    """

    xref: int
    page: int                                       # 1-based
    bbox: tuple[float, float, float, float]
    ext: str
    bytes: bytes
    suggested_alt: str


def _bbox_to_tuple(rect) -> tuple[float, float, float, float]:
    """Convert a ``pymupdf.Rect`` (or any 4-tuple-like) to a plain tuple.

    Kept out of :class:`ExtractedImage` so a downstream consumer never
    needs to import ``pymupdf`` just to read a bbox.
    """
    return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))


def _caption_for(
    bbox: tuple[float, float, float, float],
    page_blocks: Sequence["Block"],
) -> str:
    """Return the matched caption text near ``bbox``, or empty string.

    Searches lines whose vertical position is within
    ``_CAPTION_SEARCH_RADIUS_BELOW_PT`` below the image (preferred)
    or ``_CAPTION_SEARCH_RADIUS_ABOVE_PT`` above. Returns the first
    line that matches :data:`_CAPTION_LABEL_RE` (e.g. "Figure 1: ...",
    "Listing 2 - ..."). No bare-line fallback - empty alt is the
    documented graceful failure.
    """
    img_x0, img_y0, _img_x1, img_y1 = bbox

    below: list[tuple[float, str]] = []
    above: list[tuple[float, str]] = []
    for block in page_blocks:
        for line in block.lines:
            text = line.text.strip()
            if not text:
                continue
            ly0 = line.bbox[1]
            if img_y1 <= ly0 <= img_y1 + _CAPTION_SEARCH_RADIUS_BELOW_PT:
                below.append((ly0, text))
            elif img_y0 - _CAPTION_SEARCH_RADIUS_ABOVE_PT <= ly0 < img_y0:
                above.append((ly0, text))

    # Below candidates: nearest first (smallest y0 above image's bottom).
    below.sort(key=lambda t: t[0])
    # Above candidates: nearest first (largest y0, just before image top).
    above.sort(key=lambda t: -t[0])

    for _y, text in below + above:
        if _CAPTION_LABEL_RE.match(text):
            return text.strip()
    return ""


def extract_page_images(
    page: "pymupdf.Page",
    page_blocks: Sequence["Block"],
) -> list[_PageImageCandidate]:
    """Enumerate embedded raster images for a single page.

    Called per page inside step 1 of the pipeline (the document must
    still be open). Reads ``page.get_images(full=True)``, looks up
    bbox rectangles via ``page.get_image_rects(xref)``, pulls bytes
    via ``doc.extract_image(xref)``, and runs the caption-proximity
    heuristic over the provided spatial-path text blocks for this page.

    Returns one :class:`_PageImageCandidate` per image rect on the
    page. Cross-page deduplication and capping happen later in
    :func:`finalize_extraction`.
    """
    doc = page.parent
    page_num = page.number + 1                     # 1-based for downstream
    candidates: list[_PageImageCandidate] = []

    try:
        image_records = page.get_images(full=True)
    except Exception:  # pymupdf raises on malformed resource dicts
        _log.warning("page %d: get_images failed", page_num, exc_info=True)
        return candidates

    cached_bytes: dict[int, tuple[str, bytes] | None] = {}

    for record in image_records:
        xref = record[0]

        try:
            rects = page.get_image_rects(xref)
        except Exception:
            _log.warning("page %d xref %d: get_image_rects failed",
                         page_num, xref, exc_info=True)
            continue
        if not rects:
            # Resource is referenced but not rendered on this page.
            continue

        if xref not in cached_bytes:
            try:
                info = doc.extract_image(xref)
            except Exception:
                _log.warning("page %d xref %d: extract_image failed",
                             page_num, xref, exc_info=True)
                cached_bytes[xref] = None
                continue
            ext = (info.get("ext") or "bin").lower()
            data = info.get("image")
            if not isinstance(data, (bytes, bytearray)):
                _log.warning("page %d xref %d: extract_image returned non-bytes",
                             page_num, xref)
                cached_bytes[xref] = None
                continue
            cached_bytes[xref] = (ext, bytes(data))
        info_pair = cached_bytes[xref]
        if info_pair is None:
            continue
        ext, data = info_pair

        for rect in rects:
            bbox = _bbox_to_tuple(rect)
            suggested_alt = _caption_for(bbox, page_blocks)
            candidates.append(_PageImageCandidate(
                xref=xref,
                page=page_num,
                bbox=bbox,
                ext=ext,
                bytes=data,
                suggested_alt=suggested_alt,
            ))

    return candidates


def finalize_extraction(
    per_page: Iterable[Iterable[_PageImageCandidate]],
    pid: str,
) -> ExtractionResult:
    """Dedupe by xref, apply the cap, assign stable filenames.

    ``per_page`` is the per-page candidate lists from
    :func:`extract_page_images`. ``pid`` is the lowercased paper id
    used for the ``<pid>-fig{page}-{index}.{ext}`` filenames.

    Steps:

    1. Flatten all candidates into one list.
    2. Group by ``xref``. For each unique xref, pick the
       smallest ``(page, bbox.y0, bbox.x0)`` rect as canonical.
       Other rects of the same xref produce no output - a logo that
       appears on every page contributes one IMAGE section, at its
       first occurrence.
    3. Sort the unique-xref representatives by their canonical
       ``(page, y0, x0)``.
    4. Truncate to :data:`_MAX_IMAGES_PER_PAPER`.
    5. Within each surviving image's first-occurrence page, assign
       ``index_on_page`` after sorting that page's survivors by
       ``(y0, x0)`` - so filenames stay stable across ``pymupdf``
       major-version bumps that might change ``get_images``'s order.
    """
    flat: list[_PageImageCandidate] = []
    for page_list in per_page:
        flat.extend(page_list)

    by_xref: dict[int, list[_PageImageCandidate]] = {}
    for cand in flat:
        by_xref.setdefault(cand.xref, []).append(cand)

    canonicals: list[_PageImageCandidate] = []
    for xref, cands in by_xref.items():
        cands_sorted = sorted(cands, key=lambda c: (c.page, c.bbox[1], c.bbox[0]))
        canonicals.append(cands_sorted[0])

    # Drop emoji-sized rasters before counting. The cap and the
    # source_image_count both see post-filter numbers, so the
    # truncation marker doesn't mis-attribute emoji bloat as a
    # figure overload.
    canonicals = [
        c for c in canonicals
        if min(c.bbox[2] - c.bbox[0], c.bbox[3] - c.bbox[1])
        >= _MIN_IMAGE_DIM_PT
    ]

    canonicals.sort(key=lambda c: (c.page, c.bbox[1], c.bbox[0]))
    source_image_count = len(canonicals)
    images_truncated = source_image_count > _MAX_IMAGES_PER_PAPER
    if images_truncated:
        canonicals = canonicals[:_MAX_IMAGES_PER_PAPER]

    # Assign index_on_page after the final (page, y0, x0) sort so the
    # numbering matches the on-disk order even if multiple kept
    # images share a page.
    pid_lower = pid.strip().lower()
    by_page: dict[int, list[_PageImageCandidate]] = {}
    for cand in canonicals:
        by_page.setdefault(cand.page, []).append(cand)

    final: list[ExtractedImage] = []
    for page_num, cands in by_page.items():
        cands_sorted = sorted(cands, key=lambda c: (c.bbox[1], c.bbox[0]))
        for idx, cand in enumerate(cands_sorted, start=1):
            final.append(ExtractedImage(
                page=cand.page,
                index_on_page=idx,
                ext=cand.ext,
                bytes=cand.bytes,
                bbox=cand.bbox,
                suggested_alt=cand.suggested_alt,
                stored_filename=f"{pid_lower}-fig{cand.page}-{idx}.{cand.ext}",
                xref=cand.xref,
            ))

    final.sort(key=lambda im: (im.page, im.bbox[1], im.bbox[0]))

    return ExtractionResult(
        images=final,
        source_image_count=source_image_count,
        images_truncated=images_truncated,
    )
