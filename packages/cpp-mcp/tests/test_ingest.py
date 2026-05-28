#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the ingestion pipeline (offline, no git clone)."""

from __future__ import annotations

import pytest

from cpp_mcp.ingest import ingest_from_directory
from cpp_mcp.sqlite_backend import SqliteStandardBackend


FIXTURE_BASIC_TEX = r"""
\rSec0[basic]{Basic concepts}

\pnum
Introductory text about basic concepts.

\rSec1[basic.life]{Object lifetime}

\pnum
The lifetime of an object of type T begins when storage is obtained.

\pnum
The lifetime ends when the destructor call starts.

\rSec2[basic.life.general]{General}

\pnum
General rules about lifetime.
"""

FIXTURE_EXPR_TEX = r"""
\rSec0[expr]{Expressions}

\pnum
An expression is a sequence of operators and operands.

\rSec1[expr.prim]{Primary expressions}

\pnum
Primary expressions include literals, names, and lambda expressions.
"""

FIXTURE_STD_TEX = r"""
\input{basic}
\input{expr}
"""


@pytest.fixture
def source_dir(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "std.tex").write_text(FIXTURE_STD_TEX, encoding="utf-8")
    (src / "basic.tex").write_text(FIXTURE_BASIC_TEX, encoding="utf-8")
    (src / "expr.tex").write_text(FIXTURE_EXPR_TEX, encoding="utf-8")
    return src


@pytest.fixture
def backend(tmp_path):
    b = SqliteStandardBackend(tmp_path / "test.db")
    b.create_schema()
    return b


def test_ingest_from_directory(source_dir, backend):
    count = ingest_from_directory(backend, source_dir, "test-tag", git_sha="abc123")
    assert count == 5


def test_sections_queryable_after_ingest(source_dir, backend):
    ingest_from_directory(backend, source_dir, "test-tag")
    assert backend.lookup_section("basic", "test-tag") is not None
    assert backend.lookup_section("basic.life", "test-tag") is not None
    assert backend.lookup_section("expr", "test-tag") is not None
    assert backend.lookup_section("expr.prim", "test-tag") is not None


def test_drafts_table_populated(source_dir, backend):
    ingest_from_directory(backend, source_dir, "test-tag", git_sha="abc123")
    drafts = backend.list_drafts()
    assert len(drafts) == 1
    assert drafts[0].draft_tag == "test-tag"
    assert drafts[0].section_count == 5
    assert drafts[0].git_sha == "abc123"
    assert drafts[0].ingested_at is not None


def test_reingest_updates_drafts(source_dir, backend):
    ingest_from_directory(backend, source_dir, "test-tag", git_sha="abc123")
    ingest_from_directory(backend, source_dir, "test-tag", git_sha="def456")
    drafts = backend.list_drafts()
    assert len(drafts) == 1
    assert drafts[0].git_sha == "def456"


def test_reingest_replaces_sections(source_dir, backend, tmp_path):
    ingest_from_directory(backend, source_dir, "test-tag")
    assert backend.lookup_section("basic.life.general", "test-tag") is not None

    new_src = tmp_path / "source2"
    new_src.mkdir()
    (new_src / "std.tex").write_text(r"\input{basic}", encoding="utf-8")
    (new_src / "basic.tex").write_text(
        r"\rSec0[basic]{Basic concepts}" + "\n\nRevised content.\n",
        encoding="utf-8",
    )
    ingest_from_directory(backend, new_src, "test-tag")
    assert backend.lookup_section("basic.life.general", "test-tag") is None
    assert backend.lookup_section("basic", "test-tag") is not None


def test_fts_works_after_ingest(source_dir, backend):
    ingest_from_directory(backend, source_dir, "test-tag")
    results = backend.search("lifetime", draft_tag="test-tag")
    labels = [r.stable_label for r in results]
    assert "basic.life" in labels


def test_multiple_drafts_coexist(source_dir, backend):
    ingest_from_directory(backend, source_dir, "v1")
    ingest_from_directory(backend, source_dir, "v2")
    assert backend.lookup_section("basic", "v1") is not None
    assert backend.lookup_section("basic", "v2") is not None
    drafts = backend.list_drafts()
    assert len(drafts) == 2


def test_include_order_from_std_tex(source_dir, backend):
    """Sections from basic.tex should appear before expr.tex per std.tex order."""
    ingest_from_directory(backend, source_dir, "test-tag")
    sections = backend.list_sections(draft_tag="test-tag")
    labels = [s.stable_label for s in sections]
    basic_idx = labels.index("basic")
    expr_idx = labels.index("expr")
    assert basic_idx < expr_idx
