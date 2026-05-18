#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for the Relatio renderer."""

from __future__ import annotations

from paperstore import SourceLoc

from advocatus.models import (
    Articulus,
    ArticulusExam,
    Boundary,
    CandidateCharge,
    DefensorChallenge,
    DefensorChargeOutput,
    DossierEntry,
    ExamOutcome,
    Motivatio,
    NotaMinor,
    Objection,
    PipelineState,
    Probatio,
    Stakeholder,
    SurvivingCharge,
    TabulaFontiumEntry,
)
from advocatus.render import render_debug_md, render_relatio, render_trace


def _loc(line=1):
    return SourceLoc(line=line, start_char=0, end_char=10)


def _articulus(line=1):
    return Articulus(
        uid=line, loc=_loc(line), text="X should be Y", section="2.1",
        kind="normative", question="Q?",
    )


def _charge(line=1):
    return CandidateCharge(
        articulus_uid=line,
        quoted_text="X is the best approach",
        failed_test="auctoritas",
        contradicting_evidence="P1234R3 reaches the opposite conclusion.",
        gravamen="The cited source contradicts the inference.",
    )


def test_render_sine_causa():
    state = PipelineState(
        paper_id="P1000R0", paper_title="Schedule",
        seal="sine_causa",
        one_sentence_assessment="The paper contains no claims to examine.",
        confidence=1.0,
    )
    out = render_relatio(state)
    assert "***Sine causa.***" in out
    assert "tribunal does not convene" in out
    assert "P1000R0" in out
    # No objections / probationes / acta sections under sine_causa
    assert "## Objections" not in out
    assert "## Acta" not in out


def test_render_nihil_obstat_no_objections():
    state = PipelineState(
        paper_id="P2000R0", paper_title="Strong Paper",
        seal="nihil_obstat",
        one_sentence_assessment="The cause is sustained.",
        confidence=0.92,
        articuli=[_articulus(5), _articulus(10)],
        candidate_charges=[],
        objections=[],
    )
    out = render_relatio(state)
    assert "***Nihil obstat.***" in out
    assert "0.92" in out
    assert "## Objections" not in out  # no objections section when empty
    assert "## Acta" in out
    assert "2 articuli examined" in out
    assert "0 candidate charges" in out


def test_render_cum_objectionibus_full():
    charge = _charge(10)
    surviving = SurvivingCharge(
        articulus_uid=charge.articulus_uid, charge=charge, defensor_chain=[],
    )
    motivatio = Motivatio(
        adversary="UK NB", forum="nb_comment",
        damage="revision_forcing",
        explanation="UK has flagged X-related concerns previously.",
    )
    obj_high = Objection(
        articulus_uid=charge.articulus_uid, charge=surviving,
        motivatio=motivatio, severity="high",
    )
    obj_low = Objection(
        articulus_uid=charge.articulus_uid, charge=surviving,
        motivatio=motivatio, severity="low",
    )

    killed = _charge(20)
    # Distinct section on articulus 20 so the Probatio heading uniquely
    # identifies which articulus the section came from.
    art_20 = _articulus(20).model_copy(update={"section": "2.20"})
    p = Probatio(
        articulus_uid=killed.articulus_uid,
        killed_charge=killed,
        killing_challenge="prudentia",
        explanation="No rational opponent would volunteer this.",
    )

    nota = NotaMinor(uid=99, text="formatting inconsistency in section 3")

    tabula = TabulaFontiumEntry(
        paper_id="P1234R3", resolution_method="wg21_link",
        resolved=True, source_url="https://wg21.link/p1234r3",
        quote_match="exact",
    )

    defensor = DefensorChargeOutput(
        charge_uid=killed.articulus_uid,
        challenges=[
            DefensorChallenge(challenge="confessio", verdict="survived",
                              reasoning="not conceded", confidence=0.8),
            DefensorChallenge(challenge="prudentia", verdict="killed",
                              reasoning="self-defeating", confidence=0.9),
        ],
        final="killed",
    )

    state = PipelineState(
        paper_id="P3000R0", paper_title="Contested Paper",
        seal="cum_objectionibus",
        one_sentence_assessment="The cause proceeds with one objection of consequence.",
        confidence=0.78,
        articuli=[_articulus(10), art_20],
        candidate_charges=[_charge(10), _charge(20)],
        defensor_results=[defensor],
        surviving_charges=[surviving],
        probationes=[p],
        notae_minores=[nota],
        objections=[obj_low, obj_high],  # out of severity order
        tabula_fontium=[tabula],
    )

    out = render_relatio(state)

    # Verdict first
    seal_pos = out.index("***Cum objectionibus.***")
    obj_pos = out.index("## Objections")
    assert seal_pos < obj_pos

    # Objections rendered in severity order: High before Low
    high_pos = out.index("Severity: High")
    low_pos = out.index("Severity: Low")
    assert high_pos < low_pos

    # Each section present
    assert "## Probationes" in out
    assert "Prudentia" in out
    assert "## Tabula Fontium" in out
    assert "P1234R3" in out
    assert "## Acta" in out
    assert "Kill attribution by challenge" in out
    assert "Prudentia: 1" in out
    assert "## Notae Minores" in out
    assert "formatting inconsistency" in out

    # Provenance from the originating articuli surfaces in the output.
    # Articulus 20 was killed → its section heads the Probatio entry.
    assert "2.20" in out
    # Articulus 10 carries the surviving objection → its charge's
    # quoted text is rendered verbatim under each Objection.
    assert "X is the best approach" in out


def test_render_omits_empty_optional_sections():
    state = PipelineState(
        paper_id="P4000R0", paper_title="Minimal",
        seal="nihil_obstat",
        one_sentence_assessment="Sustained.",
        confidence=1.0,
    )
    out = render_relatio(state)
    assert "## Objections" not in out
    assert "## Probationes" not in out
    assert "## Tabula Fontium" not in out
    assert "## Notae Minores" not in out
    # Acta is always present
    assert "## Acta" in out


# ---- render_trace ---------------------------------------------------------


def _full_state_for_trace() -> PipelineState:
    """Build a state with every field populated, suitable for full-trace tests."""
    a = _articulus(5)
    a2 = _articulus(15)
    return PipelineState(
        paper_id="P5000R0",
        paper_title="Sample Paper",
        paper_authors=["Alice", "Bob"],
        dissect_articuli_seed=[a, a2],
        dissect_evidence=[
            DossierEntry(label="operator_provided", text="evidence text"),
        ],
        dissect_rhetoric=[a],
        dissect_caput_causae="The paper argues for X.",
        dissect_citation_audit=[
            TabulaFontiumEntry(paper_id="P9999R0", resolution_method="wg21_link",
                               resolved=True, source_url="https://wg21.link/p9999r0"),
        ],
        dissect_external_evidence=[
            DossierEntry(label="public_record", text="external", source_url="https://x.com"),
        ],
        central_thesis_recap="X holds.",
        articuli=[a, a2],
        boundaries=[Boundary(uid=7, loc=_loc(7), text="we do not propose Y", kind="disclaim")],
        dossier=[DossierEntry(label="public_record", text="public finding")],
        stakeholders=[Stakeholder(name="UK NB", position="opposes Y", stance="opponent")],
        tabula_fontium=[
            TabulaFontiumEntry(paper_id="P9999R0", resolution_method="wg21_link",
                               resolved=True),
        ],
        exams=[
            ArticulusExam(
                articulus_uid=a.uid,
                veritas=ExamOutcome(passed=True, reasoning="ok"),
                ratio=ExamOutcome(passed=False, reasoning="gap"),
                auctoritas=ExamOutcome(passed=True, reasoning="ok"),
                confidence=0.6,
            ),
        ],
        candidate_charges=[_charge(5)],
        defensor_results=[
            DefensorChargeOutput(
                charge_uid=5,
                challenges=[
                    DefensorChallenge(challenge="prudentia", verdict="killed",
                                      reasoning="self-defeating", confidence=0.8),
                ],
                final="killed",
            ),
        ],
        surviving_charges=[],
        probationes=[
            Probatio(articulus_uid=5, killed_charge=_charge(5),
                     killing_challenge="prudentia",
                     explanation="rational opponent would not press"),
        ],
        notae_minores=[],
        objections=[],
        seal="nihil_obstat",
        central_thesis_survives=True,
        one_sentence_assessment="The cause is sustained.",
        confidence=0.85,
    )


def test_render_trace_full_state_has_all_sections():
    state = _full_state_for_trace()
    out = render_trace(state, stop_step=9)
    for header in (
        "# Trace: P5000R0",
        "## 0. Load",
        "## 1. Read Scripta",
        "## 2. Survey Public Record",
        "## 3. Map Stakeholders",
        "## 4. Verify Citations",
        "## 5. Examine Articuli",
        "## 6. File Charges",
        "## 7. Defensor Cross-Examination",
        "## 8. Motivatio",
        "## 9. Weigh the Cause",
    ):
        assert header in out, f"missing section: {header}"
    assert "Mean confidence" in out
    assert "killed: 1" in out
    assert "**Confidence:** 0.85" in out


def test_render_trace_stops_at_stop_step():
    state = _full_state_for_trace()
    out = render_trace(state, stop_step=3)
    assert "## 0. Load" in out
    assert "## 3. Map Stakeholders" in out
    assert "## 4. Verify Citations" not in out
    assert "## 9. Weigh the Cause" not in out


def test_render_trace_handles_empty_state():
    """Trace must not crash when post-step fields are None (partial run)."""
    state = PipelineState(paper_id="P0000R0", paper_title="Empty")
    out = render_trace(state, stop_step=9)
    assert out.startswith("# Trace: P0000R0")
    # Doesn't crash; all sections rendered with their "no data" branches.


# ---- render_debug_md ------------------------------------------------------


def test_render_debug_md_handles_minimal_result():
    """Smoke: render_debug_md does not crash on a result-shaped object
    with no messages and no output."""
    class _Result:
        def all_messages(self):
            return []
    out = render_debug_md(_Result(), "Step X")
    assert out.startswith("# Step X")
