#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the CLI's honest-output branch in ``run_process_command``.

When ``process_paper`` returns a ``ProcessResult`` with empty
``stages_run``, the CLI prints ``"<pid>: already at <stage> (nothing
to do)"`` instead of the misleading ``"<pid>: <stage>"`` it used to
print unconditionally.
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
        force=True,  # bypass the "p.status < through" filter
    )


def test_cli_prints_already_at_when_stages_run_empty(
    backend: SqliteBackend, capsys
):
    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])

    async def fake(pid, be, **kwargs):
        return ProcessResult(final_status=kwargs.get("through", 2), stages_run=[])

    from cli.convert import command
    with patch("pipeline.process_paper", new=fake):
        rc = command(_stub_args("P1234R0"), backend)

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

    from cli.convert import command
    with patch("pipeline.process_paper", new=fake):
        rc = command(_stub_args("P1234R0"), backend)

    out = capsys.readouterr().out
    assert rc == 0
    assert "P1234R0: convert" in out
    assert "already at" not in out
