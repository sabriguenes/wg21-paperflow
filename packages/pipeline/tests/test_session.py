#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Unit tests for WebResearcher and core types."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pipeline.errors import BackendConfigError
from pipeline.session import (
    FetchResponse,
    SearchBackend,
    SearchResponse,
    SearchResult,
    WebResearcher,
    _MAX_FETCH_BYTES,
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


def _stream_mock(
    *,
    status_code=200,
    headers=None,
    chunks=(),
    charset_encoding=None,
    raise_on_iter=False,
):
    """Mock for ``client.stream(...)`` returning the given byte chunks.

    Returns a MagicMock suitable for ``patch.object(client, "stream", ...)``.
    Calling it (sync) returns an async context manager whose ``__aenter__``
    resolves to a Response-shaped AsyncMock. The Response has
    ``aiter_bytes`` yielding the given chunks one at a time, so cap-related
    tests can verify that the body never lands in memory all at once.

    The async iterator is annotated with a ``called`` flag (set to True the
    first time it's iterated) so tests can assert body consumption was
    skipped on non-200 responses.
    """
    resp = AsyncMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.charset_encoding = charset_encoding

    iter_state = {"called": False}

    async def _iter():
        iter_state["called"] = True
        if raise_on_iter:
            raise httpx.HTTPError("boom")
        for c in chunks:
            yield c

    resp.aiter_bytes = _iter
    resp._iter_state = iter_state  # exposed for assertions

    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = resp
    return MagicMock(return_value=stream_cm), resp


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
    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "text/html"},
        chunks=[b"<html><body><p>Hello world</p></body></html>"],
        charset_encoding="utf-8",
    )

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "stream", stream):
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
        with pytest.raises(BackendConfigError, match="BRAVE_API_KEY"):
            BraveBackend()


# ---------------------------------------------------------------------------
# Binary extractor registry
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_routes_to_registered_extractor():
    seen = {}

    def stub_extractor(content: bytes, max_length: int) -> str | None:
        seen["content"] = content
        seen["max_length"] = max_length
        return "extracted text"

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=[b"%PDF-fake"],
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/paper.pdf")
    assert response.status_code == 200
    assert response.content == "extracted text"
    assert seen["content"] == b"%PDF-fake"
    assert seen["max_length"] > 0


@pytest.mark.anyio
async def test_fetch_extractor_none_returns_existing_error():
    def stub_extractor(content: bytes, max_length: int) -> str | None:
        return None

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=[b"%PDF-bad"],
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/paper.pdf")
    assert response.status_code == 200
    assert response.content == "Error: Could not extract content from page"


@pytest.mark.anyio
async def test_fetch_unregistered_content_type_falls_through():
    # PDF bytes, no extractor registered. Bytes flow through trafilatura
    # which returns None on non-HTML, producing the existing error string.
    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=[b"%PDF-1.4 raw bytes"],
    )

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/paper.pdf")
    assert response.status_code == 200
    assert response.content == "Error: Could not extract content from page"


@pytest.mark.anyio
async def test_fetch_content_type_with_charset_param():
    # Header "application/pdf; charset=binary" still matches the
    # registry key "application/pdf".
    def stub_extractor(content: bytes, max_length: int) -> str | None:
        return "extracted"

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf; charset=binary"},
        chunks=[b"%PDF"],
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/paper.pdf")
    assert response.content == "extracted"


@pytest.mark.anyio
async def test_fetch_truncates_extractor_output_at_max_length():
    def stub_extractor(content: bytes, max_length: int) -> str | None:
        return "A" * 5000

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=[b"%PDF"],
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch(
                "https://example.com/paper.pdf", max_length=100,
            )
    assert response.content.endswith("[Content truncated]")
    assert len(response.content) <= 100 + len("\n\n[Content truncated]")


@pytest.mark.anyio
async def test_fetch_extract_false_on_binary_returns_empty():
    calls = []

    def stub_extractor(content: bytes, max_length: int) -> str | None:
        calls.append(1)
        return "should-not-be-called"

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=[b"%PDF"],
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch(
                "https://example.com/paper.pdf", extract=False,
            )
    assert response.content == ""
    assert calls == []


@pytest.mark.anyio
async def test_fetch_html_path_unchanged():
    # Regression: HTML response, no extractors, extract=True still runs
    # trafilatura and returns extracted content. Build a page substantial
    # enough that trafilatura's minimum-length heuristics don't drop it.
    paragraphs = "".join(
        f"<p>The quick brown fox jumps over the lazy dog. "
        f"Paragraph number {i} of an article about web tooling and "
        f"content extraction. This sentence exists to give the "
        f"extractor a body of text to work with.</p>"
        for i in range(20)
    )
    html = (
        f"<!DOCTYPE html><html><head><title>Test Article</title></head>"
        f"<body><article><h1>Test Article Heading</h1>{paragraphs}"
        f"</article></body></html>"
    )
    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "text/html"},
        chunks=[html.encode("utf-8")],
        charset_encoding="utf-8",
    )

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/article.html")
    assert response.status_code == 200
    assert "quick brown fox" in response.content


# ---------------------------------------------------------------------------
# Streaming / size cap
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fetch_aborts_response_exceeding_cap():
    chunk = b"x" * (1024 * 1024)  # 1 MB chunks
    # _MAX_FETCH_BYTES // 1MB + 1 chunks => first chunk past the cap fires.
    chunks = [chunk] * (_MAX_FETCH_BYTES // len(chunk) + 1)

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "text/html"},
        chunks=chunks,
    )

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/big")
    assert response.status_code == 200
    assert response.content.startswith("Error: Response exceeded")
    assert str(_MAX_FETCH_BYTES) in response.content


@pytest.mark.anyio
async def test_fetch_at_cap_boundary_succeeds():
    # Total bytes equal _MAX_FETCH_BYTES exactly: should NOT trip the cap
    # (the check is strict ``>``). Route through a stub extractor so we
    # don't pay the cost of running trafilatura on 25 MB of noise; we
    # only care that the extractor was reached, not what came out.
    seen = {}

    def stub_extractor(content: bytes, max_length: int) -> str | None:
        seen["size"] = len(content)
        return "ok"

    chunk = b"x" * (1024 * 1024)
    n = _MAX_FETCH_BYTES // len(chunk)
    chunks = [chunk] * n  # n * 1 MB == _MAX_FETCH_BYTES exactly
    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=chunks,
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/atcap")
    assert seen.get("size") == _MAX_FETCH_BYTES
    assert response.content == "ok"


@pytest.mark.anyio
async def test_fetch_oversized_binary_does_not_call_extractor():
    calls = []

    def stub_extractor(content, max_length):
        calls.append(len(content))
        return "should-not-be-called"

    chunk = b"x" * (1024 * 1024)
    chunks = [chunk] * (_MAX_FETCH_BYTES // len(chunk) + 1)

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=chunks,
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/big.pdf")
    assert response.content.startswith("Error: Response exceeded")
    assert calls == []


@pytest.mark.anyio
async def test_fetch_streams_multiple_chunks_into_body():
    seen = {}

    def stub_extractor(content: bytes, max_length: int) -> str | None:
        seen["content"] = content
        return "ok"

    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=[b"a", b"b", b"c"],
    )

    backend = FakeBackend()
    async with WebResearcher(
        backend=backend,
        binary_extractors={"application/pdf": stub_extractor},
    ) as r:
        with patch.object(r._client, "stream", stream):
            await r.fetch("https://example.com/p.pdf")
    assert seen["content"] == b"abc"


@pytest.mark.anyio
async def test_fetch_streaming_preserves_html_charset():
    body = "<html><body><p>café</p></body></html>".encode("utf-8")
    stream, _resp = _stream_mock(
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        chunks=[body],
        charset_encoding="utf-8",
    )

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch(
                "https://example.com/cafe", extract=False,
            )
    assert "café" in response.content


@pytest.mark.anyio
async def test_fetch_status_code_non_200_skips_body_consumption():
    stream, resp = _stream_mock(
        status_code=500,
        headers={"content-type": "text/html"},
        chunks=[b"ignored"],
    )

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "stream", stream):
            response = await r.fetch("https://example.com/err")
    assert response.status_code == 500
    assert response.content.startswith("Error: HTTP 500")
    assert resp._iter_state["called"] is False


@pytest.mark.anyio
async def test_fetch_http_error_during_stream_returns_error_response():
    failing_stream = MagicMock(side_effect=httpx.HTTPError("boom"))

    backend = FakeBackend()
    async with WebResearcher(backend=backend) as r:
        with patch.object(r._client, "stream", failing_stream):
            response = await r.fetch("https://example.com/fail")
    assert response.status_code == 0
    assert response.content.startswith("Error: Failed to fetch URL")
