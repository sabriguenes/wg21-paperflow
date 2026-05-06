#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Fetch paper source bytes over HTTP. Pure network I/O, no storage."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from mailing import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = 120
_ALLOWED_SUFFIXES = (".pdf", ".html", ".htm")


def _suffix_from_url(source_url: str) -> str:
    name = Path(urlparse(source_url).path).name.lower()
    suffix = Path(name).suffix
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(
            f"source_url must end with one of {_ALLOWED_SUFFIXES}: {source_url!r}"
        )
    # Normalize .htm to .html so get_source_path finds exactly one entry.
    return ".html" if suffix == ".htm" else suffix


def default_client(*, timeout: float = _FETCH_TIMEOUT_SEC) -> httpx.AsyncClient:
    """Create an ``AsyncClient`` with standard paperflow settings.

    Callers that issue many requests should create one client and share
    it across the batch to benefit from TCP connection reuse.
    """
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )


async def content_length(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
) -> int | None:
    """Send a HEAD request and return Content-Length as int, or None if absent.

    Uses ``Accept-Encoding: identity`` to get the uncompressed size.
    Returns ``None`` if the header is missing or the request fails.

    When ``client`` is provided it is used as-is (caller owns its
    lifetime). Otherwise a throwaway client is created for the call.
    """
    try:
        if client is not None:
            resp = await client.head(
                url, headers={"Accept-Encoding": "identity"},
            )
            resp.raise_for_status()
        else:
            async with default_client(timeout=timeout) as c:
                resp = await c.head(
                    url, headers={"Accept-Encoding": "identity"},
                )
                resp.raise_for_status()
        cl = resp.headers.get("content-length")
        return int(cl) if cl is not None else None
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError):
        logger.warning("HEAD request failed for %s", url, exc_info=True)
        return None


async def download_paper(
    paper_id: str,
    *,
    source_url: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = _FETCH_TIMEOUT_SEC,
) -> tuple[bytes, str] | None:
    """Fetch a paper's source over HTTP.

    Returns ``(content, suffix)`` on success; ``None`` if ``source_url``
    is empty. Suffix is normalized (``.htm`` becomes ``.html``).
    Performs no storage I/O.

    When ``client`` is provided it is used as-is (caller owns its
    lifetime). Otherwise a throwaway client is created for the call.
    """
    if not source_url:
        logger.warning("No source URL for %s - skipping download", paper_id)
        return None

    suffix = _suffix_from_url(source_url)
    logger.info("Downloading %s from %s", paper_id, source_url)

    if client is not None:
        resp = await client.get(source_url)
    else:
        async with default_client(timeout=timeout) as c:
            resp = await c.get(source_url)
    resp.raise_for_status()

    return resp.content, suffix
