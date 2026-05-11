#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pydantic models for the extractor pipeline.

Domain models are the sole schema authority. ``extractor.md`` provides
LLM instructions; these models enforce the output structure via Pydantic
AI's ``output_type``. Frozen domain models are updated via
``model_copy(update=...)``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Stance = Literal["supports", "contradicts"]


# -- Domain models -----------------------------------------------------------


class SourceLoc(BaseModel, frozen=True):
    """Position of an extracted item in the source paper."""

    line: int = Field(description="1-based line number in the paper markdown.")
    start_char: int = Field(
        description="Ordinal disambiguator when multiple items share a line. "
        "Not a character offset; the Nth item on the same line gets start_char=N-1.",
    )
    end_char: int = Field(description="Character count of the source line.")


class Chunk(BaseModel, frozen=True):
    """A contiguous slice of the paper, with its starting line number."""

    text: str
    line_offset: int = Field(description="1-based line number of the first line in this chunk.")


class CitationRef(BaseModel, frozen=True):
    """A WG21 paper number cited in the source, with occurrence count."""

    paper_id: str = Field(description="Uppercased paper number, e.g. 'P2300R10'.")
    count: int = Field(description="Number of times this paper is cited.")


class Claim(BaseModel, frozen=True):
    """A normative assertion extracted from the paper."""

    loc: SourceLoc
    text: str = Field(description="Exact quote from the paper.")
    original_quotes: list[str] = Field(
        description="Source quotes absorbed during dedup. Initially [text].",
    )
    section: str = Field(description="Section header where the claim appears.")
    question: str = Field(
        description="Question whose answer would constitute sufficient evidence.",
    )
    depends_on: list[SourceLoc] = Field(
        description="Claims whose truth this claim requires. "
        "Resolved from text references in RawClaim.depends_on.",
    )
    merged_into: SourceLoc | None = Field(
        default=None,
        description="Tombstone: points to the survivor that absorbed this claim. "
        "None if this claim is alive.",
    )


class Evidence(BaseModel, frozen=True):
    """A factual statement offered in support of one or more claims."""

    loc: SourceLoc
    text: str = Field(description="Exact quote from the paper.")
    original_quotes: list[str] = Field(
        description="Source quotes absorbed during dedup. Initially [text].",
    )
    section: str = Field(description="Section header where the evidence appears.")
    supports: list[str] = Field(
        description="Assertions this evidence advances. Each entry is a complete "
        "subject-verb-stance phrase, not a topic label.",
    )
    quantitative: bool = Field(
        description="True if the evidence contains measurements, benchmarks, or numeric data.",
    )
    cited: bool = Field(
        description="True if the evidence references an external source (paper, standard, URL).",
    )
    verifiable: bool = Field(
        description="True if a reader could independently verify the claim "
        "(e.g. by running code, checking a standard).",
    )
    normative: bool = Field(
        description="True if the evidence states a requirement or obligation, "
        "not merely an observation.",
    )
    merged_into: SourceLoc | None = Field(
        default=None,
        description="Tombstone: points to the survivor that absorbed this evidence. "
        "None if alive.",
    )


class SupportLink(BaseModel, frozen=True):
    """Maps a claim to the evidence that supports or fails to support it."""

    claim_loc: SourceLoc
    evidence_locs: list[SourceLoc]
    status: Literal["directly_supported", "transitively_supported", "unsupported"] = Field(
        description="'directly_supported': evidence explicitly addresses the claim. "
        "'transitively_supported': evidence supports a dependency of the claim. "
        "'unsupported': no matching evidence found.",
    )


class InternalContradiction(BaseModel, frozen=True):
    """An evidence item that contradicts a claim within the same paper."""

    evidence_loc: SourceLoc
    claim_loc: SourceLoc


class LoadBearingResult(BaseModel, frozen=True):
    """Graph analysis result: how critical a claim is to the paper's argument."""

    claim_loc: SourceLoc
    dependents: list[SourceLoc] = Field(
        description="Claims that depend on this one (directly or transitively).",
    )
    classification: Literal[
        "internally_contested",
        "externally_contested",
        "externally_anchored",
        "critical_gap",
        "anchored",
        "depends_on_contested",
        "peripheral",
    ] = Field(
        description="'internally_contested': load-bearing + contradicted by internal evidence. "
        "'externally_contested': load-bearing + contradicted by external evidence. "
        "'externally_anchored': load-bearing + confirmed by external evidence. "
        "'critical_gap': load-bearing + unsupported. "
        "'anchored': load-bearing + supported by internal evidence. "
        "'depends_on_contested': depends on a contested claim. "
        "'peripheral': not load-bearing.",
    )


class ExternalEvidence(BaseModel, frozen=True):
    """Evidence found via web search for a triggered claim."""

    claim_loc: SourceLoc
    source_url: str
    source_title: str
    text: str = Field(description="Extracted passage from the source.")
    finding: str = Field(description="One sentence, max 30 words, compressed result.")
    stance: Stance
    quantitative: bool
    cited: bool
    verifiable: bool
    normative: bool


class WebResolution(BaseModel, frozen=True):
    """Result of resolving external evidence against load-bearing claims."""

    external_loc: SourceLoc
    source_url: str
    stance: Stance
    finding: str
    resolved_claims: list[SourceLoc] = Field(
        description="Chain of claims resolved by this external evidence.",
    )


# -- Pre-loc models (LLM output before harness adds SourceLocs) --------------


class RawClaim(BaseModel, frozen=True):
    """LLM output before the harness computes SourceLoc."""

    text: str
    start_line: int = Field(
        default=0,
        description="Line number reported by the LLM. 0 means unreported; "
        "the harness clamps to 1.",
    )
    original_quotes: list[str] = []
    section: str = ""
    question: str = ""
    depends_on: list[str] = Field(
        default=[],
        description="Quoted text of claims this one depends on. "
        "Resolved to SourceLocs by promote_claims.",
    )


class RawEvidence(BaseModel, frozen=True):
    """LLM output before the harness computes SourceLoc."""

    text: str
    start_line: int = Field(
        default=0,
        description="Line number reported by the LLM. 0 means unreported.",
    )
    original_quotes: list[str] = []
    section: str = ""
    supports: list[str] = []
    quantitative: bool = False
    cited: bool = False
    verifiable: bool = False
    normative: bool = False


# -- Per-step output models --------------------------------------------------


class ExtractClaimsOutput(BaseModel, frozen=True):
    """Step 1: per-chunk claim extraction."""

    claims: list[RawClaim] = []


class ExtractEvidenceOutput(BaseModel, frozen=True):
    """Step 3: per-chunk evidence extraction."""

    evidence: list[RawEvidence] = []


class DedupGroupingOutput(BaseModel, frozen=True):
    """Steps 2/4 tier 2: semantic grouping indices."""

    groups: list[list[int]] = []


class VerifyOutput(BaseModel, frozen=True):
    """Step 5: verify + deps + map + contradict."""

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

    # Step 5
    support_map: Optional[list[SupportLink]] = None
    internal_contradictions: Optional[list[InternalContradiction]] = None

    # Step 6
    load_bearing_claims: Optional[list[LoadBearingResult]] = None

    # Step 7
    external_evidence: Optional[list[ExternalEvidence]] = None

    # Step 8
    web_resolutions: Optional[list[WebResolution]] = None

    # Step 9
    report: Optional[str] = None
