#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pydantic models for the extractor pipeline.

Domain models match the Classes section of extractor.md. Per-step output
models group the Writes fields for each step so Pydantic AI can return
structured data. Frozen domain models are updated via model_copy(update=...).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


# -- Domain models -----------------------------------------------------------


class SourceLoc(BaseModel, frozen=True):
    line: int
    start_char: int
    end_char: int


class Chunk(BaseModel, frozen=True):
    text: str
    line_offset: int


class CitationRef(BaseModel, frozen=True):
    paper_id: str
    count: int


class Claim(BaseModel, frozen=True):
    loc: SourceLoc
    text: str
    original_quotes: list[str]
    section: str
    question: str
    depends_on: list[SourceLoc]
    merged_into: SourceLoc | None = None


class Evidence(BaseModel, frozen=True):
    loc: SourceLoc
    text: str
    original_quotes: list[str]
    section: str
    supports: list[str]
    quantitative: bool
    cited: bool
    verifiable: bool
    normative: bool
    merged_into: SourceLoc | None = None


class SupportLink(BaseModel, frozen=True):
    claim_loc: SourceLoc
    evidence_locs: list[SourceLoc]
    status: Literal["directly_supported", "transitively_supported", "unsupported"]


class InternalContradiction(BaseModel, frozen=True):
    evidence_loc: SourceLoc
    claim_loc: SourceLoc


class LoadBearingResult(BaseModel, frozen=True):
    claim_loc: SourceLoc
    dependents: list[SourceLoc]
    classification: Literal[
        "internally_contested",
        "externally_contested",
        "externally_anchored",
        "critical_gap",
        "anchored",
        "depends_on_contested",
        "peripheral",
    ]


class ExternalEvidence(BaseModel, frozen=True):
    claim_loc: SourceLoc
    source_url: str
    source_title: str
    text: str
    finding: str
    stance: Literal["supports", "contradicts"]
    quantitative: bool
    cited: bool
    verifiable: bool
    normative: bool


class WebResolution(BaseModel, frozen=True):
    external_loc: SourceLoc
    source_url: str
    stance: Literal["supports", "contradicts"]
    finding: str
    resolved_claims: list[SourceLoc]


# -- Pre-loc models (LLM output before harness adds SourceLocs) --------------


class RawClaim(BaseModel, frozen=True):
    text: str
    original_quotes: list[str] = []
    section: str = ""
    question: str = ""
    depends_on: list[str] = []


class RawEvidence(BaseModel, frozen=True):
    text: str
    original_quotes: list[str] = []
    section: str = ""
    supports: list[str] = []
    quantitative: bool = False
    cited: bool = False
    verifiable: bool = False
    normative: bool = False


# -- Per-step output models --------------------------------------------------


class ExtractClaimsOutput(BaseModel, frozen=True):
    """Steps 1: per-chunk claim extraction."""

    claims: list[RawClaim] = []


class ExtractEvidenceOutput(BaseModel, frozen=True):
    """Step 2: per-chunk evidence extraction."""

    evidence: list[RawEvidence] = []


class DedupGroupingOutput(BaseModel, frozen=True):
    """Steps 3-4 tier 2: semantic grouping indices."""

    groups: list[list[int]] = []


class VerifyOutput(BaseModel, frozen=True):
    """Step 5: verify + deps + map + contradict."""

    splits: list[int] = []
    cross_deps: list[tuple[SourceLoc, SourceLoc]] = []
    support_map: list[SupportLink] = []
    internal_contradictions: list[InternalContradiction] = []


class LoadBearingOutput(BaseModel, frozen=True):
    """Step 6: load-bearing classification."""

    results: list[LoadBearingResult] = []


class WebSearchOutput(BaseModel, frozen=True):
    """Step 7: web search results."""

    external_evidence: list[ExternalEvidence] = []


class ResolveOutput(BaseModel, frozen=True):
    """Step 8: resolve external evidence."""

    load_bearing_claims: list[LoadBearingResult] = []
    web_resolutions: list[WebResolution] = []


# -- Pipeline state ----------------------------------------------------------


class PipelineState(BaseModel):
    """Mutable accumulator threaded through every step."""

    paper_source: Optional[str] = None

    # Step 0
    chunks: Optional[list[Chunk]] = None
    citations: Optional[list[CitationRef]] = None

    # Step 1
    raw_claims: Optional[list[RawClaim]] = None

    # Step 2
    raw_evidence: Optional[list[RawEvidence]] = None

    # Step 3
    claims: Optional[list[Claim]] = None

    # Step 4
    evidence: Optional[list[Evidence]] = None

    # Step 5 (claims/evidence replaced in place)
    support_map: Optional[list[SupportLink]] = None
    internal_contradictions: Optional[list[InternalContradiction]] = None

    # Step 6
    load_bearing_claims: Optional[list[LoadBearingResult]] = None

    # Step 7
    external_evidence: Optional[list[ExternalEvidence]] = None

    # Step 8 (load_bearing_claims replaced in place)
    web_resolutions: Optional[list[WebResolution]] = None

    # Step 9
    report: Optional[str] = None
