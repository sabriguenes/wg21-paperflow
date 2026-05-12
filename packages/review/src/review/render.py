#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Rendering functions for pipeline output.

Produces three output formats from ``PipelineState``:

- ``render_report`` -- the final review markdown (unsupported/supported
  claims + external resources).
- ``render_trace`` -- diagnostic trace at each ``stop_after`` level.
- ``render_debug_md`` -- full LLM interaction transcript for debugging.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from review.models import PipelineState, SourceLoc

_STATUS_DIRECTLY = "directly_supported"
_STATUS_TRANSITIVELY = "transitively_supported"
_STATUS_UNSUPPORTED = "unsupported"
_SUPPORTED_STATUSES = (_STATUS_DIRECTLY, _STATUS_TRANSITIVELY)

_TOOL_OMIT_NAMES = frozenset({
    "web_search", "web_fetch", "read_file", "paper_meta", "paper_meta_latest",
})


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
            output_str = json.dumps(
                output.model_dump(), indent=2, ensure_ascii=False,
            )
        else:
            output_str = str(output)
        parts.append(f"## Final Output\n\n```json\n{output_str}\n```\n")
    return "\n".join(parts)


def safe_quote(text: str) -> str:
    """Format text for trace output, handling embedded code fences.

    If text contains triple backticks, splits into preamble + standalone
    fenced block so markdown renders correctly.
    """
    if "```" not in text:
        return f'"{text}"'
    parts = text.split("```")
    result = f'"{parts[0].rstrip()}"'
    for i in range(1, len(parts), 2):
        code = parts[i].strip()
        result += f"\n\n```\n{code}\n```"
        if i + 1 < len(parts) and parts[i + 1].strip():
            result += f"\n\n{parts[i + 1].strip()}"
    return result


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
    """Render the final review as structured markdown."""
    lines: list[str] = [f"# {pid}: {title}\n"]

    claims = state.claims or []
    support_map = state.support_map or []
    external_evidence = state.external_evidence or []
    evidence = state.evidence or []

    ev_index = _build_loc_index(evidence)

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
        for c in sorted(unsupported, key=lambda x: (x.loc.line, x.loc.start_char)):
            lines.append(f'- **"{c.text}"** ({c.section})')
            lines.append(f"  - {c.question}")
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

        for c in sorted(supported, key=lambda x: (x.loc.line, x.loc.start_char)):
            lines.append(f'- **"{c.text}"** ({c.section})')
            for eloc in loc_to_evidence_locs.get(c.loc, []):
                ev_text = ev_index.get(eloc)
                if ev_text:
                    lines.append(f'  - "{ev_text}" ({eloc.line})')
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


def render_trace(state: PipelineState, meta: Mapping[str, Any], stop_step: int) -> str:
    """Render a diagnostic trace of pipeline state up to stop_step."""
    title = meta.get("title", "Untitled")
    pid = meta.get("paper_id", "")
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
        lines.append("## 1. Extract Claims\n")
        raws = state.raw_claims or []
        lines.append(f"{len(raws)} claims extracted:\n")
        for i, rc in enumerate(raws[:50], 1):
            lines.append(f"{i}. {safe_quote(rc.text)} ({rc.section})")
            if rc.question:
                lines.append(f"  - Q: {rc.question}")
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
                lines.append(f"{i}. {safe_quote(c.text)} ({c.section})")
                if c.question:
                    lines.append(f"   - Q: {c.question}")
        lines.append("")

    if stop_step >= 3:
        lines.append("## 3. Extract Evidence\n")
        raws = state.raw_evidence or []
        lines.append(f"{len(raws)} evidence items extracted:\n")
        for i, re_ in enumerate(raws[:50], 1):
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
            lines.append(f"{i}. {safe_quote(re_.text)} ({re_.section})")
            lines.append(f'   - Supports: "{supports_str}"{flag_str}')
        lines.append("")

    if stop_step >= 4:
        lines.append("## 4. Dedup Evidence\n")
        all_ev = state.evidence or []
        survivors, merged = _partition_merged(all_ev)
        lines.append(f"{len(all_ev)} -> {len(survivors)} survivors ({len(merged)} merged):\n")
        for i, e in enumerate(all_ev, 1):
            if e.merged_into is not None:
                lines.append(f"{i}. [tombstone]")
            else:
                supports_str = e.supports[0] if e.supports else ""
                lines.append(f"{i}. {safe_quote(e.text)} ({e.section})")
                lines.append(f'   - Supports: "{supports_str}"')
        lines.append("")

    claims = state.claims or []
    evidence = state.evidence or []
    claim_index = _build_loc_index(claims)
    ev_index = _build_loc_index(evidence)

    if stop_step >= 5:
        lines.append("## 5. Verify + Deps + Map\n")
        smap = state.support_map or []

        directly = [s for s in smap if s.status == _STATUS_DIRECTLY]
        transitively = [s for s in smap if s.status == _STATUS_TRANSITIVELY]
        unsupported = [s for s in smap if s.status == _STATUS_UNSUPPORTED]
        contras = state.internal_contradictions or []

        if contras:
            lines.append(f"### Internal Contradictions ({len(contras)})\n")
            for ic in contras:
                lines.append(f'- Claim: "{_loc_text(claim_index, ic.claim_loc)}"')
                lines.append(f'  - Contradicted by: "{_loc_text(ev_index, ic.evidence_loc)}"')
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

    if stop_step >= 6:
        lines.append("## 6. Load-Bearing\n")
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

    if stop_step >= 7:
        lines.append("## 7. Web Search\n")
        ext = state.external_evidence or []
        lines.append(f"{len(ext)} external evidence items found:\n")
        for ex in ext[:10]:
            lines.append(f"- [{ex.source_title}]({ex.source_url}) \u2014 {ex.stance}")
            lines.append(f"  - {ex.finding}")
        lines.append("")

    if stop_step >= 8:
        lines.append("## 8. Resolve External\n")
        resolutions = state.web_resolutions or []
        if resolutions:
            lines.append(f"{len(resolutions)} resolutions applied:\n")
            for wr in resolutions:
                lines.append(f"- [{wr.finding}]({wr.source_url}) \u2014 {wr.stance}")
                for cl in wr.resolved_claims:
                    lines.append(f"  - Resolved: \"{_loc_text(claim_index, cl)}\"")
        else:
            lines.append("No resolutions.")
        lines.append("")

    return "\n".join(lines)
