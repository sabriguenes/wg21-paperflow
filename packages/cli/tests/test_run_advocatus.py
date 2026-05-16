#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Integration smoke for `paperflow advocatus` dispatch.

Stubs out ``process_paper`` so tests run without network or API keys.
Verifies that the verb wiring routes through ``run_process_command``
with the correct ``through`` value.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from paperstore import SqliteBackend
from pipeline import ProcessResult


@pytest.fixture
def backend(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


def _stub_args(target: str, debug: bool = False, trace=None, force=False) -> argparse.Namespace:
    return SimpleNamespace(
        targets=[target],
        debug=debug,
        trace=trace,
        force=force,
    )


def test_command_calls_process_paper_with_through_4(backend: SqliteBackend):
    """advocatus command calls process_paper with through=4."""
    from cli.advocatus import command

    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    calls = []

    async def fake_process_paper(pid, be, **kwargs):
        calls.append({"pid": pid, "through": kwargs.get("through")})
        return ProcessResult(final_status=kwargs.get("through", 4), stages_run=[3])

    with patch("pipeline.process_paper", new=fake_process_paper):
        rc = command(_stub_args("P1234R0"), backend)

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["pid"] == "P1234R0"
    assert calls[0]["through"] == 4


def test_command_handles_pipeline_error(backend: SqliteBackend, capsys):
    """When process_paper raises PipelineError, CLI exits 1."""
    from pipeline.errors import PipelineError
    from cli.advocatus import command

    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    async def fake_process_paper(pid, be, **kwargs):
        raise PipelineError(f"something broke for {pid}")

    with patch("pipeline.process_paper", new=fake_process_paper):
        rc = command(_stub_args("P1234R0"), backend)

    assert rc == 1
    captured = capsys.readouterr()
    assert "P1234R0" in captured.err


def test_command_batch_processes_month(backend: SqliteBackend, capsys):
    """Year-month target processes all papers in that mailing."""
    from cli.advocatus import command

    backend.upsert_year("2026", [
        {"paper_id": "P1000R0", "mailing_date": "2026-01"},
        {"paper_id": "P2000R0", "mailing_date": "2026-01"},
    ])

    calls = []

    async def fake_process_paper(pid, be, **kwargs):
        calls.append(pid)
        return ProcessResult(final_status=4, stages_run=[3])

    with patch("pipeline.process_paper", new=fake_process_paper):
        rc = command(_stub_args("2026-01"), backend)

    assert len(calls) == 2
    assert set(calls) == {"P1000R0", "P2000R0"}


def test_command_skips_papers_already_done(backend: SqliteBackend):
    """Papers with status >= through are skipped."""
    from cli.advocatus import command

    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])
    backend.advance_status("P1234R0", 0, 4)

    calls = []

    async def fake_process_paper(pid, be, **kwargs):
        calls.append(pid)
        return ProcessResult(final_status=4, stages_run=[])

    with patch("pipeline.process_paper", new=fake_process_paper):
        rc = command(_stub_args("P1234R0"), backend)

    assert calls == []
    assert rc == 0
