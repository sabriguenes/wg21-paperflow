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
    RhetoricRow,
    SourceLoc,
    loc_from_row,
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
        uid=1,
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
        uid=1,
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


def test_loc_from_row_rhetoric():
    row = RhetoricRow(
        paper_id="P1234R0",
        uid=1,
        loc_line=33,
        loc_start=1,
        loc_end=44,
        text="x",
        section="s",
        marker_type="dismissal",
        target="t",
        intensity="medium",
    )
    assert loc_from_row(row) == SourceLoc(line=33, start_char=1, end_char=44)
