#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Integration tests against real backends. Run manually, not in CI.

    uv run pytest packages/pipeline/tests/test_integration.py -v -m network
"""

from __future__ import annotations

import os

import pytest

from pipeline import WebResearcher

pytestmark = pytest.mark.network


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("BRAVE_API_KEY"),
    reason="BRAVE_API_KEY not set",
)
async def test_brave_real_search():
    async with WebResearcher() as researcher:
        response = await researcher.search("Python asyncio tutorial", max_results=3)
        assert response.status_code == 200
        assert len(response.results) > 0
        assert response.results[0].url


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("BRAVE_API_KEY"),
    reason="BRAVE_API_KEY not set",
)
async def test_brave_real_web_search_json():
    import json
    async with WebResearcher() as researcher:
        result = await researcher.web_search("Python asyncio tutorial")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) > 0


@pytest.mark.anyio
async def test_real_fetch():
    from pipeline.session import SearchBackend, SearchResponse

    class NoOpBackend(SearchBackend):
        name = "noop"
        async def search(self, query, max_results):
            return SearchResponse(status_code=200, results=[])

    async with WebResearcher(backend=NoOpBackend()) as researcher:
        response = await researcher.fetch("https://httpbin.org/html", extract=True)
        assert response.status_code == 200
        assert len(response.content) > 0
        assert "Error" not in response.content
