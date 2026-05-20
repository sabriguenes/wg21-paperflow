#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Pure-Python processing for the assay pipeline.

No LLM calls, no network I/O.
Functions here implement collect (Step 6), cross_examine (Step 12),
and synthesize (Step 14) logic.
"""

from __future__ import annotations

from assay.models import (
    AskOutput,
    BreadcrumbOutput,
    ChunkExtractOutput,
    CollectedItem,
    CollectedItems,
    CrossExamVerdict,
    DeriveOutput,
    FindingOutput,
    FrontMatter,
    ItemOutput,
    KilledFinding,
    ReferenceEntry,
    ReferenceOutput,
    ScanOutput,
    SynthesisOutput,
)

LENS_ORDER = ["Performance", "Design", "Specification", "Usability", "Ecosystem", "Rationale"]


def collect(
    raw_extractions: list[ChunkExtractOutput],
    raw_scans: list[ScanOutput],
    front_matter: FrontMatter | None,
) -> tuple[CollectedItems, dict[str, list[BreadcrumbOutput]], list[AskOutput], list[str], list[str], list[ReferenceEntry]]:
    """Step 5: aggregate and dedup all per-chunk extractions and scans.

    Returns (items, breadcrumbs_by_lens, asks, active_lenses, inactive_lenses, reference_registry).
    """
    all_claims: list[ItemOutput] = []
    all_evidence: list[ItemOutput] = []
    all_concessions: list[ItemOutput] = []
    all_questions: list[ItemOutput] = []
    all_dependencies: list[ItemOutput] = []
    all_scope: list[ItemOutput] = []
    all_ask_items: list[ItemOutput] = []
    all_references: list[ReferenceOutput] = []

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
        all_references.extend(extraction.references)

    claims = _dedup_items(all_claims)
    evidence = _dedup_items(all_evidence)
    concessions = _dedup_items(all_concessions)
    deduped_ask_items = _dedup_items(all_ask_items)
    asks = [
        AskOutput(quote=item.quote, line=item.line, target="", type="")
        for item in deduped_ask_items
    ]

    collected_claims = [
        CollectedItem(id=f"C{i + 1}", type=c.type,
                      quote=c.quote, line=c.line, quality_tier=c.quality_tier)
        for i, c in enumerate(claims)
    ]
    collected_evidence = [
        CollectedItem(id=f"E{i + 1}", type=e.type,
                      quote=e.quote, line=e.line, quality_tier=e.quality_tier)
        for i, e in enumerate(evidence)
    ]
    collected_concessions = [
        CollectedItem(id=f"CON{i + 1}", type=c.type,
                      quote=c.quote, line=c.line, quality_tier=c.quality_tier)
        for i, c in enumerate(concessions)
    ]

    all_breadcrumbs: list[BreadcrumbOutput] = []
    for scan in raw_scans:
        all_breadcrumbs.extend(scan.breadcrumbs)

    breadcrumbs_by_lens: dict[str, list[BreadcrumbOutput]] = {lens: [] for lens in LENS_ORDER}
    for b in all_breadcrumbs:
        primary = b.primary_lens
        if primary in breadcrumbs_by_lens:
            breadcrumbs_by_lens[primary].append(b)
        secondary = b.secondary_lens
        if secondary and secondary in breadcrumbs_by_lens and secondary != primary:
            breadcrumbs_by_lens[secondary].append(b)

    active_lenses = [lens for lens in LENS_ORDER if breadcrumbs_by_lens[lens] or lens == "Rationale"]
    inactive_lenses = [lens for lens in LENS_ORDER if lens not in active_lenses]

    reference_registry = _build_reference_registry(all_references, front_matter)

    items = CollectedItems(
        claims=collected_claims,
        evidence=collected_evidence,
        concessions=collected_concessions,
        questions=all_questions,
        dependencies=all_dependencies,
        scope=all_scope,
    )

    return items, breadcrumbs_by_lens, asks, active_lenses, inactive_lenses, reference_registry


def upgrade_breadcrumbs(
    breadcrumbs_by_lens: dict[str, list[BreadcrumbOutput]],
    central_claim: str,
    problem_statement: str,
) -> dict[str, list[BreadcrumbOutput]]:
    """Post-Derive: upgrade breadcrumb severity if gap touches thesis."""
    thesis_words = set((central_claim + " " + problem_statement).lower().split())
    result: dict[str, list[BreadcrumbOutput]] = {}
    for lens, breadcrumbs in breadcrumbs_by_lens.items():
        new_list: list[BreadcrumbOutput] = []
        for b in breadcrumbs:
            if b.severity in ("significant", "minor"):
                gap_words = set(b.gap.lower().split())
                overlap = thesis_words & gap_words
                if len(overlap) >= 3:
                    b = b.model_copy(update={"severity": "critical"})
            new_list.append(b)
        result[lens] = new_list
    return result


def cross_examine(
    findings: list[FindingOutput],
    verdicts: list[CrossExamVerdict],
) -> tuple[list[FindingOutput], list[KilledFinding]]:
    """Step 9: apply LLM cross-examination verdicts to findings.

    Returns (surviving, killed).
    """
    verdict_map: dict[str, CrossExamVerdict] = {}
    for v in verdicts:
        verdict_map[v.finding_title] = v

    surviving: list[FindingOutput] = []
    killed: list[KilledFinding] = []

    for f in findings:
        title = f.title
        v = verdict_map.get(title)

        if v and not v.survived:
            killed.append(KilledFinding(
                finding_title=title,
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
    """Step 10: promote to Major, compute verdict."""
    central_claim = derive.central_claim

    compound_titles: set[str] = set()
    for comp in compounds:
        for title in comp.constituents:
            compound_titles.add(title)

    major_findings: list[FindingOutput] = []
    regular_findings: list[FindingOutput] = []

    for f in surviving:
        title = f.title
        is_major = False

        if title in compound_titles:
            is_major = True
        elif _touches_thesis(f, central_claim):
            is_major = True

        if is_major:
            major_findings.append(f)
        else:
            regular_findings.append(f)

    critical_count = sum(1 for f in surviving if f.severity == "critical")
    significant_count = sum(1 for f in surviving if f.severity == "significant")

    contradicts_thesis = any(
        _touches_thesis(f, central_claim) and f.severity == "critical"
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

    central_thesis_sentence = ""
    if dominant_dynamic:
        central_thesis_sentence = f"The dominant structural weakness is {dominant_dynamic}."
    elif major_findings:
        central_thesis_sentence = f"The primary gap is: {major_findings[0].title}."
    else:
        central_thesis_sentence = "No structural weaknesses found."

    return SynthesisOutput(
        verdict=verdict,
        verdict_confidence=verdict_confidence,
        central_thesis=central_thesis_sentence,
        dominant_dynamic=dominant_dynamic,
        thesis_survives=verdict in ("Sound", "Weakened"),
        thesis_statement=central_claim,
        major_findings=major_findings,
        regular_findings=regular_findings,
        critical_count=critical_count,
        significant_count=significant_count,
    )


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



def _build_reference_registry(
    all_references: list[ReferenceOutput], front_matter: FrontMatter | None,
) -> list[ReferenceEntry]:
    """Build deduped reference registry keyed by ref_label."""
    by_label: dict[str, ReferenceEntry] = {}
    authors = {a.lower() for a in (front_matter.authors if front_matter else [])}

    for ref in all_references:
        label = ref.ref_label or ref.url or ref.text
        if not label:
            continue

        if label not in by_label:
            by_label[label] = ReferenceEntry(
                ref_id=f"R{len(by_label) + 1}",
                ref_label=ref.ref_label or "",
                url=ref.url,
                source_type="paper",
                contexts=[],
                chunk_appearances=[],
                relationship=ref.relationship,
                same_author=False,
                mention_count=0,
            )

        entry = by_label[label]
        ctx = ref.context
        if ctx and ctx not in entry.contexts:
            entry.contexts.append(ctx)
        entry.mention_count += 1

        rel = ref.relationship
        tier_order = ["companion", "predecessor", "dependency", "citation", "background", "tool"]
        if tier_order.index(rel) < tier_order.index(entry.relationship):
            entry.relationship = rel

        text_lower = ref.text.lower()
        if any(a in text_lower for a in authors):
            entry.same_author = True

    return list(by_label.values())


def _touches_thesis(finding: FindingOutput, central_claim: str) -> bool:
    """Check if a finding touches the thesis."""
    if not central_claim:
        return False
    thesis_words = set(central_claim.lower().split())
    quote_words = set(finding.quote.lower().split())
    expl_words = set(finding.explanation.lower().split())
    combined = quote_words | expl_words
    overlap = thesis_words & combined
    return len(overlap) >= 3
