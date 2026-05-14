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

_STATUS_DIRECTLY = "directly_supported"
_STATUS_TRANSITIVELY = "transitively_supported"
_STATUS_UNSUPPORTED = "unsupported"
_SUPPORTED_STATUSES = (_STATUS_DIRECTLY, _STATUS_TRANSITIVELY)


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

    claims = state.claims or []
    support_map = state.support_map or []
    external_evidence = state.external_evidence or []
    evidence = state.evidence or []

    ev_by_uid = {e.uid: e for e in evidence if e.merged_into is None}

    supported_uids = {
        s.claim_uid for s in support_map
        if s.status in _SUPPORTED_STATUSES
    }
    unsupported_uids = {
        s.claim_uid for s in support_map if s.status == _STATUS_UNSUPPORTED
    }

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
        uid_to_evidence_uids: dict[int, list[int]] = {}
        for s in support_map:
            if s.status in _SUPPORTED_STATUSES:
                uid_to_evidence_uids[s.claim_uid] = s.evidence_uids

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
        if citations:
            cit_list = ", ".join(c.paper_id for c in citations)
            lines.append(f"- Paper citations: {cit_list}")
        lines.append("")

    if stop_step >= 1:
        lines.append("## 1. Extract Normative\n")
        raw_claims = state.raw_claims or []
        raw_evidence = state.raw_evidence or []
        rhetoric = state.rhetoric or []
        lines.append(f"{len(raw_claims)} claims, {len(raw_evidence)} evidence, {len(rhetoric)} markers extracted:\n")

        if raw_claims:
            lines.append("### Claims\n")
            for i, rc in enumerate(raw_claims[:50], 1):
                lines.append(f'{i}. "{sanitize_md(rc.text)}" ({rc.section})')
                if rc.question:
                    lines.append(f"  - Q: {rc.question}")
            lines.append("")

        if raw_evidence:
            lines.append("### Evidence\n")
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
                lines.append(f'{i}. "{sanitize_md(re_.text)}" ({re_.section})')
                lines.append(f'   - Supports: "{supports_str}"{flag_str}')
            lines.append("")

        if rhetoric:
            lines.append("### Rhetorical Markers\n")
            for i, m in enumerate(rhetoric, 1):
                lines.append(f'{i}. [{m.marker_type}] "{sanitize_md(m.text)}" ({m.section})')
                lines.append(f"   - Target: {m.target} | Intensity: {m.intensity}")
            lines.append("")

    if stop_step >= 2:
        lines.append("## 2. Dedup Claims\n")
        all_claims = state.claims or []
        survivors, merged = _partition_merged(all_claims)
        lines.append(f"{len(all_claims)} -> {len(survivors)} survivors ({len(merged)} merged):\n")
        for i, c in enumerate(all_claims, 1):
            if c.merged_into is not None:
                lines.append(f"{i}. [tombstone]")
            else:
                lines.append(f'{i}. "{sanitize_md(c.text)}" ({c.section})')
                if c.question:
                    lines.append(f"   - Q: {c.question}")
        lines.append("")

    if stop_step >= 3:
        lines.append("## 3. Extract Factual\n")
        raw_factual = state.raw_factual_claims or []
        lines.append(f"{len(raw_factual)} factual claims extracted:\n")
        for i, rc in enumerate(raw_factual[:50], 1):
            lines.append(f'{i}. "{sanitize_md(rc.text)}" ({rc.section})')
            if rc.question:
                lines.append(f"   - Q: {rc.question}")
        lines.append("")

    if stop_step >= 4:
        lines.append("## 4. Dedup Factual Claims\n")
        all_claims = state.claims or []
        factual = [c for c in all_claims if c.kind == "factual"]
        survivors = [c for c in factual if c.merged_into is None]
        merged = [c for c in factual if c.merged_into is not None]
        lines.append(f"{len(factual)} -> {len(survivors)} survivors ({len(merged)} merged)")
        lines.append("")

    if stop_step >= 5:
        lines.append("## 5. Dedup Evidence\n")
        all_ev = state.evidence or []
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

    claims = state.claims or []
    evidence = state.evidence or []
    claim_index = _build_uid_index(claims)
    ev_index = _build_uid_index(evidence)

    if stop_step >= 6:
        lines.append("## 6. Verify\n")
        smap = state.support_map or []

        directly = [s for s in smap if s.status == _STATUS_DIRECTLY]
        transitively = [s for s in smap if s.status == _STATUS_TRANSITIVELY]
        unsupported = [s for s in smap if s.status == _STATUS_UNSUPPORTED]
        contras = state.internal_contradictions or []

        claim_vs_claim = [ic for ic in contras if ic.kind == "claim_vs_claim"]
        ev_vs_claim = [ic for ic in contras if ic.kind == "evidence_vs_claim"]

        if claim_vs_claim:
            lines.append(f"### Claim-vs-Claim Contradictions ({len(claim_vs_claim)})\n")
            for ic in claim_vs_claim:
                lines.append(f'- Claim: "{_uid_text(claim_index, ic.claim_uid)}"')
                lines.append(f'  - Contradicted by: "{_uid_text(claim_index, ic.source_uid)}"')
            lines.append("")

        if ev_vs_claim:
            lines.append(f"### Evidence-vs-Claim Contradictions ({len(ev_vs_claim)})\n")
            for ic in ev_vs_claim:
                lines.append(f'- Claim: "{_uid_text(claim_index, ic.claim_uid)}"')
                lines.append(f'  - Contradicted by: "{_uid_text(ev_index, ic.source_uid)}"')
            lines.append("")

        if unsupported:
            lines.append(f"### Unsupported ({len(unsupported)})\n")
            for s in unsupported:
                lines.append(f'- "{_uid_text(claim_index, s.claim_uid)}"')
            lines.append("")

        if transitively:
            lines.append(f"### Transitively Supported ({len(transitively)})\n")
            for s in transitively:
                lines.append(f'- "{_uid_text(claim_index, s.claim_uid)}"')
            lines.append("")

        lines.append(f"### Directly Supported ({len(directly)})\n")
        for s in directly:
            lines.append(f'- "{_uid_text(claim_index, s.claim_uid)}"')
            for euid in s.evidence_uids:
                lines.append(f'  - <- "{_uid_text(ev_index, euid)}"')
        lines.append("")

    if stop_step >= 7:
        lines.append("## 7. Load-Bearing\n")
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

    if stop_step >= 8:
        lines.append("## 8. Verify Citations\n")
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

    if stop_step >= 9:
        lines.append("## 9. Web Search\n")
        ext = state.external_evidence or []
        lines.append(f"{len(ext)} external evidence items found:\n")
        for ex in ext[:10]:
            lines.append(f"- [{ex.source_title}]({ex.source_url}) - {ex.stance}")
            lines.append(f"  - {ex.finding}")
        lines.append("")

    if stop_step >= 10:
        lines.append("## 10. Resolve External\n")
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

    if stop_step >= 11:
        lines.append("## 11. Caput Causae\n")
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

    if stop_step >= 12:
        lines.append("## 12. Detect Patterns\n")
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

    return "\n".join(lines)
