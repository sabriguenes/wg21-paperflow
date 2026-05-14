#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Unit tests for WebResearcher and core types."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from pipeline.session import (
    FetchResponse,
    SearchBackend,
    SearchResponse,
    SearchResult,
    WebResearcher,
)


class FakeBackend(SearchBackend):
    """Test backend that returns canned results or raises."""

    name = "fake"

    _DEFAULT_RESULTS = [
        SearchResult(title="Fake", url="https://fake.com", snippet="A result")
    ]

    def __init__(self, *, results=_DEFAULT_RESULTS, status_code=200, error=None):
        self._results = results
        self._status_code = status_code
        self._error = error
        self.call_count = 0

    async def search(self, query, max_results):
        self.call_count += 1
        if self._error:
            raise self._error
        return SearchResponse(
            status_code=self._status_code,
            results=self._results[:max_results],
        )


@pytest.mark.anyio
async def test_search_returns_search_response():
    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        response = await r.search("test query")
    assert isinstance(response, SearchResponse)
    assert response.status_code == 200
    assert len(response.results) == 1
    assert response.results[0].title == "Fake"
    assert backend.call_count == 1


@pytest.mark.anyio
async def test_search_non_200_logs_warning(caplog):
    backend = FakeBackend(status_code=429, results=[])
    async with WebResearcher(backend=backend) as r:
        response = await r.search("test")
    assert response.status_code == 429
    assert response.results == []
    assert "429" in caplog.text


@pytest.mark.anyio
async def test_fetch_returns_fetch_response():
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body><p>Hello world</p></body></html>"

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "get", return_value=mock_resp):
            response = await r.fetch("https://example.com", extract=False)
    assert isinstance(response, FetchResponse)
    assert response.status_code == 200
    assert "Hello world" in response.content


@pytest.mark.anyio
async def test_fetch_empty_url():
    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        response = await r.fetch("")
    assert response.status_code == 0
    assert response.content == "Error: Empty URL"


@pytest.mark.anyio
async def test_web_search_returns_json():
    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        result = await r.web_search("test")
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert parsed[0]["title"] == "Fake"


@pytest.mark.anyio
async def test_web_search_empty_query():
    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        result = await r.web_search("")
    parsed = json.loads(result)
    assert "error" in parsed


@pytest.mark.anyio
async def test_closed_researcher_raises():
    backend = FakeBackend()
    r = WebResearcher(backend=backend)
    await r.close()
    with pytest.raises(RuntimeError, match="closed"):
        await r.search("test")
    with pytest.raises(RuntimeError, match="closed"):
        await r.fetch("https://example.com")


@pytest.mark.anyio
async def test_close_is_idempotent():
    backend = FakeBackend()
    r = WebResearcher(backend=backend)
    await r.close()
    await r.close()


@pytest.mark.anyio
async def test_owns_backend_closes_it():
    """When no backend is passed, the researcher owns and closes it."""
    with patch("pipeline.backends.get_default_backend") as mock_get:
        mock_backend = AsyncMock(spec=SearchBackend)
        mock_backend.name = "mock"
        mock_get.return_value = mock_backend
        r = WebResearcher()
        assert r._owns_backend is True
        await r.close()
        mock_backend.close.assert_awaited_once()


@pytest.mark.anyio
async def test_borrowed_backend_not_closed():
    """When a backend is passed in, the researcher does not close it."""
    backend = FakeBackend()
    r = WebResearcher(backend=backend)
    assert r._owns_backend is False
    await r.close()


def test_search_result_frozen():
    r = SearchResult(title="T", url="U", snippet="S")
    with pytest.raises(Exception):
        r.title = "X"


def test_search_response_frozen():
    r = SearchResponse(status_code=200, results=[])
    with pytest.raises(Exception):
        r.status_code = 500


def test_fetch_response_frozen():
    r = FetchResponse(status_code=200, content="hello")
    with pytest.raises(Exception):
        r.content = "bye"


def test_brave_missing_key_raises():
    from pipeline.backends.brave import BraveBackend
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="BRAVE_API_KEY"):
            BraveBackend()
