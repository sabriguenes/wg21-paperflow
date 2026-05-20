#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for agora pipeline guards, subreddit routing, and Step 0 fail-fast."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from paperstore import SqliteBackend

from agora import agora_paper
from pipeline.errors import (
    PaperNotConvertedError,
    PaperNotFoundError,
)
from agora.models import PipelineState
from agora.pipeline import (
    _guard_encounter_count_positive,
    _route_subreddit,
    _split_paper_id,
)


@pytest.fixture(autouse=True)
def _placeholder_api_keys(monkeypatch):
    # Tests in this module invoke ``agora_paper`` to exercise error
    # paths unrelated to authentication. ``pipeline.resolve_slots``
    # now validates env vars at slot-binding; placeholder values let
    # the validation pass so the test reaches the path it actually
    # cares about. The fail-fast contract itself is covered in
    # ``packages/pipeline/tests/test_services.py``.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-for-tests")
    monkeypatch.setenv("RUNPOD_API_KEY", "placeholder-for-tests")


def test_guard_encounter_count_skips_when_zero():
    s = PipelineState(encounter_count=0)
    assert _guard_encounter_count_positive(s) is False


def test_guard_encounter_count_skips_when_none():
    s = PipelineState()
    assert _guard_encounter_count_positive(s) is False


def test_guard_encounter_count_runs_when_positive():
    s = PipelineState(encounter_count=2)
    assert _guard_encounter_count_positive(s) is True


@pytest.mark.parametrize(
    "audience,expected",
    [
        ("EWG", "r/ewg"),
        ("EWGI", "r/ewg"),
        ("LEWG", "r/lewg"),
        ("LEWGI", "r/lewg"),
        ("CWG", "r/cwg"),
        ("LWG", "r/lwg"),
        ("SG21", "r/ewg"),
        ("Plenary", "r/ewg"),
        ("EWG, LEWG", "r/ewg"),  # first wins
        ("LEWG/LEWGI", "r/lewg"),
        ("", "r/ewg"),  # default
        ("unknown", "r/ewg"),
    ],
)
def test_route_subreddit(audience: str, expected: str):
    assert _route_subreddit(audience) == expected


@pytest.mark.parametrize(
    "pid,expected",
    [
        ("P4003R2", ("P4003", 2)),
        ("p4003r0", ("P4003", 0)),
        ("P12345R14", ("P12345", 14)),
        ("D1234R0", ("D1234R0", 0)),  # D-prefix: not a P-paper, no split
    ],
)
def test_split_paper_id(pid: str, expected: tuple[str, int]):
    assert _split_paper_id(pid) == expected


def test_agora_paper_unknown_pid_raises(tmp_path: Path):
    backend = SqliteBackend(tmp_path)
    with pytest.raises(PaperNotFoundError):
        asyncio.run(agora_paper("PXXXXR0", backend))


def test_agora_paper_not_converted_raises(tmp_path: Path):
    backend = SqliteBackend(tmp_path)
    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])
    with pytest.raises(PaperNotConvertedError):
        asyncio.run(agora_paper("P1234R0", backend))


def test_paperstore_agora_round_trip(tmp_path: Path):
    """write_agora_json/read_agora_json/get_agora_path/clear_agora behave."""
    backend = SqliteBackend(tmp_path)
    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])
    payload = {"document": "P1234R0", "replies": []}
    out_path = backend.write_agora_json("P1234R0", payload)
    assert out_path.exists()
    assert out_path.name == "p1234r0.agora.json"

    got = backend.read_agora_json("P1234R0")
    assert got == payload

    p = backend.get_agora_path("P1234R0")
    assert p == out_path

    backend.clear_agora("P1234R0")
    assert not p.exists()
