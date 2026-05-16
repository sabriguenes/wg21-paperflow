#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for ``pipeline.postconditions``.

Cover the artifact-existence check for every stage and the
``truthful_status`` floor-to-first-gap algorithm. Uses a real
``SqliteBackend`` against ``tmp_path`` so we exercise the actual
``get_meta`` / path-column wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paperstore import SqliteBackend
from paperstore.stages import STAGES
from pipeline.postconditions import postcondition_satisfied, truthful_status


@pytest.fixture
def backend(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


@pytest.fixture
def staged_paper(backend: SqliteBackend) -> str:
    """Insert one paper row and return its id. No artifacts yet."""
    pid = "P1234R0"
    backend.upsert_year("2026", [{"paper_id": pid, "url": "https://example.com/p1234r0.pdf"}])
    return pid


def test_postcondition_download_false_when_no_source(
    backend: SqliteBackend, staged_paper: str
):
    assert not postcondition_satisfied(backend, staged_paper, STAGES["download"])


def test_postcondition_download_true_after_put_source(
    backend: SqliteBackend, staged_paper: str
):
    backend.put_source(staged_paper, b"PDF bytes", suffix=".pdf")
    assert postcondition_satisfied(backend, staged_paper, STAGES["download"])


def test_postcondition_download_false_when_file_deleted(
    backend: SqliteBackend, staged_paper: str
):
    backend.put_source(staged_paper, b"PDF bytes", suffix=".pdf")
    src = backend.get_meta(staged_paper).source_file
    Path(src).unlink()
    assert not postcondition_satisfied(backend, staged_paper, STAGES["download"])


def test_postcondition_convert_true_when_md_exists(
    backend: SqliteBackend, staged_paper: str
):
    backend.write_paper_md(staged_paper, "# Hello\n")
    assert postcondition_satisfied(backend, staged_paper, STAGES["convert"])


def test_postcondition_convert_false_when_md_path_empty(
    backend: SqliteBackend, staged_paper: str
):
    assert not postcondition_satisfied(backend, staged_paper, STAGES["convert"])


def test_postcondition_convert_false_when_md_path_set_but_file_deleted(
    backend: SqliteBackend, staged_paper: str
):
    md = backend.write_paper_md(staged_paper, "# Hello\n")
    Path(md).unlink()
    assert not postcondition_satisfied(backend, staged_paper, STAGES["convert"])


def test_postcondition_dissect_true_when_dissect_md_exists(
    backend: SqliteBackend, staged_paper: str
):
    backend.write_dissect_md(staged_paper, "# Dissect\n")
    assert postcondition_satisfied(backend, staged_paper, STAGES["dissect"])


def test_postcondition_herald_and_ready_are_no_op_true(
    backend: SqliteBackend, staged_paper: str
):
    assert postcondition_satisfied(backend, staged_paper, STAGES["herald"])
    assert postcondition_satisfied(backend, staged_paper, STAGES["ready"])


def test_truthful_status_no_rewind_when_all_present(
    backend: SqliteBackend, staged_paper: str
):
    backend.put_source(staged_paper, b"PDF", suffix=".pdf")
    backend.write_paper_md(staged_paper, "# md\n")
    backend.write_dissect_md(staged_paper, "# dissect\n")
    assert truthful_status(backend, staged_paper, claimed=3) == 3


def test_truthful_status_floors_to_missing_gap(
    backend: SqliteBackend, staged_paper: str
):
    backend.put_source(staged_paper, b"PDF", suffix=".pdf")
    # convert artifact path set, file deleted.
    md = backend.write_paper_md(staged_paper, "# md\n")
    Path(md).unlink()
    # claim dissect-done despite missing markdown
    assert truthful_status(backend, staged_paper, claimed=3) == 1


def test_truthful_status_floors_to_zero_when_nothing_staged(
    backend: SqliteBackend, staged_paper: str
):
    assert truthful_status(backend, staged_paper, claimed=2) == 0


def test_truthful_status_returns_claimed_when_zero(
    backend: SqliteBackend, staged_paper: str
):
    # claimed=0 means no stages have been claimed; nothing to verify.
    assert truthful_status(backend, staged_paper, claimed=0) == 0


def test_truthful_status_caps_at_ready(
    backend: SqliteBackend, staged_paper: str
):
    # Pathological claimed value beyond the stage table. Should not
    # walk past STAGES["ready"].
    backend.put_source(staged_paper, b"PDF", suffix=".pdf")
    backend.write_paper_md(staged_paper, "# md\n")
    backend.write_dissect_md(staged_paper, "# d\n")
    # Stages 3..6 have no artifacts; the FIRST missing is advocatus (3).
    assert truthful_status(backend, staged_paper, claimed=999) == STAGES["advocatus"]
