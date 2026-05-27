#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Assay pipeline orchestration.

18-step two-pass structural analysis. All LLM calls are sequential
``agent.run()`` inside custom hooks. All LLM-facing text comes from
``assay.md`` at runtime via ``ctx.sections``. Zero prompt strings in
this file.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter

from paperstore import StorageBackend

from paperstore.progress import ProgressCallback

from pipeline import (
    AgentBackend,
    PipelinePrompt,
    StepContext,
    StepHooks,
    build_pipeline,
    dispatch,
    resolve_pipeline_models,
    validate_capabilities,
)
from pipeline.services import load_embedders, load_services

from assay.harness import (
    collect,
    cross_examine,
    dedupe_findings,
    synthesize,
    upgrade_gaps,
)
from assay.locs import format_numbered_lines
from assay.models import (
    BatchClassifyOutput,
    GapOutput,
    ChunkAnalyzeOutput,
    ChunkDecideOutput,
    ChunkEntry,
    ChunkExtractOutput,
    ClaimDecision,
    CollectedItem,
    CollectedItems,
    CoupleOutput,
    CrossChunkDecideOutput,
    CrossExamBatchOutput,
    CrossExamVerdict,
    DeriveOutput,
    FindingOutput,
    PipelineState,
    ProbeResult,
    RationaleOutput,
    ResearchLensOutput,
    StrengthOutput,
    SynthesisOutput,
    VerifyOutput,
)
from assay.references import extract_references, extract_urls, verify_references
from assay.blanking import blank_paper
from assay.chunker import chunk_paper
from assay.rag import (
    build_cited_paper_index, build_single_paper_index,
    query_index,
    query_for_research, query_for_challenge, IndexStats,
)
from assay.triage import should_analyze
from pipeline import tokens_to_chars
from assay.render import render_report, render_trace

logger = logging.getLogger(__name__)

# -- Step name constants (must match assay.md headers) -----------------------

_STEP_0_RECEIVE = "0. Receive"
_STEP_1_REFERENCES = "1. References"
_STEP_2_INDEX = "2. Index"
_STEP_3_SURVEY = "3. Survey"
_STEP_4_EXTRACT = "4. Extract"
_STEP_5_DECIDE = "5. Decide"
_STEP_6_CLASSIFY = "6. Classify"
_STEP_7_COLLECT = "7. Collect"
_STEP_8_DERIVE = "8. Derive"
_STEP_9_VERIFY = "9. Verify"
_STEP_10_RESEARCH = "10. Research"
_STEP_11_PROBE = "11. Probe"
_STEP_12_ANALYZE = "12. Analyze"
_STEP_13_RATIONALE = "13. Rationale"
_STEP_14_CHALLENGE = "14. Challenge"
_STEP_15_COUPLE = "15. Couple"
_STEP_16_SYNTHESIZE = "16. Synthesize"
_STEP_17_REPORT = "17. Report"


# -- Prompt helpers ----------------------------------------------------------


def _prompt_for(ctx: StepContext, step_name: str) -> str:
    """Read the prompt text from assay.md for a given step.

    Metadata bullets (``- **Model:** ...``) and the per-step
    ``### System Prompt`` block are stripped so the returned string is
    pure instruction text suitable for the user message.
    """
    return ctx.prompt.step_section(step_name).strip()


# -- Tool factories ----------------------------------------------------------


def _make_explore_paper_tool(
    index, paper_id: str, paper_lines: list[str], embedder, ctx: StepContext,
):
    """Create an explore tool scoped to one paper's RAG index.

    The closure captures the pre-built index and source lines.
    Each call runs query_index, slices the original lines for the
    top hits, formats with line numbers, and wraps via inject_untrusted.
    """
    def _explore(query: str) -> str:
        hits = query_index(index, query, embedder, top_k=5, max_per_paper=5)
        if not hits:
            return "No relevant passages found."
        parts = []
        for hit in hits:
            header = f"### {hit.heading} (lines {hit.start_line}-{hit.end_line}, score {hit.score:.2f})\n"
            numbered = format_numbered_lines(paper_lines, hit.start_line, hit.end_line)
            parts.append(f"{header}\n{ctx.inject_untrusted(numbered)}\n")
        return "\n".join(parts)

    _explore.__name__ = f"explore_{paper_id.lower()}"
    _explore.__doc__ = (
        f"Search paper {paper_id} by semantic similarity. "
        "Pass a natural-language query describing what to look for."
    )
    return _explore


# -- User message builders ---------------------------------------------------



def _build_extract_user_message(
    pid: str, chunk: ChunkEntry, paper_lines: list[str], ctx: StepContext,
) -> str:
    numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)
    parts = [
        f"# Paper: {pid}\n\n",
        f"## {chunk.heading} (chunk {chunk.index}, lines {chunk.start_line}-{chunk.end_line})\n\n",
        f"{ctx.inject_untrusted(numbered)}\n",
    ]
    return "".join(parts)



def _build_derive_user_message(state: PipelineState) -> str:
    items = state.items or CollectedItems()
    claims = items.claims
    evidence = items.evidence
    asks = state.asks or []

    parts = [f"# Paper: {state.paper_id}\n"]
    parts.append("## Claims\n")
    for c in claims:
        parts.append(f"- [{c.id}] \"{c.quote}\" (line {c.line})\n")
    parts.append("\n## Evidence\n")
    for e in evidence:
        parts.append(f"- [{e.id}] (tier: {e.quality_tier or ''}) \"{e.quote}\" (line {e.line})\n")
    if asks:
        parts.append("\n## Asks\n")
        for a in asks:
            parts.append(f"- [{a.id}] \"{a.quote}\" (line {a.line})\n")
    return "".join(parts)


def _build_research_user_message(lens: str, state: PipelineState) -> str:
    derive = state.derive
    return (
        f"# Research for lens: {lens}\n\n"
        f"Paper: {state.paper_id.upper()} - \"{state.paper_title}\"\n"
        f"Authors: {', '.join(state.authors)}\n"
        f"Thesis: {derive.central_claim if derive else ''}\n"
        f"Scope: {derive.scope_boundary if derive else ''}\n"
    )


def _build_analyze_user_message(
    pid: str, chunk: ChunkEntry, paper_lines: list[str], state: PipelineState,
    ctx: StepContext,
) -> str:
    derive = state.derive
    numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)

    own_gaps: list[GapOutput] = []
    other_gaps: list[GapOutput] = []
    seen: set[int] = set()
    for _lens, bcs in (state.gaps_by_lens or {}).items():
        for b in bcs:
            if b.id in seen or b.closed_by:
                continue
            seen.add(b.id)
            if b.chunk_index == chunk.index:
                own_gaps.append(b)
            else:
                other_gaps.append(b)

    parts = [
        f"# Paper: {pid}\n\n",
        f"## Thesis\n\nCentral claim: {derive.central_claim if derive else ''}\n",
        f"Problem: {derive.problem_statement if derive else ''}\n",
        f"Scope: {derive.scope_boundary if derive else ''}\n",
        f"Ask calibration: {derive.ask_calibration if derive else ''}\n\n",
        "## Load-bearing claims\n\n",
    ]
    for lb in (derive.load_bearing_claims if derive else []):
        parts.append(f"- [{lb.id}] \"{lb.quote}\"\n")

    if own_gaps:
        parts.append("\n## Gaps already raised on THIS chunk (use as inputs; do not duplicate)\n\n")
        for b in own_gaps:
            parts.append(f"- [{b.id}] [{b.severity}] {b.gap} (line {b.line})\n")
    if other_gaps:
        parts.append("\n## Cross-chunk gaps (for context only)\n\n")
        for b in other_gaps[:20]:
            parts.append(f"- [{b.id}] [{b.severity}] {b.gap} (line {b.line})\n")

    if state.verify and state.items is not None:
        closed_evidence = [
            e for e in state.items.evidence if getattr(e, "source_pid", "")
        ][:10]
        if closed_evidence:
            parts.append("\n## Companion paper evidence (already resolved by Verify)\n\n")
            for e in closed_evidence:
                parts.append(
                    f"- [{e.id}] ({e.source_pid}) \"{e.quote}\" (line {e.line})\n"
                )

    if state.verify and state.verify.contradictions:
        parts.append("\n## Contradictions found by Verify\n\n")
        for c in state.verify.contradictions:
            suffix = f" (claim [{c.claim_id}])" if c.claim_id else ""
            parts.append(
                f"- {c.source_pid} line {c.line}: \"{c.quote}\" refutes: {c.refutes}{suffix}\n"
            )

    if state.research:
        parts.append("\n## Research context\n\n")
        for lens_name, r in sorted(state.research.items()):
            if r.findings:
                parts.append(f"### {lens_name}\n")
                for rf in r.findings[:3]:
                    parts.append(f"- {rf.finding} (source: {rf.source})\n")

    parts.append(f"\n## Chunk: {chunk.heading} (lines {chunk.start_line}-{chunk.end_line})\n\n")
    parts.append(f"{ctx.inject_untrusted(numbered)}\n")

    return "".join(parts)


def _build_rationale_user_message(state: PipelineState) -> str:
    items = state.items or CollectedItems()
    claims = items.claims
    evidence = items.evidence
    derive = state.derive

    parts = [f"# Paper: {state.paper_id}\n\n"]
    parts.append(f"## Thesis: {derive.central_claim if derive else ''}\n")
    parts.append(f"Ask calibration: {derive.ask_calibration if derive else ''}\n\n")
    parts.append(f"## Claims ({len(claims)})\n\n")
    for c in claims[:30]:
        parts.append(f"- [{c.id}] \"{c.quote}\"\n")
    parts.append(f"\n## Evidence ({len(evidence)})\n\n")
    for e in evidence[:30]:
        parts.append(f"- [{e.id}] ({e.quality_tier or ''}) \"{e.quote}\"\n")
    return "".join(parts)


def _build_cross_exam_user_message(
    findings_batch: list[FindingOutput], state: PipelineState, ctx: StepContext
) -> str:
    """Build user message for one cross-examination batch."""
    derive = state.derive
    items = state.items or CollectedItems()
    concessions = items.concessions
    scope_items = items.scope
    paper_lines = state.paper_md.splitlines()

    chunk_by_line: dict[int, ChunkEntry] = {}
    for ch in (state.chunk_map or []):
        for ln in range(ch.start_line, ch.end_line + 1):
            chunk_by_line[ln] = ch

    parts = [
        f"# Paper: {state.paper_id}\n\n",
        f"## Thesis: {derive.central_claim if derive else ''}\n",
        f"Scope: {derive.scope_boundary if derive else ''}\n\n",
    ]

    if concessions:
        parts.append("## Concessions\n\n")
        for c in concessions:
            parts.append(f"- [{c.id}] \"{c.quote}\" (line {c.line})\n")
        parts.append("\n")

    if scope_items:
        parts.append("## Scope statements (function as concessions)\n\n")
        for s in scope_items:
            parts.append(f"- \"{s.quote}\" (line {s.line})\n")
        parts.append("\n")

    if state.verify and state.verify.closes:
        parts.append("## Already resolved by Verify (do not re-raise)\n\n")
        for r in state.verify.closes:
            parts.append(
                f"- gap [{r.gap_id}]: \"{r.evidence_quote}\" (line {r.evidence_line})\n"
            )
        parts.append("\n")
    if state.verify and state.verify.contradictions:
        parts.append("## Companion-paper contradictions (Resolution may rely on these)\n\n")
        for c in state.verify.contradictions:
            parts.append(
                f"- {c.source_pid} line {c.line}: \"{c.quote}\" refutes: {c.refutes}\n"
            )
        parts.append("\n")

    parts.append("## Findings to cross-examine\n\n")
    for f in findings_batch:
        parts.append(f"### [{f.id}] {f.title}\n\n")
        parts.append(f"**Severity:** {f.severity}\n")
        parts.append(f"**Lens:** {f.lens}\n")
        if f.quote:
            parts.append(f"**Quote:** \"{f.quote}\" (line {f.line})\n")
        parts.append(f"**Explanation:** {f.explanation}\n")
        if f.damage:
            parts.append(f"**Damage:** {f.damage}\n")

        ch = chunk_by_line.get(f.line) if f.line > 0 else None
        if ch is not None:
            context = format_numbered_lines(paper_lines, ch.start_line, ch.end_line)
            parts.append(
                f"\n**Containing chunk: {ch.heading} (lines {ch.start_line}-{ch.end_line}):**"
                f"\n\n{ctx.inject_untrusted(context)}\n"
            )
        elif f.line > 0 and paper_lines:
            start = max(0, f.line - 16)
            end = min(len(paper_lines), f.line + 15)
            context = format_numbered_lines(paper_lines, start + 1, end)
            parts.append(
                f"\n**Paper context (lines {start + 1}-{end}):**\n\n"
                f"{ctx.inject_untrusted(context)}\n"
            )

        parts.append("\n")

    return "".join(parts)


def _build_couple_user_message(state: PipelineState) -> str:
    surviving = state.surviving or []
    by_lens: dict[str, list[FindingOutput]] = {}
    for f in surviving:
        by_lens.setdefault(f.lens or "Other", []).append(f)

    parts = ["# Surviving findings by lens\n\n"]
    for lens in sorted(by_lens.keys()):
        parts.append(f"## {lens}\n\n")
        for f in by_lens[lens]:
            parts.append(f"- [{f.id}] **{f.title}** ({f.severity}): {f.explanation[:200]}\n")
        parts.append("\n")
    return "".join(parts)


# -- Custom hooks ------------------------------------------------------------

# Step 0
async def _custom_receive(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 0: validate paper exists, load source, blank."""
    assert ctx.backend is not None
    pid = ctx.pid

    meta = await asyncio.to_thread(ctx.backend.get_meta, pid)
    paper_md = await asyncio.to_thread(ctx.backend.get_paper_md, pid)

    paper_md = blank_paper(paper_md, paper_id=pid)

    state.paper_id = pid
    state.paper_md = paper_md
    state.paper_title = meta.title or ""
    state.paper_date = meta.document_date or ""
    state.audience = [meta.target_group] if meta.target_group else []
    state.authors = meta.authors or []
    state.intent = meta.intent or ""

# Step 1
async def _custom_references(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 1: mechanical reference extraction."""
    refs = extract_references(state.paper_md)
    refs = verify_references(refs, ctx.backend, state.authors)
    state.ref_pids = refs
    state.ref_urls = extract_urls(state.paper_md)

# Step2
async def _custom_index(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 2: build ephemeral RAG index over cited papers."""
    import time
    if not state.ref_pids:
        return
    if ctx.embedder is None:
        return
    t0 = time.perf_counter()
    state.cited_paper_index = build_cited_paper_index(
        state.ref_pids,
        ctx.backend,
        ctx.embedder,
        paper_id=state.paper_id,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    idx = state.cited_paper_index
    if idx is not None:
        by_paper = Counter(c.paper_id for c in idx.chunks)
        state.index_stats = IndexStats(
            papers_indexed=len(by_paper),
            total_chunks=len(idx.chunks),
            embedding_dim=idx.embeddings.shape[1],
            embed_time_ms=elapsed_ms,
            per_paper=[(pid, next(c.relationship for c in idx.chunks if c.paper_id == pid), n)
                       for pid, n in by_paper.most_common()],
            skipped=[r.paper_id for r in state.ref_pids
                     if not r.in_paperstore or r.paper_id.upper() == state.paper_id.upper()],
        )


async def _custom_survey(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 3: chunk paper, wording signal, triage."""
    # Survey is pure-Python but uses the same tokenizer profile as
    # Extract / Scan to size chunks consistently. Grab the 'fast' agent
    # if the pipeline declares one; otherwise fall back to 'default'.
    agent = ctx.agents.get("fast") or ctx.agents.get("default")
    chunk_tokens = spec.step.chunk_tokens or 2000
    sections = chunk_paper(
        state.paper_md,
        max_chars=tokens_to_chars(chunk_tokens, agent=agent),
    )
    state.chunk_map = [
        ChunkEntry(index=i, heading=s.heading, start_line=s.start_line,
                   end_line=s.end_line, char_count=s.char_count)
        for i, s in enumerate(sections)
    ]

    _WORDING_HEADING_RE = re.compile(
        r"(?i)\bwording\b|\bproposed\s+changes\b|\bproposed\s+resolution\b"
    )
    wording_headings = [s for s in sections if _WORDING_HEADING_RE.search(s.heading)]
    state.wording_lines = sum(s.end_line - s.start_line for s in wording_headings)
    audience = " ".join(state.audience).upper()
    state.targets_cwg_lwg = "CWG" in audience or "LWG" in audience

    triage = should_analyze(
        state.chunk_map, state.paper_title, state.intent, state.audience, state.paper_md
    )
    if not triage.analyze:
        state.synthesis = SynthesisOutput(
            verdict_label="Skipped",
            verdict_confidence="High",
            verdict_statement=f"{triage.paper_type.replace('_', ' ').title()}: not analyzed.",
            dominant_dynamic=None,
            thesis_survives=False,
            thesis_statement="",
            skip_reason=triage.reason,
            paper_stats=triage.stats,
        )
        state.items = CollectedItems()
        state.findings = []
        state.surviving = []
        state.killed = []
        state.compounds = []
        state.strengths = []


async def _custom_extract(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 4: per-chunk extraction (Pass 1)."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget
    paper_lines = state.paper_md.splitlines()

    system_prompt = _prompt_for(ctx, _STEP_4_EXTRACT)
    chunks = state.chunk_map or []

    async def _extract_one(ci: int, chunk: ChunkEntry):
        local_log: list[str] | None = [] if ctx.debug else None
        user_msg = _build_extract_user_message(
            state.paper_id, chunk, paper_lines, ctx,
        )
        result = await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=ChunkExtractOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            label=f"extract-chunk-{chunk.index}",
            debug_log=local_log,
        )
        return ci, result, local_log

    concurrency = spec.step.concurrency or ctx.default_concurrency
    results = await ctx.gather_concurrent(
        [_extract_one(ci, chunk) for ci, chunk in enumerate(chunks)],
        concurrency=concurrency,
        label="extract",
    )
    state.raw_extractions = [r for _, r, _ in results]
    if ctx.debug and ctx.debug_log is not None:
        for _, _, local_log in results:
            if local_log:
                ctx.debug_log.extend(local_log)

    # Pre-assign global claim IDs so the cross-chunk Decide follow-up can
    # echo them back unchanged. Local ID is the claim's position within
    # its chunk's items.
    claim_map: dict[tuple[int, int], int] = {}
    gid = state._next_id
    for ext in state.raw_extractions:
        local_id = 0
        for it in ext.items:
            if it.type == "claim":
                claim_map[(ext.chunk_index, local_id)] = gid
                gid += 1
                local_id += 1
    state.claim_global_id_map = claim_map
    state._next_id = gid


async def _custom_decide(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 5: per-chunk support judgment on Extract's claims."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget
    system_prompt = _prompt_for(ctx, _STEP_5_DECIDE)
    chunks = state.chunk_map or []
    paper_lines = state.paper_md.splitlines()

    extractions = state.raw_extractions or []
    ext_by_chunk: dict[int, list] = {}
    for ext in extractions:
        claims = [it for it in ext.items if it.type == "claim"]
        if claims:
            ext_by_chunk[ext.chunk_index] = claims

    async def _decide_one(ci: int, chunk: ChunkEntry):
        local_log: list[str] | None = [] if ctx.debug else None
        numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)
        claims = ext_by_chunk.get(chunk.index, [])
        claims_block = "\n".join(
            f"- [{i}] (line {c.line}) {c.quote}"
            for i, c in enumerate(claims)
        )
        user_msg = (
            f"# Paper: {state.paper_id}\n\n"
            f"## {chunk.heading} (chunk {chunk.index}, lines {chunk.start_line}-{chunk.end_line})\n\n"
            f"{ctx.inject_untrusted(numbered)}\n\n"
            f"## Claims to judge\n\n{claims_block}\n"
        )
        result = await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=ChunkDecideOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            label=f"decide-chunk-{chunk.index}",
            debug_log=local_log,
        )
        return ci, result, local_log

    concurrency = spec.step.concurrency or ctx.default_concurrency
    results = await ctx.gather_concurrent(
        [_decide_one(ci, chunk) for ci, chunk in enumerate(chunks)],
        concurrency=concurrency,
        label="decide",
    )
    state.raw_decisions = [r for _, r, _ in results]
    if ctx.debug and ctx.debug_log is not None:
        for _, _, local_log in results:
            if local_log:
                ctx.debug_log.extend(local_log)

    await _cross_chunk_decide(state, ctx, agent, max_output, thinking)


async def _cross_chunk_decide(
    state: PipelineState,
    ctx: StepContext,
    agent: AgentBackend,
    max_output: int,
    thinking: int | None,
) -> None:
    """Cross-chunk Decide follow-up: re-judge per-chunk-unsupported claims.

    A claim in chunk 0 may be supported by evidence in chunk 2. The
    per-chunk Decide pass cannot see that. This second pass shows the
    model every unsupported claim against all paper-wide evidence,
    section-anchored, with stable global IDs echoed verbatim in both
    directions for reconciliation.
    """
    extractions = state.raw_extractions or []
    decisions = state.raw_decisions or []
    if not extractions or not decisions:
        return

    section_by_line: dict[int, str] = {}
    for ch in (state.chunk_map or []):
        for ln in range(ch.start_line, ch.end_line + 1):
            section_by_line[ln] = ch.heading

    ext_by_chunk: dict[int, list] = {}
    for ext in extractions:
        ext_by_chunk[ext.chunk_index] = [it for it in ext.items if it.type == "claim"]

    unsupported: list[tuple[int, object, str]] = []
    for dec in decisions:
        chunk_claims = ext_by_chunk.get(dec.chunk_index, [])
        for d in dec.decisions:
            if d.supported or not (0 <= d.claim_id < len(chunk_claims)):
                continue
            gid = state.claim_global_id_map.get((dec.chunk_index, d.claim_id))
            if gid is not None:
                unsupported.append((gid, chunk_claims[d.claim_id], d.reason))
    if not unsupported:
        return

    all_evidence = [
        it for ext in extractions for it in ext.items if it.type == "evidence"
    ]
    if not all_evidence:
        return

    claims_block = "\n".join(
        f'<claim claim_id="{gid}" line="{c.line}" section="{section_by_line.get(c.line, "?")}">'
        f"{c.quote}</claim> -- chunk-local reason: {reason}"
        for gid, c, reason in unsupported
    )
    evidence_block = "\n".join(
        f'<evidence line="{e.line}" section="{section_by_line.get(e.line, "?")}">{e.quote}</evidence>'
        for e in all_evidence
    )
    user_msg = (
        f"# Paper: {state.paper_id}\n\n"
        f"## Claims judged unsupported by their own chunk\n\n{claims_block}\n\n"
        f"## All evidence across the paper (with section context)\n\n{evidence_block}\n\n"
        "For each <claim>, return one decision object. The decision's `claim_id` field "
        "MUST equal the `claim_id` attribute of the corresponding <claim> verbatim. "
        "Do not skip, merge, or invent claims. List the line numbers of evidence you relied on."
    )

    try:
        result = await agent.run(
            system_prompt=_prompt_for(ctx, _STEP_5_DECIDE),
            user_message=user_msg,
            output_type=CrossChunkDecideOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            label="decide-cross-chunk",
            debug_log=ctx.debug_log if ctx.debug else None,
        )
    except Exception as exc:
        logger.warning("cross-chunk Decide failed: %s", exc)
        return

    input_ids = {gid for gid, _, _ in unsupported}
    returned_ids = {d.claim_id for d in result.decisions}
    missing = input_ids - returned_ids
    hallucinated = returned_ids - input_ids
    if missing or hallucinated:
        logger.warning(
            "cross-chunk Decide reconciliation: %d missing, %d hallucinated",
            len(missing), len(hallucinated),
        )

    flips = {
        d.claim_id: d for d in result.decisions
        if d.supported and d.claim_id in input_ids
    }
    if not flips:
        return

    new_decisions: list[ChunkDecideOutput] = []
    for dec in decisions:
        new_decs: list[ClaimDecision] = []
        for d in dec.decisions:
            gid = state.claim_global_id_map.get((dec.chunk_index, d.claim_id))
            if gid in flips and not d.supported:
                cc = flips[gid]
                d = d.model_copy(update={
                    "supported": True,
                    "reason": (
                        f"cross-chunk via lines {cc.supporting_evidence_lines}: {cc.reason}"
                    ),
                })
            new_decs.append(d)
        new_decisions.append(dec.model_copy(update={"decisions": new_decs}))
    state.raw_decisions = new_decisions


async def _custom_classify(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 6: single-batch gap classification on unsupported claims."""
    extractions = state.raw_extractions or []
    decisions = state.raw_decisions or []

    claims_by_chunk: dict[int, list] = {}
    for ext in extractions:
        claims = [it for it in ext.items if it.type == "claim"]
        if claims:
            claims_by_chunk[ext.chunk_index] = claims

    unsupported: list[dict] = []
    for dec_output in decisions:
        chunk_claims = claims_by_chunk.get(dec_output.chunk_index, [])
        for d in dec_output.decisions:
            if not d.supported and 0 <= d.claim_id < len(chunk_claims):
                c = chunk_claims[d.claim_id]
                unsupported.append({
                    "quote": c.quote,
                    "line": c.line,
                    "chunk_index": dec_output.chunk_index,
                    "reason": d.reason,
                })

    if not unsupported:
        state.raw_classifications = BatchClassifyOutput(gaps=[])
        state.raw_scans = []
        return

    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget
    system_prompt = _prompt_for(ctx, _STEP_6_CLASSIFY)

    unsupported_by_chunk: dict[int, list[dict]] = {}
    for u in unsupported:
        unsupported_by_chunk.setdefault(u["chunk_index"], []).append(u)

    paper_lines = state.paper_md.splitlines()
    chunk_by_index = {c.index: c for c in (state.chunk_map or [])}

    parts: list[str] = [
        f"# Paper: {state.paper_id}\n\n",
        f"## Unsupported claims grouped by chunk ({len(unsupported)} total)\n\n",
    ]
    for ci, items_in in sorted(unsupported_by_chunk.items()):
        chunk = chunk_by_index.get(ci)
        if chunk is None:
            continue
        numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)
        parts.append(
            f"### Chunk {ci}: {chunk.heading} (lines {chunk.start_line}-{chunk.end_line})\n\n"
        )
        parts.append(f"{ctx.inject_untrusted(numbered)}\n\n")
        parts.append("**Unsupported claims in this chunk:**\n\n")
        for u in items_in:
            parts.append(f"- (line {u['line']}) \"{u['quote']}\" - {u['reason']}\n")
        parts.append("\n")
    user_msg = "".join(parts)

    result = await agent.run(
        system_prompt=system_prompt,
        user_message=user_msg,
        output_type=BatchClassifyOutput,
        max_tokens=max_output,
        thinking_budget=thinking,
        label="classify-batch",
        debug_log=ctx.debug_log if ctx.debug else None,
    )
    state.raw_classifications = result
    from assay.models import ScanOutput
    state.raw_scans = [ScanOutput(chunk_index=0, gaps=result.gaps)]


async def _custom_collect(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 7: aggregate and dedup (pure Python)."""
    items, gaps_by_lens, asks, active, inactive, next_id = collect(
        state.raw_extractions or [], state.raw_scans or [],
        start_id=state._next_id,
    )
    state._next_id = next_id
    state.items = items
    state.gaps_by_lens = gaps_by_lens
    state.asks = asks
    state.active_lenses = active
    state.inactive_lenses = inactive


async def _custom_derive(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 8: thesis compression and load-bearing identification."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget

    user_msg = _build_derive_user_message(state)
    result = await agent.run(
        system_prompt=_prompt_for(ctx, _STEP_8_DERIVE),
        user_message=user_msg,
        output_type=DeriveOutput,
        max_tokens=max_output,
        thinking_budget=thinking,
        label="derive",
        debug_log=ctx.debug_log if ctx.debug else None,
    )

    state.derive = result

    state.gaps_by_lens = upgrade_gaps(
        state.gaps_by_lens or {},
        result.central_claim,
        result.problem_statement,
        embedder=ctx.embedder,
    )


_DEFAULT_VERIFY_PROMPT = (
    "You have a tool to search a companion paper by the same author(s). "
    "Use it to verify claims, find supporting evidence, or identify contradictions."
)


def _open_gaps_remain(state: PipelineState) -> bool:
    return any(
        not g.closed_by
        for lens_list in (state.gaps_by_lens or {}).values()
        for g in lens_list
    )


async def _verify_against_one_companion(
    state: PipelineState,
    ctx: StepContext,
    agent: AgentBackend,
    system_prompt: str,
    max_output: int,
    thinking: int | None,
    companion,
    open_gaps: list[GapOutput],
) -> VerifyOutput | None:
    """Run one Verify call against one companion paper."""
    md = ctx.backend.try_read_paper_md(companion.paper_id)
    if not md:
        return None
    index = build_single_paper_index(md, companion.paper_id, ctx.embedder)
    if index is None:
        return None
    paper_lines = md.splitlines()
    tool_fn = _make_explore_paper_tool(
        index, companion.paper_id, paper_lines, ctx.embedder, ctx,
    )

    gap_lines = [
        f"- [{g.id}] [{g.severity}] {g.gap} (line {g.line})"
        for g in sorted(open_gaps, key=lambda x: x.id)
    ]

    user_msg = (
        f"# Paper: {state.paper_id}\n\n"
        f"Thesis: {state.derive.central_claim if state.derive else ''}\n"
        f"Companion paper: {companion.paper_id} (author overlap: {companion.author_overlap:.0%})\n\n"
    )
    if gap_lines:
        user_msg += "## Open gaps\n\n" + "\n".join(gap_lines) + "\n\n"
    user_msg += (
        "Search the companion paper for evidence that addresses the open gaps. "
        "For each gap resolved, include it in the `closes` list with the evidence "
        "quote and line. When you report a contradiction, fill the `quote`, `line`, "
        "and `refutes` fields; set `claim_id` to the gap ID when applicable."
    )

    try:
        return await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=VerifyOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            tools={tool_fn.__name__: tool_fn},
            label=f"verify-{companion.paper_id}",
            debug_log=ctx.debug_log if ctx.debug else None,
        )
    except Exception as exc:
        logger.warning("Verify against %s failed: %s", companion.paper_id, exc)
        return None


def _merge_verify(a: VerifyOutput, b: VerifyOutput) -> VerifyOutput:
    """Merge two VerifyOutput payloads.

    Closes dedupe by stripped/lowercased evidence quote; contradictions
    dedupe by ``(source_pid, line, quote)``. Confirmations and
    new_evidence concatenate (free-form text where dedupe is unsafe).
    """
    merged_closes = list(a.closes)
    seen_close = {r.evidence_quote.strip().lower() for r in merged_closes}
    for r in b.closes:
        key = r.evidence_quote.strip().lower()
        if key not in seen_close:
            merged_closes.append(r)
            seen_close.add(key)

    merged_contra = list(a.contradictions)
    seen_contra = {(c.source_pid, c.line, c.quote.strip().lower()) for c in merged_contra}
    for c in b.contradictions:
        key = (c.source_pid, c.line, c.quote.strip().lower())
        if key not in seen_contra:
            merged_contra.append(c)
            seen_contra.add(key)

    return VerifyOutput(
        confirmations=list(a.confirmations) + list(b.confirmations),
        contradictions=merged_contra,
        new_evidence=list(a.new_evidence) + list(b.new_evidence),
        closes=merged_closes,
    )


def _apply_verify_closes(
    state: PipelineState, verify: VerifyOutput, source_pid_default: str = "",
) -> None:
    """Materialize Verify closes into state.items.evidence and gap closed_by lists."""
    if not verify.closes or state.items is None:
        return
    new_evidence_items: list[CollectedItem] = []
    close_eid_by_gid: dict[int, list[int]] = {}
    for resolution in verify.closes:
        eid = state._next_id
        state._next_id += 1
        new_evidence_items.append(CollectedItem(
            type="evidence",
            quote=resolution.evidence_quote,
            line=resolution.evidence_line,
            id=eid,
            source_pid=source_pid_default,
        ))
        close_eid_by_gid.setdefault(resolution.gap_id, []).append(eid)

    state.items = CollectedItems(
        claims=state.items.claims,
        evidence=list(state.items.evidence) + new_evidence_items,
        concessions=state.items.concessions,
        questions=state.items.questions,
        dependencies=state.items.dependencies,
        scope=state.items.scope,
    )
    for lens in (state.gaps_by_lens or {}):
        updated: list[GapOutput] = []
        for g in state.gaps_by_lens[lens]:
            new_ids = close_eid_by_gid.get(g.id)
            if new_ids:
                merged = list(g.closed_by) + [i for i in new_ids if i not in g.closed_by]
                updated.append(g.model_copy(update={"closed_by": merged}))
            else:
                updated.append(g)
        state.gaps_by_lens[lens] = updated


async def _custom_verify(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 9: verify open gaps against author-overlap companion papers.

    Iterates up to 4 companions in descending author overlap, with an
    early exit once no open gaps remain. Per-companion outputs are
    merged with dedup before being applied to state.
    """
    if ctx.embedder is None:
        return
    candidates = sorted(
        [r for r in state.ref_pids if r.in_paperstore and r.author_overlap >= 0.5],
        key=lambda r: -r.author_overlap,
    )
    if not candidates:
        return

    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget
    system_prompt = _prompt_for(ctx, _STEP_9_VERIFY) or _DEFAULT_VERIFY_PROMPT

    accumulated = VerifyOutput()
    for companion in candidates[:4]:
        if not _open_gaps_remain(state):
            break
        open_gaps = [
            g for lens_list in (state.gaps_by_lens or {}).values()
            for g in lens_list if not g.closed_by
        ]
        partial = await _verify_against_one_companion(
            state, ctx, agent, system_prompt, max_output, thinking,
            companion, open_gaps,
        )
        if partial is None:
            continue
        accumulated = _merge_verify(accumulated, partial)
        # Apply this companion's closes incrementally so the next iteration
        # sees an accurate set of open gaps.
        _apply_verify_closes(state, partial, source_pid_default=companion.paper_id)

    state.verify = accumulated


async def _custom_research(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 10: per-lens external research."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget

    system_prompt = _prompt_for(ctx, _STEP_10_RESEARCH)
    research_results: dict[str, ResearchLensOutput] = {}
    for lens in ["Performance", "Design", "Specification", "Usability", "Ecosystem", "Rationale"]:
        user_msg = _build_research_user_message(lens, state)

        if state.cited_paper_index is not None and ctx.embedder is not None:
            lens_bcs = (state.gaps_by_lens or {}).get(lens, [])
            thesis = state.derive.central_claim if state.derive else ""
            evidence = query_for_research(
                state.cited_paper_index, ctx.embedder, lens, lens_bcs, thesis,
            )
            if evidence:
                user_msg += f"\n\n{evidence}"

        try:
            result = await agent.run(
                system_prompt=system_prompt,
                user_message=user_msg,
                output_type=ResearchLensOutput,
                max_tokens=max_output,
                thinking_budget=thinking,
                label=f"research-{lens}",
                debug_log=ctx.debug_log if ctx.debug else None,
            )
            research_results[lens] = result
        except Exception as exc:
            logger.warning("Research for %s failed: %s", lens, exc)
            research_results[lens] = ResearchLensOutput(lens=lens, findings=[])

    state.research = research_results


async def _custom_probe(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 11: reference inventory checks (stale, author overlap)."""
    inventory = state.ref_pids
    stale_refs = [r.paper_id for r in inventory if r.stale]

    state.probe = ProbeResult(
        total_inventory=len(inventory),
        stale_refs=stale_refs,
    )


async def _custom_analyze(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 12: per-chunk analysis (Pass 2)."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget
    paper_lines = state.paper_md.splitlines()

    system_prompt = _prompt_for(ctx, _STEP_12_ANALYZE)
    all_findings: list[FindingOutput] = []
    all_strengths: list[StrengthOutput] = []
    chunks = state.chunk_map or []

    async def _analyze_one(ci: int, chunk: ChunkEntry):
        local_log: list[str] | None = [] if ctx.debug else None
        user_msg = _build_analyze_user_message(state.paper_id, chunk, paper_lines, state, ctx)
        result = await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=ChunkAnalyzeOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            label=f"analyze-chunk-{chunk.index}",
            debug_log=local_log,
        )
        return ci, result, local_log

    concurrency = spec.step.concurrency or ctx.default_concurrency
    results = await ctx.gather_concurrent(
        [_analyze_one(ci, chunk) for ci, chunk in enumerate(chunks)],
        concurrency=concurrency,
        label="analyze",
    )
    for _, result, _ in results:
        all_findings.extend(result.findings)
        all_strengths.extend(result.strengths)
    if ctx.debug and ctx.debug_log is not None:
        for _, _, local_log in results:
            if local_log:
                ctx.debug_log.extend(local_log)

    for i, f in enumerate(all_findings):
        all_findings[i] = f.model_copy(update={"id": state._next_id})
        state._next_id += 1
    for i, s in enumerate(all_strengths):
        all_strengths[i] = s.model_copy(update={"id": state._next_id})
        state._next_id += 1

    state.findings = all_findings
    state.strengths = all_strengths


async def _custom_rationale(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 13: SD-4 rationale checklist."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget

    user_msg = _build_rationale_user_message(state)
    result = await agent.run(
        system_prompt=_prompt_for(ctx, _STEP_13_RATIONALE),
        user_message=user_msg,
        output_type=RationaleOutput,
        max_tokens=max_output,
        thinking_budget=thinking,
        label="rationale",
        debug_log=ctx.debug_log if ctx.debug else None,
    )

    new_findings = dedupe_findings(
        state.findings or [], list(result.findings), embedder=ctx.embedder,
    )
    for i, f in enumerate(new_findings):
        new_findings[i] = f.model_copy(update={"id": state._next_id})
        state._next_id += 1
    findings = list(state.findings or [])
    findings.extend(new_findings)
    state.findings = findings

    new_strengths = list(result.strengths)
    for i, s in enumerate(new_strengths):
        new_strengths[i] = s.model_copy(update={"id": state._next_id})
        state._next_id += 1
    strengths = list(state.strengths or [])
    strengths.extend(new_strengths)
    state.strengths = strengths

    checklist = list(result.checklist)
    for i, c in enumerate(checklist):
        checklist[i] = c.model_copy(update={"id": state._next_id})
        state._next_id += 1
    state.checklist = checklist


async def _custom_challenge(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 14: LLM cross-examination of findings."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget
    system_prompt = _prompt_for(ctx, _STEP_14_CHALLENGE)

    findings = state.findings or []
    if not findings:
        state.surviving = []
        state.killed = []
        return

    by_lens: dict[str, list[FindingOutput]] = {}
    for f in findings:
        by_lens.setdefault(f.lens or "Other", []).append(f)

    all_verdicts: list[CrossExamVerdict] = []
    max_batch = 15

    for lens in sorted(by_lens.keys()):
        lens_findings = by_lens[lens]
        for i in range(0, len(lens_findings), max_batch):
            batch = lens_findings[i:i + max_batch]
            user_msg = _build_cross_exam_user_message(batch, state, ctx)

            if state.cited_paper_index is not None and ctx.embedder is not None:
                evidence_map = query_for_challenge(
                    state.cited_paper_index, ctx.embedder, batch,
                )
                if evidence_map:
                    parts = ["\n\n## Companion paper evidence\n"]
                    for f in batch:
                        if f.title in evidence_map:
                            parts.append(f"\n**{f.title}:**\n{evidence_map[f.title]}\n")
                    if len(parts) > 1:
                        user_msg += "".join(parts)

            result = await agent.run(
                system_prompt=system_prompt,
                user_message=user_msg,
                output_type=CrossExamBatchOutput,
                max_tokens=max_output,
                thinking_budget=thinking,
                label=f"cross-exam-{lens}-{i // max_batch}",
                debug_log=ctx.debug_log if ctx.debug else None,
            )
            all_verdicts.extend(result.verdicts)

    surviving, killed = cross_examine(findings, all_verdicts)
    state.surviving = surviving
    state.killed = killed


async def _custom_couple(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 15: compound dynamic detection."""
    agent = ctx.agents[spec.step.model]
    max_output = spec.step.max_output_tokens or agent.max_tokens
    thinking = spec.step.thinking_budget

    if not state.surviving:
        state.compounds = []
        return

    user_msg = _build_couple_user_message(state)
    result = await agent.run(
        system_prompt=_prompt_for(ctx, _STEP_15_COUPLE),
        user_message=user_msg,
        output_type=CoupleOutput,
        max_tokens=max_output,
        thinking_budget=thinking,
        label="couple",
        debug_log=ctx.debug_log if ctx.debug else None,
    )
    state.compounds = list(result.compounds)


async def _custom_synthesize(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 16: verdict derivation (pure Python)."""
    state.synthesis = synthesize(
        state.surviving or [],
        state.compounds or [],
        state.derive or DeriveOutput(central_claim="", problem_statement="", scope_boundary=""),
    )


async def _custom_report(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 17: render markdown report."""
    section_text = _prompt_for(ctx, _STEP_17_REPORT)
    state.report = render_report(state, section_text)


# -- Hook registry -----------------------------------------------------------


def _build_hooks() -> dict[str, StepHooks]:
    """Build the step hook table.

    No agents are attached here; each custom hook resolves its agent
    at runtime via ``ctx.agents[spec.step.model]`` so the pipeline
    markdown's `**Model:**` declaration is the single source of truth.
    """
    return {
        _STEP_0_RECEIVE: StepHooks(custom=_custom_receive),
        _STEP_1_REFERENCES: StepHooks(custom=_custom_references),
        _STEP_2_INDEX: StepHooks(custom=_custom_index),
        _STEP_3_SURVEY: StepHooks(custom=_custom_survey),
        _STEP_4_EXTRACT: StepHooks(custom=_custom_extract),
        _STEP_5_DECIDE: StepHooks(custom=_custom_decide),
        _STEP_6_CLASSIFY: StepHooks(custom=_custom_classify),
        _STEP_7_COLLECT: StepHooks(custom=_custom_collect),
        _STEP_8_DERIVE: StepHooks(custom=_custom_derive),
        _STEP_9_VERIFY: StepHooks(custom=_custom_verify),
        _STEP_10_RESEARCH: StepHooks(custom=_custom_research),
        _STEP_11_PROBE: StepHooks(custom=_custom_probe),
        _STEP_12_ANALYZE: StepHooks(custom=_custom_analyze),
        _STEP_13_RATIONALE: StepHooks(custom=_custom_rationale),
        _STEP_14_CHALLENGE: StepHooks(custom=_custom_challenge),
        _STEP_15_COUPLE: StepHooks(custom=_custom_couple),
        _STEP_16_SYNTHESIZE: StepHooks(custom=_custom_synthesize),
        _STEP_17_REPORT: StepHooks(custom=_custom_report),
    }


# -- Persistence callback ----------------------------------------------------


def _persist_step(spec, state: PipelineState, ctx: StepContext) -> None:
    """Persist pipeline artifacts to the database after each producing step."""
    if ctx.backend is None:
        return
    step_name = spec.step.name
    if step_name == _STEP_1_REFERENCES:
        _persist_pids(ctx.backend, ctx.pid, state.ref_pids)
        _persist_urls(ctx.backend, ctx.pid, state.ref_urls)
    elif step_name == _STEP_7_COLLECT and state.items is not None:
        items = state.items
        _persist_claims(ctx.backend, ctx.pid, items.claims)
        _persist_evidence(ctx.backend, ctx.pid, items.evidence)
        _persist_concessions(ctx.backend, ctx.pid, items.concessions)
        _persist_gaps(ctx.backend, ctx.pid, state.gaps_by_lens or {})
        _persist_asks(ctx.backend, ctx.pid, state.asks or [])
    elif step_name == _STEP_8_DERIVE and state.derive is not None:
        _persist_thesis(ctx.backend, ctx.pid, state.derive)
    elif step_name == _STEP_9_VERIFY:
        _persist_gaps(ctx.backend, ctx.pid, state.gaps_by_lens or {})
        if state.items is not None:
            _persist_evidence(ctx.backend, ctx.pid, state.items.evidence)
    elif step_name == _STEP_12_ANALYZE:
        _persist_strengths(ctx.backend, ctx.pid, state.strengths or [])
    elif step_name == _STEP_13_RATIONALE:
        _persist_checklist(ctx.backend, ctx.pid, state.checklist or [])
        _persist_strengths(ctx.backend, ctx.pid, state.strengths or [])
    elif step_name == _STEP_14_CHALLENGE:
        _persist_findings(ctx.backend, ctx.pid, state.surviving or [], state.killed or [], state.synthesis)
    elif step_name == _STEP_15_COUPLE:
        _persist_compounds(ctx.backend, ctx.pid, state.compounds or [])
    elif step_name == _STEP_16_SYNTHESIZE:
        _persist_synthesis(ctx.backend, ctx.pid, state.synthesis)


def _persist_claims(backend, pid, claims: list):
    from dataclasses import dataclass as _dc

    @_dc
    class _Row:
        uid: int
        loc_line: int
        quote: str
        section: str
        kind: str
        load_bearing: bool

    rows = [_Row(c.id, c.line, c.quote, c.section, "", False)
            for c in claims]
    backend.store_assay_claims(pid, rows)


def _persist_evidence(backend, pid, evidence: list):
    from dataclasses import dataclass as _dc

    @_dc
    class _Row:
        uid: int
        loc_line: int
        quote: str
        section: str
        subtype: str
        quality_tier: str
        supports: str
        source_pid: str

    rows = [_Row(e.id, e.line, e.quote, e.section, "", e.quality_tier or "", "[]",
                 getattr(e, 'source_pid', ''))
            for e in evidence]
    backend.store_assay_evidence(pid, rows)


def _persist_concessions(backend, pid, concessions: list):
    from dataclasses import dataclass as _dc

    @_dc
    class _Row:
        uid: int
        loc_line: int
        quote: str
        section: str
        subtype: str

    rows = [_Row(i, c.line, c.quote, c.section, "")
            for i, c in enumerate(concessions, 1)]
    backend.store_assay_concessions(pid, rows)


def _persist_gaps(backend, pid, gaps_by_lens):
    from dataclasses import dataclass as _dc, field as _field

    @_dc
    class _Row:
        uid: int
        chunk_index: int
        loc_line: int
        gap: str
        why_important: str
        primary_lens: str
        secondary_lens: str
        severity: str
        closed_by: list = _field(default_factory=list)

    seen: set[int] = set()
    rows = []
    for lens, bcs in gaps_by_lens.items():
        for b in bcs:
            if b.id in seen:
                continue
            seen.add(b.id)
            rows.append(_Row(b.id, b.chunk_index, b.line,
                            b.gap, b.why_important,
                            b.primary_lens or lens, b.secondary_lens or "",
                            b.severity, list(b.closed_by)))
    backend.store_assay_gaps(pid, rows)


def _persist_asks(backend, pid, asks: list):
    rows = [{"target": a.target, "quote": a.quote, "type": a.type, "line": a.line}
            for a in asks]
    backend.store_assay_asks(pid, rows)


def _persist_pids(backend, pid, inventory: list):
    rows = [
        {"raw_pid": r.raw_pid, "resolved_pid": r.paper_id, "url": r.url,
         "mention_count": r.count, "in_paperstore": r.in_paperstore,
         "stale": r.stale, "author_overlap": r.author_overlap}
        for r in inventory
    ]
    backend.store_assay_pids(pid, rows)


def _persist_urls(backend, pid, urls: list):
    rows = [{"url": u.url, "line": u.line} for u in urls]
    backend.store_assay_urls(pid, rows)


def _persist_thesis(backend, pid, derive: DeriveOutput):
    from dataclasses import dataclass as _dc

    @_dc
    class _Row:
        central_claim: str
        problem_statement: str
        scope_boundary: str
        ask_calibration: str

    row = _Row(derive.central_claim, derive.problem_statement,
               derive.scope_boundary, derive.ask_calibration)
    backend.store_assay_thesis(pid, row)


def _persist_strengths(backend, pid, strengths: list):
    rows = [{"title": s.title, "quote": s.quote, "line": s.line,
             "explanation": s.explanation, "lens": s.lens}
            for s in strengths]
    backend.store_assay_strengths(pid, rows)


def _persist_checklist(backend, pid, checklist: list):
    rows = [{"id": c.id, "name": c.name, "passed": c.passed,
             "location": c.location or "", "note": c.note or ""}
            for c in checklist]
    backend.store_assay_checklist(pid, rows)


def _persist_findings(backend, pid, surviving: list, killed: list, synthesis=None):
    from dataclasses import dataclass as _dc, field as _field

    @_dc
    class _Row:
        uid: int
        title: str
        lens: str
        severity: str
        quote: str
        loc_line: int
        explanation: str
        test: str
        survived: bool
        major: bool
        challenge: str
        reasoning: str
        from_gap_ids: list = _field(default_factory=list)

    major_set: set[str] = set()
    if synthesis is not None:
        major_set = {f.title for f in synthesis.major_findings}

    rows = []
    for i, f in enumerate(surviving, 1):
        rows.append(_Row(i, f.title, f.lens, f.severity,
                        f.quote, f.line, f.explanation,
                        f.test, True, f.title in major_set,
                        "", "", list(getattr(f, "from_gap_ids", []) or [])))
    offset = len(surviving)
    for i, k in enumerate(killed, offset + 1):
        rows.append(_Row(i, k.finding_title, k.lens,
                        "", "",
                        0, "",
                        "", False, False,
                        k.challenge, k.reasoning, []))
    backend.store_assay_findings(pid, rows)


def _persist_compounds(backend, pid, compounds: list):
    rows = [
        {"name": c.name, "constituents": list(c.constituents),
         "mechanism": c.mechanism, "cross_lens": c.cross_lens,
         "emergent_risk": c.emergent_risk or ""}
        for c in compounds
    ]
    backend.store_assay_compounds(pid, rows)


def _persist_synthesis(backend, pid, synthesis):
    if synthesis is None:
        return
    row = {
        "verdict": synthesis.verdict_label,
        "verdict_confidence": synthesis.verdict_confidence,
        "central_thesis": synthesis.verdict_statement,
        "dominant_dynamic": synthesis.dominant_dynamic or "",
        "thesis_survives": synthesis.thesis_survives,
        "thesis_statement": synthesis.thesis_statement,
        "critical_count": synthesis.critical_count,
        "significant_count": synthesis.significant_count,
    }
    backend.store_assay_synthesis(pid, row)


# -- Public API --------------------------------------------------------------


async def assay_paper(
    pid: str,
    backend: StorageBackend,
    *,
    debug: bool = False,
    trace: bool = False,
    stop_after: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Run the assay pipeline on a WG21 paper and return the report markdown.

    Model selection comes from ``assay.md``'s ``## Services`` block.
    SERVICES.toml is a pure inventory; to change which backend runs
    which step, edit ``assay.md``.
    """
    prompt = PipelinePrompt.load("assay", "assay.md")
    registry = load_services()
    models = resolve_pipeline_models(prompt.services, registry)

    default_concurrency = int(prompt.config.get("concurrency", "1") or 1)

    agents: dict[str, AgentBackend] = {
        name: AgentBackend(
            model_backend,
            max_tokens=16384,
            slot_name=name,
            service_name=prompt.services[name],
        )
        for name, model_backend in models.items()
    }

    hooks = _build_hooks()
    pipeline = build_pipeline(prompt, hooks)
    validate_capabilities(pipeline, prompt, agents, stop_after=stop_after)

    embedders, embedder_defaults = load_embedders()
    embedder_name = embedder_defaults.get("default")
    embedder = embedders.get(embedder_name) if embedder_name else None

    state = PipelineState()

    ctx = StepContext(
        prompt=prompt,
        agents=agents,
        backend=backend,
        debug=debug,
        pid=pid,
        default_concurrency=default_concurrency,
        embedder=embedder,
    )

    debug_path = backend.get_debug_md_path(pid, tool="assay")
    if debug:
        debug_path.unlink(missing_ok=True)

    trace_path = (
        backend.get_trace_md_path(pid, tool="assay")
        if (trace or stop_after is not None)
        else None
    )

    await dispatch(
        pipeline,
        state,
        ctx,
        tool_name="assay",
        stop_after=stop_after,
        on_progress=on_progress,
        on_step_complete=lambda spec, st: _persist_step(spec, st, ctx),
        render_trace_fn=lambda st, step: render_trace(st, step, step_durations=[m.duration_s for m in ctx.step_metrics]),
        trace_path=trace_path,
        debug_path=debug_path if debug else None,
    )

    if stop_after is not None:
        return render_trace(state, stop_after)
    return state.report or ""


async def assay_since(
    month: str,
    backend: StorageBackend,
    *,
    debug: bool = False,
    trace: bool = False,
    on_progress: ProgressCallback | None = None,
) -> list[dict[str, str | None]]:
    """Run assay on all papers since a given month."""
    papers = backend.list_papers_since(month)
    results: list[dict[str, str | None]] = []

    for paper in papers:
        pid = paper.paper_id
        try:
            report = await assay_paper(
                pid, backend,
                debug=debug, trace=trace,
                on_progress=on_progress,
            )
            backend.write_assay_md(pid, report)
            results.append({"paper_id": pid, "status": "ok", "error": None})
        except Exception as exc:
            logger.error("assay failed for %s: %s", pid, exc)
            results.append({"paper_id": pid, "status": "error", "error": str(exc)})

    return results
