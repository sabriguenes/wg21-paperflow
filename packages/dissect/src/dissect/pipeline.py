#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async extractor pipeline for WG21 papers.

All LLM-facing text comes from ``dissect.md`` at runtime. This module
contains only structural orchestration: hook definitions, the generic
runner, and the dispatch loop. ``dissect.md`` is the upstream
authority for pipeline structure; this module conforms to it.
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

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from paperstore.backend import StorageBackend
from paperstore.progress import ProgressCallback, ProgressEvent
from paperstore.errors import MissingMetaError, MissingPaperMdError

from dissect.errors import (
    HookMismatchError,
    PaperNotConvertedError,
    PaperNotFoundError,
    PromptFileError,
    StepError,
    TransientStepError,
    ValidationStepError,
)
from dissect.harness import (
    _chunk_paper,
    _dedup_tier0,
    _dedup_tier1,
    _extract_citations,
    _number_lines,
    _promote_claims,
    _promote_evidence,
    _promote_rhetoric,
)
from dissect.models import (
    CaputCausaeOutput,
    Chunk,
    CitationRef,
    CitationTaskOutput,
    DedupGroupingOutput,
    ExtractAllOutput,
    ExtractFactualOutput,
    LoadBearingOutput,
    PatternDetectionOutput,
    PipelineState,
    RawClaim,
    ResolveOutput,
    VerifyOutput,
    WebSearchOutput,
)
from dissect.parse import sections
from dissect.prompt import StepHooks, StepSpec, build_pipeline
from dissect.render import render_debug_md, render_report, render_trace

if TYPE_CHECKING:
    from web_tools import WebResearcher

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_TASK_CONCURRENCY = 5
_task_semaphore = asyncio.Semaphore(_TASK_CONCURRENCY)


async def run_task(
    system_prompt: str,
    user_message: str,
    output_type: type[T],
    tools: dict[str, Callable] | None = None,
    model: str | None = None,
    request_limit: int = 10,
) -> T:
    """Run an isolated agent with its own context and return structured output.

    Mirrors Cursor's Task tool pattern: focused mission, tight budget,
    one-way data flow. Raw content stays inside the task.

    Concurrency is capped at ``_TASK_CONCURRENCY`` (5) to avoid hitting
    API rate limits when many tasks are dispatched in parallel.
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
        return result.output


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
_REQUEST_LIMIT = 500
_REQUEST_LIMIT_DEDUP = 50
_REQUEST_LIMIT_PER_CLAIM = 36
_REQUEST_LIMIT_PER_CITATION = 36
_RETRIES_CHUNK = 5
_RETRIES_SINGLE = 3
_CLASSIFICATION_CRITICAL_GAP = "critical_gap"
_RETRIES_EMPTY_OUTPUT = 3
_DEBUG_SEPARATOR = "\n\n---\n\n"

_STEP_0_READ = "Step 0 - Read"
_STEP_1_EXTRACT = "Step 1 - Extract Normative"
_STEP_2_DEDUP_CLAIMS = "Step 2 - Dedup Claims"
_STEP_3_EXTRACT_FACTUAL = "Step 3 - Extract Factual"
_STEP_4_DEDUP_FACTUAL = "Step 4 - Dedup Factual Claims"
_STEP_5_DEDUP_EVIDENCE = "Step 5 - Dedup Evidence"
_STEP_6_VERIFY = "Step 6 - Verify"
_STEP_7_LOAD_BEARING = "Step 7 - Load-Bearing"
_STEP_8_VERIFY_CITATIONS = "Step 8 - Verify Citations"
_STEP_9_WEB_SEARCH = "Step 9 - Web Search"
_STEP_10_RESOLVE = "Step 10 - Resolve External"
_STEP_11_CAPUT_CAUSAE = "Step 11 - Caput Causae"
_STEP_12_DETECT_PATTERNS = "Step 12 - Detect Patterns"
_STEP_13_REPORT = "Step 13 - Report"


@dataclass
class StepContext:
    """Shared resources available to every step."""

    sections: dict[str, str]
    model_slots: dict[str, str]
    researcher: WebResearcher | None = None
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
    """Load and parse dissect.md once per process."""
    try:
        resource = importlib.resources.files("dissect").joinpath("dissect.md")
        return sections(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        raise PromptFileError(
            f"Failed to read dissect.md: {exc}"
        ) from exc


# -- Generic runner -----------------------------------------------------------


_TRANSIENT_EXCEPTIONS = (ModelHTTPError,)
_VALIDATION_EXCEPTIONS = (UnexpectedModelBehavior, UsageLimitExceeded)


async def _run_agent(
    ctx: StepContext,
    spec: StepSpec,
    user_msg: str,
    *,
    request_limit: int = _REQUEST_LIMIT,
    retries: int = _RETRIES_SINGLE,
) -> Any:
    """Create an Agent, run it, handle debug logging and errors.

    Prompt-driven tool registration: reads ``spec.meta.tools``, looks
    up each name in ``ctx.tool_registry``, and registers on the Agent.
    """
    system = ctx.sections.get(_SECTION_SYSTEM_PROMPT, "")
    model_slot = spec.meta.model_slot
    resolved = ctx.model_slots.get(model_slot)
    if resolved is None:
        resolved = _DEFAULT_MODEL_SLOTS.get(model_slot, model_slot)

    agent: Agent[None, Any] = Agent(
        model=resolved,
        output_type=spec.hooks.output_type or str,
        system_prompt=system,
        retries=retries,
        model_settings=_MODEL_SETTINGS_BY_SLOT.get(model_slot, _DEFAULT_MODEL_SETTINGS),
    )

    for tool_name in spec.meta.tools:
        if tool_name not in ctx.tool_registry:
            raise HookMismatchError(
                f"Step '{spec.meta.name}' declares tool '{tool_name}' "
                f"but no callable is registered in the tool registry. "
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


async def _run_agent_with_retry(
    ctx: StepContext,
    spec: StepSpec,
    user_msg: str,
    *,
    request_limit: int = _REQUEST_LIMIT,
    retries: int = _RETRIES_SINGLE,
    chunk_label: str | None = None,
) -> Any:
    """Run agent with retry-on-empty logic."""
    label = f"{spec.meta.name} ({chunk_label})" if chunk_label else spec.meta.name
    for attempt in range(_RETRIES_EMPTY_OUTPUT):
        result = await _run_agent(
            ctx, spec, user_msg,
            request_limit=request_limit, retries=retries,
        )
        if spec.hooks.retry_empty is None or not spec.hooks.retry_empty(result.output):
            return result
        logger.warning(
            "%s: empty output on attempt %d, retrying",
            label, attempt + 1,
        )
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


def _classify_and_raise(exc: Exception, spec: StepSpec) -> None:
    """Wrap a pydantic-ai exception into the appropriate StepError subclass."""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        raise TransientStepError(spec.meta.number, spec.meta.name, exc) from exc
    if isinstance(exc, _VALIDATION_EXCEPTIONS):
        raise ValidationStepError(spec.meta.number, spec.meta.name, exc) from exc
    raise StepError(spec.meta.number, spec.meta.name, exc) from exc


# -- Prepare hooks ------------------------------------------------------------


def _prepare_extract_chunk(state: PipelineState, ctx: StepContext, chunk: Chunk) -> str:
    prompt_body = ctx.sections.get(_STEP_1_EXTRACT, "")
    return (
        f"## Chunk\n\n{_number_lines(chunk)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_extract_factual_chunk(state: PipelineState, ctx: StepContext, chunk: Chunk) -> str:
    prompt_body = ctx.sections.get(_STEP_3_EXTRACT_FACTUAL, "")
    normative_questions: list[str] = []
    if state.claims:
        normative_questions = [
            c.question for c in state.claims if c.merged_into is None
        ]
    questions_text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(normative_questions))
    return (
        f"## Normative Claim Questions\n\n{questions_text}\n\n"
        f"## Chunk\n\n{_number_lines(chunk)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_dedup_claims(state: PipelineState, ctx: StepContext) -> str:
    assert state.claims is not None
    survivors = [c for c in state.claims if c.merged_into is None]
    prompt_body = ctx.sections.get(_STEP_2_DEDUP_CLAIMS, "")
    survivor_questions = json.dumps(
        [{"idx": i, "question": s.question} for i, s in enumerate(survivors)],
        ensure_ascii=False,
    )
    return (
        f"## Survivors\n\n{survivor_questions}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_dedup_evidence(state: PipelineState, ctx: StepContext) -> str:
    assert state.evidence is not None
    survivors = [e for e in state.evidence if e.merged_into is None]
    prompt_body = ctx.sections.get(_STEP_5_DEDUP_EVIDENCE, "")
    survivor_supports = json.dumps(
        [{"idx": i, "supports": s.supports} for i, s in enumerate(survivors)],
        ensure_ascii=False,
    )
    return (
        f"## Survivors\n\n{survivor_supports}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_verify(state: PipelineState, ctx: StepContext) -> str:
    assert state.claims is not None and state.evidence is not None
    prompt_body = ctx.sections.get(_STEP_6_VERIFY, "")
    claims_json = json.dumps(
        [c.model_dump() for c in state.claims if c.merged_into is None],
        ensure_ascii=False,
    )
    evidence_json = json.dumps(
        [e.model_dump() for e in state.evidence if e.merged_into is None],
        ensure_ascii=False,
    )
    return (
        f"## Claims\n\n{claims_json}\n\n"
        f"## Evidence\n\n{evidence_json}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_load_bearing(state: PipelineState, ctx: StepContext) -> str:
    assert state.claims is not None and state.support_map is not None
    prompt_body = ctx.sections.get(_STEP_7_LOAD_BEARING, "")
    claims_json = json.dumps(
        [c.model_dump() for c in state.claims if c.merged_into is None],
        ensure_ascii=False,
    )
    support_json = json.dumps(
        [s.model_dump() for s in state.support_map],
        ensure_ascii=False,
    )
    contradictions_json = json.dumps(
        [ic.model_dump() for ic in (state.internal_contradictions or [])],
        ensure_ascii=False,
    )
    return (
        f"## Claims\n\n{claims_json}\n\n"
        f"## Support Map\n\n{support_json}\n\n"
        f"## Internal Contradictions\n\n{contradictions_json}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_resolve(state: PipelineState, ctx: StepContext) -> str:
    assert state.load_bearing_claims is not None and state.claims is not None
    prompt_body = ctx.sections.get(_STEP_10_RESOLVE, "")
    return (
        f"## Load-Bearing Claims\n\n"
        f"{json.dumps([lb.model_dump() for lb in state.load_bearing_claims], ensure_ascii=False)}\n\n"
        f"## External Evidence\n\n"
        f"{json.dumps([ee.model_dump() for ee in (state.external_evidence or [])], ensure_ascii=False)}\n\n"
        f"## Claims\n\n"
        f"{json.dumps([c.model_dump() for c in state.claims if c.merged_into is None], ensure_ascii=False)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


# -- Extract hooks ------------------------------------------------------------


def _extract_all(state: PipelineState, results: list[Any]) -> None:
    all_raw_claims = []
    all_raw_evidence = []
    all_raw_markers = []
    for r in results:
        all_raw_claims.extend(r.output.claims)
        all_raw_evidence.extend(r.output.evidence)
        all_raw_markers.extend(r.output.markers)
    state.raw_claims = all_raw_claims
    state.raw_evidence = all_raw_evidence
    state.raw_rhetoric = all_raw_markers
    assert state.paper_source is not None
    state.claims, state.next_uid = _promote_claims(all_raw_claims, state.paper_source, state.next_uid)
    state.evidence, state.next_uid = _promote_evidence(all_raw_evidence, state.paper_source, state.next_uid)
    state.rhetoric, state.next_uid = _promote_rhetoric(all_raw_markers, state.paper_source, state.next_uid)


def _extract_factual(state: PipelineState, results: list[Any]) -> None:
    all_raw: list[RawClaim] = []
    for r in results:
        all_raw.extend(r.output.claims)
    state.raw_factual_claims = all_raw
    assert state.paper_source is not None
    factual_claims, state.next_uid = _promote_claims(all_raw, state.paper_source, state.next_uid)
    factual_claims = [
        c.model_copy(update={"kind": "factual"}) if c.kind != "factual" else c
        for c in factual_claims
    ]
    if state.claims is None:
        state.claims = factual_claims
    else:
        state.claims = list(state.claims) + factual_claims


def _extract_dedup_claims(state: PipelineState, output: DedupGroupingOutput) -> None:
    assert state.claims is not None
    claims = list(state.claims)
    survivors = [c for c in claims if c.merged_into is None]
    for group in output.groups:
        if len(group) < 2:
            continue
        valid = [i for i in group if 0 <= i < len(survivors)]
        if len(valid) < 2:
            continue
        longest_idx = max(valid, key=lambda i: len(survivors[i].text))
        for i in valid:
            if i != longest_idx:
                s = survivors[i]
                survivor_obj = survivors[longest_idx]
                idx_in_claims = next(
                    j for j, c in enumerate(claims) if c.uid == s.uid
                )
                claims[idx_in_claims] = s.model_copy(update={"merged_into": survivor_obj.uid})
                absorber_idx = next(
                    j for j, c in enumerate(claims) if c.uid == survivor_obj.uid
                )
                merged_quotes = list(claims[absorber_idx].original_quotes) + list(s.original_quotes)
                claims[absorber_idx] = claims[absorber_idx].model_copy(
                    update={"original_quotes": merged_quotes}
                )
    state.claims = claims


def _extract_dedup_evidence(state: PipelineState, output: DedupGroupingOutput) -> None:
    assert state.evidence is not None
    evidence = list(state.evidence)
    survivors = [e for e in evidence if e.merged_into is None]
    for group in output.groups:
        if len(group) < 2:
            continue
        valid = [i for i in group if 0 <= i < len(survivors)]
        if len(valid) < 2:
            continue
        lowest_idx = min(valid, key=lambda i: survivors[i].uid)
        for i in valid:
            if i != lowest_idx:
                s = survivors[i]
                survivor_obj = survivors[lowest_idx]
                idx_in_evidence = next(
                    j for j, e in enumerate(evidence) if e.uid == s.uid
                )
                evidence[idx_in_evidence] = s.model_copy(update={"merged_into": survivor_obj.uid})
                absorber_idx = next(
                    j for j, e in enumerate(evidence) if e.uid == survivor_obj.uid
                )
                merged_quotes = list(evidence[absorber_idx].original_quotes) + list(s.original_quotes)
                all_supports = list(evidence[absorber_idx].supports)
                for sup in s.supports:
                    if sup not in all_supports:
                        all_supports.append(sup)
                evidence[absorber_idx] = evidence[absorber_idx].model_copy(
                    update={
                        "original_quotes": merged_quotes,
                        "supports": all_supports,
                        "quantitative": evidence[absorber_idx].quantitative or s.quantitative,
                        "cited": evidence[absorber_idx].cited or s.cited,
                        "verifiable": evidence[absorber_idx].verifiable or s.verifiable,
                        "normative": evidence[absorber_idx].normative or s.normative,
                    }
                )
    state.evidence = evidence


def _extract_verify(state: PipelineState, output: VerifyOutput) -> None:
    state.support_map = [
        s for s in output.support_map
        if not any(euid == s.claim_uid for euid in s.evidence_uids)
    ]
    claim_uids = {c.uid for c in state.claims if c.merged_into is None} if state.claims else set()
    state.internal_contradictions = [
        ic.model_copy(update={
            "kind": "claim_vs_claim" if ic.source_uid in claim_uids else "evidence_vs_claim",
        })
        for ic in output.internal_contradictions
    ]


def _extract_load_bearing(state: PipelineState, output: LoadBearingOutput) -> None:
    state.load_bearing_claims = output.results


def _extract_resolve(state: PipelineState, output: ResolveOutput) -> None:
    state.load_bearing_claims = output.load_bearing_claims
    state.web_resolutions = output.web_resolutions


def _prepare_caput_causae(state: PipelineState, ctx: StepContext) -> str:
    assert state.load_bearing_claims is not None and state.claims is not None
    prompt_body = ctx.sections.get(_STEP_11_CAPUT_CAUSAE, "")
    anchored_uids = {
        lb.claim_uid for lb in state.load_bearing_claims
        if lb.classification in ("anchored", "externally_anchored")
    }
    anchored_claims = [
        c for c in state.claims
        if c.uid in anchored_uids and c.merged_into is None
    ]
    evidence_root_uids: set = set()
    if state.support_map:
        for s in state.support_map:
            if s.claim_uid in anchored_uids:
                evidence_root_uids.update(s.evidence_uids)
    evidence_items = []
    if state.evidence:
        evidence_items = [
            e for e in state.evidence
            if e.uid in evidence_root_uids and e.merged_into is None
        ]
    return (
        f"## Anchored Claims\n\n"
        f"{json.dumps([c.model_dump() for c in anchored_claims], ensure_ascii=False)}\n\n"
        f"## Evidence Roots\n\n"
        f"{json.dumps([e.model_dump() for e in evidence_items], ensure_ascii=False)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _extract_caput_causae(state: PipelineState, output: CaputCausaeOutput) -> None:
    state.caput_causae = output.caput_causae


def _prepare_detect_patterns(state: PipelineState, ctx: StepContext) -> str:
    assert state.rhetoric is not None and state.claims is not None
    prompt_body = ctx.sections.get(_STEP_12_DETECT_PATTERNS, "")
    markers_json = json.dumps(
        [m.model_dump() for m in state.rhetoric],
        ensure_ascii=False,
    )
    claims_json = json.dumps(
        [c.model_dump() for c in state.claims if c.merged_into is None],
        ensure_ascii=False,
    )
    thesis_preamble = ""
    if state.caput_causae is not None:
        thesis_preamble = f"The paper's central thesis: {state.caput_causae.thesis}\n\n"
    return (
        f"{thesis_preamble}"
        f"## Rhetorical Markers\n\n{markers_json}\n\n"
        f"## Claims\n\n{claims_json}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _extract_detect_patterns(state: PipelineState, output: PatternDetectionOutput) -> None:
    state.marker_patterns = output


# -- Pure step hooks ----------------------------------------------------------


async def _pure_read(state: PipelineState, ctx: StepContext) -> None:
    assert state.paper_source is not None
    state.chunks = _chunk_paper(state.paper_source)
    state.citations = _extract_citations(state.paper_source)


async def _pure_dedup_claims(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1 + one LLM call for tier 2 semantic grouping."""
    assert state.claims is not None
    claims = _dedup_tier0(state.claims)
    claims = _dedup_tier1(claims)
    state.claims = claims

    survivors = [c for c in claims if c.merged_into is None]
    if len(survivors) <= 1:
        return

    assert ctx._current_spec is not None
    user_msg = _prepare_dedup_claims(state, ctx)
    result = await _run_agent_with_retry(
        ctx, ctx._current_spec, user_msg,
        request_limit=_REQUEST_LIMIT_DEDUP,
    )
    _extract_dedup_claims(state, result.output)


async def _pure_dedup_evidence(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1 + one LLM call for tier 2 semantic grouping."""
    assert state.evidence is not None
    evidence = _dedup_tier0(state.evidence)
    evidence = _dedup_tier1(evidence)
    state.evidence = evidence

    survivors = [e for e in evidence if e.merged_into is None]
    if len(survivors) <= 1:
        return

    assert ctx._current_spec is not None
    user_msg = _prepare_dedup_evidence(state, ctx)
    result = await _run_agent_with_retry(
        ctx, ctx._current_spec, user_msg,
        request_limit=_REQUEST_LIMIT_DEDUP,
    )
    _extract_dedup_evidence(state, result.output)


async def _pure_dedup_factual(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1 + one LLM call for tier 2 on factual claims only."""
    assert state.claims is not None
    normative = [c for c in state.claims if c.kind != "factual"]
    factual = [c for c in state.claims if c.kind == "factual"]
    if not factual:
        return

    factual = _dedup_tier0(factual)
    factual = _dedup_tier1(factual)

    survivors = [c for c in factual if c.merged_into is None]
    if len(survivors) > 1:
        assert ctx._current_spec is not None
        prompt_body = ctx.sections.get(_STEP_4_DEDUP_FACTUAL, "")
        survivor_questions = json.dumps(
            [{"idx": i, "question": s.question} for i, s in enumerate(survivors)],
            ensure_ascii=False,
        )
        user_msg = (
            f"## Survivors\n\n{survivor_questions}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        result = await _run_agent_with_retry(
            ctx, ctx._current_spec, user_msg,
            request_limit=_REQUEST_LIMIT_DEDUP,
        )
        _extract_dedup_claims(state, result.output)
        factual = [c for c in state.claims if c.kind == "factual"]

    state.claims = normative + factual


def _known_paper_urls(
    citations: list[CitationRef],
    backend: StorageBackend | None,
) -> dict[str, str]:
    """Map citation paper_id -> canonical URL, when paperstore knows it.

    Returns an empty dict when ``backend`` is None or no citation has a
    matching paperstore row. Missing citations are silently omitted; the
    agent falls back to the cascade for those.
    """
    if backend is None:
        return {}
    out: dict[str, str] = {}
    for cit in citations:
        result = backend.resolve_year_for_paper(cit.paper_id)
        if result is None:
            continue
        _, row = result
        if row.url:
            out[cit.paper_id] = row.url
    return out


async def _pure_verify_citations(state: PipelineState, ctx: StepContext) -> None:
    """Spawn a run_task per citation in parallel to verify and collect evidence."""
    assert state.citations is not None and state.claims is not None

    web_fetch_fn = ctx.tool_registry["web_fetch"]

    assert ctx._current_spec is not None
    prompt_body = ctx.sections.get(_STEP_8_VERIFY_CITATIONS, "")
    system = (
        "You are a citation verifier. Fetch the cited paper, check whether "
        "it says what the citing paper claims, and report any evidence "
        "relevant to the paper's claims."
    )
    model_slot = ctx._current_spec.meta.model_slot
    resolved = ctx.model_slots.get(model_slot)
    if resolved is None:
        resolved = _DEFAULT_MODEL_SLOTS.get(model_slot, model_slot)

    alive_claims = [c for c in state.claims if c.merged_into is None]
    alive_evidence = [e for e in (state.evidence or []) if e.merged_into is None]

    known_urls = await asyncio.to_thread(
        _known_paper_urls, state.citations, ctx.backend,
    )

    async def _one_citation(cit) -> CitationTaskOutput:
        pid_num = cit.paper_id
        primary_claims = [c for c in alive_claims if pid_num in c.text]
        primary_evidence = [e for e in alive_evidence if pid_num in e.text]
        secondary_questions = [c.question for c in alive_claims]

        known_url = known_urls.get(cit.paper_id)
        known_url_block = (
            f"## Known URL\n\n{known_url}\n\n"
            if known_url
            else ""
        )
        user_msg = (
            f"## Citation\n\nPaper: {cit.paper_id} (cited {cit.count} times)\n\n"
            f"{known_url_block}"
            f"## Primary Claims\n\n"
            f"{json.dumps([c.model_dump() for c in primary_claims], ensure_ascii=False)}\n\n"
            f"## Primary Evidence\n\n"
            f"{json.dumps([e.model_dump() for e in primary_evidence], ensure_ascii=False)}\n\n"
            f"## Secondary Questions\n\n"
            f"{json.dumps(secondary_questions, ensure_ascii=False)}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        return await run_task(
            system_prompt=system,
            user_message=user_msg,
            output_type=CitationTaskOutput,
            tools={"web_fetch": web_fetch_fn},
            model=resolved,
            request_limit=_REQUEST_LIMIT_PER_CITATION,
        )

    results = await asyncio.gather(*[_one_citation(c) for c in state.citations])

    audit_entries = []
    evidence_items = list(state.external_evidence or [])
    for r in results:
        audit_entries.append(r.audit)
        evidence_items.extend(r.evidence)

    state.citation_audit = audit_entries
    state.external_evidence = evidence_items


async def _pure_web_search(state: PipelineState, ctx: StepContext) -> None:
    """Spawn a run_task per triggered claim in parallel for web research."""
    assert state.claims is not None and state.load_bearing_claims is not None

    triggered = [
        lb for lb in state.load_bearing_claims
        if lb.classification == _CLASSIFICATION_CRITICAL_GAP
    ]
    covered_uids = set()
    if state.external_evidence:
        for ee in state.external_evidence:
            if ee.stance == "supports":
                covered_uids.add(ee.claim_uid)
    triggered = [lb for lb in triggered if lb.claim_uid not in covered_uids]

    claims_for_search = []
    for lb in triggered:
        claim = next(
            (c for c in state.claims if c.uid == lb.claim_uid and c.merged_into is None),
            None,
        )
        if claim:
            claims_for_search.append(claim)

    if not claims_for_search:
        return

    web_search_fn = ctx.tool_registry["web_search"]
    web_fetch_fn = ctx.tool_registry["web_fetch"]

    assert ctx._current_spec is not None
    prompt_body = ctx.sections.get(_STEP_9_WEB_SEARCH, "")
    system = ctx.sections.get(_SECTION_SYSTEM_PROMPT, "")
    model_slot = ctx._current_spec.meta.model_slot
    resolved = ctx.model_slots.get(model_slot)
    if resolved is None:
        resolved = _DEFAULT_MODEL_SLOTS.get(model_slot, model_slot)

    async def _one_claim(claim) -> list:
        user_msg = (
            f"## Claim\n\n"
            f"{json.dumps(claim.model_dump(), ensure_ascii=False)}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        result = await run_task(
            system_prompt=system,
            user_message=user_msg,
            output_type=WebSearchOutput,
            tools={"web_search": web_search_fn, "web_fetch": web_fetch_fn},
            model=resolved,
            request_limit=_REQUEST_LIMIT_PER_CLAIM,
        )
        return [
            ee.model_copy(update={"claim_uid": claim.uid})
            for ee in result.external_evidence
        ]

    results = await asyncio.gather(*[_one_claim(c) for c in claims_for_search])

    all_evidence = list(state.external_evidence or [])
    for batch in results:
        all_evidence.extend(batch)
    state.external_evidence = all_evidence


async def _pure_report(state: PipelineState, ctx: StepContext) -> None:
    assert state.claims is not None
    meta = await asyncio.to_thread(ctx.backend.get_meta, ctx.pid) if ctx.backend else None
    title = meta.title if meta else "Untitled"
    state.report = render_report(state, ctx.pid, title)


# -- Guard hooks --------------------------------------------------------------


def _guard_verify_citations(state: PipelineState) -> bool:
    return bool(state.citations)


def _guard_web_search(state: PipelineState) -> bool:
    if not state.load_bearing_claims:
        return False
    triggered = [
        lb for lb in state.load_bearing_claims
        if lb.classification == _CLASSIFICATION_CRITICAL_GAP
    ]
    if not triggered:
        return False
    covered_uids = set()
    if state.external_evidence:
        for ee in state.external_evidence:
            if ee.stance == "supports":
                covered_uids.add(ee.claim_uid)
    return any(lb.claim_uid not in covered_uids for lb in triggered)


def _guard_resolve(state: PipelineState) -> bool:
    return bool(state.external_evidence)


def _guard_caput_causae(state: PipelineState) -> bool:
    if not state.load_bearing_claims:
        return False
    return any(
        lb.classification in ("anchored", "externally_anchored")
        for lb in state.load_bearing_claims
    )


def _guard_detect_patterns(state: PipelineState) -> bool:
    return bool(state.rhetoric)


# -- Hook registry ------------------------------------------------------------

# Step names must match exactly the ## headers in dissect.md.
_HOOKS: dict[str, StepHooks] = {
    _STEP_0_READ: StepHooks(pure=_pure_read),

    _STEP_1_EXTRACT: StepHooks(
        output_type=ExtractAllOutput,
        prepare=_prepare_extract_chunk,
        extract=_extract_all,
        retry_empty=lambda o: not o.analysis_complete,
        parallel=True,
    ),

    _STEP_2_DEDUP_CLAIMS: StepHooks(
        output_type=DedupGroupingOutput,
        pure=_pure_dedup_claims,
    ),

    _STEP_3_EXTRACT_FACTUAL: StepHooks(
        output_type=ExtractFactualOutput,
        prepare=_prepare_extract_factual_chunk,
        extract=_extract_factual,
        retry_empty=lambda o: not o.analysis_complete,
        parallel=True,
    ),

    _STEP_4_DEDUP_FACTUAL: StepHooks(
        output_type=DedupGroupingOutput,
        pure=_pure_dedup_factual,
    ),

    _STEP_5_DEDUP_EVIDENCE: StepHooks(
        output_type=DedupGroupingOutput,
        pure=_pure_dedup_evidence,
    ),

    _STEP_6_VERIFY: StepHooks(
        output_type=VerifyOutput,
        prepare=_prepare_verify,
        extract=_extract_verify,
    ),

    _STEP_7_LOAD_BEARING: StepHooks(
        output_type=LoadBearingOutput,
        prepare=_prepare_load_bearing,
        extract=_extract_load_bearing,
    ),

    _STEP_8_VERIFY_CITATIONS: StepHooks(
        pure=_pure_verify_citations,
        guard=_guard_verify_citations,
    ),

    _STEP_9_WEB_SEARCH: StepHooks(
        pure=_pure_web_search,
        guard=_guard_web_search,
    ),

    _STEP_10_RESOLVE: StepHooks(
        output_type=ResolveOutput,
        prepare=_prepare_resolve,
        extract=_extract_resolve,
        guard=_guard_resolve,
        request_limit=15,
    ),

    _STEP_11_CAPUT_CAUSAE: StepHooks(
        output_type=CaputCausaeOutput,
        prepare=_prepare_caput_causae,
        extract=_extract_caput_causae,
        guard=_guard_caput_causae,
    ),

    _STEP_12_DETECT_PATTERNS: StepHooks(
        output_type=PatternDetectionOutput,
        prepare=_prepare_detect_patterns,
        extract=_extract_detect_patterns,
        guard=_guard_detect_patterns,
    ),

    _STEP_13_REPORT: StepHooks(pure=_pure_report),
}


# -- Dispatch -----------------------------------------------------------------


async def _run_parallel_chunks(
    state: PipelineState,
    ctx: StepContext,
    spec: StepSpec,
) -> list[Any]:
    """Run a step per-chunk via asyncio.gather, with retry-on-empty."""
    assert state.chunks is not None
    assert spec.hooks.prepare is not None

    total = len(state.chunks)

    async def _one_chunk(idx: int, chunk: Chunk) -> Any:
        user_msg = spec.hooks.prepare(state, ctx, chunk)
        return await _run_agent_with_retry(
            ctx, spec, user_msg,
            retries=_RETRIES_CHUNK,
            chunk_label=f"chunk {idx}/{total}",
        )

    return await asyncio.gather(
        *[_one_chunk(i + 1, c) for i, c in enumerate(state.chunks)]
    )


async def _dispatch(
    pipeline: list[StepSpec],
    state: PipelineState,
    ctx: StepContext,
    *,
    stop_after: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Execute the pipeline step by step."""
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
            elif spec.hooks.parallel:
                results = await _run_parallel_chunks(state, ctx, spec)
                if spec.hooks.extract:
                    spec.hooks.extract(state, results)
            else:
                assert spec.hooks.prepare is not None
                user_msg = spec.hooks.prepare(state, ctx)
                result = await _run_agent_with_retry(
                    ctx, spec, user_msg,
                    request_limit=spec.hooks.request_limit or _REQUEST_LIMIT,
                )
                if spec.hooks.extract:
                    spec.hooks.extract(state, result.output)
        except (StepError, PromptFileError):
            raise
        except Exception as exc:
            logger.error(
                "Step %d (%s) failed: %s", i, spec.meta.name, exc, exc_info=True,
            )
            raise StepError(i, spec.meta.name, exc) from exc

        if ctx.backend is not None:
            step_name = spec.meta.name
            if step_name == _STEP_0_READ and state.citations:
                ctx.backend.store_paper_citations(ctx.pid, state.citations)
            elif step_name == _STEP_1_EXTRACT and state.rhetoric:
                ctx.backend.store_rhetoric(ctx.pid, state.rhetoric)
            elif step_name == _STEP_2_DEDUP_CLAIMS and state.claims:
                ctx.backend.store_claims(ctx.pid, state.claims)
            elif step_name == _STEP_5_DEDUP_EVIDENCE and state.evidence:
                ctx.backend.store_evidence(ctx.pid, state.evidence)
            elif step_name == _STEP_6_VERIFY and state.support_map and state.claims:
                ctx.backend.store_questions(ctx.pid, state.claims, state.support_map)
            elif step_name == _STEP_8_VERIFY_CITATIONS and state.citation_audit:
                # CitationAuditEntry calls the cited paper number `paper_id`,
                # but store_citation_audit (and the DB column) expect
                # `cited_paper_id`. Adapt at the boundary so the LLM-facing
                # field name stays simple and the storage schema stays
                # explicit about what kind of paper_id it stores.
                from types import SimpleNamespace
                ctx.backend.store_citation_audit(ctx.pid, [
                    SimpleNamespace(
                        cited_paper_id=e.paper_id,
                        resolution_method=e.resolution_method,
                        resolved=e.resolved,
                        source_url=e.source_url,
                        quote_match=e.quote_match,
                        discrepancy=e.discrepancy,
                    )
                    for e in state.citation_audit
                ])
            elif step_name == _STEP_9_WEB_SEARCH and state.external_evidence:
                ctx.backend.store_external_citations(ctx.pid, state.external_evidence)
            elif step_name == _STEP_11_CAPUT_CAUSAE and state.caput_causae:
                ctx.backend.store_caput_causae(ctx.pid, state.caput_causae.thesis)

    if on_progress is not None:
        on_progress(ProgressEvent(
            step=total, total=total, name="done", pct=1.0,
        ))


# -- Public API ---------------------------------------------------------------


async def dissect_paper(
    pid: str,
    backend: StorageBackend,
    *,
    model_slots: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> str:
    """Extract structural questions from a WG21 paper.

    Loads the paper from paperstore via ``pid``, runs the multi-step
    extractor pipeline, and returns the final report string (two
    bulleted question lists).

    Pass ``on_progress`` to receive
    :class:`~paperstore.progress.ProgressEvent` notifications at each
    step transition.

    Pass ``debug=True`` to write a single markdown transcript of every
    LLM interaction to paperstore as ``<pid>.debug.md``.

    Pass ``trace=True`` to write a pipeline state summary to
    ``<pid>.trace.md`` alongside the dissection. Shows intermediate state
    at every step (claims, evidence, support map, etc.).

    Raises :class:`PromptFileError` if ``dissect.md`` has structural
    problems. Raises :class:`PaperNotFoundError` or
    :class:`PaperNotConvertedError` if the paper is missing.
    """
    from web_tools import WebResearcher

    slots = {**_DEFAULT_MODEL_SLOTS, **(model_slots or {})}
    secs = load_sections()

    if _SECTION_SYSTEM_PROMPT not in secs:
        raise PromptFileError(
            "'System Prompt' section not found in dissect.md. "
            f"Available sections: {sorted(secs)}"
        )

    pipeline = build_pipeline(secs, _HOOKS)

    try:
        meta = backend.get_meta(pid)
    except MissingMetaError as exc:
        raise PaperNotFoundError(
            f"Paper '{pid}' not found in paperstore. "
            f"Run 'paperflow mailing <year>' to index it, "
            f"then 'paperflow download {pid}' to stage its source."
        ) from exc

    try:
        paper_md = backend.get_paper_md(pid)
    except MissingPaperMdError as exc:
        raise PaperNotConvertedError(
            f"Paper '{pid}' has no converted markdown. "
            f"Run 'paperflow convert {pid}' first."
        ) from exc

    backend.clear_dissect(pid)

    state = PipelineState(paper_source=paper_md)

    async with WebResearcher() as researcher:
        tool_reg: dict[str, Callable[..., Any]] = {}

        from paperstore.tools import PaperstoreTools
        ps_tools = PaperstoreTools(backend)
        tool_reg["paper_meta"] = ps_tools.paper_meta
        tool_reg["paper_meta_latest"] = ps_tools.paper_meta_latest
        tool_reg["read_file"] = ps_tools.read_file
        tool_reg["web_search"] = researcher.web_search
        tool_reg["web_fetch"] = researcher.web_fetch

        ctx = StepContext(
            sections=secs,
            model_slots=slots,
            researcher=researcher,
            backend=backend,
            debug=debug,
            pid=pid,
            tool_registry=tool_reg,
        )

        debug_path = backend.get_debug_md_path(pid, "dissect")
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
        return render_trace(state, meta, stop_after)

    if trace:
        last_step = len(pipeline) - 1
        trace_path = backend.get_trace_md_path(pid, "dissect")
        trace_path.write_text(
            render_trace(state, meta, last_step), encoding="utf-8",
        )

    return state.report or ""


async def dissect_since(
    month: str,
    backend: StorageBackend,
    *,
    model_slots: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> list[dict[str, str | None]]:
    """Dissect all papers with mailing_date >= ``month``.

    Iterates sequentially, calling :func:`dissect_paper` for each.
    Per-paper errors are caught and logged; the loop continues.

    Returns a list of result dicts:
    ``{"paper_id": str, "status": "ok"|"error", "error": str|None}``.
    """
    papers = backend.list_papers_since(month)
    results: list[dict[str, str | None]] = []

    for paper in papers:
        pid = paper.paper_id
        try:
            report = await dissect_paper(
                pid, backend,
                model_slots=model_slots,
                on_progress=on_progress,
                stop_after=stop_after,
                debug=debug,
                trace=trace,
            )
            out_path = backend.write_dissect_md(pid, report)
            logger.info("Dissected %s -> %s", pid, out_path)
            results.append({"paper_id": pid, "status": "ok", "error": None})
        except Exception as exc:
            logger.error("Failed to dissect %s: %s", pid, exc)
            results.append({"paper_id": pid, "status": "error", "error": str(exc)})

    return results
