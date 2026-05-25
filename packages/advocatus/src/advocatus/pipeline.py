#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async examination pipeline for WG21 papers (Advocatus Diaboli).

All LLM-facing text comes from ``advocatus.md`` at runtime. This module
contains structural orchestration: hook definitions, step functions,
and the public API. ``advocatus.md`` is the upstream authority for
pipeline structure; this module conforms to it.

One-shot, fully batch. No human-in-the-loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from paperstore import (
    StorageBackend,
    loc_from_row,
)
from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.progress import ProgressCallback

from pipeline import (
    AgentBackend,
    StepContext,
    StepHooks,
    StepSpec,
    WebResearcher,
    build_pipeline,
    dispatch,
    load_sections,
    load_services,
    resolve_slots,
    run_task,
)
from pipeline.errors import (
    PaperNotConvertedError,
    PaperNotFoundError,
    PromptFileError,
)
from advocatus.models import (
    Articulus,
    ArticulusExam,
    CandidateCharge,
    ChargesOutput,
    DefensorChargeOutput,
    DossierEntry,
    ExamenOutput,
    MotivatioOutput,
    NotaMinor,
    PipelineState,
    Probatio,
    PublicRecordOutput,
    ScriptaOutput,
    StakeholdersOutput,
    SurvivingCharge,
    TabulaFontiumEntry,
    WeighCauseOutput,
)
from advocatus.render import render_relatio, render_trace

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 16384

# Step name constants - must match exactly the ## headers in advocatus.md.
_STEP_0_LOAD = "Step 0 - Load"
_STEP_1_READ_SCRIPTA = "Step 1 - Read Scripta"
_STEP_2_PUBLIC_RECORD = "Step 2 - Survey Public Record"
_STEP_3_STAKEHOLDERS = "Step 3 - Map Stakeholders"
_STEP_4_VERIFY_CITATIONS = "Step 4 - Verify Citations"
_STEP_5_EXAMINE = "Step 5 - Examine Articuli"
_STEP_6_FILE_CHARGES = "Step 6 - File Charges"
_STEP_7_DEFENSOR = "Step 7 - Defensor Cross-Examination"
_STEP_8_MOTIVATIO = "Step 8 - Motivatio"
_STEP_9_WEIGH = "Step 9 - Weigh the Cause"
_STEP_10_RENDER = "Step 10 - Render Relatio"


# -- Guards ------------------------------------------------------------------


def _guard_not_sine_causa(state: PipelineState) -> bool:
    """Skip steps once the Sine causa early exit fires."""
    return state.seal != "sine_causa"


# -- Step 0 - Load -----------------------------------------------------------


async def _pure_load(state: PipelineState, ctx: StepContext, spec: StepSpec) -> None:
    """Load paper + extract data from paperstore. Sine causa early exit."""
    assert ctx.backend is not None
    pid = ctx.pid

    try:
        meta = await asyncio.to_thread(ctx.backend.get_meta, pid)
    except MissingMetaError as exc:
        raise PaperNotFoundError(
            f"Paper '{pid}' not found in paperstore. "
            f"Run 'paperflow mailing <year>' to index it."
        ) from exc

    try:
        paper_md = await asyncio.to_thread(ctx.backend.get_paper_md, pid)
    except MissingPaperMdError as exc:
        raise PaperNotConvertedError(
            f"Paper '{pid}' has no converted markdown. "
            f"Run 'paperflow convert {pid}' first."
        ) from exc

    state.paper_id = pid
    state.paper_source = paper_md
    state.paper_title = meta.title or ""
    state.paper_audience = meta.target_group or ""
    state.paper_authors = list(meta.authors or [])

    claim_rows = await asyncio.to_thread(ctx.backend.get_claims, pid)
    evidence_rows = await asyncio.to_thread(ctx.backend.get_evidence, pid)
    rhetoric_rows = await asyncio.to_thread(ctx.backend.get_rhetoric, pid)
    citation_audit_rows = await asyncio.to_thread(ctx.backend.get_citation_audit, pid)
    external_rows = await asyncio.to_thread(ctx.backend.get_external_citations, pid)
    caput = await asyncio.to_thread(ctx.backend.get_caput_causae, pid)

    # Articuli seed: alive claims (skip tombstones)
    articuli_seed: list[Articulus] = []
    for row in claim_rows:
        if row.merged_into is not None:
            continue
        articuli_seed.append(Articulus(
            uid=row.uid,
            loc=loc_from_row(row),
            text=row.text,
            section=row.section,
            kind=row.kind if row.kind in ("normative", "factual") else "normative",
            question=row.question,
        ))
    state.dissect_articuli_seed = articuli_seed

    # Evidence as initial dossier (operator_provided label - it came from the
    # paper's own evidence, not from independent web search).
    dissect_evidence: list[DossierEntry] = []
    for row in evidence_rows:
        if row.merged_into is not None:
            continue
        dissect_evidence.append(DossierEntry(
            label="operator_provided",
            text=row.text,
            source_url="",
            relevance=f"({row.section}) supports: {row.supports}",
        ))
    state.dissect_evidence = dissect_evidence

    # Rhetoric (kept as Articulus-shaped objects for convenience; the field is
    # only used by the Defensor's Confessio challenge).
    dissect_rhetoric: list[Articulus] = []
    for row in rhetoric_rows:
        dissect_rhetoric.append(Articulus(
            uid=row.uid,
            loc=loc_from_row(row),
            text=row.text,
            section=row.section,
            kind="normative",
            question=f"[{row.marker_type}] target: {row.target}",
        ))
    state.dissect_rhetoric = dissect_rhetoric

    # Citation audit -> tabula fontium entries
    state.dissect_citation_audit = [
        TabulaFontiumEntry(
            paper_id=row.cited_paper_id,
            resolution_method=row.resolution_method,
            resolved=row.resolved,
            source_url=row.source_url,
            quote_match=row.quote_match,
            discrepancy=row.discrepancy,
        )
        for row in citation_audit_rows
    ]

    # External citations -> public_record dossier entries
    state.dissect_external_evidence = [
        DossierEntry(
            label="public_record",
            text=row.text,
            source_url=row.source_url,
            relevance=f"[{row.stance}] {row.finding}",
        )
        for row in external_rows
    ]

    state.dissect_caput_causae = caput.thesis if caput else None

    # Sine causa early exit: no claims means the tribunal does not convene.
    if not articuli_seed:
        state.seal = "sine_causa"
        state.central_thesis_survives = False
        state.one_sentence_assessment = (
            "The paper contains no claims to examine; the tribunal does not convene."
        )
        state.confidence = 1.0
        logger.info("Step 0: no articuli found, seal=sine_causa, jumping to Step 10")


# -- Step 1 - Read Scripta ---------------------------------------------------


def _prepare_read_scripta(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_1_READ_SCRIPTA, "")
    seed = state.dissect_articuli_seed or []
    seed_json = json.dumps(
        [a.model_dump(mode="json") for a in seed],
        ensure_ascii=False, default=str,
    )
    caput = state.dissect_caput_causae or "(no caput causae)"
    paper = state.paper_source or ""
    return (
        f"## Caput Causae\n\n{caput}\n\n"
        f"## Seed Articuli (with locs)\n\n{seed_json}\n\n"
        f"## Paper Source\n\n{paper}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_read_scripta(state: PipelineState, output: ScriptaOutput) -> None:
    state.central_thesis_recap = output.central_thesis_recap
    state.articuli = list(output.articuli)
    state.boundaries = list(output.boundaries)


# -- Step 2 - Survey Public Record -------------------------------------------


async def _pure_public_record(state: PipelineState, ctx: StepContext, spec: StepSpec) -> None:
    """Spawn parallel sub-agents for public-record search."""
    body = ctx.sections.get(_STEP_2_PUBLIC_RECORD, "")
    deep_search = ctx.tool_registry.get("deep_search")
    web_fetch = ctx.tool_registry.get("web_fetch")
    if deep_search is None or web_fetch is None:
        logger.warning("Step 2 skipped: deep_search/web_fetch not available")
        state.dossier = list(state.dissect_external_evidence or [])
        return

    research_agent = ctx.agents["tool"]
    domains = _public_record_domains(state)

    system = (
        "You are a research scout. Run web searches to find published "
        "positions on the assigned topic; return a compressed list of "
        "DossierEntry items labeled 'public_record'. No raw HTML. No "
        "speculation. Only structured findings."
    )

    async def _one_domain(domain: str) -> PublicRecordOutput:
        user_msg = (
            f"## Domain\n\n{domain}\n\n"
            f"## Paper Context\n\n"
            f"Paper: {state.paper_id} - {state.paper_title}\n"
            f"Thesis: {state.central_thesis_recap or state.dissect_caput_causae or '?'}\n\n"
            f"## Instructions\n\n{body}"
        )
        return await run_task(
            research_agent,
            system,
            user_msg,
            PublicRecordOutput,
            label=f"Step 2 - Survey Public Record ({domain})",
            debug_log=ctx.debug_log if ctx.debug else None,
            tools={"deep_search": deep_search, "web_fetch": web_fetch},
        )

    if not domains:
        state.dossier = list(state.dissect_external_evidence or [])
        return

    results = await asyncio.gather(
        *[_one_domain(d) for d in domains],
        return_exceptions=True,
    )

    dossier = list(state.dissect_external_evidence or [])
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Public record sub-agent failed: %s", r)
            continue
        dossier.extend(r.dossier_entries)
    state.dossier = dossier


def _public_record_domains(state: PipelineState) -> list[str]:
    """Pick search domains: paper number + thesis keywords + cited papers."""
    domains: list[str] = []
    if state.paper_id:
        domains.append(f"WG21 paper {state.paper_id} discussion")
    thesis = state.central_thesis_recap or state.dissect_caput_causae
    if thesis:
        domains.append(f"WG21 C++ committee positions: {thesis}")
    domains.append("WG21 mailing reflector commentary")
    return domains[:5]


# -- Step 3 - Map Stakeholders ----------------------------------------------


async def _pure_stakeholders(state: PipelineState, ctx: StepContext, spec: StepSpec) -> None:
    body = ctx.sections.get(_STEP_3_STAKEHOLDERS, "")
    deep_search = ctx.tool_registry.get("deep_search")
    web_fetch = ctx.tool_registry.get("web_fetch")
    if deep_search is None or web_fetch is None:
        state.stakeholders = []
        return

    research_agent = ctx.agents["tool"]
    targets = _stakeholder_targets(state)
    if not targets:
        state.stakeholders = []
        return

    system = (
        "You are a research scout. Find the stakeholder's published "
        "position on the topic. Return a list of Stakeholder items: "
        "name, position (one sentence), source URL, stance "
        "(opponent/ally/neutral). No HTML, no speculation."
    )

    async def _one_target(target: str) -> StakeholdersOutput:
        user_msg = (
            f"## Stakeholder Target\n\n{target}\n\n"
            f"## Paper Context\n\n"
            f"Paper: {state.paper_id} - {state.paper_title}\n"
            f"Thesis: {state.central_thesis_recap or '?'}\n\n"
            f"## Instructions\n\n{body}"
        )
        return await run_task(
            research_agent,
            system,
            user_msg,
            StakeholdersOutput,
            label=f"Step 3 - Map Stakeholders ({target})",
            debug_log=ctx.debug_log if ctx.debug else None,
            tools={"deep_search": deep_search, "web_fetch": web_fetch},
        )

    results = await asyncio.gather(
        *[_one_target(t) for t in targets],
        return_exceptions=True,
    )

    stakeholders: list = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Stakeholder sub-agent failed: %s", r)
            continue
        stakeholders.extend(r.stakeholders)
    state.stakeholders = stakeholders


def _stakeholder_targets(state: PipelineState) -> list[str]:
    """Pick targets: paper authors + named external evidence sources."""
    targets: list[str] = []
    for author in state.paper_authors[:3]:
        targets.append(f"WG21 author {author}")
    return targets[:5]


# -- Step 4 - Verify Citations -----------------------------------------------


async def _pure_verify_citations(state: PipelineState, ctx: StepContext, spec: StepSpec) -> None:
    """Pure conversion of citation_audit into tabula_fontium."""
    state.tabula_fontium = list(state.dissect_citation_audit or [])


# -- Step 5 - Examine Articuli -----------------------------------------------


async def _pure_examine(state: PipelineState, ctx: StepContext, spec: StepSpec) -> None:
    """One LLM call per articulus, in parallel via run_task + semaphore."""
    articuli = state.articuli or []
    if not articuli:
        state.exams = []
        return

    body = ctx.sections.get(_STEP_5_EXAMINE, "")
    synthesis_agent = ctx.agents["default"]
    dossier = state.dossier or []
    dossier_json = json.dumps(
        [d.model_dump(mode="json") for d in dossier],
        ensure_ascii=False, default=str,
    )
    boundaries = state.boundaries or []
    boundaries_json = json.dumps(
        [b.model_dump(mode="json") for b in boundaries],
        ensure_ascii=False, default=str,
    )

    system = (
        "You apply the three Examen tests (Veritas, Ratio, Auctoritas) "
        "to a single articulus. Be specific. If a test fails, name what "
        "contradicts the claim. Self-report confidence in [0.0, 1.0]."
    )

    async def _one(articulus: Articulus) -> ExamenOutput:
        articulus_json = json.dumps(
            articulus.model_dump(mode="json"),
            ensure_ascii=False, default=str,
        )
        user_msg = (
            f"## Articulus\n\n{articulus_json}\n\n"
            f"## Dossier\n\n{dossier_json}\n\n"
            f"## Boundaries\n\n{boundaries_json}\n\n"
            f"## Instructions\n\n{body}"
        )
        return await run_task(
            synthesis_agent,
            system,
            user_msg,
            ExamenOutput,
            label=f"Step 5 - Examine Articuli (uid {articulus.uid})",
            debug_log=ctx.debug_log if ctx.debug else None,
        )

    results = await asyncio.gather(
        *[_one(a) for a in articuli],
        return_exceptions=True,
    )

    exams: list[ArticulusExam] = []
    for a, r in zip(articuli, results):
        if isinstance(r, Exception):
            logger.warning("Examen failed for articulus uid %d: %s", a.uid, r)
            continue
        # Force the uid to the articulus's uid (defensive: the LLM might echo
        # something different).
        exam = r.exam.model_copy(update={"articulus_uid": a.uid})
        exams.append(exam)
    state.exams = exams


# -- Step 6 - File Charges ---------------------------------------------------


def _prepare_file_charges(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_6_FILE_CHARGES, "")
    articuli = state.articuli or []
    exams = state.exams or []
    failed_uids = {
        e.articulus_uid for e in exams
        if not (e.veritas.passed and e.ratio.passed and e.auctoritas.passed)
    }
    failed = [a for a in articuli if a.uid in failed_uids]
    failed_json = json.dumps(
        [a.model_dump(mode="json") for a in failed],
        ensure_ascii=False, default=str,
    )
    exams_json = json.dumps(
        [e.model_dump(mode="json") for e in exams if e.articulus_uid in failed_uids],
        ensure_ascii=False, default=str,
    )
    dossier_json = json.dumps(
        [d.model_dump(mode="json") for d in (state.dossier or [])],
        ensure_ascii=False, default=str,
    )
    return (
        f"## Articuli with Failed Tests\n\n{failed_json}\n\n"
        f"## Exam Results\n\n{exams_json}\n\n"
        f"## Dossier\n\n{dossier_json}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_file_charges(state: PipelineState, output: ChargesOutput) -> None:
    state.candidate_charges = _dedup_charges(list(output.candidate_charges))


def _dedup_charges(charges: list[CandidateCharge]) -> list[CandidateCharge]:
    """Deduplicate candidate charges by (quoted_text, gravamen).

    Positioned after Step 6 (File Charges) and before Step 7 (Defensor)
    because the cost asymmetry favors late dedup:

    - A missed dedup at the claim stage permanently loses a
      claim the Advocatus can never examine. Unrecoverable.
    - A missed dedup here costs one extra Defensor sub-agent call
      (~tokens + seconds). Recoverable by deduping objections in the
      Relatio if needed.
    - A bad dedup here loses a charge before the Defensor speaks.
      Safer than losing it before the Examen, but still a loss.

    Exact-match on (quoted_text, gravamen) means only true duplicates
    collapse. Two charges with different gravamens about the same quote
    survive (genuinely different objections). Two charges with the same
    gravamen about different quotes survive (different claims targeted).
    """
    seen: set[tuple[str, str]] = set()
    result: list[CandidateCharge] = []
    for c in charges:
        key = (c.quoted_text.strip(), c.gravamen.strip())
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


# -- Step 7 - Defensor Cross-Examination -------------------------------------


async def _pure_defensor(state: PipelineState, ctx: StepContext, spec: StepSpec) -> None:
    """One sub-agent per candidate charge. Isolated context."""
    charges = state.candidate_charges or []
    if not charges:
        state.defensor_results = []
        state.surviving_charges = []
        state.probationes = []
        state.notae_minores = []
        return

    body = ctx.sections.get(_STEP_7_DEFENSOR, "")
    synthesis_agent = ctx.agents["default"]
    boundaries_json = json.dumps(
        [b.model_dump(mode="json") for b in (state.boundaries or [])],
        ensure_ascii=False, default=str,
    )
    rhetoric_json = json.dumps(
        [m.model_dump(mode="json") for m in (state.dissect_rhetoric or [])],
        ensure_ascii=False, default=str,
    )
    stakeholders_json = json.dumps(
        [s.model_dump(mode="json") for s in (state.stakeholders or [])],
        ensure_ascii=False, default=str,
    )

    system = (
        "You are the Defensor Causae. Cross-examine ONE candidate "
        "charge through the six challenges in order: Confessio, "
        "Articulus, Testimonium, Humanitas, Prudentia, Dignitas. "
        "Stop at the first 'killed' or 'relegated' verdict, otherwise "
        "emit 'survived' for all six. You do not see the prosecution's "
        "reasoning. You see only this charge. Be willing to kill it."
    )

    async def _one(charge: CandidateCharge) -> DefensorChargeOutput:
        charge_json = json.dumps(
            charge.model_dump(mode="json"),
            ensure_ascii=False, default=str,
        )
        dossier_slice = _dossier_slice_for_charge(state, charge)
        slice_json = json.dumps(
            [d.model_dump(mode="json") for d in dossier_slice],
            ensure_ascii=False, default=str,
        )
        user_msg = (
            f"## Candidate Charge\n\n{charge_json}\n\n"
            f"## Relevant Dossier Slice\n\n{slice_json}\n\n"
            f"## Boundaries\n\n{boundaries_json}\n\n"
            f"## Concession Rhetoric\n\n{rhetoric_json}\n\n"
            f"## Stakeholders\n\n{stakeholders_json}\n\n"
            f"## Instructions\n\n{body}"
        )
        return await run_task(
            synthesis_agent,
            system,
            user_msg,
            DefensorChargeOutput,
            label=f"Step 7 - Defensor (charge uid {charge.articulus_uid})",
            debug_log=ctx.debug_log if ctx.debug else None,
        )

    results = await asyncio.gather(
        *[_one(c) for c in charges],
        return_exceptions=True,
    )

    defensor_results: list[DefensorChargeOutput] = []
    surviving: list[SurvivingCharge] = []
    probationes: list[Probatio] = []
    notae: list[NotaMinor] = []

    for charge, r in zip(charges, results):
        if isinstance(r, Exception):
            logger.warning("Defensor sub-agent failed for charge uid %d: %s",
                           charge.articulus_uid, r)
            continue
        # Force charge_uid to the articulus uid.
        result = r.model_copy(update={"charge_uid": charge.articulus_uid})
        defensor_results.append(result)

        if result.final == "survived":
            surviving.append(SurvivingCharge(
                articulus_uid=charge.articulus_uid,
                charge=charge,
                defensor_chain=list(result.challenges),
            ))
        elif result.final == "killed":
            killing_challenge = result.challenges[-1].challenge if result.challenges else "humanitas"
            killing_reasoning = result.challenges[-1].reasoning if result.challenges else ""
            probationes.append(Probatio(
                articulus_uid=charge.articulus_uid,
                killed_charge=charge,
                killing_challenge=killing_challenge,
                explanation=killing_reasoning,
            ))
        elif result.final == "relegated":
            notae.append(NotaMinor(
                uid=charge.articulus_uid,
                text=charge.gravamen,
            ))

    state.defensor_results = defensor_results
    state.surviving_charges = surviving
    state.probationes = probationes
    state.notae_minores = notae


def _dossier_slice_for_charge(
    state: PipelineState,
    charge: CandidateCharge,
) -> list[DossierEntry]:
    """Return dossier entries whose text overlaps the charge's terms."""
    full = state.dossier or []
    # Heuristic: match on words >= 5 chars from the contradicting evidence
    # and the quoted text.
    needles = set(
        w.lower() for w in (charge.contradicting_evidence + " " + charge.quoted_text).split()
        if len(w) >= 5
    )
    if not needles:
        return list(full)[:8]
    scored = []
    for d in full:
        text_words = set(w.lower() for w in d.text.split())
        score = len(needles & text_words)
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:8]] or list(full)[:4]


# -- Step 8 - Motivatio ------------------------------------------------------


def _prepare_motivatio(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_8_MOTIVATIO, "")
    survivors = state.surviving_charges or []
    survivors_json = json.dumps(
        [s.model_dump(mode="json") for s in survivors],
        ensure_ascii=False, default=str,
    )
    stakeholders_json = json.dumps(
        [s.model_dump(mode="json") for s in (state.stakeholders or [])],
        ensure_ascii=False, default=str,
    )
    return (
        f"## Surviving Charges\n\n{survivors_json}\n\n"
        f"## Stakeholders\n\n{stakeholders_json}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_motivatio(state: PipelineState, output: MotivatioOutput) -> None:
    state.objections = list(output.objections)


# -- Step 9 - Weigh the Cause ------------------------------------------------


def _prepare_weigh(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_9_WEIGH, "")
    objections_json = json.dumps(
        [o.model_dump(mode="json") for o in (state.objections or [])],
        ensure_ascii=False, default=str,
    )
    probationes_json = json.dumps(
        [p.model_dump(mode="json") for p in (state.probationes or [])],
        ensure_ascii=False, default=str,
    )
    exams = state.exams or []
    exam_summary = {
        "n_exams": len(exams),
        "n_failed_any": sum(
            1 for e in exams
            if not (e.veritas.passed and e.ratio.passed and e.auctoritas.passed)
        ),
        "mean_confidence": (
            sum(e.confidence for e in exams) / len(exams) if exams else 1.0
        ),
    }
    defensor = state.defensor_results or []
    defensor_summary = {
        "n_charges": len(defensor),
        "n_survived": sum(1 for d in defensor if d.final == "survived"),
        "n_killed": sum(1 for d in defensor if d.final == "killed"),
        "n_relegated": sum(1 for d in defensor if d.final == "relegated"),
    }
    return (
        f"## Central Thesis\n\n{state.central_thesis_recap or '(unset)'}\n\n"
        f"## Objections\n\n{objections_json}\n\n"
        f"## Probationes\n\n{probationes_json}\n\n"
        f"## Examen Summary\n\n{json.dumps(exam_summary)}\n\n"
        f"## Defensor Summary\n\n{json.dumps(defensor_summary)}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_weigh(state: PipelineState, output: WeighCauseOutput) -> None:
    state.seal = output.seal
    state.central_thesis_survives = output.central_thesis_survives
    state.one_sentence_assessment = output.one_sentence_assessment
    state.confidence = output.confidence


# -- Step 10 - Render Relatio ------------------------------------------------


async def _pure_render(state: PipelineState, ctx: StepContext, spec: StepSpec) -> None:
    state.relatio = render_relatio(state)


# -- Hook registry -----------------------------------------------------------

def _build_hooks(
    synthesis_agent: AgentBackend,
    research_agent: AgentBackend,
) -> dict[str, StepHooks]:
    """Build the step hooks dict with agents assigned."""
    return {
        _STEP_0_LOAD: StepHooks(custom=_pure_load),
        _STEP_1_READ_SCRIPTA: StepHooks(
            agent=synthesis_agent,
            output_type=ScriptaOutput,
            prepare=_prepare_read_scripta,
            extract=_extract_read_scripta,
            guard=_guard_not_sine_causa,
        ),
        _STEP_2_PUBLIC_RECORD: StepHooks(
            agent=research_agent,
            custom=_pure_public_record,
            guard=_guard_not_sine_causa,
        ),
        _STEP_3_STAKEHOLDERS: StepHooks(
            agent=research_agent,
            custom=_pure_stakeholders,
            guard=_guard_not_sine_causa,
        ),
        _STEP_4_VERIFY_CITATIONS: StepHooks(
            custom=_pure_verify_citations,
            guard=_guard_not_sine_causa,
        ),
        _STEP_5_EXAMINE: StepHooks(
            agent=synthesis_agent,
            custom=_pure_examine,
            guard=_guard_not_sine_causa,
        ),
        _STEP_6_FILE_CHARGES: StepHooks(
            agent=synthesis_agent,
            output_type=ChargesOutput,
            prepare=_prepare_file_charges,
            extract=_extract_file_charges,
            guard=_guard_not_sine_causa,
        ),
        _STEP_7_DEFENSOR: StepHooks(
            agent=synthesis_agent,
            custom=_pure_defensor,
            guard=_guard_not_sine_causa,
        ),
        _STEP_8_MOTIVATIO: StepHooks(
            agent=synthesis_agent,
            output_type=MotivatioOutput,
            prepare=_prepare_motivatio,
            extract=_extract_motivatio,
            guard=_guard_not_sine_causa,
        ),
        _STEP_9_WEIGH: StepHooks(
            agent=synthesis_agent,
            output_type=WeighCauseOutput,
            prepare=_prepare_weigh,
            extract=_extract_weigh,
            guard=_guard_not_sine_causa,
        ),
        _STEP_10_RENDER: StepHooks(custom=_pure_render),
    }


# -- Public API --------------------------------------------------------------


async def advocatus_paper(
    pid: str,
    backend: StorageBackend,
    *,
    service_overrides: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> str:
    """Examine a WG21 paper and return the Relatio markdown.

    Loads services from SERVICES.toml, builds agents, runs the
    11-step examination pipeline, and returns the Relatio. One-shot;
    no human input. Pass ``service_overrides`` to bind slots to
    specific services (e.g. ``{"default": "b200-r1"}``).

    With ``debug=True``, every LLM call is rendered to a debug
    transcript at ``backend.get_debug_md_path(pid)``.

    With ``trace=True``, a full per-step state dump is written to
    ``backend.get_trace_md_path(pid)`` after dispatch completes.

    With ``stop_after=N``, dispatch halts after pipeline step ``N``
    and the partial trace string is returned in place of the Relatio.

    Raises :class:`PromptFileError` if ``advocatus.md`` has structural
    problems. Raises :class:`PaperNotFoundError` or
    :class:`PaperNotConvertedError` if the prerequisite paperstore
    artifacts are missing.
    """
    registry = load_services()
    slots = resolve_slots(registry, service_overrides)

    default_svc, default_backend = slots["default"]
    tool_svc, tool_backend = slots.get("tool", slots["default"])

    synthesis_agent = AgentBackend(
        default_backend,
        max_tokens=MAX_OUTPUT_TOKENS,
        thinking_budget=4096,
        slot_name="default",
        service_name=default_svc,
    )
    research_agent = AgentBackend(
        tool_backend,
        max_tokens=MAX_OUTPUT_TOKENS,
        slot_name="tool",
        service_name=tool_svc,
    )

    agents = {
        "default": synthesis_agent,
        "tool": research_agent,
    }

    secs = dict(load_sections("advocatus", "advocatus.md"))

    if "System Prompt" not in secs:
        raise PromptFileError(
            "'System Prompt' section not found in advocatus.md. "
            f"Available sections: {sorted(secs)}"
        )

    hooks = _build_hooks(synthesis_agent, research_agent)
    pipeline = build_pipeline(secs, hooks)

    try:
        await asyncio.to_thread(backend.get_meta, pid)
    except MissingMetaError as exc:
        raise PaperNotFoundError(
            f"Paper '{pid}' not found in paperstore. "
            f"Run 'paperflow mailing <year>' to index it."
        ) from exc
    try:
        await asyncio.to_thread(backend.get_paper_md, pid)
    except MissingPaperMdError as exc:
        raise PaperNotConvertedError(
            f"Paper '{pid}' has no converted markdown. "
            f"Run 'paperflow convert {pid}' first."
        ) from exc
    state = PipelineState()

    async with WebResearcher() as researcher:
        tool_reg: dict[str, Callable[..., Any]] = {
            "deep_search": researcher.deep_search,
            "web_search": researcher.web_search,
            "web_fetch": researcher.web_fetch,
        }

        ctx = StepContext(
            sections=secs,
            agents=agents,
            researcher=researcher,
            backend=backend,
            debug=debug,
            pid=pid,
            tool_registry=tool_reg,
        )

        debug_path = backend.get_debug_md_path(pid)
        if debug:
            debug_path.unlink(missing_ok=True)

        trace_path = backend.get_trace_md_path(pid) if (trace or stop_after is not None) else None
        dp = debug_path if debug else None

        await dispatch(
            pipeline, state, ctx,
            stop_after=stop_after,
            on_progress=on_progress,
            trace_path=trace_path,
            debug_path=dp,
            render_trace_fn=lambda st, step: render_trace(st, step),
        )

    if stop_after is not None:
        return render_trace(state, stop_after)

    return state.relatio or ""


async def advocatus_since(
    month: str,
    backend: StorageBackend,
    *,
    service_overrides: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> list[dict[str, str | None]]:
    """Examine all papers with mailing_date >= ``month``.

    Iterates sequentially, calling :func:`advocatus_paper` for each.
    Per-paper errors are caught and logged; the loop continues.

    Returns a list of result dicts:
    ``{"paper_id": str, "status": "ok"|"error", "error": str|None}``.
    """
    papers = backend.list_papers_since(month)
    results: list[dict[str, str | None]] = []

    for paper in papers:
        pid = paper.paper_id
        try:
            relatio = await advocatus_paper(
                pid, backend,
                service_overrides=service_overrides,
                on_progress=on_progress,
                stop_after=stop_after,
                debug=debug,
                trace=trace,
            )
            out_path = backend.write_advocatus_md(pid, relatio)
            logger.info("Examined %s -> %s", pid, out_path)
            results.append({"paper_id": pid, "status": "ok", "error": None})
        except Exception as exc:  # batch worker firewall: log + continue per-paper
            logger.error("Failed to examine %s: %s", pid, exc)
            results.append({"paper_id": pid, "status": "error", "error": str(exc)})

    return results
