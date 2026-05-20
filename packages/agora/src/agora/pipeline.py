#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async planning pipeline for WG21 paper Reddit threads (Agora / The Mod).

All LLM-facing text comes from ``agora.md`` at runtime. This module
contains structural orchestration: hook definitions, step functions,
and the public API. ``agora.md`` is the upstream authority for pipeline
structure; this module conforms to it.

One-shot, fully batch. No human-in-the-loop.

The pipeline runs Steps 0-7 (analysis phase). It plans the thread and
writes ``{pid}.agora.json`` to paperstore. It does **not** generate
reply text, characters, vote counts, or Reddit furniture; those
remain ``None`` on the emitted ``Thread`` and are filled later by a
future generation phase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from paperstore import StorageBackend
from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.progress import ProgressCallback

from pipeline import (
    AgentBackend,
    StepContext,
    StepHooks,
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
    PaperNotDissectedError,
    PaperNotFoundError,
    PromptFileError,
    StepError,
    ValidationStepError,
)
from agora.models import (
    CalibrationOutput,
    EncountersOutput,
    PipelineState,
    ResearchAgentReport,
    ResearchSummary,
    SkeletonOutput,
    SmellTestOutput,
    SubmissionOutput,
    Subreddit,
    Thread,
)
from agora.render import render_trace

logger = logging.getLogger(__name__)

# Step name constants - must match exactly the ## headers in agora.md.
_STEP_0_LOAD = "Step 0 - Load"
_STEP_1_SMELL_TEST = "Step 1 - Smell Test"
_STEP_2_RESEARCH = "Step 2 - Research"
_STEP_3_CALIBRATE = "Step 3 - Calibrate"
_STEP_4_SUBMISSION = "Step 4 - Submission"
_STEP_5_SKELETON = "Step 5 - Skeleton"
_STEP_6_ENCOUNTERS = "Step 6 - Encounters"
_STEP_7_SERIALIZE = "Step 7 - Serialize"


# -- Subreddit routing -------------------------------------------------------

_SUBREDDIT_BY_AUDIENCE: dict[str, Subreddit] = {
    "EWG": "r/ewg",
    "EWGI": "r/ewg",
    "SG": "r/ewg",
    "PLENARY": "r/ewg",
    "LEWG": "r/lewg",
    "LEWGI": "r/lewg",
    "CWG": "r/cwg",
    "LWG": "r/lwg",
}

_PAPER_ID_RE = re.compile(r"^(P\d+)R(\d+)$", re.IGNORECASE)


def _route_subreddit(audience: str) -> Subreddit:
    """Pick a subreddit by first target-group token. Defaults to ``r/ewg``."""
    if not audience:
        return "r/ewg"
    for token in re.split(r"[\s,;/]+", audience.strip()):
        key = token.strip().upper()
        if not key:
            continue
        for prefix, sub in _SUBREDDIT_BY_AUDIENCE.items():
            if key.startswith(prefix):
                return sub
    return "r/ewg"


def _split_paper_id(pid: str) -> tuple[str, int]:
    """Return ``(paper_number, revision)`` for ``Pnnnn[Rk]``."""
    m = _PAPER_ID_RE.match(pid.strip())
    if not m:
        return pid.strip().upper(), 0
    return m.group(1).upper(), int(m.group(2))


# -- Guards ------------------------------------------------------------------


def _guard_encounter_count_positive(state: PipelineState) -> bool:
    """Skip Step 6 when calibration produced zero encounters."""
    return (state.encounter_count or 0) > 0


# -- Step 0 - Load -----------------------------------------------------------


async def _pure_load(state: PipelineState, ctx: StepContext) -> None:
    """Load paper + dissect data from paperstore, route subreddit, detect case."""
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

    if not meta.dissect_path:
        raise PaperNotDissectedError(
            f"Paper '{pid}' has no dissect output. "
            f"Run 'paperflow dissect {pid}' first."
        )

    state.paper_id = pid
    state.paper_source = paper_md
    state.paper_title = meta.title or ""
    state.paper_authors = list(meta.authors or [])
    state.paper_audience = meta.target_group or ""
    state.paper_date = meta.document_date or ""
    state.paper_url = meta.url or ""
    paper_number, revision = _split_paper_id(pid)
    state.paper_number = paper_number
    state.paper_revision = revision
    state.subreddit = _route_subreddit(state.paper_audience)

    # Load every dissect artifact as raw row dicts; downstream steps
    # pick the fields they need without rebuilding typed models here.
    claim_rows = await asyncio.to_thread(ctx.backend.get_claims, pid)
    evidence_rows = await asyncio.to_thread(ctx.backend.get_evidence, pid)
    marker_rows = await asyncio.to_thread(ctx.backend.get_rhetoric, pid)
    citation_audit_rows = await asyncio.to_thread(ctx.backend.get_citation_audit, pid)
    external_rows = await asyncio.to_thread(ctx.backend.get_external_citations, pid)
    caput = await asyncio.to_thread(ctx.backend.get_caput_causae, pid)

    state.dissect_claims = [_row_to_dict(r) for r in claim_rows]
    state.dissect_evidence = [_row_to_dict(r) for r in evidence_rows]
    state.dissect_markers = [_row_to_dict(r) for r in marker_rows]
    state.dissect_citation_audit = [_row_to_dict(r) for r in citation_audit_rows]
    state.dissect_external_citations = [_row_to_dict(r) for r in external_rows]
    state.dissect_caput_causae = caput.thesis if caput else None

    state.revision_case, state.prior_revision = await _detect_revision_case(
        ctx.backend, paper_number, revision, pid,
    )
    logger.info(
        "Step 0: %s (R%d) routed to %s; case=%s; prior=%s",
        pid, revision, state.subreddit,
        state.revision_case, state.prior_revision or "-",
    )


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a frozen-dataclass row to a plain dict for JSON injection."""
    if hasattr(row, "__dict__"):
        return dict(row.__dict__)
    if hasattr(row, "_asdict"):
        return row._asdict()
    return {k: getattr(row, k) for k in row.__dataclass_fields__}  # type: ignore[attr-defined]


async def _detect_revision_case(
    backend: StorageBackend,
    paper_number: str,
    revision: int,
    pid: str,
) -> tuple[str, str | None]:
    """Return ``(revision_case, prior_revision)``.

    ``A`` = no prior agora json for any revision of ``paper_number``.
    ``B`` = an agora json exists for this exact revision (re-run).
    ``C`` = an agora json exists for a strictly lower revision.
    """
    all_ids = await asyncio.to_thread(backend.list_all_paper_ids)
    candidate_revs: list[int] = []
    for other in all_ids:
        m = _PAPER_ID_RE.match(other)
        if not m or m.group(1).upper() != paper_number:
            continue
        candidate_revs.append(int(m.group(2)))

    has_same = False
    lower_revs: list[int] = []
    for r in candidate_revs:
        if r == revision:
            # Check if that revision actually has an agora artifact.
            try:
                await asyncio.to_thread(backend.get_agora_path, f"{paper_number}R{r}")
                has_same = True
            except Exception:
                pass
        elif r < revision:
            try:
                await asyncio.to_thread(backend.get_agora_path, f"{paper_number}R{r}")
                lower_revs.append(r)
            except Exception:
                continue

    if lower_revs:
        prior = f"{paper_number}R{max(lower_revs)}"
        return "C", prior
    if has_same:
        return "B", None
    return "A", None


# -- Step 1 - Smell Test -----------------------------------------------------


def _prepare_smell_test(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_1_SMELL_TEST, "")
    return (
        f"## Paper Identity\n\n"
        f"- id: {state.paper_id}\n"
        f"- title: {state.paper_title}\n"
        f"- authors: {', '.join(state.paper_authors)}\n"
        f"- audience: {state.paper_audience}\n"
        f"- date: {state.paper_date}\n\n"
        f"## Caput Causae (from dissect)\n\n"
        f"{state.dissect_caput_causae or '(none recorded)'}\n\n"
        f"## Dissect Claims\n\n"
        f"{_json(state.dissect_claims)}\n\n"
        f"## Dissect Evidence\n\n"
        f"{_json(state.dissect_evidence)}\n\n"
        f"## Dissect Markers\n\n"
        f"{_json(state.dissect_markers)}\n\n"
        f"## Paper Source\n\n{state.paper_source or ''}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_smell_test(state: PipelineState, output: SmellTestOutput) -> None:
    state.paper_type = output.paper_type
    state.technical_anchors = list(output.technical_anchors)
    state.hot_takes = list(output.hot_takes)
    state.tangent_magnets = list(output.tangent_magnets)
    state.misconception_traps = list(output.misconception_traps)
    state.design_tensions = list(output.design_tensions)


# -- Step 2 - Research -------------------------------------------------------

_RESEARCH_DOMAINS = ("public_reception", "committee_history", "author_ecosystem")

_RESEARCH_AGENT_PROMPTS = {
    "public_reception": (
        "You are a research scout focusing on PUBLIC RECEPTION. Search "
        "Reddit (r/cpp, r/programming), Hacker News, blog posts, "
        "Twitter / Mastodon, and conference talk recordings for mentions "
        "of the paper number, the paper title, or the lead author. Return "
        "a ResearchAgentReport with agent='public_reception'. Findings "
        "must be paraphrased prose under ~200 words. Quote nothing. "
        "List the URLs that mattered. Give coarse heat and interest signals."
    ),
    "committee_history": (
        "You are a research scout focusing on COMMITTEE HISTORY. Search "
        "wg21.link, open-std.org, isocpp.org, and committee blog "
        "summaries for prior revisions of this paper, prior papers in "
        "the same topic family, and any publicly visible mailing-list "
        "or reflector traffic. Return a ResearchAgentReport with "
        "agent='committee_history'. ~200 word paraphrased findings. "
        "List the URLs that mattered. Give coarse heat and interest signals."
    ),
    "author_ecosystem": (
        "You are a research scout focusing on the AUTHOR + ECOSYSTEM. "
        "Search for the lead author's other WG21 papers, talks, books, "
        "and library implementations touching the subject. Return a "
        "ResearchAgentReport with agent='author_ecosystem'. ~200 word "
        "paraphrased findings. List the URLs that mattered. Give coarse "
        "heat and interest signals."
    ),
}


async def _pure_research(state: PipelineState, ctx: StepContext) -> None:
    """Dispatch three sub-agents in parallel and gather a ResearchSummary."""
    body = ctx.sections.get(_STEP_2_RESEARCH, "")
    deep_search = ctx.tool_registry.get("deep_search")
    web_fetch = ctx.tool_registry.get("web_fetch")

    if deep_search is None or web_fetch is None:
        logger.warning("Step 2 skipped: web tools not available; emitting empty research summary")
        state.research_summary = _empty_research_summary()
        return

    research_agent = ctx.agents["tool"]
    tools = {"deep_search": deep_search, "web_fetch": web_fetch}
    anchors_json = _json(
        [a.model_dump(mode="json") for a in (state.technical_anchors or [])]
    )
    paper_ctx = (
        f"## Paper\n\n"
        f"- id: {state.paper_id}\n"
        f"- title: {state.paper_title}\n"
        f"- authors: {', '.join(state.paper_authors)}\n"
        f"- audience: {state.paper_audience}\n\n"
        f"## Technical Anchors\n\n{anchors_json}\n\n"
        f"## Instructions\n\n{body}\n"
    )

    async def _one(agent_name: str) -> ResearchAgentReport:
        return await run_task(
            research_agent,
            _RESEARCH_AGENT_PROMPTS[agent_name],
            paper_ctx,
            ResearchAgentReport,
            label=f"Step 2 - Research ({agent_name})",
            debug_log=ctx.debug_log if ctx.debug else None,
            tools=tools,
        )

    results = await asyncio.gather(
        *[_one(name) for name in _RESEARCH_DOMAINS],
        return_exceptions=True,
    )

    reports: dict[str, ResearchAgentReport] = {}
    for name, r in zip(_RESEARCH_DOMAINS, results):
        if isinstance(r, Exception):
            logger.warning("Research sub-agent %s failed: %s", name, r)
            reports[name] = _fallback_report(name)
        else:
            # Force the agent label to the slot name to defend against
            # the model echoing something different.
            reports[name] = r.model_copy(update={"agent": name})

    state.research_summary = ResearchSummary(
        public_reception=reports["public_reception"],
        committee_history=reports["committee_history"],
        author_ecosystem=reports["author_ecosystem"],
    )


def _fallback_report(agent: str) -> ResearchAgentReport:
    return ResearchAgentReport(
        agent=agent,  # type: ignore[arg-type]
        findings="(research sub-agent failed; no findings)",
        sources=[],
        heat_signal="warm",
        interest_signal="relevant",
    )


def _empty_research_summary() -> ResearchSummary:
    return ResearchSummary(
        public_reception=_fallback_report("public_reception"),
        committee_history=_fallback_report("committee_history"),
        author_ecosystem=_fallback_report("author_ecosystem"),
    )


# -- Step 3 - Calibrate ------------------------------------------------------


def _prepare_calibrate(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_3_CALIBRATE, "")
    rs = state.research_summary
    if rs is None:
        research = "(no research summary)"
    else:
        research = _json(rs.model_dump(mode="json"))
    return (
        f"## Paper Identity\n\n"
        f"- id: {state.paper_id}\n"
        f"- type: {state.paper_type}\n"
        f"- audience: {state.paper_audience}\n"
        f"- authors: {', '.join(state.paper_authors)}\n\n"
        f"## Technical Anchors\n\n"
        f"{_json([a.model_dump(mode='json') for a in (state.technical_anchors or [])])}\n\n"
        f"## Hot Takes\n\n{_json(state.hot_takes)}\n\n"
        f"## Design Tensions\n\n"
        f"{_json([t.model_dump(mode='json') for t in (state.design_tensions or [])])}\n\n"
        f"## Research Summary\n\n{research}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_calibrate(state: PipelineState, output: CalibrationOutput) -> None:
    state.heat = output.heat
    state.interest = output.interest
    state.target_comment_count = output.target_comment_count
    state.encounter_count = output.encounter_count
    state.signal_count = output.signal_count
    state.noise_count = output.noise_count


# -- Step 4 - Submission -----------------------------------------------------


def _prepare_submission(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_4_SUBMISSION, "")
    rs = state.research_summary
    research = _json(rs.model_dump(mode="json")) if rs else "(none)"
    return (
        f"## Paper Identity\n\n"
        f"- id: {state.paper_id} (number={state.paper_number}, R{state.paper_revision})\n"
        f"- title: {state.paper_title}\n"
        f"- authors: {', '.join(state.paper_authors)}\n"
        f"- audience: {state.paper_audience}\n"
        f"- date: {state.paper_date}\n"
        f"- paperstore url: {state.paper_url or '(none)'}\n"
        f"- subreddit: {state.subreddit}\n"
        f"- revision case: {state.revision_case}"
        f" (prior: {state.prior_revision or '-'})\n\n"
        f"## Heat / Interest\n\n"
        f"- heat: {state.heat}\n"
        f"- interest: {state.interest}\n"
        f"- paper_type: {state.paper_type}\n\n"
        f"## Technical Anchors\n\n"
        f"{_json([a.model_dump(mode='json') for a in (state.technical_anchors or [])])}\n\n"
        f"## Hot Takes\n\n{_json(state.hot_takes)}\n\n"
        f"## Research Summary\n\n{research}\n\n"
        f"## Paper Source\n\n{state.paper_source or ''}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_submission(state: PipelineState, output: SubmissionOutput) -> None:
    state.submission_title = output.submission_title
    state.submission_body = output.submission_body
    state.submission_link = output.submission_link or _fallback_link(state)
    state.submission_flair = output.submission_flair
    # Trust Step 0's detected revision case unless the model overrides;
    # only allow upgrades from A -> C (model spotted a delta we missed).
    if output.revision_case == "C" and state.revision_case == "A":
        state.revision_case = "C"


def _fallback_link(state: PipelineState) -> str:
    if state.paper_id:
        return f"https://wg21.link/{state.paper_id.lower()}"
    return state.paper_url or ""


# -- Step 5 - Skeleton -------------------------------------------------------


def _prepare_skeleton(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_5_SKELETON, "")
    return (
        f"## Plan Targets (from Step 3)\n\n"
        f"- heat: {state.heat}\n"
        f"- interest: {state.interest}\n"
        f"- target_comment_count: {state.target_comment_count}\n"
        f"- signal_count: {state.signal_count}\n"
        f"- noise_count: {state.noise_count}\n"
        f"- encounter_count: {state.encounter_count}\n"
        f"- subreddit: {state.subreddit}\n\n"
        f"## Technical Anchors (every anchor must be addressed by >=1 slot)\n\n"
        f"{_json([a.model_dump(mode='json') for a in (state.technical_anchors or [])])}\n\n"
        f"## Hot Takes\n\n{_json(state.hot_takes)}\n\n"
        f"## Tangent Magnets\n\n{_json(state.tangent_magnets)}\n\n"
        f"## Misconception Traps\n\n{_json(state.misconception_traps)}\n\n"
        f"## Design Tensions\n\n"
        f"{_json([t.model_dump(mode='json') for t in (state.design_tensions or [])])}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_skeleton(state: PipelineState, output: SkeletonOutput) -> None:
    state.replies = list(output.replies)
    state.encounter_slot_groups = [list(g) for g in output.encounter_slot_groups]


# -- Step 6 - Encounters -----------------------------------------------------


def _prepare_encounters(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_6_ENCOUNTERS, "")
    groups = state.encounter_slot_groups or []
    return (
        f"## Design Tensions\n\n"
        f"{_json([t.model_dump(mode='json') for t in (state.design_tensions or [])])}\n\n"
        f"## Technical Anchors\n\n"
        f"{_json([a.model_dump(mode='json') for a in (state.technical_anchors or [])])}\n\n"
        f"## Heat / Interest\n\n"
        f"- heat: {state.heat}\n"
        f"- interest: {state.interest}\n"
        f"- encounter_count: {state.encounter_count}\n\n"
        f"## Pre-allocated Encounter Slot Groups\n\n{_json(groups)}\n\n"
        f"## Planned Replies\n\n"
        f"{_json([r.model_dump(mode='json') for r in (state.replies or [])])}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_encounters(state: PipelineState, output: EncountersOutput) -> None:
    state.encounters = list(output.encounters)


# -- Step 7 - Serialize ------------------------------------------------------


async def _pure_serialize(state: PipelineState, ctx: StepContext) -> None:
    """Assemble the final Thread, validate, write to paperstore."""
    assert ctx.backend is not None
    assert state.subreddit is not None
    assert state.paper_type is not None
    assert state.heat is not None
    assert state.interest is not None
    assert state.research_summary is not None

    replies = list(state.replies or [])
    encounters = list(state.encounters or [])

    _validate_blueprint(state, replies, encounters)

    thread = Thread(
        document=state.paper_id or "",
        paper=state.paper_number or "",
        revision=state.paper_revision,
        title=state.paper_title,
        authors=", ".join(state.paper_authors),
        audience=state.paper_audience,
        date=state.paper_date,
        subreddit=state.subreddit,
        prior_revision=state.prior_revision,
        revision_case=state.revision_case,  # type: ignore[arg-type]
        paper_type=state.paper_type,
        technical_anchors=list(state.technical_anchors or []),
        tangent_magnets=list(state.tangent_magnets or []),
        hot_takes=list(state.hot_takes or []),
        misconception_traps=list(state.misconception_traps or []),
        design_tensions=list(state.design_tensions or []),
        research_summary=state.research_summary,
        heat=state.heat,
        interest=state.interest,
        target_comment_count=state.target_comment_count or 0,
        encounter_count=state.encounter_count or 0,
        signal_count=state.signal_count or 0,
        noise_count=state.noise_count or 0,
        submission_title=state.submission_title or "",
        submission_body=state.submission_body or "",
        submission_link=state.submission_link or "",
        submission_flair=state.submission_flair or "",
        replies=replies,
        encounters=encounters,
    )

    payload = thread.model_dump(mode="json")
    out_path = await asyncio.to_thread(
        ctx.backend.write_agora_json, ctx.pid, payload,
    )
    logger.info("Step 7: wrote agora thread blueprint -> %s", out_path)
    state.thread = thread


_INTEREST_LENS_FLOOR: dict[str, int] = {
    "niche": 0,
    "relevant": 0,
    "magnetic": 3,
    "gravitational": 4,
}


def _validate_blueprint(state: PipelineState, replies: list, encounters: list) -> None:
    """Sanity-check the Thread structure before serialisation."""
    anchor_ids = {a.id for a in (state.technical_anchors or [])}
    addressed: set[str] = set()
    lens_used: set[int] = set()
    for r in replies:
        if r.depth < 0 or r.depth > 6:
            raise ValidationStepError(
                7, _STEP_7_SERIALIZE,
                ValueError(f"Reply '{r.slot_id}' has out-of-range depth {r.depth}."),
            )
        if r.role in ("signal", "encounter", "teaser") and r.anchor_id:
            addressed.add(r.anchor_id)
        if r.role in ("signal", "encounter", "teaser") and r.domain_lens is not None:
            lens_used.add(r.domain_lens)

    missing_anchors = anchor_ids - addressed
    if missing_anchors and anchor_ids:
        logger.warning(
            "Step 7: %d technical anchors have no addressing reply: %s",
            len(missing_anchors), sorted(missing_anchors),
        )

    encounter_slot_ids = {sid for e in encounters for sid in e.slot_ids}
    skel_encounter_ids = {
        r.slot_id for r in replies if r.role == "encounter"
    }
    orphan_encounter = skel_encounter_ids - encounter_slot_ids
    if orphan_encounter and encounters:
        logger.warning(
            "Step 7: %d encounter slots are not linked to any EncounterPlan: %s",
            len(orphan_encounter), sorted(orphan_encounter),
        )

    floor = _INTEREST_LENS_FLOOR.get(state.interest or "niche", 0)
    if floor and len(lens_used) < floor:
        logger.warning(
            "Step 7: interest=%s requires >=%d distinct domain lenses; "
            "skeleton used %d.",
            state.interest, floor, len(lens_used),
        )

    if state.revision_case == "C" and not state.prior_revision:
        raise ValidationStepError(
            7, _STEP_7_SERIALIZE,
            ValueError("revision_case='C' but prior_revision is unset."),
        )


# -- JSON helper -------------------------------------------------------------


def _json(obj: Any) -> str:
    """Compact JSON dump suitable for injecting into LLM user messages."""
    return json.dumps(obj or [], ensure_ascii=False, default=str)


# -- Hook registry -----------------------------------------------------------

def _build_hooks(
    synthesis_agent: AgentBackend,
    research_agent: AgentBackend,
) -> dict[str, StepHooks]:
    """Build the step hooks dict with agents assigned."""
    return {
        _STEP_0_LOAD: StepHooks(custom=_pure_load),
        _STEP_1_SMELL_TEST: StepHooks(
            agent=synthesis_agent,
            output_type=SmellTestOutput,
            prepare=_prepare_smell_test,
            extract=_extract_smell_test,
        ),
        _STEP_2_RESEARCH: StepHooks(
            agent=research_agent,
            custom=_pure_research,
        ),
        _STEP_3_CALIBRATE: StepHooks(
            agent=synthesis_agent,
            output_type=CalibrationOutput,
            prepare=_prepare_calibrate,
            extract=_extract_calibrate,
        ),
        _STEP_4_SUBMISSION: StepHooks(
            agent=synthesis_agent,
            output_type=SubmissionOutput,
            prepare=_prepare_submission,
            extract=_extract_submission,
        ),
        _STEP_5_SKELETON: StepHooks(
            agent=synthesis_agent,
            output_type=SkeletonOutput,
            prepare=_prepare_skeleton,
            extract=_extract_skeleton,
        ),
        _STEP_6_ENCOUNTERS: StepHooks(
            agent=synthesis_agent,
            output_type=EncountersOutput,
            prepare=_prepare_encounters,
            extract=_extract_encounters,
            guard=_guard_encounter_count_positive,
        ),
        _STEP_7_SERIALIZE: StepHooks(custom=_pure_serialize),
    }


# -- Public API --------------------------------------------------------------


async def agora_paper(
    pid: str,
    backend: StorageBackend,
    *,
    service_overrides: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> Thread | str:
    """Plan a Reddit thread for a dissected WG21 paper.

    Loads services from SERVICES.toml, builds agents, runs the 8-step
    analysis pipeline, writes ``{pid}.agora.json`` via
    ``backend.write_agora_json``, and returns the planned
    :class:`Thread`. Generation-phase fields stay ``None``. Pass
    ``service_overrides`` to bind slots to specific services.

    Raises :class:`PromptFileError` if ``agora.md`` has structural
    problems. Raises :class:`PaperNotFoundError`,
    :class:`PaperNotConvertedError`, or :class:`PaperNotDissectedError`
    if the prerequisite paperstore artifacts are missing.
    """
    registry = load_services()
    slots = resolve_slots(registry, service_overrides)

    default_svc, default_backend = slots["default"]
    tool_svc, tool_backend = slots.get("tool", slots["default"])

    synthesis_agent = AgentBackend(
        default_backend,
        thinking_budget=4096,
        slot_name="default",
        service_name=default_svc,
    )
    research_agent = AgentBackend(
        tool_backend,
        slot_name="tool",
        service_name=tool_svc,
    )

    agents = {
        "default": synthesis_agent,
        "tool": research_agent,
    }

    secs = dict(load_sections("agora", "agora.md"))

    if "System Prompt" not in secs:
        raise PromptFileError(
            "'System Prompt' section not found in agora.md. "
            f"Available sections: {sorted(secs)}"
        )

    hooks = _build_hooks(synthesis_agent, research_agent)
    pipeline = build_pipeline(secs, hooks)

    try:
        meta = await asyncio.to_thread(backend.get_meta, pid)
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
    if not meta.dissect_path:
        raise PaperNotDissectedError(
            f"Paper '{pid}' has no dissect output. "
            f"Run 'paperflow dissect {pid}' first."
        )

    state = PipelineState()

    if stop_after is None:
        try:
            await asyncio.to_thread(backend.clear_agora, pid)
        except Exception as exc:
            logger.debug("clear_agora(%s) raised %s; continuing", pid, exc)

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

    if state.thread is None:
        raise StepError(
            7, _STEP_7_SERIALIZE,
            RuntimeError("Pipeline completed without producing a Thread."),
        )
    return state.thread


async def agora_since(
    month: str,
    backend: StorageBackend,
    *,
    service_overrides: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> list[dict[str, str | None]]:
    """Plan threads for all papers with ``mailing_date >= month``.

    Iterates sequentially, calling :func:`agora_paper` for each. The
    JSON is written inside :func:`agora_paper` via
    ``backend.write_agora_json``; this function only collects status.

    Per-paper errors are caught and logged; the loop continues.

    Returns a list of result dicts:
    ``{"paper_id": str, "status": "ok"|"error", "error": str|None}``.
    """
    papers = backend.list_papers_since(month)
    results: list[dict[str, str | None]] = []

    for paper in papers:
        pid = paper.paper_id
        try:
            await agora_paper(
                pid, backend,
                service_overrides=service_overrides,
                on_progress=on_progress,
                stop_after=stop_after,
                debug=debug,
                trace=trace,
            )
            logger.info("Planned thread for %s", pid)
            results.append({"paper_id": pid, "status": "ok", "error": None})
        except Exception as exc:  # batch worker firewall: log + continue per-paper
            logger.error("Failed to plan thread for %s: %s", pid, exc)
            results.append({"paper_id": pid, "status": "error", "error": str(exc)})

    return results
