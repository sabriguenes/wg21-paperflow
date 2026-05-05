#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""WebResearcher: web search and content fetch for LLM pipelines."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
import trafilatura

logger = logging.getLogger(__name__)

_DEFAULT_FETCH_TIMEOUT = 15
_DEFAULT_MAX_FETCH_LENGTH = 8000
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_trafilatura_config = trafilatura.settings.use_config()
_trafilatura_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")


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


class WebResearcher:
    """Web search and fetch for LLM pipelines.

    Create one per pipeline run. Pass a shared ``SearchBackend`` for
    connection reuse across parallel runs, or omit to auto-create one.

    Example::

        async with WebResearcher() as researcher:
            results = await researcher.search("coroutine executor C++")
            page = await researcher.fetch("https://example.com")
    """

    def __init__(self, *, backend: SearchBackend | None = None) -> None:
        from web_tools.backends import get_default_backend

        self._backend = backend or get_default_backend()
        self._owns_backend = backend is None
        self._client = httpx.AsyncClient(timeout=60.0)
        self._closed = False

    async def __aenter__(self) -> WebResearcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close HTTP clients. Idempotent."""
        if not self._closed:
            await self._client.aclose()
            if self._owns_backend:
                await self._backend.close()
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
        """Fetch a URL and optionally extract article content."""
        if self._closed:
            raise RuntimeError("Researcher is closed.")
        if not url:
            return FetchResponse(status_code=0, content="Error: Empty URL")

        try:
            resp = await self._client.get(
                url,
                headers={"User-Agent": _DEFAULT_USER_AGENT},
                timeout=_DEFAULT_FETCH_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            logger.warning("Fetch failed for %r: %s", url, exc)
            return FetchResponse(
                status_code=0,
                content=f"Error: Failed to fetch URL: {exc}",
            )

        if resp.status_code != 200:
            logger.warning("Fetch got HTTP %d for %r", resp.status_code, url)
            return FetchResponse(
                status_code=resp.status_code,
                content=f"Error: HTTP {resp.status_code} for {url}",
            )

        html = resp.text
        if not html:
            return FetchResponse(
                status_code=resp.status_code,
                content="Error: No content retrieved from URL",
            )

        if not extract:
            return FetchResponse(status_code=resp.status_code, content=html)

        text = trafilatura.extract(
            html, output_format="markdown", config=_trafilatura_config,
        )
        if not text:
            text = trafilatura.extract(
                html, output_format="txt", config=_trafilatura_config,
            )
        if not text:
            return FetchResponse(
                status_code=resp.status_code,
                content="Error: Could not extract content from page",
            )

        if len(text) > max_length:
            text = text[:max_length] + "\n\n[Content truncated]"

        return FetchResponse(status_code=resp.status_code, content=text)

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
