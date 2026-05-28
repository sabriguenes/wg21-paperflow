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

from cpp_mcp.backend import SectionRow
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
