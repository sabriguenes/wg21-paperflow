#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for Pydantic model construction and serialization."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from dissect.models import (
    CaputCausae,
    CitationAuditEntry,
    CitationTaskOutput,
    Claim,
    Chunk,
    Evidence,
    ExtractAllOutput,
    ExtractFactualOutput,
    ExternalEvidence,
    InternalContradiction,
    LoadBearingResult,
    PipelineState,
    RawClaim,
    RawEvidence,
    SourceLoc,
    SupportLink,
    WebResolution,
)


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_source_loc_round_trip():
    loc = _loc(5, 10, 20)
    assert SourceLoc(**dataclasses.asdict(loc)) == loc


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
    ic = InternalContradiction(source_loc=_loc(1), claim_loc=_loc(2), kind="evidence_vs_claim")
    assert InternalContradiction.model_validate(ic.model_dump()) == ic


def test_internal_contradiction_claim_vs_claim():
    ic = InternalContradiction(source_loc=_loc(3), claim_loc=_loc(4), kind="claim_vs_claim")
    assert ic.kind == "claim_vs_claim"
    assert ic.source_loc.line == 3


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


def test_extract_all_output_claims():
    rc = RawClaim(text="X", original_quotes=["X"], section="1", question="Q?", depends_on=[])
    out = ExtractAllOutput(claims=[rc])
    assert len(out.claims) == 1


def test_extract_all_output_evidence():
    re_ = RawEvidence(
        text="E", original_quotes=["E"], section="2",
        supports=["S"], quantitative=False, cited=False,
        verifiable=False, normative=False,
    )
    out = ExtractAllOutput(evidence=[re_])
    assert len(out.evidence) == 1


def test_claim_kind_factual():
    c = Claim(
        loc=_loc(), text="X is 5ns", original_quotes=["X is 5ns"],
        section="2", question="How fast is X?", kind="factual", depends_on=[],
    )
    assert c.kind == "factual"
    assert Claim.model_validate(c.model_dump()) == c


def test_caput_causae_round_trip():
    cc = CaputCausae(
        thesis="The paper argues for coroutines.",
        anchored_claim_locs=[_loc(1), _loc(2)],
        evidence_root_locs=[_loc(3)],
    )
    assert CaputCausae.model_validate(cc.model_dump()) == cc


def test_caput_causae_defaults():
    cc = CaputCausae(thesis="Minimal thesis.")
    assert cc.anchored_claim_locs == []
    assert cc.evidence_root_locs == []


def test_citation_audit_entry_round_trip():
    entry = CitationAuditEntry(
        paper_id="P1928R15",
        resolution_method="wg21_link",
        resolved=True,
        source_url="https://example.com",
        quote_match="exact",
        discrepancy="",
    )
    assert CitationAuditEntry.model_validate(entry.model_dump()) == entry


def test_citation_audit_entry_defaults():
    entry = CitationAuditEntry(
        paper_id="P0001R0",
        resolution_method="not_found",
        resolved=False,
    )
    assert entry.source_url == ""
    assert entry.quote_match == "not_checked"
    assert entry.discrepancy == ""


def test_citation_audit_entry_rejects_unknown_resolution_method():
    # `resolution_method` accepts only `local_index`, `wg21_link`,
    # `open_std`, or `not_found`. `"isocpp"` is outside that set, so
    # construction must fail at validation time rather than persist a
    # bogus token into the citation_audit table.
    with pytest.raises(ValidationError):
        CitationAuditEntry(
            paper_id="P0001R0",
            resolution_method="isocpp",
            resolved=False,
        )


def test_citation_task_output_round_trip():
    audit = CitationAuditEntry(
        paper_id="P2300R10",
        resolution_method="wg21_link",
        resolved=True,
    )
    ee = ExternalEvidence(
        claim_loc=_loc(1), source_url="https://example.com",
        source_title="Paper", text="passage", finding="confirmed",
        stance="supports", quantitative=False, cited=True,
        verifiable=True, normative=False,
    )
    out = CitationTaskOutput(audit=audit, evidence=[ee])
    assert CitationTaskOutput.model_validate(out.model_dump()) == out


def test_extract_factual_output():
    rc = RawClaim(text="X", section="1", question="Q?", kind="factual")
    out = ExtractFactualOutput(claims=[rc])
    assert len(out.claims) == 1
    assert out.claims[0].kind == "factual"
