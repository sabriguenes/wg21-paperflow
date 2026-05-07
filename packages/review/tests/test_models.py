#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for Pydantic model construction and serialization."""

from __future__ import annotations

import pytest

from review.models import (
    Claim,
    Chunk,
    Evidence,
    ExternalEvidence,
    InternalContradiction,
    LoadBearingResult,
    PipelineState,
    RawClaim,
    RawEvidence,
    SourceLoc,
    SupportLink,
    WebResolution,
    ExtractClaimsOutput,
    ExtractEvidenceOutput,
)


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_source_loc_round_trip():
    loc = _loc(5, 10, 20)
    assert SourceLoc.model_validate(loc.model_dump()) == loc


def test_chunk_round_trip():
    c = Chunk(text="hello", line_offset=42)
    assert Chunk.model_validate(c.model_dump()) == c


def test_claim_round_trip():
    c = Claim(
        loc=_loc(), text="X is fast", original_quotes=["X is fast"],
        section="3.1", question="Is X fast?", depends_on=[], merged_into=None,
    )
    assert Claim.model_validate(c.model_dump()) == c


def test_claim_model_copy_merged_into():
    c = Claim(
        loc=_loc(1), text="A", original_quotes=["A"],
        section="1", question="Q?", depends_on=[], merged_into=None,
    )
    target = _loc(2)
    updated = c.model_copy(update={"merged_into": target})
    assert updated.merged_into == target
    assert c.merged_into is None


def test_evidence_round_trip():
    e = Evidence(
        loc=_loc(), text="measured 5ns", original_quotes=["measured 5ns"],
        section="2", supports=["X is fast"], quantitative=True,
        cited=False, verifiable=True, normative=False, merged_into=None,
    )
    assert Evidence.model_validate(e.model_dump()) == e


def test_support_link_round_trip():
    sl = SupportLink(claim_loc=_loc(1), evidence_locs=[_loc(2)], status="directly_supported")
    assert SupportLink.model_validate(sl.model_dump()) == sl


def test_internal_contradiction_round_trip():
    ic = InternalContradiction(evidence_loc=_loc(1), claim_loc=_loc(2))
    assert InternalContradiction.model_validate(ic.model_dump()) == ic


def test_load_bearing_result_round_trip():
    lb = LoadBearingResult(
        claim_loc=_loc(), dependents=[_loc(2)], classification="critical_gap",
    )
    assert LoadBearingResult.model_validate(lb.model_dump()) == lb


def test_external_evidence_round_trip():
    ee = ExternalEvidence(
        claim_loc=_loc(), source_url="https://example.com", source_title="Example",
        text="passage", finding="it works", stance="supports",
        quantitative=False, cited=True, verifiable=True, normative=False,
    )
    assert ExternalEvidence.model_validate(ee.model_dump()) == ee


def test_web_resolution_round_trip():
    wr = WebResolution(
        external_loc=_loc(), source_url="https://x.com", stance="supports",
        finding="confirmed", resolved_claims=[_loc(2)],
    )
    assert WebResolution.model_validate(wr.model_dump()) == wr


def test_raw_claim_round_trip():
    rc = RawClaim(
        text="X", original_quotes=["X"], section="1",
        question="Q?", depends_on=["Y"],
    )
    assert RawClaim.model_validate(rc.model_dump()) == rc


def test_raw_evidence_round_trip():
    re_ = RawEvidence(
        text="E", original_quotes=["E"], section="2",
        supports=["S"], quantitative=False, cited=False,
        verifiable=False, normative=False,
    )
    assert RawEvidence.model_validate(re_.model_dump()) == re_


def test_pipeline_state_defaults_none():
    s = PipelineState()
    for field_name in PipelineState.model_fields:
        assert getattr(s, field_name) is None


def test_pipeline_state_assignable():
    s = PipelineState()
    s.paper_source = "# Test"
    s.report = "result"
    assert s.paper_source == "# Test"
    assert s.report == "result"


def test_frozen_models_are_immutable():
    c = Claim(
        loc=_loc(), text="X", original_quotes=["X"],
        section="1", question="Q?", depends_on=[],
    )
    with pytest.raises(Exception):
        c.text = "Y"  # type: ignore[misc]


def test_extract_claims_output():
    rc = RawClaim(text="X", original_quotes=["X"], section="1", question="Q?", depends_on=[])
    out = ExtractClaimsOutput(claims=[rc])
    assert len(out.claims) == 1


def test_extract_evidence_output():
    re_ = RawEvidence(
        text="E", original_quotes=["E"], section="2",
        supports=["S"], quantitative=False, cited=False,
        verifiable=False, normative=False,
    )
    out = ExtractEvidenceOutput(evidence=[re_])
    assert len(out.evidence) == 1
