#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Brave Search API backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time

import httpx

from pipeline.session import SearchBackend, SearchResponse, SearchResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_MAX_BRAVE_COUNT = 20
_RETRYABLE_STATUS = {500, 502, 503, 504, 429}
_MAX_RETRIES = 3


class _TokenBucket:
    """Async token-bucket rate limiter. Process-wide budget guard."""

    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._tokens = rate
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = (min)(
                self._rate,
                self._tokens + (now - self._last) * self._rate,
            )
            self._last = now
            if self._tokens < 1:
                await asyncio.sleep((1 - self._tokens) / self._rate)
                self._tokens = 0
            else:
                self._tokens -= 1


class BraveBackend(SearchBackend):
    """Brave Search API.

    Long-lived, shareable across ``WebResearcher`` instances. Owns its
    own ``httpx.AsyncClient`` for persistent connection pooling and a
    token-bucket rate limiter (50 req/s) for budget protection.

    Reads ``BRAVE_API_KEY`` from the environment. Raises ``ValueError``
    immediately if the key is missing.
    """

    name = "brave"

    def __init__(self) -> None:
        key = os.environ.get("BRAVE_API_KEY", "")
        if not key:
            raise ValueError(
                "BRAVE_API_KEY environment variable is required. "
                "Get a key at https://api-dashboard.search.brave.com/register"
            )
        self._api_key = key
        self._client = httpx.AsyncClient(timeout=60.0)
        self._limiter = _TokenBucket(rate=50)

    async def search(
        self, query: str, max_results: int
    ) -> SearchResponse:
        await self._limiter.acquire()
        count = min(max(1, max_results), _MAX_BRAVE_COUNT)

        resp = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.get(
                    _ENDPOINT,
                    params={"q": query, "count": count},
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": self._api_key,
                    },
                    timeout=15,
                )
                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    delay = min(10, (2 ** attempt) * random.uniform(0.5, 1.5))
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("retry-after")
                        if retry_after and retry_after.isdigit():
                            delay = max(delay, float(retry_after))
                    logger.warning(
                        "Brave search %r returned %d, retrying in %.1fs",
                        query, resp.status_code, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as exc:
                if attempt < _MAX_RETRIES:
                    delay = min(10, (2 ** attempt) * random.uniform(0.5, 1.5))
                    logger.warning(
                        "Brave search %r failed (%s), retrying in %.1fs",
                        query, type(exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning("Brave search failed for %r: %s", query, exc)
                return SearchResponse(status_code=0, results=[])
            except httpx.HTTPError as exc:
                logger.warning("Brave search failed for %r: %s", query, exc)
                return SearchResponse(status_code=0, results=[])

        assert resp is not None

        if resp.status_code != 200:
            return SearchResponse(status_code=resp.status_code, results=[])

        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.warning("Brave returned non-JSON response for %r", query)
            return SearchResponse(status_code=resp.status_code, results=[])
        results: list[SearchResult] = []
        for item in data.get("web", {}).get("results", []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            ))
        return SearchResponse(status_code=resp.status_code, results=results)

    async def close(self) -> None:
        """Close the HTTP client. Idempotent."""
        await self._client.aclose()
