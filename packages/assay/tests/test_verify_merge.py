#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for `_merge_verify` (multi-companion Verify accumulation)."""

from __future__ import annotations

from assay.models import GapResolution, VerifyContradiction, VerifyOutput
from assay.pipeline import _merge_verify


def _close(gap_id: int, quote: str, line: int = 1) -> GapResolution:
    return GapResolution(gap_id=gap_id, evidence_quote=quote, evidence_line=line)


def _contra(source: str, quote: str, line: int = 1, refutes: str = "x") -> VerifyContradiction:
    return VerifyContradiction(source_pid=source, quote=quote, line=line, refutes=refutes)


def test_merge_empty_into_populated_returns_populated():
    populated = VerifyOutput(
        confirmations=["c1"],
        contradictions=[_contra("p1", "q1")],
        new_evidence=["ne1"],
        closes=[_close(1, "ev1")],
    )
    merged = _merge_verify(populated, VerifyOutput())
    assert merged == populated


def test_merge_populated_into_empty_returns_populated():
    populated = VerifyOutput(closes=[_close(2, "ev2")])
    merged = _merge_verify(VerifyOutput(), populated)
    assert merged.closes == populated.closes


def test_merge_dedupes_close_evidence_by_quote():
    a = VerifyOutput(closes=[_close(1, "  Identical evidence text  ")])
    b = VerifyOutput(closes=[
        _close(2, "identical evidence text"),  # case + whitespace duplicate
        _close(3, "Truly different"),
    ])
    merged = _merge_verify(a, b)
    quotes = [c.evidence_quote.strip().lower() for c in merged.closes]
    assert quotes == ["identical evidence text", "truly different"]
    assert [c.gap_id for c in merged.closes] == [1, 3]


def test_merge_dedupes_contradictions_by_triple():
    a = VerifyOutput(contradictions=[_contra("P1", "Quote A", 10)])
    b = VerifyOutput(contradictions=[
        _contra("P1", "  quote a  ", 10),  # same pid/line/quote (case-insensitive)
        _contra("P1", "Quote A", 11),       # different line
        _contra("P2", "Quote A", 10),       # different pid
    ])
    merged = _merge_verify(a, b)
    assert len(merged.contradictions) == 3
    keys = {(c.source_pid, c.line, c.quote.strip().lower()) for c in merged.contradictions}
    assert keys == {
        ("P1", 10, "quote a"),
        ("P1", 11, "quote a"),
        ("P2", 10, "quote a"),
    }


def test_merge_concatenates_confirmations_and_new_evidence():
    a = VerifyOutput(confirmations=["c1"], new_evidence=["ne1"])
    b = VerifyOutput(confirmations=["c2", "c1"], new_evidence=["ne2"])
    merged = _merge_verify(a, b)
    assert merged.confirmations == ["c1", "c2", "c1"]
    assert merged.new_evidence == ["ne1", "ne2"]
