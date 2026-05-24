#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for the honest-output branch in ``cli._process.run_process_command``.

When ``process_paper`` returns a ``ProcessResult`` with empty
``stages_run``, the CLI prints ``"<pid>: already at <stage> (nothing
to do)"`` instead of the misleading ``"<pid>: <stage>"`` it used to
print unconditionally.

These tests drive ``run_process_command`` with ``through=2`` so the
verb resolves to ``"convert"`` and the printed stage label matches the
original assertion. ``run_process_command`` is the shared driver for
the assay / agora verbs; the convert verb has its own dispatch
path in ``cli.jobs.run_convert``, but the honest-output branch in
``run_process_command`` is independent of the verb gate and is the
contract being exercised here.
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


def _stub_args(target: str) -> argparse.Namespace:
    return SimpleNamespace(
        targets=[target],
        debug=False,
        trace=None,
        force=True,
    )


def test_cli_prints_already_at_when_stages_run_empty(
    backend: SqliteBackend, capsys
):
    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    async def fake(pid, be, **kwargs):
        return ProcessResult(final_status=kwargs.get("through", 2), stages_run=[])

    from cli._process import run_process_command
    with patch("pipeline.process_paper", new=fake):
        rc = run_process_command(_stub_args("P1234R0"), backend, through=2)

    out = capsys.readouterr().out
    assert rc == 0
    assert "P1234R0: already at convert (nothing to do)" in out


def test_cli_prints_stage_name_when_work_ran(
    backend: SqliteBackend, capsys
):
    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    async def fake(pid, be, **kwargs):
        return ProcessResult(
            final_status=kwargs.get("through", 2), stages_run=[1],
        )

    from cli._process import run_process_command
    with patch("pipeline.process_paper", new=fake):
        rc = run_process_command(_stub_args("P1234R0"), backend, through=2)

    out = capsys.readouterr().out
    assert rc == 0
    assert "P1234R0: convert" in out
    assert "already at" not in out
