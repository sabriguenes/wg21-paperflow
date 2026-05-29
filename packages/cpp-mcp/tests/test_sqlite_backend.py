#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for SqliteStandardBackend."""

from __future__ import annotations

import sqlite3

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
    section_number: str | None = None,
    is_deprecated: bool = False,
    is_synopsis: bool = False,
) -> SectionRow:
    return SectionRow(
        draft_tag=draft_tag,
        stable_label=stable_label,
        section_number=section_number,
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
    db_path = tmp_path / "test.db"
    b = SqliteStandardBackend(db_path)
    b.create_schema()
    return b


def _seed_basic(backend: SqliteStandardBackend, tag: str = "n5008"):
    rows = [
        _make_row(tag, "basic", "Basic concepts", 0, None, "basic.tex",
                  r"\pnum Intro", "Intro to basic concepts"),
        _make_row(tag, "basic.life", "Object lifetime", 1, "basic", "basic.tex",
                  r"\pnum Lifetime", "The lifetime of an object"),
        _make_row(tag, "basic.life.general", "General", 2, "basic.life", "basic.tex",
                  r"\pnum General", "General rules about lifetime"),
        _make_row(tag, "basic.types", "Types", 1, "basic", "basic.tex",
                  r"\pnum Types", "Types are fundamental to C++"),
    ]
    backend.upsert_draft(tag, rows)


def test_create_schema(backend):
    tables = backend.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in tables}
    assert "standard_sections" in names
    assert "drafts" in names


def test_upsert_and_lookup(backend):
    _seed_basic(backend)
    row = backend.lookup_section("basic.life")
    assert row is not None
    assert row.title == "Object lifetime"
    assert row.draft_tag == "n5008"


def test_lookup_missing(backend):
    _seed_basic(backend)
    assert backend.lookup_section("nonexistent") is None


def test_unique_constraint(backend):
    _seed_basic(backend)
    with pytest.raises(sqlite3.IntegrityError):
        backend.conn.execute(
            "INSERT INTO standard_sections (draft_tag, stable_label, title, depth, chapter_file, raw_latex, cleaned_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("n5008", "basic.life", "Duplicate", 1, "basic.tex", "", ""),
        )


def test_fts_search(backend):
    _seed_basic(backend)
    results = backend.search("lifetime")
    assert len(results) > 0
    labels = [r.stable_label for r in results]
    assert "basic.life" in labels


def test_fts_search_filters_by_draft(backend):
    _seed_basic(backend, "n5008")
    _seed_basic(backend, "n4950")
    results = backend.search("lifetime", draft_tag="n5008")
    assert all(r.draft_tag == "n5008" for r in results)


def test_get_section_with_children(backend):
    _seed_basic(backend)
    sections = backend.get_section_with_children("basic.life")
    labels = {s.stable_label for s in sections}
    assert "basic.life" in labels
    assert "basic.life.general" in labels
    assert "basic.types" not in labels


def test_get_section_with_children_root(backend):
    _seed_basic(backend)
    sections = backend.get_section_with_children("basic")
    labels = {s.stable_label for s in sections}
    assert "basic" in labels
    assert "basic.life" in labels
    assert "basic.life.general" in labels
    assert "basic.types" in labels


def test_list_chapters(backend):
    _seed_basic(backend)
    chapters = backend.list_chapters()
    assert len(chapters) == 1
    assert chapters[0].stable_label == "basic"
    assert chapters[0].depth == 0


def test_list_sections_by_chapter(backend):
    _seed_basic(backend)
    sections = backend.list_sections(chapter="basic.tex")
    assert len(sections) == 4


def test_list_sections_by_depth(backend):
    _seed_basic(backend)
    sections = backend.list_sections(depth=1)
    assert all(s.depth == 1 for s in sections)
    labels = {s.stable_label for s in sections}
    assert "basic.life" in labels
    assert "basic.types" in labels


def test_multi_version_independent(backend):
    _seed_basic(backend, "n5008")
    backend.upsert_draft("n4950", [
        _make_row("n4950", "basic", "Basic concepts", 0, None, "basic.tex",
                  r"\pnum Old intro", "Old intro text"),
    ])
    assert backend.lookup_section("basic", "n5008") is not None
    assert backend.lookup_section("basic", "n4950") is not None
    assert backend.lookup_section("basic.life", "n5008") is not None
    assert backend.lookup_section("basic.life", "n4950") is None


def test_reingest_replaces_rows(backend):
    _seed_basic(backend, "n5008")
    assert backend.lookup_section("basic.life", "n5008") is not None

    backend.upsert_draft("n5008", [
        _make_row("n5008", "basic", "Basic concepts revised", 0, None, "basic.tex",
                  r"\pnum Revised", "Revised text"),
    ])
    assert backend.lookup_section("basic", "n5008").title == "Basic concepts revised"
    assert backend.lookup_section("basic.life", "n5008") is None


def test_reingest_preserves_other_drafts(backend):
    _seed_basic(backend, "n5008")
    _seed_basic(backend, "n4950")
    backend.upsert_draft("n5008", [
        _make_row("n5008", "basic", "Revised", 0, None, "basic.tex", "", ""),
    ])
    assert backend.lookup_section("basic.life", "n4950") is not None


def test_list_drafts(backend):
    _seed_basic(backend, "n5008")
    _seed_basic(backend, "n4950")
    drafts = backend.list_drafts()
    tags = {d.draft_tag for d in drafts}
    assert "n5008" in tags
    assert "n4950" in tags
    assert all(d.section_count > 0 for d in drafts)


def test_drafts_metadata(backend):
    _seed_basic(backend)
    drafts = backend.list_drafts()
    assert len(drafts) == 1
    assert drafts[0].draft_tag == "n5008"
    assert drafts[0].section_count == 4
    assert drafts[0].ingested_at is not None


def test_diff_section_both_exist(backend):
    _seed_basic(backend, "n5008")
    backend.upsert_draft("n4950", [
        _make_row("n4950", "basic.life", "Object lifetime", 1, "basic", "basic.tex",
                  r"\pnum Old lifetime", "Old lifetime text"),
    ])
    left, right = backend.diff_section("basic.life", "n4950", "n5008")
    assert left is not None
    assert right is not None
    assert left.draft_tag == "n4950"
    assert right.draft_tag == "n5008"


def test_diff_section_one_missing(backend):
    _seed_basic(backend, "n5008")
    left, right = backend.diff_section("basic.life", "n4950", "n5008")
    assert left is None
    assert right is not None


def test_diff_section_both_missing(backend):
    _seed_basic(backend, "n5008")
    left, right = backend.diff_section("nonexistent", "n4950", "n5008")
    assert left is None
    assert right is None


def test_default_draft_tag(backend):
    assert backend.default_draft_tag() is None
    _seed_basic(backend, "n5008")
    assert backend.default_draft_tag() == "n5008"


def test_close_is_idempotent(tmp_path):
    b = SqliteStandardBackend(tmp_path / "test.db")
    b.create_schema()
    b.close()
    b.close()


def test_context_manager(tmp_path):
    with SqliteStandardBackend(tmp_path / "test.db") as b:
        b.create_schema()
        _seed_basic(b)
        assert b.lookup_section("basic") is not None


def test_upsert_xrefs_and_query(backend):
    _seed_basic(backend)
    backend.upsert_xrefs("n5008", [
        ("basic.life", "basic.types"),
        ("basic.life", "basic"),
        ("basic.types", "basic.life"),
    ])
    refs_from = backend.get_references_from("basic.life")
    assert set(refs_from) == {"basic.types", "basic"}

    refs_to = backend.get_references_to("basic.life")
    assert refs_to == ["basic.types"]


def test_upsert_index_terms_and_search(backend):
    _seed_basic(backend)
    backend.upsert_index_terms("n5008", [
        IndexTermRow("n5008", "basic.life", "text", "lifetime"),
        IndexTermRow("n5008", "basic.life", "text", "object lifetime"),
        IndexTermRow("n5008", "basic.types", "library", "type"),
    ])
    results = backend.search_index("lifetime")
    assert len(results) == 2
    assert all(r.term.endswith("lifetime") for r in results)

    results_cat = backend.search_index("type", category="library")
    assert len(results_cat) == 1
    assert results_cat[0].stable_label == "basic.types"

    results_no_match = backend.search_index("nonexistent")
    assert results_no_match == []


def test_upsert_mechanisms_and_verify(backend):
    _seed_basic(backend)
    backend.upsert_mechanisms("n5008", [
        MechanismRow("n5008", "constexpr", "keyword", "basic.types"),
        MechanismRow("n5008", "std::move", "library", "basic.life"),
    ])
    found = backend.verify_mechanism("constexpr")
    assert len(found) == 1
    assert found[0].name == "constexpr"
    assert found[0].category == "keyword"

    not_found = backend.verify_mechanism("nonexistent_mechanism")
    assert not_found == []


def test_upsert_grammar_rules_and_search(backend):
    _seed_basic(backend)
    backend.upsert_grammar_rules("n5008", [
        GrammarRuleRow("n5008", "expression", "expr", "expression: assignment-expression"),
        GrammarRuleRow("n5008", "declaration", "dcl.dcl", "declaration: block-declaration"),
    ])
    rule = backend.search_grammar("expression")
    assert rule is not None
    assert rule.nonterminal == "expression"
    assert rule.stable_label == "expr"

    missing = backend.search_grammar("nonexistent-rule")
    assert missing is None


def test_upsert_defined_terms_and_lookup(backend):
    _seed_basic(backend)
    backend.upsert_defined_terms("n5008", [
        DefinedTermRow("n5008", "undefined behavior", "defns.undefined",
                       "behavior for which this document imposes no requirements"),
        DefinedTermRow("n5008", "lvalue", "basic.lval",
                       "an expression whose evaluation designates an entity"),
    ])
    results = backend.lookup_definition("undefined behavior")
    assert len(results) >= 1
    assert results[0].stable_label == "defns.undefined"
    assert "no requirements" in results[0].definition_text

    missing = backend.lookup_definition("nonexistent term")
    assert missing == []


def test_upsert_library_declarations_and_lookup(backend):
    _seed_basic(backend)
    backend.upsert_library_declarations("n5008", [
        LibraryDeclRow("n5008", "vector.modifiers", "void push_back(const T& x)",
                       "Appends a copy of x.", effects="Appends x to the sequence."),
        LibraryDeclRow("n5008", "alg.sort", "void sort(RandomIt first, RandomIt last)",
                       "Sorts the range.", complexity="O(N log N) comparisons."),
    ])
    results = backend.lookup_declarations("push_back")
    assert len(results) == 1
    assert results[0].stable_label == "vector.modifiers"
    assert results[0].effects == "Appends x to the sequence."

    results_pattern = backend.lookup_declarations("sort")
    assert len(results_pattern) == 1
    assert results_pattern[0].complexity == "O(N log N) comparisons."


def test_upsert_paragraphs_and_query(backend):
    _seed_basic(backend)
    backend.upsert_paragraphs("n5008", [
        ParagraphRow("n5008", "basic.life", 1,
                     r"\pnum The lifetime begins", "The lifetime begins", "normative"),
        ParagraphRow("n5008", "basic.life", 2,
                     r"\pnum The lifetime ends", "The lifetime ends", "normative"),
        ParagraphRow("n5008", "basic.types", 1,
                     r"\pnum Types intro", "Types intro", "informative"),
    ])
    para = backend.lookup_paragraph("basic.life", 1)
    assert para is not None
    assert para.cleaned_text == "The lifetime begins"
    assert para.normative_force == "normative"

    missing = backend.lookup_paragraph("basic.life", 99)
    assert missing is None

    all_paras = backend.get_paragraphs("basic.life")
    assert len(all_paras) == 2
    assert all_paras[0].paragraph_number == 1
    assert all_paras[1].paragraph_number == 2


def test_get_ancestors(backend):
    rows = [
        _make_row("n5008", "basic", "Basic concepts", 0, None, "basic.tex",
                  r"\pnum Ch", "Chapter"),
        _make_row("n5008", "basic.scope", "Scope", 1, "basic", "basic.tex",
                  r"\pnum Sc", "Scope section"),
        _make_row("n5008", "basic.scope.pdecl", "Point of declaration", 2,
                  "basic.scope", "basic.tex", r"\pnum Pd", "Point of decl"),
    ]
    backend.upsert_draft("n5008", rows)

    ancestors = backend.get_ancestors("basic.scope.pdecl")
    labels = [a.stable_label for a in ancestors]
    assert labels == ["basic", "basic.scope"]

    root_ancestors = backend.get_ancestors("basic")
    assert root_ancestors == []


def test_lookup_sections_batch(backend):
    _seed_basic(backend)
    results = backend.lookup_sections(["basic.life", "basic.types", "nonexistent"])
    labels = [r.stable_label for r in results]
    assert "basic.life" in labels
    assert "basic.types" in labels
    assert "nonexistent" not in labels
    assert len(results) == 2

    empty = backend.lookup_sections([])
    assert empty == []


def test_atomic_replace_draft(backend):
    staging_tag = "_staging_n9999_123"
    real_tag = "n9999"

    rows = [
        _make_row(staging_tag, "intro", "Introduction", 0, None, "intro.tex",
                  r"\pnum Intro", "Introduction text"),
    ]
    backend.upsert_draft(staging_tag, rows)
    backend.upsert_xrefs(staging_tag, [("intro", "basic.life")])
    backend.upsert_mechanisms(staging_tag, [
        MechanismRow(staging_tag, "decltype", "keyword", "intro"),
    ])

    assert backend.lookup_section("intro", staging_tag) is not None
    assert backend.lookup_section("intro", real_tag) is None

    backend.atomic_replace_draft(staging_tag, real_tag)

    assert backend.lookup_section("intro", staging_tag) is None
    assert backend.lookup_section("intro", real_tag) is not None

    refs = backend.get_references_from("intro", real_tag)
    assert refs == ["basic.life"]

    mechs = backend.verify_mechanism("decltype", real_tag)
    assert len(mechs) == 1
    assert mechs[0].draft_tag == real_tag
