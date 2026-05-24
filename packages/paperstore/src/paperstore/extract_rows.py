#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Row types for extract pipeline results stored in paperstore.db.

Stdlib only, no Pydantic. Write methods on :class:`~paperstore.SqliteBackend`
accept duck-typed domain objects; read methods return these frozen dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClaimRow:
    paper_id: str
    uid: int
    loc_line: int
    loc_start: int
    loc_end: int
    text: str
    section: str
    question: str
    kind: str = "normative"
    merged_into: int | None = None


@dataclass(frozen=True)
class EvidenceRow:
    paper_id: str
    uid: int
    loc_line: int
    loc_start: int
    loc_end: int
    text: str
    section: str
    supports: str  # JSON array
    quantitative: bool
    cited: bool
    verifiable: bool
    normative: bool
    merged_into: int | None = None


@dataclass(frozen=True)
class PaperCitationRow:
    paper_id: str
    cited_paper_id: str
    count: int


@dataclass(frozen=True)
class ExternalCitationRow:
    paper_id: str
    source_url: str
    source_title: str
    text: str
    finding: str
    stance: str


@dataclass(frozen=True)
class QuestionRow:
    paper_id: str
    uid: int
    loc_line: int
    loc_start: int
    loc_end: int
    claim_text: str
    section: str
    question: str
    kind: str = "normative"


@dataclass(frozen=True)
class CaputCausaeRow:
    paper_id: str
    thesis: str


@dataclass(frozen=True)
class CitationAuditRow:
    paper_id: str
    cited_paper_id: str
    resolution_method: str
    resolved: bool
    source_url: str = ""
    quote_match: str = "not_checked"
    discrepancy: str = ""


@dataclass(frozen=True)
class RhetoricRow:
    paper_id: str
    uid: int
    loc_line: int
    loc_start: int
    loc_end: int
    text: str
    section: str
    marker_type: str
    target: str
    intensity: str


@dataclass(frozen=True)
class CandidateRow:
    paper_id: str
    rule: str
    label: str
    detail: str = ""
    data: str = "{}"


@dataclass(frozen=True)
class FindingRow:
    paper_id: str
    id: str
    lens: str
    severity: str
    title: str
    quoted_text: str = ""
    source_line: int = 0
    explanation: str = ""


@dataclass(frozen=True)
class AssayClaimRow:
    paper_id: str
    uid: int
    loc_line: int
    quote: str
    section: str
    kind: str = "normative"
    load_bearing: bool = False


@dataclass(frozen=True)
class AssayEvidenceRow:
    paper_id: str
    uid: int
    loc_line: int
    quote: str
    section: str
    subtype: str = ""
    quality_tier: str = ""
    supports: str = "[]"
    source_pid: str = ""


@dataclass(frozen=True)
class AssayConcessionRow:
    paper_id: str
    uid: int
    loc_line: int
    quote: str
    section: str = ""
    subtype: str = ""


@dataclass(frozen=True)
class AssayGapRow:
    paper_id: str
    uid: int
    chunk_index: int
    loc_line: int
    gap: str
    why_important: str = ""
    primary_lens: str = ""
    secondary_lens: str = ""
    severity: str = "minor"
    closed_by: list = field(default_factory=list)


@dataclass(frozen=True)
class AssayThesisRow:
    paper_id: str
    central_claim: str
    problem_statement: str = ""
    scope_boundary: str = ""
    ask_calibration: str = ""


@dataclass(frozen=True)
class AssayFindingRow:
    paper_id: str
    uid: int
    title: str
    lens: str
    severity: str
    quote: str = ""
    loc_line: int = 0
    explanation: str = ""
    test: str = ""
    survived: bool = True
    major: bool = False
    challenge: str = ""
    reasoning: str = ""
    from_gap_ids: list = field(default_factory=list)


@dataclass(frozen=True)
class AssayAskRow:
    """A persisted assay ask entry."""
    paper_id: str
    uid: int
    target: str
    quote: str
    type: str


@dataclass(frozen=True)
class AssayPidRow:
    """A persisted paper-number reference from assay inventory."""
    paper_id: str
    uid: int
    raw_pid: str
    resolved_pid: str
    url: str = ""
    mention_count: int = 0
    in_paperstore: bool = False
    stale: bool = False
    author_overlap: float = 0.0


@dataclass(frozen=True)
class AssayUrlRow:
    """A persisted standalone URL from assay inventory."""
    paper_id: str
    uid: int
    url: str
    line: int = 0


@dataclass(frozen=True)
class AssayStrengthRow:
    """A persisted assay strength entry."""
    paper_id: str
    uid: int
    title: str
    quote: str = ""
    loc_line: int = 0
    explanation: str = ""


@dataclass(frozen=True)
class AssayChecklistRow:
    """A persisted assay SD-4 rationale checklist item."""
    paper_id: str
    item_id: str
    name: str
    passed: bool = False
    location: str = ""
    note: str = ""


@dataclass(frozen=True)
class AssayCompoundRow:
    """A persisted assay compound dynamic entry."""
    paper_id: str
    uid: int
    name: str
    constituents: list = None
    mechanism: str = ""
    cross_lens: bool = False
    emergent_risk: str = ""

    def __post_init__(self):
        if self.constituents is None:
            object.__setattr__(self, 'constituents', [])


@dataclass(frozen=True)
class AssaySynthesisRow:
    """Persisted assay synthesis (verdict and counts)."""
    paper_id: str
    verdict: str
    verdict_confidence: str = "Medium"
    thesis_statement: str = ""
    thesis_survives: bool = False
    central_thesis: str = ""
    dominant_dynamic: str = ""
    critical_count: int = 0
    significant_count: int = 0
