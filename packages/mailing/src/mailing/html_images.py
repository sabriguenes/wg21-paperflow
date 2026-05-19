#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Walk HTML source for ``<img>`` references and fetch their bytes.

Mirror of the PDF :mod:`tomd.lib.pdf.images` path's responsibilities, but
specialized for HTML sources: data: URIs are decoded in-process, http(s)://
URIs are fetched via ``httpx``, and relative-path src attributes are resolved
against the paper's source URL and fetched. The output is one
:class:`HtmlFetchedImage` per successfully-recovered image, returned in
document order.

Pure module by the mailing CLAUDE.md invariant: returns data, never writes.
The caller (CLI download stage) persists each entry via
:meth:`StorageBackend.write_paper_image` and emits the manifest sidecar via
:meth:`StorageBackend.get_html_images_manifest_path`.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from mailing import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)

# Per-image fetch timeout, named so a future tweak doesn't require a code dive.
# Smaller than the paper-fetch timeout because individual images are typically
# << the paper source itself; a slow per-image fetch should not stall the
# whole download stage.
_HTML_IMAGE_FETCH_TIMEOUT_S = 30

# Mirror of pipeline.session._MAX_FETCH_BYTES so a malicious or broken
# referenced image cannot exhaust memory.
_MAX_IMAGE_BYTES = 25 * 1024 * 1024

# Map common ``image/*`` MIME types to the file extension used by
# :meth:`StorageBackend.write_paper_image`. Unknown subtypes fall back to
# the subtype string, which is good enough for round-tripping through the
# manifest (and matches the PDF path's pymupdf-reported ``ext`` field).
_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
}

# data: URI of the form  data:[<mediatype>][;base64],<data>
_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[^;,]+)?(?P<params>(?:;[^,]+)*)?,(?P<data>.*)$",
    re.DOTALL,
)


@dataclass(frozen=True)
class HtmlFetchedImage:
    """One image successfully recovered from an HTML paper source.

    ``document_order`` is 1-based and reflects the position of the
    ``<img>`` element in the parsed HTML. Drives the on-disk filename
    (``<pid>-fig0-{document_order}.{ext}``) and the markdown emit order.

    ``caption_text`` is the contents of the nearest enclosing
    ``<figcaption>`` (empty when the image is not inside ``<figure>``);
    ``alt_attr`` is the raw ``alt=`` attribute (empty when absent).
    The tomd-side reader picks the better of the two as the alt text.
    """

    original_src: str
    ext: str
    bytes: bytes
    document_order: int
    caption_text: str
    alt_attr: str


def _ext_from_mime(mime: str | None) -> str:
    if not mime:
        return "bin"
    mime = mime.strip().lower()
    if mime in _MIME_TO_EXT:
        return _MIME_TO_EXT[mime]
    # ``image/foo`` -> ``foo``. Drops parameters defensively.
    if mime.startswith("image/"):
        subtype = mime[len("image/") :].split(";", 1)[0]
        return subtype or "bin"
    return "bin"


def _ext_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lstrip(".").lower()
    return suffix or "bin"


def _decode_data_uri(src: str) -> tuple[str, bytes] | None:
    """Parse a ``data:`` URI into ``(ext, bytes)``. Returns None on malformed.

    Honors only ``base64`` and the implicit URL-encoded default. Other
    parameters (charset, ...) are tolerated but ignored.
    """
    m = _DATA_URI_RE.match(src)
    if not m:
        return None
    mime = m.group("mime") or ""
    params = m.group("params") or ""
    payload = m.group("data") or ""
    is_base64 = ";base64" in params.lower()
    try:
        if is_base64:
            data = base64.b64decode(payload, validate=False)
        else:
            from urllib.parse import unquote_to_bytes

            data = unquote_to_bytes(payload)
    except (ValueError, base64.binascii.Error):
        return None
    if len(data) > _MAX_IMAGE_BYTES:
        return None
    return _ext_from_mime(mime), data


async def _fetch_http(
    url: str,
    *,
    client: httpx.AsyncClient,
) -> tuple[str, bytes] | None:
    """GET ``url``, return ``(ext, bytes)``, or None on error / oversized.

    Streams the response so an oversized image is aborted before the
    full body is buffered. Per-image errors are logged and surface as
    None so the caller can skip and continue.
    """
    try:
        async with client.stream(
            "GET",
            url,
            timeout=_HTML_IMAGE_FETCH_TIMEOUT_S,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > _MAX_IMAGE_BYTES:
                    logger.warning(
                        "html image %s exceeded %d bytes; skipping",
                        url, _MAX_IMAGE_BYTES,
                    )
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
        mime = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        ext = _ext_from_mime(mime) if mime else _ext_from_url(url)
        if ext == "bin":
            ext = _ext_from_url(url)
        return ext, data
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning("html image fetch failed for %s: %s", url, exc)
        return None


def _caption_from(img: Tag) -> str:
    """Return ``<figcaption>`` text when the image is inside a ``<figure>``.

    Walks ancestors looking for a ``<figure>``. Returns the first
    ``<figcaption>`` child's stripped text, or empty if absent.
    """
    figure = img.find_parent("figure")
    if figure is None:
        return ""
    cap = figure.find("figcaption")
    if cap is None:
        return ""
    return " ".join(cap.get_text(" ", strip=True).split())


async def fetch_html_images(
    html_bytes: bytes | str,
    *,
    source_url: str,
    client: httpx.AsyncClient,
) -> list[HtmlFetchedImage]:
    """Parse ``html_bytes`` and recover every ``<img>`` it references.

    ``source_url`` is the paper source's URL; used to resolve relative
    ``src`` attributes against. ``client`` is an already-open httpx
    client, reused for both this and the parent paper download so TCP
    connections amortize.

    Returns one :class:`HtmlFetchedImage` per successfully-recovered
    image, in document order. Per-image failures (network errors,
    malformed data URIs, oversized payloads) log a warning and are
    skipped so the rest of the paper's images still land. The whole
    paper download does not fail on a single bad image.
    """
    if isinstance(html_bytes, bytes):
        soup = BeautifulSoup(html_bytes, "html.parser")
    else:
        soup = BeautifulSoup(html_bytes, "html.parser")

    out: list[HtmlFetchedImage] = []
    order = 0
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        order += 1

        alt_attr = (img.get("alt") or "").strip()
        caption_text = _caption_from(img)

        decoded: tuple[str, bytes] | None
        if src.startswith("data:"):
            decoded = _decode_data_uri(src)
            if decoded is None:
                logger.warning(
                    "html image %d: malformed or oversized data: URI; skipping",
                    order,
                )
                continue
        else:
            absolute = src if urlparse(src).netloc else urljoin(source_url, src)
            if urlparse(absolute).scheme not in ("http", "https"):
                logger.warning(
                    "html image %d: unsupported scheme in src=%r; skipping",
                    order, src,
                )
                continue
            decoded = await _fetch_http(absolute, client=client)
            if decoded is None:
                continue

        ext, data = decoded
        out.append(HtmlFetchedImage(
            original_src=src,
            ext=ext,
            bytes=data,
            document_order=order,
            caption_text=caption_text,
            alt_attr=alt_attr,
        ))

    return out
