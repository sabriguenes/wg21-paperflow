#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

from __future__ import annotations

from assay.models import PipelineState
from assay.render import prepare_report_data, render_report


def _make_state() -> PipelineState:
    """Build a minimal PipelineState with known data for testing."""
    return PipelineState(
        paper_id="P9999R0",
        paper_title="Test Paper",
        model_name="test-model",
        service_name="test-svc",
        items={
            "claims": [{"line": 1, "quote": "claim1"}],
            "evidence": [{"line": 2, "quote": "ev1"}],
            "concessions": [],
            "questions": [],
            "dependencies": [],
        },
        breadcrumbs_by_lens={
            "Design": [{"severity": "critical", "gap": "gap1", "line": 10, "chunk_index": 0}],
            "Performance": [{"severity": "minor", "gap": "gap2", "line": 20, "chunk_index": 1}],
        },
        asks=[{"target": "committee", "quote": "do X", "type": "poll"}],
        reference_registry=[
            {"ref_label": "R1", "relationship": "citation", "url": "http://x", "mention_count": 3},
        ],
        derive={"central_claim": "thesis"},
        findings=[
            {"title": "F1", "severity": "significant", "lens": "Design"},
            {"title": "F2", "severity": "minor", "lens": "Usability"},
        ],
        surviving=[
            {"title": "F1", "severity": "significant", "lens": "Design",
             "quote": "q1", "line": 5, "explanation": "expl1", "test": "t1",
             "promotion_reason": "compound"},
        ],
        killed=[
            {"finding_title": "F2", "lens": "Usability",
             "challenge": "resolution", "reasoning": "paper answers it"},
        ],
        strengths=[
            {"title": "S1", "quote": "strong", "line": 30, "explanation": "well done"},
        ],
        checklist=[
            {"id": "SD4-1", "name": "Motivating Examples", "passed": True, "location": "sec 2", "note": ""},
            {"id": "SD4-2", "name": "Design Principles", "passed": False, "location": "", "note": "missing"},
        ],
        compounds=[
            {"name": "Comp1", "constituents": ["F1"], "mechanism": "mech", "emergent_risk": "risk"},
        ],
        synthesis={
            "verdict": "Weakened",
            "verdict_confidence": "Medium",
            "thesis_statement": "The paper argues X",
            "thesis_survives": True,
            "central_thesis": "Central thesis text",
            "dominant_dynamic": "Comp1",
            "critical_count": 0,
            "significant_count": 1,
            "major_findings": [
                {"title": "F1", "severity": "significant", "lens": "Design",
                 "quote": "q1", "line": 5, "explanation": "expl1", "test": "t1"},
            ],
            "regular_findings": [],
        },
        chunk_map=[{"index": 0}, {"index": 1}],
    )


def test_prepare_report_data_verdict():
    state = _make_state()
    data = prepare_report_data(state)
    assert data.verdict == "Weakened"
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
    assert data.inventory.breadcrumb_total == 2
    assert data.inventory.breadcrumb_critical == 1
    assert data.inventory.breadcrumb_minor == 1
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
    assert data.compounds[0].constituents == ["F1"]


def test_render_report_with_template():
    state = _make_state()
    template = '```jinja\n# {{ pid }} Assay\n\n{{ verdict }}\n```'
    result = render_report(state, template)
    assert "# P9999R0 Assay" in result
    assert "Weakened" in result


def test_render_report_no_template_raises():
    state = _make_state()
    import pytest
    with pytest.raises(RuntimeError, match="No Jinja template"):
        render_report(state, "No code blocks here")
