#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for advocatus domain models and per-step output schemas."""

from __future__ import annotations

import pytest
from paperstore import SourceLoc

from advocatus.models import (
    Articulus,
    ArticulusExam,
    Boundary,
    CandidateCharge,
    DefensorChallenge,
    DefensorChargeOutput,
    ExamOutcome,
    Motivatio,
    NotaMinor,
    Objection,
    PipelineState,
    Probatio,
    ScriptaOutput,
    Stakeholder,
    SurvivingCharge,
    TabulaFontiumEntry,
    WeighCauseOutput,
)


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_articulus_round_trip():
    a = Articulus(
        loc=_loc(),
        text="X should be Y",
        section="2.1",
        kind="normative",
        question="What evidence shows X should be Y?",
    )
    assert Articulus.model_validate(a.model_dump()) == a


def test_boundary_round_trip():
    b = Boundary(loc=_loc(7), text="we do not propose changes to std::variant", kind="disclaim")
    assert Boundary.model_validate(b.model_dump()) == b


def test_articulus_exam_with_confidence():
    e = ArticulusExam(
        articulus_loc=_loc(),
        veritas=ExamOutcome(passed=True, reasoning="dossier confirms"),
        ratio=ExamOutcome(passed=False, reasoning="logical gap at step 2"),
        auctoritas=ExamOutcome(passed=True, reasoning="citation supports"),
        confidence=0.7,
    )
    assert ArticulusExam.model_validate(e.model_dump()) == e
    # Confidence bounds
    with pytest.raises(Exception):
        ArticulusExam(
            articulus_loc=_loc(),
            veritas=ExamOutcome(passed=True, reasoning=""),
            ratio=ExamOutcome(passed=True, reasoning=""),
            auctoritas=ExamOutcome(passed=True, reasoning=""),
            confidence=1.5,
        )


def test_candidate_charge_with_all_elements():
    c = CandidateCharge(
        articulus_loc=_loc(5),
        quoted_text="X is the best approach",
        failed_test="auctoritas",
        contradicting_loc=_loc(12),
        contradicting_evidence="P1234R3 section 4 reaches the opposite conclusion.",
        gravamen="The cited source contradicts the inference being drawn.",
    )
    assert CandidateCharge.model_validate(c.model_dump()) == c


def test_defensor_challenge_chain():
    chain = [
        DefensorChallenge(challenge="confessio", verdict="survived",
                          reasoning="not conceded", confidence=0.8),
        DefensorChallenge(challenge="articulus", verdict="killed",
                          reasoning="paper does not claim this", confidence=0.9),
    ]
    out = DefensorChargeOutput(charge_loc=_loc(5), challenges=chain, final="killed")
    assert DefensorChargeOutput.model_validate(out.model_dump()) == out


def test_objection_with_motivatio():
    charge = CandidateCharge(
        articulus_loc=_loc(5),
        quoted_text="Y", failed_test="ratio",
        contradicting_evidence="z",
        gravamen="g",
    )
    surviving = SurvivingCharge(
        articulus_loc=_loc(5), charge=charge, defensor_chain=[],
    )
    motivatio = Motivatio(
        adversary="UK NB", forum="nb_comment",
        damage="revision_forcing", explanation="UK has flagged X-related concerns previously",
    )
    obj = Objection(
        articulus_loc=_loc(5), charge=surviving,
        motivatio=motivatio, severity="medium",
    )
    assert Objection.model_validate(obj.model_dump()) == obj


def test_probatio_round_trip():
    charge = CandidateCharge(
        articulus_loc=_loc(5), quoted_text="Y", failed_test="ratio",
        contradicting_evidence="z", gravamen="g",
    )
    p = Probatio(
        articulus_loc=_loc(5),
        killed_charge=charge,
        killing_challenge="humanitas",
        explanation="No human committee member would press this",
    )
    assert Probatio.model_validate(p.model_dump()) == p


def test_nota_minor_optional_loc():
    n = NotaMinor(loc=None, text="formatting inconsistency in section 3")
    assert n.loc is None
    n2 = NotaMinor(loc=_loc(3), text="typo")
    assert n2.loc == _loc(3)


def test_weigh_cause_output():
    w = WeighCauseOutput(
        seal="cum_objectionibus",
        central_thesis_survives=True,
        one_sentence_assessment="The cause proceeds with two minor objections.",
        confidence=0.85,
    )
    assert WeighCauseOutput.model_validate(w.model_dump()) == w


def test_scripta_output_defaults():
    s = ScriptaOutput(central_thesis_recap="X is the head of the cause.")
    assert s.articuli == []
    assert s.boundaries == []


def test_pipeline_state_defaults_none():
    s = PipelineState()
    # Optional fields default to None, list fields to [].
    assert s.paper_source is None
    assert s.seal is None
    assert s.confidence is None
    assert s.paper_authors == []


def test_pipeline_state_assignable():
    s = PipelineState()
    s.paper_id = "P9999R0"
    s.seal = "nihil_obstat"
    s.confidence = 0.95
    assert s.paper_id == "P9999R0"
    assert s.seal == "nihil_obstat"
    assert s.confidence == 0.95


def test_tabula_fontium_entry_defaults():
    e = TabulaFontiumEntry(paper_id="P1234R0", resolution_method="wg21_link", resolved=True)
    assert e.source_url == ""
    assert e.quote_match == "not_checked"


def test_stakeholder_round_trip():
    s = Stakeholder(name="Alice", position="opposes X", source_url="", stance="opponent")
    assert Stakeholder.model_validate(s.model_dump()) == s


def test_sourceloc_round_trips_through_pydantic_field():
    a = Articulus(loc=_loc(7, 0, 50), text="x", section="s", kind="normative", question="q?")
    a2 = Articulus.model_validate(a.model_dump())
    assert a2.loc == _loc(7, 0, 50)
    assert isinstance(a2.loc, SourceLoc)
