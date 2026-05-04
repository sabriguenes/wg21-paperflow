#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for SqliteBackend."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from paperstore import SqliteBackend
from paperstore.errors import (
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperMdError,
    MissingSourceError,
)


@pytest.fixture
def store(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


class _FailingConn:
    """Proxy a sqlite3.Connection, raising OperationalError on the Nth execute()."""

    def __init__(self, real: sqlite3.Connection, fail_on_nth: int) -> None:
        self._real = real
        self._fail_on = fail_on_nth
        self._calls = 0

    def execute(self, *args, **kwargs):
        self._calls += 1
        if self._calls == self._fail_on:
            raise sqlite3.OperationalError("simulated SQL failure")
        return self._real.execute(*args, **kwargs)

    def __enter__(self):
        return self._real.__enter__()

    def __exit__(self, *args):
        return self._real.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_put_source_and_get_source_path(store: SqliteBackend, tmp_path: Path):
    path = store.put_source("P1234R0", b"%PDF-1.7\n", suffix=".pdf")
    assert path == tmp_path / "paperstore" / "p1234r0.pdf"
    assert path.read_bytes() == b"%PDF-1.7\n"
    assert store.get_source_path("p1234r0") == path


def test_put_source_updates_source_file_in_db(store: SqliteBackend):
    path = store.put_source("P1", b"x", suffix=".pdf")
    meta = store.get_meta("P1")
    assert meta["source_file"] == str(path)


def test_put_source_overwrites_existing(store: SqliteBackend):
    store.put_source("P1", b"v1", suffix=".pdf")
    store.put_source("P1", b"v2", suffix=".pdf")
    assert store.get_source_path("P1").read_bytes() == b"v2"


def test_get_source_path_missing_raises(store: SqliteBackend):
    with pytest.raises(MissingSourceError):
        store.get_source_path("NOPE")


def test_write_and_get_paper_md(store: SqliteBackend, tmp_path: Path):
    path = store.write_paper_md("P1", "# Hi\n")
    assert path == tmp_path / "paperstore" / "p1.md"
    assert store.get_paper_md("P1") == "# Hi\n"


def test_get_paper_md_missing_raises(store: SqliteBackend):
    with pytest.raises(MissingPaperMdError):
        store.get_paper_md("NOPE")


def test_write_paper_md_updates_markdown_path(store: SqliteBackend):
    path = store.write_paper_md("P1", "content")
    meta = store.get_meta("P1")
    assert meta["markdown_path"] == str(path)


def test_write_intermediate(store: SqliteBackend, tmp_path: Path):
    store.write_intermediate("P1", "1-findings", [{"n": 1}])
    path = tmp_path / "paperstore" / "p1.1-findings.json"
    assert path.exists()
    assert json.loads(path.read_text())  == [{"n": 1}]
    assert list((tmp_path / "paperstore").glob("*.partial")) == []


def test_upsert_year_and_list_papers(store: SqliteBackend):
    papers = [
        {"paper_id": "P1", "title": "One"},
        {"paper_id": "P2", "title": "Two"},
    ]
    store.upsert_year("2026", papers)
    rows = store.list_papers_for_year("2026")
    ids = {r["paper_id"] for r in rows}
    assert ids == {"P1", "P2"}


def test_upsert_year_preserves_source_file(store: SqliteBackend):
    """Re-upsert must not clobber source_file set by download."""
    store.upsert_year("2026", [{"paper_id": "P1", "title": "T"}])
    source_path = store.put_source("P1", b"bytes", suffix=".pdf")
    store.upsert_year("2026", [{"paper_id": "P1", "title": "Updated"}])
    meta = store.get_meta("P1")
    assert meta["source_file"] == str(source_path)


def test_write_meta_json_preserves_source_and_markdown(store: SqliteBackend):
    """write_meta_json must not clobber source_file/markdown_path on omission."""
    source_path = store.put_source("P1", b"bytes", suffix=".pdf")
    md_path = store.write_paper_md("P1", "# body\n")
    store.write_meta_json("P1", {"title": "T", "year": "2026"})
    meta = store.get_meta("P1")
    assert meta["title"] == "T"
    assert meta["source_file"] == str(source_path)
    assert meta["markdown_path"] == str(md_path)


def test_write_meta_json_can_set_source_file_when_provided(store: SqliteBackend):
    """write_meta_json still writes columns the caller explicitly supplies."""
    store.write_meta_json("P1", {"title": "T", "source_file": "/tmp/explicit.pdf"})
    meta = store.get_meta("P1")
    assert meta["source_file"] == "/tmp/explicit.pdf"


def test_close_is_idempotent(tmp_path: Path):
    backend = SqliteBackend(tmp_path)
    backend.close()
    backend.close()


def test_context_manager_closes_connection(tmp_path: Path):
    with SqliteBackend(tmp_path) as backend:
        backend.upsert_year("2026", [{"paper_id": "P1"}])
    # AttributeError if close() nulled _conn; ProgrammingError if it left a closed handle.
    with pytest.raises((AttributeError, sqlite3.ProgrammingError)):
        backend.list_all_paper_ids()


def test_put_source_rejects_suffix_without_dot(store: SqliteBackend):
    with pytest.raises(ValueError, match=r"must start with '\.'"):
        store.put_source("P1", b"x", suffix="pdf")


def test_atomic_write_bytes_cleans_partial_on_failure(
    store: SqliteBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A rename failure must remove the .partial file, not the (absent) target."""
    from paperstore import sqlite_backend as backend_mod

    def _boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(backend_mod, "_atomic_replace", _boom)
    target = tmp_path / "p1.pdf"
    with pytest.raises(OSError, match="simulated rename"):
        store._atomic_write_bytes(target, b"data")
    assert not target.exists()
    assert not (tmp_path / "p1.pdf.partial").exists()


def test_atomic_write_text_cleans_partial_on_failure(
    store: SqliteBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Same cleanup contract for the text variant."""
    from paperstore import sqlite_backend as backend_mod

    def _boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(backend_mod, "_atomic_replace", _boom)
    target = tmp_path / "p1.md"
    with pytest.raises(OSError, match="simulated rename"):
        store._atomic_write_text(target, "body")
    assert not target.exists()
    assert not (tmp_path / "p1.md.partial").exists()


def test_writers_do_not_leave_temp_files_after_success(
    store: SqliteBackend, tmp_path: Path
):
    """No .partial or legacy .tmp.* siblings should linger after a successful write."""
    store.put_source("P1", b"x", suffix=".pdf")
    store.write_paper_md("P1", "body")
    papers_dir = tmp_path / "paperstore"
    leftovers = list(papers_dir.glob("*.partial")) + list(papers_dir.glob("*.tmp.*"))
    assert leftovers == []


def test_put_source_rolls_back_on_sql_failure(
    store: SqliteBackend, tmp_path: Path
):
    """If the UPDATE step fails, the file remains but the row is rolled back."""
    real = store._conn
    store._conn = _FailingConn(real, fail_on_nth=2)  # 1=INSERT, 2=UPDATE
    try:
        with pytest.raises(sqlite3.OperationalError, match="simulated"):
            store.put_source("P1", b"data", suffix=".pdf")
    finally:
        store._conn = real
    assert (tmp_path / "paperstore" / "p1.pdf").exists()
    with pytest.raises(MissingMetaError):
        store.get_meta("P1")


def test_write_paper_md_rolls_back_on_sql_failure(
    store: SqliteBackend, tmp_path: Path
):
    """File on disk, no DB row, when the UPDATE step fails."""
    real = store._conn
    store._conn = _FailingConn(real, fail_on_nth=2)
    try:
        with pytest.raises(sqlite3.OperationalError, match="simulated"):
            store.write_paper_md("P1", "body")
    finally:
        store._conn = real
    assert (tmp_path / "paperstore" / "p1.md").exists()
    with pytest.raises(MissingMetaError):
        store.get_meta("P1")


def test_write_meta_json_rolls_back_on_sql_failure(store: SqliteBackend):
    """A failed UPDATE leaves the row's prior values intact."""
    store.upsert_year("2026", [{"paper_id": "P1", "title": "original"}])
    real = store._conn
    store._conn = _FailingConn(real, fail_on_nth=2)
    try:
        with pytest.raises(sqlite3.OperationalError, match="simulated"):
            store.write_meta_json("P1", {"title": "updated", "year": "2027"})
    finally:
        store._conn = real
    meta = store.get_meta("P1")
    assert meta["title"] == "original"
    assert meta["year"] == "2026"


def test_reconcile_empty_workspace(store: SqliteBackend):
    """Empty workspace is a clean no-op."""
    assert store.reconcile() == {"sources": 0, "markdowns": 0}


def test_reconcile_backfills_orphan_artifacts(
    store: SqliteBackend, tmp_path: Path
):
    """Files dropped into the paperstore/ subdir get indexed without re-writing them."""
    papers_dir = tmp_path / "paperstore"
    (papers_dir / "p1.pdf").write_bytes(b"%PDF-1.7\n")
    (papers_dir / "p2.html").write_text("<html/>")
    (papers_dir / "p3.md").write_text("# body\n")

    counts = store.reconcile()
    assert counts == {"sources": 2, "markdowns": 1}
    assert store.get_source_path("P1") == papers_dir / "p1.pdf"
    assert store.get_source_path("P2") == papers_dir / "p2.html"
    assert store.get_paper_md("P3") == "# body\n"


def test_reconcile_preserves_existing_values(store: SqliteBackend, tmp_path: Path):
    """Reconcile fills empties only; it does not overwrite indexed paths."""
    real_path = store.put_source("P1", b"x", suffix=".pdf")
    papers_dir = tmp_path / "paperstore"
    (papers_dir / "p1.html").write_text("<html/>")
    counts = store.reconcile()
    assert counts["sources"] == 0
    assert store.get_source_path("P1") == real_path


def test_reconcile_skips_intermediates_partials_and_db(
    store: SqliteBackend, tmp_path: Path
):
    """Non-artifact files (intermediates, .partial) are ignored."""
    papers_dir = tmp_path / "paperstore"
    (papers_dir / "p1.1-findings.json").write_text("[]")
    (papers_dir / "p1.prompts.json").write_text("[]")
    (papers_dir / "p1.pdf.partial").write_bytes(b"in-flight")
    counts = store.reconcile()
    assert counts == {"sources": 0, "markdowns": 0}
    assert store.list_all_paper_ids() == []


def test_reconcile_is_idempotent(store: SqliteBackend, tmp_path: Path):
    (tmp_path / "paperstore" / "p1.pdf").write_bytes(b"x")
    first = store.reconcile()
    second = store.reconcile()
    assert first == {"sources": 1, "markdowns": 0}
    assert second == {"sources": 0, "markdowns": 0}


def test_list_papers_for_year_missing_raises(store: SqliteBackend):
    with pytest.raises(MissingMailingIndexError):
        store.list_papers_for_year("9999")


def test_has_year(store: SqliteBackend):
    assert not store.has_year("2026")
    store.upsert_year("2026", [{"paper_id": "P1"}])
    assert store.has_year("2026")


def test_list_all_paper_ids(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1"}, {"paper_id": "P2"}])
    ids = store.list_all_paper_ids()
    assert set(ids) == {"P1", "P2"}


def test_resolve_year_for_paper_hit(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1", "url": "https://example.com/p1.pdf"}])
    result = store.resolve_year_for_paper("P1")
    assert result is not None
    year, row = result
    assert year == "2026"
    assert row["url"] == "https://example.com/p1.pdf"


def test_resolve_year_for_paper_miss(store: SqliteBackend):
    assert store.resolve_year_for_paper("NOPE") is None


def test_resolve_year_for_paper_case_insensitive(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P3000R5"}])
    assert store.resolve_year_for_paper("p3000r5") is not None
    assert store.resolve_year_for_paper("P3000R5") is not None


def test_get_meta_missing_raises(store: SqliteBackend):
    with pytest.raises(MissingMetaError):
        store.get_meta("NOPE")


def test_write_meta_json_upserts(store: SqliteBackend):
    store.write_meta_json("P1", {"title": "Hello", "year": "2026"})
    meta = store.get_meta("P1")
    assert meta["title"] == "Hello"
    assert meta["year"] == "2026"


def test_authors_roundtrip_as_list(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1", "authors": ["Alice", "Bob"]}])
    meta = store.get_meta("P1")
    assert meta["authors"] == ["Alice", "Bob"]


def test_list_years(store: SqliteBackend):
    store.upsert_year("2025", [{"paper_id": "P1"}])
    store.upsert_year("2026", [{"paper_id": "P2"}, {"paper_id": "P3"}])
    years = store.list_years()
    assert ("2025", 1) in years
    assert ("2026", 2) in years
