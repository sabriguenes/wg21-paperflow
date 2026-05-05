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
    Assumption,
    CandidateFinding,
    CertifiedSection,
    CitationEntry,
    Claim,
    Evidence,
    EvidenceFinding,
    InterpretedFinding,
    KilledFinding,
    PipelineState,
    Premise,
    ThinSection,
    ArgumentStructure,
    ConfirmedCounterexample,
    ClassifyOutput,
    ReadPaperOutput,
    WriteOutputOutput,
)


def test_claim_round_trip():
    c = Claim(text="X is fast", section="3.1", tag="factual")
    assert Claim.model_validate(c.model_dump()) == c


def test_premise_round_trip():
    p = Premise(text="Assume Y", section="2")
    assert Premise.model_validate(p.model_dump()) == p


def test_thin_section_round_trip():
    t = ThinSection(section="4", scope_stated="Performance", audience_affected="SG1")
    assert ThinSection.model_validate(t.model_dump()) == t


def test_argument_structure_round_trip():
    a = ArgumentStructure(type="elimination", section="5", elements=["A", "B"])
    assert ArgumentStructure.model_validate(a.model_dump()) == a


def test_evidence_finding_round_trip():
    ef = EvidenceFinding(source="P1234R0", date="2026-01-01", substance="Found X")
    assert EvidenceFinding.model_validate(ef.model_dump()) == ef


def test_evidence_round_trip():
    ef = EvidenceFinding(source="src", date="2026", substance="sub")
    e = Evidence(
        paper_reception=[ef],
        committee_history=[],
        referenced_papers=[],
        domain_landscape=[],
        rehabilitated_alternatives=[],
    )
    assert Evidence.model_validate(e.model_dump()) == e


def test_assumption_with_source():
    a = Assumption(assumption="Author intends X", status="verified", source="email")
    assert a.source == "email"


def test_assumption_without_source():
    a = Assumption(assumption="Author intends X", status="plausible")
    assert a.source is None


def test_candidate_finding_round_trip():
    cf = CandidateFinding(
        quoted_text="claim X",
        section="3",
        failed_test="accuracy",
        contradicting_evidence="source Y says Z",
        core_complaint="X is wrong",
        finding_type="inconsistency",
    )
    assert CandidateFinding.model_validate(cf.model_dump()) == cf


def test_killed_finding_round_trip():
    cf = CandidateFinding(
        quoted_text="q", section="1", failed_test="logic",
        contradicting_evidence="e", core_complaint="c", finding_type="miss",
    )
    kf = KilledFinding(finding=cf, killed_by="paper_handles_it", reason="Concession in s3")
    assert KilledFinding.model_validate(kf.model_dump()) == kf


def test_interpreted_finding_round_trip():
    cf = CandidateFinding(
        quoted_text="q", section="1", failed_test="accuracy",
        contradicting_evidence="e", core_complaint="c", finding_type="miss",
    )
    inf = InterpretedFinding(finding=cf, who="LEWG", where="reflector", what_damage="blocks")
    assert InterpretedFinding.model_validate(inf.model_dump()) == inf


def test_citation_entry_minimal():
    ce = CitationEntry(link="P1234R0", status="resolved")
    assert ce.target_url is None
    assert ce.quote_match is None


def test_pipeline_state_defaults_none():
    s = PipelineState()
    for field_name in PipelineState.model_fields:
        assert getattr(s, field_name) is None


def test_pipeline_state_assignable():
    s = PipelineState()
    s.title = "Test Paper"
    s.paper_type = "ask"
    s.verdict = "no_objections"
    assert s.title == "Test Paper"
    assert s.paper_type == "ask"
    assert s.verdict == "no_objections"


def test_pipeline_state_no_prior_review_field():
    assert "prior_review" not in PipelineState.model_fields
    assert "cache_status" not in PipelineState.model_fields


def test_classify_output():
    o = ClassifyOutput(
        title="T", document_number="P1234R0", author="A",
        audience="LEWG", paper_type="ask",
    )
    assert o.paper_type == "ask"


def test_read_paper_output():
    o = ReadPaperOutput(
        thesis="T",
        claims=[Claim(text="c", section="1", tag="factual")],
        boundaries=["b"],
        premises=[Premise(text="p", section="2")],
        thin_sections=[],
        argument_structures=[],
    )
    assert len(o.claims) == 1


def test_write_output_has_report_field():
    o = WriteOutputOutput(report="# Report\n\nContent.")
    assert o.report.startswith("# Report")


def test_frozen_models_are_immutable():
    c = Claim(text="X", section="1", tag="factual")
    with pytest.raises(Exception):
        c.text = "Y"  # type: ignore[misc]
