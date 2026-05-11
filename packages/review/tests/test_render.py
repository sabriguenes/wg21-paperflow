#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for rendering functions."""

from __future__ import annotations

from review.models import (
    Claim,
    Evidence,
    PipelineState,
    SourceLoc,
    SupportLink,
)
from review.render import render_report, render_trace, safe_quote


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_safe_quote_simple():
    assert safe_quote("hello") == '"hello"'


def test_safe_quote_with_code_fence():
    text = "before```python\ncode()```after"
    result = safe_quote(text)
    assert "```" in result
    assert '"before"' in result


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
    assert "X is fast" in report
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
    assert "X is fast" in report
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
    trace = render_trace(state, {"title": "T", "paper_id": "P0001R0"}, 0)
    assert "0. Read" in trace


def test_render_trace_step5():
    state = PipelineState(
        chunks=[],
        citations=[],
        raw_claims=[],
        claims=[],
        raw_evidence=[],
        evidence=[],
        support_map=[],
        internal_contradictions=[],
    )
    trace = render_trace(state, {"title": "T", "paper_id": "P0001R0"}, 5)
    assert "5. Verify" in trace
    assert "0. Read" in trace
