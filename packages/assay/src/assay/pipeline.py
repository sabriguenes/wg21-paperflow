#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Assay pipeline orchestration.

16-step two-pass structural analysis. All LLM calls are sequential
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
from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.progress import ProgressCallback

from pipeline import (
    AgentBackend,
    StepContext,
    StepHooks,
    build_pipeline,
    dispatch,
    load_sections,
    validate_capabilities,
)
from pipeline.services import load_embedders, load_services, parse_pipeline_config, parse_service_overrides, resolve_slots
from pipeline.tools import wrap_source

from assay.harness import (
    collect,
    cross_examine,
    synthesize,
    upgrade_breadcrumbs,
)
from assay.locs import format_numbered_lines
from assay.models import (
    BreadcrumbOutput,
    ChunkAnalyzeOutput,
    ChunkEntry,
    ChunkExtractOutput,
    CollectedItems,
    CoupleOutput,
    CrossExamBatchOutput,
    CrossExamVerdict,
    DeriveOutput,
    FindingOutput,
    FrontMatter,
    PipelineState,
    ProbeResult,
    RationaleOutput,
    ResearchLensOutput,
    StrengthOutput,
    SynthesisOutput,
)
from assay.blanking import blank_paper
from assay.chunker import chunk_paper
from assay.rag import build_cited_paper_index, query_for_research, query_for_challenge, IndexStats
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
_STEP_5_SCAN = "5. Scan"
_STEP_6_COLLECT = "6. Collect"
_STEP_7_DERIVE = "7. Derive"
_STEP_8_RESEARCH = "8. Research"
_STEP_9_PROBE = "9. Probe"
_STEP_10_ANALYZE = "10. Analyze"
_STEP_11_RATIONALE = "11. Rationale"
_STEP_12_CHALLENGE = "12. Challenge"
_STEP_13_COUPLE = "13. Couple"
_STEP_14_SYNTHESIZE = "14. Synthesize"
_STEP_15_REPORT = "15. Report"


# -- Prompt helpers ----------------------------------------------------------


def _prompt_for(ctx: StepContext, step_name: str) -> str:
    """Read the prompt text from assay.md for a given step."""
    return ctx.sections.get(step_name, "").strip()


# -- User message builders ---------------------------------------------------



def _build_extract_user_message(
    pid: str, chunk: ChunkEntry, paper_lines: list[str],
    reference_inventory: list | None = None,
) -> str:
    numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)
    parts = [
        f"# Paper: {pid}\n\n",
        f"## {chunk.heading} (chunk {chunk.index}, lines {chunk.start_line}-{chunk.end_line})\n\n",
    ]

    chunk_refs = _refs_in_range(
        reference_inventory or [], chunk.start_line, chunk.end_line,
    )
    if chunk_refs:
        parts.append("## References in this chunk\n\n")
        for r in chunk_refs:
            url_part = f", {r.urls[0]}" if r.urls else ""
            lines_str = ", ".join(str(ln) for ln in r.lines[:3])
            parts.append(f"- {r.paper_id} (line {lines_str}{url_part})\n")
        parts.append("\n")

    parts.append(f"{wrap_source(numbered)}\n")
    return "".join(parts)


def _refs_in_range(
    inventory: list, start_line: int, end_line: int,
) -> list:
    """Filter reference inventory to entries with at least one line in range."""
    result = []
    for ref in inventory:
        if any(start_line <= ln <= end_line for ln in ref.lines):
            result.append(ref)
    return result


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
            parts.append(f"- \"{a.quote}\" (line {a.line})\n")
    return "".join(parts)


def _build_research_user_message(lens: str, state: PipelineState) -> str:
    derive = state.derive
    front = state.front_matter
    return (
        f"# Research for lens: {lens}\n\n"
        f"Paper: {front.document if front else state.paper_id} - \"{front.title if front else state.paper_title}\"\n"
        f"Authors: {', '.join(front.authors if front else [])}\n"
        f"Thesis: {derive.central_claim if derive else ''}\n"
        f"Scope: {derive.scope_boundary if derive else ''}\n"
    )


def _build_analyze_user_message(
    pid: str, chunk: ChunkEntry, paper_lines: list[str], state: PipelineState
) -> str:
    derive = state.derive
    numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)

    other_breadcrumbs: list[BreadcrumbOutput] = []
    for lens, bcs in (state.breadcrumbs_by_lens or {}).items():
        for b in bcs:
            if b.chunk_index != chunk.index:
                other_breadcrumbs.append(b)

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

    if other_breadcrumbs[:20]:
        parts.append("\n## Cross-chunk breadcrumbs\n\n")
        for b in other_breadcrumbs[:20]:
            parts.append(f"- [{b.severity}] {b.gap} (line {b.line})\n")

    parts.append(f"\n## Chunk: {chunk.heading} (lines {chunk.start_line}-{chunk.end_line})\n\n")
    parts.append(f"{wrap_source(numbered)}\n")

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
    findings_batch: list[FindingOutput], state: PipelineState
) -> str:
    """Build user message for one cross-examination batch."""
    derive = state.derive
    items = state.items or CollectedItems()
    concessions = items.concessions
    paper_lines = (state.paper_source or "").splitlines()

    parts = [
        f"# Paper: {state.paper_id}\n\n",
        f"## Thesis: {derive.central_claim if derive else ''}\n",
        f"Scope: {derive.scope_boundary if derive else ''}\n\n",
    ]

    if concessions:
        parts.append("## Concessions\n\n")
        for c in concessions:
            parts.append(f"- \"{c.quote}\" (line {c.line})\n")
        parts.append("\n")

    parts.append("## Findings to cross-examine\n\n")
    for f in findings_batch:
        parts.append(f"### {f.title}\n\n")
        parts.append(f"**Severity:** {f.severity}\n")
        parts.append(f"**Lens:** {f.lens}\n")
        if f.quote:
            parts.append(f"**Quote:** \"{f.quote}\" (line {f.line})\n")
        parts.append(f"**Explanation:** {f.explanation}\n")
        if f.damage:
            parts.append(f"**Damage:** {f.damage}\n")

        if f.line > 0 and paper_lines:
            start = max(0, f.line - 6)
            end = min(len(paper_lines), f.line + 5)
            context = format_numbered_lines(paper_lines, start + 1, end)
            parts.append(f"\n**Paper context (lines {start + 1}-{end}):**\n\n{context}\n")

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
            parts.append(f"- **{f.title}** ({f.severity}): {f.explanation[:200]}\n")
        parts.append("\n")
    return "".join(parts)


# -- Custom hooks ------------------------------------------------------------


async def _custom_receive(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 0: validate paper exists, load source, blank."""
    assert ctx.backend is not None
    pid = ctx.pid

    try:
        meta = await asyncio.to_thread(ctx.backend.get_meta, pid)
    except MissingMetaError as exc:
        raise RuntimeError(
            f"Paper '{pid}' not found in paperstore. "
            f"Run 'paperflow mailing <year>' to index it."
        ) from exc

    try:
        paper_md = await asyncio.to_thread(ctx.backend.get_paper_md, pid)
    except MissingPaperMdError as exc:
        raise RuntimeError(
            f"Paper '{pid}' has no converted markdown. "
            f"Run 'paperflow convert {pid}' first."
        ) from exc

    paper_md = blank_paper(paper_md, paper_id=pid)

    state.paper_id = pid
    state.paper_source = paper_md
    state.paper_title = meta.title or ""
    state.front_matter = FrontMatter(
        document=pid.upper(),
        title=meta.title or "",
        date=meta.document_date or "",
        audience=[meta.target_group] if meta.target_group else [],
        authors=meta.authors or [],
        intent=meta.intent or "",
    )


async def _custom_references(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 1: mechanical reference extraction."""
    from assay.references import extract_references, verify_references
    refs = extract_references(state.paper_source or "")
    if ctx.backend is not None:
        refs = verify_references(
            refs, ctx.backend, (state.front_matter.authors if state.front_matter else [])
        )
    state.reference_inventory = refs


async def _custom_index(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 2: build ephemeral RAG index over cited papers."""
    import time
    if not state.reference_inventory:
        return
    if ctx.embedder is None:
        return
    t0 = time.perf_counter()
    state.cited_paper_index = build_cited_paper_index(
        state.reference_inventory,
        state.reference_registry,
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
            skipped=[r.paper_id for r in state.reference_inventory
                     if not r.in_paperstore or r.paper_id.upper() == state.paper_id.upper()],
        )


async def _custom_survey(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 3: chunk paper, wording signal, triage."""
    agent = ctx.agents.get("fast")
    chunk_tokens = spec.meta.chunk_tokens or 2000
    sections = chunk_paper(
        state.paper_source or "",
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
    wording_lines = sum(s.end_line - s.start_line for s in wording_headings)
    fm = state.front_matter
    audience = " ".join(fm.audience if fm else []).upper()
    targets_cwg_lwg = "CWG" in audience or "LWG" in audience
    state.front_matter = FrontMatter(
        document=fm.document if fm else "",
        title=fm.title if fm else "",
        date=fm.date if fm else "",
        audience=fm.audience if fm else [],
        authors=fm.authors if fm else [],
        intent=fm.intent if fm else "",
        wording_lines=wording_lines,
        targets_cwg_lwg=targets_cwg_lwg,
    )

    triage = should_analyze(
        state.chunk_map, state.front_matter, state.paper_source or ""
    )
    if not triage.analyze:
        state.synthesis = SynthesisOutput(
            verdict="Skipped",
            verdict_confidence="High",
            central_thesis=f"{triage.paper_type.replace('_', ' ').title()}: not analyzed.",
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
    agent = ctx.agents["fast"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None
    paper_lines = (state.paper_source or "").splitlines()

    system_prompt = _prompt_for(ctx, _STEP_4_EXTRACT)
    chunks = state.chunk_map or []

    async def _extract_one(ci: int, chunk: ChunkEntry):
        user_msg = _build_extract_user_message(
            state.paper_id, chunk, paper_lines, state.reference_inventory,
        )
        result = await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=ChunkExtractOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            label=f"extract-chunk-{chunk.index}",
            debug_log=ctx.debug_log if ctx.debug else None,
        )
        return ci, result

    concurrency = spec.meta.concurrency or ctx.default_concurrency
    state.raw_extractions = await ctx.gather_concurrent(
        [_extract_one(ci, chunk) for ci, chunk in enumerate(chunks)],
        concurrency=concurrency,
        label="extract",
    )


async def _custom_scan(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 5: per-chunk breadcrumb detection."""
    from assay.models import ScanOutput

    agent = ctx.agents["fast"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None
    system_prompt = _prompt_for(ctx, _STEP_5_SCAN)
    chunks = state.chunk_map or []
    paper_lines = (state.paper_source or "").splitlines()

    async def _scan_one(ci: int, chunk: ChunkEntry):
        numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)
        user_msg = (
            f"# Paper: {state.paper_id}\n\n"
            f"## {chunk.heading} (chunk {chunk.index}, lines {chunk.start_line}-{chunk.end_line})\n\n"
            f"{wrap_source(numbered)}\n"
        )
        result = await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=ScanOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            label=f"scan-chunk-{chunk.index}",
            debug_log=ctx.debug_log if ctx.debug else None,
        )
        return ci, result

    concurrency = spec.meta.concurrency or ctx.default_concurrency
    state.raw_scans = await ctx.gather_concurrent(
        [_scan_one(ci, chunk) for ci, chunk in enumerate(chunks)],
        concurrency=concurrency,
        label="scan",
    )


async def _custom_collect(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 6: aggregate and dedup (pure Python)."""
    items, breadcrumbs_by_lens, asks, active, inactive, registry = collect(
        state.raw_extractions or [], state.raw_scans or [], state.front_matter
    )
    state.items = items
    state.breadcrumbs_by_lens = breadcrumbs_by_lens
    state.asks = asks
    state.active_lenses = active
    state.inactive_lenses = inactive
    state.reference_registry = registry


async def _custom_derive(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 7: thesis compression and load-bearing identification."""
    agent = ctx.agents["default"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None

    user_msg = _build_derive_user_message(state)
    result = await agent.run(
        system_prompt=_prompt_for(ctx, _STEP_7_DERIVE),
        user_message=user_msg,
        output_type=DeriveOutput,
        max_tokens=max_output,
        thinking_budget=thinking,
        label="derive",
        debug_log=ctx.debug_log if ctx.debug else None,
    )

    state.derive = result

    state.breadcrumbs_by_lens = upgrade_breadcrumbs(
        state.breadcrumbs_by_lens or {},
        result.central_claim,
        result.problem_statement,
    )


async def _custom_research(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 8: per-lens external research."""
    agent = ctx.agents["tool"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None

    system_prompt = _prompt_for(ctx, _STEP_8_RESEARCH)
    research_results: dict[str, ResearchLensOutput] = {}
    for lens in ["Performance", "Design", "Specification", "Usability", "Ecosystem", "Rationale"]:
        user_msg = _build_research_user_message(lens, state)

        if state.cited_paper_index is not None and ctx.embedder is not None:
            lens_bcs = (state.breadcrumbs_by_lens or {}).get(lens, [])
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
    """Step 9: reference inventory vs LLM registry diff."""
    inventory = {r.paper_id: r for r in (state.reference_inventory or [])}
    registry = {r.ref_label: r for r in (state.reference_registry or [])}

    cited_not_referenced = []
    for pid, ref in inventory.items():
        matched = any(
            pid.upper() in (r.ref_label or "").upper()
            or pid.upper() in (r.url or "").upper()
            for r in (state.reference_registry or [])
        )
        if not matched:
            cited_not_referenced.append(pid)

    referenced_not_cited = []
    for r in (state.reference_registry or []):
        label = r.ref_label or ""
        matched = any(
            label.upper() in pid.upper() or pid.upper() in label.upper()
            for pid in inventory
        )
        if not matched and label:
            referenced_not_cited.append(label)

    companions = [
        r.ref_label
        for r in (state.reference_registry or [])
        if r.relationship == "companion"
    ]

    stale_refs = [r.paper_id for r in (state.reference_inventory or []) if r.stale]
    self_cites = [r.paper_id for r in (state.reference_inventory or []) if r.self_cite]

    state.probe = ProbeResult(
        total_inventory=len(inventory),
        total_registry=len(registry),
        cited_not_referenced=cited_not_referenced,
        referenced_not_cited=referenced_not_cited,
        companions=companions,
        stale_refs=stale_refs,
        self_cites=self_cites,
    )


async def _custom_analyze(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 10: per-chunk analysis (Pass 2)."""
    agent = ctx.agents["default"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None
    paper_lines = (state.paper_source or "").splitlines()

    system_prompt = _prompt_for(ctx, _STEP_10_ANALYZE)
    all_findings: list[FindingOutput] = []
    all_strengths: list[StrengthOutput] = []
    chunks = state.chunk_map or []

    async def _analyze_one(ci: int, chunk: ChunkEntry):
        user_msg = _build_analyze_user_message(state.paper_id, chunk, paper_lines, state)
        result = await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=ChunkAnalyzeOutput,
            max_tokens=max_output,
            thinking_budget=thinking,
            label=f"analyze-chunk-{chunk.index}",
            debug_log=ctx.debug_log if ctx.debug else None,
        )
        return ci, result

    concurrency = spec.meta.concurrency or ctx.default_concurrency
    results = await ctx.gather_concurrent(
        [_analyze_one(ci, chunk) for ci, chunk in enumerate(chunks)],
        concurrency=concurrency,
        label="analyze",
    )
    for result in results:
        all_findings.extend(result.findings)
        all_strengths.extend(result.strengths)

    state.findings = all_findings
    state.strengths = all_strengths


async def _custom_rationale(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 11: SD-4 rationale checklist."""
    agent = ctx.agents["default"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None

    user_msg = _build_rationale_user_message(state)
    result = await agent.run(
        system_prompt=_prompt_for(ctx, _STEP_11_RATIONALE),
        user_message=user_msg,
        output_type=RationaleOutput,
        max_tokens=max_output,
        thinking_budget=thinking,
        label="rationale",
        debug_log=ctx.debug_log if ctx.debug else None,
    )

    findings = list(state.findings or [])
    findings.extend(result.findings)
    state.findings = findings

    strengths = list(state.strengths or [])
    strengths.extend(result.strengths)
    state.strengths = strengths

    state.checklist = list(result.checklist)


async def _custom_challenge(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 12: LLM cross-examination of findings."""
    agent = ctx.agents["default"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None
    system_prompt = _prompt_for(ctx, _STEP_12_CHALLENGE)

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
            user_msg = _build_cross_exam_user_message(batch, state)

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
    """Step 13: compound dynamic detection."""
    agent = ctx.agents["default"]
    max_output = spec.meta.max_output_tokens or agent.max_tokens
    thinking = spec.meta.thinking_budget or None

    if not state.surviving:
        state.compounds = []
        return

    user_msg = _build_couple_user_message(state)
    result = await agent.run(
        system_prompt=_prompt_for(ctx, _STEP_13_COUPLE),
        user_message=user_msg,
        output_type=CoupleOutput,
        max_tokens=max_output,
        thinking_budget=thinking,
        label="couple",
        debug_log=ctx.debug_log if ctx.debug else None,
    )
    state.compounds = list(result.compounds)


async def _custom_synthesize(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 14: verdict derivation (pure Python)."""
    state.synthesis = synthesize(
        state.surviving or [],
        state.compounds or [],
        state.derive or DeriveOutput(central_claim="", problem_statement="", scope_boundary=""),
    )


async def _custom_report(state: PipelineState, ctx: StepContext, spec) -> None:
    """Step 15: render markdown report."""
    section_text = _prompt_for(ctx, _STEP_15_REPORT)
    state.report = render_report(state, section_text)


# -- Hook registry -----------------------------------------------------------


def _build_hooks(
    extraction_agent: AgentBackend,
    synthesis_agent: AgentBackend,
    research_agent: AgentBackend,
) -> dict[str, StepHooks]:
    return {
        _STEP_0_RECEIVE: StepHooks(custom=_custom_receive),
        _STEP_1_REFERENCES: StepHooks(custom=_custom_references),
        _STEP_2_INDEX: StepHooks(custom=_custom_index),
        _STEP_3_SURVEY: StepHooks(custom=_custom_survey),
        _STEP_4_EXTRACT: StepHooks(custom=_custom_extract, agent=extraction_agent),
        _STEP_5_SCAN: StepHooks(custom=_custom_scan, agent=extraction_agent),
        _STEP_6_COLLECT: StepHooks(custom=_custom_collect),
        _STEP_7_DERIVE: StepHooks(custom=_custom_derive, agent=synthesis_agent),
        _STEP_8_RESEARCH: StepHooks(custom=_custom_research, agent=research_agent),
        _STEP_9_PROBE: StepHooks(custom=_custom_probe),
        _STEP_10_ANALYZE: StepHooks(custom=_custom_analyze, agent=synthesis_agent),
        _STEP_11_RATIONALE: StepHooks(custom=_custom_rationale, agent=synthesis_agent),
        _STEP_12_CHALLENGE: StepHooks(custom=_custom_challenge, agent=synthesis_agent),
        _STEP_13_COUPLE: StepHooks(custom=_custom_couple, agent=synthesis_agent),
        _STEP_14_SYNTHESIZE: StepHooks(custom=_custom_synthesize),
        _STEP_15_REPORT: StepHooks(custom=_custom_report),
    }


# -- Persistence callback ----------------------------------------------------


def _persist_step(spec, state: PipelineState, ctx: StepContext) -> None:
    """Persist pipeline artifacts to the database after each producing step."""
    if ctx.backend is None:
        return
    step_name = spec.meta.name
    if step_name == _STEP_6_COLLECT and state.items is not None:
        items = state.items
        _persist_claims(ctx.backend, ctx.pid, items.claims)
        _persist_evidence(ctx.backend, ctx.pid, items.evidence)
        _persist_concessions(ctx.backend, ctx.pid, items.concessions)
        _persist_breadcrumbs(ctx.backend, ctx.pid, state.breadcrumbs_by_lens or {})
        _persist_asks(ctx.backend, ctx.pid, state.asks or [])
        _persist_references(ctx.backend, ctx.pid, state.reference_registry or [])
    elif step_name == _STEP_7_DERIVE and state.derive is not None:
        _persist_thesis(ctx.backend, ctx.pid, state.derive)
    elif step_name == _STEP_10_ANALYZE:
        _persist_strengths(ctx.backend, ctx.pid, state.strengths or [])
    elif step_name == _STEP_11_RATIONALE:
        _persist_checklist(ctx.backend, ctx.pid, state.checklist or [])
        _persist_strengths(ctx.backend, ctx.pid, state.strengths or [])
    elif step_name == _STEP_12_CHALLENGE:
        _persist_findings(ctx.backend, ctx.pid, state.surviving or [], state.killed or [], state.synthesis)
    elif step_name == _STEP_13_COUPLE:
        _persist_compounds(ctx.backend, ctx.pid, state.compounds or [])
    elif step_name == _STEP_14_SYNTHESIZE:
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

    rows = [_Row(i, c.line, c.quote, c.section, "", False)
            for i, c in enumerate(claims, 1)]
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

    rows = [_Row(i, e.line, e.quote, e.section, "", e.quality_tier or "", "[]")
            for i, e in enumerate(evidence, 1)]
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


def _persist_breadcrumbs(backend, pid, breadcrumbs_by_lens):
    from dataclasses import dataclass as _dc

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

    uid = 0
    rows = []
    for lens, bcs in breadcrumbs_by_lens.items():
        for b in bcs:
            uid += 1
            rows.append(_Row(uid, b.chunk_index, b.line,
                            b.gap, b.why_important,
                            b.primary_lens or lens, b.secondary_lens or "",
                            b.severity))
    backend.store_assay_breadcrumbs(pid, rows)


def _persist_asks(backend, pid, asks: list):
    rows = [{"target": a.target, "quote": a.quote, "type": a.type, "line": a.line}
            for a in asks]
    backend.store_assay_asks(pid, rows)


def _persist_references(backend, pid, registry: list):
    rows = [
        {"ref_id": r.ref_id, "ref_label": r.ref_label, "url": r.url,
         "source_type": r.source_type, "relationship": r.relationship,
         "same_author": r.same_author, "mention_count": r.mention_count,
         "contexts": r.contexts}
        for r in registry
    ]
    backend.store_assay_references(pid, rows)


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
    from dataclasses import dataclass as _dc

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

    major_set: set[str] = set()
    if synthesis is not None:
        major_set = {f.title for f in synthesis.major_findings}

    rows = []
    for i, f in enumerate(surviving, 1):
        rows.append(_Row(i, f.title, f.lens, f.severity,
                        f.quote, f.line, f.explanation,
                        f.test, True, f.title in major_set,
                        "", ""))
    offset = len(surviving)
    for i, k in enumerate(killed, offset + 1):
        rows.append(_Row(i, k.finding_title, k.lens,
                        "", "",
                        0, "",
                        "", False, False,
                        k.challenge, k.reasoning))
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
        "verdict": synthesis.verdict,
        "verdict_confidence": synthesis.verdict_confidence,
        "central_thesis": synthesis.central_thesis,
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
    service_overrides: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Run the assay pipeline on a WG21 paper and return the report markdown."""
    registry = load_services()
    secs = dict(load_sections("assay", "assay.md"))
    md_overrides = parse_service_overrides(secs.get("Services", ""))
    pipeline_config = parse_pipeline_config(secs.get("Config", ""))
    merged = {**md_overrides, **(service_overrides or {})}
    slots = resolve_slots(registry, merged or None)
    default_concurrency = int(pipeline_config.get("concurrency", 1) or 1)

    if "System Prompt" not in secs:
        raise RuntimeError("assay.md missing required '## System Prompt' section")

    fast_svc, fast_backend = slots["fast"]
    default_svc, default_backend = slots["default"]
    tool_svc, tool_backend = slots["tool"]

    extraction_agent = AgentBackend(
        fast_backend, max_tokens=16384, slot_name="fast", service_name=fast_svc
    )
    synthesis_agent = AgentBackend(
        default_backend, max_tokens=16384, slot_name="default", service_name=default_svc
    )
    research_agent = AgentBackend(
        tool_backend, max_tokens=16384, slot_name="tool", service_name=tool_svc
    )

    hooks = _build_hooks(extraction_agent, synthesis_agent, research_agent)
    pipeline = build_pipeline(secs, hooks)
    validate_capabilities(pipeline, stop_after=stop_after)

    # Load embedder for RAG index step
    embedders, embedder_defaults = load_embedders()
    embedder_name = embedder_defaults.get("default")
    embedder = embedders.get(embedder_name) if embedder_name else None

    model_name = getattr(default_backend, "model_name", str(default_backend))
    state = PipelineState(service_name=default_svc, model_name=str(model_name))

    ctx = StepContext(
        sections=secs,
        agents={"fast": extraction_agent, "default": synthesis_agent, "tool": research_agent},
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
    service_overrides: dict[str, str] | None = None,
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
                service_overrides=service_overrides,
                on_progress=on_progress,
            )
            backend.write_assay_md(pid, report)
            results.append({"paper_id": pid, "status": "ok", "error": None})
        except Exception as exc:
            logger.error("assay failed for %s: %s", pid, exc)
            results.append({"paper_id": pid, "status": "error", "error": str(exc)})

    return results
