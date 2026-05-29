#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Assay report and trace rendering.

Renders the final markdown report (Step 16) via a Jinja template
embedded in ``assay.md``, and diagnostic trace dumps for
--trace/--step.

The report pipeline:

1. ``prepare_report_data(state)`` collates PipelineState into a clean
   ``ReportData`` dataclass tree (sorted, counted, presentation-ready).
2. ``render_report(state, section_text)`` extracts the Jinja template
   from the section body and renders it with the prepared data.
3. ``rerender_report(pid, backend)`` loads state from DB and re-applies
   the current template (for ``--rerender``).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from jinja2 import Template

from pipeline import extract_code_blocks, load_sections

from assay.models import (
    AskOutput,
    GapOutput,
    ChecklistItem,
    CollectedItem,
    CollectedItems,
    CompoundOutput,
    DeriveOutput,
    FindingOutput,
    KilledFinding,
    PipelineState,
    StrengthOutput,
    SynthesisOutput,
)

_EEL_IS_BASE = "https://eel.is/c++draft"
_LINKIFY_RE = re.compile(r"(?<!\()\[([a-z][a-z0-9.]+)\](?!\()")


def _linkify_stable_labels(text: str) -> str:
    """Convert [stable.label] references to eel.is hyperlinks in markdown.

    Skips labels already inside markdown links (preceded or followed by
    parentheses) to avoid double-linking.
    """
    def _replace(m: re.Match) -> str:
        label = m.group(1)
        return f"[{label}]({_EEL_IS_BASE}/{label})"
    return _LINKIFY_RE.sub(_replace, text)


SEVERITY_ORDER = {"critical": 0, "significant": 1, "minor": 2}


# -- Report data model -------------------------------------------------------


@dataclass
class FindingEntry:
    """A single finding ready for template rendering."""
    number: int
    title: str
    severity: str
    lens: str
    test: str
    quote: str
    line: int
    explanation: str
    examiner: str = ""
    damage: str = ""


@dataclass
class CompoundEntry:
    """A compound dynamic ready for template rendering."""
    name: str
    constituents: list[str] = field(default_factory=list)
    mechanism: str = ""
    emergent_risk: str = ""


@dataclass
class ChecklistEntry:
    """An SD-4 rationale checklist item."""
    id: str
    name: str
    passed: bool
    passed_str: str
    location: str
    note: str


@dataclass
class RenderPaperRef:
    """A paper-number reference for the report table."""
    raw_pid: str
    pid: str
    url: str
    link: str
    count: int
    status: str


@dataclass
class RenderUrlEntry:
    """A standalone URL for the report table."""
    url: str
    link: str
    line: int


@dataclass
class StrengthEntry:
    """A strength entry."""
    title: str
    quote: str
    line: int
    explanation: str


@dataclass
class InventoryData:
    """Aggregate counts for the inventory section."""
    claim_count: int = 0
    evidence_count: int = 0
    concession_count: int = 0
    question_count: int = 0
    dependency_count: int = 0
    gap_total: int = 0
    gap_critical: int = 0
    gap_significant: int = 0
    gap_minor: int = 0
    findings_generated: int = 0
    findings_survived: int = 0
    findings_killed: int = 0
    killed_breakdown: str = ""
    major_count: int = 0
    regular_count: int = 0
    compound_count: int = 0
    strength_count: int = 0


@dataclass
class ReportData:
    """Complete, presentation-ready data for the Jinja report template.

    All sorting, counting, and conditional computation is done by
    ``prepare_report_data`` before this object is constructed. The
    template only iterates and displays.
    """
    pid: str = ""
    title: str = ""
    verdict_statement: str = ""

    verdict_label: str = "Insufficient"
    confidence: str = "Medium"
    thesis_statement: str = ""
    thesis_survives: bool = False
    critical_count: int = 0
    significant_count: int = 0
    minor_count: int = 0

    asks: list[dict] = field(default_factory=list)
    intent: str = ""
    ask_calibration: str = ""
    wording_lines: int = 0
    targets_cwg_lwg: bool = False

    has_structural: bool = False
    dominant_dynamic: str = ""
    structural_summary: str = ""

    compounds: list[CompoundEntry] = field(default_factory=list)

    major_findings: list[FindingEntry] = field(default_factory=list)
    regular_findings: list[FindingEntry] = field(default_factory=list)
    total_survived: int = 0
    total_killed: int = 0

    strengths: list[StrengthEntry] = field(default_factory=list)

    checklist: list[ChecklistEntry] = field(default_factory=list)
    checklist_passed: int = 0
    checklist_total: int = 0

    paper_refs: list[RenderPaperRef] = field(default_factory=list)
    standalone_urls: list[RenderUrlEntry] = field(default_factory=list)

    inventory: InventoryData = field(default_factory=InventoryData)

    chunk_count: int = 0


# -- Data preparation --------------------------------------------------------


def prepare_report_data(state: PipelineState) -> ReportData:
    """Collate PipelineState into a clean, sorted ReportData for rendering.

    Performs all computation (sorting, counting, flattening) so the
    template receives pre-baked values only.
    """
    synthesis = state.synthesis
    items = state.items or CollectedItems()

    critical = synthesis.critical_count if synthesis else 0
    significant = synthesis.significant_count if synthesis else 0
    survived_count = len(state.surviving or [])
    minor_count = max(0, survived_count - critical - significant)

    major_raw = synthesis.major_findings if synthesis else []
    regular_raw = synthesis.regular_findings if synthesis else []

    major_findings = _prepare_findings(major_raw, start_num=1)
    regular_findings = _prepare_findings(regular_raw, start_num=len(major_raw) + 1)

    compounds = [
        CompoundEntry(
            name=c.name,
            constituents=list(c.constituents),
            mechanism=c.mechanism,
            emergent_risk=c.emergent_risk or "",
        )
        for c in (state.compounds or [])
    ]

    checklist_items = state.checklist or []
    checklist = [
        ChecklistEntry(
            id=c.id,
            name=c.name,
            passed=c.passed,
            passed_str="pass" if c.passed else "fail",
            location=c.location or "absent",
            note=c.note or "",
        )
        for c in checklist_items
    ]
    checklist_passed = sum(1 for c in checklist_items if c.passed)

    paper_refs = []
    for r in state.ref_pids:
        parts = []
        if r.in_paperstore:
            parts.append("in-paperstore")
        if r.stale:
            parts.append("stale")
        if r.author_overlap > 0:
            parts.append(f"overlap:{r.author_overlap:.2f}")
        status = ", ".join(parts)
        link = f"[{r.raw_pid}]({r.url})" if r.url else r.raw_pid
        paper_refs.append(RenderPaperRef(
            raw_pid=r.raw_pid, pid=r.paper_id, url=r.url,
            link=link, count=r.count, status=status,
        ))

    standalone_urls = [
        RenderUrlEntry(url=u.url, link=f"[link]({u.url})", line=u.line)
        for u in state.ref_urls
    ]

    strengths = [
        StrengthEntry(
            title=s.title,
            quote=s.quote,
            line=s.line,
            explanation=s.explanation,
        )
        for s in (state.strengths or [])
    ]

    all_gaps: list[GapOutput] = []
    for lens_list in (state.gaps_by_lens or {}).values():
        all_gaps.extend(lens_list)
    gap_sev = Counter(b.severity for b in all_gaps)

    killed_list = state.killed or []
    killed_breakdown = ""
    if killed_list:
        challenge_counts = Counter(k.challenge for k in killed_list)
        killed_breakdown = ", ".join(f"{v} {k}" for k, v in challenge_counts.most_common())

    has_structural = bool(major_raw or compounds)
    structural_summary = ""
    if major_raw:
        compound_count = sum(1 for f in major_raw
                            if f.title in {t for c in (state.compounds or []) for t in c.constituents})
        thesis_count = len(major_raw) - compound_count
        parts = []
        if compound_count:
            parts.append(f"{compound_count} participate in compound dynamics")
        if thesis_count:
            parts.append(f"{thesis_count} overlap the thesis")
        structural_summary = f"{len(major_raw)} major findings: {', '.join(parts)}." if parts else ""

    asks_dicts = [{"target": a.target, "quote": a.quote, "type": a.type, "line": a.line}
                  for a in (state.asks or [])]

    return ReportData(
        pid=state.paper_id,
        title=state.paper_title,
        verdict_statement=synthesis.verdict_statement if synthesis else "",
        verdict_label=synthesis.verdict_label if synthesis else "Insufficient",
        confidence=synthesis.verdict_confidence if synthesis else "Medium",
        thesis_statement=synthesis.thesis_statement if synthesis else "",
        thesis_survives=synthesis.thesis_survives if synthesis else False,
        critical_count=critical,
        significant_count=significant,
        minor_count=minor_count,
        asks=asks_dicts,
        intent=state.intent,
        ask_calibration=state.derive.ask_calibration if state.derive else "",
        wording_lines=state.wording_lines,
        targets_cwg_lwg=state.targets_cwg_lwg,
        has_structural=has_structural,
        dominant_dynamic=(synthesis.dominant_dynamic or "") if synthesis else "",
        structural_summary=structural_summary,
        compounds=compounds,
        major_findings=major_findings,
        regular_findings=regular_findings,
        total_survived=survived_count,
        total_killed=len(killed_list),
        strengths=strengths,
        checklist=checklist,
        checklist_passed=checklist_passed,
        checklist_total=len(checklist_items),
        paper_refs=paper_refs,
        standalone_urls=standalone_urls,
        inventory=InventoryData(
            claim_count=len(items.claims),
            evidence_count=len(items.evidence),
            concession_count=len(items.concessions),
            question_count=len(items.questions),
            dependency_count=len(items.dependencies),
            gap_total=len(all_gaps),
            gap_critical=gap_sev.get("critical", 0),
            gap_significant=gap_sev.get("significant", 0),
            gap_minor=gap_sev.get("minor", 0),
            findings_generated=len(state.findings or []),
            findings_survived=survived_count,
            findings_killed=len(killed_list),
            killed_breakdown=killed_breakdown,
            major_count=len(major_raw),
            regular_count=len(regular_raw),
            compound_count=len(compounds),
            strength_count=len(strengths),
        ),
        chunk_count=len(state.chunk_map or []),
    )


def _prepare_findings(raw: list[FindingOutput], start_num: int) -> list[FindingEntry]:
    """Sort findings by severity and number them sequentially."""
    sorted_raw = sorted(raw, key=lambda f: SEVERITY_ORDER.get(f.severity, 3))
    return [
        FindingEntry(
            number=i,
            title=f.title,
            severity=f.severity,
            lens=f.lens,
            test=f.test,
            quote=f.quote,
            line=f.line,
            explanation=f.explanation,
            examiner=f.examiner,
            damage=f.damage,
        )
        for i, f in enumerate(sorted_raw, start=start_num)
    ]


# -- Report rendering --------------------------------------------------------


def render_report(state: PipelineState, section_text: str) -> str:
    """Render the assay report using the Jinja template from *section_text*.

    *section_text* is the raw body of the ``## 12. Report`` section from
    ``assay.md``, which contains a fenced Jinja template. The template is
    extracted, the state is collated into ``ReportData``, and the template
    is rendered with the flattened data.

    For skipped papers (verdict == "Skipped"), falls back to a hardcoded
    summary format.
    """
    synthesis = state.synthesis
    if synthesis is not None and synthesis.verdict_label == "Skipped":
        return _render_skipped_report(state, synthesis)

    blocks = extract_code_blocks(section_text)
    if not blocks:
        raise RuntimeError(
            "No Jinja template found in the '17. Report' section of assay.md. "
            "Expected a fenced code block containing the report template."
        )

    data = prepare_report_data(state)
    tmpl = Template(blocks[0], keep_trailing_newline=True,
                    trim_blocks=True, lstrip_blocks=True)
    report = tmpl.render(vars(data))
    return _linkify_stable_labels(report)


def rerender_report(pid: str, backend) -> str:
    """Regenerate the assay report from stored DB data + current template.

    Loads all persisted assay artifacts from the database, reconstructs
    a PipelineState sufficient for report rendering, and applies the
    current Jinja template from ``assay.md``.

    Use via ``paperflow assay <pid> --rerender``.
    """
    state = load_assay_state(pid, backend)
    secs = dict(load_sections("assay", "assay.md"))
    section_text = secs.get("17. Report", "")
    return render_report(state, section_text)


# -- DB state loader ---------------------------------------------------------


def load_assay_state(pid: str, backend) -> PipelineState:
    """Reconstruct a report-ready PipelineState from database rows.

    Loads claims, evidence, concessions, gaps, thesis, findings,
    asks, references, strengths, checklist, compounds, and synthesis.
    Does NOT load paper_md or chunk_map (not needed for report).
    """
    meta = backend.get_meta(pid)

    claims_rows = backend.get_assay_claims(pid)
    evidence_rows = backend.get_assay_evidence(pid)
    gap_rows = backend.get_assay_gaps(pid)
    thesis_row = backend.get_assay_thesis(pid)
    finding_rows = backend.get_assay_findings(pid)
    ask_rows = backend.get_assay_asks(pid)
    pid_rows = backend.get_assay_pids(pid)
    url_rows = backend.get_assay_urls(pid)
    strength_rows = backend.get_assay_strengths(pid)
    checklist_rows = backend.get_assay_checklist(pid)
    compound_rows = backend.get_assay_compounds(pid)
    synthesis_row = backend.get_assay_synthesis(pid)

    claims = [
        CollectedItem(type="claim", line=r.loc_line, quote=r.quote,
                      section=r.section)
        for r in claims_rows
    ]
    evidence = [
        CollectedItem(type="evidence", line=r.loc_line, quote=r.quote,
                      section=r.section, quality_tier=r.quality_tier)
        for r in evidence_rows
    ]
    concession_rows = backend.get_assay_concessions(pid)
    concessions = [
        CollectedItem(type="concession", line=r.loc_line, quote=r.quote,
                      section=r.section if hasattr(r, 'section') else "")
        for r in concession_rows if hasattr(r, 'loc_line')
    ]

    items = CollectedItems(
        claims=claims,
        evidence=evidence,
        concessions=concessions,
    )

    gaps_by_lens: dict[str, list[GapOutput]] = {}
    for b in gap_rows:
        lens = b.primary_lens or "Other"
        g = GapOutput(
            chunk_index=b.chunk_index, item_quote="", line=b.loc_line,
            gap=b.gap, why_important=b.why_important,
            primary_lens=b.primary_lens, secondary_lens=b.secondary_lens or None,
            severity=b.severity,
        )
        gaps_by_lens.setdefault(lens, []).append(g)

    derive = None
    if thesis_row:
        derive = DeriveOutput(
            central_claim=thesis_row.central_claim,
            problem_statement=thesis_row.problem_statement,
            scope_boundary=thesis_row.scope_boundary,
            ask_calibration=thesis_row.ask_calibration,
        )

    surviving: list[FindingOutput] = []
    killed: list[KilledFinding] = []
    findings_all: list[FindingOutput] = []
    major_titles: set[str] = set()

    for f in finding_rows:
        if f.survived:
            fo = FindingOutput(
                title=f.title, lens=f.lens, severity=f.severity,
                quote=f.quote, line=f.loc_line, explanation=f.explanation,
                test=f.test,
            )
            findings_all.append(fo)
            surviving.append(fo)
            if f.major:
                major_titles.add(f.title)
        else:
            killed.append(KilledFinding(
                finding_id=f.uid, finding_title=f.title, lens=f.lens,
                challenge=f.challenge, reasoning=f.reasoning,
            ))

    asks = [AskOutput(target=a.target, quote=a.quote, type=a.type, line=0)
            for a in ask_rows]

    from assay.references import RefEntry, UrlEntry
    ref_pids = [
        RefEntry(
            paper_id=r.resolved_pid, raw_pid=r.raw_pid, url=r.url,
            count=r.mention_count, in_paperstore=r.in_paperstore,
            stale=r.stale, author_overlap=r.author_overlap,
        )
        for r in pid_rows
    ]
    ref_urls = [
        UrlEntry(url=u.url, line=u.line)
        for u in url_rows
    ]

    strengths_list = [
        StrengthOutput(
            title=s.title, quote=s.quote, line=s.loc_line,
            explanation=s.explanation, lens=getattr(s, 'lens', ''),
        )
        for s in strength_rows
    ]

    checklist_list = [
        ChecklistItem(
            id=c.item_id, name=c.name, passed=c.passed,
            location=c.location, note=c.note,
        )
        for c in checklist_rows
    ]

    compounds_list = [
        CompoundOutput(
            name=c.name, constituents=c.constituents,
            mechanism=c.mechanism, cross_lens=c.cross_lens,
            emergent_risk=c.emergent_risk,
        )
        for c in compound_rows
    ]

    synthesis = None
    if synthesis_row:
        major_findings = [f for f in surviving if f.title in major_titles]
        regular_findings = [f for f in surviving if f.title not in major_titles]
        synthesis = SynthesisOutput(
            verdict_label=synthesis_row.verdict,
            verdict_confidence=synthesis_row.verdict_confidence,
            thesis_statement=synthesis_row.thesis_statement,
            thesis_survives=synthesis_row.thesis_survives,
            verdict_statement=synthesis_row.central_thesis,
            dominant_dynamic=synthesis_row.dominant_dynamic or None,
            critical_count=synthesis_row.critical_count,
            significant_count=synthesis_row.significant_count,
            major_findings=major_findings,
            regular_findings=regular_findings,
        )

    state = PipelineState(
        paper_id=pid,
        paper_title=meta.title or "",
        items=items,
        gaps_by_lens=gaps_by_lens,
        derive=derive,
        asks=asks,
        ref_pids=ref_pids,
        ref_urls=ref_urls,
        findings=findings_all,
        surviving=surviving,
        killed=killed,
        strengths=strengths_list,
        checklist=checklist_list,
        compounds=compounds_list,
        synthesis=synthesis,
    )

    return state


def render_trace(state: PipelineState, step: int, *, step_durations: list[float] | None = None) -> str:
    """Render diagnostic trace dump after step N.

    Every executed step gets a ## heading. Items are grouped by type
    under ### subheadings. Trace is compact: truncated quotes, counts,
    bracketed qualifiers. Full data lives in debug.
    """
    _TRACE_STEPS = [
        "Receive", "References", "Index", "Survey", "Extract", "Decide",
        "Classify", "Collect", "Derive", "Verify", "Research", "Probe",
        "Analyze", "Rationale", "Challenge", "Couple", "Synthesize", "Report",
    ]
    _QUOTE_LEN = 60

    def _q(text: str) -> str:
        text = text.replace("\n", " ").strip()
        if len(text) > _QUOTE_LEN:
            return f'"{text[:_QUOTE_LEN]}..."'
        return f'"{text}"'

    lines: list[str] = []

    for i in range(min(step + 1, len(_TRACE_STEPS))):
        duration = ""
        if step_durations and i < len(step_durations):
            d = step_durations[i]
            duration = f" ({d:.1f}s)" if d < 60 else f" ({d / 60:.1f}m)"
        lines.append(f"## Step {i} ({_TRACE_STEPS[i]}){duration}")
        lines.append("")
        before = len(lines)

        if i == 0:
            if state.audience:
                lines.append(f"- audience: {', '.join(state.audience)}")
            if state.intent:
                lines.append(f"- intent: {state.intent}")
            lines.append("")

        elif i == 1:
            if state.ref_pids:
                refs = state.ref_pids
                in_ps = sum(1 for r in refs if r.in_paperstore)
                overlap_count = sum(1 for r in refs if r.author_overlap > 0)
                summary_parts = [f"{len(refs)} refs"]
                if in_ps:
                    summary_parts.append(f"{in_ps} in paperstore")
                if overlap_count:
                    summary_parts.append(f"{overlap_count} author overlap")
                lines.append(f"{', '.join(summary_parts)}:")
                for r in refs:
                    flags = []
                    if not r.in_paperstore:
                        flags.append("not found")
                    if r.stale:
                        flags.append("stale")
                    if r.author_overlap > 0:
                        flags.append(f"overlap:{r.author_overlap:.2f}")
                    count = r.count or 1
                    flag_str = f", {', '.join(flags)}" if flags else ""
                    lines.append(f"- {r.raw_pid}->{r.paper_id} (x{count}{flag_str})")
                lines.append("")

        elif i == 2:
            if state.index_stats is not None:
                st = state.index_stats
                lines.append(f"{st.papers_indexed} papers indexed, {st.total_chunks} chunks, dim={st.embedding_dim}, {st.embed_time_ms:.0f}ms")
                for pid, rel, count in st.per_paper:
                    lines.append(f"- {pid} ({rel}): {count} chunks")
                if st.skipped:
                    lines.append(f"- skipped: {', '.join(st.skipped)}")
                lines.append("")
            else:
                lines.append("no cited papers in paperstore (skipped)")
                lines.append("")

        elif i == 3:
            if state.chunk_map is not None:
                lines.append(f"{len(state.chunk_map)} chunks:")
                for c in state.chunk_map:
                    heading = c.heading if len(c.heading) <= 50 else c.heading[:47] + "..."
                    lines.append(f"- [{c.index}] {heading} (lines {c.start_line}-{c.end_line}, ~{c.char_count // 3} tokens)")
                lines.append("")
            if state.wording_lines or state.targets_cwg_lwg:
                wording_parts = []
                if state.wording_lines:
                    wording_parts.append(f"wording_lines={state.wording_lines}")
                if state.targets_cwg_lwg:
                    wording_parts.append("targets_cwg_lwg")
                lines.append(f"wording: {', '.join(wording_parts)}")
                lines.append("")
            if state.synthesis is not None and state.synthesis.verdict_label == "Skipped":
                lines.append(f"triage: skipped ({state.synthesis.skip_reason})")
                lines.append("")

        elif i == 4:
            if state.raw_extractions is not None:
                all_items: list = []
                for ext in state.raw_extractions:
                    all_items.extend(ext.items)

                n_chunks = len(state.raw_extractions)
                lines.append(f"{n_chunks} chunks, {len(all_items)} items")
                lines.append("")

                by_type: dict[str, list] = {}
                for item in all_items:
                    by_type.setdefault(item.type, []).append(item)
                for item_type, items_list in by_type.items():
                    lines.append(f"### {item_type.title()} ({len(items_list)})")
                    lines.append("")
                    for item in items_list:
                        lines.append(f"- {_q(item.quote)}")
                    lines.append("")

        elif i == 5:
            if state.raw_decisions is not None:
                total = sum(len(d.decisions) for d in state.raw_decisions)
                unsupported = sum(
                    1 for d in state.raw_decisions
                    for dec in d.decisions if not dec.supported
                )
                lines.append(f"{total} claims judged, {unsupported} unsupported")
                lines.append("")

        elif i == 6:
            if state.raw_classifications is not None:
                all_bcs: list[GapOutput] = state.raw_classifications.gaps
                lines.append(f"{len(all_bcs)} gaps")
                lines.append("")
                if all_bcs:
                    for b in sorted(all_bcs, key=lambda x: {"critical": 0, "significant": 1, "minor": 2}.get(x.severity, 3)):
                        lines.append(f"- [{b.severity}] {b.gap} (line {b.line})")
                    lines.append("")

        elif i == 7:
            if state.items is not None:
                items = state.items
                raw_count = sum(len(ext.items) for ext in (state.raw_extractions or []))
                collected_count = len(items.claims) + len(items.evidence) + len(items.concessions) + len(items.questions) + len(items.dependencies) + len(items.scope)
                deduped = raw_count - collected_count
                lines.append(f"dedup: {raw_count} raw -> {collected_count} collected ({deduped} absorbed)")
                lines.append(f"claims: {len(items.claims)}, evidence: {len(items.evidence)}, concessions: {len(items.concessions)}, questions: {len(items.questions)}, dependencies: {len(items.dependencies)}, scope: {len(items.scope)}")
                if state.asks:
                    lines.append(f"asks: {len(state.asks)}")
                if state.active_lenses:
                    lines.append(f"active lenses: {', '.join(state.active_lenses)}")
                if state.inactive_lenses:
                    lines.append(f"inactive lenses: {', '.join(state.inactive_lenses)}")
                lines.append("")

        elif i == 8:
            if state.derive is not None:
                d = state.derive
                lines.append(f"thesis: {_q(d.central_claim)}")
                lines.append(f"problem: {_q(d.problem_statement)}")
                lines.append(f"scope: {_q(d.scope_boundary)}")
                lines.append(f"ask calibration: {d.ask_calibration}")
                lines.append("")
                if d.load_bearing_claims:
                    lines.append(f"### Load-bearing ({len(d.load_bearing_claims)})")
                    lines.append("")
                    for lb in d.load_bearing_claims:
                        lines.append(f"- [{lb.id}] {_q(lb.quote)}")
                    lines.append("")
            if state.gaps_by_lens is not None:
                all_bcs_derive: list[GapOutput] = []
                for lens_list in state.gaps_by_lens.values():
                    all_bcs_derive.extend(lens_list)
                if all_bcs_derive:
                    bc_sev = Counter(b.severity for b in all_bcs_derive)
                    lines.append(f"### Gaps ({len(all_bcs_derive)}: {bc_sev.get('critical', 0)} critical, {bc_sev.get('significant', 0)} significant, {bc_sev.get('minor', 0)} minor)")
                    lines.append("")
                    for b in sorted(all_bcs_derive, key=lambda x: {"critical": 0, "significant": 1, "minor": 2}.get(x.severity, 3)):
                        lines.append(f"- [{b.id}] [{b.severity}] {b.gap} (line {b.line})")
                    lines.append("")

        elif i == 9:
            if state.verify is not None:
                v = state.verify
                if v.closes:
                    lines.append(f"### Closures ({len(v.closes)})")
                    lines.append("")
                    for r in v.closes:
                        lines.append(f"- [{r.gap_id}] closed by evidence (line {r.evidence_line})")
                    lines.append("")
                if v.confirmations:
                    lines.append(f"### Confirmations ({len(v.confirmations)})")
                    lines.append("")
                    for c in v.confirmations:
                        lines.append(f"- {c}")
                    lines.append("")
                if v.contradictions:
                    lines.append(f"### Contradictions ({len(v.contradictions)})")
                    lines.append("")
                    for c in v.contradictions:
                        lines.append(f"- {c}")
                    lines.append("")
                if v.new_evidence:
                    lines.append(f"### New evidence ({len(v.new_evidence)})")
                    lines.append("")
                    for e in v.new_evidence:
                        lines.append(f"- {e}")
                    lines.append("")

        elif i == 10:
            if state.research is not None:
                for lens, data in state.research.items():
                    findings = data.findings
                    if findings:
                        lines.append(f"### {lens} ({len(findings)})")
                        lines.append("")
                        for f in findings:
                            lines.append(f"- {_q(f.finding)} ({f.source})")
                        lines.append("")

        elif i == 11:
            if state.probe is not None:
                p = state.probe
                lines.append(f"{p.total_inventory} references in inventory")
                if p.stale_refs:
                    lines.append(f"- stale: {', '.join(p.stale_refs)}")
                lines.append("")

        elif i == 12:
            if state.findings is not None:
                sev = Counter(f.severity for f in state.findings)
                lines.append(f"### Findings ({len(state.findings)}: {sev.get('critical', 0)} critical, {sev.get('significant', 0)} significant, {sev.get('minor', 0)} minor)")
                lines.append("")
                for f in sorted(state.findings, key=lambda x: {"critical": 0, "significant": 1, "minor": 2}.get(x.severity, 3)):
                    lines.append(f"- [{f.id}] [{f.severity}] {f.title} ({f.lens}, {f.test}, {f.confidence})")
                lines.append("")
            if state.strengths is not None and state.strengths:
                lines.append(f"### Strengths ({len(state.strengths)})")
                lines.append("")
                for s in state.strengths:
                    lines.append(f"- [{s.id}] {s.title} ({s.lens}) {_q(s.quote)} (line {s.line})")
                lines.append("")

        elif i == 13:
            if state.checklist is not None:
                passed = sum(1 for c in state.checklist if c.passed)
                lines.append(f"### Checklist ({passed}/{len(state.checklist)})")
                lines.append("")
                for c in state.checklist:
                    mark = "pass" if c.passed else "FAIL"
                    lines.append(f"- [{c.id}] {c.name}: {mark}")
                lines.append("")

        elif i == 14:
            if state.surviving is not None:
                killed = state.killed or []
                lines.append(f"{len(state.surviving)} survived, {len(killed)} killed")
                lines.append("")
                if state.surviving:
                    lines.append(f"### Survived ({len(state.surviving)})")
                    lines.append("")
                    for f in state.surviving:
                        lines.append(f"- [{f.id}] [{f.severity}] {f.title} ({f.lens})")
                    lines.append("")
                if killed:
                    lines.append(f"### Killed ({len(killed)})")
                    lines.append("")
                    for k in killed:
                        lines.append(f"- [{k.finding_id}] [{k.challenge}] {k.finding_title} - {k.reasoning[:80]}")
                    lines.append("")

        elif i == 15:
            if state.compounds is not None:
                for comp in state.compounds:
                    cross = " (cross-lens)" if comp.cross_lens else ""
                    lines.append(f"- {comp.name} (constituents: {', '.join(f'[{c}]' for c in comp.constituents)}{cross})")
                    lines.append(f"  - mechanism: {_q(comp.mechanism)}")
                    if comp.emergent_risk:
                        lines.append(f"  - emergent risk: {_q(comp.emergent_risk)}")
                lines.append("")

        elif i == 16:
            if state.synthesis is not None:
                syn = state.synthesis
                lines.append(f"verdict: {syn.verdict_label} ({syn.verdict_confidence})")
                lines.append(f"thesis survives: {syn.thesis_survives}")
                if syn.thesis_statement:
                    lines.append(f"thesis: {_q(syn.thesis_statement)}")
                if syn.dominant_dynamic:
                    lines.append(f"dominant dynamic: {syn.dominant_dynamic}")
                lines.append("")
                if syn.major_findings:
                    lines.append(f"### Promoted ({len(syn.major_findings)})")
                    lines.append("")
                    for mf in syn.major_findings:
                        reason = syn.promotion_reasons.get(mf.id, "")
                        tag = f" - {reason}" if reason else ""
                        lines.append(f"- [{mf.id}] [{mf.severity}] {mf.title} ({mf.lens}){tag}")
                    lines.append("")
                if syn.regular_findings:
                    lines.append(f"### Regular ({len(syn.regular_findings)})")
                    lines.append("")
                    for rf in syn.regular_findings:
                        lines.append(f"- [{rf.severity}] {rf.title} ({rf.lens})")
                    lines.append("")

        if len(lines) == before:
            pass

    return "\n".join(lines)


def _render_skipped_report(state: PipelineState, synthesis: SynthesisOutput) -> str:
    """Render a clean summary for papers that were triaged out."""
    lines: list[str] = []
    pid = state.paper_id
    title = state.paper_title
    stats = synthesis.paper_stats
    paper_type = synthesis.verdict_statement or "Skipped"
    reason = synthesis.skip_reason

    lines.append(f"# {pid} Assay")
    lines.append("")
    lines.append(title)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Classification")
    lines.append("")
    lines.append(paper_type)
    lines.append("")
    if reason:
        lines.append(reason)
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Paper Statistics")
    lines.append("")
    total_chars = stats.get("total_chars", 0)
    lines.append(f"- Total characters: {total_chars:,}")
    lines.append(f"- Sections: {stats.get('chunk_count', 0)}")
    largest = stats.get("largest_chunk_chars", 0)
    if largest:
        lines.append(f"- Largest section: {largest:,} characters")
    wr = stats.get("wording_ratio", 0)
    lines.append(f"- Wording ratio: {wr:.0%}")
    audience = stats.get("audience", "")
    if audience:
        lines.append(f"- Audience: {audience}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(f"- Paper: {pid}, \"{title}\"")
    lines.append("- Triage: skipped at Step 1 (Survey)")
    lines.append(f"- Model: {state.model_name}")
    lines.append(f"- Service: {state.service_name}")
    lines.append("")

    return "\n".join(lines)
