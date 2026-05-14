#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the pure-Python code harness.

All tests use synthetic data - no LLM, no paperstore, no fixtures.
"""

from __future__ import annotations

from dissect.harness import (
    _blank_non_prose,
    _chunk_paper,
    _dedup_tier0,
    _dedup_tier1,
    _extract_citations,
    _number_lines,
    _promote_claims,
    _promote_evidence,
    _promote_rhetoric,
)
from dissect.models import Chunk, Claim, RawClaim, RawEvidence, RawRhetoric, SourceLoc


def test_number_lines():
    chunk = Chunk(text="line one\nline two\nline three", line_offset=10)
    result = _number_lines(chunk)
    assert result == "10| line one\n11| line two\n12| line three"


def test_chunk_paper_small():
    small = "# Title\n\nShort paper."
    chunks = _chunk_paper(small, max_chars=70_000)
    assert len(chunks) == 1
    assert chunks[0].text == small
    assert chunks[0].line_offset == 1


def test_chunk_paper_splits_at_headings():
    lines = []
    for i in range(20):
        lines.append(f"## Section {i}\n")
        lines.append("x" * 4000 + "\n")
    paper = "".join(lines)
    chunks = _chunk_paper(paper, max_chars=10_000)
    assert len(chunks) > 1


def test_chunk_paper_overlap():
    lines = []
    for i in range(10):
        lines.append(f"## Section {i}\n")
        lines.append("content " * 1000 + "\n")
    paper = "".join(lines)
    chunks = _chunk_paper(paper, max_chars=10_000)
    if len(chunks) >= 2:
        assert chunks[1].line_offset < chunks[0].line_offset + len(chunks[0].text.splitlines())


def test_promote_claims_uses_start_line():
    source = "line one\nline two\nline three"
    raws = [
        RawClaim(text="line two", start_line=2, section="1", question="Q?"),
    ]
    claims, next_uid = _promote_claims(raws, source)
    assert len(claims) == 1
    assert claims[0].loc.line == 2
    assert claims[0].uid == 1
    assert next_uid == 2


def test_promote_claims_never_drops():
    source = "Only this text exists."
    raws = [
        RawClaim(text="nonexistent quote", start_line=1, section="1", question="Q?"),
    ]
    claims, next_uid = _promote_claims(raws, source)
    assert len(claims) == 1
    assert next_uid == 2


def test_promote_claims_defaults_to_normative():
    source = "claim text"
    raws = [
        RawClaim(text="claim text", start_line=1, question="Q?"),
    ]
    claims, next_uid = _promote_claims(raws, source)
    assert len(claims) == 1
    assert claims[0].kind == "normative"


def test_promote_evidence_uses_start_line():
    source = "Evidence text here."
    raws = [
        RawEvidence(
            text="Evidence text", start_line=1, section="1",
            supports=["something"], quantitative=False, cited=False,
            verifiable=False, normative=False,
        ),
    ]
    evidence, next_uid = _promote_evidence(raws, source)
    assert len(evidence) == 1
    assert evidence[0].loc.line == 1
    assert evidence[0].uid == 1
    assert next_uid == 2


def test_dedup_tier0_tombstones_duplicate_locs():
    loc = SourceLoc(line=1, start_char=0, end_char=5)
    c1 = Claim(uid=1, loc=loc, text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[])
    c2 = Claim(uid=2, loc=loc, text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[])
    result = _dedup_tier0([c1, c2])
    assert result[0].merged_into is None
    assert result[1].merged_into == 1


def test_dedup_tier0_preserves_distinct():
    c1 = Claim(
        uid=1, loc=SourceLoc(line=1, start_char=0, end_char=5),
        text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[],
    )
    c2 = Claim(
        uid=2, loc=SourceLoc(line=2, start_char=0, end_char=5),
        text="B", original_quotes=["B"], section="1", question="Q?", depends_on=[],
    )
    result = _dedup_tier0([c1, c2])
    assert all(r.merged_into is None for r in result)


def test_dedup_tier1_substring_tombstone():
    c_short = Claim(
        uid=1, loc=SourceLoc(line=1, start_char=0, end_char=2),
        text="AB", original_quotes=["AB"], section="1", question="Q?", depends_on=[],
    )
    c_long = Claim(
        uid=2, loc=SourceLoc(line=2, start_char=0, end_char=4),
        text="XABX", original_quotes=["XABX"], section="1", question="Q?", depends_on=[],
    )
    result = _dedup_tier1([c_short, c_long])
    assert result[0].merged_into == c_long.uid
    assert result[1].merged_into is None
    assert "AB" in result[1].original_quotes


def test_promote_rhetoric_uses_start_line():
    source = "This is line one.\nWe dismiss complexity.\nLine three."
    raws = [
        RawRhetoric(
            text="We dismiss complexity.", start_line=2,
            section="1", marker_type="dismissal",
            target="complexity", intensity="medium",
        ),
    ]
    items, next_uid = _promote_rhetoric(raws, source)
    assert len(items) == 1
    assert items[0].loc.line == 2
    assert items[0].marker_type == "dismissal"
    assert items[0].uid == 1
    assert next_uid == 2


def test_extract_citations_basic():
    text = "See [P4172R0](https://example.com/p4172r0.pdf) and P2300R10 for details."
    refs = _extract_citations(text)
    assert len(refs) == 2
    # Equal counts tie-break alphabetically by paper_id; "P2300R10" < "P4172R0".
    assert refs[0].paper_id == "P2300R10"
    assert refs[0].count == 1
    assert refs[1].paper_id == "P4172R0"
    assert refs[1].count == 1


def test_extract_citations_dedup_case_insensitive():
    text = "p4172r0 and P4172R0 both appear"
    refs = _extract_citations(text)
    assert len(refs) == 1
    assert refs[0].paper_id == "P4172R0"
    assert refs[0].count == 2


def test_extract_citations_n_papers():
    text = "N4861 is the working draft"
    refs = _extract_citations(text)
    assert len(refs) == 1
    assert refs[0].paper_id == "N4861"


def test_blank_non_prose_preserves_line_count():
    source = "prose\n```cpp\nint x = 1;\n```\nmore prose\n"
    result, blanked = _blank_non_prose(source)
    assert len(result.splitlines()) == len(source.splitlines())
    assert blanked == 3


def test_blank_non_prose_strips_fenced_code():
    source = "before\n```cpp\nint x = 1;\nreturn x;\n```\nafter\n"
    result, _ = _blank_non_prose(source)
    assert "int x" not in result
    assert "before" in result
    assert "after" in result


def test_blank_non_prose_strips_wording_div():
    source = "before\n:::wording\nspec text\n:::\nafter\n"
    result, blanked = _blank_non_prose(source)
    assert "spec text" not in result
    assert "before" in result
    assert "after" in result
    assert blanked == 3


def test_blank_non_prose_strips_wording_add():
    source = "before\n:::wording-add\nnew text\n:::\nafter\n"
    result, _ = _blank_non_prose(source)
    assert "new text" not in result
    assert "before" in result
    assert "after" in result


def test_blank_non_prose_preserves_prose():
    source = "This is a normative claim.\nAnother sentence.\n"
    result, blanked = _blank_non_prose(source)
    assert result == source
    assert blanked == 0


def test_blank_non_prose_fence_with_language_tag():
    source = "x\n```python\nprint('hi')\n```\ny\n"
    result, _ = _blank_non_prose(source)
    assert "print" not in result
    assert "x\n" in result
    assert "y\n" in result
