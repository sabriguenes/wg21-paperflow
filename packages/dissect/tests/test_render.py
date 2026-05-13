#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for rendering functions."""

from __future__ import annotations

from dissect.models import (
    Claim,
    Evidence,
    PipelineState,
    SourceLoc,
    SupportLink,
)
from paperstore.backend import PaperRow
from dissect.render import render_report, render_trace, sanitize_md


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_sanitize_md_balanced_code_span_preserved():
    assert sanitize_md("`std::vector`") == "`std::vector`"


def test_sanitize_md_bare_angle_brackets_escaped():
    assert sanitize_md("vector<int>") == r"vector\<int\>"


def test_sanitize_md_pipes_escaped():
    assert sanitize_md("a | b") == r"a \| b"


def test_sanitize_md_leading_hash_escaped():
    assert sanitize_md("# heading") == r"\# heading"


def test_sanitize_md_unbalanced_asterisk_escaped():
    assert sanitize_md("one * here") == r"one \* here"


def test_sanitize_md_balanced_bold_preserved():
    assert sanitize_md("**bold**") == "**bold**"


def test_sanitize_md_mixed_code_span_and_prose():
    result = sanitize_md("use `std::vector<int>` for this")
    assert "`std::vector<int>`" in result
    assert r"\<" not in result.split("`")[2]


def test_render_report_unsupported():
    state = PipelineState(
        claims=[
            Claim(loc=_loc(1), text="X is fast", original_quotes=["X is fast"],
                  section="3", question="How fast?", depends_on=[]),
        ],
        support_map=[
            SupportLink(claim_loc=_loc(1), evidence_locs=[], status="unsupported"),
        ],
    )
    report = render_report(state, "P0001R0", "Test Paper")
    assert "Unsupported Claims" in report
    assert "How fast?" in report


def test_render_report_supported():
    state = PipelineState(
        claims=[
            Claim(loc=_loc(1), text="X is fast", original_quotes=["X is fast"],
                  section="3", question="How fast?", depends_on=[]),
        ],
        evidence=[
            Evidence(loc=_loc(2), text="measured 5ns", original_quotes=["measured 5ns"],
                     section="4", supports=["X is fast"], quantitative=True,
                     cited=False, verifiable=True, normative=False),
        ],
        support_map=[
            SupportLink(claim_loc=_loc(1), evidence_locs=[_loc(2)],
                        status="directly_supported"),
        ],
    )
    report = render_report(state, "P0001R0", "Test Paper")
    assert "Supported Claims" in report
    assert "How fast?" in report
    assert "measured 5ns" in report


def test_render_report_empty():
    state = PipelineState(claims=[], support_map=[])
    report = render_report(state, "P0001R0", "Test")
    assert "None identified" in report


def test_render_trace_step0():
    state = PipelineState(
        chunks=[],
        citations=[],
    )
    trace = render_trace(state, PaperRow(title="T", paper_id="P0001R0"), 0)
    assert "0. Read" in trace


def test_render_trace_step6():
    state = PipelineState(
        chunks=[],
        citations=[],
        raw_claims=[],
        claims=[],
        raw_evidence=[],
        raw_factual_claims=[],
        evidence=[],
        support_map=[],
        internal_contradictions=[],
    )
    trace = render_trace(state, PaperRow(title="T", paper_id="P0001R0"), 6)
    assert "0. Read" in trace
    assert "1. Extract Normative" in trace
    assert "3. Extract Factual" in trace
    assert "5. Dedup Evidence" in trace
    assert "6. Verify" in trace
