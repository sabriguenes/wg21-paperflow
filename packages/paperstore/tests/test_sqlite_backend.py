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
from types import SimpleNamespace

import pytest

from paperstore import SqliteBackend
from paperstore.errors import (
    InvalidSuffixError,
    MissingAdvocatusError,
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
    assert meta.source_file == str(path)


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
    assert meta.markdown_path == str(path)


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
    ids = {r.paper_id for r in rows}
    assert ids == {"P1", "P2"}


def test_fail_paper_sets_failed_status_and_error(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1", "title": "One"}])

    before = store._conn.execute(
        "SELECT updated_at FROM papers WHERE paper_id = ?", ("P1",)
    ).fetchone()["updated_at"]
    store.fail_paper("P1", 2, "boom")
    row = store.get_meta("P1")
    after = store._conn.execute(
        "SELECT updated_at FROM papers WHERE paper_id = ?", ("P1",)
    ).fetchone()["updated_at"]

    assert row.status == -3
    assert row.error == "boom"
    assert after != before


def test_upsert_year_preserves_source_file(store: SqliteBackend):
    """Re-upsert must not clobber source_file set by download."""
    store.upsert_year("2026", [{"paper_id": "P1", "title": "T"}])
    source_path = store.put_source("P1", b"bytes", suffix=".pdf")
    store.upsert_year("2026", [{"paper_id": "P1", "title": "Updated"}])
    meta = store.get_meta("P1")
    assert meta.source_file == str(source_path)


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
    with pytest.raises(InvalidSuffixError, match=r"must start with '\.'"):
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


def test_reconcile_empty_workspace(store: SqliteBackend):
    """Empty workspace is a clean no-op."""
    assert store.reconcile() == {
        "sources": 0,
        "markdowns": 0,
        "advocati": 0,
        "agorae": 0,
        "line_counts": 0,
    }


def test_reconcile_backfills_orphan_artifacts(
    store: SqliteBackend, tmp_path: Path
):
    """Files dropped into the paperstore/ subdir get indexed without re-writing them."""
    papers_dir = tmp_path / "paperstore"
    (papers_dir / "p1.pdf").write_bytes(b"%PDF-1.7\n")
    (papers_dir / "p2.html").write_text("<html/>")
    (papers_dir / "p3.md").write_text("# body\n")

    counts = store.reconcile()
    assert counts == {
        "sources": 2,
        "markdowns": 1,
        "advocati": 0,
        "agorae": 0,
        "line_counts": 1,
    }
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
    assert counts == {
        "sources": 0,
        "markdowns": 0,
        "advocati": 0,
        "agorae": 0,
        "line_counts": 0,
    }
    assert store.list_all_paper_ids() == []


def test_reconcile_is_idempotent(store: SqliteBackend, tmp_path: Path):
    (tmp_path / "paperstore" / "p1.pdf").write_bytes(b"x")
    first = store.reconcile()
    second = store.reconcile()
    assert first == {
        "sources": 1,
        "markdowns": 0,
        "advocati": 0,
        "agorae": 0,
        "line_counts": 0,
    }
    assert second == {
        "sources": 0,
        "markdowns": 0,
        "advocati": 0,
        "agorae": 0,
        "line_counts": 0,
    }


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
    assert row.url == "https://example.com/p1.pdf"


def test_resolve_year_for_paper_miss(store: SqliteBackend):
    assert store.resolve_year_for_paper("NOPE") is None


def test_resolve_year_for_paper_case_insensitive(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P3000R5"}])
    assert store.resolve_year_for_paper("p3000r5") is not None
    assert store.resolve_year_for_paper("P3000R5") is not None


def test_get_meta_missing_raises(store: SqliteBackend):
    with pytest.raises(MissingMetaError):
        store.get_meta("NOPE")


def test_authors_roundtrip_as_list(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1", "authors": ["Alice", "Bob"]}])
    meta = store.get_meta("P1")
    assert meta.authors == ["Alice", "Bob"]


def test_list_years(store: SqliteBackend):
    store.upsert_year("2025", [{"paper_id": "P1"}])
    store.upsert_year("2026", [{"paper_id": "P2"}, {"paper_id": "P3"}])
    years = store.list_years()
    assert ("2025", 1) in years
    assert ("2026", 2) in years


def test_reconcile_skips_per_tool_debug_and_trace_artifacts(store: SqliteBackend):
    """Per-tool .debug.md / .trace.md files are scratch outputs; reconcile
    must not classify them as paper markdown or advocatus."""
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    for name in (
        "p1000r0.advocatus.debug.md",
        "p1000r0.advocatus.trace.md",
    ):
        (store._papers_dir / name).write_text("scratch", encoding="utf-8")
    counts = store.reconcile()
    assert counts["markdowns"] == 0
    assert counts["advocati"] == 0
    meta = store.get_meta("P1000R0")
    assert meta.markdown_path == ""
    assert meta.advocatus_path == ""


# ---- advocatus lifecycle --------------------------------------------------


def test_write_advocatus_md(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    path = store.write_advocatus_md("P1000R0", "# Relatio\n\nContent.")
    assert path.exists()
    assert path.name == "p1000r0.advocatus.md"
    assert path.read_text(encoding="utf-8") == "# Relatio\n\nContent."
    meta = store.get_meta("P1000R0")
    assert meta.advocatus_path == str(path)


def test_get_advocatus_path(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    store.write_advocatus_md("P1000R0", "# Relatio")
    assert store.get_advocatus_path("P1000R0").exists()


def test_get_advocatus_path_missing_raises(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    with pytest.raises(MissingAdvocatusError):
        store.get_advocatus_path("P1000R0")


def test_get_advocatus_path_no_paper_raises(store: SqliteBackend):
    with pytest.raises(MissingAdvocatusError):
        store.get_advocatus_path("NOPE")


def test_clear_advocatus_deletes_file(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    path = store.write_advocatus_md("P1000R0", "# Relatio")
    assert path.exists()
    store.clear_advocatus("P1000R0")
    assert not path.exists()
    with pytest.raises(MissingAdvocatusError):
        store.get_advocatus_path("P1000R0")


def test_clear_advocatus_idempotent(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    store.clear_advocatus("P1000R0")
    store.clear_advocatus("P1000R0")


def test_write_advocatus_md_overwrites(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    store.write_advocatus_md("P1000R0", "# Old")
    store.write_advocatus_md("P1000R0", "# New")
    path = store.get_advocatus_path("P1000R0")
    assert path.read_text(encoding="utf-8") == "# New"


def test_reconcile_finds_advocatus_files(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    advocatus_path = store._papers_dir / "p1000r0.advocatus.md"
    advocatus_path.write_text("# Relatio", encoding="utf-8")
    counts = store.reconcile()
    assert counts["advocati"] == 1
    meta = store.get_meta("P1000R0")
    assert meta.advocatus_path == str(advocatus_path)


# ---- per-paper debug/trace path helpers ------------------------------------


def test_get_debug_md_path(store: SqliteBackend):
    p = store.get_debug_md_path("P1234R0")
    assert p.name == "p1234r0.debug.md"
    assert p.parent == store._papers_dir
    assert not p.exists()


def test_get_trace_md_path(store: SqliteBackend):
    p = store.get_trace_md_path("P1234R0")
    assert p.name == "p1234r0.trace.md"
    assert p.parent == store._papers_dir
    assert not p.exists()


# ---- extract store/get round-trips ----------------------------------------


def _make_loc(line=1, start_char=0, end_char=10):
    return SimpleNamespace(line=line, start_char=start_char, end_char=end_char)


def _make_claim(text="test claim", section="intro", question="why?", line=1, uid=1):
    return SimpleNamespace(
        uid=uid,
        loc=_make_loc(line=line),
        text=text,
        section=section,
        question=question,
        merged_into=None,
        original_quotes=[text],
        depends_on=[],
    )


def _make_marker(text="however", section="intro", marker_type="hedge",
                 target="claim", intensity="low", line=5, uid=1):
    return SimpleNamespace(
        uid=uid,
        loc=_make_loc(line=line),
        text=text,
        section=section,
        marker_type=marker_type,
        target=target,
        intensity=intensity,
    )


def _make_paper_citation(paper_id="P2000R0", count=3):
    return SimpleNamespace(paper_id=paper_id, count=count)


def test_store_and_get_claims(store: SqliteBackend):
    claims = [
        _make_claim(text="claim A", section="intro", question="why A?", line=1, uid=1),
        _make_claim(text="claim B", section="design", question="why B?", line=10, uid=2),
    ]
    store.store_claims("P1", claims)
    rows = store.get_claims("P1")
    assert len(rows) == 2
    assert rows[0].text == "claim A"
    assert rows[0].section == "intro"
    assert rows[0].question == "why A?"
    assert rows[0].loc_line == 1
    assert rows[0].loc_start == 0
    assert rows[0].loc_end == 10
    assert rows[0].merged_into is None
    assert rows[1].text == "claim B"
    assert rows[1].section == "design"


def test_store_and_get_rhetoric(store: SqliteBackend):
    markers = [
        _make_marker(text="however", marker_type="hedge", target="claim",
                     intensity="low", line=5, uid=1),
        _make_marker(text="clearly", marker_type="booster", target="evidence",
                     intensity="high", line=12, uid=2),
    ]
    store.store_rhetoric("P1", markers)
    rows = store.get_rhetoric("P1")
    assert len(rows) == 2
    assert rows[0].text == "however"
    assert rows[0].marker_type == "hedge"
    assert rows[0].target == "claim"
    assert rows[0].intensity == "low"
    assert rows[0].loc_line == 5
    assert rows[1].text == "clearly"
    assert rows[1].marker_type == "booster"
    assert rows[1].intensity == "high"


def test_store_and_get_paper_citations(store: SqliteBackend):
    cites = [
        _make_paper_citation("P2000R0", 5),
        _make_paper_citation("P3000R1", 2),
    ]
    store.store_paper_citations("P1", cites)
    rows = store.get_paper_citations("P1")
    assert len(rows) == 2
    assert rows[0].cited_paper_id == "P2000R0"
    assert rows[0].count == 5
    assert rows[1].cited_paper_id == "P3000R1"
    assert rows[1].count == 2


def test_store_replaces_previous(store: SqliteBackend):
    store.store_claims("P1", [_make_claim(text="old", uid=1)])
    assert len(store.get_claims("P1")) == 1

    store.store_claims("P1", [_make_claim(text="new A", line=2, uid=1), _make_claim(text="new B", line=3, uid=2)])
    rows = store.get_claims("P1")
    assert len(rows) == 2
    texts = {r.text for r in rows}
    assert texts == {"new A", "new B"}


def _make_claim_real_loc(text="x", section="s", question="q", line=1, kind="normative", uid=1):
    """Variant of _make_claim that uses the real SourceLoc (hashable)
    so it can flow through store_questions, which builds a set of uids."""
    from paperstore import SourceLoc
    return SimpleNamespace(
        uid=uid,
        loc=SourceLoc(line=line, start_char=0, end_char=10),
        text=text, section=section, question=question, kind=kind,
        merged_into=None, original_quotes=[text], depends_on=[],
    )


def _make_verdict(claim_uid, status="unproven"):
    return SimpleNamespace(claim_uid=claim_uid, related_uid=-1, status=status)


def test_store_questions_keeps_distinct_uids_with_identical_text(store: SqliteBackend):
    """Two unsupported claims at different uids with identical text+kind
    both persist; identity is the uid, not the text."""
    a = _make_claim_real_loc(text="X is fast", section="intro", question="why X?", line=1, uid=1)
    b = _make_claim_real_loc(text="X is fast", section="design", question="why X?", line=42, uid=2)
    store.store_questions(
        "P1", [a, b],
        [_make_verdict(a.uid), _make_verdict(b.uid)],
    )
    rows = store.get_questions("P1")
    assert len(rows) == 2
    assert {(r.loc_line, r.claim_text) for r in rows} == {
        (1, "X is fast"), (42, "X is fast"),
    }


def test_store_questions_keeps_distinct_text(store: SqliteBackend):
    a = _make_claim_real_loc(text="X is fast", section="intro", question="why X?", line=1, uid=1)
    b = _make_claim_real_loc(text="Y is slow", section="design", question="why Y?", line=10, uid=2)
    store.store_questions(
        "P1", [a, b],
        [_make_verdict(a.uid), _make_verdict(b.uid)],
    )
    rows = store.get_questions("P1")
    assert {r.claim_text for r in rows} == {"X is fast", "Y is slow"}


def test_store_questions_replaces_previous(store: SqliteBackend):
    """A re-run replaces all rows for the paper; no leftovers from prior runs."""
    a = _make_claim_real_loc(text="old", section="intro", question="old?", line=1, uid=1)
    store.store_questions("P1", [a], [_make_verdict(a.uid)])
    assert len(store.get_questions("P1")) == 1

    b = _make_claim_real_loc(text="new", section="design", question="new?", line=5, uid=2)
    store.store_questions("P1", [b], [_make_verdict(b.uid)])
    rows = store.get_questions("P1")
    assert len(rows) == 1
    assert rows[0].claim_text == "new"
    assert rows[0].loc_line == 5


def test_list_papers_since(store: SqliteBackend):
    store.upsert_year("2026", [
        {"paper_id": "P1", "title": "Jan", "mailing_date": "2026-01"},
        {"paper_id": "P2", "title": "Feb", "mailing_date": "2026-02"},
        {"paper_id": "P3", "title": "Mar", "mailing_date": "2026-03"},
        {"paper_id": "P4", "title": "Apr", "mailing_date": "2026-04"},
    ])
    rows = store.list_papers_since("2026-03")
    ids = {r.paper_id for r in rows}
    assert ids == {"P3", "P4"}
    assert all(r.mailing_date >= "2026-03" for r in rows)


# ---- disposition + previous_version round-trip -----------------------------


def test_upsert_year_stores_disposition_and_previous_version(store: SqliteBackend):
    papers = [
        {
            "paper_id": "P1000R8",
            "title": "Schedule",
            "disposition": "Adopted 2026-03",
            "previous_version": "p1000r7",
        },
    ]
    store.upsert_year("2026", papers)
    row = store.get_meta("P1000R8")
    assert row.disposition == "Adopted 2026-03"
    assert row.previous_version == "p1000r7"


def test_upsert_year_disposition_defaults_to_empty(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1"}])
    row = store.get_meta("P1")
    assert row.disposition == ""
    assert row.previous_version == ""


def test_upsert_year_updates_disposition_on_reupsert(store: SqliteBackend):
    store.upsert_year("2026", [{"paper_id": "P1", "disposition": ""}])
    store.upsert_year("2026", [{"paper_id": "P1", "disposition": "Adopted 2026-03"}])
    row = store.get_meta("P1")
    assert row.disposition == "Adopted 2026-03"


# ---- mailing label ---------------------------------------------------------


def test_upsert_year_populates_mailings_table(store: SqliteBackend):
    papers = [
        {
            "paper_id": "P1",
            "mailing_date": "2026-04",
            "mailing_label": "post-Croydon",
        },
        {
            "paper_id": "P2",
            "mailing_date": "2026-04",
            "mailing_label": "post-Croydon",
        },
    ]
    store.upsert_year("2026", papers)
    assert store.get_mailing_label("2026-04") == "post-Croydon"


def test_upsert_mailing_label_standalone(store: SqliteBackend):
    store.upsert_mailing_label("2026-02", "pre-Croydon")
    assert store.get_mailing_label("2026-02") == "pre-Croydon"


def test_get_mailing_label_missing_returns_empty(store: SqliteBackend):
    assert store.get_mailing_label("9999-01") == ""


def test_upsert_mailing_label_updates_existing(store: SqliteBackend):
    store.upsert_mailing_label("2026-04", "old")
    store.upsert_mailing_label("2026-04", "post-Croydon")
    assert store.get_mailing_label("2026-04") == "post-Croydon"


def test_upsert_year_skips_mailing_label_when_absent(store: SqliteBackend):
    """Papers without mailing_label don't insert empty labels."""
    store.upsert_year("2026", [{"paper_id": "P1", "mailing_date": "2026-01"}])
    assert store.get_mailing_label("2026-01") == ""


_ASSAY_TABLES = (
    "assay_claims", "assay_evidence", "assay_concessions",
    "assay_breadcrumbs", "assay_thesis", "assay_findings",
    "assay_asks", "assay_references", "assay_strengths",
    "assay_checklist", "assay_compounds", "assay_synthesis",
)


def _seed_assay_rows(store: SqliteBackend, pid: str) -> None:
    """Insert one row into each assay_* table for ``pid`` via raw SQL.

    Bypasses the typed writers so this test stays focused on the DELETE
    side of the contract. Each table gets enough columns to satisfy its
    NOT NULL constraints.
    """
    with store._conn:
        store._conn.execute(
            "INSERT INTO assay_claims (paper_id, uid, loc_line, quote) "
            "VALUES (?, ?, ?, ?)", (pid, 1, 10, "c"),
        )
        store._conn.execute(
            "INSERT INTO assay_evidence (paper_id, uid, loc_line, quote) "
            "VALUES (?, ?, ?, ?)", (pid, 1, 11, "e"),
        )
        store._conn.execute(
            "INSERT INTO assay_concessions (paper_id, uid, loc_line, quote) "
            "VALUES (?, ?, ?, ?)", (pid, 1, 12, "conc"),
        )
        store._conn.execute(
            "INSERT INTO assay_breadcrumbs "
            "(paper_id, uid, chunk_index, loc_line, gap) "
            "VALUES (?, ?, ?, ?, ?)", (pid, 1, 0, 13, "gap"),
        )
        store._conn.execute(
            "INSERT INTO assay_thesis (paper_id, central_claim) "
            "VALUES (?, ?)", (pid, "thesis"),
        )
        store._conn.execute(
            "INSERT INTO assay_findings (paper_id, uid, title, lens, severity) "
            "VALUES (?, ?, ?, ?, ?)", (pid, 1, "t", "design", "minor"),
        )
        store._conn.execute(
            "INSERT INTO assay_asks (paper_id, uid, target, quote, type) "
            "VALUES (?, ?, ?, ?, ?)", (pid, 1, "committee", "q", "poll"),
        )
        store._conn.execute(
            "INSERT INTO assay_references (paper_id, uid, ref_label) "
            "VALUES (?, ?, ?)", (pid, 1, "P9999R0"),
        )
        store._conn.execute(
            "INSERT INTO assay_strengths (paper_id, uid, title) "
            "VALUES (?, ?, ?)", (pid, 1, "strong"),
        )
        store._conn.execute(
            "INSERT INTO assay_checklist (paper_id, item_id, name) "
            "VALUES (?, ?, ?)", (pid, "item1", "name"),
        )
        store._conn.execute(
            "INSERT INTO assay_compounds (paper_id, uid, name) "
            "VALUES (?, ?, ?)", (pid, 1, "compound"),
        )
        store._conn.execute(
            "INSERT INTO assay_synthesis (paper_id, verdict) "
            "VALUES (?, ?)", (pid, "neutral"),
        )


def _count(store: SqliteBackend, table: str, pid: str) -> int:
    return store._conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE paper_id = ?", (pid,)
    ).fetchone()[0]


def test_clear_downstream_outputs_wipes_assay_rows(store: SqliteBackend):
    """``clear_downstream_outputs`` deletes every ``assay_*`` row for the
    target paper alongside the ``.assay.md`` report file. Stored
    ``loc.line`` offsets go stale on any markdown content change, so
    leaving them in place after a re-convert would cause a subsequent
    ``paperflow assay --rerender`` to point at the wrong lines.
    """
    store.upsert_year("2026", [{"paper_id": "P1"}, {"paper_id": "P2"}])
    store.write_assay_md("P1", "# assay\n")
    _seed_assay_rows(store, "P1")
    _seed_assay_rows(store, "P2")

    for table in _ASSAY_TABLES:
        assert _count(store, table, "P1") == 1
        assert _count(store, table, "P2") == 1

    cleared = store.clear_downstream_outputs("P1")
    assert cleared.assay is True

    for table in _ASSAY_TABLES:
        assert _count(store, table, "P1") == 0, (
            f"{table} not wiped for P1"
        )
        assert _count(store, table, "P2") == 1, (
            f"{table} unexpectedly wiped for unrelated paper P2"
        )


def test_clear_downstream_outputs_skips_assay_when_path_unset(
    store: SqliteBackend,
):
    """Without an ``assay_path``, ``clear_downstream_outputs`` reports
    ``assay=False`` and the wipe is gated by ``meta.assay_path`` (matching
    the advocatus/agora pattern). Orphan rows are uncommon in practice
    but left alone here.
    """
    store.upsert_year("2026", [{"paper_id": "P1"}])
    _seed_assay_rows(store, "P1")

    cleared = store.clear_downstream_outputs("P1")
    assert cleared.advocatus is False
    assert cleared.agora is False
    assert cleared.assay is False

    for table in _ASSAY_TABLES:
        assert _count(store, table, "P1") == 1


def test_clear_downstream_outputs_no_op_for_unknown_paper(
    store: SqliteBackend,
):
    """An unknown paper id returns an empty ClearedSet and leaves
    every assay_* table untouched."""
    store.upsert_year("2026", [{"paper_id": "P1"}])
    store.write_assay_md("P1", "# assay\n")
    _seed_assay_rows(store, "P1")

    cleared = store.clear_downstream_outputs("P_DOES_NOT_EXIST")
    assert cleared.advocatus is False
    assert cleared.agora is False
    assert cleared.assay is False

    for table in _ASSAY_TABLES:
        assert _count(store, table, "P1") == 1
