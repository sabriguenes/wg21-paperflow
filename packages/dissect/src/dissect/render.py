#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Rendering functions for pipeline output.

Produces three output formats from ``PipelineState``:

- ``render_report`` -- the final dissection markdown (unsupported/supported
  claims + external resources).
- ``render_trace`` -- diagnostic trace of pipeline state up to a given step.
- ``render_debug_md`` -- full LLM interaction transcript for debugging.
"""

from __future__ import annotations

import json
import re
from typing import Any

from paperstore.backend import PaperRow

from dissect.models import PipelineState, SourceLoc

_STATUS_DIRECTLY = "directly_supported"
_STATUS_TRANSITIVELY = "transitively_supported"
_STATUS_UNSUPPORTED = "unsupported"
_SUPPORTED_STATUSES = (_STATUS_DIRECTLY, _STATUS_TRANSITIVELY)

_CODE_SPAN_RE = re.compile(r'``.+?``|`[^`]+`')


def render_debug_md(result: Any, step_name: str) -> str:
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
            output_str = json.dumps(
                output.model_dump(), indent=2, ensure_ascii=False,
            )
        else:
            output_str = str(output)
        parts.append(f"## Final Output\n\n```json\n{output_str}\n```\n")
    return "\n".join(parts)


def _escape_md_chars(text: str) -> str:
    """Escape markdown-sensitive characters in prose text."""
    text = text.replace('<', r'\<').replace('>', r'\>')
    text = text.replace('|', r'\|')
    text = re.sub(r'^(\s*)(#)', r'\1\\\2', text, flags=re.MULTILINE)
    for double in ('**', '__'):
        if text.count(double) % 2 != 0:
            text = text.replace(double, '\\' + double)
    for single in ('*', '_'):
        double = single * 2
        esc_double = '\\' + double
        temp = text.replace(esc_double, '\x00\x00\x00')
        temp = temp.replace(double, '\x00\x00')
        temp = temp.replace('\\' + single, '\x00\x00')
        count = temp.count(single)
        if count % 2 != 0:
            parts: list[str] = []
            i = 0
            while i < len(text):
                if text[i:i + 3] == esc_double:
                    parts.append(text[i:i + 3])
                    i += 3
                elif text[i:i + 2] == double:
                    parts.append(text[i:i + 2])
                    i += 2
                elif text[i:i + 2] == '\\' + single:
                    parts.append(text[i:i + 2])
                    i += 2
                elif text[i] == single:
                    parts.append('\\' + single)
                    i += 1
                else:
                    parts.append(text[i])
                    i += 1
            text = ''.join(parts)
    return text


def _sanitize_inline(text: str) -> str:
    """Escape prose segments while preserving inline code spans."""
    segments: list[str] = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(text):
        if m.start() > last:
            segments.append(_escape_md_chars(text[last:m.start()]))
        segments.append(m.group())
        last = m.end()
    if last < len(text):
        segments.append(_escape_md_chars(text[last:]))
    return ''.join(segments)


def sanitize_md(text: str) -> str:
    """Sanitize text for safe markdown embedding.

    Splits on balanced inline code spans and triple-backtick fences.
    Code spans pass through unchanged.  Prose segments get ``<``, ``>``,
    ``|``, leading ``#``, and unbalanced emphasis markers escaped.
    """
    if '```' in text:
        parts = text.split('```')
        result = _sanitize_inline(parts[0].rstrip())
        for i in range(1, len(parts), 2):
            code = parts[i].strip()
            result += f'\n\n```\n{code}\n```'
            if i + 1 < len(parts) and parts[i + 1].strip():
                result += f'\n\n{_sanitize_inline(parts[i + 1].strip())}'
        return result
    return _sanitize_inline(text)


def _loc_text(
    index: dict[SourceLoc, str],
    loc: SourceLoc,
) -> str:
    """Look up display text for a loc, with fallback."""
    text = index.get(loc)
    if text is not None:
        return text
    return f"(loc {loc.line}:{loc.start_char})"


def _build_loc_index(items: list[Any], alive_only: bool = True) -> dict[SourceLoc, str]:
    """Build a loc -> text dict from claims or evidence."""
    index: dict[SourceLoc, str] = {}
    for item in items:
        if alive_only and item.merged_into is not None:
            continue
        index[item.loc] = item.text
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

    ev_by_loc = {e.loc: e for e in evidence if e.merged_into is None}

    supported_locs = {
        s.claim_loc for s in support_map
        if s.status in _SUPPORTED_STATUSES
    }
    unsupported_locs = {
        s.claim_loc for s in support_map if s.status == _STATUS_UNSUPPORTED
    }

    lines.append("## Unsupported Claims\n")
    unsupported = [
        c for c in claims
        if c.merged_into is None and c.loc in unsupported_locs
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
        if c.merged_into is None and c.loc in supported_locs
    ]
    if not supported:
        lines.append("None identified.\n")
    else:
        loc_to_evidence_locs: dict[SourceLoc, list[SourceLoc]] = {}
        for s in support_map:
            if s.status in _SUPPORTED_STATUSES:
                loc_to_evidence_locs[s.claim_loc] = s.evidence_locs

        has_normative = any(c.kind == "normative" for c in supported)
        has_factual = any(c.kind == "factual" for c in supported)
        if has_normative and has_factual:
            for kind_label, kind_value in [("Normative", "normative"), ("Factual", "factual")]:
                kind_claims = [c for c in supported if c.kind == kind_value]
                if kind_claims:
                    lines.append(f"### {kind_label}\n")
                    for c in sorted(kind_claims, key=lambda x: (x.loc.line, x.loc.start_char)):
                        lines.append(f"- {c.question}")
                        for eloc in loc_to_evidence_locs.get(c.loc, []):
                            ev = ev_by_loc.get(eloc)
                            if ev:
                                lines.append(f"  - {sanitize_md(ev.text)} ({ev.section})")
                    lines.append("")
        else:
            for c in sorted(supported, key=lambda x: (x.loc.line, x.loc.start_char)):
                lines.append(f"- {c.question}")
                for eloc in loc_to_evidence_locs.get(c.loc, []):
                    ev = ev_by_loc.get(eloc)
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
        markers = state.markers or []
        lines.append(f"{len(raw_claims)} claims, {len(raw_evidence)} evidence, {len(markers)} markers extracted:\n")

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

        if markers:
            lines.append("### Rhetorical Markers\n")
            for i, m in enumerate(markers, 1):
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
    claim_index = _build_loc_index(claims)
    ev_index = _build_loc_index(evidence)

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
                lines.append(f'- Claim: "{_loc_text(claim_index, ic.claim_loc)}"')
                lines.append(f'  - Contradicted by: "{_loc_text(claim_index, ic.source_loc)}"')
            lines.append("")

        if ev_vs_claim:
            lines.append(f"### Evidence-vs-Claim Contradictions ({len(ev_vs_claim)})\n")
            for ic in ev_vs_claim:
                lines.append(f'- Claim: "{_loc_text(claim_index, ic.claim_loc)}"')
                lines.append(f'  - Contradicted by: "{_loc_text(ev_index, ic.source_loc)}"')
            lines.append("")

        if unsupported:
            lines.append(f"### Unsupported ({len(unsupported)})\n")
            for s in unsupported:
                lines.append(f'- "{_loc_text(claim_index, s.claim_loc)}"')
            lines.append("")

        if transitively:
            lines.append(f"### Transitively Supported ({len(transitively)})\n")
            for s in transitively:
                lines.append(f'- "{_loc_text(claim_index, s.claim_loc)}"')
            lines.append("")

        lines.append(f"### Directly Supported ({len(directly)})\n")
        for s in directly:
            lines.append(f'- "{_loc_text(claim_index, s.claim_loc)}"')
            for eloc in s.evidence_locs:
                lines.append(f'  - <- "{_loc_text(ev_index, eloc)}"')
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
                    lines.append(f'- "{_loc_text(claim_index, item.claim_loc)}"')
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
                    lines.append(f'  - Resolved: "{_loc_text(claim_index, cl)}"')
        else:
            lines.append("No resolutions.")
        lines.append("")

    if stop_step >= 11:
        lines.append("## 11. Caput Causae\n")
        cc = state.caput_causae
        if cc:
            lines.append(f"**Thesis:** {cc.thesis}\n")
            if cc.anchored_claim_locs:
                lines.append(f"Anchored claims ({len(cc.anchored_claim_locs)}):\n")
                for loc in cc.anchored_claim_locs:
                    lines.append(f'- "{_loc_text(claim_index, loc)}"')
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
                    lines.append(f'  - Marker: "{_loc_text(claim_index, a.marker_loc)}"')
                    lines.append(f'  - Claim: "{_loc_text(claim_index, a.claim_loc)}"')
                lines.append("")
            if patterns.concession_clusters:
                lines.append(f"### Concession Clusters ({len(patterns.concession_clusters)})\n")
                for cc in patterns.concession_clusters:
                    lines.append(f"- Topic: {cc.topic} ({len(cc.marker_locs)} markers)")
                lines.append("")
            if patterns.scope_chains:
                lines.append(f"### Scope Chains ({len(patterns.scope_chains)})\n")
                for sc in patterns.scope_chains:
                    lines.append(f"- {sc.paper_id} ({len(sc.marker_locs)} deflections)")
                lines.append("")
        else:
            lines.append("No patterns detected.")
        lines.append("")

    return "\n".join(lines)
