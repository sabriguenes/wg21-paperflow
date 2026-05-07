#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async extractor pipeline for WG21 papers.

All LLM-facing text comes from ``extractor.md`` at runtime. This module
contains only structural orchestration — no prompt strings.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.resources
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from paperstore.backend import StorageBackend
from paperstore.progress import ProgressCallback, ProgressEvent
from paperstore.errors import MissingMetaError, MissingPaperMdError

from review.errors import ReviewError
from review.harness import (
    chunk_paper,
    dedup_tier0,
    dedup_tier1,
    extract_citations,
    promote_claims,
    promote_evidence,
)
from review.models import (
    DedupGroupingOutput,
    ExtractClaimsOutput,
    ExtractEvidenceOutput,
    LoadBearingOutput,
    LoadBearingResult,
    PipelineState,
    ResolveOutput,
    VerifyOutput,
    WebSearchOutput,
)
from review.parse import sections

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_SLOTS = {
    "fast": "anthropic:claude-haiku-4-5-20251001",
    "default": "anthropic:claude-sonnet-4-6",
}

StepFn = Callable[["PipelineState", "StepContext"], Awaitable[None]]


@dataclass
class StepContext:
    """Shared resources available to every step function."""

    sections: dict[str, str]
    model_slots: dict[str, str]
    researcher: Any = None
    backend: Any = None
    on_progress: ProgressCallback | None = None
    debug: bool = False
    pid: str = ""
    debug_log: list[str] | None = None

    def __post_init__(self) -> None:
        if self.debug and self.debug_log is None:
            self.debug_log = []


@functools.cache
def load_sections() -> dict[str, str]:
    """Load and parse extractor.md once per process."""
    resource = importlib.resources.files("review").joinpath("extractor.md")
    return sections(resource.read_text(encoding="utf-8"))


_TOOL_OMIT_NAMES = frozenset({
    "web_search", "web_fetch", "read_file", "paper_meta", "paper_meta_latest",
})


def _render_debug_md(result: Any, step_name: str) -> str:
    """Render an agent run result as a markdown debug section."""
    parts: list[str] = [f"# {step_name}\n"]
    for msg in result.all_messages():
        kind = msg.kind
        if kind == "request":
            for part in msg.parts:
                if hasattr(part, "content") and part.part_kind == "system-prompt":
                    parts.append(f"## System Prompt\n\n{part.content}\n")
                elif hasattr(part, "content") and part.part_kind == "user-prompt":
                    parts.append(f"## User Message\n\n{part.content}\n")
                elif part.part_kind == "tool-return":
                    tool_name = getattr(part, "tool_name", "")
                    if tool_name in _TOOL_OMIT_NAMES:
                        parts.append(
                            f"### Tool Return: {tool_name}\n\n*(response omitted)*\n"
                        )
                    else:
                        content = getattr(part, "content", "")
                        parts.append(
                            f"### Tool Return: {tool_name}\n\n{content}\n"
                        )
        elif kind == "response":
            for part in msg.parts:
                if part.part_kind == "text":
                    parts.append(f"## Model Response\n\n{part.content}\n")
                elif part.part_kind == "tool-call":
                    tool_name = getattr(part, "tool_name", "")
                    args = getattr(part, "args", "")
                    args_str = json.dumps(args) if not isinstance(args, str) else args
                    parts.append(
                        f"### Tool Call: {tool_name}\n\n```json\n{args_str}\n```\n"
                    )
    if hasattr(result, "output"):
        output = result.output
        if hasattr(output, "model_dump"):
            output_str = json.dumps(output.model_dump(), indent=2, ensure_ascii=False, default=str)
        else:
            output_str = str(output)
        parts.append(f"## Final Output\n\n```json\n{output_str}\n```\n")
    return "\n".join(parts)


def _load_paper(pid: str, backend: StorageBackend) -> tuple[dict, str]:
    """Load paper metadata and markdown, raising ReviewError on failure."""
    try:
        meta = backend.get_meta(pid)
    except MissingMetaError as exc:
        raise ReviewError(
            f"Paper '{pid}' not found in paperstore. "
            f"Run 'paperflow mailing <year>' to index it, "
            f"then 'paperflow download {pid}' to stage its source."
        ) from exc

    try:
        paper_md = backend.get_paper_md(pid)
    except MissingPaperMdError as exc:
        raise ReviewError(
            f"Paper '{pid}' has no converted markdown. "
            f"Run 'paperflow convert {pid}' first."
        ) from exc

    return meta, paper_md


# -- Step functions ----------------------------------------------------------


async def _step0_read(state: PipelineState, ctx: StepContext) -> None:
    """Pure Python: chunk the paper source and extract citations."""
    assert state.paper_source is not None
    state.chunks = chunk_paper(state.paper_source)
    state.citations = extract_citations(state.paper_source)


async def _step1_claims(state: PipelineState, ctx: StepContext) -> None:
    """Parallel LLM: extract claims from each chunk, then promote."""
    assert state.chunks is not None
    prompt_body = ctx.sections.get("Step 1 — Extract Claims", "")
    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])

    async def _extract_chunk(chunk_text: str, line_offset: int):
        agent: Agent[None, ExtractClaimsOutput] = Agent(
            model=model,
            output_type=ExtractClaimsOutput,
            system_prompt=system,
            retries=5,
        )
        user_msg = (
            f"## Chunk (line offset {line_offset})\n\n{chunk_text}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        result = await agent.run(
            user_msg, usage_limits=UsageLimits(request_limit=500)
        )
        return result

    tasks = [
        _extract_chunk(c.text, c.line_offset) for c in state.chunks
    ]
    results = await asyncio.gather(*tasks)

    if ctx.debug and ctx.debug_log is not None:
        for i, r in enumerate(results):
            ctx.debug_log.append(_render_debug_md(r, f"Step 1 — Extract Claims (chunk {i})"))

    all_raws = []
    for r in results:
        all_raws.extend(r.output.claims)

    state.raw_claims = all_raws
    state.claims = promote_claims(all_raws, state.paper_source)


async def _step2_evidence(state: PipelineState, ctx: StepContext) -> None:
    """Parallel LLM: extract evidence from each chunk, then promote."""
    assert state.chunks is not None
    prompt_body = ctx.sections.get("Step 2 — Extract Evidence", "")
    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])

    async def _extract_chunk(chunk_text: str, line_offset: int):
        agent: Agent[None, ExtractEvidenceOutput] = Agent(
            model=model,
            output_type=ExtractEvidenceOutput,
            system_prompt=system,
            retries=5,
        )
        user_msg = (
            f"## Chunk (line offset {line_offset})\n\n{chunk_text}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        result = await agent.run(
            user_msg, usage_limits=UsageLimits(request_limit=500)
        )
        return result

    tasks = [
        _extract_chunk(c.text, c.line_offset) for c in state.chunks
    ]
    results = await asyncio.gather(*tasks)

    if ctx.debug and ctx.debug_log is not None:
        for i, r in enumerate(results):
            ctx.debug_log.append(_render_debug_md(r, f"Step 2 — Extract Evidence (chunk {i})"))

    all_raws = []
    for r in results:
        all_raws.extend(r.output.evidence)

    state.raw_evidence = all_raws
    state.evidence = promote_evidence(all_raws, state.paper_source)


async def _step3_dedup_claims(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1 + one LLM call for tier 2 semantic grouping."""
    assert state.claims is not None

    claims = dedup_tier0(state.claims)
    claims = dedup_tier1(claims)

    survivors = [c for c in claims if c.merged_into is None]
    if len(survivors) <= 1:
        state.claims = claims
        return

    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])
    prompt_body = ctx.sections.get("Step 3 — Dedup Claims", "")

    survivor_questions = json.dumps(
        [{"idx": i, "question": s.question} for i, s in enumerate(survivors)],
        ensure_ascii=False,
    )
    user_msg = (
        f"## Survivors\n\n{survivor_questions}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )

    agent: Agent[None, DedupGroupingOutput] = Agent(
        model=model, output_type=DedupGroupingOutput, system_prompt=system, retries=3,
    )
    result = await agent.run(user_msg, usage_limits=UsageLimits(request_limit=50))
    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(_render_debug_md(result, "Step 3 — Dedup Claims"))
    grouping = result.output

    for group in grouping.groups:
        if len(group) < 2:
            continue
        longest_idx = max(group, key=lambda i: len(survivors[i].text))
        for i in group:
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


async def _step4_dedup_evidence(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1 + one LLM call for tier 2 semantic grouping."""
    assert state.evidence is not None

    evidence = dedup_tier0(state.evidence)
    evidence = dedup_tier1(evidence)

    survivors = [e for e in evidence if e.merged_into is None]
    if len(survivors) <= 1:
        state.evidence = evidence
        return

    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])
    prompt_body = ctx.sections.get("Step 4 — Dedup Evidence", "")

    survivor_supports = json.dumps(
        [{"idx": i, "supports": s.supports} for i, s in enumerate(survivors)],
        ensure_ascii=False,
    )
    user_msg = (
        f"## Survivors\n\n{survivor_supports}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )

    agent: Agent[None, DedupGroupingOutput] = Agent(
        model=model, output_type=DedupGroupingOutput, system_prompt=system, retries=3,
    )
    result = await agent.run(user_msg, usage_limits=UsageLimits(request_limit=50))
    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(_render_debug_md(result, "Step 4 — Dedup Evidence"))
    grouping = result.output

    for group in grouping.groups:
        if len(group) < 2:
            continue
        lowest_idx = min(group, key=lambda i: (survivors[i].loc.line, survivors[i].loc.start_char))
        for i in group:
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


async def _step5_verify(state: PipelineState, ctx: StepContext) -> None:
    """LLM: verify merges, resolve cross-chunk deps, map support, find contradictions."""
    assert state.claims is not None and state.evidence is not None

    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])
    prompt_body = ctx.sections.get("Step 5 — Verify + Deps + Map + Contradict", "")

    claims_json = json.dumps(
        [c.model_dump() for c in state.claims if c.merged_into is None],
        ensure_ascii=False, default=str,
    )
    evidence_json = json.dumps(
        [e.model_dump() for e in state.evidence if e.merged_into is None],
        ensure_ascii=False, default=str,
    )
    user_msg = (
        f"## Claims\n\n{claims_json}\n\n"
        f"## Evidence\n\n{evidence_json}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )

    agent: Agent[None, VerifyOutput] = Agent(
        model=model, output_type=VerifyOutput, system_prompt=system, retries=3,
    )
    result = await agent.run(user_msg, usage_limits=UsageLimits(request_limit=500))
    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(_render_debug_md(result, "Step 5 — Verify + Deps + Map + Contradict"))
    output = result.output

    state.support_map = output.support_map
    state.internal_contradictions = output.internal_contradictions


async def _step6_load_bearing(state: PipelineState, ctx: StepContext) -> None:
    """LLM: graph analysis to identify load-bearing claims."""
    assert state.claims is not None and state.support_map is not None

    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])
    prompt_body = ctx.sections.get("Step 6 — Load-Bearing", "")

    claims_json = json.dumps(
        [c.model_dump() for c in state.claims if c.merged_into is None],
        ensure_ascii=False, default=str,
    )
    support_json = json.dumps(
        [s.model_dump() for s in state.support_map],
        ensure_ascii=False, default=str,
    )
    contradictions_json = json.dumps(
        [ic.model_dump() for ic in (state.internal_contradictions or [])],
        ensure_ascii=False, default=str,
    )
    user_msg = (
        f"## Claims\n\n{claims_json}\n\n"
        f"## Support Map\n\n{support_json}\n\n"
        f"## Internal Contradictions\n\n{contradictions_json}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )

    agent: Agent[None, LoadBearingOutput] = Agent(
        model=model, output_type=LoadBearingOutput, system_prompt=system, retries=3,
    )
    result = await agent.run(user_msg, usage_limits=UsageLimits(request_limit=500))
    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(_render_debug_md(result, "Step 6 — Load-Bearing"))
    state.load_bearing_claims = result.output.results


def _has_triggered_claims(state: PipelineState) -> bool:
    """Check if any load-bearing claims trigger web search."""
    if not state.load_bearing_claims:
        return False
    return any(
        lb.classification == "critical_gap" for lb in state.load_bearing_claims
    )


async def _step7_web_search(state: PipelineState, ctx: StepContext) -> None:
    """LLM + web tools: search for evidence on triggered claims."""
    if not _has_triggered_claims(state):
        return

    assert state.claims is not None and state.load_bearing_claims is not None

    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])
    prompt_body = ctx.sections.get("Step 7 — Web Search", "")

    triggered = [
        lb for lb in state.load_bearing_claims
        if lb.classification == "critical_gap"
    ]
    claims_for_search = []
    for lb in triggered:
        claim = next(
            (c for c in state.claims if c.loc == lb.claim_loc and c.merged_into is None),
            None,
        )
        if claim:
            claims_for_search.append(claim.model_dump())

    user_msg = (
        f"## Triggered Claims\n\n{json.dumps(claims_for_search, ensure_ascii=False, default=str)}\n\n"
        f"## Paper Citations\n\n{json.dumps([c.model_dump() for c in (state.citations or [])], ensure_ascii=False)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )

    agent: Agent[None, WebSearchOutput] = Agent(
        model=model, output_type=WebSearchOutput, system_prompt=system, retries=3,
    )

    def _wrap_tool(fn, name):
        """Wrap a tool function to log calls to stderr when debugging."""
        if not ctx.debug:
            return fn
        import sys
        import functools

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            args_str = ", ".join(
                [repr(a) for a in args] +
                [f"{k}={repr(v)}" for k, v in kwargs.items()]
            )
            print(f"[tool] {name}({args_str})", file=sys.stderr, flush=True)
            return await fn(*args, **kwargs)
        return wrapper

    if ctx.backend is not None:
        from paperstore.tools import PaperstoreTools
        ps_tools = PaperstoreTools(ctx.backend)
        agent.tool_plain(_wrap_tool(ps_tools.paper_meta, "paper_meta"))
        agent.tool_plain(_wrap_tool(ps_tools.paper_meta_latest, "paper_meta_latest"))
        agent.tool_plain(_wrap_tool(ps_tools.read_file, "read_file"))

    if ctx.researcher is not None:
        agent.tool_plain(_wrap_tool(ctx.researcher.web_search, "web_search"))
        agent.tool_plain(_wrap_tool(ctx.researcher.web_fetch, "web_fetch"))

    result = await agent.run(user_msg, usage_limits=UsageLimits(request_limit=500))
    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(_render_debug_md(result, "Step 7 — Web Search"))
    state.external_evidence = result.output.external_evidence


async def _step8_resolve(state: PipelineState, ctx: StepContext) -> None:
    """LLM: resolve external evidence into classifications."""
    if not state.external_evidence:
        return

    assert state.load_bearing_claims is not None and state.claims is not None

    system = ctx.sections.get("System Prompt", "")
    model = ctx.model_slots.get("default", _DEFAULT_MODEL_SLOTS["default"])
    prompt_body = ctx.sections.get("Step 8 — Resolve External", "")

    user_msg = (
        f"## Load-Bearing Claims\n\n"
        f"{json.dumps([lb.model_dump() for lb in state.load_bearing_claims], ensure_ascii=False, default=str)}\n\n"
        f"## External Evidence\n\n"
        f"{json.dumps([ee.model_dump() for ee in state.external_evidence], ensure_ascii=False, default=str)}\n\n"
        f"## Claims\n\n"
        f"{json.dumps([c.model_dump() for c in state.claims if c.merged_into is None], ensure_ascii=False, default=str)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )

    agent: Agent[None, ResolveOutput] = Agent(
        model=model, output_type=ResolveOutput, system_prompt=system, retries=3,
    )
    result = await agent.run(user_msg, usage_limits=UsageLimits(request_limit=500))
    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(_render_debug_md(result, "Step 8 — Resolve External"))
    state.load_bearing_claims = result.output.load_bearing_claims
    state.web_resolutions = result.output.web_resolutions


async def _step9_report(state: PipelineState, ctx: StepContext) -> None:
    """Pure Python: render the final report as two bulleted question lists."""
    assert state.claims is not None

    lb_claims = state.load_bearing_claims or []
    claims = state.claims

    critical_gaps: list[LoadBearingResult] = sorted(
        [lb for lb in lb_claims if lb.classification == "critical_gap"],
        key=lambda lb: (lb.claim_loc.line, lb.claim_loc.start_char),
    )

    peripheral_unsupported: list[LoadBearingResult] = []
    if state.support_map:
        unsupported_locs = {
            s.claim_loc for s in state.support_map if s.status == "unsupported"
        }
        peripheral_unsupported = sorted(
            [
                lb for lb in lb_claims
                if lb.classification == "peripheral" and lb.claim_loc in unsupported_locs
            ],
            key=lambda lb: (lb.claim_loc.line, lb.claim_loc.start_char),
        )

    def _question_for_loc(loc: Any) -> str | None:
        for c in claims:
            if c.loc == loc and c.merged_into is None:
                return c.question
        return None

    lines: list[str] = []
    lines.append("### Unsupported Load-Bearing Claims\n")
    if not critical_gaps:
        lines.append("No questions.\n")
    else:
        for lb in critical_gaps:
            q = _question_for_loc(lb.claim_loc)
            if q:
                lines.append(f"- {q}")
        lines.append("")

    lines.append("### Unsupported Peripheral Claims\n")
    if not peripheral_unsupported:
        lines.append("No questions.\n")
    else:
        for lb in peripheral_unsupported:
            q = _question_for_loc(lb.claim_loc)
            if q:
                lines.append(f"- {q}")
        lines.append("")

    state.report = "\n".join(lines)


# -- Step registry -----------------------------------------------------------

_STEPS: list[tuple[str, StepFn]] = [  # noqa: RUF012
    ("Step 0 — Read", _step0_read),
    ("Step 1 — Extract Claims", _step1_claims),
    ("Step 2 — Extract Evidence", _step2_evidence),
    ("Step 3 — Dedup Claims", _step3_dedup_claims),
    ("Step 4 — Dedup Evidence", _step4_dedup_evidence),
    ("Step 5 — Verify + Deps", _step5_verify),
    ("Step 6 — Load-Bearing", _step6_load_bearing),
    ("Step 7 — Web Search", _step7_web_search),
    ("Step 8 — Resolve External", _step8_resolve),
    ("Step 9 — Report", _step9_report),
]


# -- Public API --------------------------------------------------------------


async def review_paper(
    pid: str,
    backend: StorageBackend,
    *,
    model_slots: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    debug: bool = False,
) -> str:
    """Extract structural questions from a WG21 paper.

    Loads the paper from paperstore via ``pid``, runs the multi-step
    extractor pipeline, and returns the final report string (two
    bulleted question lists).

    Pass ``on_progress`` to receive :class:`~paperstore.progress.ProgressEvent`
    notifications at each step transition. The CLI uses this to drive a
    rich progress bar; other callers may pass ``None``.

    Pass ``debug=True`` to write a single markdown transcript of every
    LLM interaction to paperstore as ``<pid>.debug.md``.

    Raises :class:`ReviewError` if the paper is not found or has no
    converted markdown.
    """
    from web_tools import WebResearcher

    slots = {**_DEFAULT_MODEL_SLOTS, **(model_slots or {})}
    secs = load_sections()

    if "System Prompt" not in secs:
        raise ReviewError(
            "'System Prompt' section not found in extractor.md. "
            f"Available sections: {sorted(secs)}"
        )

    _meta, paper_md = _load_paper(pid, backend)
    backend.clear_review(pid)

    state = PipelineState(paper_source=paper_md)

    async with WebResearcher() as researcher:
        ctx = StepContext(
            sections=secs,
            model_slots=slots,
            researcher=researcher,
            backend=backend,
            on_progress=on_progress,
            debug=debug,
            pid=pid,
        )

        debug_log: list[str] = []
        debug_path = backend.get_paper_md_path(pid).with_suffix(".debug.md")
        if debug:
            debug_path.unlink(missing_ok=True)
        total = len(_STEPS)
        try:
            for i, (name, step_fn) in enumerate(_STEPS):
                if stop_after is not None and i > stop_after:
                    break

                if on_progress is not None:
                    on_progress(ProgressEvent(
                        step=i, total=total, name=name, pct=i / total,
                    ))

                logger.info("Step %d: %s", i, name)
                try:
                    await step_fn(state, ctx)
                except Exception as exc:
                    logger.error(
                        "Step %d (%s) failed: %s", i, name, exc, exc_info=True
                    )
                    raise
                finally:
                    if debug and ctx.debug_log:
                        debug_path.write_text(
                            "\n\n---\n\n".join(ctx.debug_log), encoding="utf-8"
                        )
        finally:
            if debug and ctx.debug_log:
                debug_path.write_text(
                    "\n\n---\n\n".join(ctx.debug_log), encoding="utf-8"
                )

    return state.report or ""
