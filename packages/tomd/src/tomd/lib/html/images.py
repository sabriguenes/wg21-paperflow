#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""HTML side of the embedded-image flow.

Reads the typed ``<pid>.html-images.json`` manifest that the mailing
fetcher wrote alongside the source HTML, applies the same 20-image cap
the PDF path uses (see :mod:`tomd.lib.pdf.images`), and returns a list
of :class:`ExtractedImage` records that the HTML renderer can slot in.

Pure module: no network, no disk reads of image bytes. The bytes are
already on disk from mailing time; the convert step only needs to know
where each ``<img>`` reference in the source HTML maps to on disk so
the rendered markdown can carry a stable ``![alt](filename)`` reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from paperstore.html_manifest import HtmlImageEntry, HtmlImagesManifest

from tomd.lib.pdf.images import _MAX_IMAGES_PER_PAPER, ExtractedImage


@dataclass(frozen=True)
class HtmlImagesResult:
    """Output of :func:`load_html_images`.

    ``images`` is the capped, document-order list to slot into the
    markdown; ``src_to_entry`` is the lookup the renderer uses to
    rewrite ``<img src=...>`` references to stable on-disk filenames
    and resolve their prioritized alt text.
    """

    images: list[ExtractedImage] = field(default_factory=list)
    source_image_count: int = 0
    images_truncated: bool = False
    src_to_entry: dict[str, HtmlImageEntry] = field(default_factory=dict)


def load_html_images(manifest: HtmlImagesManifest) -> HtmlImagesResult:
    """Build :class:`ExtractedImage` records from a parsed manifest.

    Cap matches the PDF path's :data:`_MAX_IMAGES_PER_PAPER`. Truncation
    drops images with the highest ``document_order`` so the kept set is
    the earliest ones. Caption priority for ``suggested_alt``:
    ``caption_text`` (from ``<figcaption>``) > ``alt_attr`` (from
    ``<img alt=...>``) > empty.

    ``ExtractedImage.bytes`` is the empty bytes sentinel here: the
    mailing fetcher already persisted the bytes at download time, so
    the convert orchestration must not re-write them. ``ExtractedImage.bbox``
    is ``(0, 0, 0, 0)`` because HTML has no spatial concept; emit
    position is driven by ``document_order`` instead.
    """
    entries = sorted(manifest.entries, key=lambda e: e.document_order)
    source_image_count = len(entries)
    truncated = source_image_count > _MAX_IMAGES_PER_PAPER
    kept = entries[:_MAX_IMAGES_PER_PAPER] if truncated else entries

    images: list[ExtractedImage] = []
    src_to_entry: dict[str, HtmlImageEntry] = {}
    for entry in kept:
        alt = entry.caption_text or entry.alt_attr or ""
        ext = Path(entry.stored_filename).suffix.lstrip(".").lower() or "bin"
        images.append(ExtractedImage(
            page=0,
            index_on_page=entry.document_order,
            ext=ext,
            bytes=b"",
            bbox=(0.0, 0.0, 0.0, 0.0),
            suggested_alt=alt,
            stored_filename=entry.stored_filename,
            xref=0,
        ))
        src_to_entry[entry.original_src] = entry

    return HtmlImagesResult(
        images=images,
        source_image_count=source_image_count,
        images_truncated=truncated,
        src_to_entry=src_to_entry,
    )
