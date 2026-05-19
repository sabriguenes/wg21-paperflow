#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for the convert batch-confirmation gate and end-of-batch summaries.

Plan section 3.1: when a multi-paper convert invocation would
invalidate downstream artifacts, the CLI prompts the user once
before doing any work. ``--yes`` skips the prompt; a non-TTY
invocation skips it (so scripts and CI don't hang); single-paper
invocations skip it (deliberate user action).

The end-of-batch summary block prints two independent reports:
truncation (papers that hit the 20-image cap) and invalidation
(papers whose downstream artifacts were wiped because the
markdown content changed).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from paperstore import SqliteBackend
from pipeline import ConvertReport, ProcessResult


@pytest.fixture
def backend(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


def _stub_args(
    targets: list[str],
    *,
    yes: bool = False,
    keep_downstream: bool = False,
) -> argparse.Namespace:
    return SimpleNamespace(
        targets=targets,
        debug=False,
        trace=None,
        force=True,
        yes=yes,
        keep_downstream=keep_downstream,
    )


def _seed(backend: SqliteBackend, pid: str, *,
          with_md: bool = False,
          with_dissect: bool = False,
          with_advocatus: bool = False,
          with_agora: bool = False) -> None:
    backend.upsert_year("2026", [{"paper_id": pid, "title": pid}])
    backend.put_source(pid, b"%PDF stub", suffix=".pdf")
    if with_md:
        backend.write_paper_md(pid, "# body\n")
    if with_dissect:
        backend.write_dissect_md(pid, "# dissect\n")
    if with_advocatus:
        backend.write_advocatus_md(pid, "# advocatus\n")
    if with_agora:
        backend.write_agora_json(pid, {"x": 1})


def _run_convert(
    backend: SqliteBackend,
    args: argparse.Namespace,
    *,
    fake_process_paper,
    stdin_isatty: bool = True,
    stdin_answer: str = "n",
) -> int:
    """Invoke the convert command with a stubbed process_paper.

    Patches the stdin TTY check and the ``input()`` builtin so the
    prompt path is exercised hermetically.
    """
    from cli.convert import command

    with (
        patch("pipeline.process_paper", new=fake_process_paper),
        patch("sys.stdin.isatty", return_value=stdin_isatty),
        patch("builtins.input", return_value=stdin_answer),
    ):
        return command(args, backend)


async def _noop_process_paper(pid, backend, **kwargs):
    return ProcessResult(final_status=kwargs.get("through", 2), stages_run=[1])


# ---- preflight prompt -------------------------------------------------------


def test_single_paper_invocation_skips_prompt(
    backend: SqliteBackend, capsys,
):
    """Single paper, deliberate user action, no prompt regardless of
    downstream state."""
    _seed(backend, "P1234R0",
          with_md=True, with_dissect=True, with_advocatus=True)

    rc = _run_convert(
        backend, _stub_args(["P1234R0"]),
        fake_process_paper=_noop_process_paper,
        stdin_answer="n",
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "invalidate downstream" not in out
    assert "Continue?" not in out


def test_batch_with_no_downstream_skips_prompt(
    backend: SqliteBackend, capsys,
):
    """A multi-paper batch where no paper has any downstream artifacts
    doesn't prompt."""
    _seed(backend, "P1", with_md=True)
    _seed(backend, "P2", with_md=True)

    rc = _run_convert(
        backend, _stub_args(["2026"]),
        fake_process_paper=_noop_process_paper,
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "invalidate downstream" not in out


def test_batch_with_downstream_prompts(backend: SqliteBackend, capsys):
    """Batch of papers, at least one with downstream artifacts AND
    existing markdown: prompt fires."""
    _seed(backend, "P1", with_md=True, with_dissect=True)
    _seed(backend, "P2", with_md=True, with_advocatus=True)
    _seed(backend, "P3", with_md=True)   # no downstream

    rc = _run_convert(
        backend, _stub_args(["2026"]),
        fake_process_paper=_noop_process_paper,
        stdin_answer="n",
    )

    out = capsys.readouterr().out
    assert "invalidate downstream artifacts for 2 paper(s)" in out
    assert "dissect outputs:   1" in out
    assert "advocatus outputs: 1" in out
    # User declined.
    assert "aborted" in out
    assert rc == 0


def test_prompt_accepts_y_and_proceeds(backend: SqliteBackend, capsys):
    _seed(backend, "P1", with_md=True, with_dissect=True)
    _seed(backend, "P2", with_md=True, with_dissect=True)

    rc = _run_convert(
        backend, _stub_args(["2026"]),
        fake_process_paper=_noop_process_paper,
        stdin_answer="y",
    )

    out = capsys.readouterr().out
    assert "invalidate downstream artifacts for 2 paper(s)" in out
    assert "aborted" not in out
    assert rc == 0


def test_yes_flag_skips_prompt(backend: SqliteBackend, capsys):
    _seed(backend, "P1", with_md=True, with_dissect=True)
    _seed(backend, "P2", with_md=True, with_dissect=True)

    rc = _run_convert(
        backend, _stub_args(["2026"], yes=True),
        fake_process_paper=_noop_process_paper,
        stdin_answer="n",   # ignored
    )

    out = capsys.readouterr().out
    assert "Continue?" not in out
    assert "aborted" not in out
    assert rc == 0


def test_non_tty_skips_prompt(backend: SqliteBackend, capsys):
    """Non-interactive (cron, CI) invocations don't hang on input()."""
    _seed(backend, "P1", with_md=True, with_dissect=True)
    _seed(backend, "P2", with_md=True, with_dissect=True)

    rc = _run_convert(
        backend, _stub_args(["2026"]),
        fake_process_paper=_noop_process_paper,
        stdin_isatty=False,
    )

    out = capsys.readouterr().out
    assert "Continue?" not in out
    assert "aborted" not in out
    assert rc == 0


def test_keep_downstream_skips_prompt(backend: SqliteBackend, capsys):
    """--keep-downstream means we don't clear anything, so no prompt."""
    _seed(backend, "P1", with_md=True, with_dissect=True)
    _seed(backend, "P2", with_md=True, with_dissect=True)

    rc = _run_convert(
        backend, _stub_args(["2026"], keep_downstream=True),
        fake_process_paper=_noop_process_paper,
        stdin_answer="n",
    )

    out = capsys.readouterr().out
    assert "Continue?" not in out
    assert rc == 0


# ---- end-of-batch summaries -------------------------------------------------


def test_truncation_summary_lists_capped_papers(
    backend: SqliteBackend, capsys,
):
    _seed(backend, "P1", with_md=True)
    _seed(backend, "P2", with_md=True)

    async def fake(pid, be, **kwargs):
        return ProcessResult(
            final_status=2,
            stages_run=[1],
            convert_report=ConvertReport(
                images_kept=20,
                source_image_count=64 if pid == "P1" else 107,
                images_truncated=True,
            ),
        )

    _run_convert(backend, _stub_args(["2026"]), fake_process_paper=fake)

    out = capsys.readouterr().out
    assert "convert: 2 paper(s) truncated to the 20-image cap:" in out
    assert "P1 (kept 20 of 64)" in out
    assert "P2 (kept 20 of 107)" in out


def test_invalidation_summary_lists_cleared_pipelines(
    backend: SqliteBackend, capsys,
):
    _seed(backend, "P1", with_md=True, with_dissect=True)
    _seed(backend, "P2", with_md=True, with_dissect=True, with_advocatus=True)

    async def fake(pid, be, **kwargs):
        cleared = ("dissect",) if pid == "P1" else ("dissect", "advocatus")
        return ProcessResult(
            final_status=2,
            stages_run=[1],
            convert_report=ConvertReport(
                downstream_cleared=cleared,
            ),
        )

    _run_convert(
        backend, _stub_args(["2026"], yes=True), fake_process_paper=fake,
    )

    out = capsys.readouterr().out
    assert "convert: 2 paper(s) had downstream artifacts invalidated" in out
    assert "P1 (dissect)" in out
    assert "P2 (dissect, advocatus)" in out
    assert "re-run `paperflow dissect`" in out


def test_no_summary_when_nothing_to_report(
    backend: SqliteBackend, capsys,
):
    """A batch where nobody truncated and nobody cleared downstream
    produces no summary blocks - just the ok/failed count."""
    _seed(backend, "P1", with_md=True)
    _seed(backend, "P2", with_md=True)

    async def fake(pid, be, **kwargs):
        return ProcessResult(
            final_status=2,
            stages_run=[1],
            convert_report=ConvertReport(),  # all zeros
        )

    _run_convert(backend, _stub_args(["2026"]), fake_process_paper=fake)

    out = capsys.readouterr().out
    assert "truncated to" not in out
    assert "invalidated by re-convert" not in out
    assert "2 succeeded" in out
