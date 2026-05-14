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
from pydantic_ai import ModelRetry

from dissect.models import (
    BatchVerifyOutput,
    CaputCausae,
    CitationAuditEntry,
    CitationTaskOutput,
    Claim,
    ClaimVerdict,
    Chunk,
    DisclaimPairOutput,
    Evidence,
    ExtractClaimsOutput,
    ExtractEvidenceOutput,
    ExtractFactualOutput,
    ExtractRhetoricOutput,
    ExternalEvidence,
    LoadBearingBinaryOutput,
    LoadBearingResult,
    PipelineState,
    RawClaim,
    RawEvidence,
    RawRhetoric,
    SourceLoc,
    VerifyProposition,
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
        uid=1, loc=_loc(), text="X is fast", original_quotes=["X is fast"],
        section="3.1", question="Is X fast?", depends_on=[], merged_into=None,
    )
    assert Claim.model_validate(c.model_dump()) == c


def test_claim_model_copy_merged_into():
    c = Claim(
        uid=1, loc=_loc(1), text="A", original_quotes=["A"],
        section="1", question="Q?", depends_on=[], merged_into=None,
    )
    updated = c.model_copy(update={"merged_into": 2})
    assert updated.merged_into == 2
    assert c.merged_into is None


def test_evidence_round_trip():
    e = Evidence(
        uid=1, loc=_loc(), text="measured 5ns", original_quotes=["measured 5ns"],
        section="2", supports=["X is fast"], quantitative=True,
        cited=False, verifiable=True, normative=False, merged_into=None,
    )
    assert Evidence.model_validate(e.model_dump()) == e


def test_claim_verdict_round_trip():
    v = ClaimVerdict(claim_uid=1, related_uid=2, status="proven")
    assert ClaimVerdict.model_validate(v.model_dump()) == v


def test_claim_verdict_unproven():
    v = ClaimVerdict(claim_uid=1, status="unproven")
    assert v.related_uid == -1
    assert v.status == "unproven"


def test_claim_verdict_disclaimed():
    v = ClaimVerdict(claim_uid=3, related_uid=4, status="disclaimed")
    assert v.status == "disclaimed"
    assert v.related_uid == 4


def test_load_bearing_result_round_trip():
    lb = LoadBearingResult(
        claim_uid=1, dependents=[2], classification="critical_gap",
    )
    assert LoadBearingResult.model_validate(lb.model_dump()) == lb


def test_external_evidence_round_trip():
    ee = ExternalEvidence(
        claim_uid=1, source_url="https://example.com", source_title="Example",
        text="passage", finding="it works", stance="supports",
        quantitative=False, cited=True, verifiable=True, normative=False,
    )
    assert ExternalEvidence.model_validate(ee.model_dump()) == ee


def test_web_resolution_round_trip():
    wr = WebResolution(
        external_uid=1, source_url="https://x.com", stance="supports",
        finding="confirmed", resolved_claims=[2],
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


def test_pipeline_state_defaults():
    s = PipelineState()
    assert s.next_uid == 1
    non_none_int_defaults = {
        "next_uid", "blanked_lines", "verify_batch_count", "self_pair_dropped",
    }
    for field_name in PipelineState.model_fields:
        if field_name in non_none_int_defaults:
            continue
        assert getattr(s, field_name) is None


def test_pipeline_state_assignable():
    s = PipelineState()
    s.paper_source = "# Test"
    s.report = "result"
    assert s.paper_source == "# Test"
    assert s.report == "result"


def test_frozen_models_are_immutable():
    c = Claim(
        uid=1, loc=_loc(), text="X", original_quotes=["X"],
        section="1", question="Q?", depends_on=[],
    )
    with pytest.raises(Exception):
        c.text = "Y"  # type: ignore[misc]


def test_extract_claims_output_claims():
    rc = RawClaim(text="X", original_quotes=["X"], section="1", question="Q?", depends_on=[])
    out = ExtractClaimsOutput(claims=[rc])
    assert len(out.claims) == 1


def test_extract_evidence_output_evidence():
    re_ = RawEvidence(
        text="E", original_quotes=["E"], section="2",
        supports=["S"], quantitative=False, cited=False,
        verifiable=False, normative=False,
    )
    out = ExtractEvidenceOutput(evidence=[re_])
    assert len(out.evidence) == 1


def test_extract_rhetoric_output_markers():
    marker = RawRhetoric(text="R", section="2", marker_type="dismissal")
    out = ExtractRhetoricOutput(markers=[marker])
    assert len(out.markers) == 1


@pytest.mark.skip(reason="empty-rejection validators disabled for wording-section chunks")
def test_extract_claims_output_rejects_empty():
    with pytest.raises(ModelRetry):
        ExtractClaimsOutput(claims=[])


@pytest.mark.skip(reason="empty-rejection validators disabled for wording-section chunks")
def test_extract_evidence_output_rejects_empty():
    with pytest.raises(ModelRetry):
        ExtractEvidenceOutput(evidence=[])


@pytest.mark.skip(reason="empty-rejection validators disabled for wording-section chunks")
def test_extract_rhetoric_output_rejects_empty():
    with pytest.raises(ModelRetry):
        ExtractRhetoricOutput(markers=[])


def test_claim_kind_factual():
    c = Claim(
        uid=1, loc=_loc(), text="X is 5ns", original_quotes=["X is 5ns"],
        section="2", question="How fast is X?", kind="factual", depends_on=[],
    )
    assert c.kind == "factual"
    assert Claim.model_validate(c.model_dump()) == c


def test_caput_causae_round_trip():
    cc = CaputCausae(
        thesis="The paper argues for coroutines.",
        anchored_claim_uids=[1, 2],
        evidence_root_uids=[3],
    )
    assert CaputCausae.model_validate(cc.model_dump()) == cc


def test_caput_causae_defaults():
    cc = CaputCausae(thesis="Minimal thesis.")
    assert cc.anchored_claim_uids == []
    assert cc.evidence_root_uids == []


def test_citation_audit_entry_round_trip():
    entry = CitationAuditEntry(
        paper_id="P1928R15",
        resolution_method="local_index",
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
    with pytest.raises(ValidationError):
        CitationAuditEntry(
            paper_id="P0001R0",
            resolution_method="isocpp",
            resolved=False,
        )


def test_citation_task_output_round_trip():
    audit = CitationAuditEntry(
        paper_id="P2300R10",
        resolution_method="local_index",
        resolved=True,
    )
    ee = ExternalEvidence(
        claim_uid=1, source_url="https://example.com",
        source_title="Paper", text="passage", finding="confirmed",
        stance="supports", quantitative=False, cited=True,
        verifiable=True, normative=False,
    )
    out = CitationTaskOutput(audit=audit, evidence=[ee])
    assert CitationTaskOutput.model_validate(out.model_dump()) == out


def test_extract_factual_output():
    rc = RawClaim(text="X", question="Q?")
    out = ExtractFactualOutput(claims=[rc])
    assert len(out.claims) == 1


def test_verify_proposition_round_trip():
    p = VerifyProposition(claim_uid=1, evidence_uid=10, verdict="support")
    assert VerifyProposition.model_validate(p.model_dump()) == p


def test_verify_proposition_rejects_unknown_verdict():
    with pytest.raises(ValidationError):
        VerifyProposition(claim_uid=1, evidence_uid=10, verdict="maybe")


def test_batch_verify_output_round_trip():
    p1 = VerifyProposition(claim_uid=1, evidence_uid=10, verdict="support")
    p2 = VerifyProposition(claim_uid=1, evidence_uid=11, verdict="contradict")
    out = BatchVerifyOutput(judgements=[p1, p2])
    assert BatchVerifyOutput.model_validate(out.model_dump()) == out


def test_batch_verify_output_empty_default():
    out = BatchVerifyOutput()
    assert out.judgements == []


def test_disclaim_pair_output_round_trip():
    d = DisclaimPairOutput(claim_a_uid=1, claim_b_uid=2, relation="a_disclaims_b")
    assert DisclaimPairOutput.model_validate(d.model_dump()) == d


def test_disclaim_pair_output_none_relation():
    d = DisclaimPairOutput(claim_a_uid=1, claim_b_uid=2, relation="none")
    assert d.relation == "none"


def test_disclaim_pair_output_rejects_unknown_relation():
    with pytest.raises(ValidationError):
        DisclaimPairOutput(claim_a_uid=1, claim_b_uid=2, relation="other")


def test_load_bearing_binary_output_round_trip():
    out = LoadBearingBinaryOutput(claim_uid=1, load_bearing=True, reason="thesis claim")
    assert LoadBearingBinaryOutput.model_validate(out.model_dump()) == out


def test_load_bearing_binary_output_defaults():
    out = LoadBearingBinaryOutput(claim_uid=1, load_bearing=False)
    assert out.reason == ""


def test_pipeline_state_centrality_fields_default_none():
    s = PipelineState()
    assert s.centrality_scores is None
    assert s.triaged_evidence is None
    assert s.disclaim_candidates is None
    assert s.verify_batch_count == 0


def test_pipeline_state_centrality_assignable():
    s = PipelineState()
    s.centrality_scores = {1: 2.0, 2: 1.0}
    s.triaged_evidence = {1: [10, 11], 2: [12]}
    s.disclaim_candidates = [(1, 2)]
    s.verify_batch_count = 3
    assert s.centrality_scores[1] == 2.0
    assert s.triaged_evidence[2] == [12]
    assert s.disclaim_candidates == [(1, 2)]
    assert s.verify_batch_count == 3
