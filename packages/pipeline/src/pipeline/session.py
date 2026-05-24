#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""WebResearcher: web search and content fetch for LLM pipelines."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import trafilatura

from pipeline.tools import _random_tag, inject_untrusted

logger = logging.getLogger(__name__)

_DEFAULT_FETCH_TIMEOUT = 15
_DEFAULT_MAX_FETCH_LENGTH = 8000
_RETRYABLE_STATUS = {500, 502, 503, 504, 429}
_MAX_FETCH_RETRIES = 3
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Hard cap on response body size. WG21 papers are < 5 MB; the largest
# scanned proceedings observed is ~22 MB. The cap aborts oversized
# responses mid-stream before any extractor runs.
_MAX_FETCH_BYTES = 25 * 1024 * 1024

# (raw bytes, max_length) -> extracted text or None.
BinaryExtractor = Callable[[bytes, int], "str | None"]

_trafilatura_config = trafilatura.settings.use_config()


@dataclass(frozen=True)
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class SearchResponse:
    """Search results with the HTTP status code from the provider."""

    status_code: int
    results: list[SearchResult]


@dataclass(frozen=True)
class FetchResponse:
    """Fetched page content with the HTTP status code."""

    status_code: int
    content: str


class SearchBackend(ABC):
    """Base class for search backends.

    Subclass, implement ``search()``, and register in
    ``backends/__init__.py``.
    """

    name: str

    @abstractmethod
    async def search(
        self, query: str, max_results: int
    ) -> SearchResponse:
        """Execute a search and return results with status code."""

    async def close(self) -> None:
        """Release resources. Default is a no-op."""


# open-std.org serves papers with lowercase filenames (e.g. p3175r3.html)
# on a case-sensitive Linux filesystem.  LLM agents often construct URLs
# using the uppercase form from bibliographic references (P3175R3.html),
# which returns a 404.  Lowercase the filename component to fix this.
_OPEN_STD_PAPER_RE = re.compile(
    r"^(https?://www\.open-std\.org/jtc1/sc22/wg21/docs/papers/\d{4}/)([^/]+)$",
    re.IGNORECASE,
)


def _normalize_open_std_url(url: str) -> str:
    m = _OPEN_STD_PAPER_RE.match(url)
    if m:
        return m.group(1) + m.group(2).lower()
    return url


class WebResearcher:
    """Web search and fetch for LLM pipelines.

    Create one per pipeline run. Pass a shared ``SearchBackend`` for
    connection reuse across parallel runs, or omit to auto-create one.

    Example::

        async with WebResearcher() as researcher:
            results = await researcher.search("coroutine executor C++")
            page = await researcher.fetch("https://example.com")
    """

    def __init__(
        self,
        *,
        backend: SearchBackend | None = None,
        binary_extractors: dict[str, BinaryExtractor] | None = None,
        guard_tag: str | None = None,
    ) -> None:
        from pipeline.backends import get_default_backend

        self._backend = backend or get_default_backend()
        self._owns_backend = backend is None
        self._client = httpx.AsyncClient(timeout=60.0)
        self._binary_extractors: dict[str, BinaryExtractor] = (
            dict(binary_extractors) if binary_extractors else {}
        )
        self._guard_tag = guard_tag or _random_tag()
        self._closed = False

    async def __aenter__(self) -> WebResearcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close HTTP clients. Idempotent."""
        if not self._closed:
            try:
                await self._client.aclose()
                if self._owns_backend:
                    await self._backend.close()
            finally:
                self._closed = True

    async def search(
        self, query: str, max_results: int = 5
    ) -> SearchResponse:
        """Search via the configured backend."""
        if self._closed:
            raise RuntimeError("Researcher is closed.")

        response = await self._backend.search(query, max_results)

        if response.status_code != 200:
            logger.warning(
                "Search backend %s returned HTTP %d for %r",
                self._backend.name, response.status_code, query,
            )
        else:
            logger.debug(
                "Search backend %s returned %d results for %r",
                self._backend.name, len(response.results), query,
            )

        return response

    async def fetch(
        self,
        url: str,
        *,
        extract: bool = True,
        max_length: int = _DEFAULT_MAX_FETCH_LENGTH,
    ) -> FetchResponse:
        """Fetch a URL and optionally extract article content.

        The response body is read with a hard cap of ``_MAX_FETCH_BYTES``
        (25 MB). Oversized responses are aborted mid-stream and return an
        error message without buffering the full body.

        When ``extract=True`` (default): HTML is run through trafilatura;
        binary content types matching a registered extractor are routed
        through it; otherwise the standard extraction-failed error is
        returned.

        When ``extract=False``: HTML is returned as the raw decoded
        response body. Binary content types matching a registered
        extractor return an empty string; binary bodies have no
        meaningful raw text form for LLM tools, and the registry is the
        entry point for getting useful text out of them.
        """
        if self._closed:
            raise RuntimeError("Researcher is closed.")
        if not url:
            return FetchResponse(status_code=0, content="Error: Empty URL")

        url = _normalize_open_std_url(url)

        body: bytes | None = None
        content_type = ""
        charset = "utf-8"
        status_code = 0

        for attempt in range(_MAX_FETCH_RETRIES + 1):
            try:
                async with self._client.stream(
                    "GET", url,
                    headers={"User-Agent": _DEFAULT_USER_AGENT},
                    timeout=_DEFAULT_FETCH_TIMEOUT,
                    follow_redirects=True,
                ) as resp:
                    if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_FETCH_RETRIES:
                        delay = min(10, (2 ** attempt) * random.uniform(0.5, 1.5))
                        if resp.status_code == 429:
                            retry_after = resp.headers.get("retry-after")
                            if retry_after and retry_after.isdigit():
                                delay = max(delay, float(retry_after))
                        logger.warning(
                            "Fetch %s returned %d, retrying in %.1fs",
                            url, resp.status_code, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status_code != 200:
                        logger.warning(
                            "Fetch got HTTP %d for %r", resp.status_code, url,
                        )
                        return FetchResponse(
                            status_code=resp.status_code,
                            content=f"Error: HTTP {resp.status_code} for {url}",
                        )

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_FETCH_BYTES:
                            logger.warning(
                                "Fetch aborted at %d bytes (cap %d) for %r",
                                total, _MAX_FETCH_BYTES, url,
                            )
                            return FetchResponse(
                                status_code=resp.status_code,
                                content=(
                                    f"Error: Response exceeded "
                                    f"{_MAX_FETCH_BYTES} bytes for {url}"
                                ),
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    content_type = (
                        resp.headers.get("content-type", "")
                        .split(";")[0].strip().lower()
                    )
                    charset = resp.charset_encoding or "utf-8"
                    status_code = resp.status_code
                    break
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as exc:
                if attempt < _MAX_FETCH_RETRIES:
                    delay = min(10, (2 ** attempt) * random.uniform(0.5, 1.5))
                    logger.warning(
                        "Fetch %s failed (%s), retrying in %.1fs",
                        url, type(exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning("Fetch failed for %r: %s", url, exc)
                return FetchResponse(
                    status_code=0,
                    content=f"Error: Failed to fetch URL: {exc}",
                )
            except httpx.HTTPError as exc:
                logger.warning("Fetch failed for %r: %s", url, exc)
                return FetchResponse(
                    status_code=0,
                    content=f"Error: Failed to fetch URL: {exc}",
                )

        assert body is not None

        extractor = (
            self._binary_extractors.get(content_type) if content_type else None
        )

        if extractor is not None:
            if not extract:
                # Binary responses have no meaningful raw form for an LLM
                # tool; the registry is the only way to get useful text out.
                return FetchResponse(status_code=status_code, content="")
            text = await asyncio.to_thread(extractor, body, max_length)
            if not text:
                return FetchResponse(
                    status_code=status_code,
                    content="Error: Could not extract content from page",
                )
            if len(text) > max_length:
                text = text[:max_length] + "\n\n[Content truncated]"
            return FetchResponse(status_code=status_code, content=text)

        html = body.decode(charset, errors="replace")
        if not html:
            return FetchResponse(
                status_code=status_code,
                content="Error: No content retrieved from URL",
            )

        if not extract:
            return FetchResponse(status_code=status_code, content=html)

        text = await asyncio.to_thread(
            trafilatura.extract,
            html, output_format="markdown", config=_trafilatura_config,
        )
        if not text:
            text = await asyncio.to_thread(
                trafilatura.extract,
                html, output_format="txt", config=_trafilatura_config,
            )
        if not text:
            return FetchResponse(
                status_code=status_code,
                content="Error: Could not extract content from page",
            )

        if len(text) > max_length:
            text = text[:max_length] + "\n\n[Content truncated]"

        return FetchResponse(status_code=status_code, content=text)

    async def web_search(self, query: str) -> str:
        """Search the web. Returns JSON for LLM tool registration.

        Designed for Pydantic AI::

            agent.tool_plain(researcher.web_search)
        """
        if not query:
            return json.dumps({"error": "Empty search query"})

        response = await self.search(query)
        return json.dumps(
            [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in response.results
            ],
            ensure_ascii=False,
        )

    async def web_fetch(self, url: str) -> str:
        """Fetch a URL and extract content. Returns string for LLM tool registration.

        Designed for Pydantic AI::

            agent.tool_plain(researcher.web_fetch)
        """
        response = await self.fetch(url, extract=True)
        return response.content

    async def deep_search(
        self,
        query: str,
        *,
        fan_out: int = 3,
        max_fetch: int = 2,
    ) -> str:
        """Search multiple angles and auto-fetch top results in one call.

        Fans out to ``fan_out`` search variants (deterministic query
        expansion), merges results with Reciprocal Rank Fusion, and
        auto-fetches the top ``max_fetch`` unique URLs. Returns a
        digest with search results and fetched content wrapped in
        configured source delimiters.

        ``fan_out=1`` collapses to a simple search + auto-fetch.

        Designed for Pydantic AI::

            agent.tool_plain(researcher.deep_search)
        """
        if not query:
            return "Error: Empty search query"

        variants = _make_search_variants(query, fan_out)

        search_tasks = [self.search(v, max_results=3) for v in variants]
        responses = await asyncio.gather(*search_tasks, return_exceptions=True)

        url_scores: dict[str, float] = {}
        url_to_result: dict[str, SearchResult] = {}
        for resp in responses:
            if isinstance(resp, Exception):
                continue
            for rank, r in enumerate(resp.results):
                if not r.url:
                    continue
                score = 1.0 / (60 + rank)
                url_scores[r.url] = url_scores.get(r.url, 0.0) + score
                if r.url not in url_to_result:
                    url_to_result[r.url] = r

        ranked = sorted(url_scores, key=lambda u: url_scores[u], reverse=True)

        parts: list[str] = [f"## Search: {query}\n"]
        for url in ranked[:6]:
            r = url_to_result[url]
            parts.append(f"- [{r.title}]({r.url})\n  {r.snippet}\n")

        if not ranked:
            parts.append("No results found.\n")
            return "\n".join(parts)

        fetch_urls = ranked[:max_fetch]
        fetch_tasks = [
            self.fetch(url, extract=True, max_length=10000)
            for url in fetch_urls
        ]
        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        total_chars = 0
        for url, result in zip(fetch_urls, fetch_results):
            if isinstance(result, Exception):
                parts.append(f"\n### Fetch failed: {url}\n{result}\n")
                continue
            if result.status_code >= 400:
                parts.append(f"\n### Fetch failed: {url} (HTTP {result.status_code})\n")
                continue
            content = result.content
            if total_chars + len(content) > 30000:
                content = content[:30000 - total_chars]
            if content:
                total_chars += len(content)
                title = url_to_result.get(url)
                label = title.title if title else url
                parts.append(f"\n### {label}\n{inject_untrusted(content, self._guard_tag)}\n")

        return "\n".join(parts)


def _make_search_variants(query: str, fan_out: int) -> list[str]:
    """Generate search query variants for fan-out.

    Deterministic, no LLM. Returns up to ``fan_out`` variants.
    """
    if fan_out <= 1:
        return [query]

    variants = [query]

    words = query.split()
    key_terms = [w for w in words if len(w) > 3 and w.isalpha()]
    if len(key_terms) >= 2:
        quoted = " ".join(f'"{t}"' for t in key_terms[:3])
        variants.append(quoted)
    else:
        variants.append(f'"{query}"')

    domain_keywords = {
        "c++": "site:stackoverflow.com",
        "coroutine": "site:stackoverflow.com",
        "proposal": "site:open-std.org",
        "standard": "site:open-std.org",
        "wg21": "site:open-std.org",
        "benchmark": "site:github.com",
        "performance": "site:github.com",
    }
    site_filter = ""
    for kw, site in domain_keywords.items():
        if kw in query.lower():
            site_filter = site
            break
    if site_filter:
        variants.append(f"{query} {site_filter}")
    else:
        variants.append(f"{query} C++ standard")

    return variants[:fan_out]
