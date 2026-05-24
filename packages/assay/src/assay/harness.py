#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Pure-Python processing for the assay pipeline.

No LLM calls, no network I/O.
Functions here implement collect (Step 7), cross_examine (Step 14),
and synthesize (Step 16) logic.
"""

from __future__ import annotations

import logging

import numpy as np

from assay.models import (
    AskOutput,
    GapOutput,
    ChunkExtractOutput,
    CollectedItem,
    CollectedItems,
    CrossExamVerdict,
    DeriveOutput,
    FindingOutput,
    ItemOutput,
    KilledFinding,
    ScanOutput,
    SynthesisOutput,
)

logger = logging.getLogger(__name__)

LENS_ORDER = ["Performance", "Design", "Specification", "Usability", "Ecosystem", "Rationale"]

# -- Embedding / lexical similarity helpers ---------------------------------

# Mid of the SBERT-style 0.60-0.75 "near-paraphrase" band. Above this, gap
# text is judged thesis-touching and severity escalates to critical.
_THESIS_SIM_THRESHOLD = 0.65
# Lexical fallback gates: Jaccard ratio of content words plus a hard
# minimum on overlap count, so 2-3 short common words cannot trigger it.
_LEXICAL_JACCARD_THRESHOLD = 0.25
_LEXICAL_MIN_CONTENT_OVERLAP = 3
# High-precision threshold for finding-vs-finding dedupe. Above LlamaIndex's
# "flag-for-review" band, below NeMo Curator's "near-identical" 0.99.
_FINDING_DUP_SIM_THRESHOLD = 0.92


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two vectors. Returns 0 for zero-norm input."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _lexical_thesis_score(a: str, b: str) -> tuple[float, int]:
    """Stop-word-filtered Jaccard ratio plus raw overlap count.

    Returned as ``(jaccard, overlap_count)`` so callers can apply a
    minimum-content-overlap gate alongside the ratio threshold.
    """
    sa = set(a.lower().split()) - _STOP_WORDS
    sb = set(b.lower().split()) - _STOP_WORDS
    if not sa or not sb:
        return 0.0, 0
    inter = sa & sb
    return len(inter) / len(sa | sb), len(inter)


def _embed_to_numpy(embedder, texts: list[str]) -> list[np.ndarray] | None:
    """Embed ``texts`` and return numpy vectors, or ``None`` on failure.

    ``EmbeddingBackend.embed`` returns a torch tensor; pull it back to
    CPU/float32 and split into row vectors. Returning ``None`` lets the
    caller fall through to the lexical path.
    """
    if not texts:
        return []
    try:
        raw = embedder.embed(texts)
        if raw is None:
            return None
        arr = raw.float().cpu().numpy()
        return [arr[i] for i in range(arr.shape[0])]
    except Exception as exc:
        logger.debug("embedder.embed failed: %s", exc)
        return None


def collect(
    raw_extractions: list[ChunkExtractOutput],
    raw_scans: list[ScanOutput],
    start_id: int = 1,
) -> tuple[CollectedItems, dict[str, list[GapOutput]], list[AskOutput], list[str], list[str], int]:
    """Step 7: aggregate and dedup all per-chunk extractions and gaps.

    Returns (items, gaps_by_lens, asks, active_lenses, inactive_lenses, next_id).
    """
    all_claims: list[ItemOutput] = []
    all_evidence: list[ItemOutput] = []
    all_concessions: list[ItemOutput] = []
    all_questions: list[ItemOutput] = []
    all_dependencies: list[ItemOutput] = []
    all_scope: list[ItemOutput] = []
    all_ask_items: list[ItemOutput] = []

    for extraction in raw_extractions:
        for item in extraction.items:
            item_type = item.type
            if item_type == "claim":
                all_claims.append(item)
            elif item_type == "evidence":
                all_evidence.append(item)
            elif item_type == "concession":
                all_concessions.append(item)
            elif item_type == "question":
                all_questions.append(item)
            elif item_type == "dependency":
                all_dependencies.append(item)
            elif item_type == "scope":
                all_scope.append(item)
            elif item_type == "ask":
                all_ask_items.append(item)

    claims = _dedup_items(all_claims)
    evidence = _dedup_items(all_evidence)
    concessions = _dedup_items(all_concessions)
    deduped_ask_items = _dedup_items(all_ask_items)
    asks = [
        AskOutput(quote=item.quote, line=item.line, target="", type="")
        for item in deduped_ask_items
    ]

    collected_claims = [
        CollectedItem(type=c.type,
                      quote=c.quote, line=c.line, quality_tier=c.quality_tier)
        for c in claims
    ]
    collected_evidence = [
        CollectedItem(type=e.type,
                      quote=e.quote, line=e.line, quality_tier=e.quality_tier)
        for e in evidence
    ]
    collected_concessions = [
        CollectedItem(type=c.type,
                      quote=c.quote, line=c.line, quality_tier=c.quality_tier)
        for c in concessions
    ]

    all_gaps: list[GapOutput] = []
    for scan in raw_scans:
        all_gaps.extend(scan.gaps)

    next_id = start_id
    collected_claims = [c.model_copy(update={"id": next_id + i}) for i, c in enumerate(collected_claims)]
    next_id += len(collected_claims)
    collected_evidence = [e.model_copy(update={"id": next_id + i}) for i, e in enumerate(collected_evidence)]
    next_id += len(collected_evidence)
    collected_concessions = [c.model_copy(update={"id": next_id + i}) for i, c in enumerate(collected_concessions)]
    next_id += len(collected_concessions)
    all_gaps = [g.model_copy(update={"id": next_id + i}) for i, g in enumerate(all_gaps)]
    next_id += len(all_gaps)
    asks = [a.model_copy(update={"id": next_id + i}) for i, a in enumerate(asks)]
    next_id += len(asks)

    gaps_by_lens: dict[str, list[GapOutput]] = {lens: [] for lens in LENS_ORDER}
    for b in all_gaps:
        primary = b.primary_lens
        if primary in gaps_by_lens:
            gaps_by_lens[primary].append(b)
        secondary = b.secondary_lens
        if secondary and secondary in gaps_by_lens and secondary != primary:
            gaps_by_lens[secondary].append(b)

    active_lenses = [lens for lens in LENS_ORDER if gaps_by_lens[lens] or lens == "Rationale"]
    inactive_lenses = [lens for lens in LENS_ORDER if lens not in active_lenses]

    items = CollectedItems(
        claims=collected_claims,
        evidence=collected_evidence,
        concessions=collected_concessions,
        questions=all_questions,
        dependencies=all_dependencies,
        scope=all_scope,
    )

    return items, gaps_by_lens, asks, active_lenses, inactive_lenses, next_id


def upgrade_gaps(
    gaps_by_lens: dict[str, list[GapOutput]],
    central_claim: str,
    problem_statement: str,
    *,
    embedder=None,
) -> dict[str, list[GapOutput]]:
    """Post-Derive: upgrade gap severity when gap touches the thesis.

    Embedding cosine is the primary signal (mid of the SBERT 0.6-0.75
    near-paraphrase band). Falls back to stop-word-filtered Jaccard
    plus a hard overlap floor when no embedder is available or
    embedding fails. Bag-of-words alone is length-sensitive and
    paraphrase-blind, so it is intentionally a fallback only.
    """
    thesis_text = (central_claim + " " + problem_statement).strip()
    if not thesis_text:
        return {lens: list(gaps) for lens, gaps in gaps_by_lens.items()}

    thesis_vec: np.ndarray | None = None
    if embedder is not None:
        vecs = _embed_to_numpy(embedder, [thesis_text])
        if vecs:
            thesis_vec = vecs[0]

    result: dict[str, list[GapOutput]] = {}
    for lens, gaps in gaps_by_lens.items():
        gap_vecs: list[np.ndarray | None] = [None] * len(gaps)
        if thesis_vec is not None and gaps:
            embedded = _embed_to_numpy(embedder, [b.gap for b in gaps])
            if embedded is not None and len(embedded) == len(gaps):
                gap_vecs = list(embedded)

        new_list: list[GapOutput] = []
        for i, b in enumerate(gaps):
            if b.severity not in ("significant", "minor"):
                new_list.append(b)
                continue
            upgrade = False
            if thesis_vec is not None and gap_vecs[i] is not None:
                upgrade = _cosine(thesis_vec, gap_vecs[i]) >= _THESIS_SIM_THRESHOLD
            else:
                jacc, overlap = _lexical_thesis_score(thesis_text, b.gap)
                upgrade = (
                    jacc >= _LEXICAL_JACCARD_THRESHOLD
                    and overlap >= _LEXICAL_MIN_CONTENT_OVERLAP
                )
            new_list.append(
                b.model_copy(update={"severity": "critical"}) if upgrade else b
            )
        result[lens] = new_list
    return result


def dedupe_findings(
    existing: list[FindingOutput],
    candidates: list[FindingOutput],
    *,
    embedder=None,
) -> list[FindingOutput]:
    """Return ``candidates`` with cross-pass duplicates of ``existing`` removed.

    Cosine on ``(title + " " + explanation)`` against the embedded
    corpus of existing findings. 0.92 is high-precision territory: above
    the practitioner flag-for-review band (0.85-0.95), below the
    near-identical rewording band (0.95+). Falls back to substring/Jaccard
    on title only when no embedder is available.
    """
    if not candidates:
        return []
    if not existing:
        return list(candidates)

    if embedder is not None:
        ex_vecs = _embed_to_numpy(
            embedder, [f"{f.title} {f.explanation}" for f in existing]
        )
        cand_vecs = _embed_to_numpy(
            embedder, [f"{f.title} {f.explanation}" for f in candidates]
        )
        if ex_vecs is not None and cand_vecs is not None \
                and len(ex_vecs) == len(existing) \
                and len(cand_vecs) == len(candidates):
            kept: list[FindingOutput] = []
            for cv, cand in zip(cand_vecs, candidates):
                if all(_cosine(cv, ev) < _FINDING_DUP_SIM_THRESHOLD for ev in ex_vecs):
                    kept.append(cand)
            return kept

    existing_titles = [f.title.lower() for f in existing]

    def _is_dup(title: str) -> bool:
        t = title.lower()
        for et in existing_titles:
            if t == et or t in et or et in t:
                return True
            jacc, overlap = _lexical_thesis_score(t, et)
            if jacc >= 0.5 and overlap >= 3:
                return True
        return False

    return [c for c in candidates if not _is_dup(c.title)]


def cross_examine(
    findings: list[FindingOutput],
    verdicts: list[CrossExamVerdict],
) -> tuple[list[FindingOutput], list[KilledFinding]]:
    """Step 14: apply LLM cross-examination verdicts to findings.

    Returns (surviving, killed).
    """
    verdict_map: dict[int, CrossExamVerdict] = {}
    for v in verdicts:
        verdict_map[v.finding_id] = v

    surviving: list[FindingOutput] = []
    killed: list[KilledFinding] = []

    for f in findings:
        v = verdict_map.get(f.id)

        if v and not v.survived:
            killed.append(KilledFinding(
                f.id,
                finding_title=f.title,
                lens=f.lens,
                challenge=v.killed_by or "",
                reasoning=v.reasoning,
            ))
        else:
            surviving.append(f)

    return surviving, killed


def synthesize(
    surviving: list[FindingOutput],
    compounds: list,
    derive: DeriveOutput,
) -> SynthesisOutput:
    """Step 16: promote to Major, compute verdict."""
    central_claim = derive.central_claim

    compound_map: dict[int, str] = {}
    for comp in compounds:
        for fid in comp.constituents:
            compound_map[fid] = comp.name

    major_findings: list[FindingOutput] = []
    regular_findings: list[FindingOutput] = []
    promotion_reasons: dict[int, str] = {}

    for f in surviving:
        reason = None

        if f.id in compound_map:
            reason = f"compound: {compound_map[f.id]}"
        else:
            overlap = _thesis_overlap(f, central_claim)
            if overlap:
                reason = f"thesis-overlap: {', '.join(sorted(overlap))}"

        if reason:
            major_findings.append(f)
            promotion_reasons[f.id] = reason
        else:
            regular_findings.append(f)

    critical_count = sum(1 for f in surviving if f.severity == "critical")
    significant_count = sum(1 for f in surviving if f.severity == "significant")

    contradicts_thesis = any(
        _thesis_overlap(f, central_claim) and f.severity == "critical"
        for f in surviving
    )

    if not surviving:
        verdict = "Sound"
        verdict_confidence = "High"
    elif contradicts_thesis:
        verdict = "Undermined"
        verdict_confidence = "High"
    elif critical_count > 0 or significant_count > 0:
        verdict = "Weakened"
        verdict_confidence = "High" if critical_count > 0 else "Medium"
    else:
        verdict = "Sound"
        verdict_confidence = "High"

    dominant_dynamic = None
    if compounds:
        best = max(compounds, key=lambda c: len(c.constituents))
        dominant_dynamic = best.name

    verdict_statement = _build_verdict_statement(
        verdict, major_findings, dominant_dynamic, compounds,
        critical_count, significant_count,
    )

    return SynthesisOutput(
        verdict_label=verdict,
        verdict_confidence=verdict_confidence,
        verdict_statement=verdict_statement,
        dominant_dynamic=dominant_dynamic,
        thesis_survives=verdict in ("Sound", "Weakened"),
        thesis_statement=central_claim,
        major_findings=major_findings,
        regular_findings=regular_findings,
        promotion_reasons=promotion_reasons,
        critical_count=critical_count,
        significant_count=significant_count,
    )


def _build_verdict_statement(
    verdict: str,
    major_findings: list[FindingOutput],
    dominant_dynamic: str | None,
    compounds: list,
    critical_count: int,
    significant_count: int,
) -> str:
    if verdict == "Sound":
        return "No structural weaknesses found."

    parts: list[str] = []
    if dominant_dynamic:
        comp = next((c for c in compounds if c.name == dominant_dynamic), None)
        if comp and comp.emergent_risk:
            parts.append(comp.emergent_risk.rstrip(".") + ".")
        else:
            parts.append(f"The dominant structural weakness is {dominant_dynamic}.")
    elif major_findings:
        top = major_findings[0]
        parts.append(f"{top.title}: {top.damage}" if top.damage else top.title + ".")

    counts: list[str] = []
    if critical_count:
        counts.append(f"{critical_count} critical")
    if significant_count:
        counts.append(f"{significant_count} significant")
    if counts:
        parts.append(f"{', '.join(counts)} finding{'s' if critical_count + significant_count != 1 else ''} survived challenge.")

    return " ".join(parts) if parts else "No structural weaknesses found."


# -- Internal helpers --------------------------------------------------------


def _dedup_items(items: list[ItemOutput]) -> list[ItemOutput]:
    """Two-tier dedup: exact quote match, then substring absorption."""
    seen: dict[str, int] = {}
    tier0: list[ItemOutput] = []
    for item in items:
        key = item.quote.strip().lower()
        if not key:
            tier0.append(item)
            continue
        if key not in seen:
            seen[key] = len(tier0)
            tier0.append(item)

    survivors = list(tier0)
    n = len(survivors)
    absorbed: set[int] = set()
    for i in range(n):
        if i in absorbed:
            continue
        qi = survivors[i].quote.strip().lower()
        if not qi:
            continue
        for j in range(i + 1, n):
            if j in absorbed:
                continue
            qj = survivors[j].quote.strip().lower()
            if not qj:
                continue
            if qi in qj and qi != qj:
                absorbed.add(i)
                break
            elif qj in qi and qi != qj:
                absorbed.add(j)

    return [s for idx, s in enumerate(survivors) if idx not in absorbed]



_STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or"
    " that the this to was were which with".split()
)


def _thesis_overlap(finding: FindingOutput, central_claim: str) -> set[str]:
    """Content-word overlap (>=3 shared words) between the finding and the central claim.

    Returns the overlapping word set, or empty set when below threshold.
    """
    if not central_claim:
        return set()
    thesis_words = set(central_claim.lower().split()) - _STOP_WORDS
    quote_words = set(finding.quote.lower().split()) - _STOP_WORDS
    expl_words = set(finding.explanation.lower().split()) - _STOP_WORDS
    combined = quote_words | expl_words
    overlap = thesis_words & combined
    return overlap if len(overlap) >= 3 else set()
