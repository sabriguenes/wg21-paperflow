#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for the agora render helpers (debug + trace markdown)."""

from __future__ import annotations

from agora.models import (
    DesignTension,
    EncounterPlan,
    PipelineState,
    Reply,
    ResearchAgentReport,
    ResearchSummary,
    TechnicalAnchor,
)
from agora.render import render_trace


def _anchor(id_="a01", line=1):
    return TechnicalAnchor(
        id=id_, kind="load_bearing",
        summary="X holds the proposal up.",
        claim_text="X holds the proposal up.",
        claim_uid=line,
    )


def _rep(name="public_reception"):
    return ResearchAgentReport(
        agent=name,  # type: ignore[arg-type]
        findings="Nothing notable.",
        sources=[],
        heat_signal="warm",
        interest_signal="relevant",
    )


def test_render_trace_step_0_only_renders_load_section():
    state = PipelineState(
        paper_id="P1000R0",
        paper_title="Schedule",
        subreddit="r/lewg",
        paper_audience="LEWG",
        paper_revision=0,
        dissect_caput_causae="Foo bar baz.",
    )
    out = render_trace(state, stop_step=0)
    assert "# Trace: P1000R0" in out
    assert "## 0. Load" in out
    assert "## 1. Smell Test" not in out
    assert "Foo bar baz" in out
    assert "r/lewg" in out


def test_render_trace_includes_anchors_and_tensions():
    state = PipelineState(
        paper_id="P2000R1", paper_title="Anchors",
        subreddit="r/ewg",
        paper_type="proposal",
        technical_anchors=[_anchor("a01", 5), _anchor("a02", 9)],
        design_tensions=[DesignTension(id="t01", description="A vs B")],
        hot_takes=["committee is asleep"],
        tangent_magnets=["history of widgets"],
        misconception_traps=[],
    )
    out = render_trace(state, stop_step=1)
    assert "## 1. Smell Test" in out
    assert "**a01**" in out
    assert "tension **t01**" in out
    assert "## 2. Research" not in out


def test_render_trace_includes_research_when_step_2_reached():
    rs = ResearchSummary(
        public_reception=_rep("public_reception"),
        committee_history=_rep("committee_history"),
        author_ecosystem=_rep("author_ecosystem"),
    )
    state = PipelineState(
        paper_id="P3000R0", paper_title="Research",
        subreddit="r/lwg",
        research_summary=rs,
    )
    out = render_trace(state, stop_step=2)
    assert "## 2. Research" in out
    assert "public reception" in out
    assert "Nothing notable." in out


def test_render_trace_calibration_section():
    state = PipelineState(
        paper_id="P4000R0", paper_title="Hot",
        subreddit="r/ewg",
        heat="thermonuclear", interest="gravitational",
        target_comment_count=200,
        encounter_count=3, signal_count=80, noise_count=120,
    )
    out = render_trace(state, stop_step=3)
    assert "heat=**thermonuclear**" in out
    assert "target_comments=200" in out
    assert "encounters=3" in out


def test_render_trace_skeleton_lists_replies():
    replies = [
        Reply(slot_id="s01", parent_slot_id=None, depth=0,
              role="signal", brief="Address anchor a01.", anchor_id="a01"),
        Reply(slot_id="s02", parent_slot_id="s01", depth=1,
              role="noise", brief="Low-stakes complaint.",
              noise_tone="snark", noise_stance="con"),
    ]
    state = PipelineState(
        paper_id="P5000R0", paper_title="Skeleton",
        subreddit="r/ewg",
        replies=replies,
        encounter_slot_groups=[],
    )
    out = render_trace(state, stop_step=5)
    assert "## 5. Skeleton" in out
    assert "signal=1" in out
    assert "noise=1" in out
    assert "**s01**" in out
    assert "Address anchor a01." in out


def test_render_trace_encounters_section_handles_empty():
    state = PipelineState(
        paper_id="P6000R0", paper_title="No encounters",
        subreddit="r/ewg",
        encounter_count=0,
        encounters=[],
    )
    out = render_trace(state, stop_step=6)
    assert "## 6. Encounters" in out
    assert "no encounters" in out.lower()


def test_render_trace_encounters_section_renders_plans():
    enc = EncounterPlan(
        encounter_id="e01",
        design_tension_id="t01",
        design_tension="A vs B",
        position_a="A",
        position_b="B",
        resolution="narrowing",
        slot_ids=["s05", "s06", "s07"],
    )
    state = PipelineState(
        paper_id="P7000R0", paper_title="Encs",
        subreddit="r/ewg",
        encounter_count=1,
        encounters=[enc],
    )
    out = render_trace(state, stop_step=6)
    assert "e01" in out
    assert "narrowing" in out
    assert "s05, s06, s07" in out
