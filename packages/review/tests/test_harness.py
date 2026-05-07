#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the pure-Python code harness.

All tests use synthetic data — no LLM, no paperstore, no fixtures.
"""

from __future__ import annotations

from review.harness import (
    build_newline_offsets,
    chunk_paper,
    dedup_tier0,
    dedup_tier1,
    find_loc,
    promote_claims,
    promote_evidence,
)
from review.models import Claim, Evidence, RawClaim, RawEvidence, SourceLoc


SAMPLE = "line one\nline two\nline three\n"


def test_build_newline_offsets():
    offsets = build_newline_offsets(SAMPLE)
    assert offsets == [8, 17, 28]


def test_find_loc_exact_match():
    offsets = build_newline_offsets(SAMPLE)
    loc = find_loc("line two", SAMPLE, offsets)
    assert loc is not None
    assert loc.line == 2
    assert loc.start_char == 0
    assert loc.end_char == 7


def test_find_loc_mid_line():
    offsets = build_newline_offsets(SAMPLE)
    loc = find_loc("two", SAMPLE, offsets)
    assert loc is not None
    assert loc.line == 2
    assert loc.start_char == 5
    assert loc.end_char == 7


def test_find_loc_not_found():
    offsets = build_newline_offsets(SAMPLE)
    loc = find_loc("nonexistent", SAMPLE, offsets)
    assert loc is None


def test_find_loc_first_line():
    offsets = build_newline_offsets(SAMPLE)
    loc = find_loc("line one", SAMPLE, offsets)
    assert loc is not None
    assert loc.line == 1
    assert loc.start_char == 0


def test_chunk_paper_small():
    small = "# Title\n\nShort paper."
    chunks = chunk_paper(small, max_chars=70_000)
    assert len(chunks) == 1
    assert chunks[0].text == small
    assert chunks[0].line_offset == 1


def test_chunk_paper_splits_at_headings():
    lines = []
    for i in range(20):
        lines.append(f"## Section {i}\n")
        lines.append("x" * 4000 + "\n")
    paper = "".join(lines)
    chunks = chunk_paper(paper, max_chars=10_000)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 15_000  # allow overlap margin


def test_chunk_paper_overlap():
    lines = []
    for i in range(10):
        lines.append(f"## Section {i}\n")
        lines.append("content " * 1000 + "\n")
    paper = "".join(lines)
    chunks = chunk_paper(paper, max_chars=10_000)
    if len(chunks) >= 2:
        assert chunks[1].line_offset < chunks[0].line_offset + len(chunks[0].text.splitlines())


def test_promote_claims_basic():
    source = "This is claim A. This is claim B."
    raws = [
        RawClaim(text="claim A", original_quotes=["claim A"], section="1", question="Q1?", depends_on=[]),
        RawClaim(text="claim B", original_quotes=["claim B"], section="1", question="Q2?", depends_on=["claim A"]),
    ]
    claims = promote_claims(raws, source)
    assert len(claims) == 2
    assert claims[0].loc.line == 1
    assert claims[1].depends_on == [claims[0].loc]


def test_promote_claims_drops_unfound():
    source = "Only this text exists."
    raws = [
        RawClaim(text="nonexistent", original_quotes=["nonexistent"], section="1", question="Q?", depends_on=[]),
    ]
    claims = promote_claims(raws, source)
    assert len(claims) == 0


def test_promote_evidence_basic():
    source = "Evidence text here."
    raws = [
        RawEvidence(
            text="Evidence text", original_quotes=["Evidence text"], section="1",
            supports=["something"], quantitative=False, cited=False,
            verifiable=False, normative=False,
        ),
    ]
    evidence = promote_evidence(raws, source)
    assert len(evidence) == 1
    assert evidence[0].loc.line == 1


def test_dedup_tier0_tombstones_duplicate_locs():
    loc = SourceLoc(line=1, start_char=0, end_char=5)
    c1 = Claim(loc=loc, text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[])
    c2 = Claim(loc=loc, text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[])
    result = dedup_tier0([c1, c2])
    assert result[0].merged_into is None
    assert result[1].merged_into == loc


def test_dedup_tier0_preserves_distinct():
    c1 = Claim(
        loc=SourceLoc(line=1, start_char=0, end_char=5),
        text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[],
    )
    c2 = Claim(
        loc=SourceLoc(line=2, start_char=0, end_char=5),
        text="B", original_quotes=["B"], section="1", question="Q?", depends_on=[],
    )
    result = dedup_tier0([c1, c2])
    assert all(r.merged_into is None for r in result)


def test_dedup_tier1_substring_tombstone():
    c_short = Claim(
        loc=SourceLoc(line=1, start_char=0, end_char=2),
        text="AB", original_quotes=["AB"], section="1", question="Q?", depends_on=[],
    )
    c_long = Claim(
        loc=SourceLoc(line=1, start_char=0, end_char=5),
        text="XABYZ", original_quotes=["XABYZ"], section="1", question="Q?", depends_on=[],
    )
    result = dedup_tier1([c_short, c_long])
    short_result = next(r for r in result if r.loc == c_short.loc)
    long_result = next(r for r in result if r.loc == c_long.loc)
    assert short_result.merged_into == c_long.loc
    assert "AB" in long_result.original_quotes


def test_dedup_tier1_no_self_merge():
    c = Claim(
        loc=SourceLoc(line=1, start_char=0, end_char=5),
        text="exact", original_quotes=["exact"], section="1", question="Q?", depends_on=[],
    )
    result = dedup_tier1([c])
    assert result[0].merged_into is None
