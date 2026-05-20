#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Pydantic models for the assay pipeline.

Domain models are the sole schema authority for LLM output. ``assay.md``
provides instructions; these models enforce output structure via
``output_type``. Frozen domain models are updated via
``model_copy(update=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# -- Enumerations -----------------------------------------------------------

ItemType = Literal["claim", "evidence", "concession", "question", "dependency", "scope"]
QualityTier = Literal[
    "field_experience", "implementation", "prototype", "example", "assertion", "citation_only",
]
SeverityKind = Literal["critical", "significant", "minor"]
AskType = Literal["adopt", "direction", "review", "poll", "feedback", "inform"]
LensName = Literal["Performance", "Design", "Specification", "Usability", "Ecosystem", "Rationale"]
VerdictKind = Literal["Sound", "Weakened", "Undermined", "Insufficient"]
ConfidenceKind = Literal["High", "Medium", "Low"]
RelationshipKind = Literal["companion", "predecessor", "dependency", "citation", "background", "tool"]


# -- Dataclasses (internal, not LLM output) ---------------------------------


@dataclass(frozen=True)
class FrontMatter:
    """Built once at end of Survey with all fields known."""

    document: str = ""
    title: str = ""
    date: str = ""
    audience: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    intent: str = ""
    wording_lines: int = 0
    targets_cwg_lwg: bool = False


@dataclass(frozen=True)
class ChunkEntry:
    """A chunk boundary from the survey step."""

    index: int
    heading: str
    start_line: int
    end_line: int
    char_count: int


@dataclass
class ReferenceEntry:
    """A deduped reference in the registry."""

    ref_id: str
    ref_label: str
    url: str | None
    source_type: str
    contexts: list[str] = field(default_factory=list)
    chunk_appearances: list[int] = field(default_factory=list)
    relationship: str = "citation"
    same_author: bool = False
    mention_count: int = 1


@dataclass(frozen=True)
class KilledFinding:
    """A finding killed by cross-examination."""

    finding_title: str
    lens: str
    challenge: str
    reasoning: str


@dataclass(frozen=True)
class SynthesisOutput:
    """Verdict derivation result from Step 11."""

    verdict: str = "Insufficient"
    verdict_confidence: str = "Medium"
    central_thesis: str = ""
    dominant_dynamic: str | None = None
    thesis_survives: bool = False
    thesis_statement: str = ""
    major_findings: list = field(default_factory=list)
    regular_findings: list = field(default_factory=list)
    critical_count: int = 0
    significant_count: int = 0
    skip_reason: str = ""
    paper_stats: dict = field(default_factory=dict)


@dataclass
class ProbeResult:
    """Reference inventory vs registry diff from Step 6."""

    total_inventory: int = 0
    total_registry: int = 0
    cited_not_referenced: list[str] = field(default_factory=list)
    referenced_not_cited: list[str] = field(default_factory=list)
    companions: list[str] = field(default_factory=list)
    stale_refs: list[str] = field(default_factory=list)
    self_cites: list[str] = field(default_factory=list)


# -- Step 2: Extract output -------------------------------------------------


class ItemOutput(BaseModel, frozen=True):
    """A single extracted item from a chunk."""

    type: Literal["claim", "evidence", "concession", "question", "dependency", "scope", "ask"] = Field(description="Item type.")
    quote: str = Field(description="Verbatim quote from paper.")
    line: int = Field(description="Line number.")
    quality_tier: str | None = Field(default=None, description="Evidence quality tier.")


class CollectedItem(ItemOutput):
    """ItemOutput extended with collect-assigned id and section."""

    id: str = Field(default="", description="Assigned ID (e.g. C1, E1).")
    section: str = Field(default="", description="Section heading for persistence.")


@dataclass(frozen=True)
class CollectedItems:
    """Aggregated and deduped items from all chunks."""

    claims: list[CollectedItem] = field(default_factory=list)
    evidence: list[CollectedItem] = field(default_factory=list)
    concessions: list[CollectedItem] = field(default_factory=list)
    questions: list[ItemOutput] = field(default_factory=list)
    dependencies: list[ItemOutput] = field(default_factory=list)
    scope: list[ItemOutput] = field(default_factory=list)


class BreadcrumbOutput(BaseModel, frozen=True):
    """A gap identified during extraction."""

    chunk_index: int = Field(description="Source chunk index.")
    item_quote: str = Field(description="Quote of the item with the gap.")
    line: int = Field(description="Line number.")
    gap: str = Field(description="One sentence describing the gap.")
    why_important: str = Field(description="One sentence: why this matters.")
    primary_lens: str = Field(description="Primary analytical lens.")
    secondary_lens: str | None = Field(default=None, description="Optional secondary lens.")
    severity: str = Field(default="minor", description="significant|minor (no critical in Pass 1).")


class AskOutput(BaseModel, frozen=True):
    """An explicit ask from the paper to the committee."""

    quote: str = Field(description="Verbatim quote.")
    line: int = Field(description="Line number.")
    target: str = Field(description="Target group or entity.")
    type: str = Field(description="adopt|direction|review|poll|feedback|inform")


class ReferenceOutput(BaseModel, frozen=True):
    """A reference found in a chunk."""

    ref_label: str | None = Field(default=None, description="Reference label e.g. [12].")
    text: str = Field(default="", description="Display text of the link.")
    url: str | None = Field(default=None, description="Resolved URL.")
    line: int = Field(default=0, description="Line number.")
    context: str = Field(default="", description="One sentence: what paper says about this ref.")
    relationship: str = Field(default="citation", description="companion|predecessor|dependency|citation|background|tool")


class ChunkExtractOutput(BaseModel, frozen=True):
    """Structured output from one extract sub-agent (one chunk)."""

    chunk_index: int = Field(description="Which chunk this extraction covers.")
    items: list[ItemOutput] = Field(default=[], description="Extracted items.")
    references: list[ReferenceOutput] = Field(default=[], description="References found.")


class ScanOutput(BaseModel, frozen=True):
    """Structured output from one scan sub-agent (one chunk or batch)."""

    chunk_index: int = Field(description="Which chunk this scan covers.")
    breadcrumbs: list[BreadcrumbOutput] = Field(default=[], description="Identified gaps.")


# -- Step 4: Derive output --------------------------------------------------


class LoadBearingClaim(BaseModel, frozen=True):
    """A claim identified as load-bearing for the thesis."""

    id: str = Field(description="Claim ID (e.g. C1).")
    quote: str = Field(description="Exact quote of the claim.")


class DeriveOutput(BaseModel, frozen=True):
    """Structured output from the derive sub-agent."""

    central_claim: str = Field(description="One sentence: the paper's thesis.")
    problem_statement: str = Field(description="One sentence: what deficiency the paper addresses.")
    scope_boundary: str = Field(description="What the paper does and does not cover.")
    load_bearing_claims: list[LoadBearingClaim] = Field(default=[], description="Claims the thesis depends on.")
    ask_calibration: str = Field(default="direction", description="adopt|direction|review|poll|feedback|inform")


# -- Step 5: Research output ------------------------------------------------


class ResearchFinding(BaseModel, frozen=True):
    """A single research finding from web search."""

    source: str = Field(description="Source URL or title.")
    finding: str = Field(description="What was found.")
    relevance: str = Field(default="", description="How it relates to the paper.")


class ResearchLensOutput(BaseModel, frozen=True):
    """Structured output from one research lens sub-agent."""

    lens: str = Field(description="Which lens this covers.")
    findings: list[ResearchFinding] = Field(default=[], description="Research findings.")


# -- Step 7: Analyze output -------------------------------------------------


class FindingOutput(BaseModel, frozen=True):
    """A single finding from per-chunk analysis."""

    title: str = Field(description="Finding title.")
    lens: str = Field(description="Performance|Design|Specification|Usability|Ecosystem|Rationale")
    severity: str = Field(description="critical|significant|minor")
    quote: str = Field(description="Verbatim quote from paper.")
    line: int = Field(description="Line number.")
    explanation: str = Field(description="Why this is a problem. 2-4 sentences.")
    test: str = Field(default="novel", description="Test number and name, or 'novel'.")
    from_breadcrumb: bool = Field(default=False, description="Whether derived from a breadcrumb.")
    external_evidence: str | None = Field(default=None, description="External evidence if any.")
    examiner: str = Field(default="", description="Committee role that would raise this.")
    damage: str = Field(default="", description="Structural consequence. 1-2 sentences.")
    confidence: str = Field(default="medium", description="high|medium|low")


class StrengthOutput(BaseModel, frozen=True):
    """A strength identified during analysis."""

    title: str = Field(description="Strength title.")
    lens: str = Field(description="Analytical lens.")
    quote: str = Field(description="Verbatim quote from paper.")
    line: int = Field(description="Line number.")
    explanation: str = Field(description="Why this is solid. 1-2 sentences.")


class ChunkAnalyzeOutput(BaseModel, frozen=True):
    """Structured output from one per-chunk analyze sub-agent."""

    chunk_index: int = Field(description="Which chunk this analysis covers.")
    findings: list[FindingOutput] = Field(default=[], description="Findings from this chunk.")
    strengths: list[StrengthOutput] = Field(default=[], description="Strengths from this chunk.")


class ChecklistItem(BaseModel, frozen=True):
    """One item from the SD-4 rationale checklist."""

    id: str = Field(description="E.g. SD4-1.")
    name: str = Field(description="Item name.")
    passed: bool = Field(description="Whether the item passes.")
    location: str | None = Field(default=None, description="Section heading or 'absent'.")
    note: str | None = Field(default=None, description="One sentence note.")


class RationaleOutput(BaseModel, frozen=True):
    """Structured output from the rationale sub-agent."""

    checklist: list[ChecklistItem] = Field(default=[], description="SD-4 mechanical checklist.")
    findings: list[FindingOutput] = Field(default=[], description="Rationale findings.")
    strengths: list[StrengthOutput] = Field(default=[], description="Rationale strengths.")


ChallengeName = Literal[
    "concession", "phantom", "resolution", "plausibility", "substance",
]


# -- Step 9a: Cross-examination output --------------------------------------


class CrossExamVerdict(BaseModel, frozen=True):
    """Per-finding verdict from LLM cross-examination."""

    finding_title: str = Field(description="Title of the finding being examined.")
    survived: bool = Field(description="True if finding survives all five challenges.")
    killed_by: ChallengeName | None = Field(default=None, description="Challenge that killed this finding, or null.")
    reasoning: str = Field(description="One sentence explaining the verdict.")


class CrossExamBatchOutput(BaseModel, frozen=True):
    """Structured output from one cross-examination batch."""

    verdicts: list[CrossExamVerdict] = Field(default=[], description="Per-finding verdicts.")


# -- Step 9b: Couple output -------------------------------------------------


class CompoundOutput(BaseModel, frozen=True):
    """A compound dynamic where findings combine."""

    name: str = Field(description="Compound name.")
    constituents: list[str] = Field(description="Finding titles in this compound.")
    mechanism: str = Field(description="One sentence per causal link.")
    cross_lens: bool = Field(default=False, description="Whether it spans lenses.")
    emergent_risk: str | None = Field(default=None, description="New risk from combination.")


class CoupleOutput(BaseModel, frozen=True):
    """Structured output from the couple sub-agent."""

    compounds: list[CompoundOutput] = Field(default=[], description="Compound dynamics found.")


# -- Pipeline state ----------------------------------------------------------


class PipelineState(BaseModel):
    """Mutable accumulator threaded through every assay pipeline step."""

    # Init
    service_name: str = ""
    model_name: str = ""
    paper_source: Optional[str] = None
    paper_id: str = ""
    paper_title: str = ""

    # Step 1 - References
    reference_inventory: Optional[list] = None

    # Step 2 - Index
    cited_paper_index: Any = None
    index_stats: Any = None

    # Step 3 - Survey
    front_matter: Optional[FrontMatter] = None
    chunk_map: Optional[list[ChunkEntry]] = None

    # Step 3 - Extract
    raw_extractions: Optional[list[ChunkExtractOutput]] = None

    # Step 4 - Scan
    raw_scans: Optional[list[ScanOutput]] = None

    # Step 5 - Collect
    items: Optional[CollectedItems] = None
    breadcrumbs_by_lens: Optional[dict[str, list[BreadcrumbOutput]]] = None
    asks: Optional[list[AskOutput]] = None
    active_lenses: Optional[list[str]] = None
    inactive_lenses: Optional[list[str]] = None
    reference_registry: Optional[list[ReferenceEntry]] = None

    # Step 6 - Derive
    derive: Optional[DeriveOutput] = None

    # Step 7 - Research
    research: Optional[dict[str, ResearchLensOutput]] = None

    # Step 8 - Probe
    probe: Optional[ProbeResult] = None

    # Step 9 - Analyze
    findings: Optional[list[FindingOutput]] = None
    strengths: Optional[list[StrengthOutput]] = None
    checklist: Optional[list[ChecklistItem]] = None

    # Step 10 - Challenge
    surviving: Optional[list[FindingOutput]] = None
    killed: Optional[list[KilledFinding]] = None

    # Step 11 - Couple
    compounds: Optional[list[CompoundOutput]] = None

    # Step 12 - Synthesize
    synthesis: Optional[SynthesisOutput] = None

    # Step 11 - Report
    report: Optional[str] = None
