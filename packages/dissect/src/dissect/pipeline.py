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
import re
from collections.abc import Callable
from typing import Any

from paperstore.backend import StorageBackend
from paperstore.progress import ProgressCallback
from paperstore.errors import MissingMetaError, MissingPaperMdError

from pipeline import (
    AgentBackend,
    ClassifierBackend,
    ModelBackend,
    StepContext,
    StepHooks,
    StepSpec,
    WebResearcher,
    build_pipeline,
    dispatch,
    ensure_paper_md,
    load_sections,
    load_services,
    make_read_paper_tool,
    resolve_slots,
    run_task,
    validate_capabilities,
)
from pipeline.errors import (
    PaperNotConvertedError,
    PaperNotFoundError,
    StepError,
)
from dissect.harness import (
    _blank_non_prose,
    _chunk_paper,
    _dedup_tier0,
    _dedup_tier1,
    _extract_citations,
    _number_lines,
    _promote_claims,
    _promote_evidence,
    _promote_rhetoric,
)
from dissect.shadow import shadow_groups
from dissect.models import (
    BatchVerifyOutput,
    CaputCausaeOutput,
    CitationRef,
    CitationTaskOutput,
    ClaimVerdict,
    DedupGroupingOutput,
    DisclaimPairOutput,
    ExtractClaimsOutput,
    ExtractEvidenceOutput,
    ExtractFactualOutput,
    ExtractRhetoricOutput,
    LoadBearingBinaryOutput,
    LoadBearingResult,
    PatternDetectionOutput,
    PipelineState,
    RawClaim,
    ResolveOutput,
    SentenceSpan,
    WebSearchOutput,
)
from dissect.render import render_report, render_trace
from dissect import triage
from pipeline.tools import wrap_source

logger = logging.getLogger(__name__)

_REQUEST_LIMIT_DEDUP = 50
_REQUEST_LIMIT_PER_CLAIM = 12
_REQUEST_LIMIT_PER_CITATION = 12
_REQUEST_LIMIT_PER_TASK = 5
_CLASSIFICATION_CRITICAL_GAP = "critical_gap"
_MAX_ITEM_FAILURE_FRACTION = 0.5

# Verify/Load-Bearing tunables.
#
# Each Verify LLM call carries at most VERIFY_BATCH_CLAIMS *
# VERIFY_BATCH_EVIDENCE propositions; interleaved by claim to dilute
# pattern bias. Disclaim checks fire per cosine-filtered claim pair.
# CENTRALITY_TOP_K is the lower bound on Tier 1 size so very small
# papers still get full LLM scrutiny; CENTRALITY_TOP_FRACTION scales
# with N for larger papers. Whichever is larger wins; the cutoff is
# generous so Tier 2 (auto-peripheral) genuinely is peripheral.
_VERIFY_BATCH_CLAIMS = 2
_VERIFY_BATCH_EVIDENCE = 5
_DISCLAIM_COSINE_THRESHOLD = 0.55
_CENTRALITY_TOP_K = 30
_CENTRALITY_TOP_FRACTION = 0.30
_CENTRALITY_EVIDENCE_THRESHOLD = 0.55
_CENTRALITY_PEER_THRESHOLD = 0.55

_STEP_0_READ = "0. Read"
_STEP_1_TAG_SENTENCES = "1. Tag Sentences"
_STEP_2_EXTRACT_CLAIMS = "2. Extract Claims"
_STEP_3_DEDUP_CLAIMS = "3. Dedup Claims"
_STEP_4_EXTRACT_EVIDENCE = "4. Extract Evidence"
_STEP_5_DEDUP_EVIDENCE = "5. Dedup Evidence"
_STEP_6_EXTRACT_FACTUAL = "6. Extract Factual"
_STEP_7_DEDUP_FACTUAL = "7. Dedup Factual Claims"
_STEP_8_EXTRACT_RHETORIC = "8. Extract Rhetoric"
_STEP_9_VERIFY = "9. Verify"
_STEP_10_LOAD_BEARING = "10. Load-Bearing"
_STEP_11_VERIFY_CITATIONS = "11. Verify Citations"
_STEP_12_WEB_SEARCH = "12. Web Search"
_STEP_13_RESOLVE = "13. Resolve External"
_STEP_14_CAPUT_CAUSAE = "14. Caput Causae"
_STEP_15_DETECT_PATTERNS = "15. Detect Patterns"
_STEP_16_REPORT = "16. Report"


# -- Output validators --------------------------------------------------------




# -- Prepare hooks ------------------------------------------------------------


def _chunk_block(chunk) -> str:
    """Return a line-numbered chunk wrapped as untrusted source."""
    return wrap_source(_number_lines(chunk))


def _prepare_extract_claims_chunks(state: PipelineState, ctx: StepContext) -> list[str]:
    """Render one Step 2 (Extract Claims) prompt per chunk.

    When Step 1 (Tag Sentences) has populated ``state.tagged_sentences``,
    each chunk is rendered with inline ``[TARGET]`` / ``[CONTEXT]``
    prefixes via ``_render_tagged_chunk`` and SKIP sentences dropped.
    When Step 1 was skipped or produced no tags (e.g. no classifier
    configured), fall back to the untagged ``_chunk_block`` render so
    the pipeline still works without the classifier.
    """
    from dissect.harness import _render_tagged_chunk, _split_tagged_by_chunk

    assert state.chunks is not None
    prompt_body = ctx.sections.get(_STEP_2_EXTRACT_CLAIMS, "")

    if state.tagged_sentences:
        tagged_by_chunk = _split_tagged_by_chunk(state.chunks, state.tagged_sentences)
        return [
            f"## Instructions\n\n{prompt_body}\n\n"
            f"## Input\n\n{wrap_source(_render_tagged_chunk(chunk, tagged))}"
            for chunk, tagged in zip(state.chunks, tagged_by_chunk)
        ]

    return [
        f"## Instructions\n\n{prompt_body}\n\n"
        f"## Input\n\n{_chunk_block(chunk)}"
        for chunk in state.chunks
    ]


def _prepare_extract_evidence_chunks(state: PipelineState, ctx: StepContext) -> list[str]:
    assert state.chunks is not None
    prompt_body = ctx.sections.get(_STEP_4_EXTRACT_EVIDENCE, "")
    return [
        f"## Instructions\n\n{prompt_body}\n\n"
        f"## Input\n\n{_chunk_block(chunk)}"
        for chunk in state.chunks
    ]


def _prepare_extract_rhetoric_chunks(state: PipelineState, ctx: StepContext) -> list[str]:
    assert state.chunks is not None
    prompt_body = ctx.sections.get(_STEP_8_EXTRACT_RHETORIC, "")
    return [
        f"## Instructions\n\n{prompt_body}\n\n"
        f"## Input\n\n{_chunk_block(chunk)}"
        for chunk in state.chunks
    ]


def _prepare_extract_factual_chunks(state: PipelineState, ctx: StepContext) -> list[str]:
    assert state.chunks is not None
    prompt_body = ctx.sections.get(_STEP_6_EXTRACT_FACTUAL, "")
    normative_questions: list[str] = []
    normative_texts: list[str] = []
    if state.normative_claims:
        survivors = [c for c in state.normative_claims if c.merged_into is None]
        normative_questions = [c.question for c in survivors]
        normative_texts = [c.text for c in survivors]
    questions_text = "\n".join(f"- {q}" for q in normative_questions)
    claims_text = "\n".join(f"- {t}" for t in normative_texts)
    return [
        f"## Instructions\n\n{prompt_body}\n\n"
        f"## Normative Claim Questions\n\n{wrap_source(questions_text)}\n\n"
        f"## Normative Claims (do not re-extract)\n\n{wrap_source(claims_text)}\n\n"
        f"## Input\n\n{_chunk_block(chunk)}"
        for chunk in state.chunks
    ]


def _prepare_dedup_claims(state: PipelineState, ctx: StepContext) -> str:
    assert state.normative_claims is not None
    survivors = [c for c in state.normative_claims if c.merged_into is None]
    prompt_body = ctx.sections.get(_STEP_3_DEDUP_CLAIMS, "")
    survivor_questions = json.dumps(
        [{"idx": i, "question": s.question} for i, s in enumerate(survivors)],
        ensure_ascii=False,
    )
    return (
        f"## Survivors\n\n{wrap_source(survivor_questions)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _prepare_resolve(state: PipelineState, ctx: StepContext) -> str:
    assert state.load_bearing_claims is not None and state.normative_claims is not None
    prompt_body = ctx.sections.get(_STEP_13_RESOLVE, "")
    return (
        f"## Load-Bearing Claims\n\n"
        f"{wrap_source(json.dumps([lb.model_dump() for lb in state.load_bearing_claims], ensure_ascii=False))}\n\n"
        f"## External Evidence\n\n"
        f"{wrap_source(json.dumps([ee.model_dump() for ee in (state.external_evidence or [])], ensure_ascii=False))}\n\n"
        f"## Claims\n\n"
        f"{wrap_source(json.dumps([c.model_dump() for c in state.normative_claims if c.merged_into is None], ensure_ascii=False))}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


# -- Extract hooks ------------------------------------------------------------


def _extract_claims(state: PipelineState, outputs: list[Any]) -> None:
    all_raw_claims: list[RawClaim] = []
    claim_chunk_indices: list[int] = []
    all_raw_facts: list[RawClaim] = []
    fact_chunk_indices: list[int] = []

    for chunk_idx, output in enumerate(outputs):
        for claim in output.claims:
            all_raw_claims.append(claim)
            claim_chunk_indices.append(chunk_idx)
        for fact in getattr(output, "facts", []):
            all_raw_facts.append(fact)
            fact_chunk_indices.append(chunk_idx)

    state.raw_claims = all_raw_claims
    assert state.paper_source is not None

    state.normative_claims, state.next_uid = _promote_claims(
        all_raw_claims, state.paper_source, state.next_uid,
        chunk_indices=claim_chunk_indices,
    )

    if all_raw_facts:
        funnel_facts, state.next_uid = _promote_claims(
            all_raw_facts, state.paper_source, state.next_uid,
            chunk_indices=fact_chunk_indices,
        )
        funnel_facts = [
            c.model_copy(update={"kind": "factual"})
            for c in funnel_facts
        ]
        state.normative_claims = list(state.normative_claims) + funnel_facts


def _extract_evidence(state: PipelineState, outputs: list[Any]) -> None:
    all_raw_evidence = []
    chunk_indices: list[int] = []
    for chunk_idx, output in enumerate(outputs):
        for ev in output.evidence:
            all_raw_evidence.append(ev)
            chunk_indices.append(chunk_idx)
    state.raw_evidence = all_raw_evidence
    assert state.paper_source is not None
    state.deduped_evidence, state.next_uid = _promote_evidence(
        all_raw_evidence, state.paper_source, state.next_uid,
        chunk_indices=chunk_indices,
    )


def _extract_rhetoric(state: PipelineState, outputs: list[Any]) -> None:
    all_raw_markers = []
    chunk_indices: list[int] = []
    for chunk_idx, output in enumerate(outputs):
        for marker in output.markers:
            all_raw_markers.append(marker)
            chunk_indices.append(chunk_idx)
    state.raw_rhetoric = all_raw_markers
    assert state.paper_source is not None
    state.rhetoric, state.next_uid = _promote_rhetoric(
        all_raw_markers, state.paper_source, state.next_uid,
        chunk_indices=chunk_indices,
    )


def _extract_factual(state: PipelineState, outputs: list[Any]) -> None:
    all_raw: list[RawClaim] = []
    chunk_indices: list[int] = []
    for chunk_idx, output in enumerate(outputs):
        for claim in output.claims:
            all_raw.append(claim)
            chunk_indices.append(chunk_idx)
    state.raw_factual = all_raw
    assert state.paper_source is not None
    factual_claims, state.next_uid = _promote_claims(
        all_raw, state.paper_source, state.next_uid,
        chunk_indices=chunk_indices,
    )
    factual_claims = [
        c.model_copy(update={"kind": "factual"}) if c.kind != "factual" else c
        for c in factual_claims
    ]
    if state.normative_claims is None:
        state.normative_claims = factual_claims
    else:
        state.normative_claims = list(state.normative_claims) + factual_claims


def _extract_dedup_claims(state: PipelineState, output: DedupGroupingOutput) -> None:
    assert state.normative_claims is not None
    claims = list(state.normative_claims)
    survivors = [c for c in claims if c.merged_into is None]

    # Only allow merges where questions share enough content words.
    # Prevents the LLM from grouping by topic when questions require
    # different evidence. Cost of a missed merge: one extra LLM call
    # downstream. Cost of a bad merge: a lost finding.
    from dissect.harness import dedup_overlap_candidates
    eligible = dedup_overlap_candidates(
        [s.question for s in survivors], min_overlap=2,
    )

    for group in output.groups:
        if len(group) < 2:
            continue
        valid = [i for i in group if 0 <= i < len(survivors)]
        # Filter group to only pairs that passed the overlap check.
        filtered = []
        for i in valid:
            if any(frozenset((i, j)) in eligible for j in valid if j != i):
                filtered.append(i)
        if len(filtered) < 2:
            continue
        valid = filtered
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
    state.normative_claims = claims


def _extract_resolve(state: PipelineState, output: ResolveOutput) -> None:
    state.load_bearing_claims = output.load_bearing_claims
    state.web_resolutions = output.web_resolutions


def _prepare_caput_causae(state: PipelineState, ctx: StepContext) -> str:
    assert state.load_bearing_claims is not None and state.normative_claims is not None
    prompt_body = ctx.sections.get(_STEP_14_CAPUT_CAUSAE, "")
    anchored_uids = {
        lb.claim_uid for lb in state.load_bearing_claims
        if lb.classification in ("anchored", "externally_anchored")
    }
    anchored_claims = [
        c for c in state.normative_claims
        if c.uid in anchored_uids and c.merged_into is None
    ]
    evidence_root_uids: set = set()
    if state.verdicts:
        for v in state.verdicts:
            if v.claim_uid in anchored_uids and v.status == "proven" and v.related_uid >= 0:
                evidence_root_uids.add(v.related_uid)
    evidence_items = []
    if state.deduped_evidence:
        evidence_items = [
            e for e in state.deduped_evidence
            if e.uid in evidence_root_uids and e.merged_into is None
        ]
    return (
        f"## Anchored Claims\n\n"
        f"{wrap_source(json.dumps([c.model_dump() for c in anchored_claims], ensure_ascii=False))}\n\n"
        f"## Evidence Roots\n\n"
        f"{wrap_source(json.dumps([e.model_dump() for e in evidence_items], ensure_ascii=False))}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _extract_caput_causae(state: PipelineState, output: CaputCausaeOutput) -> None:
    state.caput_causae = output.caput_causae


def _prepare_detect_patterns(state: PipelineState, ctx: StepContext) -> str:
    assert state.rhetoric is not None and state.normative_claims is not None
    prompt_body = ctx.sections.get(_STEP_15_DETECT_PATTERNS, "")
    markers_json = json.dumps(
        [m.model_dump() for m in state.rhetoric],
        ensure_ascii=False,
    )
    claims_json = json.dumps(
        [c.model_dump() for c in state.normative_claims if c.merged_into is None],
        ensure_ascii=False,
    )
    thesis_preamble = ""
    if state.caput_causae is not None:
        thesis_preamble = f"The paper's central thesis: {state.caput_causae.thesis}\n\n"
    return (
        f"{thesis_preamble}"
        f"## Rhetorical Markers\n\n{wrap_source(markers_json)}\n\n"
        f"## Claims\n\n{wrap_source(claims_json)}\n\n"
        f"## Instructions\n\n{prompt_body}"
    )


def _extract_detect_patterns(state: PipelineState, output: PatternDetectionOutput) -> None:
    state.marker_patterns = output


# -- Custom step hooks --------------------------------------------------------


async def _custom_read(state: PipelineState, ctx: StepContext) -> None:
    assert state.paper_source is not None
    _, state.blanked_lines = _blank_non_prose(state.paper_source)
    state.chunks = _chunk_paper(state.paper_source)
    state.citations = _extract_citations(state.paper_source)


async def _custom_tag_sentences(state: PipelineState, ctx: StepContext) -> None:
    """Step 1: decompose each chunk into sentences and tag each as
    TARGET / CONTEXT / SKIP via the configured classifier.

    Reads ``ctx.classifiers["selector"]``. Writes
    ``state.tagged_sentences``. Read by the renumbered Step 2
    (Extract Claims) through ``_prepare_extract_claims_chunks``.

    When ``--debug`` is on, appends a per-sentence detail block to
    ``ctx.debug_log`` via ``render_debug_tag_sentences`` (parallel to
    the LLM-step ``render_debug_md`` calls in
    ``pipeline.model_backends``). The trace renderer emits a
    summary-only block (counts per tag + classifier + thresholds).
    """
    from dissect.harness import (
        _decompose_sentences,
        _tag_sentences,
        _DEFAULT_SKIP_MARGIN,
        _DEFAULT_TARGET_MARGIN,
        _TAG_SKIP_LABEL,
        _TAG_TARGET_LABEL,
    )

    assert state.chunks is not None

    classifier = ctx.classifiers.get("selector")
    if classifier is None:
        # No classifier configured. Leave tagged_sentences as None so
        # _prepare_extract_claims_chunks falls back to untagged
        # rendering. This is the graceful degradation path for callers
        # that haven't migrated to the Step 1 abstraction yet.
        return

    # Honor ``--chunk N``: when the orchestrator restricts downstream
    # parallel steps to one chunk, Step 1 must also limit work to that
    # chunk. Otherwise interactive iteration loops on ``--step 1
    # --chunk 0`` would still pay the cost of classifying every
    # sentence in the paper.
    chunks_to_tag = state.chunks
    if ctx.chunk_index is not None:
        if 0 <= ctx.chunk_index < len(state.chunks):
            chunks_to_tag = [state.chunks[ctx.chunk_index]]
        else:
            chunks_to_tag = []

    # One batched classify() over every span from every chunk. Both
    # ClassifierBackend implementations batch internally (HF pipeline
    # batch_size, CrossEncoder.predict batch_size), so one large call
    # fills their batches better than N small calls. Concurrent calls
    # would serialize at the model anyway (single instance per
    # process). SentenceSpan carries absolute line numbers from
    # _decompose_sentences, so cross-chunk merging is safe.
    all_spans: list[SentenceSpan] = []
    for chunk in chunks_to_tag:
        all_spans.extend(_decompose_sentences(chunk))
    state.tagged_sentences = _tag_sentences(all_spans, classifier)
    all_tagged = state.tagged_sentences

    if ctx.debug and ctx.debug_log is not None:
        from dissect.render import render_debug_tag_sentences
        ctx.debug_log.append(render_debug_tag_sentences(
            all_tagged,
            classifier_name=getattr(classifier, "_slot_name", "selector"),
            classifier_model=getattr(classifier, "model_id", "unknown"),
            device=getattr(classifier, "device", "cpu"),
            target_label=_TAG_TARGET_LABEL,
            skip_label=_TAG_SKIP_LABEL,
            target_margin=_DEFAULT_TARGET_MARGIN,
            skip_margin=_DEFAULT_SKIP_MARGIN,
            multi_label=True,
        ))


async def _custom_dedup_claims(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1-2, plus observational embedding shadow."""
    assert state.normative_claims is not None
    claims = _dedup_tier0(state.normative_claims)
    claims = _dedup_tier1(claims)
    state.normative_claims = claims

    survivors = [c for c in claims if c.merged_into is None]

    # Shadow runs over post-Tier-1 survivors (before Tier 2 lexical
    # grouping) so we can see every candidate the embeddings propose.
    # Observational: the trace renders proposals but nothing is merged.
    sg = shadow_groups([s.question for s in survivors])
    state.shadow_claim_groups = [[survivors[i].uid for i in g] for g in sg]

    if len(survivors) <= 1:
        return

    from dissect.harness import _dedup_tier2_groups
    groups = _dedup_tier2_groups([s.question for s in survivors])
    if groups:
        _extract_dedup_claims(state, DedupGroupingOutput(groups=groups))

    # LLM tier-2 disabled for determinism. Restore by uncommenting:
    # assert ctx._current_spec is not None
    # user_msg = _prepare_dedup_claims(state, ctx)
    # result = await run_agent(
    #     ctx, ctx._current_spec, user_msg,
    #     request_limit=_REQUEST_LIMIT_DEDUP,
    # )
    # _extract_dedup_claims(state, result.output)


async def _custom_dedup_evidence(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1, plus observational embedding shadow.

    Tier 2 was removed: the prior word-overlap-on-supports heuristic
    produced lossy semantic merges (15-of-15 problematic on P4003R3).
    Real semantic dedup needs embeddings, which now run as the shadow.
    """
    assert state.deduped_evidence is not None
    evidence = _dedup_tier0(state.deduped_evidence)
    evidence = _dedup_tier1(evidence)
    state.deduped_evidence = evidence

    survivors = [e for e in evidence if e.merged_into is None]
    sg = shadow_groups([s.text for s in survivors])
    state.shadow_evidence_groups = [[survivors[i].uid for i in g] for g in sg]


async def _custom_dedup_factual(state: PipelineState, ctx: StepContext) -> None:
    """Deterministic tiers 0-1-2 on factual claims only (no LLM)."""
    assert state.normative_claims is not None
    normative = [c for c in state.normative_claims if c.kind != "factual"]
    factual = [c for c in state.normative_claims if c.kind == "factual"]
    if not factual:
        return

    factual = _dedup_tier0(factual)
    factual = _dedup_tier1(factual)

    survivors = [c for c in factual if c.merged_into is None]
    if len(survivors) > 1:
        from dissect.harness import _dedup_tier2_groups
        groups = _dedup_tier2_groups([s.question for s in survivors])
        if groups:
            # _extract_dedup_claims rebuilds survivors from state.normative_claims.
            # Put factual first so survivor indices 0..N-1 align with our
            # factual-only groups; final reorder happens below.
            state.normative_claims = factual + normative
            _extract_dedup_claims(state, DedupGroupingOutput(groups=groups))
            factual = [c for c in state.normative_claims if c.kind == "factual"]

    # LLM tier-2 disabled for determinism. Restore by uncommenting:
    # if len(survivors) > 1:
    #     assert ctx._current_spec is not None
    #     prompt_body = ctx.sections.get(_STEP_7_DEDUP_FACTUAL, "")
    #     survivor_questions = json.dumps(
    #         [{"idx": i, "question": s.question} for i, s in enumerate(survivors)],
    #         ensure_ascii=False,
    #     )
    #     user_msg = (
    #         f"## Survivors\n\n{wrap_source(survivor_questions)}\n\n"
    #         f"## Instructions\n\n{prompt_body}"
    #     )
    #     result = await run_agent(
    #         ctx, ctx._current_spec, user_msg,
    #         request_limit=_REQUEST_LIMIT_DEDUP,
    #     )
    #     _extract_dedup_claims(state, result.output)
    #     factual = [c for c in state.normative_claims if c.kind == "factual"]

    state.normative_claims = normative + factual


_SUB_PROMPT_RE = re.compile(
    r"^###\s+Sub-prompt:\s+(?P<name>[^\n]+?)\s*\n(?P<body>.*?)(?=^###\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _split_sub_prompts(section_body: str) -> dict[str, str]:
    """Extract ``### Sub-prompt: Name`` H3 blocks from a step body.

    Returns ``{name: body}``. Names are case-sensitive and must match
    the ``dissect.md`` headings exactly. Bodies are stripped of leading
    and trailing whitespace. Missing names produce no entry.
    """
    return {
        m.group("name").strip(): m.group("body").strip()
        for m in _SUB_PROMPT_RE.finditer(section_body)
    }


def _get_agent(ctx: StepContext, slot: str = "default") -> AgentBackend:
    """Get the agent for a named slot from the step context."""
    return ctx.agents[slot]


_TRAILING_PUNCT = ".,;:!?"


def _normalize(s: str) -> str:
    """Return ``s`` normalized for self-pair identity comparison.

    Normalization applied:

    - ``str.strip`` of outer whitespace,
    - ``str.casefold`` for case-insensitive equality (handles Unicode
      better than ``.lower()``),
    - internal whitespace runs collapsed to a single space,
    - trailing terminal punctuation (``.,;:!?``) stripped.

    This is intentionally conservative: it only catches exact-string
    matches and whitespace / case / trailing-punctuation variants.
    Wider semantic equivalence is left to the model-side rule in
    ``dissect.md`` (``Sub-prompt: Batched Verify``).
    """
    out = " ".join(s.strip().casefold().split())
    while out and out[-1] in _TRAILING_PUNCT:
        out = out[:-1].rstrip()
    return out


async def _custom_verify(state: PipelineState, ctx: StepContext) -> None:
    """Verify claims via per-batch propositions and per-pair disclaim checks.

    Phases (all serial per D11):
      1. Triage: embed alive claims and evidence; cosine matrices;
         per-claim top-K evidence; cosine-thresholded disclaim pairs.
      2. Centrality: rank claims by graph in-degree + cosine prominence;
         split into Tier 1 (full LLM scrutiny) and Tier 2 (auto-unproven).
      3. Batched Verify: small LLM calls evaluating up to
         ``VERIFY_BATCH_CLAIMS * VERIFY_BATCH_EVIDENCE`` interleaved
         (claim, evidence) propositions. Tier 1 only.
      4. Detect Disclaim: one LLM call per candidate pair returning
         the propositional relation between two claims.
      5. Aggregate: collapse to ``ClaimVerdict`` rows, default
         ``unproven`` for any claim with no other verdict, sort,
         write ``state.verdicts``.

    The triage and centrality phases populate diagnostic state
    (``centrality_scores``, ``triaged_evidence``, ``disclaim_candidates``,
    ``verify_batch_count``, ``self_pair_dropped``) so the trace can show
    what the embedding pre-filter did before any LLM call.

    Between pair construction and batching, the harness drops any
    ``(claim_uid, evidence_uid)`` whose normalized text is identical:
    upstream Steps 3 and 5 sometimes capture the same source sentence
    as both an Evidence item and a Factual claim, which would otherwise
    produce a self-prove verdict at Step 9. ``self_pair_dropped``
    counts the drops; ``dissect.md`` carries a model-side backstop for
    near-identical wording the normalizer cannot reduce to identity.
    """
    assert state.normative_claims is not None
    alive_claims = [c for c in state.normative_claims if c.merged_into is None]
    alive_evidence = [
        e for e in (state.deduped_evidence or []) if e.merged_into is None
    ]

    if not alive_claims:
        state.verdicts = []
        state.centrality_scores = {}
        state.triaged_evidence = {}
        state.disclaim_candidates = []
        state.verify_batch_count = 0
        state.self_pair_dropped = 0
        return

    # Phase 1: Triage.
    claim_vecs = await asyncio.to_thread(triage.embed_claims, alive_claims)
    evid_vecs = await asyncio.to_thread(triage.embed_evidence, alive_evidence)
    claim_cos = triage.cosine_matrix(claim_vecs, claim_vecs)
    evid_cos = triage.cosine_matrix(claim_vecs, evid_vecs)

    claim_uids = [c.uid for c in alive_claims]
    evid_uids = [e.uid for e in alive_evidence]

    state.triaged_evidence = triage.top_k_per_row(
        evid_cos, claim_uids, evid_uids, k=_VERIFY_BATCH_EVIDENCE,
    )
    state.disclaim_candidates = triage.above_threshold_pairs(
        claim_cos, claim_uids, threshold=_DISCLAIM_COSINE_THRESHOLD,
    )

    # Phase 2: Centrality.
    state.centrality_scores = triage.centrality_scores(
        alive_claims, claim_cos, evid_cos,
        evidence_threshold=_CENTRALITY_EVIDENCE_THRESHOLD,
        peer_threshold=_CENTRALITY_PEER_THRESHOLD,
    )
    tier1_uids, _tier2_uids = triage.tier_split(
        state.centrality_scores,
        top_k=_CENTRALITY_TOP_K,
        top_fraction=_CENTRALITY_TOP_FRACTION,
    )
    tier1_set = set(tier1_uids)

    # Phase 3: Batched Verify.
    sub_prompts = _split_sub_prompts(ctx.sections.get(_STEP_9_VERIFY, ""))
    verify_prompt = sub_prompts.get("Batched Verify", "")
    disclaim_prompt = sub_prompts.get("Detect Disclaim", "")

    claim_by_uid = {c.uid: c for c in alive_claims}
    evid_by_uid = {e.uid: e for e in alive_evidence}
    system = ctx.system_prompt_for(ctx._current_spec) if ctx._current_spec else ""
    synthesis_agent = _get_agent(ctx, "default")

    pairs: list[tuple[int, int]] = []
    for cuid in sorted(tier1_set):
        for euid in state.triaged_evidence.get(cuid, []):
            pairs.append((cuid, euid))

    before = len(pairs)
    pairs = [
        (cuid, euid) for cuid, euid in pairs
        if _normalize(claim_by_uid[cuid].text) != _normalize(evid_by_uid[euid].text)
    ]
    state.self_pair_dropped = before - len(pairs)

    batches = triage.interleave_propositions(
        pairs,
        batch_claims=_VERIFY_BATCH_CLAIMS,
        batch_evidence=_VERIFY_BATCH_EVIDENCE,
    )
    state.verify_batch_count = len(batches)

    judgements: list = []
    for batch_idx, batch in enumerate(batches):
        prop_payload = [
            {
                "claim_uid": cuid,
                "claim_text": claim_by_uid[cuid].text,
                "claim_question": claim_by_uid[cuid].question,
                "evidence_uid": euid,
                "evidence_text": evid_by_uid[euid].text,
                "evidence_supports": list(evid_by_uid[euid].supports or []),
            }
            for cuid, euid in batch
        ]
        user_msg = (
            f"## Propositions\n\n{wrap_source(json.dumps(prop_payload, ensure_ascii=False))}\n\n"
            f"## Instructions\n\n{verify_prompt}"
        )
        try:
            batch_out = await run_task(
                synthesis_agent,
                system,
                user_msg,
                BatchVerifyOutput,
                label=f"9. Verify (batch {batch_idx + 1}/{len(batches)})",
                debug_log=ctx.debug_log if ctx.debug else None,
            )
        except Exception:
            logger.warning(
                "Verify batch %d/%d failed; treating as unrelated.",
                batch_idx + 1, len(batches),
                exc_info=True,
            )
            continue
        judgements.extend(batch_out.judgements)

    # Phase 4: Detect Disclaim.
    disclaim_outputs: list[DisclaimPairOutput] = []
    for a_uid, b_uid in state.disclaim_candidates:
        if a_uid not in claim_by_uid or b_uid not in claim_by_uid:
            continue
        ca = claim_by_uid[a_uid]
        cb = claim_by_uid[b_uid]
        pair_payload = {
            "claim_a": {
                "uid": a_uid, "text": ca.text, "question": ca.question,
            },
            "claim_b": {
                "uid": b_uid, "text": cb.text, "question": cb.question,
            },
        }
        user_msg = (
            f"## Pair\n\n{wrap_source(json.dumps(pair_payload, ensure_ascii=False))}\n\n"
            f"## Instructions\n\n{disclaim_prompt}"
        )
        try:
            pair_out = await run_task(
                synthesis_agent,
                system,
                user_msg,
                DisclaimPairOutput,
                label=f"9. Detect Disclaim ({a_uid} vs {b_uid})",
                debug_log=ctx.debug_log if ctx.debug else None,
            )
        except Exception:
            logger.warning(
                "Disclaim detection failed for pair (%d, %d).", a_uid, b_uid,
                exc_info=True,
            )
            continue
        # Force the model's reported uids to the canonical input order
        # so a misreported uid cannot silently corrupt aggregation.
        disclaim_outputs.append(
            pair_out.model_copy(update={
                "claim_a_uid": a_uid, "claim_b_uid": b_uid,
            })
        )

    # Phase 5: Aggregate.
    verdicts: list[ClaimVerdict] = []
    for j in judgements:
        if j.claim_uid not in claim_by_uid or j.evidence_uid not in evid_by_uid:
            continue
        if j.verdict == "support":
            verdicts.append(ClaimVerdict(
                claim_uid=j.claim_uid,
                related_uid=j.evidence_uid,
                status="proven",
            ))
        elif j.verdict == "contradict":
            verdicts.append(ClaimVerdict(
                claim_uid=j.claim_uid,
                related_uid=j.evidence_uid,
                status="disproven",
            ))

    for r in disclaim_outputs:
        if r.relation == "a_disclaims_b":
            verdicts.append(ClaimVerdict(
                claim_uid=r.claim_b_uid,
                related_uid=r.claim_a_uid,
                status="disclaimed",
            ))
        elif r.relation == "b_disclaims_a":
            verdicts.append(ClaimVerdict(
                claim_uid=r.claim_a_uid,
                related_uid=r.claim_b_uid,
                status="disclaimed",
            ))
        elif r.relation == "mutual":
            verdicts.append(ClaimVerdict(
                claim_uid=r.claim_a_uid,
                related_uid=r.claim_b_uid,
                status="disclaimed",
            ))
            verdicts.append(ClaimVerdict(
                claim_uid=r.claim_b_uid,
                related_uid=r.claim_a_uid,
                status="disclaimed",
            ))

    covered = {v.claim_uid for v in verdicts}
    for c in alive_claims:
        if c.uid not in covered:
            verdicts.append(ClaimVerdict(
                claim_uid=c.uid, related_uid=-1, status="unproven",
            ))

    verdicts.sort(key=lambda v: (v.claim_uid, v.related_uid, v.status))
    state.verdicts = verdicts


def _classify_provisional(
    claim_uid: int,
    verdicts_by_claim: dict[int, list[ClaimVerdict]],
) -> str:
    """Map a claim's verdicts to a provisional load-bearing classification.

    Returns one of ``conflicted``, ``anchored``, or ``critical_gap``.
    The final classification may be downgraded to ``peripheral`` or
    upgraded to ``depends_on_contested`` after the per-claim LLM and
    the dependency sweep.
    """
    vs = verdicts_by_claim.get(claim_uid, [])
    supportive = any(v.status in ("proven", "implied") for v in vs)
    adverse = any(v.status in ("disproven", "disclaimed") for v in vs)
    if supportive and adverse:
        return "conflicted"
    if supportive:
        return "anchored"
    return "critical_gap"


async def _custom_load_bearing(state: PipelineState, ctx: StepContext) -> None:
    """Classify each claim deterministically, then per-claim LLM binary.

    Phase 1: provisional classification from Step 9 verdicts plus
    ``depends_on`` chains (pure Python).
    Phase 2: per-claim LLM call (Tier 1 only) deciding whether the
    claim is structurally load-bearing. Tier 2 claims auto-classified
    as ``peripheral`` with no LLM call.

    The deterministic + LLM split keeps the per-claim binary
    cheap (one short prompt per claim, bounded by Tier 1 size) while
    preserving the classification semantics used downstream by Steps
    10-15.
    """
    assert state.normative_claims is not None
    alive_claims = [c for c in state.normative_claims if c.merged_into is None]
    verdicts = state.verdicts or []

    if not alive_claims:
        state.load_bearing_claims = []
        return

    verdicts_by_claim: dict[int, list[ClaimVerdict]] = {}
    for v in verdicts:
        verdicts_by_claim.setdefault(v.claim_uid, []).append(v)

    provisional: dict[int, str] = {
        c.uid: _classify_provisional(c.uid, verdicts_by_claim)
        for c in alive_claims
    }

    # Re-derive Tier 1 deterministically from the centrality scores
    # written by Step 9 so Step 10 sees the exact same partition. When
    # Step 9 ran in fallback mode (no embeddings), every claim falls
    # into Tier 1 by the generous-cutoff rule.
    tier1_uids, _tier2 = triage.tier_split(
        state.centrality_scores or {c.uid: 1.0 for c in alive_claims},
        top_k=_CENTRALITY_TOP_K,
        top_fraction=_CENTRALITY_TOP_FRACTION,
    )
    tier1_set = set(tier1_uids)

    dependents_by_uid: dict[int, list[int]] = {c.uid: [] for c in alive_claims}
    for c in alive_claims:
        for dep_uid in c.depends_on or []:
            if dep_uid in dependents_by_uid:
                dependents_by_uid[dep_uid].append(c.uid)

    sub_prompts = _split_sub_prompts(ctx.sections.get(_STEP_10_LOAD_BEARING, ""))
    binary_prompt = sub_prompts.get("Load-Bearing Binary", "")

    system = ctx.system_prompt_for(ctx._current_spec) if ctx._current_spec else ""
    synthesis_agent = _get_agent(ctx, "default")
    caput_hint = ""
    if state.caput_causae is not None:
        caput_hint = state.caput_causae.thesis

    claim_text_by_uid = {c.uid: c.text for c in alive_claims}

    load_bearing_by_uid: dict[int, bool] = {}
    for c in alive_claims:
        if c.uid not in tier1_set:
            load_bearing_by_uid[c.uid] = False
            continue
        deps_text = [
            claim_text_by_uid.get(u, "") for u in dependents_by_uid[c.uid]
        ]
        payload = {
            "claim_uid": c.uid,
            "claim_text": c.text,
            "claim_question": c.question,
            "section": c.section,
            "dependent_claims": deps_text,
            "provisional_classification": provisional[c.uid],
            "central_thesis_hint": caput_hint,
        }
        user_msg = (
            f"## Claim\n\n{wrap_source(json.dumps(payload, ensure_ascii=False))}\n\n"
            f"## Instructions\n\n{binary_prompt}"
        )
        try:
            out = await run_task(
                synthesis_agent,
                system,
                user_msg,
                LoadBearingBinaryOutput,
                label=f"10. Load-Bearing Binary (uid {c.uid})",
                debug_log=ctx.debug_log if ctx.debug else None,
            )
        except Exception:
            # Bias toward load-bearing on failure so a missing per-claim
            # decision never silently demotes a claim out of downstream
            # scrutiny.
            logger.warning(
                "Load-Bearing Binary failed for claim uid %d; defaulting to True.",
                c.uid, exc_info=True,
            )
            load_bearing_by_uid[c.uid] = True
            continue
        load_bearing_by_uid[c.uid] = bool(out.load_bearing)

    # Compose final classifications.
    final: dict[int, str] = {}
    for c in alive_claims:
        if not load_bearing_by_uid.get(c.uid, False):
            final[c.uid] = "peripheral"
            continue
        final[c.uid] = provisional[c.uid]

    # depends_on_contested: an otherwise-anchored claim that points to
    # a contested or critically-gapped dependency carries the
    # contestation forward. Done after the per-claim pass so the source
    # claim's own classification is settled first.
    for c in alive_claims:
        if final[c.uid] != "anchored":
            continue
        for dep_uid in c.depends_on or []:
            dep_cls = final.get(dep_uid)
            if dep_cls in ("conflicted", "critical_gap"):
                final[c.uid] = "depends_on_contested"
                break

    state.load_bearing_claims = [
        LoadBearingResult(
            claim_uid=c.uid,
            dependents=sorted(dependents_by_uid[c.uid]),
            classification=final[c.uid],  # type: ignore[arg-type]
        )
        for c in sorted(alive_claims, key=lambda x: x.uid)
    ]


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
    assert state.citations is not None and state.normative_claims is not None

    web_fetch_fn = ctx.tool_registry["web_fetch"]

    assert ctx._current_spec is not None
    prompt_body = ctx.sections.get(_STEP_11_VERIFY_CITATIONS, "")
    system = ctx.system_prompt_for(ctx._current_spec)
    research_agent = _get_agent(ctx, "tool")

    alive_claims = [c for c in state.normative_claims if c.merged_into is None]
    alive_evidence = [e for e in (state.deduped_evidence or []) if e.merged_into is None]

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
            f"{wrap_source(json.dumps([c.model_dump() for c in primary_claims], ensure_ascii=False))}\n\n"
            f"## Primary Evidence\n\n"
            f"{wrap_source(json.dumps([e.model_dump() for e in primary_evidence], ensure_ascii=False))}\n\n"
            f"## Secondary Questions\n\n"
            f"{wrap_source(json.dumps(secondary_questions, ensure_ascii=False))}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        try:
            return await run_task(
                research_agent,
                system,
                user_msg,
                CitationTaskOutput,
                tools=tools,
                label=f"11. Verify Citations ({cit.paper_id})",
                debug_log=ctx.debug_log if ctx.debug else None,
            )
        except Exception:
            # Per-citation failures are thresholded after all tasks complete.
            logger.warning(
                "Citation verification failed for %s", cit.paper_id,
                exc_info=True,
            )
            return None

    results = await asyncio.gather(*[_one_citation(c) for c in state.citations])
    failed = sum(1 for r in results if r is None)
    if results and failed / len(results) > _MAX_ITEM_FAILURE_FRACTION:
        raise StepError(
            ctx._current_spec.meta.number,
            ctx._current_spec.meta.name,
            RuntimeError(
                f"Citation verification failed for {failed}/{len(results)} "
                f"citations, above threshold {_MAX_ITEM_FAILURE_FRACTION:.0%}."
            ),
        )

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
    assert state.normative_claims is not None and state.load_bearing_claims is not None

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
            (c for c in state.normative_claims if c.uid == lb.claim_uid and c.merged_into is None),
            None,
        )
        if claim:
            claims_for_search.append(claim)

    if not claims_for_search:
        return

    deep_search_fn = ctx.tool_registry["deep_search"]
    web_fetch_fn = ctx.tool_registry["web_fetch"]

    assert ctx._current_spec is not None
    prompt_body = ctx.sections.get(_STEP_12_WEB_SEARCH, "")
    system = ctx.system_prompt_for(ctx._current_spec)
    research_agent = _get_agent(ctx, "tool")

    async def _one_claim(claim) -> tuple[bool, list]:
        user_msg = (
            f"## Claim\n\n"
            f"{wrap_source(json.dumps(claim.model_dump(), ensure_ascii=False))}\n\n"
            f"## Instructions\n\n{prompt_body}"
        )
        try:
            result = await run_task(
                research_agent,
                system,
                user_msg,
                WebSearchOutput,
                tools={"deep_search": deep_search_fn, "web_fetch": web_fetch_fn},
                label=f"12. Web Search (uid {claim.uid})",
                debug_log=ctx.debug_log if ctx.debug else None,
            )
            return True, [
                ee.model_copy(update={"claim_uid": claim.uid})
                for ee in result.external_evidence
            ]
        except Exception:
            # Per-claim search failures are thresholded after all tasks complete.
            logger.warning(
                "Web search failed for claim uid %d", claim.uid,
                exc_info=True,
            )
            return False, []

    results = await asyncio.gather(*[_one_claim(c) for c in claims_for_search])
    failed = sum(1 for ok, _ in results if not ok)
    if results and failed / len(results) > _MAX_ITEM_FAILURE_FRACTION:
        raise StepError(
            ctx._current_spec.meta.number,
            ctx._current_spec.meta.name,
            RuntimeError(
                f"Web search failed for {failed}/{len(results)} claims, "
                f"above threshold {_MAX_ITEM_FAILURE_FRACTION:.0%}."
            ),
        )

    all_evidence = list(state.external_evidence or [])
    for _, batch in results:
        all_evidence.extend(batch)
    state.external_evidence = all_evidence


async def _custom_report(state: PipelineState, ctx: StepContext) -> None:
    assert state.normative_claims is not None
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


def _build_hooks(
    extraction_agent: AgentBackend,
    synthesis_agent: AgentBackend,
    research_agent: AgentBackend,
) -> dict[str, StepHooks]:
    """Build the step hooks dict with agents assigned."""
    return {
        _STEP_0_READ: StepHooks(custom=_custom_read),

        _STEP_1_TAG_SENTENCES: StepHooks(custom=_custom_tag_sentences),

        _STEP_2_EXTRACT_CLAIMS: StepHooks(
            agent=extraction_agent,
            output_type=ExtractClaimsOutput,
            prepare=_prepare_extract_claims_chunks,
            extract=_extract_claims,
            parallel=True,
        ),

        _STEP_3_DEDUP_CLAIMS: StepHooks(
            output_type=DedupGroupingOutput,
            custom=_custom_dedup_claims,
        ),

        _STEP_4_EXTRACT_EVIDENCE: StepHooks(
            agent=extraction_agent,
            output_type=ExtractEvidenceOutput,
            prepare=_prepare_extract_evidence_chunks,
            extract=_extract_evidence,
            parallel=True,
        ),

        _STEP_5_DEDUP_EVIDENCE: StepHooks(
            output_type=DedupGroupingOutput,
            custom=_custom_dedup_evidence,
        ),

        _STEP_6_EXTRACT_FACTUAL: StepHooks(
            agent=extraction_agent,
            output_type=ExtractFactualOutput,
            prepare=_prepare_extract_factual_chunks,
            extract=_extract_factual,
            parallel=True,
        ),

        _STEP_7_DEDUP_FACTUAL: StepHooks(
            output_type=DedupGroupingOutput,
            custom=_custom_dedup_factual,
        ),

        _STEP_8_EXTRACT_RHETORIC: StepHooks(
            agent=extraction_agent,
            output_type=ExtractRhetoricOutput,
            prepare=_prepare_extract_rhetoric_chunks,
            extract=_extract_rhetoric,
            parallel=True,
        ),

        _STEP_9_VERIFY: StepHooks(agent=synthesis_agent, custom=_custom_verify),

        _STEP_10_LOAD_BEARING: StepHooks(agent=synthesis_agent, custom=_custom_load_bearing),

        _STEP_11_VERIFY_CITATIONS: StepHooks(
            agent=research_agent,
            custom=_custom_verify_citations,
            guard=_guard_verify_citations,
        ),

        _STEP_12_WEB_SEARCH: StepHooks(
            agent=research_agent,
            custom=_custom_web_search,
            guard=_guard_web_search,
        ),

        _STEP_13_RESOLVE: StepHooks(
            agent=synthesis_agent,
            output_type=ResolveOutput,
            prepare=_prepare_resolve,
            extract=_extract_resolve,
            guard=_guard_resolve,
            request_limit=15,
        ),

        _STEP_14_CAPUT_CAUSAE: StepHooks(
            agent=synthesis_agent,
            output_type=CaputCausaeOutput,
            prepare=_prepare_caput_causae,
            extract=_extract_caput_causae,
            guard=_guard_caput_causae,
        ),

        _STEP_15_DETECT_PATTERNS: StepHooks(
            agent=synthesis_agent,
            output_type=PatternDetectionOutput,
            prepare=_prepare_detect_patterns,
            extract=_extract_detect_patterns,
            guard=_guard_detect_patterns,
        ),

        _STEP_16_REPORT: StepHooks(custom=_custom_report),
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
    elif step_name == _STEP_3_DEDUP_CLAIMS and state.normative_claims:
        ctx.backend.store_claims(ctx.pid, state.normative_claims)
    elif step_name == _STEP_5_DEDUP_EVIDENCE and state.deduped_evidence:
        ctx.backend.store_evidence(ctx.pid, state.deduped_evidence)
    elif step_name == _STEP_8_EXTRACT_RHETORIC and state.rhetoric:
        ctx.backend.store_rhetoric(ctx.pid, state.rhetoric)
    elif step_name == _STEP_9_VERIFY and state.verdicts and state.normative_claims:
        ctx.backend.store_questions(ctx.pid, state.normative_claims, state.verdicts)
    elif step_name == _STEP_11_VERIFY_CITATIONS and state.citation_audit:
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
    elif step_name == _STEP_12_WEB_SEARCH and state.external_evidence:
        ctx.backend.store_external_citations(ctx.pid, state.external_evidence)
    elif step_name == _STEP_14_CAPUT_CAUSAE and state.caput_causae:
        ctx.backend.store_caput_causae(ctx.pid, state.caput_causae.thesis)


# -- Public API ---------------------------------------------------------------


def build_dissect_pipeline(
    secs: dict[str, str],
    slot_bindings: dict[str, tuple[str, ModelBackend]],
    classifier_slots: dict[str, ClassifierBackend],
    *,
    stop_after: int | None = None,
) -> tuple[list[StepSpec], dict[str, AgentBackend]]:
    """Build the dissect pipeline and validate slot capabilities.

    Constructs the three named agents from ``slot_bindings``,
    threading slot and service names so capability-mismatch errors
    can echo them back. Runs ``_build_hooks``, ``build_pipeline``,
    and ``validate_capabilities`` in that order.

    ``slot_bindings`` maps slot names (``fast``, ``default``,
    ``tool``) to ``(service_name, ModelBackend)`` tuples as
    returned by :func:`pipeline.resolve_slots`.

    ``stop_after`` honors the run's ``--step`` scope and is
    forwarded to :func:`validate_capabilities` so slots that won't
    be reached are not validated. Mirrors ``dispatch``'s scoping
    rule exactly.

    Returns ``(pipeline, agents)`` where ``agents`` is the
    ``slot -> AgentBackend`` map for ``StepContext``.

    Raises :class:`pipeline.CapabilityMismatchError` if any in-scope
    step's declared tools or assigned thinking_budget exceed the
    bound backend's capabilities. Raises
    :class:`pipeline.PromptFileError` subtypes on prompt-file
    structural problems.
    """
    fast_svc, fast_backend = slot_bindings.get("fast", slot_bindings["default"])
    default_svc, default_backend = slot_bindings["default"]
    tool_svc, tool_backend = slot_bindings.get("tool", slot_bindings["default"])

    extraction_agent = AgentBackend(
        fast_backend,
        thinking_budget=2048,
        slot_name="fast",
        service_name=fast_svc,
    )
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

    hooks = _build_hooks(extraction_agent, synthesis_agent, research_agent)
    pipeline_specs = build_pipeline(secs, hooks)
    validate_capabilities(pipeline_specs, stop_after=stop_after)

    agents = {
        "fast": extraction_agent,
        "default": synthesis_agent,
        "tool": research_agent,
    }
    return pipeline_specs, agents


async def dissect_paper(
    pid: str,
    backend: StorageBackend,
    *,
    service_overrides: dict[str, str] | None = None,
    classifier_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    on_progress: ProgressCallback | None = None,
    stop_after: int | None = None,
    chunk_index: int | None = None,
    debug: bool = False,
    trace: bool = False,
) -> str:
    """Extract structural questions from a WG21 paper.

    Loads services and classifiers from SERVICES.toml, builds agents,
    runs the multi-step extractor pipeline, and returns the final
    report string. Pass ``service_overrides`` to bind LLM slots to
    specific services (e.g. ``{"default": "fireworks-405b"}``); pass
    ``classifier_overrides`` to bind classifier slots (e.g.
    ``{"selector": "zeroshot-base"}``) for Step 1 Tag Sentences; pass
    ``provider_override`` to lock the transformer provider
    (device/dtype/batch) used by all classifiers.
    """
    from dissect.pdf_extract import extract_pdf_text
    from pipeline import (
        load_classifiers,
        load_transformer_providers,
        resolve_classifier_slots,
        resolve_transformer_provider,
    )

    services, defaults = load_services()
    slot_bindings = resolve_slots(services, defaults, service_overrides)

    providers, provider_defaults = load_transformer_providers()
    provider = resolve_transformer_provider(
        providers, provider_defaults, override=provider_override,
    )

    classifiers, classifier_defaults = load_classifiers(provider=provider)
    classifier_slots = resolve_classifier_slots(
        classifiers, classifier_defaults, classifier_overrides,
    )

    secs = dict(load_sections("dissect", "dissect.md"))
    pipeline, agents = build_dissect_pipeline(
        secs, slot_bindings, classifier_slots, stop_after=stop_after,
    )

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

    async with WebResearcher(
        binary_extractors={"application/pdf": extract_pdf_text},
    ) as researcher:
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
            agents=agents,
            classifiers=classifier_slots,
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
            chunk_index=chunk_index,
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
    service_overrides: dict[str, str] | None = None,
    classifier_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
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
                service_overrides=service_overrides,
                classifier_overrides=classifier_overrides,
                provider_override=provider_override,
                on_progress=on_progress,
                stop_after=stop_after,
                debug=debug,
                trace=trace,
            )
            out_path = backend.write_dissect_md(pid, report)
            logger.info("Dissected %s -> %s", pid, out_path)
            results.append({"paper_id": pid, "status": "ok", "error": None})
        except Exception as exc:
            # Batch mode records the failed paper and continues with later papers.
            logger.error("Failed to dissect %s: %s", pid, exc)
            backend.fail_paper(pid, 2, str(exc))
            results.append({"paper_id": pid, "status": "error", "error": str(exc)})

    return results
