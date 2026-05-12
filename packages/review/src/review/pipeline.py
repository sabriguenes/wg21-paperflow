#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async extractor pipeline for WG21 papers.

All LLM-facing text comes from ``extractor.md`` at runtime. This module
contains only structural orchestration: hook definitions, the generic
runner, and the dispatch loop. ``extractor.md`` is the upstream
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
from pathlib import Path
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
from paperstore.backend import PaperRow, StorageBackend
from paperstore.progress import ProgressCallback, ProgressEvent
from paperstore.errors import MissingMetaError, MissingPaperMdError

from review.errors import (
    HookMismatchError,
    PaperNotConvertedError,
    PaperNotFoundError,
    PromptFileError,
    StepError,
    TransientStepError,
    ValidationStepError,
)
from review.harness import (
    chunk_paper,
    dedup_tier0,
    dedup_tier1,
    extract_citations,
    number_lines,
    promote_claims,
    promote_evidence,
)
from review.models import (
    Chunk,
    DedupGroupingOutput,
    ExtractClaimsOutput,
    ExtractEvidenceOutput,
    LoadBearingOutput,
    PipelineState,
    ResolveOutput,
    VerifyOutput,
    WebSearchOutput,
)
from review.parse import sections
from review.prompt import StepHooks, StepSpec, build_pipeline
from review.render import render_debug_md, render_report, render_trace

if TYPE_CHECKING:
    from reviewstore import ReviewStore
    from web_tools import WebResearcher

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL_SLOTS = {
    "fast": "anthropic:claude-haiku-4-5-20251001",
    "default": "anthropic:claude-opus-4-6",
}

_DEFAULT_MODEL_SETTINGS = ModelSettings(max_tokens=80000)

_SECTION_SYSTEM_PROMPT = "System Prompt"
_REQUEST_LIMIT = 500
_REQUEST_LIMIT_DEDUP = 50
_RETRIES_CHUNK = 5
_RETRIES_SINGLE = 3
_CLASSIFICATION_CRITICAL_GAP = "critical_gap"
_RETRIES_EMPTY_OUTPUT = 3
_DEBUG_SEPARATOR = "\n\n---\n\n"

_STEP_0_READ = "Step 0 \u2014 Read"
_STEP_1_EXTRACT_CLAIMS = "Step 1 \u2014 Extract Claims"
_STEP_2_DEDUP_CLAIMS = "Step 2 \u2014 Dedup Claims"
_STEP_3_EXTRACT_EVIDENCE = "Step 3 \u2014 Extract Evidence"
_STEP_4_DEDUP_EVIDENCE = "Step 4 \u2014 Dedup Evidence"
_STEP_5_VERIFY = "Step 5 \u2014 Verify + Deps + Map + Contradict"
_STEP_6_LOAD_BEARING = "Step 6 \u2014 Load-Bearing"
_STEP_7_WEB_SEARCH = "Step 7 \u2014 Web Search"
_STEP_8_RESOLVE = "Step 8 \u2014 Resolve External"
_STEP_9_REPORT = "Step 9 \u2014 Report"


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
    """Load and parse extractor.md once per process."""
    try:
        resource = importlib.resources.files("review").joinpath("extractor.md")
        return sections(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        raise PromptFileError(
            f"Failed to read extractor.md: {exc}"
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
        model_settings=_DEFAULT_MODEL_SETTINGS,
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


def _wrap_tool_debug(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    """Wrap a tool function to log calls when debugging."""
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        args_str = ", ".join(
            [repr(a) for a in args] +
            [f"{k}={repr(v)}" for k, v in kwargs.items()]
        )
        logger.debug("[tool] %s(%s)", name, args_str)
        return await fn(*args, **kwargs)
    return wrapper


def _classify_and_raise(exc: Exception, spec: StepSpec) -> None:
    """Wrap a pydantic-ai exception into the appropriate StepError subclass."""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        raise TransientStepError(spec.meta.number, spec.meta.name, exc) from exc
    if isinstance(exc, _VALIDATION_EXCEPTIONS):
        raise ValidationStepError(spec.meta.number, spec.meta.name, exc) from exc
    raise StepError(spec.meta.number, spec.meta.name, exc) from exc


# -- Prepare hooks ------------------------------------------------------------


def _prepare_claims_chunk(state: PipelineState, ctx: StepContext, chunk: Chunk) -> str:
    prompt_body = ctx.sections.get(_STEP_1_EXTRACT_CLAIMS, "")
    return (
        f"## Chunk\n\n{number_lines(chunk)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_evidence_chunk(state: PipelineState, ctx: StepContext, chunk: Chunk) -> str:
    prompt_body = ctx.sections.get(_STEP_3_EXTRACT_EVIDENCE, "")
    return (
        f"## Chunk\n\n{number_lines(chunk)}\n\n"
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
    prompt_body = ctx.sections.get(_STEP_4_DEDUP_EVIDENCE, "")
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
    prompt_body = ctx.sections.get(_STEP_5_VERIFY, "")
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
    prompt_body = ctx.sections.get(_STEP_6_LOAD_BEARING, "")
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


def _prepare_web_search(state: PipelineState, ctx: StepContext) -> str:
    assert state.claims is not None and state.load_bearing_claims is not None
    prompt_body = ctx.sections.get(_STEP_7_WEB_SEARCH, "")
    triggered = [
        lb for lb in state.load_bearing_claims
        if lb.classification == _CLASSIFICATION_CRITICAL_GAP
    ]
    claims_for_search = []
    for lb in triggered:
        claim = next(
            (c for c in state.claims if c.loc == lb.claim_loc and c.merged_into is None),
            None,
        )
        if claim:
            claims_for_search.append(claim.model_dump())
    return (
        f"## Triggered Claims\n\n{json.dumps(claims_for_search, ensure_ascii=False)}\n\n"
        f"## Paper Citations\n\n{json.dumps([c.model_dump() for c in (state.citations or [])], ensure_ascii=False)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_resolve(state: PipelineState, ctx: StepContext) -> str:
    assert state.load_bearing_claims is not None and state.claims is not None
    prompt_body = ctx.sections.get(_STEP_8_RESOLVE, "")
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


def _extract_claims(state: PipelineState, results: list[Any]) -> None:
    all_raws = []
    for r in results:
        all_raws.extend(r.output.claims)
    state.raw_claims = all_raws
    assert state.paper_source is not None
    state.claims = promote_claims(all_raws, state.paper_source)


def _extract_evidence(state: PipelineState, results: list[Any]) -> None:
    all_raws = []
    for r in results:
        all_raws.extend(r.output.evidence)
    state.raw_evidence = all_raws
    assert state.paper_source is not None
    state.evidence = promote_evidence(all_raws, state.paper_source)


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
                    j for j, c in enumerate(claims) if c.loc == s.loc
                )
                claims[idx_in_claims] = s.model_copy(update={"merged_into": survivor_obj.loc})
                absorber_idx = next(
                    j for j, c in enumerate(claims) if c.loc == survivor_obj.loc
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
        lowest_idx = min(valid, key=lambda i: (survivors[i].loc.line, survivors[i].loc.start_char))
        for i in valid:
            if i != lowest_idx:
                s = survivors[i]
                survivor_obj = survivors[lowest_idx]
                idx_in_evidence = next(
                    j for j, e in enumerate(evidence) if e.loc == s.loc
                )
                evidence[idx_in_evidence] = s.model_copy(update={"merged_into": survivor_obj.loc})
                absorber_idx = next(
                    j for j, e in enumerate(evidence) if e.loc == survivor_obj.loc
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
        if not any(eloc == s.claim_loc for eloc in s.evidence_locs)
    ]
    state.internal_contradictions = output.internal_contradictions


def _extract_load_bearing(state: PipelineState, output: LoadBearingOutput) -> None:
    state.load_bearing_claims = output.results


def _extract_web_search(state: PipelineState, output: WebSearchOutput) -> None:
    state.external_evidence = output.external_evidence


def _extract_resolve(state: PipelineState, output: ResolveOutput) -> None:
    state.load_bearing_claims = output.load_bearing_claims
    state.web_resolutions = output.web_resolutions


# -- Pure step hooks ----------------------------------------------------------


async def _pure_read(state: PipelineState, ctx: StepContext) -> None:
    assert state.paper_source is not None
    state.chunks = chunk_paper(state.paper_source)
    state.citations = extract_citations(state.paper_source)


async def _pure_dedup_claims(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1 + one LLM call for tier 2 semantic grouping."""
    assert state.claims is not None
    claims = dedup_tier0(state.claims)
    claims = dedup_tier1(claims)
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
    evidence = dedup_tier0(state.evidence)
    evidence = dedup_tier1(evidence)
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


async def _pure_report(state: PipelineState, ctx: StepContext) -> None:
    assert state.claims is not None
    meta = await asyncio.to_thread(ctx.backend.get_meta, ctx.pid) if ctx.backend else {}
    title = meta.get("title", "Untitled")
    state.report = render_report(state, ctx.pid, title)


# -- Guard hooks --------------------------------------------------------------


def _guard_web_search(state: PipelineState) -> bool:
    if not state.load_bearing_claims:
        return False
    return any(
        lb.classification == _CLASSIFICATION_CRITICAL_GAP
        for lb in state.load_bearing_claims
    )


def _guard_resolve(state: PipelineState) -> bool:
    return bool(state.external_evidence)


# -- Hook registry ------------------------------------------------------------

# Step names must match exactly the ## headers in extractor.md.
_HOOKS: dict[str, StepHooks] = {
    _STEP_0_READ: StepHooks(pure=_pure_read),

    _STEP_1_EXTRACT_CLAIMS: StepHooks(
        output_type=ExtractClaimsOutput,
        prepare=_prepare_claims_chunk,
        extract=_extract_claims,
        retry_empty=lambda o: not o.claims,
        parallel=True,
    ),

    _STEP_2_DEDUP_CLAIMS: StepHooks(
        output_type=DedupGroupingOutput,
        pure=_pure_dedup_claims,
    ),

    _STEP_3_EXTRACT_EVIDENCE: StepHooks(
        output_type=ExtractEvidenceOutput,
        prepare=_prepare_evidence_chunk,
        extract=_extract_evidence,
        retry_empty=lambda o: not o.evidence,
        parallel=True,
    ),

    _STEP_4_DEDUP_EVIDENCE: StepHooks(
        output_type=DedupGroupingOutput,
        pure=_pure_dedup_evidence,
    ),

    _STEP_5_VERIFY: StepHooks(
        output_type=VerifyOutput,
        prepare=_prepare_verify,
        extract=_extract_verify,
    ),

    _STEP_6_LOAD_BEARING: StepHooks(
        output_type=LoadBearingOutput,
        prepare=_prepare_load_bearing,
        extract=_extract_load_bearing,
    ),

    _STEP_7_WEB_SEARCH: StepHooks(
        output_type=WebSearchOutput,
        prepare=_prepare_web_search,
        extract=_extract_web_search,
        guard=_guard_web_search,
    ),

    _STEP_8_RESOLVE: StepHooks(
        output_type=ResolveOutput,
        prepare=_prepare_resolve,
        extract=_extract_resolve,
        guard=_guard_resolve,
    ),

    _STEP_9_REPORT: StepHooks(pure=_pure_report),
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

    async def _one_chunk(chunk: Chunk) -> Any:
        user_msg = spec.hooks.prepare(state, ctx, chunk)
        return await _run_agent_with_retry(
            ctx, spec, user_msg, retries=_RETRIES_CHUNK,
        )

    return await asyncio.gather(*[_one_chunk(c) for c in state.chunks])


async def _dispatch(
    pipeline: list[StepSpec],
    state: PipelineState,
    ctx: StepContext,
    *,
    stop_after: int | None = None,
    on_progress: ProgressCallback | None = None,
    rstore: ReviewStore | None = None,
    pid: str = "",
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
                result = await _run_agent_with_retry(ctx, spec, user_msg)
                if spec.hooks.extract:
                    spec.hooks.extract(state, result.output)
        except (StepError, PromptFileError):
            raise
        except Exception as exc:
            logger.error(
                "Step %d (%s) failed: %s", i, spec.meta.name, exc, exc_info=True,
            )
            raise StepError(i, spec.meta.name, exc) from exc

        if rstore is not None:
            if i == 0 and state.citations:
                rstore.store_paper_citations(pid, state.citations)
            elif i == 2 and state.claims:
                rstore.store_claims(pid, state.claims)
            elif i == 4 and state.evidence:
                rstore.store_evidence(pid, state.evidence)
            elif i == 5 and state.support_map and state.claims:
                rstore.store_questions(pid, state.claims, state.support_map)
            elif i == 7 and state.external_evidence:
                rstore.store_external_citations(pid, state.external_evidence)

    if on_progress is not None:
        on_progress(ProgressEvent(
            step=total, total=total, name="done", pct=1.0,
        ))


# -- Public API ---------------------------------------------------------------


async def review_paper(
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
    ``<pid>.trace.md`` alongside the review. Shows intermediate state
    at every step (claims, evidence, support map, etc.).

    Raises :class:`PromptFileError` if ``extractor.md`` has structural
    problems. Raises :class:`PaperNotFoundError` or
    :class:`PaperNotConvertedError` if the paper is missing.
    """
    from web_tools import WebResearcher
    from reviewstore import ReviewStore

    slots = {**_DEFAULT_MODEL_SLOTS, **(model_slots or {})}
    secs = load_sections()

    if _SECTION_SYSTEM_PROMPT not in secs:
        raise PromptFileError(
            "'System Prompt' section not found in extractor.md. "
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

    backend.clear_review(pid)

    state = PipelineState(paper_source=paper_md)
    rstore = ReviewStore(backend.workspace_dir)

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

        debug_path = backend.get_paper_md_path(pid).with_suffix(".debug.md")
        if debug:
            debug_path.unlink(missing_ok=True)

        try:
            await _dispatch(
                pipeline, state, ctx,
                stop_after=stop_after,
                on_progress=on_progress,
                rstore=rstore,
                pid=pid,
            )
        finally:
            if debug and ctx.debug_log:
                debug_path.write_text(
                    _DEBUG_SEPARATOR.join(ctx.debug_log), encoding="utf-8",
                )
            rstore.close()

    if stop_after is not None:
        return render_trace(state, meta, stop_after)

    if trace:
        last_step = len(pipeline) - 1
        trace_path = backend.get_paper_md_path(pid).with_suffix(".trace.md")
        trace_path.write_text(
            render_trace(state, meta, last_step), encoding="utf-8",
        )

    return state.report or ""
