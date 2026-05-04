#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the async ``paperlint.jobs.run_download`` orchestrator.

Stubs ``mailing.download.download_paper`` so the tests run hermetically -
no httpx, no network. The goal is to pin the orchestration contract:
idempotency filtering, no-URL skipping, and the ``force`` flag.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from paperlint import jobs
from paperstore import SqliteBackend


def _seed(store: SqliteBackend, papers: list[dict]) -> None:
    store.upsert_year("2026", papers)


def _stub_download(*, returns: dict[str, tuple[bytes, str]]):
    """Build a download_paper stub that returns canned content per paper id."""

    async def _impl(paper_id: str, *, source_url: str, client=None, timeout: float = 30.0):
        return returns.get(paper_id)

    return _impl


def test_run_download_skips_already_staged_papers(tmp_path: Path, monkeypatch):
    store = SqliteBackend(tmp_path)
    _seed(store, [
        {"paper_id": "P1000R0", "title": "Already staged", "url": "http://x/a.pdf"},
        {"paper_id": "P1001R0", "title": "Pending", "url": "http://x/b.pdf"},
    ])
    store.put_source("P1000R0", b"%PDF-staged", suffix=".pdf")

    calls: list[str] = []

    async def _record(paper_id: str, *, source_url: str, client=None, timeout: float = 30.0):
        calls.append(paper_id)
        return (b"%PDF-fresh", ".pdf")

    monkeypatch.setattr("mailing.download.download_paper", _record)

    result = asyncio.run(jobs.run_download(["P1000R0", "P1001R0"], store))

    assert calls == ["P1001R0"], "should only download the unstaged paper"
    assert result["succeeded"] == ["P1001R0"]
    skipped_ids = {entry["paper_id"] for entry in result["skipped"]}
    assert skipped_ids == {"P1000R0"}


def test_run_download_force_redownloads_staged(tmp_path: Path, monkeypatch):
    store = SqliteBackend(tmp_path)
    _seed(store, [
        {"paper_id": "P1000R0", "title": "Staged", "url": "http://x/a.pdf"},
    ])
    store.put_source("P1000R0", b"%PDF-old", suffix=".pdf")

    monkeypatch.setattr(
        "mailing.download.download_paper",
        _stub_download(returns={"P1000R0": (b"%PDF-new", ".pdf")}),
    )

    result = asyncio.run(jobs.run_download(["P1000R0"], store, force=True))

    assert result["succeeded"] == ["P1000R0"]
    assert result["skipped"] == []


def test_run_download_skips_papers_without_url(tmp_path: Path, monkeypatch):
    store = SqliteBackend(tmp_path)
    _seed(store, [
        {"paper_id": "P1000R0", "title": "Has URL", "url": "http://x/a.pdf"},
        {"paper_id": "P1001R0", "title": "Missing URL"},
    ])

    monkeypatch.setattr(
        "mailing.download.download_paper",
        _stub_download(returns={"P1000R0": (b"%PDF", ".pdf")}),
    )

    result = asyncio.run(jobs.run_download(["P1000R0", "P1001R0"], store))

    assert result["succeeded"] == ["P1000R0"]
    skipped_ids = {entry["paper_id"] for entry in result["skipped"]}
    assert "P1001R0" in skipped_ids
    no_url_skips = [s for s in result["skipped"] if s.get("reason") == "no_url"]
    assert any(s["paper_id"] == "P1001R0" for s in no_url_skips)


def test_run_download_progress_hooks_fire(tmp_path: Path, monkeypatch):
    store = SqliteBackend(tmp_path)
    _seed(store, [
        {"paper_id": "P1000R0", "title": "One", "url": "http://x/a.pdf"},
        {"paper_id": "P1001R0", "title": "Two", "url": "http://x/b.pdf"},
    ])

    monkeypatch.setattr(
        "mailing.download.download_paper",
        _stub_download(returns={
            "P1000R0": (b"%PDF-1", ".pdf"),
            "P1001R0": (b"%PDF-2", ".pdf"),
        }),
    )

    totals: list[int] = []
    progress: list[dict] = []
    asyncio.run(jobs.run_download(
        ["P1000R0", "P1001R0"],
        store,
        on_total=totals.append,
        on_progress=progress.append,
    ))

    assert totals == [2]
    assert {p["paper_id"] for p in progress} == {"P1000R0", "P1001R0"}
