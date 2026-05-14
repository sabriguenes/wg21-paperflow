#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async extractor pipeline for WG21 papers.

All LLM-facing text comes from ``dissect.md`` at runtime. This module
contains only structural orchestration: hook definitions and the
entry point. ``dissect.md`` is the upstream authority for pipeline
structure; this module conforms to it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic_ai import ModelRetry
from paperstore.backend import StorageBackend
from paperstore.progress import ProgressCallback
from paperstore.errors import MissingMetaError, MissingPaperMdError

from pipeline import (
    DEFAULT_MODEL_SLOTS,
    StepContext,
    StepHooks,
    StepSpec,
    WebResearcher,
    build_pipeline,
    dispatch,
    ensure_paper_md,
    load_sections,
    make_read_paper_tool,
    run_agent,
    run_task,
    sanitize_md,
)
from pipeline.errors import (
    PaperNotConvertedError,
    PaperNotFoundError,
    PromptFileError,
    StepError,
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
from dissect.render import render_report, render_trace

logger = logging.getLogger(__name__)

_REQUEST_LIMIT_DEDUP = 50
_REQUEST_LIMIT_PER_CLAIM = 12
_REQUEST_LIMIT_PER_CITATION = 12
_CLASSIFICATION_CRITICAL_GAP = "critical_gap"

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


# -- Output validators --------------------------------------------------------


def _validate_analysis_complete(ctx, output):
    if not output.analysis_complete:
        raise ModelRetry(
            "Analysis incomplete - set analysis_complete=True when "
            "the chunk has been fully analyzed"
        )
    return output


# -- Prepare hooks ------------------------------------------------------------


def _prepare_extract_chunks(state: PipelineState, ctx: StepContext) -> list[str]:
    assert state.chunks is not None
    prompt_body = ctx.sections.get(_STEP_1_EXTRACT, "")
    return [
        f"## Chunk\n\n{_number_lines(chunk)}\n\n"
        f"## Instructions\n\n{prompt_body}"
        for chunk in state.chunks
    ]


def _prepare_extract_factual_chunks(state: PipelineState, ctx: StepContext) -> list[str]:
    assert state.chunks is not None
    prompt_body = ctx.sections.get(_STEP_3_EXTRACT_FACTUAL, "")
    normative_questions: list[str] = []
    if state.claims:
        normative_questions = [
            c.question for c in state.claims if c.merged_into is None
        ]
    questions_text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(normative_questions))
    return [
        f"## Normative Claim Questions\n\n{questions_text}\n\n"
        f"## Chunk\n\n{_number_lines(chunk)}\n\n"
        f"## Instructions\n\n{prompt_body}"
        for chunk in state.chunks
    ]


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


def _extract_all(state: PipelineState, outputs: list[Any]) -> None:
    all_raw_claims = []
    all_raw_evidence = []
    all_raw_markers = []
    for output in outputs:
        all_raw_claims.extend(output.claims)
        all_raw_evidence.extend(output.evidence)
        all_raw_markers.extend(output.markers)
    state.raw_claims = all_raw_claims
    state.raw_evidence = all_raw_evidence
    state.raw_rhetoric = all_raw_markers
    assert state.paper_source is not None
    state.claims, state.next_uid = _promote_claims(all_raw_claims, state.paper_source, state.next_uid)
    state.evidence, state.next_uid = _promote_evidence(all_raw_evidence, state.paper_source, state.next_uid)
    state.rhetoric, state.next_uid = _promote_rhetoric(all_raw_markers, state.paper_source, state.next_uid)


def _extract_factual(state: PipelineState, outputs: list[Any]) -> None:
    all_raw: list[RawClaim] = []
    for output in outputs:
        all_raw.extend(output.claims)
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


# -- Custom step hooks --------------------------------------------------------


async def _custom_read(state: PipelineState, ctx: StepContext) -> None:
    assert state.paper_source is not None
    state.chunks = _chunk_paper(state.paper_source)
    state.citations = _extract_citations(state.paper_source)


async def _custom_dedup_claims(state: PipelineState, ctx: StepContext) -> None:
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
    result = await run_agent(
        ctx, ctx._current_spec, user_msg,
        request_limit=_REQUEST_LIMIT_DEDUP,
    )
    _extract_dedup_claims(state, result.output)


async def _custom_dedup_evidence(state: PipelineState, ctx: StepContext) -> None:
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
    result = await run_agent(
        ctx, ctx._current_spec, user_msg,
        request_limit=_REQUEST_LIMIT_DEDUP,
    )
    _extract_dedup_evidence(state, result.output)


async def _custom_dedup_factual(state: PipelineState, ctx: StepContext) -> None:
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
        result = await run_agent(
            ctx, ctx._current_spec, user_msg,
            request_limit=_REQUEST_LIMIT_DEDUP,
        )
        _extract_dedup_claims(state, result.output)
        factual = [c for c in state.claims if c.kind == "factual"]

    state.claims = normative + factual


def _citation_info(
    citations: list[CitationRef],
    backend: StorageBackend | None,
) -> dict[str, dict]:
    """Map cited paper_id -> {url, status, readable} from paperstore.

    Returns an empty dict when ``backend`` is None. Missing citations
    are silently omitted.
    """
    if backend is None:
        return {}
    out: dict[str, dict] = {}
    for cit in citations:
        result = backend.resolve_year_for_paper(cit.paper_id)
        if result is None:
            continue
        _, row = result
        url = row.url or ""
        readable = url.lower().endswith((".html", ".pdf", ".htm")) if url else False
        out[cit.paper_id] = {"url": url, "status": row.status, "readable": readable}
    return out


async def _custom_verify_citations(state: PipelineState, ctx: StepContext) -> None:
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
        resolved = DEFAULT_MODEL_SLOTS.get(model_slot, model_slot)

    alive_claims = [c for c in state.claims if c.merged_into is None]
    alive_evidence = [e for e in (state.evidence or []) if e.merged_into is None]

    citation_info = await asyncio.to_thread(
        _citation_info, state.citations, ctx.backend,
    )

    async def _one_citation(cit) -> CitationTaskOutput | None:
        pid_num = cit.paper_id
        primary_claims = [c for c in alive_claims if pid_num in c.text]
        primary_evidence = [e for e in alive_evidence if pid_num in e.text]
        secondary_questions = [c.question for c in alive_claims]

        info = citation_info.get(cit.paper_id)
        tools: dict[str, Any] = {"web_fetch": web_fetch_fn}

        if info and info["readable"]:
            md = await ensure_paper_md(cit.paper_id, ctx.backend)
            if md:
                read_fn = make_read_paper_tool(cit.paper_id, ctx.backend)
                tools[f"read_paper_{cit.paper_id.lower()}"] = read_fn

        if info is None:
            status_block = (
                "## Citation Status\n\n"
                'This paper is not in the local index. '
                'Report resolved: false, resolution_method: "not_found".\n\n'
            )
        elif not info["url"]:
            status_block = (
                "## Citation Status\n\n"
                'This paper is not in the local index. '
                'Report resolved: false, resolution_method: "not_found".\n\n'
            )
        elif not info["readable"]:
            status_block = (
                "## Citation Status\n\n"
                f'This paper exists but is in an unreadable format ({info["url"]}). '
                'Report resolved: true, quote_match: "unreadable".\n\n'
            )
        else:
            status_block = f'## Known URL\n\n{info["url"]}\n\n'

        user_msg = (
            f"## Citation\n\nPaper: {cit.paper_id} (cited {cit.count} times)\n\n"
            f"{status_block}"
            f"## Primary Claims\n\n"
            f"{json.dumps([c.model_dump() for c in primary_claims], ensure_ascii=False)}\n\n"
            f"## Primary Evidence\n\n"
            f"{json.dumps([e.model_dump() for e in primary_evidence], ensure_ascii=False)}\n\n"
            f"## Secondary Questions\n\n"
            f"{json.dumps(secondary_questions, ensure_ascii=False)}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        try:
            return await run_task(
                system_prompt=system,
                user_message=user_msg,
                output_type=CitationTaskOutput,
                label=f"Step 8 - Verify Citations ({cit.paper_id})",
                debug_log=ctx.debug_log if ctx.debug else None,
                tools=tools,
                model=resolved,
                request_limit=_REQUEST_LIMIT_PER_CITATION,
            )
        except Exception:
            logger.warning(
                "Citation verification failed for %s", cit.paper_id,
                exc_info=True,
            )
            return None

    results = await asyncio.gather(*[_one_citation(c) for c in state.citations])

    audit_entries = []
    evidence_items = list(state.external_evidence or [])
    for r in results:
        if r is None:
            continue
        audit_entries.append(r.audit)
        evidence_items.extend(r.evidence)

    state.citation_audit = audit_entries
    state.external_evidence = evidence_items


async def _custom_web_search(state: PipelineState, ctx: StepContext) -> None:
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

    deep_search_fn = ctx.tool_registry["deep_search"]
    web_fetch_fn = ctx.tool_registry["web_fetch"]

    assert ctx._current_spec is not None
    prompt_body = ctx.sections.get(_STEP_9_WEB_SEARCH, "")
    system = ctx.sections.get("System Prompt", "")
    model_slot = ctx._current_spec.meta.model_slot
    resolved = ctx.model_slots.get(model_slot)
    if resolved is None:
        resolved = DEFAULT_MODEL_SLOTS.get(model_slot, model_slot)

    async def _one_claim(claim) -> list:
        user_msg = (
            f"## Claim\n\n"
            f"{json.dumps(claim.model_dump(), ensure_ascii=False)}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        try:
            result = await run_task(
                system_prompt=system,
                user_message=user_msg,
                output_type=WebSearchOutput,
                label=f"Step 9 - Web Search (uid {claim.uid})",
                debug_log=ctx.debug_log if ctx.debug else None,
                tools={"deep_search": deep_search_fn, "web_fetch": web_fetch_fn},
                model=resolved,
                request_limit=_REQUEST_LIMIT_PER_CLAIM,
            )
            return [
                ee.model_copy(update={"claim_uid": claim.uid})
                for ee in result.external_evidence
            ]
        except Exception:
            logger.warning(
                "Web search failed for claim uid %d", claim.uid,
                exc_info=True,
            )
            return []

    results = await asyncio.gather(*[_one_claim(c) for c in claims_for_search])

    all_evidence = list(state.external_evidence or [])
    for batch in results:
        all_evidence.extend(batch)
    state.external_evidence = all_evidence


async def _custom_report(state: PipelineState, ctx: StepContext) -> None:
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

_HOOKS: dict[str, StepHooks] = {
    _STEP_0_READ: StepHooks(custom=_custom_read),

    _STEP_1_EXTRACT: StepHooks(
        output_type=ExtractAllOutput,
        prepare=_prepare_extract_chunks,
        extract=_extract_all,
        output_validator=_validate_analysis_complete,
        parallel=True,
    ),

    _STEP_2_DEDUP_CLAIMS: StepHooks(
        output_type=DedupGroupingOutput,
        custom=_custom_dedup_claims,
    ),

    _STEP_3_EXTRACT_FACTUAL: StepHooks(
        output_type=ExtractFactualOutput,
        prepare=_prepare_extract_factual_chunks,
        extract=_extract_factual,
        output_validator=_validate_analysis_complete,
        parallel=True,
    ),

    _STEP_4_DEDUP_FACTUAL: StepHooks(
        output_type=DedupGroupingOutput,
        custom=_custom_dedup_factual,
    ),

    _STEP_5_DEDUP_EVIDENCE: StepHooks(
        output_type=DedupGroupingOutput,
        custom=_custom_dedup_evidence,
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
        custom=_custom_verify_citations,
        guard=_guard_verify_citations,
    ),

    _STEP_9_WEB_SEARCH: StepHooks(
        custom=_custom_web_search,
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

    _STEP_13_REPORT: StepHooks(custom=_custom_report),
}


# -- Persistence callback -----------------------------------------------------


def _persist_step(
    spec: StepSpec,
    state: PipelineState,
    ctx: StepContext,
) -> None:
    """Persist step results to the backend database."""
    if ctx.backend is None:
        return
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
    slots = {**DEFAULT_MODEL_SLOTS, **(model_slots or {})}
    secs = load_sections("dissect", "dissect.md")

    if "System Prompt" not in secs:
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
        tool_reg["deep_search"] = researcher.deep_search
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

        debug_path = backend.get_debug_md_path(pid)
        if debug:
            debug_path.unlink(missing_ok=True)

        trace_path = backend.get_trace_md_path(pid) if (trace or stop_after is not None) else None
        dp = debug_path if debug else None

        await dispatch(
            pipeline, state, ctx,
            stop_after=stop_after,
            on_progress=on_progress,
            on_step_complete=lambda spec, st: _persist_step(spec, st, ctx),
            trace_path=trace_path,
            debug_path=dp,
            render_trace_fn=lambda st, step: render_trace(st, meta, step),
        )

    if stop_after is not None:
        return render_trace(state, meta, stop_after)

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
