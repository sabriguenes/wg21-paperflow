#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for assay.standard (StandardClient and from_service_config)."""

from __future__ import annotations

import pytest

from assay.standard import (
    StandardClient,
    from_service_config,
)


def test_from_service_config_returns_client():
    client = from_service_config("https://example.com/mcp", "test-key")
    assert isinstance(client, StandardClient)
    assert client._url == "https://example.com/mcp"


def test_from_service_config_empty_url_raises():
    with pytest.raises(ValueError, match="base_url is empty"):
        from_service_config("", "test-key")


def test_from_service_config_empty_key_raises():
    with pytest.raises(ValueError, match="API key is empty"):
        from_service_config("https://example.com/mcp", "")


def test_extract_stable_labels():
    text = "See [basic.life] and [class.dtor] for details. Also [expr.prim.lambda]."
    labels = StandardClient.extract_stable_labels(text)
    assert "basic.life" in labels
    assert "class.dtor" in labels
    assert "expr.prim.lambda" in labels


def test_extract_stable_labels_no_false_positives():
    text = "Use [10] and [note: something] but not [A]."
    labels = StandardClient.extract_stable_labels(text)
    assert labels == []


def test_extract_paragraph_refs():
    text = "As specified in [basic.life] paragraph 6 and [class.dtor] p3."
    refs = StandardClient.extract_paragraph_refs(text)
    assert ("basic.life", 6) in refs
    assert ("class.dtor", 3) in refs


def test_extract_mechanism_names():
    text = "Uses `await_transform` and `std::move` but not plain text."
    names = StandardClient.extract_mechanism_names(text)
    assert "await_transform" in names
    assert "std::move" in names


def test_extract_mechanism_names_dedup():
    text = "`vector` and `vector` again."
    names = StandardClient.extract_mechanism_names(text)
    assert names.count("vector") == 1


def test_prefetch_mechanism_verification_nonexistent():
    """Verify that a nonexistent mechanism returns NOT FOUND, not an infinite loop."""
    import asyncio
    from unittest.mock import MagicMock

    client = StandardClient.__new__(StandardClient)
    client._client = MagicMock()
    client._call_count = 0

    async def mock_call_tool(tool, args):
        result = MagicMock()
        result.content = [MagicMock()]
        result.content[0].text = '{"exists": false, "name": "' + args["name"] + '", "matches": []}'
        return result

    client._client.call_tool = mock_call_tool

    text = "Uses `totally_fake_mechanism` and `another_nonexistent_thing`."
    block = asyncio.run(client.prefetch_mechanism_verification(text))
    assert "NOT FOUND" in block
    assert "totally_fake_mechanism" in block
    assert "another_nonexistent_thing" in block
    assert "EXISTS" not in block


def test_prefetch_mechanism_verification_mixed():
    """Mixed real and fake mechanisms: real ones show EXISTS, fake show NOT FOUND."""
    import asyncio
    from unittest.mock import MagicMock

    client = StandardClient.__new__(StandardClient)
    client._client = MagicMock()
    client._call_count = 0

    async def mock_call_tool(tool, args):
        name = args["name"]
        result = MagicMock()
        result.content = [MagicMock()]
        if name == "vector":
            result.content[0].text = '{"exists": true, "name": "vector", "matches": [{"name": "vector", "category": "library", "stable_label": "vector.overview"}]}'
        else:
            result.content[0].text = '{"exists": false, "name": "' + name + '", "matches": []}'
        return result

    client._client.call_tool = mock_call_tool

    text = "`vector` and `nonexistent_widget`."
    block = asyncio.run(client.prefetch_mechanism_verification(text))
    assert "vector" in block
    assert "EXISTS" in block
    assert "NOT FOUND" in block
    assert "nonexistent_widget" in block


def _client_returning(text_by_tool):
    """Build a StandardClient whose MCP calls return canned JSON text per tool."""
    import json
    from unittest.mock import MagicMock

    client = StandardClient.__new__(StandardClient)
    client._client = MagicMock()
    client._call_count = 0

    async def mock_call_tool(tool, args):
        result = MagicMock()
        result.content = [MagicMock()]
        payload = text_by_tool[tool]
        result.content[0].text = payload if isinstance(payload, str) else json.dumps(payload)
        return result

    client._client.call_tool = mock_call_tool
    return client


def test_lookup_sections_error_envelope_normalizes_to_empty():
    """A {"error": ...} envelope (no draft ingested) must not leak as a dict."""
    import asyncio

    client = _client_returning(
        {"lookup_sections": {"error": "No drafts ingested. Run 'cpp-mcp ingest' first."}}
    )
    result = asyncio.run(client.lookup_sections(["basic.life"]))
    assert result == []


def test_lookup_sections_filters_non_dict_items():
    """Stray non-dict items in the array are dropped, keeping the list[dict] contract."""
    import asyncio

    client = _client_returning(
        {"lookup_sections": [{"stable_label": "basic.life", "cleaned_text": "x"}, "garbage"]}
    )
    result = asyncio.run(client.lookup_sections(["basic.life"]))
    assert result == [{"stable_label": "basic.life", "cleaned_text": "x"}]


def test_prefetch_standard_context_survives_error_envelope():
    """Regression: server returning {"error": ...} for lookup_sections must not crash.

    Previously iterated the dict's keys and hit 'str' object has no attribute 'get'.
    """
    import asyncio

    client = _client_returning(
        {"lookup_sections": {"error": "No drafts ingested. Run 'cpp-mcp ingest' first."}}
    )
    block = asyncio.run(client.prefetch_standard_context("See [basic.life] for details."))
    assert block == ""
