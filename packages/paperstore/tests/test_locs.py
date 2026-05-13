#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for SourceLoc and the loc reconstruction helpers."""

from __future__ import annotations

from paperstore import (
    ClaimRow,
    EvidenceRow,
    MarkerRow,
    SourceLoc,
    loc_from_row,
    merged_into_loc,
)


def test_sourceloc_is_frozen_and_hashable():
    a = SourceLoc(line=10, start_char=0, end_char=42)
    b = SourceLoc(line=10, start_char=0, end_char=42)
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_loc_from_row_claim():
    row = ClaimRow(
        paper_id="P1234R0",
        loc_line=7,
        loc_start=0,
        loc_end=120,
        text="x",
        section="s",
        question="q",
    )
    loc = loc_from_row(row)
    assert loc == SourceLoc(line=7, start_char=0, end_char=120)


def test_loc_from_row_evidence():
    row = EvidenceRow(
        paper_id="P1234R0",
        loc_line=11,
        loc_start=2,
        loc_end=80,
        text="x",
        section="s",
        supports="[]",
        quantitative=False,
        cited=False,
        verifiable=False,
        normative=False,
    )
    assert loc_from_row(row) == SourceLoc(line=11, start_char=2, end_char=80)


def test_loc_from_row_marker():
    row = MarkerRow(
        paper_id="P1234R0",
        loc_line=33,
        loc_start=1,
        loc_end=44,
        text="x",
        section="s",
        marker_type="dismissal",
        target="t",
        intensity="moderate",
    )
    assert loc_from_row(row) == SourceLoc(line=33, start_char=1, end_char=44)


def test_merged_into_loc_alive_returns_none():
    row = ClaimRow(
        paper_id="P1234R0",
        loc_line=1,
        loc_start=0,
        loc_end=10,
        text="x",
        section="s",
        question="q",
    )
    assert merged_into_loc(row) is None


def test_merged_into_loc_tombstone_returns_loc():
    row = ClaimRow(
        paper_id="P1234R0",
        loc_line=5,
        loc_start=0,
        loc_end=10,
        text="x",
        section="s",
        question="q",
        merged_into_line=2,
        merged_into_start=1,
        merged_into_end=20,
    )
    assert merged_into_loc(row) == SourceLoc(line=2, start_char=1, end_char=20)
