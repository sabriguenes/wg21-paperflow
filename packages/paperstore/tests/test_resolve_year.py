#
# Copyright (c) 2026 C++ Alliance (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for SqliteBackend.resolve_year_for_paper."""

from __future__ import annotations

from pathlib import Path

from paperstore import SqliteBackend


def _seed(store: SqliteBackend, year: str, papers: list[dict]) -> None:
    store.upsert_year(year, papers)


def test_resolve_hit(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    _seed(store, "2026", [
        {"paper_id": "P3000R5", "url": "https://example.com/p3000r5.pdf", "title": "Contracts"},
        {"paper_id": "P3100R1", "url": "https://example.com/p3100r1.html", "title": "Reflection"},
    ])
    result = store.resolve_year_for_paper("P3000R5")
    assert result is not None
    year, row = result
    assert year == "2026"
    assert row.paper_id == "P3000R5"
    assert row.url == "https://example.com/p3000r5.pdf"


def test_resolve_case_insensitive(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    _seed(store, "2026", [{"paper_id": "P2900R14", "url": "https://example.com/p2900r14.pdf"}])
    assert store.resolve_year_for_paper("p2900r14") is not None
    assert store.resolve_year_for_paper("P2900R14") is not None


def test_resolve_miss(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    _seed(store, "2026", [{"paper_id": "P3000R5", "url": "https://example.com/p3000r5.pdf"}])
    assert store.resolve_year_for_paper("P9999R0") is None


def test_resolve_no_data(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    assert store.resolve_year_for_paper("P3000R5") is None


def test_resolve_across_years(tmp_path: Path):
    store = SqliteBackend(tmp_path)
    _seed(store, "2025", [{"paper_id": "P2900R14", "url": "https://example.com/old.pdf"}])
    _seed(store, "2026", [{"paper_id": "P3000R5", "url": "https://example.com/new.pdf"}])

    y1, r1 = store.resolve_year_for_paper("P2900R14")
    assert y1 == "2025"

    y2, r2 = store.resolve_year_for_paper("P3000R5")
    assert y2 == "2026"
