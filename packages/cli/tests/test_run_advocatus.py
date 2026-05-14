#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Integration smoke for `paperflow advocatus` dispatch.

Stubs out the LLM call (``advocatus.advocatus_paper``) so the test runs
without network or API keys. Verifies that the verb wiring routes to
the advocatus command module and that the resulting Relatio gets
written to the paperstore via ``backend.write_advocatus_md``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from paperstore import SqliteBackend


@pytest.fixture
def backend(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


def _stub_args(target: str, debug: bool = False, trace=None) -> argparse.Namespace:
    return SimpleNamespace(
        targets=[target],
        debug=debug,
        trace=trace,
    )


def test_command_writes_relatio_via_backend(backend: SqliteBackend, capsys):
    """Single-paper happy path: stub returns the Relatio, CLI writes it."""
    from cli.advocatus import command

    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    async def fake_advocatus_paper(pid, backend, **kwargs):
        return f"# Relatio for {pid}\n\n*Nihil obstat*\n"

    with patch("advocatus.advocatus_paper", new=fake_advocatus_paper):
        rc = command(_stub_args("P1234R0"), backend)

    assert rc == 0
    out_path = backend.get_advocatus_path("P1234R0")
    assert out_path.exists()
    assert "Nihil obstat" in out_path.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "Relatio written to" in captured.out


def test_command_handles_pipeline_error(backend: SqliteBackend, capsys):
    """When advocatus_paper raises a non-prereq PipelineError, CLI exits 1."""
    from pipeline.errors import PipelineError
    from cli.advocatus import command

    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    async def fake_advocatus_paper(pid, backend, **kwargs):
        raise PipelineError(f"something broke for {pid}")

    with patch("advocatus.advocatus_paper", new=fake_advocatus_paper):
        rc = command(_stub_args("P1234R0"), backend)

    assert rc == 1
    captured = capsys.readouterr()
    assert "Advocatus failed" in captured.err


def test_command_batch_dispatch(backend: SqliteBackend, capsys):
    """Year-month target routes to advocatus_since after run_full."""
    from cli.advocatus import command

    captured_months: list[str] = []

    async def fake_advocatus_since(month, backend, **kwargs):
        captured_months.append(month)
        return [
            {"paper_id": "P1", "status": "ok", "error": None},
            {"paper_id": "P2", "status": "error", "error": "boom"},
        ]

    async def fake_run_full(targets, backend, **kwargs):
        return {}

    with (
        patch("advocatus.advocatus_since", new=fake_advocatus_since),
        patch("cli.jobs.run_full", new=fake_run_full),
    ):
        rc = command(_stub_args("2026-01"), backend)

    assert captured_months == ["2026-01"]
    assert rc == 1
    captured = capsys.readouterr()
    assert "1 succeeded, 1 failed" in captured.out
    assert "FAILED: P2" in captured.err


def test_command_stop_after_writes_trace(backend: SqliteBackend, capsys):
    """With --trace N (stop_after set), CLI writes the returned trace
    string to backend.get_trace_md_path(pid, 'advocatus')."""
    from cli.advocatus import command

    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    async def fake_advocatus_paper(pid, backend, **kwargs):
        # When stop_after is set, advocatus_paper returns the partial
        # trace string; the CLI is responsible for writing it.
        return "# Trace: P1234R0\n\n## 0. Load\n\n- 0 articuli\n"

    with patch("advocatus.advocatus_paper", new=fake_advocatus_paper):
        rc = command(_stub_args("P1234R0", trace=3), backend)

    assert rc == 0
    trace_path = backend.get_trace_md_path("P1234R0", "advocatus")
    assert trace_path.exists()
    assert "Trace: P1234R0" in trace_path.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "Trace written to" in captured.out
