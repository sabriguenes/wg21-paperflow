#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pin the ``write_prompts`` gate in ``jobs.run_convert``.

When the converter produces a non-empty prompt list, ``write_prompts=False``
must skip persisting ``<pid>.prompts.json``. Stub ``convert_one_paper`` so
the gate is exercised regardless of whether the sample paper has uncertain
regions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paperlint import jobs
from paperlint.models import ConvertResult
from paperstore.testing import store  # noqa: F401  (pytest fixture)


def _seed_paper_with_source(backend) -> str:
    pid = "P1234R0"
    backend.upsert_year("2026", [{"paper_id": pid, "title": "Sample"}])
    backend.put_source(pid, b"%PDF-1.4 stub", suffix=".pdf")
    return pid


def _stub_convert(_paper):
    return ConvertResult(
        paper_id="P1234R0",
        markdown="# Sample\n\nbody.\n",
        prompts=["paste me into an LLM verbatim"],
        intent="",
        title="Sample",
        status="ok",
    )


@pytest.mark.parametrize(
    "write_prompts,expect_prompts_file",
    [(True, True), (False, False)],
)
def test_run_convert_write_prompts_gate(
    store, tmp_path: Path, monkeypatch, write_prompts: bool, expect_prompts_file: bool
):
    monkeypatch.setattr(
        "paperlint.orchestrator.convert_one_paper", _stub_convert
    )

    pid = _seed_paper_with_source(store)

    asyncio.run(jobs.run_convert(
        [pid], store, force=True, concurrency=1, write_prompts=write_prompts,
    ))

    prompts_path = tmp_path / "paperstore" / f"{pid.lower()}.prompts.json"
    assert prompts_path.exists() is expect_prompts_file
