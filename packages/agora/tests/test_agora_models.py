#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for agora domain models and the analysis/generation field split."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agora.models import (
    CalibrationOutput,
    DesignTension,
    EncounterPlan,
    EncountersOutput,
    PipelineState,
    Reply,
    ResearchAgentReport,
    ResearchSummary,
    SkeletonOutput,
    SmellTestOutput,
    SubmissionOutput,
    TechnicalAnchor,
    Thread,
)


def _anchor(id_="a01"):
    return TechnicalAnchor(
        id=id_,
        kind="load_bearing",
        summary="X is the central claim.",
        claim_text="X is the central claim.",
        claim_uid=1,
    )


def _research():
    rep = ResearchAgentReport(
        agent="public_reception",
        findings="No prior mentions.",
        sources=[],
        heat_signal="warm",
        interest_signal="relevant",
    )
    return ResearchSummary(
        public_reception=rep,
        committee_history=rep.model_copy(update={"agent": "committee_history"}),
        author_ecosystem=rep.model_copy(update={"agent": "author_ecosystem"}),
    )


def test_technical_anchor_round_trip():
    a = _anchor()
    assert TechnicalAnchor.model_validate(a.model_dump()) == a
    assert a.claim_uid == 1


def test_design_tension_optional_anchor():
    t = DesignTension(id="t01", description="A vs B")
    assert t.anchor_id is None
    t2 = DesignTension(id="t02", description="C vs D", anchor_id="a01")
    assert t2.anchor_id == "a01"


def test_reply_required_fields_only_analysis():
    r = Reply(slot_id="s01", parent_slot_id=None, depth=0,
              role="signal", brief="Address anchor a01.")
    assert r.content is None
    assert r.character_username is None
    assert r.score is None
    assert r.awards == []
    assert r.is_op is False


def test_reply_depth_bounds():
    Reply(slot_id="s01", depth=6, role="signal", brief="b")
    with pytest.raises(ValidationError):
        Reply(slot_id="s01", depth=7, role="signal", brief="b")
    with pytest.raises(ValidationError):
        Reply(slot_id="s01", depth=-1, role="signal", brief="b")


def test_reply_lens_bounds():
    Reply(slot_id="s01", depth=0, role="signal", brief="b", domain_lens=13)
    with pytest.raises(ValidationError):
        Reply(slot_id="s01", depth=0, role="signal", brief="b", domain_lens=14)
    with pytest.raises(ValidationError):
        Reply(slot_id="s01", depth=0, role="signal", brief="b", domain_lens=0)


def test_encounter_plan_round_trip():
    e = EncounterPlan(
        encounter_id="e01",
        design_tension_id="t01",
        design_tension="A vs B",
        position_a="A",
        position_b="B",
        resolution="narrowing",
        slot_ids=["s05", "s06", "s07"],
    )
    assert EncounterPlan.model_validate(e.model_dump()) == e


def test_thread_construct_with_analysis_only_fields():
    t = Thread(
        document="P4003R2", paper="P4003", revision=2,
        title="Foo", authors="A, B", audience="EWG",
        date="2026-01-15", subreddit="r/ewg",
        paper_type="proposal",
        technical_anchors=[_anchor()],
        research_summary=_research(),
        heat="warm", interest="relevant",
        target_comment_count=25, encounter_count=0,
        signal_count=10, noise_count=15,
        submission_title="[P4003R2] foo",
        submission_body="body",
        submission_link="https://wg21.link/p4003r2",
    )
    # Generation-phase fields all None / default.
    assert t.submission_poster_id is None
    assert t.submission_votes is None
    assert t.generated_at is None
    # Defaults for collections.
    assert t.replies == []
    assert t.encounters == []
    assert t.tangent_magnets == []


def test_smell_test_output_defaults():
    o = SmellTestOutput(paper_type="wording")
    assert o.technical_anchors == []
    assert o.hot_takes == []
    assert o.design_tensions == []


def test_calibration_output_negative_count_rejected():
    with pytest.raises(ValidationError):
        CalibrationOutput(
            heat="warm", interest="relevant",
            target_comment_count=-1,
            encounter_count=0, signal_count=0, noise_count=0,
            rationale="r",
        )


def test_submission_output_default_case_A():
    o = SubmissionOutput(
        submission_title="t", submission_body="b",
        submission_link="https://wg21.link/p1r0",
    )
    assert o.revision_case == "A"


def test_skeleton_output_carries_groups():
    o = SkeletonOutput(
        replies=[Reply(slot_id="s01", depth=0, role="signal", brief="b")],
        encounter_slot_groups=[["s05", "s06", "s07"]],
    )
    assert len(o.encounter_slot_groups) == 1
    assert o.encounter_slot_groups[0] == ["s05", "s06", "s07"]


def test_encounters_output_default():
    o = EncountersOutput()
    assert o.encounters == []


def test_pipeline_state_defaults_none():
    s = PipelineState()
    assert s.paper_id == ""
    assert s.paper_source is None
    assert s.subreddit is None
    assert s.revision_case == "A"
    assert s.heat is None
    assert s.thread is None
    assert s.paper_authors == []


def test_pipeline_state_assignable():
    s = PipelineState()
    s.paper_id = "P4003R2"
    s.subreddit = "r/lewg"
    s.heat = "thermonuclear"
    s.encounter_count = 3
    assert s.paper_id == "P4003R2"
    assert s.subreddit == "r/lewg"
    assert s.heat == "thermonuclear"
    assert s.encounter_count == 3
