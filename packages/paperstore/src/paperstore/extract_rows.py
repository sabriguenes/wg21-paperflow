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

from dataclasses import dataclass


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
