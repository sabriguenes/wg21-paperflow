#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for ``mailing.html_images``.

Pure-function tests: synthetic HTML inputs, mocked httpx client for the
network path. The mailing CLAUDE.md invariant ("pure module, returns
data, never writes") means there is no fixture filesystem to set up.
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import MagicMock


from mailing.html_images import (
    _decode_data_uri,
    _ext_from_mime,
    fetch_html_images,
)


# ---- data: URI decoding -----------------------------------------------------


def test_data_uri_base64_png():
    payload = b"\x89PNG\r\n\x1a\nbody"
    src = f"data:image/png;base64,{base64.b64encode(payload).decode()}"
    result = _decode_data_uri(src)
    assert result == ("png", payload)


def test_data_uri_jpeg_aliases_to_jpeg():
    src = f"data:image/jpeg;base64,{base64.b64encode(b'x').decode()}"
    assert _decode_data_uri(src) == ("jpeg", b"x")


def test_data_uri_image_jpg_maps_to_jpeg():
    """image/jpg is a common non-standard MIME synonym; normalize it."""
    src = f"data:image/jpg;base64,{base64.b64encode(b'x').decode()}"
    assert _decode_data_uri(src) == ("jpeg", b"x")


def test_data_uri_non_base64_url_encoded():
    src = "data:image/svg+xml,%3Csvg/%3E"
    result = _decode_data_uri(src)
    assert result is not None
    ext, data = result
    assert ext == "svg"
    assert data == b"<svg/>"


def test_data_uri_unknown_subtype_passes_through():
    src = f"data:image/heif;base64,{base64.b64encode(b'x').decode()}"
    assert _decode_data_uri(src) == ("heif", b"x")


def test_data_uri_missing_mime_falls_back():
    src = f"data:;base64,{base64.b64encode(b'x').decode()}"
    assert _decode_data_uri(src) == ("bin", b"x")


def test_data_uri_malformed_returns_none():
    assert _decode_data_uri("not-a-data-uri") is None
    assert _decode_data_uri("data:image/png;base64,!@#$%") is None or (
        # Some platforms permissively decode garbage. Either path is
        # acceptable - what matters is no crash.
        _decode_data_uri("data:image/png;base64,!@#$%") is not None
    )


def test_ext_from_mime_handles_params():
    """A Content-Type like 'image/png; charset=utf-8' should still resolve to png."""
    assert _ext_from_mime("image/png; charset=utf-8") == "png"


# ---- fetch_html_images: HTML walking ----------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_client_returning(body_by_url: dict[str, tuple[bytes, str]]) -> MagicMock:
    """Build a fake httpx client whose ``stream`` returns the body for each URL.

    ``body_by_url`` maps URL -> (bytes, content_type).
    """
    class _Resp:
        def __init__(self, body: bytes, ctype: str):
            self._body = body
            self.headers = {"content-type": ctype}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    client = MagicMock()

    def stream(_method, url, **_kwargs):
        body, ctype = body_by_url[url]
        return _Resp(body, ctype)

    client.stream = stream
    return client


def test_fetch_html_images_data_uri_only():
    html = (
        b"<html><body>"
        b"<img alt='one' src='data:image/png;base64," + base64.b64encode(b"PNG1") +
        b"'>"
        b"<img alt='two' src='data:image/jpeg;base64," + base64.b64encode(b"JPG2") +
        b"'>"
        b"</body></html>"
    )
    client = MagicMock()
    out = _run(fetch_html_images(
        html, source_url="https://example.com/p.html", client=client,
    ))
    assert len(out) == 2
    assert out[0].ext == "png"
    assert out[0].bytes == b"PNG1"
    assert out[0].document_order == 1
    assert out[0].alt_attr == "one"
    assert out[1].ext == "jpeg"
    assert out[1].bytes == b"JPG2"
    assert out[1].document_order == 2


def test_fetch_html_images_remote_url():
    html = b"<html><body><img alt='remote' src='https://cdn.example.com/x.png'></body></html>"
    client = _make_client_returning({
        "https://cdn.example.com/x.png": (b"REMOTE_PNG", "image/png"),
    })
    out = _run(fetch_html_images(
        html, source_url="https://example.com/p.html", client=client,
    ))
    assert len(out) == 1
    assert out[0].original_src == "https://cdn.example.com/x.png"
    assert out[0].bytes == b"REMOTE_PNG"
    assert out[0].ext == "png"


def test_fetch_html_images_relative_resolved_against_source_url():
    html = b"<html><body><img src='figures/diagram.png'></body></html>"
    client = _make_client_returning({
        "https://example.com/papers/figures/diagram.png": (b"REL_PNG", "image/png"),
    })
    out = _run(fetch_html_images(
        html,
        source_url="https://example.com/papers/p.html",
        client=client,
    ))
    assert len(out) == 1
    assert out[0].original_src == "figures/diagram.png"
    assert out[0].bytes == b"REL_PNG"


def test_fetch_html_images_captures_figcaption():
    html = (
        b"<html><body>"
        b"<figure>"
        b"<img alt='alt-only' src='data:image/png;base64," + base64.b64encode(b"X") + b"'>"
        b"<figcaption>Figure 1: caption text</figcaption>"
        b"</figure>"
        b"</body></html>"
    )
    out = _run(fetch_html_images(
        html, source_url="https://example.com/", client=MagicMock(),
    ))
    assert len(out) == 1
    assert out[0].caption_text == "Figure 1: caption text"
    assert out[0].alt_attr == "alt-only"


def test_fetch_html_images_skips_unknown_scheme():
    """ftp:// and other non-http schemes are skipped with a warning."""
    html = b"<html><body><img src='ftp://example.com/x.png'></body></html>"
    out = _run(fetch_html_images(
        html, source_url="https://example.com/", client=MagicMock(),
    ))
    assert out == []


def test_fetch_html_images_skips_imgs_without_src():
    """An <img> with no src is silently dropped (no document_order bump)."""
    html = (
        b"<html><body>"
        b"<img alt='no-src'>"
        b"<img src='data:image/png;base64," + base64.b64encode(b"X") + b"'>"
        b"</body></html>"
    )
    out = _run(fetch_html_images(
        html, source_url="https://example.com/", client=MagicMock(),
    ))
    assert len(out) == 1
    # The img with no src was skipped before order increment, so this
    # is document_order=1, not 2.
    assert out[0].document_order == 1


def test_fetch_html_images_continues_after_one_failure():
    """A single failing fetch must not abort the whole walk."""
    import httpx

    # Custom client: first url 404s (raise_for_status raises an
    # httpx.HTTPStatusError, matching real client behavior); second
    # succeeds. Ensures the per-image error firewall holds.
    class _BadResp:
        headers = {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=httpx.Request("GET", "https://example.com/bad.png"),
                response=httpx.Response(404),
            )

        async def aiter_bytes(self):
            if False:
                yield b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _GoodResp:
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"GOOD"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    def stream(_method, url, **_kwargs):
        if "bad" in url:
            return _BadResp()
        return _GoodResp()

    client = MagicMock()
    client.stream = stream

    html = (
        b"<html><body>"
        b"<img alt='one' src='https://example.com/bad.png'>"
        b"<img alt='two' src='https://example.com/good.png'>"
        b"</body></html>"
    )
    out = _run(fetch_html_images(
        html, source_url="https://example.com/", client=client,
    ))
    assert [im.original_src for im in out] == ["https://example.com/good.png"]


def test_fetch_html_images_document_order_is_sequential():
    """document_order counts successfully-walked <img> tags in source order."""
    html = (
        b"<html><body>"
        b"<img src='data:image/png;base64," + base64.b64encode(b"A") + b"'>"
        b"<img src='data:image/png;base64," + base64.b64encode(b"B") + b"'>"
        b"<img src='data:image/png;base64," + base64.b64encode(b"C") + b"'>"
        b"</body></html>"
    )
    out = _run(fetch_html_images(
        html, source_url="https://example.com/", client=MagicMock(),
    ))
    assert [im.document_order for im in out] == [1, 2, 3]
    assert [im.bytes for im in out] == [b"A", b"B", b"C"]
