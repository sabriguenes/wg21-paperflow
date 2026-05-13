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

from dissect.harness import (
    chunk_paper,
    dedup_tier0,
    dedup_tier1,
    extract_citations,
    number_lines,
    promote_claims,
    promote_evidence,
    promote_markers,
)
from dissect.models import Chunk, Claim, RawClaim, RawEvidence, RawMarker, SourceLoc


def test_number_lines():
    chunk = Chunk(text="line one\nline two\nline three", line_offset=10)
    result = number_lines(chunk)
    assert result == "10| line one\n11| line two\n12| line three"


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


def test_chunk_paper_overlap():
    lines = []
    for i in range(10):
        lines.append(f"## Section {i}\n")
        lines.append("content " * 1000 + "\n")
    paper = "".join(lines)
    chunks = chunk_paper(paper, max_chars=10_000)
    if len(chunks) >= 2:
        assert chunks[1].line_offset < chunks[0].line_offset + len(chunks[0].text.splitlines())


def test_promote_claims_uses_start_line():
    source = "line one\nline two\nline three"
    raws = [
        RawClaim(text="line two", start_line=2, section="1", question="Q?"),
    ]
    claims = promote_claims(raws, source)
    assert len(claims) == 1
    assert claims[0].loc.line == 2


def test_promote_claims_never_drops():
    source = "Only this text exists."
    raws = [
        RawClaim(text="nonexistent quote", start_line=1, section="1", question="Q?"),
    ]
    claims = promote_claims(raws, source)
    assert len(claims) == 1


def test_promote_claims_preserves_kind():
    source = "factual claim text"
    raws = [
        RawClaim(text="factual claim text", start_line=1, section="1",
                 question="Q?", kind="factual"),
    ]
    claims = promote_claims(raws, source)
    assert len(claims) == 1
    assert claims[0].kind == "factual"


def test_promote_claims_resolves_depends_on():
    source = "claim A\nclaim B"
    raws = [
        RawClaim(text="claim A", start_line=1, section="1", question="Q1?"),
        RawClaim(text="claim B", start_line=2, section="1", question="Q2?", depends_on=["claim A"]),
    ]
    claims = promote_claims(raws, source)
    assert len(claims) == 2
    assert claims[1].depends_on == [claims[0].loc]


def test_promote_evidence_uses_start_line():
    source = "Evidence text here."
    raws = [
        RawEvidence(
            text="Evidence text", start_line=1, section="1",
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
        loc=SourceLoc(line=2, start_char=0, end_char=4),
        text="XABX", original_quotes=["XABX"], section="1", question="Q?", depends_on=[],
    )
    result = dedup_tier1([c_short, c_long])
    assert result[0].merged_into == c_long.loc
    assert result[1].merged_into is None
    assert "AB" in result[1].original_quotes


def test_promote_markers_uses_start_line():
    source = "This is line one.\nWe dismiss complexity.\nLine three."
    raws = [
        RawMarker(
            text="We dismiss complexity.", start_line=2,
            section="1", marker_type="dismissal",
            target="complexity", intensity="moderate",
        ),
    ]
    markers = promote_markers(raws, source)
    assert len(markers) == 1
    assert markers[0].loc.line == 2
    assert markers[0].marker_type == "dismissal"


def test_extract_citations_basic():
    text = "See [P4172R0](https://example.com/p4172r0.pdf) and P2300R10 for details."
    refs = extract_citations(text)
    assert len(refs) == 2
    assert refs[0].paper_id == "P4172R0"
    assert refs[0].count == 1


def test_extract_citations_dedup_case_insensitive():
    text = "p4172r0 and P4172R0 both appear"
    refs = extract_citations(text)
    assert len(refs) == 1
    assert refs[0].paper_id == "P4172R0"
    assert refs[0].count == 2


def test_extract_citations_n_papers():
    text = "N4861 is the working draft"
    refs = extract_citations(text)
    assert len(refs) == 1
    assert refs[0].paper_id == "N4861"
