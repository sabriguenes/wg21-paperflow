#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async examination pipeline for WG21 papers (Advocatus Diaboli).

All LLM-facing text comes from ``advocatus.md`` at runtime. This module
contains structural orchestration: hook definitions, the generic
runner, and the dispatch loop. ``advocatus.md`` is the upstream
authority for pipeline structure; this module conforms to it.

One-shot, fully batch. No human-in-the-loop. Concurrency for parallel
sub-agents is capped at ``_TASK_CONCURRENCY`` (5) by a single
module-level semaphore.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.resources
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from paperstore import (
    SourceLoc,
    StorageBackend,
    loc_from_row,
)
from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.progress import ProgressCallback, ProgressEvent
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from advocatus.errors import (
    HookMismatchError,
    PaperNotConvertedError,
    PaperNotDissectedError,
    PaperNotFoundError,
    PromptFileError,
    StepError,
    TransientStepError,
    ValidationStepError,
)
from advocatus.models import (
    Articulus,
    ArticulusExam,
    CandidateCharge,
    ChargesOutput,
    DefensorChallenge,
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
from advocatus.parse import sections
from advocatus.prompt import StepHooks, StepSpec, build_pipeline
from advocatus.render import render_debug_md, render_relatio, render_trace

if TYPE_CHECKING:
    from web_tools import WebResearcher

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# -- Concurrency cap ---------------------------------------------------------

_TASK_CONCURRENCY = 5
_task_semaphore = asyncio.Semaphore(_TASK_CONCURRENCY)


# -- Model slots and limits --------------------------------------------------

_DEFAULT_MODEL_SLOTS = {
    "fast": "anthropic:claude-haiku-4-5-20251001",
    "default": "anthropic:claude-opus-4-6",
}

_MODEL_SETTINGS_BY_SLOT = {
    "fast": ModelSettings(max_tokens=64000),
    "default": ModelSettings(max_tokens=80000),
}
_DEFAULT_MODEL_SETTINGS = ModelSettings(max_tokens=80000)

_SECTION_SYSTEM_PROMPT = "System Prompt"
_REQUEST_LIMIT = 200
_REQUEST_LIMIT_PER_TASK = 36
_RETRIES_SINGLE = 3
_RETRIES_EMPTY_OUTPUT = 3
_DEBUG_SEPARATOR = "\n\n---\n\n"

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


# -- Sub-agent isolation -----------------------------------------------------


async def run_task(
    system_prompt: str,
    user_message: str,
    output_type: type[T],
    *,
    label: str = "run_task",
    debug_log: list[str] | None = None,
    tools: dict[str, Callable] | None = None,
    model: str | None = None,
    request_limit: int = _REQUEST_LIMIT_PER_TASK,
) -> T:
    """Run an isolated sub-agent and return structured output.

    Mirrors dissect's ``run_task``: focused mission, tight budget,
    one-way data flow. Raw content stays inside the task. Concurrency
    is capped at ``_TASK_CONCURRENCY`` (5) to avoid hitting API rate
    limits when many tasks run in parallel.

    When ``debug_log`` is provided, the agent run is rendered to a
    markdown debug entry and appended to the list under the given
    ``label``. This is a divergence from dissect's ``run_task``: most
    advocatus LLM work happens through sub-agents (Steps 2, 3, 5, 7);
    without per-task debug threading, the debug transcript would be
    almost empty. Concurrent appends are GIL-atomic; final ordering
    is non-deterministic but no entries are lost.
    """
    async with _task_semaphore:
        agent: Agent[None, T] = Agent(
            model or _DEFAULT_MODEL_SLOTS["default"],
            output_type=output_type,
            system_prompt=system_prompt,
        )
        if tools:
            for name, fn in tools.items():
                agent.tool_plain(fn)
        result = await agent.run(
            user_message,
            usage_limits=UsageLimits(request_limit=request_limit),
        )
        if debug_log is not None:
            debug_log.append(render_debug_md(result, label))
        return result.output


# -- Step context ------------------------------------------------------------


@dataclass
class StepContext:
    """Shared resources available to every step."""

    sections: dict[str, str]
    model_slots: dict[str, str]
    researcher: "WebResearcher | None" = None
    backend: StorageBackend | None = None
    debug: bool = False
    pid: str = ""
    debug_log: list[str] | None = None
    tool_registry: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _current_spec: StepSpec | None = None

    def __post_init__(self) -> None:
        if self.debug and self.debug_log is None:
            self.debug_log = []


@functools.cache
def load_sections() -> dict[str, str]:
    """Load and parse advocatus.md once per process."""
    try:
        resource = importlib.resources.files("advocatus").joinpath("advocatus.md")
        return sections(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        raise PromptFileError(
            f"Failed to read advocatus.md: {exc}"
        ) from exc


# -- Generic LLM runner ------------------------------------------------------


_TRANSIENT_EXCEPTIONS = (ModelHTTPError,)
_VALIDATION_EXCEPTIONS = (UnexpectedModelBehavior, UsageLimitExceeded)


def _resolve_model(spec: StepSpec, ctx: StepContext) -> str:
    slot = spec.meta.model_slot
    return ctx.model_slots.get(slot) or _DEFAULT_MODEL_SLOTS.get(slot, slot)


async def _run_agent(
    ctx: StepContext,
    spec: StepSpec,
    user_msg: str,
    *,
    request_limit: int = _REQUEST_LIMIT,
    retries: int = _RETRIES_SINGLE,
) -> Any:
    """Build an Agent, run it, classify exceptions."""
    system = ctx.sections.get(_SECTION_SYSTEM_PROMPT, "")
    resolved = _resolve_model(spec, ctx)
    slot = spec.meta.model_slot

    agent: Agent[None, Any] = Agent(
        model=resolved,
        output_type=spec.hooks.output_type or str,
        system_prompt=system,
        retries=retries,
        model_settings=_MODEL_SETTINGS_BY_SLOT.get(slot, _DEFAULT_MODEL_SETTINGS),
    )

    for tool_name in spec.meta.tools:
        if tool_name not in ctx.tool_registry:
            raise HookMismatchError(
                f"Step '{spec.meta.name}' declares tool '{tool_name}' "
                f"but no callable is registered. "
                f"Available tools: {sorted(ctx.tool_registry)}"
            )
        fn = ctx.tool_registry[tool_name]
        if ctx.debug:
            fn = _wrap_tool_debug(fn, tool_name)
        agent.tool_plain(fn)

    try:
        result = await agent.run(
            user_msg, usage_limits=UsageLimits(request_limit=request_limit),
        )
    except (*_TRANSIENT_EXCEPTIONS, *_VALIDATION_EXCEPTIONS, StepError, PromptFileError):
        raise
    except Exception as exc:
        _classify_and_raise(exc, spec)

    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(render_debug_md(result, spec.meta.name))

    return result


def _wrap_tool_debug(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    """Wrap a tool function to log calls when debugging."""
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        args_str = ", ".join(
            [repr(a) for a in args] +
            [f"{k}={repr(v)}" for k, v in kwargs.items()]
        )
        logger.debug("[tool] %s(%s)", name, args_str)
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    return wrapper


async def _run_agent_with_retry(
    ctx: StepContext,
    spec: StepSpec,
    user_msg: str,
    *,
    request_limit: int = _REQUEST_LIMIT,
    retries: int = _RETRIES_SINGLE,
) -> Any:
    """Run agent with retry-on-empty logic."""
    for attempt in range(_RETRIES_EMPTY_OUTPUT):
        result = await _run_agent(
            ctx, spec, user_msg,
            request_limit=request_limit, retries=retries,
        )
        if spec.hooks.retry_empty is None or not spec.hooks.retry_empty(result.output):
            return result
        logger.warning(
            "%s: empty output on attempt %d, retrying",
            spec.meta.name, attempt + 1,
        )
    return result


def _classify_and_raise(exc: Exception, spec: StepSpec) -> None:
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        raise TransientStepError(spec.meta.number, spec.meta.name, exc) from exc
    if isinstance(exc, _VALIDATION_EXCEPTIONS):
        raise ValidationStepError(spec.meta.number, spec.meta.name, exc) from exc
    raise StepError(spec.meta.number, spec.meta.name, exc) from exc


# -- Guards ------------------------------------------------------------------


def _guard_not_sine_causa(state: PipelineState) -> bool:
    """Skip steps once the Sine causa early exit fires."""
    return state.seal != "sine_causa"


# -- Step 0 - Load -----------------------------------------------------------


async def _pure_load(state: PipelineState, ctx: StepContext) -> None:
    """Load paper + dissect data from paperstore. Sine causa early exit."""
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

    # Fail-fast: the advocatus pipeline consumes dissect data. If the paper
    # has never been dissected, emitting Sine causa would silently mask the
    # real "you forgot to run paperflow dissect first" condition.
    if not meta.dissect_path:
        raise PaperNotDissectedError(
            f"Paper '{pid}' has no dissect output. "
            f"Run 'paperflow dissect {pid}' first."
        )

    state.paper_id = pid
    state.paper_source = paper_md
    state.paper_title = meta.title or ""
    state.paper_audience = meta.target_group or ""
    state.paper_authors = list(meta.authors or [])

    claim_rows = await asyncio.to_thread(ctx.backend.get_claims, pid)
    evidence_rows = await asyncio.to_thread(ctx.backend.get_evidence, pid)
    marker_rows = await asyncio.to_thread(ctx.backend.get_markers, pid)
    citation_audit_rows = await asyncio.to_thread(ctx.backend.get_citation_audit, pid)
    external_rows = await asyncio.to_thread(ctx.backend.get_external_citations, pid)
    caput = await asyncio.to_thread(ctx.backend.get_caput_causae, pid)

    # Articuli seed: alive claims (skip tombstones)
    articuli_seed: list[Articulus] = []
    for row in claim_rows:
        if row.merged_into_line is not None:
            continue
        articuli_seed.append(Articulus(
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
        if row.merged_into_line is not None:
            continue
        dissect_evidence.append(DossierEntry(
            label="operator_provided",
            text=row.text,
            source_url="",
            relevance=f"({row.section}) supports: {row.supports}",
        ))
    state.dissect_evidence = dissect_evidence

    # Markers (kept as Articulus-shaped objects for convenience; the field is
    # only used by the Defensor's Confessio challenge).
    dissect_markers: list[Articulus] = []
    for row in marker_rows:
        dissect_markers.append(Articulus(
            loc=loc_from_row(row),
            text=row.text,
            section=row.section,
            kind="normative",
            question=f"[{row.marker_type}] target: {row.target}",
        ))
    state.dissect_markers = dissect_markers

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
    caput = state.dissect_caput_causae or "(no caput causae from dissect)"
    paper = state.paper_source or ""
    return (
        f"## Caput Causae (from dissect)\n\n{caput}\n\n"
        f"## Seed Articuli (from dissect, with locs)\n\n{seed_json}\n\n"
        f"## Paper Source\n\n{paper}\n\n"
        f"## Instructions\n\n{body}"
    )


def _extract_read_scripta(state: PipelineState, output: ScriptaOutput) -> None:
    state.central_thesis_recap = output.central_thesis_recap
    state.articuli = list(output.articuli)
    state.boundaries = list(output.boundaries)


# -- Step 2 - Survey Public Record -------------------------------------------


async def _pure_public_record(state: PipelineState, ctx: StepContext) -> None:
    """Spawn parallel sub-agents for public-record search."""
    assert ctx._current_spec is not None
    body = ctx.sections.get(_STEP_2_PUBLIC_RECORD, "")
    web_search = ctx.tool_registry.get("web_search")
    web_fetch = ctx.tool_registry.get("web_fetch")
    if web_search is None or web_fetch is None:
        logger.warning("Step 2 skipped: web_search/web_fetch not available")
        state.dossier = list(state.dissect_external_evidence or [])
        return

    model = _resolve_model(ctx._current_spec, ctx)
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
            system_prompt=system,
            user_message=user_msg,
            output_type=PublicRecordOutput,
            label=f"Step 2 - Survey Public Record ({domain})",
            debug_log=ctx.debug_log if ctx.debug else None,
            tools={"web_search": web_search, "web_fetch": web_fetch},
            model=model,
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
            # Broad catch: a single sub-agent failure should not kill the run.
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


async def _pure_stakeholders(state: PipelineState, ctx: StepContext) -> None:
    assert ctx._current_spec is not None
    body = ctx.sections.get(_STEP_3_STAKEHOLDERS, "")
    web_search = ctx.tool_registry.get("web_search")
    web_fetch = ctx.tool_registry.get("web_fetch")
    if web_search is None or web_fetch is None:
        state.stakeholders = []
        return

    model = _resolve_model(ctx._current_spec, ctx)
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
            system_prompt=system,
            user_message=user_msg,
            output_type=StakeholdersOutput,
            label=f"Step 3 - Map Stakeholders ({target})",
            debug_log=ctx.debug_log if ctx.debug else None,
            tools={"web_search": web_search, "web_fetch": web_fetch},
            model=model,
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


async def _pure_verify_citations(state: PipelineState, ctx: StepContext) -> None:
    """Pure conversion of dissect's citation_audit into tabula_fontium."""
    state.tabula_fontium = list(state.dissect_citation_audit or [])


# -- Step 5 - Examine Articuli -----------------------------------------------


async def _pure_examine(state: PipelineState, ctx: StepContext) -> None:
    """One LLM call per articulus, in parallel via run_task + semaphore."""
    assert ctx._current_spec is not None
    articuli = state.articuli or []
    if not articuli:
        state.exams = []
        return

    body = ctx.sections.get(_STEP_5_EXAMINE, "")
    model = _resolve_model(ctx._current_spec, ctx)
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
            system_prompt=system,
            user_message=user_msg,
            output_type=ExamenOutput,
            label=f"Step 5 - Examine Articuli (line {articulus.loc.line})",
            debug_log=ctx.debug_log if ctx.debug else None,
            model=model,
        )

    results = await asyncio.gather(
        *[_one(a) for a in articuli],
        return_exceptions=True,
    )

    exams: list[ArticulusExam] = []
    for a, r in zip(articuli, results):
        if isinstance(r, Exception):
            logger.warning("Examen failed for articulus at line %d: %s", a.loc.line, r)
            continue
        # Force the loc to the articulus's loc (defensive: the LLM might echo
        # something different).
        exam = r.exam.model_copy(update={"articulus_loc": a.loc})
        exams.append(exam)
    state.exams = exams


# -- Step 6 - File Charges ---------------------------------------------------


def _prepare_file_charges(state: PipelineState, ctx: StepContext) -> str:
    body = ctx.sections.get(_STEP_6_FILE_CHARGES, "")
    articuli = state.articuli or []
    exams = state.exams or []
    failed_locs = {
        e.articulus_loc for e in exams
        if not (e.veritas.passed and e.ratio.passed and e.auctoritas.passed)
    }
    failed = [a for a in articuli if a.loc in failed_locs]
    failed_json = json.dumps(
        [a.model_dump(mode="json") for a in failed],
        ensure_ascii=False, default=str,
    )
    exams_json = json.dumps(
        [e.model_dump(mode="json") for e in exams if e.articulus_loc in failed_locs],
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
    state.candidate_charges = list(output.candidate_charges)


# -- Step 7 - Defensor Cross-Examination -------------------------------------


async def _pure_defensor(state: PipelineState, ctx: StepContext) -> None:
    """One sub-agent per candidate charge. Isolated context."""
    assert ctx._current_spec is not None
    charges = state.candidate_charges or []
    if not charges:
        state.defensor_results = []
        state.surviving_charges = []
        state.probationes = []
        state.notae_minores = []
        return

    body = ctx.sections.get(_STEP_7_DEFENSOR, "")
    model = _resolve_model(ctx._current_spec, ctx)
    boundaries_json = json.dumps(
        [b.model_dump(mode="json") for b in (state.boundaries or [])],
        ensure_ascii=False, default=str,
    )
    markers_json = json.dumps(
        [m.model_dump(mode="json") for m in (state.dissect_markers or [])],
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
        # Provide only the relevant dossier slice: entries whose text
        # mentions the contradicting evidence or the quoted text.
        dossier_slice = _dossier_slice_for_charge(state, charge)
        slice_json = json.dumps(
            [d.model_dump(mode="json") for d in dossier_slice],
            ensure_ascii=False, default=str,
        )
        user_msg = (
            f"## Candidate Charge\n\n{charge_json}\n\n"
            f"## Relevant Dossier Slice\n\n{slice_json}\n\n"
            f"## Boundaries\n\n{boundaries_json}\n\n"
            f"## Markers (concessions, scope deflections)\n\n{markers_json}\n\n"
            f"## Stakeholders\n\n{stakeholders_json}\n\n"
            f"## Instructions\n\n{body}"
        )
        return await run_task(
            system_prompt=system,
            user_message=user_msg,
            output_type=DefensorChargeOutput,
            label=f"Step 7 - Defensor (charge at line {charge.articulus_loc.line})",
            debug_log=ctx.debug_log if ctx.debug else None,
            model=model,
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
            logger.warning("Defensor sub-agent failed for charge at line %d: %s",
                           charge.articulus_loc.line, r)
            continue
        # Force charge_loc to the articulus loc.
        result = r.model_copy(update={"charge_loc": charge.articulus_loc})
        defensor_results.append(result)

        if result.final == "survived":
            surviving.append(SurvivingCharge(
                articulus_loc=charge.articulus_loc,
                charge=charge,
                defensor_chain=list(result.challenges),
            ))
        elif result.final == "killed":
            killing_challenge = result.challenges[-1].challenge if result.challenges else "humanitas"
            killing_reasoning = result.challenges[-1].reasoning if result.challenges else ""
            probationes.append(Probatio(
                articulus_loc=charge.articulus_loc,
                killed_charge=charge,
                killing_challenge=killing_challenge,
                explanation=killing_reasoning,
            ))
        elif result.final == "relegated":
            notae.append(NotaMinor(
                loc=charge.articulus_loc,
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


async def _pure_render(state: PipelineState, ctx: StepContext) -> None:
    state.relatio = render_relatio(state)


# -- Hook registry -----------------------------------------------------------

# Step names must match exactly the ## headers in advocatus.md.
_HOOKS: dict[str, StepHooks] = {
    _STEP_0_LOAD: StepHooks(pure=_pure_load),
    _STEP_1_READ_SCRIPTA: StepHooks(
        output_type=ScriptaOutput,
        prepare=_prepare_read_scripta,
        extract=_extract_read_scripta,
        guard=_guard_not_sine_causa,
    ),
    _STEP_2_PUBLIC_RECORD: StepHooks(
        pure=_pure_public_record,
        guard=_guard_not_sine_causa,
    ),
    _STEP_3_STAKEHOLDERS: StepHooks(
        pure=_pure_stakeholders,
        guard=_guard_not_sine_causa,
    ),
    _STEP_4_VERIFY_CITATIONS: StepHooks(
        pure=_pure_verify_citations,
        guard=_guard_not_sine_causa,
    ),
    _STEP_5_EXAMINE: StepHooks(
        pure=_pure_examine,
        guard=_guard_not_sine_causa,
    ),
    _STEP_6_FILE_CHARGES: StepHooks(
        output_type=ChargesOutput,
        prepare=_prepare_file_charges,
        extract=_extract_file_charges,
        guard=_guard_not_sine_causa,
    ),
    _STEP_7_DEFENSOR: StepHooks(
        pure=_pure_defensor,
        guard=_guard_not_sine_causa,
    ),
    _STEP_8_MOTIVATIO: StepHooks(
        output_type=MotivatioOutput,
        prepare=_prepare_motivatio,
        extract=_extract_motivatio,
        guard=_guard_not_sine_causa,
    ),
    _STEP_9_WEIGH: StepHooks(
        output_type=WeighCauseOutput,
        prepare=_prepare_weigh,
        extract=_extract_weigh,
        guard=_guard_not_sine_causa,
    ),
    _STEP_10_RENDER: StepHooks(pure=_pure_render),
}


# -- Dispatch ----------------------------------------------------------------


async def _dispatch(
    pipeline: list[StepSpec],
    state: PipelineState,
    ctx: StepContext,
    *,
    stop_after: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Execute the pipeline step by step.

    When ``stop_after`` is set, processing stops *after* completing step
    ``stop_after`` (inclusive). Useful for diagnostic trace runs.
    """
    total = len(pipeline)
    for i, spec in enumerate(pipeline):
        if stop_after is not None and i > stop_after:
            break

        if on_progress is not None:
            on_progress(ProgressEvent(
                step=i, total=total, name=spec.meta.name, pct=i / total,
            ))

        if spec.hooks.guard and not spec.hooks.guard(state):
            logger.info("Step %d: %s (skipped by guard)", i, spec.meta.name)
            continue

        logger.info("Step %d: %s", i, spec.meta.name)
        ctx._current_spec = spec

        try:
            if spec.hooks.pure:
                await spec.hooks.pure(state, ctx)
            else:
                assert spec.hooks.prepare is not None
                user_msg = spec.hooks.prepare(state, ctx)
                result = await _run_agent_with_retry(
                    ctx, spec, user_msg,
                    request_limit=spec.hooks.request_limit or _REQUEST_LIMIT,
                )
                if spec.hooks.extract:
                    spec.hooks.extract(state, result.output)
        except (
            StepError,
            PromptFileError,
            PaperNotFoundError,
            PaperNotConvertedError,
            PaperNotDissectedError,
        ):
            raise
        except Exception as exc:
            logger.error(
                "Step %d (%s) failed: %s", i, spec.meta.name, exc, exc_info=True,
            )
            raise StepError(i, spec.meta.name, exc) from exc

    if on_progress is not None:
        on_progress(ProgressEvent(
            step=total, total=total, name="done", pct=1.0,
        ))


# -- Public API --------------------------------------------------------------


async def advocatus_paper(
    pid: str,
    backend: StorageBackend,
    *,
    model_slots: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> str:
    """Examine a WG21 paper and return the Relatio markdown.

    Loads the paper and dissect data from paperstore, runs the 11-step
    examination pipeline, and returns the Relatio. One-shot; no human
    input.

    With ``debug=True``, every LLM call (top-level + every sub-agent)
    is rendered to a debug transcript at
    ``backend.get_debug_md_path(pid, "advocatus")``. The file is
    cleared before dispatch so reruns do not append to stale logs.

    With ``trace=True``, a full per-step state dump is written to
    ``backend.get_trace_md_path(pid, "advocatus")`` after dispatch
    completes; ``state.relatio`` is still returned.

    With ``stop_after=N``, dispatch halts after pipeline step ``N`` and
    the partial trace string is **returned** in place of the Relatio
    (the CLI is responsible for writing it). ``stop_after`` and bare
    ``trace`` are mutually exclusive write paths.

    Raises :class:`PromptFileError` if ``advocatus.md`` has structural
    problems. Raises :class:`PaperNotFoundError`,
    :class:`PaperNotConvertedError`, or :class:`PaperNotDissectedError`
    if the prerequisite paperstore artifacts are missing.
    """
    from web_tools import WebResearcher

    slots = {**_DEFAULT_MODEL_SLOTS, **(model_slots or {})}
    secs = load_sections()

    if _SECTION_SYSTEM_PROMPT not in secs:
        raise PromptFileError(
            "'System Prompt' section not found in advocatus.md. "
            f"Available sections: {sorted(secs)}"
        )

    pipeline = build_pipeline(secs, _HOOKS)

    state = PipelineState()

    async with WebResearcher() as researcher:
        tool_reg: dict[str, Callable[..., Any]] = {
            "web_search": researcher.web_search,
            "web_fetch": researcher.web_fetch,
        }

        ctx = StepContext(
            sections=secs,
            model_slots=slots,
            researcher=researcher,
            backend=backend,
            debug=debug,
            pid=pid,
            tool_registry=tool_reg,
        )

        debug_path = backend.get_debug_md_path(pid, "advocatus")
        if debug:
            debug_path.unlink(missing_ok=True)

        try:
            await _dispatch(
                pipeline, state, ctx,
                stop_after=stop_after,
                on_progress=on_progress,
            )
        finally:
            if debug and ctx.debug_log:
                debug_path.write_text(
                    _DEBUG_SEPARATOR.join(ctx.debug_log), encoding="utf-8",
                )

    if stop_after is not None:
        return render_trace(state, stop_after)

    if trace:
        last_step = len(pipeline) - 1
        trace_path = backend.get_trace_md_path(pid, "advocatus")
        trace_path.write_text(
            render_trace(state, last_step), encoding="utf-8",
        )

    return state.relatio or ""


async def advocatus_since(
    month: str,
    backend: StorageBackend,
    *,
    model_slots: dict[str, str] | None = None,
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
                model_slots=model_slots,
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
