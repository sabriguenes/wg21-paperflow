#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Row types for the reviewstore database. Stdlib only — no Pydantic."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimRow:
    paper_id: str
    loc_line: int
    loc_start: int
    loc_end: int
    text: str
    section: str
    question: str
    merged_into_line: int | None = None
    merged_into_start: int | None = None
    merged_into_end: int | None = None


@dataclass(frozen=True)
class EvidenceRow:
    paper_id: str
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
    merged_into_line: int | None = None
    merged_into_start: int | None = None
    merged_into_end: int | None = None


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
