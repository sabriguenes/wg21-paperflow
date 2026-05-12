#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for the paperstore read surface and from_uri factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperstore import (
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperMdError,
    MissingSourceError,
    SqliteBackend,
    from_uri,
)


def test_put_source_then_get_source_path_roundtrip(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    path = store.put_source("p1234r0", b"%PDF-1.7\n", suffix=".pdf")
    assert path == tmp_path / "paperstore" / "p1234r0.pdf"
    assert store.get_source_path("P1234R0") == path
    assert path.read_bytes() == b"%PDF-1.7\n"


def test_get_source_path_missing_raises(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    with pytest.raises(MissingSourceError):
        store.get_source_path("NOPE")


def test_get_meta_from_upserted_row(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    store.upsert_year("2026", [{"paper_id": "P1", "title": "from mailing"}])
    assert store.get_meta("P1").title == "from mailing"


def test_get_meta_missing_raises(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    with pytest.raises(MissingMetaError):
        store.get_meta("P1")


def test_get_paper_md_roundtrip_and_missing(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    store.write_paper_md("P1", "# Hi\n")
    assert store.get_paper_md("p1") == "# Hi\n"
    with pytest.raises(MissingPaperMdError):
        store.get_paper_md("P2")


def test_get_paper_md_path_is_deterministic_and_does_not_check_existence(
    tmp_path: Path,
):
    store = SqliteBackend(tmp_path)
    expected = tmp_path / "paperstore" / "p1234r0.md"
    # Existence is intentionally not checked: callers (e.g. file
    # watchers) need a stable path before the file lands.
    assert store.get_paper_md_path("P1234R0") == expected
    assert store.get_paper_md_path("p1234r0") == expected
    written = store.write_paper_md("P1234R0", "# Hi\n")
    assert store.get_paper_md_path("P1234R0") == written


def test_list_papers_for_year_roundtrip_and_missing(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    store.upsert_year("2026", [{"paper_id": "P1"}])
    rows = store.list_papers_for_year("2026")
    assert [r.paper_id for r in rows] == ["P1"]
    with pytest.raises(MissingMailingIndexError):
        store.list_papers_for_year("9999")


def test_from_uri_file_and_none(tmp_path: Path):
    b1 = from_uri(None, workspace_dir=tmp_path)
    assert isinstance(b1, SqliteBackend)
    assert b1.workspace_dir == tmp_path

    b2 = from_uri(tmp_path.as_uri())
    assert isinstance(b2, SqliteBackend)
    assert b2.workspace_dir == tmp_path


def test_from_uri_rejects_unsupported_scheme(tmp_path: Path):
    with pytest.raises(ValueError, match="unsupported URI scheme"):
        from_uri("postgres://localhost/x", workspace_dir=tmp_path)


def test_from_uri_file_rejects_non_localhost_authority(tmp_path: Path):
    # Inject an authority into the canonical empty-authority URI so the
    # resulting string is well-formed on both POSIX and Windows.
    bad_uri = tmp_path.as_uri().replace("file://", "file://example.com", 1)
    with pytest.raises(ValueError, match="empty or 'localhost' authority"):
        from_uri(bad_uri)


def test_from_uri_file_allows_localhost_authority(tmp_path: Path):
    localhost_uri = tmp_path.as_uri().replace("file://", "file://localhost", 1)
    backend = from_uri(localhost_uri)
    assert isinstance(backend, SqliteBackend)
    assert backend.workspace_dir == tmp_path
