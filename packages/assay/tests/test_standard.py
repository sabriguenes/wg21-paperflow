#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for assay.standard (StandardClient and from_env)."""

from __future__ import annotations

import pytest

from assay.standard import (
    StandardClient,
    from_env,
)


def test_from_env_no_cpp_mcp():
    result = from_env(no_cpp_mcp=True)
    assert result is None


def test_from_env_missing_key_raises(monkeypatch):
    monkeypatch.delenv("CPP_MCP_API_KEY", raising=False)
    with pytest.raises(ValueError, match="CPP_MCP_API_KEY is not set"):
        from_env()


def test_from_env_empty_key_raises(monkeypatch):
    monkeypatch.setenv("CPP_MCP_API_KEY", "")
    with pytest.raises(ValueError, match="CPP_MCP_API_KEY is not set"):
        from_env()


def test_from_env_with_key_returns_client(monkeypatch):
    monkeypatch.setenv("CPP_MCP_API_KEY", "test-key-123")
    client = from_env()
    assert isinstance(client, StandardClient)
    assert client._url == "https://mcpserver1.cpp.al/mcp"


def test_from_env_custom_url_via_env(monkeypatch):
    monkeypatch.setenv("CPP_MCP_API_KEY", "test-key-123")
    monkeypatch.setenv("CPP_MCP_URL", "http://localhost:9999/mcp")
    client = from_env()
    assert client._url == "http://localhost:9999/mcp"


def test_from_env_custom_url_via_arg(monkeypatch):
    monkeypatch.setenv("CPP_MCP_API_KEY", "test-key-123")
    client = from_env(url="http://custom:1234/mcp")
    assert client._url == "http://custom:1234/mcp"


def test_from_env_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("CPP_MCP_API_KEY", "test-key-123")
    monkeypatch.setenv("CPP_MCP_URL", "http://env-url/mcp")
    client = from_env(url="http://arg-url/mcp")
    assert client._url == "http://arg-url/mcp"


def test_from_env_no_cpp_mcp_skips_key_check(monkeypatch):
    monkeypatch.delenv("CPP_MCP_API_KEY", raising=False)
    result = from_env(no_cpp_mcp=True)
    assert result is None


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
