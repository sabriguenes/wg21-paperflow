#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Render the Relatio, plus pipeline trace and debug transcripts.

Verdict first, not last. A reader who must wade through twenty findings
to discover the outcome has been subjected to a punishment, not a
hearing.

This module also renders the diagnostic outputs:
- ``render_debug_md`` - a single agent run as a debug section
- ``render_trace``    - per-step state dump up to a chosen step
"""

from __future__ import annotations

import json
import re
from typing import Any

from advocatus.models import (
    PipelineState,
    Seal,
)

_SEAL_HEADERS = {
    "sine_causa": "Sine causa",
    "cum_objectionibus": "Cum objectionibus",
    "nihil_obstat": "Nihil obstat",
}

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_CHALLENGE_LABELS = {
    "confessio": "Confessio",
    "articulus": "Articulus",
    "testimonium": "Testimonium",
    "humanitas": "Humanitas",
    "prudentia": "Prudentia",
    "dignitas": "Dignitas",
}

_FORUM_LABELS = {
    "lewg": "LEWG",
    "reflector": "reflector",
    "nb_comment": "national body comment",
    "hallway": "hallway",
    "other": "other forum",
}

_DAMAGE_LABELS = {
    "paper_killing": "kills the paper",
    "section_weakening": "weakens the section",
    "revision_forcing": "forces a revision",
    "capital_cost": "costs political capital",
}

_CODE_SPAN_RE = re.compile(r'``.+?``|`[^`]+`')


def _escape_md(text: str) -> str:
    """Escape characters that would break markdown rendering in prose."""
    text = text.replace('<', r'\<').replace('>', r'\>')
    text = text.replace('|', r'\|')
    return text


def _sanitize(text: str) -> str:
    """Escape prose while preserving inline code spans."""
    segments: list[str] = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(text):
        if m.start() > last:
            segments.append(_escape_md(text[last:m.start()]))
        segments.append(m.group())
        last = m.end()
    if last < len(text):
        segments.append(_escape_md(text[last:]))
    return ''.join(segments)


def _seal_label(seal: Seal) -> str:
    return _SEAL_HEADERS.get(seal, seal)


def render_relatio(state: PipelineState) -> str:
    """Render the Relatio markdown from the final pipeline state."""
    pid = state.paper_id or "?"
    title = state.paper_title or "Untitled"
    lines: list[str] = [f"# Relatio: {pid} - {title}\n"]

    # 1. Seal
    seal = state.seal or "sine_causa"
    assessment = state.one_sentence_assessment or ""
    confidence = state.confidence
    lines.append(f"## *{_seal_label(seal)}*\n")
    if assessment:
        lines.append(f"{_sanitize(assessment)}\n")
    if confidence is not None:
        lines.append(f"**Confidence:** {confidence:.2f}\n")

    if seal == "sine_causa":
        lines.append("The tribunal does not convene. The paper contains no claims to examine.\n")
        return "\n".join(lines).rstrip() + "\n"

    # 2. Objections
    objections = state.objections or []
    if objections:
        ordered = sorted(objections, key=lambda o: _SEVERITY_ORDER.get(o.severity, 99))
        lines.append("## Objections\n")
        for i, obj in enumerate(ordered, start=1):
            charge = obj.charge.charge
            forum = _FORUM_LABELS.get(obj.motivatio.forum, obj.motivatio.forum)
            damage = _DAMAGE_LABELS.get(obj.motivatio.damage, obj.motivatio.damage)
            lines.append(
                f"### {i}. [{obj.severity.upper()}] uid {obj.articulus_uid}\n"
            )
            lines.append(f"> {_sanitize(charge.quoted_text)}\n")
            lines.append(f"**Gravamen.** {_sanitize(charge.gravamen)}\n")
            lines.append(
                f"**Motivatio.** {_sanitize(obj.motivatio.adversary)} "
                f"would raise this in {forum}; it {damage}. "
                f"{_sanitize(obj.motivatio.explanation)}\n"
            )

    # 3. Probationes
    probationes = state.probationes or []
    if probationes:
        lines.append("## Probationes\n")
        for p in probationes:
            challenge = _CHALLENGE_LABELS.get(p.killing_challenge, p.killing_challenge)
            lines.append(
                f"- **uid {p.articulus_uid}** - "
                f"the *{challenge}* challenge prevailed. "
                f"{_sanitize(p.explanation)}"
            )
        lines.append("")

    # 4. Tabula Fontium
    tabula = state.tabula_fontium or []
    if tabula:
        lines.append("## Tabula Fontium\n")
        lines.append("| Paper | Resolved | Quote Match | Discrepancy |")
        lines.append("|---|---|---|---|")
        for entry in tabula:
            resolved = "Yes" if entry.resolved else "No"
            disc = _sanitize(entry.discrepancy) if entry.discrepancy else "-"
            lines.append(
                f"| {entry.paper_id} | {resolved} | {entry.quote_match} | {disc} |"
            )
        lines.append("")

    # 5. Acta
    lines.append("## Acta\n")
    lines.extend(_render_acta(state))

    # 6. Notae Minores
    notae = state.notae_minores or []
    if notae:
        lines.append("\n## Notae Minores\n")
        lines.append("<details><summary>Editorial observations</summary>\n")
        for n in notae:
            uid_part = f" (uid {n.uid})" if n.uid is not None else ""
            lines.append(f"- {_sanitize(n.text)}{uid_part}")
        lines.append("\n</details>\n")

    return "\n".join(lines).rstrip() + "\n"


def _render_acta(state: PipelineState) -> list[str]:
    """Audit trail: charges filed, Defensor verdicts, what survived."""
    lines: list[str] = []
    articuli = state.articuli or []
    candidates = state.candidate_charges or []
    survivors = state.surviving_charges or []
    probationes = state.probationes or []
    notae = state.notae_minores or []
    objections = state.objections or []

    lines.append(
        f"- {len(articuli)} articuli examined."
    )
    lines.append(
        f"- {len(candidates)} candidate charges filed."
    )
    lines.append(
        f"- Defensor cross-examination: {len(probationes)} killed, "
        f"{len(notae)} relegated, {len(survivors)} survived."
    )
    lines.append(
        f"- {len(objections)} objections in the final record after motivatio review."
    )

    # Defensor breakdown by challenge
    defensor_results = state.defensor_results or []
    if defensor_results:
        kill_counts: dict[str, int] = {}
        for r in defensor_results:
            if r.final == "killed" and r.challenges:
                last = r.challenges[-1].challenge
                kill_counts[last] = kill_counts.get(last, 0) + 1
        if kill_counts:
            parts = ", ".join(
                f"{_CHALLENGE_LABELS.get(k, k)}: {v}"
                for k, v in sorted(kill_counts.items(), key=lambda kv: -kv[1])
            )
            lines.append(f"- Kill attribution by challenge: {parts}.")

    return lines


# -- Debug + trace renderers ------------------------------------------------


def render_debug_md(result: Any, step_name: str) -> str:
    """Render an agent run result as a markdown debug section.

    Mirrors dissect's render_debug_md: walks the request/response message
    pairs and emits a self-contained markdown block. Safe to call on any
    pydantic_ai ``AgentRunResult``.
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


def render_trace(state: PipelineState, stop_step: int) -> str:
    """Render a per-step state dump up to ``stop_step``.

    Each pipeline step gets a ``## N. Name`` block with the relevant
    state slice. Sections beyond ``stop_step`` are skipped. Sections
    where the relevant state is ``None`` (the step has not run) emit a
    placeholder line so the trace stays readable for partial runs.
    """
    pid = state.paper_id or ""
    title = state.paper_title or "Untitled"
    lines: list[str] = [f"# Trace: {pid} - {title}\n"]

    if stop_step >= 0:
        lines.append("## 0. Load\n")
        articuli_seed = state.dissect_articuli_seed or []
        evidence = state.dissect_evidence or []
        markers = state.dissect_markers or []
        cit_audit = state.dissect_citation_audit or []
        ext_evidence = state.dissect_external_evidence or []
        lines.append(f"- {len(articuli_seed)} dissect claims loaded as articuli seed")
        lines.append(f"- {len(evidence)} dissect evidence items")
        lines.append(f"- {len(markers)} dissect markers")
        lines.append(f"- {len(cit_audit)} citation audit entries")
        lines.append(f"- {len(ext_evidence)} external evidence items")
        if state.dissect_caput_causae:
            lines.append(f"- Caput causae (from dissect): {_sanitize(state.dissect_caput_causae)}")
        lines.append("")

    if stop_step >= 1:
        lines.append("## 1. Read Scripta\n")
        if state.central_thesis_recap:
            lines.append(f"**Thesis recap:** {_sanitize(state.central_thesis_recap)}\n")
        articuli = state.articuli or []
        boundaries = state.boundaries or []
        lines.append(f"{len(articuli)} articuli, {len(boundaries)} boundaries:\n")
        for a in articuli[:30]:
            lines.append(f'- [line {a.loc.line}] [{a.kind}] "{_sanitize(a.text)}"')
        if len(articuli) > 30:
            lines.append(f"  (... {len(articuli) - 30} more)")
        if boundaries:
            lines.append("\n### Boundaries\n")
            for b in boundaries:
                lines.append(f'- [line {b.loc.line}] [{b.kind}] "{_sanitize(b.text)}"')
        lines.append("")

    if stop_step >= 2:
        lines.append("## 2. Survey Public Record\n")
        dossier = state.dossier or []
        public = [d for d in dossier if d.label == "public_record"]
        lines.append(f"{len(public)} public-record entries:\n")
        for d in public[:10]:
            url = f" ({d.source_url})" if d.source_url else ""
            lines.append(f'- "{_sanitize(d.text[:140])}"{url}')
        lines.append("")

    if stop_step >= 3:
        lines.append("## 3. Map Stakeholders\n")
        stakeholders = state.stakeholders or []
        lines.append(f"{len(stakeholders)} stakeholders:\n")
        for s in stakeholders:
            lines.append(f"- [{s.stance}] **{_sanitize(s.name)}**: {_sanitize(s.position)}")
        lines.append("")

    if stop_step >= 4:
        lines.append("## 4. Verify Citations\n")
        tabula = state.tabula_fontium or []
        resolved = sum(1 for t in tabula if t.resolved)
        lines.append(f"{len(tabula)} citations, {resolved} resolved.\n")

    if stop_step >= 5:
        lines.append("## 5. Examine Articuli\n")
        exams = state.exams or []
        if exams:
            failed_v = sum(1 for e in exams if not e.veritas.passed)
            failed_r = sum(1 for e in exams if not e.ratio.passed)
            failed_a = sum(1 for e in exams if not e.auctoritas.passed)
            mean_conf = sum(e.confidence for e in exams) / len(exams)
            lines.append(f"{len(exams)} articuli examined.\n")
            lines.append(f"- Veritas failures: {failed_v}")
            lines.append(f"- Ratio failures: {failed_r}")
            lines.append(f"- Auctoritas failures: {failed_a}")
            lines.append(f"- Mean confidence: {mean_conf:.2f}")
        else:
            lines.append("No exams recorded.")
        lines.append("")

    if stop_step >= 6:
        lines.append("## 6. File Charges\n")
        charges = state.candidate_charges or []
        lines.append(f"{len(charges)} candidate charges:\n")
        for c in charges[:30]:
            lines.append(
                f'- [uid {c.articulus_uid}] [{c.failed_test}] {_sanitize(c.gravamen)}'
            )
        lines.append("")

    if stop_step >= 7:
        lines.append("## 7. Defensor Cross-Examination\n")
        results = state.defensor_results or []
        if results:
            killed = sum(1 for r in results if r.final == "killed")
            relegated = sum(1 for r in results if r.final == "relegated")
            survived = sum(1 for r in results if r.final == "survived")
            lines.append(f"{len(results)} charges examined.\n")
            lines.append(f"- killed: {killed}")
            lines.append(f"- relegated to Notae Minores: {relegated}")
            lines.append(f"- survived: {survived}")
            kill_counts: dict[str, int] = {}
            for r in results:
                if r.final == "killed" and r.challenges:
                    last = r.challenges[-1].challenge
                    kill_counts[last] = kill_counts.get(last, 0) + 1
            if kill_counts:
                parts = ", ".join(
                    f"{_CHALLENGE_LABELS.get(k, k)}: {v}"
                    for k, v in sorted(kill_counts.items(), key=lambda kv: -kv[1])
                )
                lines.append(f"- Kill attribution: {parts}")
        else:
            lines.append("No Defensor results recorded.")
        lines.append("")

    if stop_step >= 8:
        lines.append("## 8. Motivatio\n")
        objections = state.objections or []
        lines.append(f"{len(objections)} objections with motivatio:\n")
        for obj in objections[:20]:
            forum = _FORUM_LABELS.get(obj.motivatio.forum, obj.motivatio.forum)
            damage = _DAMAGE_LABELS.get(obj.motivatio.damage, obj.motivatio.damage)
            lines.append(
                f"- [uid {obj.articulus_uid}] [{obj.severity.upper()}] "
                f"adversary: {_sanitize(obj.motivatio.adversary)}; forum: {forum}; "
                f"damage: {damage}"
            )
        lines.append("")

    if stop_step >= 9:
        lines.append("## 9. Weigh the Cause\n")
        if state.seal:
            lines.append(f"**Seal:** {state.seal}")
            lines.append(f"**Central thesis survives:** {state.central_thesis_survives}")
            if state.one_sentence_assessment:
                lines.append(f"**Assessment:** {_sanitize(state.one_sentence_assessment)}")
            if state.confidence is not None:
                lines.append(f"**Confidence:** {state.confidence:.2f}")
        else:
            lines.append("Not weighed.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
