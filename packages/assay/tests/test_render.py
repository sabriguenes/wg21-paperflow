#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

from __future__ import annotations

from assay.models import (
    AskOutput,
    GapOutput,
    ChecklistItem,
    ChunkEntry,
    CollectedItem,
    CollectedItems,
    CompoundOutput,
    DeriveOutput,
    FindingOutput,
    KilledFinding,
    PipelineState,
    StrengthOutput,
    SynthesisOutput,
)
from assay.references import RefEntry, UrlEntry
from assay.render import prepare_report_data, render_report


def _make_state() -> PipelineState:
    """Build a minimal PipelineState with known data for testing."""
    return PipelineState(
        paper_id="P9999R0",
        paper_title="Test Paper",
        model_name="test-model",
        service_name="test-svc",
        items=CollectedItems(
            claims=[CollectedItem(type="claim", line=1, quote="claim1")],
            evidence=[CollectedItem(type="evidence", line=2, quote="ev1")],
        ),
        gaps_by_lens={
            "Design": [GapOutput(
                chunk_index=0, item_quote="iq1", line=10,
                gap="gap1", why_important="matters", primary_lens="Design",
                severity="critical",
            )],
            "Performance": [GapOutput(
                chunk_index=1, item_quote="iq2", line=20,
                gap="gap2", why_important="matters", primary_lens="Performance",
                severity="minor",
            )],
        },
        asks=[AskOutput(target="committee", quote="do X", type="poll", line=1)],
        reference_inventory=[
            RefEntry(paper_id="P1234R1", raw_pid="P1234", url="http://x",
                     count=3, in_paperstore=True),
        ],
        standalone_urls=[
            UrlEntry(url="https://godbolt.org/z/abc", line=42),
        ],
        derive=DeriveOutput(
            central_claim="thesis",
            problem_statement="problem",
            scope_boundary="scope",
        ),
        findings=[
            FindingOutput(
                title="F1", severity="significant", lens="Design",
                quote="q1", line=5, explanation="expl1",
            ),
            FindingOutput(
                title="F2", severity="minor", lens="Usability",
                quote="q2", line=6, explanation="expl2",
            ),
        ],
        surviving=[FindingOutput(
            title="F1", severity="significant", lens="Design",
            quote="q1", line=5, explanation="expl1", test="t1",
        )],
        killed=[KilledFinding(
            finding_id=2, finding_title="F2", lens="Usability",
            challenge="resolution", reasoning="paper answers it",
        )],
        strengths=[StrengthOutput(
            title="S1", lens="Design", quote="strong",
            line=30, explanation="well done",
        )],
        checklist=[
            ChecklistItem(id=1, name="Motivating Examples", passed=True, location="sec 2", note=""),
            ChecklistItem(id=2, name="Design Principles", passed=False, location="", note="missing"),
        ],
        compounds=[CompoundOutput(
            name="Comp1", constituents=[1], mechanism="mech", emergent_risk="risk",
        )],
        synthesis=SynthesisOutput(
            verdict_label="Weakened",
            verdict_confidence="Medium",
            thesis_statement="The paper argues X",
            thesis_survives=True,
            verdict_statement="Central thesis text",
            dominant_dynamic="Comp1",
            critical_count=0,
            significant_count=1,
            major_findings=[FindingOutput(
                title="F1", severity="significant", lens="Design",
                quote="q1", line=5, explanation="expl1", test="t1",
            )],
            regular_findings=[],
        ),
        chunk_map=[
            ChunkEntry(index=0, heading="S1", start_line=1, end_line=10, char_count=100),
            ChunkEntry(index=1, heading="S2", start_line=11, end_line=20, char_count=100),
        ],
    )


def test_prepare_report_data_verdict():
    state = _make_state()
    data = prepare_report_data(state)
    assert data.verdict_label == "Weakened"
    assert data.confidence == "Medium"
    assert data.thesis_survives is True
    assert data.critical_count == 0
    assert data.significant_count == 1
    assert data.minor_count == 0


def test_prepare_report_data_findings_sorted():
    state = _make_state()
    data = prepare_report_data(state)
    assert len(data.major_findings) == 1
    assert data.major_findings[0].title == "F1"
    assert data.major_findings[0].number == 1


def test_prepare_report_data_inventory():
    state = _make_state()
    data = prepare_report_data(state)
    assert data.inventory.claim_count == 1
    assert data.inventory.evidence_count == 1
    assert data.inventory.gap_total == 2
    assert data.inventory.gap_critical == 1
    assert data.inventory.gap_minor == 1
    assert data.inventory.findings_killed == 1
    assert "resolution" in data.inventory.killed_breakdown


def test_prepare_report_data_checklist():
    state = _make_state()
    data = prepare_report_data(state)
    assert data.checklist_passed == 1
    assert data.checklist_total == 2
    assert data.checklist[0].passed_str == "pass"
    assert data.checklist[1].passed_str == "fail"


def test_prepare_report_data_compounds():
    state = _make_state()
    data = prepare_report_data(state)
    assert len(data.compounds) == 1
    assert data.compounds[0].name == "Comp1"
    assert data.compounds[0].constituents == [1]


def test_render_report_with_template():
    state = _make_state()
    template = '```jinja\n# {{ pid }} Assay\n\n{{ verdict_label }}\n```'
    result = render_report(state, template)
    assert "# P9999R0 Assay" in result
    assert "Weakened" in result


def test_render_report_no_template_raises():
    state = _make_state()
    import pytest
    with pytest.raises(RuntimeError, match="No Jinja template"):
        render_report(state, "No code blocks here")
