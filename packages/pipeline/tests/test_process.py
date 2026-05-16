#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for ``pipeline.process.process_paper``.

Focus on the artifact-rewind behavior introduced alongside
``truthful_status`` and the ``ProcessResult`` return type. Each
``_stage_*`` is monkey-patched to a stub that touches the right
backend write so the postcondition check passes without invoking the
real per-stage pipelines.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paperstore import SqliteBackend
from paperstore.stages import STAGES
from pipeline import ProcessResult, process_paper
from pipeline import process as process_mod


@pytest.fixture
def backend(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


@pytest.fixture
def staged_paper(backend: SqliteBackend) -> str:
    pid = "P1234R0"
    backend.upsert_year("2026", [
        {"paper_id": pid, "url": "https://example.com/p1234r0.pdf"},
    ])
    return pid


@pytest.fixture(autouse=True)
def _patch_stage_bodies(monkeypatch, backend: SqliteBackend):
    """Replace each ``_stage_*`` body with a backend-write stub.

    The real stages reach into mailing/tomd/dissect/etc. The
    postcondition checks at the top of each stage stay live (they
    short-circuit before our stub runs), so this is sufficient to
    exercise rewind + ProcessResult.
    """
    async def fake_download(pid, be, *, on_progress=None):
        from pipeline.postconditions import postcondition_satisfied
        if postcondition_satisfied(be, pid, STAGES["download"]):
            return
        be.put_source(pid, b"PDF", suffix=".pdf")

    async def fake_convert(pid, be):
        from pipeline.postconditions import postcondition_satisfied
        if postcondition_satisfied(be, pid, STAGES["convert"]):
            return
        be.write_paper_md(pid, "# md\n")

    async def fake_dissect(pid, be, **kwargs):
        from pipeline.postconditions import postcondition_satisfied
        if postcondition_satisfied(be, pid, STAGES["dissect"]):
            return
        be.write_dissect_md(pid, "# dissect\n")

    monkeypatch.setattr(process_mod, "_stage_download", fake_download)
    monkeypatch.setattr(process_mod, "_stage_convert", fake_convert)
    monkeypatch.setattr(process_mod, "_stage_dissect", fake_dissect)


def test_process_paper_returns_process_result(
    backend: SqliteBackend, staged_paper: str
):
    result = asyncio.run(process_paper(staged_paper, backend, through=2))
    assert isinstance(result, ProcessResult)
    assert result.final_status == 2
    assert result.stages_run == [STAGES["download"], STAGES["convert"]]


def test_process_paper_empty_stages_run_when_idempotent(
    backend: SqliteBackend, staged_paper: str
):
    asyncio.run(process_paper(staged_paper, backend, through=2))
    # Second invocation: artifacts intact, status already at 2.
    result = asyncio.run(process_paper(staged_paper, backend, through=2))
    assert result.stages_run == []
    assert result.final_status == 2


def test_process_paper_reruns_convert_when_md_deleted(
    backend: SqliteBackend, staged_paper: str
):
    asyncio.run(process_paper(staged_paper, backend, through=2))
    md_path = Path(backend.get_meta(staged_paper).markdown_path)
    md_path.unlink()
    assert not md_path.exists()

    result = asyncio.run(process_paper(staged_paper, backend, through=2))

    # Convert ran again; download did not (its artifact is intact).
    assert STAGES["convert"] in result.stages_run
    assert STAGES["download"] not in result.stages_run
    assert md_path.exists()
    assert backend.get_meta(staged_paper).status == 2


def test_process_paper_rewinds_when_status_overstates_truth(
    backend: SqliteBackend, staged_paper: str
):
    # Stage download + convert artifacts, claim status=3 (dissect-done)
    # but no dissect artifact, and delete the markdown.
    backend.put_source(staged_paper, b"PDF", suffix=".pdf")
    md = backend.write_paper_md(staged_paper, "# md\n")
    backend.advance_status(staged_paper, 0, 3)
    Path(md).unlink()

    # Verb: convert. truthful_status floors to 1; loop runs convert.
    result = asyncio.run(process_paper(staged_paper, backend, through=2))

    assert STAGES["convert"] in result.stages_run
    assert backend.get_meta(staged_paper).status == 2
    assert Path(backend.get_meta(staged_paper).markdown_path).exists()


def test_process_paper_force_then_truthful_compose(
    backend: SqliteBackend, staged_paper: str
):
    # Status=2 with artifacts intact; force=True pins to through-1=1
    # (need convert again), truthful keeps it at 1.
    asyncio.run(process_paper(staged_paper, backend, through=2))
    result = asyncio.run(
        process_paper(staged_paper, backend, through=2, force=True)
    )
    assert result.stages_run == [STAGES["convert"]]
