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

from cpp_mcp.backend import (
    DefinedTermRow,
    GrammarRuleRow,
    IndexTermRow,
    LibraryDeclRow,
    MechanismRow,
    ParagraphRow,
    SectionRow,
)
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
    is_deprecated: bool = False,
    is_synopsis: bool = False,
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
        is_deprecated=is_deprecated,
        is_synopsis=is_synopsis,
    )


@pytest.fixture
def backend(tmp_path):
    b = SqliteStandardBackend(tmp_path / "test.db")
    b.create_schema()
    # n5008 is the default because it has the higher tag number (most recently published)
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
    return create_server(backend, no_auth=True)


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


def test_create_server_requires_auth_or_no_auth(backend):
    """Server refuses to start without --keys-file or --no-auth."""
    with pytest.raises(ValueError, match="No API keys loaded"):
        create_server(backend)


def test_create_server_rejects_empty_keys_file(backend, tmp_path):
    """A keys file with only comments is treated as empty."""
    keys_file = tmp_path / "keys"
    keys_file.write_text("# No real keys\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No API keys loaded"):
        create_server(backend, keys_file=keys_file)


def test_create_server_no_auth_flag(backend):
    """--no-auth allows starting without a keys file."""
    mcp = create_server(backend, no_auth=True)
    assert mcp is not None


def test_create_server_with_valid_keys_file(backend, tmp_path):
    """A keys file with at least one key enables auth."""
    keys_file = tmp_path / "keys"
    keys_file.write_text("secret-key-123\n", encoding="utf-8")
    mcp = create_server(backend, keys_file=keys_file)
    assert mcp is not None


def test_auth_rejects_missing_token(backend, tmp_path):
    """Requests without Authorization header are rejected when auth is enabled."""
    keys_file = tmp_path / "keys"
    keys_file.write_text("valid-key\n", encoding="utf-8")
    mcp = create_server(backend, keys_file=keys_file)
    try:
        result = asyncio.run(mcp.call_tool("list_drafts", {}))
        text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
        assert "Unauthorized" in text or "error" in text.lower()
    except Exception as exc:
        assert "Unauthorized" in str(exc) or "error" in str(exc).lower()


def test_auth_rejects_invalid_token(backend, tmp_path):
    """Requests with a wrong bearer token are rejected."""
    keys_file = tmp_path / "keys"
    keys_file.write_text("valid-key\n", encoding="utf-8")
    mcp = create_server(backend, keys_file=keys_file)
    try:
        result = asyncio.run(mcp.call_tool("list_drafts", {}))
        text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
        assert "Unauthorized" in text or "error" in text.lower()
    except Exception as exc:
        assert "Unauthorized" in str(exc) or "error" in str(exc).lower()


# -----------------------------------------------------------------------
# Rich backend fixture and new tool tests
# -----------------------------------------------------------------------


@pytest.fixture
def rich_backend(tmp_path):
    b = SqliteStandardBackend(tmp_path / "rich.db")
    b.create_schema()

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
        _make_row("n5008", "basic.types", "Types", 1, "basic", "basic.tex",
                  r"\pnum Types", "Types are fundamental to C++"),
    ]
    b.upsert_draft("n5008", rows)

    b.upsert_xrefs("n5008", [
        ("basic.life", "basic.types"),
        ("basic.types", "basic.life"),
        ("basic.life", "basic"),
    ])
    b.upsert_index_terms("n5008", [
        IndexTermRow("n5008", "basic.life", "text", "lifetime"),
        IndexTermRow("n5008", "basic.life", "text", "object lifetime"),
        IndexTermRow("n5008", "basic.types", "library", "type"),
    ])
    b.upsert_mechanisms("n5008", [
        MechanismRow("n5008", "constexpr", "keyword", "basic.types"),
        MechanismRow("n5008", "std::move", "library", "basic.life"),
    ])
    b.upsert_grammar_rules("n5008", [
        GrammarRuleRow("n5008", "expression", "expr", "expression: assignment-expression"),
    ])
    b.upsert_defined_terms("n5008", [
        DefinedTermRow("n5008", "undefined behavior", "defns.undefined",
                       "behavior for which this document imposes no requirements"),
    ])
    b.upsert_library_declarations("n5008", [
        LibraryDeclRow("n5008", "vector.modifiers", "void push_back(const T& x)",
                       "Appends a copy of x.", effects="Appends x to the sequence."),
    ])
    b.upsert_paragraphs("n5008", [
        ParagraphRow("n5008", "basic.life", 1,
                     r"\pnum The lifetime begins", "The lifetime begins", "normative"),
        ParagraphRow("n5008", "basic.life", 2,
                     r"\pnum The lifetime ends", "The lifetime ends", "normative"),
    ])
    return b


@pytest.fixture
def rich_mcp(rich_backend):
    return create_server(rich_backend, no_auth=True)


def test_verify_mechanism_found(rich_mcp):
    result = json.loads(asyncio.run(
        rich_mcp.call_tool("verify_mechanism", {"name": "constexpr"})
    ).content[0].text)
    assert result["exists"] is True
    assert len(result["matches"]) == 1
    assert result["matches"][0]["name"] == "constexpr"
    assert result["matches"][0]["category"] == "keyword"


def test_verify_mechanism_not_found(rich_mcp):
    result = json.loads(asyncio.run(
        rich_mcp.call_tool("verify_mechanism", {"name": "nonexistent_thing"})
    ).content[0].text)
    assert result["exists"] is False
    assert result["matches"] == []


def test_search_index(rich_mcp):
    results = _call(rich_mcp, "search_index", term="lifetime")
    assert len(results) == 2
    labels = {r["stable_label"] for r in results}
    assert "basic.life" in labels


def test_lookup_declaration(rich_mcp):
    results = _call(rich_mcp, "lookup_declaration", pattern="push_back")
    assert len(results) == 1
    assert results[0]["stable_label"] == "vector.modifiers"
    assert results[0]["effects"] == "Appends x to the sequence."


def test_search_grammar_found(rich_mcp):
    result = _call(rich_mcp, "search_grammar", nonterminal="expression")
    assert "nonterminal" in result
    assert result["nonterminal"] == "expression"
    assert "raw_rule" in result


def test_search_grammar_not_found(rich_mcp):
    result = _call(rich_mcp, "search_grammar", nonterminal="nonexistent-nt")
    assert "error" in result


def test_get_cross_references(rich_mcp):
    result = _call(rich_mcp, "get_cross_references", stable_label="basic.life")
    assert "from" in result
    assert "to" in result
    assert "basic.types" in result["from"]
    assert "basic.types" in result["to"]


def test_lookup_sections_batch(rich_mcp):
    results = _call(rich_mcp, "lookup_sections",
                    stable_labels=["basic.life", "basic.types"])
    assert len(results) == 2
    labels = {r["stable_label"] for r in results}
    assert labels == {"basic.life", "basic.types"}


def test_lookup_definition_found(rich_mcp):
    results = _call(rich_mcp, "lookup_definition", term="undefined behavior")
    assert isinstance(results, list)
    assert len(results) >= 1
    assert results[0]["term"] == "undefined behavior"
    assert "no requirements" in results[0]["definition_text"]


def test_lookup_definition_not_found(rich_mcp):
    result = _call(rich_mcp, "lookup_definition", term="nonexistent term")
    assert "error" in result


def test_lookup_paragraph(rich_mcp):
    result = _call(rich_mcp, "lookup_paragraph",
                   stable_label="basic.life", paragraph=1)
    assert result["paragraph_number"] == 1
    assert result["cleaned_text"] == "The lifetime begins"
    assert result["normative_force"] == "normative"


def test_get_ancestors(rich_mcp):
    results = _call(rich_mcp, "get_ancestors", stable_label="basic.life.general")
    labels = [r["stable_label"] for r in results]
    assert labels == ["basic", "basic.life"]


def test_guide_query_stable_label(rich_mcp):
    result = _call(rich_mcp, "guide_query",
                   question="What does [basic.life] say?")
    assert result["recommended_tool"] == "lookup_section"
    assert result["parameters"]["stable_label"] == "basic.life"


def test_guide_query_existence(rich_mcp):
    result = _call(rich_mcp, "guide_query",
                   question="Does constexpr exist in the standard?")
    assert result["recommended_tool"] == "verify_mechanism"


def test_guide_query_definition(rich_mcp):
    result = _call(rich_mcp, "guide_query",
                   question="What does undefined behavior mean?")
    assert result["recommended_tool"] == "lookup_definition"


def test_guide_query_fallback(rich_mcp):
    result = _call(rich_mcp, "guide_query",
                   question="How do templates work?")
    assert result["recommended_tool"] == "semantic_search"


def test_version_shorthand_in_draft(rich_mcp):
    result = _call(rich_mcp, "lookup_section",
                   stable_label="basic.life", draft="C++26")
    assert result["draft_tag"] == "n5008"
    assert result["stable_label"] == "basic.life"


def test_list_drafts_includes_version_info(rich_backend):
    rich_backend.conn.execute(
        "UPDATE drafts SET standard_version = ?, version_note = ? "
        "WHERE draft_tag = ?",
        ("C++26", "working draft", "n5008"),
    )
    rich_backend.conn.commit()
    mcp = create_server(rich_backend, no_auth=True)
    drafts = _call(mcp, "list_drafts")
    n5008 = next(d for d in drafts if d["draft_tag"] == "n5008")
    assert n5008["standard_version"] == "C++26"
    assert n5008["version_note"] == "working draft"


def test_lookup_section_duplicate_flag(tmp_path):
    """lookup_section flags duplicate labels and points to lookup_all_sections_by_label."""
    b = SqliteStandardBackend(tmp_path / "dup.db")
    b.create_schema()
    b.upsert_draft("n3337", [
        _make_row("n3337", "gram.basic", "Basics grammar", 1, "basic", "basic.tex",
                  r"\pnum Per-chapter", "Per-chapter grammar"),
        _make_row("n3337", "gram.basic", "Basics grammar", 1, "grammar", "grammar.tex",
                  r"\pnum Appendix", "Appendix grammar"),
    ])
    mcp = create_server(b, no_auth=True)
    result = _call(mcp, "lookup_section", stable_label="gram.basic", draft="n3337")
    assert result["chapter_file"] == "grammar.tex"
    assert result["duplicate_label"] is True
    assert "lookup_all_sections" in result["duplicate_note"]


def test_lookup_section_no_duplicate_flag(mcp):
    """Normal unique labels have no duplicate_label field."""
    result = _call(mcp, "lookup_section", stable_label="basic.life")
    assert "duplicate_label" not in result


def test_lookup_all_sections_by_label(tmp_path):
    """lookup_all_sections_by_label returns all occurrences in document order."""
    b = SqliteStandardBackend(tmp_path / "dup.db")
    b.create_schema()
    b.upsert_draft("n3337", [
        _make_row("n3337", "gram.basic", "Basics grammar", 1, "basic", "basic.tex",
                  r"\pnum Per-chapter", "Per-chapter grammar"),
        _make_row("n3337", "gram.basic", "Basics grammar", 1, "grammar", "grammar.tex",
                  r"\pnum Appendix", "Appendix grammar"),
    ])
    mcp = create_server(b, no_auth=True)
    results = _call(mcp, "lookup_all_sections_by_label",
                    stable_label="gram.basic", draft="n3337")
    assert len(results) == 2
    assert results[0]["chapter_file"] == "basic.tex"
    assert results[1]["chapter_file"] == "grammar.tex"
