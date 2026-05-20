#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Assay report and trace rendering.

Renders the final markdown report (Step 15) via a Jinja template
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

from collections import Counter
from dataclasses import dataclass, field

from jinja2 import Template

from pipeline import extract_code_blocks, load_sections

from assay.models import (
    AskOutput,
    BreadcrumbOutput,
    ChecklistItem,
    CollectedItem,
    CollectedItems,
    CompoundOutput,
    DeriveOutput,
    FindingOutput,
    KilledFinding,
    PipelineState,
    ReferenceEntry,
    StrengthOutput,
    SynthesisOutput,
)

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
class RenderReferenceEntry:
    """A reference table entry."""
    label: str
    tier: str
    url: str
    link: str
    mention_count: int


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
    breadcrumb_total: int = 0
    breadcrumb_critical: int = 0
    breadcrumb_significant: int = 0
    breadcrumb_minor: int = 0
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
    central_thesis: str = ""

    verdict: str = "Insufficient"
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

    references: list[RenderReferenceEntry] = field(default_factory=list)

    inventory: InventoryData = field(default_factory=InventoryData)

    model_name: str = ""
    service_name: str = ""
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

    references = [
        RenderReferenceEntry(
            label=r.ref_label or r.ref_id,
            tier=r.relationship,
            url=r.url or "",
            link=f"[link]({r.url})" if r.url else "-",
            mention_count=r.mention_count,
        )
        for r in (state.reference_registry or [])
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

    all_breadcrumbs: list[BreadcrumbOutput] = []
    for lens_list in (state.breadcrumbs_by_lens or {}).values():
        all_breadcrumbs.extend(lens_list)
    bc_sev = Counter(b.severity for b in all_breadcrumbs)

    killed_list = state.killed or []
    killed_breakdown = ""
    if killed_list:
        challenge_counts = Counter(k.challenge for k in killed_list)
        killed_breakdown = ", ".join(f"{v} {k}" for k, v in challenge_counts.most_common())

    has_structural = bool(major_raw or compounds)
    structural_summary = ""
    if major_raw:
        structural_summary = f"{len(major_raw)} findings touch the thesis or participate in compound dynamics."

    asks_dicts = [{"target": a.target, "quote": a.quote, "type": a.type, "line": a.line}
                  for a in (state.asks or [])]

    fm = state.front_matter

    return ReportData(
        pid=state.paper_id,
        title=state.paper_title,
        central_thesis=synthesis.central_thesis if synthesis else "",
        verdict=synthesis.verdict if synthesis else "Insufficient",
        confidence=synthesis.verdict_confidence if synthesis else "Medium",
        thesis_statement=synthesis.thesis_statement if synthesis else "",
        thesis_survives=synthesis.thesis_survives if synthesis else False,
        critical_count=critical,
        significant_count=significant,
        minor_count=minor_count,
        asks=asks_dicts,
        intent=fm.intent if fm else "",
        ask_calibration=state.derive.ask_calibration if state.derive else "",
        wording_lines=fm.wording_lines if fm else 0,
        targets_cwg_lwg=fm.targets_cwg_lwg if fm else False,
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
        references=references,
        inventory=InventoryData(
            claim_count=len(items.claims),
            evidence_count=len(items.evidence),
            concession_count=len(items.concessions),
            question_count=len(items.questions),
            dependency_count=len(items.dependencies),
            breadcrumb_total=len(all_breadcrumbs),
            breadcrumb_critical=bc_sev.get("critical", 0),
            breadcrumb_significant=bc_sev.get("significant", 0),
            breadcrumb_minor=bc_sev.get("minor", 0),
            findings_generated=len(state.findings or []),
            findings_survived=survived_count,
            findings_killed=len(killed_list),
            killed_breakdown=killed_breakdown,
            major_count=len(major_raw),
            regular_count=len(regular_raw),
            compound_count=len(compounds),
            strength_count=len(strengths),
        ),
        model_name=state.model_name,
        service_name=state.service_name,
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
    if synthesis is not None and synthesis.verdict == "Skipped":
        return _render_skipped_report(state, synthesis)

    blocks = extract_code_blocks(section_text)
    if not blocks:
        raise RuntimeError(
            "No Jinja template found in the '14. Report' section of assay.md. "
            "Expected a fenced code block containing the report template."
        )

    data = prepare_report_data(state)
    tmpl = Template(blocks[0], keep_trailing_newline=True,
                    trim_blocks=True, lstrip_blocks=True)
    return tmpl.render(vars(data))


def rerender_report(pid: str, backend) -> str:
    """Regenerate the assay report from stored DB data + current template.

    Loads all persisted assay artifacts from the database, reconstructs
    a PipelineState sufficient for report rendering, and applies the
    current Jinja template from ``assay.md``.

    Use via ``paperflow assay <pid> --rerender``.
    """
    state = load_assay_state(pid, backend)
    secs = dict(load_sections("assay", "assay.md"))
    section_text = secs.get("15. Report", "")
    return render_report(state, section_text)


# -- DB state loader ---------------------------------------------------------


def load_assay_state(pid: str, backend) -> PipelineState:
    """Reconstruct a report-ready PipelineState from database rows.

    Loads claims, evidence, concessions, breadcrumbs, thesis, findings,
    asks, references, strengths, checklist, compounds, and synthesis.
    Does NOT load paper_source or chunk_map (not needed for report).
    """
    meta = backend.get_meta(pid)

    claims_rows = backend.get_assay_claims(pid)
    evidence_rows = backend.get_assay_evidence(pid)
    breadcrumb_rows = backend.get_assay_breadcrumbs(pid)
    thesis_row = backend.get_assay_thesis(pid)
    finding_rows = backend.get_assay_findings(pid)
    ask_rows = backend.get_assay_asks(pid)
    ref_rows = backend.get_assay_references(pid)
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

    breadcrumbs_by_lens: dict[str, list[BreadcrumbOutput]] = {}
    for b in breadcrumb_rows:
        lens = b.primary_lens or "Other"
        bc = BreadcrumbOutput(
            chunk_index=b.chunk_index, item_quote="", line=b.loc_line,
            gap=b.gap, why_important=b.why_important,
            primary_lens=b.primary_lens, secondary_lens=b.secondary_lens or None,
            severity=b.severity,
        )
        breadcrumbs_by_lens.setdefault(lens, []).append(bc)

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
                finding_title=f.title, lens=f.lens,
                challenge=f.challenge, reasoning=f.reasoning,
            ))

    asks = [AskOutput(target=a.target, quote=a.quote, type=a.type, line=0)
            for a in ask_rows]

    reference_registry = [
        ReferenceEntry(
            ref_id="", ref_label=r.ref_label, url=r.url,
            source_type="paper", relationship=r.relationship,
            mention_count=r.mention_count,
        )
        for r in ref_rows
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
            verdict=synthesis_row.verdict,
            verdict_confidence=synthesis_row.verdict_confidence,
            thesis_statement=synthesis_row.thesis_statement,
            thesis_survives=synthesis_row.thesis_survives,
            central_thesis=synthesis_row.central_thesis,
            dominant_dynamic=synthesis_row.dominant_dynamic or None,
            critical_count=synthesis_row.critical_count,
            significant_count=synthesis_row.significant_count,
            major_findings=major_findings,
            regular_findings=regular_findings,
        )

    state = PipelineState(
        paper_id=pid,
        paper_title=meta.title or "",
        model_name="(from DB)",
        service_name="(from DB)",
        items=items,
        breadcrumbs_by_lens=breadcrumbs_by_lens,
        derive=derive,
        asks=asks,
        reference_registry=reference_registry,
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
        "Receive", "References", "Index", "Survey", "Extract", "Scan",
        "Collect", "Derive", "Research", "Probe", "Analyze",
        "Rationale", "Challenge", "Couple", "Synthesize", "Report",
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
            if state.front_matter is not None:
                fm = state.front_matter
                if fm.audience:
                    lines.append(f"- audience: {', '.join(fm.audience)}")
                if fm.intent:
                    lines.append(f"- intent: {fm.intent}")
                lines.append("")

        elif i == 1:
            if state.reference_inventory is not None:
                refs = state.reference_inventory
                in_ps = sum(1 for r in refs if r.in_paperstore)
                self_cite = sum(1 for r in refs if r.self_cite)
                summary_parts = [f"{len(refs)} refs"]
                if in_ps:
                    summary_parts.append(f"{in_ps} in paperstore")
                if self_cite:
                    summary_parts.append(f"{self_cite} self-cite")
                lines.append(f"{', '.join(summary_parts)}:")
                for r in refs:
                    flags = []
                    if not r.in_paperstore:
                        flags.append("not found")
                    if r.stale:
                        flags.append("stale")
                    if r.self_cite:
                        flags.append("self-cite")
                    count = r.count or 1
                    flag_str = f", {', '.join(flags)}" if flags else ""
                    lines.append(f"- {r.paper_id} (x{count}{flag_str})")
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
            if state.front_matter is not None:
                fm = state.front_matter
                if fm.wording_lines or fm.targets_cwg_lwg:
                    wording_parts = []
                    if fm.wording_lines:
                        wording_parts.append(f"wording_lines={fm.wording_lines}")
                    if fm.targets_cwg_lwg:
                        wording_parts.append("targets_cwg_lwg")
                    lines.append(f"wording: {', '.join(wording_parts)}")
                    lines.append("")
            if state.synthesis is not None and state.synthesis.verdict == "Skipped":
                lines.append(f"triage: skipped ({state.synthesis.skip_reason})")
                lines.append("")

        elif i == 4:
            if state.raw_extractions is not None:
                all_items: list = []
                all_refs: list = []
                for ext in state.raw_extractions:
                    all_items.extend(ext.items)
                    all_refs.extend(ext.references)

                n_chunks = len(state.raw_extractions)
                lines.append(f"{n_chunks} chunks, {len(all_items)} items, {len(all_refs)} references")
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

                if all_refs:
                    lines.append(f"### References ({len(all_refs)})")
                    lines.append("")
                    for ref in all_refs:
                        label = ref.ref_label or ref.text or ref.url or "?"
                        ctx = f" {_q(ref.context)}" if ref.context else ""
                        lines.append(f"- {label} [{ref.relationship}]{ctx}")
                    lines.append("")

        elif i == 5:
            if state.raw_scans is not None:
                all_bcs: list[BreadcrumbOutput] = []
                for scan in state.raw_scans:
                    all_bcs.extend(scan.breadcrumbs)
                lines.append(f"{len(all_bcs)} breadcrumbs")
                lines.append("")
                if all_bcs:
                    for b in sorted(all_bcs, key=lambda x: {"critical": 0, "significant": 1, "minor": 2}.get(x.severity, 3)):
                        lines.append(f"- [{b.severity}] {b.gap} (line {b.line})")
                    lines.append("")

        elif i == 6:
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
                if state.reference_registry:
                    lines.append(f"reference registry: {len(state.reference_registry)} entries")
                    for rr in state.reference_registry:
                        lines.append(f"- {rr.ref_label or rr.ref_id} [{rr.relationship}] {rr.mention_count} mentions")
                lines.append("")

        elif i == 7:
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
            if state.breadcrumbs_by_lens is not None:
                all_bcs_derive: list[BreadcrumbOutput] = []
                for lens_list in state.breadcrumbs_by_lens.values():
                    all_bcs_derive.extend(lens_list)
                if all_bcs_derive:
                    bc_sev = Counter(b.severity for b in all_bcs_derive)
                    lines.append(f"### Breadcrumbs ({len(all_bcs_derive)}: {bc_sev.get('critical', 0)} critical, {bc_sev.get('significant', 0)} significant, {bc_sev.get('minor', 0)} minor)")
                    lines.append("")
                    for b in sorted(all_bcs_derive, key=lambda x: {"critical": 0, "significant": 1, "minor": 2}.get(x.severity, 3)):
                        lines.append(f"- [{b.severity}] {b.gap} (line {b.line})")
                    lines.append("")

        elif i == 8:
            if state.research is not None:
                for lens, data in state.research.items():
                    findings = data.findings
                    if findings:
                        lines.append(f"### {lens} ({len(findings)})")
                        lines.append("")
                        for f in findings:
                            lines.append(f"- {_q(f.finding)} ({f.source})")
                        lines.append("")

        elif i == 9:
            if state.probe is not None:
                p = state.probe
                lines.append(f"{p.total_inventory} inventory, {p.total_registry} registry")
                if p.cited_not_referenced:
                    lines.append(f"- cited-not-referenced: {', '.join(p.cited_not_referenced)}")
                if p.referenced_not_cited:
                    lines.append(f"- referenced-not-cited: {', '.join(p.referenced_not_cited)}")
                if p.companions:
                    lines.append(f"- companions: {', '.join(p.companions)}")
                if p.stale_refs:
                    lines.append(f"- stale: {', '.join(p.stale_refs)}")
                if p.self_cites:
                    lines.append(f"- self-cites: {', '.join(p.self_cites)}")
                lines.append("")

        elif i == 10:
            if state.findings is not None:
                sev = Counter(f.severity for f in state.findings)
                lines.append(f"### Findings ({len(state.findings)}: {sev.get('critical', 0)} critical, {sev.get('significant', 0)} significant, {sev.get('minor', 0)} minor)")
                lines.append("")
                for f in sorted(state.findings, key=lambda x: {"critical": 0, "significant": 1, "minor": 2}.get(x.severity, 3)):
                    lines.append(f"- [{f.severity}] {f.title} ({f.lens}, {f.test}, {f.confidence})")
                lines.append("")
            if state.strengths is not None and state.strengths:
                lines.append(f"### Strengths ({len(state.strengths)})")
                lines.append("")
                for s in state.strengths:
                    lines.append(f"- {s.title} ({s.lens}) {_q(s.quote)} (line {s.line})")
                lines.append("")

        elif i == 11:
            if state.checklist is not None:
                passed = sum(1 for c in state.checklist if c.passed)
                lines.append(f"### Checklist ({passed}/{len(state.checklist)})")
                lines.append("")
                for c in state.checklist:
                    mark = "pass" if c.passed else "FAIL"
                    lines.append(f"- {c.id} {c.name}: {mark}")
                lines.append("")

        elif i == 12:
            if state.surviving is not None:
                killed = state.killed or []
                lines.append(f"{len(state.surviving)} survived, {len(killed)} killed")
                lines.append("")
                if state.surviving:
                    lines.append(f"### Survived ({len(state.surviving)})")
                    lines.append("")
                    for f in state.surviving:
                        lines.append(f"- [{f.severity}] {f.title} ({f.lens})")
                    lines.append("")
                if killed:
                    lines.append(f"### Killed ({len(killed)})")
                    lines.append("")
                    for k in killed:
                        lines.append(f"- [{k.challenge}] {k.finding_title} - {k.reasoning[:80]}")
                    lines.append("")

        elif i == 13:
            if state.compounds is not None:
                for comp in state.compounds:
                    n_const = len(comp.constituents)
                    cross = " (cross-lens)" if comp.cross_lens else ""
                    lines.append(f"- {comp.name} ({n_const} constituents{cross})")
                    lines.append(f"  - mechanism: {_q(comp.mechanism)}")
                    if comp.emergent_risk:
                        lines.append(f"  - emergent risk: {_q(comp.emergent_risk)}")
                lines.append("")

        elif i == 14:
            if state.synthesis is not None:
                syn = state.synthesis
                lines.append(f"verdict: {syn.verdict} ({syn.verdict_confidence})")
                lines.append(f"thesis survives: {syn.thesis_survives}")
                if syn.thesis_statement:
                    lines.append(f"thesis: {_q(syn.thesis_statement)}")
                if syn.dominant_dynamic:
                    lines.append(f"dominant dynamic: {syn.dominant_dynamic}")
                lines.append(f"major: {len(syn.major_findings)}, regular: {len(syn.regular_findings)}")
                if syn.major_findings:
                    for mf in syn.major_findings:
                        lines.append(f"- [{mf.severity}] {mf.title} ({mf.lens})")
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
    paper_type = synthesis.central_thesis or "Skipped"
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
    lines.append(f"- Triage: skipped at Step 1 (Survey)")
    lines.append(f"- Model: {state.model_name}")
    lines.append(f"- Service: {state.service_name}")
    lines.append("")

    return "\n".join(lines)
