#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Skip-reason routing in ``jobs.run_convert``."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cli import jobs
from cli.models import ConvertResult, Paper
from paperstore.testing import store  # noqa: F401  (pytest fixture)
from tomd.lib.pdf import SkipReason


def _seed_paper_with_source(backend) -> str:
    pid = "P1234R0"
    backend.upsert_year("2026", [{"paper_id": pid, "title": "Sample"}])
    backend.put_source(pid, b"%PDF-1.4 stub", suffix=".pdf")
    return pid


@pytest.mark.parametrize("reason", list(SkipReason))
def test_skip_reason_map_covers_all_enum_members(reason: SkipReason):
    assert reason in jobs._SKIP_REASON_MAP
    bucket = jobs._SKIP_REASON_MAP[reason]
    assert bucket
    assert " " not in bucket


def test_skip_reason_map_buckets_are_unique():
    buckets = list(jobs._SKIP_REASON_MAP.values())
    assert len(buckets) == len(set(buckets))


@pytest.mark.parametrize("reason", list(SkipReason))
def test_run_convert_routes_skip_reason(
    store, tmp_path: Path, monkeypatch, reason: SkipReason,
):
    pid = _seed_paper_with_source(store)
    expected_bucket = jobs._SKIP_REASON_MAP[reason]

    def _stub_convert(paper: Paper, **_kwargs):
        return ConvertResult(
            paper_id=paper.document_id,
            markdown="",
            prompts=["# tomd - Skip\n"],
            intent="",
            title=paper.title,
            images=[],
            status="skipped",
            skip_reason=reason,
        )

    # Patches orchestrator because run_convert lazy-imports convert_one_paper;
    # repoint to cli.jobs.convert_one_paper if that import is hoisted.
    monkeypatch.setattr("cli.orchestrator.convert_one_paper", _stub_convert)

    result = asyncio.run(jobs.run_convert(
        [pid], store, force=True, concurrency=1,
    ))

    skip_entries = [e for e in result["skipped"] if e["paper_id"] == pid]
    assert len(skip_entries) == 1
    assert skip_entries[0]["reason"] == expected_bucket
    assert skip_entries[0]["reason"] != "unreadable_source"
    assert pid not in result["succeeded"]
    assert not any(f.get("paper_id") == pid for f in result["failed"])


def test_run_convert_runtime_error_goes_to_failed(store, monkeypatch):
    pid = _seed_paper_with_source(store)

    def _stub_convert(_paper, **_kwargs):
        raise RuntimeError("oops")

    # Patches orchestrator because run_convert lazy-imports convert_one_paper;
    # repoint to cli.jobs.convert_one_paper if that import is hoisted.
    monkeypatch.setattr("cli.orchestrator.convert_one_paper", _stub_convert)

    result = asyncio.run(jobs.run_convert(
        [pid], store, force=True, concurrency=1,
    ))

    assert pid not in result["succeeded"]
    assert not any(
        e.get("paper_id") == pid and e.get("status") == "skipped"
        for e in result["skipped"]
    )
    failed = [f for f in result["failed"] if f["paper_id"] == pid]
    assert len(failed) == 1
    assert "oops" in failed[0]["error"]
