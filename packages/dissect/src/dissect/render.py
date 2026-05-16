#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Rendering functions for pipeline output.

Produces two output formats from ``PipelineState``:

- ``render_report`` -- the final dissection markdown (unsupported/supported
  claims + external resources).
- ``render_trace`` -- diagnostic trace of pipeline state up to a given step.
"""

from __future__ import annotations

from typing import Any

from paperstore.backend import PaperRow
from pipeline import sanitize_md

from dissect.models import PipelineState

_SUPPORTED_VERDICTS = ("proven", "implied")
_UNSUPPORTED_VERDICT = "unproven"


def _uid_text(
    index: dict[int, str],
    uid: int,
) -> str:
    """Look up display text for a uid, with fallback."""
    text = index.get(uid)
    if text is not None:
        return text
    return f"(uid {uid})"


def _build_uid_index(items: list[Any], alive_only: bool = True) -> dict[int, str]:
    """Build a uid -> text dict from claims or evidence."""
    index: dict[int, str] = {}
    for item in items:
        if alive_only and item.merged_into is not None:
            continue
        index[item.uid] = item.text
    return index


def render_report(state: PipelineState, pid: str, title: str) -> str:
    """Render the final dissection as structured markdown."""
    lines: list[str] = [f"# {pid}: {title}\n"]

    if state.caput_causae is not None:
        lines.append("## Caput Causae\n")
        lines.append(f"{state.caput_causae.thesis}\n")

    claims = state.normative_claims or []
    verdicts = state.verdicts or []
    external_evidence = state.external_evidence or []
    evidence = state.deduped_evidence or []

    ev_by_uid = {e.uid: e for e in evidence if e.merged_into is None}

    supported_uids = {
        v.claim_uid for v in verdicts if v.status in _SUPPORTED_VERDICTS
    }
    unsupported_uids = {
        v.claim_uid for v in verdicts if v.status == _UNSUPPORTED_VERDICT
    } - supported_uids

    uid_to_evidence_uids: dict[int, list[int]] = {}
    for v in verdicts:
        if v.status == "proven" and v.related_uid >= 0:
            uid_to_evidence_uids.setdefault(v.claim_uid, []).append(v.related_uid)

    lines.append("## Unsupported Claims\n")
    unsupported = [
        c for c in claims
        if c.merged_into is None and c.uid in unsupported_uids
    ]
    if not unsupported:
        lines.append("None identified.\n")
    else:
        has_normative = any(c.kind == "normative" for c in unsupported)
        has_factual = any(c.kind == "factual" for c in unsupported)
        if has_normative and has_factual:
            for kind_label, kind_value in [("Normative", "normative"), ("Factual", "factual")]:
                kind_claims = [c for c in unsupported if c.kind == kind_value]
                if kind_claims:
                    lines.append(f"### {kind_label}\n")
                    for c in sorted(kind_claims, key=lambda x: (x.loc.line, x.loc.start_char)):
                        lines.append(f"- {c.question}")
                    lines.append("")
        else:
            for c in sorted(unsupported, key=lambda x: (x.loc.line, x.loc.start_char)):
                lines.append(f"- {c.question}")
            lines.append("")

    lines.append("## Supported Claims\n")
    supported = [
        c for c in claims
        if c.merged_into is None and c.uid in supported_uids
    ]
    if not supported:
        lines.append("None identified.\n")
    else:
        has_normative = any(c.kind == "normative" for c in supported)
        has_factual = any(c.kind == "factual" for c in supported)
        if has_normative and has_factual:
            for kind_label, kind_value in [("Normative", "normative"), ("Factual", "factual")]:
                kind_claims = [c for c in supported if c.kind == kind_value]
                if kind_claims:
                    lines.append(f"### {kind_label}\n")
                    for c in sorted(kind_claims, key=lambda x: (x.loc.line, x.loc.start_char)):
                        lines.append(f"- {c.question}")
                        for euid in uid_to_evidence_uids.get(c.uid, []):
                            ev = ev_by_uid.get(euid)
                            if ev:
                                lines.append(f"  - {sanitize_md(ev.text)} ({ev.section})")
                    lines.append("")
        else:
            for c in sorted(supported, key=lambda x: (x.loc.line, x.loc.start_char)):
                lines.append(f"- {c.question}")
                for euid in uid_to_evidence_uids.get(c.uid, []):
                    ev = ev_by_uid.get(euid)
                    if ev:
                        lines.append(f"  - {sanitize_md(ev.text)} ({ev.section})")
            lines.append("")

    audit = state.citation_audit
    if audit:
        lines.append("## Citation Audit\n")
        lines.append("| Paper | Resolved | Quote Match | Discrepancy |")
        lines.append("|-------|----------|-------------|-------------|")
        for a in audit:
            resolved = "Yes" if a.resolved else "No"
            disc = sanitize_md(a.discrepancy) if a.discrepancy else "-"
            lines.append(f"| {a.paper_id} | {resolved} | {a.quote_match} | {disc} |")
        lines.append("")

    lines.append("## External Resources\n")
    seen_urls: set[str] = set()
    resources: list[str] = []
    for ex in external_evidence:
        if ex.source_url and ex.source_url not in seen_urls:
            seen_urls.add(ex.source_url)
            resources.append(f"- [{ex.source_title}]({ex.source_url})")
    if not resources:
        lines.append("None found.\n")
    else:
        lines.extend(resources)
        lines.append("")

    return "\n".join(lines)


def _partition_merged(items: list[Any]) -> tuple[list[Any], list[Any]]:
    """Split items into (survivors, merged) based on merged_into."""
    survivors = [x for x in items if x.merged_into is None]
    merged = [x for x in items if x.merged_into is not None]
    return survivors, merged


_SHADOW_HEADER = (
    "Model: BAAI/bge-small-en-v1.5 @ cosine >= 0.75 (community_detection)"
)


def _render_shadow_section(
    section_label: str,
    groups: list[list[int]] | None,
    items: list[Any],
) -> list[str]:
    """Render an embedding shadow sub-section after a Dedup section.

    ``section_label`` is the section number prefix (e.g. ``"2a"`` for
    the shadow that follows ``## 3. Dedup Claims``). ``groups`` carries
    uid lists from PipelineState; an empty list means no candidates
    above the threshold; ``None`` means the step that would have
    populated this group has not run yet (only fires for partial
    pipelines stopped before the dedup step).
    """
    lines = [f"## {section_label}. Shadow: embedding-proposed merges\n"]
    lines.append(_SHADOW_HEADER)
    if not groups:
        lines.append("")
        lines.append("No proposals (no clusters above threshold).")
        lines.append("")
        return lines

    lines.append(f"{len(groups)} candidate group(s) proposed (not applied):\n")
    by_uid = {item.uid: item for item in items}
    for i, group in enumerate(groups, 1):
        uids_str = ", ".join(str(u) for u in group)
        lines.append(f"Group {i}: uids {uids_str}")
        for j, uid in enumerate(group):
            item = by_uid.get(uid)
            marker = " (survivor)" if j == 0 else ""
            if item is None:
                lines.append(f"  {uid}{marker} (uid not found)")
            else:
                # Items can become tombstoned between shadow time and
                # render time (e.g. Tier 2 runs after the claim shadow).
                tomb = " [later tombstoned]" if item.merged_into is not None else ""
                lines.append(f'  {uid}{marker}{tomb} "{sanitize_md(item.text)}"')
        lines.append("")
    return lines


def render_trace(state: PipelineState, meta: PaperRow | None, stop_step: int) -> str:
    """Render a diagnostic trace of pipeline state up to stop_step."""
    title = meta.title if meta else "Untitled"
    pid = meta.paper_id if meta else ""
    lines: list[str] = [f"# Trace: {pid} -- {title}\n"]

    if stop_step >= 0:
        lines.append("## 0. Read\n")
        chunks = state.chunks or []
        citations = state.citations or []
        lines.append(f"- {len(chunks)} chunk{'s' if len(chunks) != 1 else ''}")
        if state.blanked_lines:
            lines.append(f"- {state.blanked_lines} non-prose lines blanked (code blocks, wording divs)")
        if citations:
            sorted_cits = sorted(citations, key=lambda c: c.paper_id)
            cit_list = ", ".join(c.paper_id for c in sorted_cits)
            lines.append(f"- Paper citations: {cit_list}")
        lines.append("")

    if stop_step >= 1 and state.tagged_sentences is not None:
        tagged = state.tagged_sentences
        counts = {"target": 0, "context": 0, "skip": 0}
        for ts in tagged:
            counts[ts.tag.value] += 1
        lines.append("## 1. Tag Sentences\n")
        lines.append(
            f"- {len(tagged)} sentences classified: "
            f"{counts['target']} target, {counts['context']} context, "
            f"{counts['skip']} skip"
        )
        lines.append("- Decision rule: TARGET if t-s>0.05, SKIP if s-t>0.40, else CONTEXT (asymmetric; biased toward TARGET/CONTEXT)")
        lines.append("")

    if stop_step >= 2:
        lines.append("## 2. Extract Claims\n")
        raw_claims = state.raw_claims or []
        lines.append(f"{len(raw_claims)} claims extracted:\n")

        if raw_claims:
            for i, rc in enumerate(raw_claims[:50], 1):
                lines.append(f'{i}. "{sanitize_md(rc.text)}"')
                if rc.question:
                    lines.append(f"  - Q: {rc.question}")
            lines.append("")

    if stop_step >= 3:
        lines.append("## 3. Dedup Claims\n")
        all_claims = state.normative_claims or []
        normative = [c for c in all_claims if c.kind != "factual"]
        survivors, merged = _partition_merged(normative)
        lines.append(f"{len(normative)} -> {len(survivors)} survivors ({len(merged)} merged):\n")
        for i, c in enumerate(normative, 1):
            if c.merged_into is not None:
                lines.append(f"{i}. [tombstone]")
            else:
                lines.append(f'{i}. "{sanitize_md(c.text)}" ({c.section})')
                if c.question:
                    lines.append(f"   - Q: {c.question}")
        lines.append("")
        lines.extend(_render_shadow_section(
            "2a", state.shadow_claim_groups, normative,
        ))

    if stop_step >= 4:
        lines.append("## 4. Extract Evidence\n")
        raw_evidence = state.raw_evidence or []
        lines.append(f"{len(raw_evidence)} evidence items extracted:\n")
        if raw_evidence:
            for i, re_ in enumerate(raw_evidence[:50], 1):
                supports_str = re_.supports[0] if re_.supports else ""
                flags = []
                if re_.quantitative:
                    flags.append("quantitative")
                if re_.cited:
                    flags.append("cited")
                if re_.verifiable:
                    flags.append("verifiable")
                if re_.normative:
                    flags.append("normative")
                flag_str = f" ({', '.join(flags)})" if flags else ""
                lines.append(f'{i}. "{sanitize_md(re_.text)}"')
                lines.append(f'   - Supports: "{supports_str}"{flag_str}')
            lines.append("")

    if stop_step >= 5:
        lines.append("## 5. Dedup Evidence\n")
        all_ev = state.deduped_evidence or []
        survivors, merged = _partition_merged(all_ev)
        lines.append(f"{len(all_ev)} -> {len(survivors)} survivors ({len(merged)} merged):\n")
        for i, e in enumerate(all_ev, 1):
            if e.merged_into is not None:
                lines.append(f"{i}. [tombstone]")
            else:
                supports_str = e.supports[0] if e.supports else ""
                lines.append(f'{i}. "{sanitize_md(e.text)}" ({e.section})')
                lines.append(f'   - Supports: "{supports_str}"')
        lines.append("")
        lines.extend(_render_shadow_section(
            "4a", state.shadow_evidence_groups, all_ev,
        ))

    if stop_step >= 6:
        lines.append("## 6. Extract Factual\n")
        raw_factual = state.raw_factual or []
        lines.append(f"{len(raw_factual)} factual claims extracted:\n")
        for i, rc in enumerate(raw_factual[:50], 1):
            lines.append(f'{i}. "{sanitize_md(rc.text)}"')
            if rc.question:
                lines.append(f"   - Q: {rc.question}")
        lines.append("")

    if stop_step >= 7:
        lines.append("## 7. Dedup Factual Claims\n")
        all_claims = state.normative_claims or []
        factual = [c for c in all_claims if c.kind == "factual"]
        survivors = [c for c in factual if c.merged_into is None]
        merged = [c for c in factual if c.merged_into is not None]
        lines.append(f"{len(factual)} -> {len(survivors)} survivors ({len(merged)} merged)")
        lines.append("")

    if stop_step >= 8:
        lines.append("## 8. Extract Rhetoric\n")
        rhetoric = state.rhetoric or []
        lines.append(f"{len(rhetoric)} markers extracted:\n")
        for i, m in enumerate(rhetoric, 1):
            lines.append(f'{i}. [{m.marker_type}] "{sanitize_md(m.text)}" ({m.section})')
            lines.append(f"   - Target: {m.target} ({m.intensity})")
        lines.append("")

    claims = state.normative_claims or []
    evidence = state.deduped_evidence or []
    claim_index = _build_uid_index(claims)
    ev_index = _build_uid_index(evidence)

    if stop_step >= 9:
        lines.append("## 9. Verify\n")

        triaged = state.triaged_evidence
        centrality = state.centrality_scores
        candidates = state.disclaim_candidates
        batches = state.verify_batch_count
        self_pair_dropped = state.self_pair_dropped

        if centrality is not None:
            lines.append(
                f"Triage: centrality scored {len(centrality)} claim(s); "
                f"{batches} verify batch(es); "
                f"{len(candidates or [])} disclaim candidate pair(s); "
                f"self-pair dropped: {self_pair_dropped}."
            )
            if triaged:
                evid_per_claim = [len(v) for v in triaged.values()]
                if evid_per_claim:
                    avg = sum(evid_per_claim) / len(evid_per_claim)
                    lines.append(
                        f"Triaged evidence: "
                        f"{len(triaged)} claim(s) saw "
                        f"{min(evid_per_claim)}-{max(evid_per_claim)} "
                        f"evidence item(s) each (mean {avg:.1f})."
                    )
            if candidates:
                preview = ", ".join(
                    f"({a},{b})" for a, b in candidates[:5]
                )
                trailing = "" if len(candidates) <= 5 else f", ... +{len(candidates) - 5} more"
                lines.append(f"Disclaim candidates (first 5): {preview}{trailing}.")
            top_central = sorted(
                centrality.items(), key=lambda kv: (-kv[1], kv[0])
            )[:5]
            if top_central:
                lines.append(
                    "Top central claims: " + ", ".join(
                        f"{uid}={score:.1f}" for uid, score in top_central
                    ) + "."
                )
            lines.append("")
        else:
            lines.append("Triage: not computed (no claims).")
            lines.append("")

        all_verdicts = state.verdicts or []

        by_status: dict[str, list[Any]] = {}
        for v in all_verdicts:
            by_status.setdefault(v.status, []).append(v)

        for status in ("disclaimed", "disproven", "unproven", "implied", "proven"):
            group = by_status.get(status, [])
            if not group and status not in ("proven",):
                continue
            lines.append(f"### {status} ({len(group)})\n")
            for v in group:
                lines.append(f'- "{_uid_text(claim_index, v.claim_uid)}"')
                if v.related_uid >= 0:
                    idx = claim_index if status in ("implied", "disclaimed") else ev_index
                    lines.append(f'  - <- "{_uid_text(idx, v.related_uid)}"')
            lines.append("")

    if stop_step >= 10:
        lines.append("## 10. Load-Bearing\n")
        lb = state.load_bearing_claims or []
        if lb:
            by_cls: dict[str, list[Any]] = {}
            for item in lb:
                by_cls.setdefault(item.classification, []).append(item)
            for cls, items in sorted(by_cls.items(), key=lambda kv: -len(kv[1])):
                lines.append(f"### {cls} ({len(items)})\n")
                for item in items:
                    lines.append(f'- "{_uid_text(claim_index, item.claim_uid)}"')
                lines.append("")
        else:
            lines.append("No classifications.")
        lines.append("")

    if stop_step >= 11:
        lines.append("## 11. Verify Citations\n")
        audit = state.citation_audit or []
        if audit:
            resolved_count = sum(1 for a in audit if a.resolved)
            lines.append(f"{len(audit)} citations checked, {resolved_count} resolved:\n")
            for a in audit:
                status = "resolved" if a.resolved else "not found"
                lines.append(f"- {a.paper_id}: {status} ({a.resolution_method})")
                if a.quote_match != "not_checked":
                    lines.append(f"  - Quote match: {a.quote_match}")
                if a.discrepancy:
                    lines.append(f"  - Discrepancy: {a.discrepancy}")
        else:
            lines.append("No citations audited.")
        lines.append("")

    if stop_step >= 12:
        lines.append("## 12. Web Search\n")
        ext = state.external_evidence or []
        lines.append(f"{len(ext)} external evidence items found:\n")
        for ex in ext[:10]:
            lines.append(f"- [{ex.source_title}]({ex.source_url}) - {ex.stance}")
            lines.append(f"  - {ex.finding}")
        lines.append("")

    if stop_step >= 13:
        lines.append("## 13. Resolve External\n")
        resolutions = state.web_resolutions or []
        if resolutions:
            lines.append(f"{len(resolutions)} resolutions applied:\n")
            for wr in resolutions:
                lines.append(f"- [{wr.finding}]({wr.source_url}) - {wr.stance}")
                for cl in wr.resolved_claims:
                    lines.append(f'  - Resolved: "{_uid_text(claim_index, cl)}"')
        else:
            lines.append("No resolutions.")
        lines.append("")

    if stop_step >= 14:
        lines.append("## 14. Caput Causae\n")
        cc = state.caput_causae
        if cc:
            lines.append(f"**Thesis:** {cc.thesis}\n")
            if cc.anchored_claim_uids:
                lines.append(f"Anchored claims ({len(cc.anchored_claim_uids)}):\n")
                for uid in cc.anchored_claim_uids:
                    lines.append(f'- "{_uid_text(claim_index, uid)}"')
            lines.append("")
        else:
            lines.append("Not computed.")
            lines.append("")

    if stop_step >= 15:
        lines.append("## 15. Detect Patterns\n")
        patterns = state.marker_patterns
        if patterns:
            if patterns.asymmetries:
                lines.append(f"### Asymmetries ({len(patterns.asymmetries)})\n")
                for a in patterns.asymmetries:
                    lines.append(f'- {a.description}')
                    lines.append(f'  - Marker: "{_uid_text(claim_index, a.marker_uid)}"')
                    lines.append(f'  - Claim: "{_uid_text(claim_index, a.claim_uid)}"')
                lines.append("")
            if patterns.concession_clusters:
                lines.append(f"### Concession Clusters ({len(patterns.concession_clusters)})\n")
                for cc in patterns.concession_clusters:
                    lines.append(f"- Topic: {cc.topic} ({len(cc.marker_uids)} markers)")
                lines.append("")
            if patterns.scope_chains:
                lines.append(f"### Scope Chains ({len(patterns.scope_chains)})\n")
                for sc in patterns.scope_chains:
                    lines.append(f"- {sc.paper_id} ({len(sc.marker_uids)} deflections)")
                lines.append("")
        else:
            lines.append("No patterns detected.")
        lines.append("")

    if stop_step >= 16:
        lines.append("## 16. Report\n")
        lines.append("Report rendered." if state.report else "Report not rendered.")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 1 (Tag Sentences) debug renderer
# ---------------------------------------------------------------------------

def render_debug_tag_sentences(
    tagged: list[Any],
    *,
    classifier_name: str,
    classifier_model: str,
    device: str,
    target_label: str,
    skip_label: str,
    target_margin: float,
    skip_margin: float,
    multi_label: bool,
) -> str:
    """Render the Step 1 (Tag Sentences) result as a debug.md section.

    Mirrors the LLM-step ``render_debug_md`` channel: returns a single
    markdown string that the caller appends to ``ctx.debug_log``. The
    section contains a header block (classifier config + hypothesis
    labels + margin + mode) and a per-sentence table with line / tag /
    target score / skip score / verbatim text. Per-sentence detail
    lives here, not in the trace; the trace shows summary counts only.

    Long sentences are NOT truncated. Debug is for inspection, not
    skimming. Pipe characters inside sentence text are escaped so they
    do not break the table.
    """
    lines: list[str] = []
    lines.append("## 1. Tag Sentences\n")
    lines.append(f"**Classifier:** {classifier_name} (`{classifier_model}`)")
    lines.append(f"**Device:** {device}")
    lines.append(f"**Mode:** multi_label={multi_label}")
    lines.append(
        f"**Decision rule:** TARGET if t-s>{target_margin}, "
        f"SKIP if s-t>{skip_margin}, else CONTEXT "
        f"(asymmetric: biased toward TARGET/CONTEXT; "
        f"SKIP requires high confidence)"
    )
    lines.append("**Hypothesis labels:**")
    lines.append(f"- TARGET: \"{target_label}\"")
    lines.append(f"- SKIP: \"{skip_label}\"")
    lines.append("")
    lines.append("### Per-sentence scores\n")
    lines.append("| line | tag | target | skip | text |")
    lines.append("| --- | --- | --- | --- | --- |")
    for ts in tagged:
        text = ts.span.text.replace("|", "\\|")
        lines.append(
            f"| {ts.span.line} | {ts.tag.name} | "
            f"{ts.target_score:.3f} | {ts.skip_score:.3f} | {text} |"
        )
    lines.append("")
    return "\n".join(lines)
