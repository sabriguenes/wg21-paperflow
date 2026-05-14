#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Debug transcript and pipeline trace renderers for agora.

The agora pipeline emits its main artifact (the planned ``Thread``) as
JSON via paperstore. This module only handles the diagnostic outputs:

- :func:`render_debug_md` - one agent run captured as a markdown
  debug section. Concatenated per pipeline run when ``debug=True``.
- :func:`render_trace` - per-step state dump up to a chosen step.
  Useful for partial runs (``--trace N``) and for full post-run
  inspection (``--trace`` alone).

HTML rendering (Reddit thread, ads, mod actions) is **not** done
here. Presentation is the Django module's responsibility downstream.
"""

from __future__ import annotations

import json
from typing import Any

from pipeline import sanitize_md

from agora.models import PipelineState

# -- Debug renderer ----------------------------------------------------------


def render_debug_md(result: Any, step_name: str) -> str:
    """Render an agent run result as a markdown debug section.

    Walks the request/response message pairs and emits a self-contained
    markdown block. Safe to call on any pydantic_ai ``AgentRunResult``.
    Sub-agent calls are captured separately by the runner and rendered
    via the same function.
    """
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


# -- Trace renderer ----------------------------------------------------------


def render_trace(state: PipelineState, stop_step: int) -> str:
    """Render a per-step state dump up to ``stop_step`` (inclusive).

    Each pipeline step gets a ``## N. Name`` block with the relevant
    state slice. Sections beyond ``stop_step`` are skipped. Sections
    where the relevant state is ``None`` (the step has not run) emit a
    placeholder so the trace stays readable for partial runs.
    """
    pid = state.paper_id or "?"
    title = state.paper_title or "Untitled"
    lines: list[str] = [f"# Trace: {pid} - {title}\n"]

    if stop_step >= 0:
        lines.append("## 0. Load\n")
        lines.append(f"- subreddit: {state.subreddit or '-'}")
        lines.append(
            f"- audience: {state.paper_audience or '-'}; "
            f"revision: R{state.paper_revision} (case {state.revision_case})"
        )
        claims = state.dissect_claims or []
        evidence = state.dissect_evidence or []
        markers = state.dissect_markers or []
        cit_audit = state.dissect_citation_audit or []
        ext = state.dissect_external_citations or []
        lines.append(
            f"- dissect: {len(claims)} claims, {len(evidence)} evidence, "
            f"{len(markers)} markers, {len(cit_audit)} citation-audits, "
            f"{len(ext)} external citations"
        )
        if state.dissect_caput_causae:
            lines.append(
                f"- caput causae: {sanitize_md(state.dissect_caput_causae)}"
            )
        if state.prior_revision:
            lines.append(f"- prior revision: {state.prior_revision}")
        lines.append("")

    if stop_step >= 1:
        lines.append("## 1. Smell Test\n")
        if state.paper_type:
            lines.append(f"- paper_type: **{state.paper_type}**")
        anchors = state.technical_anchors or []
        hot = state.hot_takes or []
        tangents = state.tangent_magnets or []
        traps = state.misconception_traps or []
        tensions = state.design_tensions or []
        lines.append(
            f"- {len(anchors)} technical anchors, {len(tensions)} design tensions, "
            f"{len(hot)} hot takes, {len(tangents)} tangent magnets, "
            f"{len(traps)} misconception traps"
        )
        for a in anchors[:20]:
            loc_info = f" (line {a.claim_loc.line})" if a.claim_loc else ""
            lines.append(
                f'  - [{a.kind}] **{a.id}**{loc_info}: '
                f'{sanitize_md(a.summary)}'
            )
        if len(anchors) > 20:
            lines.append(f"  - (... {len(anchors) - 20} more)")
        for t in tensions[:10]:
            lines.append(f"  - tension **{t.id}**: {sanitize_md(t.description)}")
        lines.append("")

    if stop_step >= 2:
        lines.append("## 2. Research\n")
        rs = state.research_summary
        if rs is None:
            lines.append("- (no research summary recorded)\n")
        else:
            for label, rep in (
                ("public reception", rs.public_reception),
                ("committee history", rs.committee_history),
                ("author + ecosystem", rs.author_ecosystem),
            ):
                lines.append(
                    f"### {label}\n"
                    f"- heat={rep.heat_signal}, interest={rep.interest_signal}, "
                    f"sources={len(rep.sources)}\n"
                )
                lines.append(f"{sanitize_md(rep.findings)}\n")

    if stop_step >= 3:
        lines.append("## 3. Calibrate\n")
        if state.heat or state.interest:
            lines.append(
                f"- heat=**{state.heat}**, interest=**{state.interest}**, "
                f"target_comments={state.target_comment_count}"
            )
            lines.append(
                f"- planned: signal={state.signal_count}, "
                f"noise={state.noise_count}, encounters={state.encounter_count}"
            )
        else:
            lines.append("- (not calibrated)")
        lines.append("")

    if stop_step >= 4:
        lines.append("## 4. Submission\n")
        if state.submission_title:
            lines.append(
                f"**Title:** {sanitize_md(state.submission_title)}\n"
            )
            link = state.submission_link or "-"
            lines.append(f"**Link:** {link}\n")
            if state.submission_flair:
                lines.append(f"**Flair:** {state.submission_flair}\n")
            if state.submission_body:
                body_preview = state.submission_body.strip().splitlines()[:8]
                for ln in body_preview:
                    lines.append(f"> {sanitize_md(ln)}")
                lines.append("")
        else:
            lines.append("- (no submission)\n")

    if stop_step >= 5:
        lines.append("## 5. Skeleton\n")
        replies = state.replies or []
        groups = state.encounter_slot_groups or []
        if replies:
            role_counts: dict[str, int] = {}
            for r in replies:
                role_counts[r.role] = role_counts.get(r.role, 0) + 1
            parts = ", ".join(
                f"{role}={n}"
                for role, n in sorted(role_counts.items(), key=lambda kv: -kv[1])
            )
            lines.append(
                f"- {len(replies)} planned reply slots ({parts})"
            )
            lines.append(
                f"- encounter slot groups pre-allocated: {len(groups)}"
            )
            top_level = [r for r in replies if r.parent_slot_id is None]
            lines.append(f"- top-level slots: {len(top_level)}")
            for r in replies[:25]:
                anchor = f" anchor={r.anchor_id}" if r.anchor_id else ""
                lens = f" lens={r.domain_lens}" if r.domain_lens is not None else ""
                lines.append(
                    f"  - **{r.slot_id}** (depth {r.depth}, {r.role}{anchor}{lens}) "
                    f"-> {sanitize_md(r.brief)}"
                )
            if len(replies) > 25:
                lines.append(f"  - (... {len(replies) - 25} more)")
        else:
            lines.append("- (no skeleton)")
        lines.append("")

    if stop_step >= 6:
        lines.append("## 6. Encounters\n")
        encounters = state.encounters or []
        if encounters:
            lines.append(f"- {len(encounters)} encounters:\n")
            for e in encounters:
                lines.append(
                    f"### {e.encounter_id} ({e.resolution})\n"
                    f"- tension: {sanitize_md(e.design_tension)}\n"
                    f"- position A: {sanitize_md(e.position_a)}\n"
                    f"- position B: {sanitize_md(e.position_b)}\n"
                    f"- slots: {', '.join(e.slot_ids)}\n"
                )
        else:
            lines.append("- (no encounters; either guard skipped or count was 0)")
        lines.append("")

    if stop_step >= 7:
        lines.append("## 7. Serialize\n")
        if state.thread is not None:
            t = state.thread
            lines.append(
                f"- thread for **{t.document}** ({t.subreddit}) - "
                f"{len(t.replies)} replies, {len(t.encounters)} encounters"
            )
            lines.append(
                f"- heat={t.heat}, interest={t.interest}, "
                f"target_comment_count={t.target_comment_count}"
            )
        else:
            lines.append("- (not serialized)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
