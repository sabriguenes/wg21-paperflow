#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the FastMCP server tools and auth."""

from __future__ import annotations

import asyncio
import json

import pytest

from cpp_mcp.backend import SectionRow
from cpp_mcp.server import _load_keys, create_server
from cpp_mcp.sqlite_backend import SqliteStandardBackend


def _make_row(
    draft_tag: str = "n5008",
    stable_label: str = "basic.life",
    title: str = "Object lifetime",
    depth: int = 1,
    parent_label: str | None = "basic",
    chapter_file: str = "basic.tex",
    raw_latex: str = "\\pnum The lifetime...",
    cleaned_text: str = "The lifetime of an object",
    paragraph_count: int = 1,
) -> SectionRow:
    return SectionRow(
        draft_tag=draft_tag,
        stable_label=stable_label,
        section_number=None,
        title=title,
        depth=depth,
        parent_label=parent_label,
        chapter_file=chapter_file,
        raw_latex=raw_latex,
        cleaned_text=cleaned_text,
        paragraph_count=paragraph_count,
    )


@pytest.fixture
def backend(tmp_path):
    b = SqliteStandardBackend(tmp_path / "test.db")
    b.create_schema()
    # n4950 first so n5008 becomes the default (most recently ingested)
    b.upsert_draft("n4950", [
        _make_row("n4950", "basic.life", "Object lifetime", 1, "basic", "basic.tex",
                  r"\pnum Old", "Old lifetime text from C++23"),
    ])
    rows = [
        _make_row("n5008", "basic", "Basic concepts", 0, None, "basic.tex",
                  r"\pnum Intro", "Intro to basic concepts"),
        _make_row("n5008", "basic.life", "Object lifetime", 1, "basic", "basic.tex",
                  r"\pnum Lifetime", "The lifetime of an object begins"),
        _make_row("n5008", "basic.life.general", "General", 2, "basic.life", "basic.tex",
                  r"\pnum General", "General rules about lifetime"),
    ]
    b.upsert_draft("n5008", rows)
    return b


@pytest.fixture
def mcp(backend):
    return create_server(backend)


def _call(mcp, name: str, **kwargs) -> object:
    """Synchronously call an MCP tool and parse the JSON result."""
    result = asyncio.run(mcp.call_tool(name, kwargs))
    content = result.content
    if isinstance(content, list):
        text = content[0].text if hasattr(content[0], "text") else str(content[0])
    elif hasattr(content, "text"):
        text = content.text
    else:
        text = str(content)
    return json.loads(text)


def test_lookup_section_found(mcp):
    result = _call(mcp, "lookup_section", stable_label="basic.life")
    assert result["stable_label"] == "basic.life"
    assert result["title"] == "Object lifetime"
    assert result["draft_tag"] == "n5008"


def test_lookup_section_with_draft(mcp):
    result = _call(mcp, "lookup_section", stable_label="basic.life", draft="n4950")
    assert result["draft_tag"] == "n4950"
    assert "Old" in result["cleaned_text"]


def test_lookup_section_not_found(mcp):
    result = _call(mcp, "lookup_section", stable_label="nonexistent")
    assert "error" in result


def test_search_standard(mcp):
    results = _call(mcp, "search_standard", query="lifetime")
    assert len(results) > 0
    labels = [r["stable_label"] for r in results]
    assert "basic.life" in labels


def test_search_standard_with_draft(mcp):
    results = _call(mcp, "search_standard", query="lifetime", draft="n4950")
    assert all(r["draft_tag"] == "n4950" for r in results)


def test_list_drafts(mcp):
    drafts = _call(mcp, "list_drafts")
    tags = {d["draft_tag"] for d in drafts}
    assert "n5008" in tags
    assert "n4950" in tags


def test_diff_section_both_exist(mcp):
    result = _call(mcp, "diff_section",
                   stable_label="basic.life", from_draft="n4950", to_draft="n5008")
    assert result["from_section"] is not None
    assert result["to_section"] is not None
    assert result["from_section"]["draft_tag"] == "n4950"
    assert result["to_section"]["draft_tag"] == "n5008"


def test_diff_section_one_missing(mcp):
    result = _call(mcp, "diff_section",
                   stable_label="basic", from_draft="n4950", to_draft="n5008")
    assert result["from_section"] is None
    assert result["to_section"] is not None


def test_list_chapters(mcp):
    chapters = _call(mcp, "list_chapters")
    assert len(chapters) == 1
    assert chapters[0]["stable_label"] == "basic"


def test_get_section_with_children(mcp):
    results = _call(mcp, "get_section_with_children", stable_label="basic.life")
    labels = {r["stable_label"] for r in results}
    assert "basic.life" in labels
    assert "basic.life.general" in labels


# -----------------------------------------------------------------------
# Auth / keys file tests
# -----------------------------------------------------------------------


def test_load_keys_with_comments(tmp_path):
    keys_file = tmp_path / "keys"
    keys_file.write_text(
        "# Admin key\nabc123\n\n# Pipeline key\ndef456\n# disabled\n",
        encoding="utf-8",
    )
    keys = _load_keys(keys_file)
    assert keys == {"abc123", "def456"}


def test_load_keys_empty_file(tmp_path):
    keys_file = tmp_path / "keys"
    keys_file.write_text("# No keys\n\n", encoding="utf-8")
    keys = _load_keys(keys_file)
    assert keys == set()


def test_load_keys_missing_file():
    keys = _load_keys("/nonexistent/path")
    assert keys == set()


def test_load_keys_none():
    keys = _load_keys(None)
    assert keys == set()
