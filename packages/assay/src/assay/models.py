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
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field


def _ensure_int_list(v: Any) -> Any:
    """Coerce legacy ``closed_by`` values (sentinel 0, single int) to ``list[int]``.

    The field was previously a single ``int`` with ``0`` meaning "open".
    Persisted rows and older pickles may still carry that shape; this
    validator normalizes them so reads do not blow up. New writers
    serialize a list directly.
    """
    if v is None or v == 0:
        return []
    if isinstance(v, int):
        return [v]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        return [int(p) for p in s.split(",") if p.strip()]
    return v


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
class ChunkEntry:
    """A chunk boundary from the survey step."""

    index: int
    heading: str
    start_line: int
    end_line: int
    char_count: int


@dataclass(frozen=True)
class KilledFinding:
    """A finding killed by cross-examination."""

    finding_id: int
    finding_title: str
    lens: str
    challenge: str
    reasoning: str


@dataclass(frozen=True)
class SynthesisOutput:
    """Verdict derivation result from the Synthesize step."""

    verdict_label: str = "Insufficient"
    verdict_confidence: str = "Medium"
    verdict_statement: str = ""
    dominant_dynamic: str | None = None
    thesis_survives: bool = False
    thesis_statement: str = ""
    major_findings: list = field(default_factory=list)
    regular_findings: list = field(default_factory=list)
    promotion_reasons: dict = field(default_factory=dict)
    critical_count: int = 0
    significant_count: int = 0
    skip_reason: str = ""
    paper_stats: dict = field(default_factory=dict)


@dataclass
class ProbeResult:
    """Reference inventory summary from Step 10."""

    total_inventory: int = 0
    stale_refs: list[str] = field(default_factory=list)


# -- Step 4: Extract output -------------------------------------------------

class ItemOutput(BaseModel, frozen=True):
    """A single extracted item from a chunk."""

    type: Literal["claim", "evidence", "concession", "question", "dependency", "scope", "ask"] = Field(description="Item type.")
    quote: str = Field(description="Verbatim quote from paper.")
    line: int = Field(description="Line number.")
    quality_tier: str | None = Field(default=None, description="Evidence quality tier.")

class ChunkExtractOutput(BaseModel, frozen=True):
    """Structured output from one extract sub-agent (one chunk)."""

    chunk_index: int = Field(description="Which chunk this extraction covers.")
    items: list[ItemOutput] = Field(default=[], description="Extracted items.")

class CollectedItem(ItemOutput, frozen=True):
    """ItemOutput extended with collect-assigned id and section."""

    id: int = Field(default=0, description="Global unique ID assigned by pipeline.")
    section: str = Field(default="", description="Section heading for persistence.")
    source_pid: str = Field(default="", description="Source paper ID if from companion, empty if from paper under analysis.")


@dataclass(frozen=True)
class CollectedItems:
    """Aggregated and deduped items from all chunks."""

    claims: list[CollectedItem] = field(default_factory=list)
    evidence: list[CollectedItem] = field(default_factory=list)
    concessions: list[CollectedItem] = field(default_factory=list)
    questions: list[ItemOutput] = field(default_factory=list)
    dependencies: list[ItemOutput] = field(default_factory=list)
    scope: list[ItemOutput] = field(default_factory=list)


class GapOutput(BaseModel, frozen=True):
    """A gap identified during scanning."""

    id: int = Field(default=0, description="Global unique ID assigned by pipeline.")
    chunk_index: int = Field(description="Source chunk index.")
    item_quote: str = Field(description="Quote of the item with the gap.")
    line: int = Field(description="Line number.")
    gap: str = Field(description="One-sentence reviewer question targeting the gap.")
    why_important: str = Field(description="One sentence: why this matters.")
    primary_lens: str = Field(description="Primary analytical lens.")
    secondary_lens: str | None = Field(default=None, description="Optional secondary lens.")
    severity: str = Field(default="minor", description="significant|minor (no critical in Pass 1).")
    closed_by: Annotated[list[int], BeforeValidator(_ensure_int_list)] = Field(
        default_factory=list,
        description="Evidence IDs that closed this gap; empty list if open.",
    )


class AskOutput(BaseModel, frozen=True):
    """An explicit ask from the paper to the committee."""

    id: int = Field(default=0, description="Global unique ID assigned by pipeline.")
    quote: str = Field(description="Verbatim quote.")
    line: int = Field(description="Line number.")
    target: str = Field(description="Target group or entity.")
    type: str = Field(description="adopt|direction|review|poll|feedback|inform")



class ScanOutput(BaseModel, frozen=True):
    """Structured output from one scan sub-agent (one chunk or batch)."""

    chunk_index: int = Field(description="Which chunk this scan covers.")
    gaps: list[GapOutput] = Field(default=[], description="Identified gaps.")


# -- Step 5: Decide output --------------------------------------------------


class ClaimDecision(BaseModel, frozen=True):
    """Per-claim support judgment from the Decide step."""

    claim_id: int = Field(description="Global claim ID from Extract.")
    supported: bool = Field(description="True if chunk provides support.")
    reason: str = Field(description="One-line reason for the decision.")


class ChunkDecideOutput(BaseModel, frozen=True):
    """Structured output from one decide sub-agent (one chunk)."""

    chunk_index: int = Field(description="Which chunk this covers.")
    decisions: list[ClaimDecision] = Field(default=[], description="Per-claim decisions.")


class CrossChunkClaimDecision(BaseModel, frozen=True):
    """Per-claim judgment from the cross-chunk Decide follow-up.

    The ``claim_id`` is the global, paper-wide claim ID (pre-assigned by
    Extract). The model echoes it back verbatim so the orchestrator can
    re-bind decisions to the original ``(chunk_index, local_id)`` pair.
    """

    claim_id: int = Field(description="Global claim ID echoed back from the input.")
    supported: bool = Field(description="True if any cross-chunk evidence supports the claim.")
    supporting_evidence_lines: list[int] = Field(
        default_factory=list,
        description="Line numbers of evidence relied on.",
    )
    reason: str = Field(description="One sentence describing the cross-chunk basis or its absence.")


class CrossChunkDecideOutput(BaseModel, frozen=True):
    """Structured output from the cross-chunk Decide follow-up pass."""

    decisions: list[CrossChunkClaimDecision] = Field(
        default=[], description="One decision per input claim.")


# -- Step 6: Classify output ------------------------------------------------


class BatchClassifyOutput(BaseModel, frozen=True):
    """Structured output from the single-batch classify step."""

    gaps: list[GapOutput] = Field(default=[], description="Gaps for unsupported claims.")


# -- Step 8: Derive output --------------------------------------------------


class LoadBearingClaim(BaseModel, frozen=True):
    """A claim identified as load-bearing for the thesis."""

    id: int = Field(description="Global unique ID of the collected claim.")
    quote: str = Field(description="Exact quote of the claim.")


class DeriveOutput(BaseModel, frozen=True):
    """Structured output from the derive sub-agent."""

    central_claim: str = Field(description="One sentence: the paper's thesis.")
    problem_statement: str = Field(description="One sentence: what deficiency the paper addresses.")
    scope_boundary: str = Field(description="What the paper does and does not cover.")
    load_bearing_claims: list[LoadBearingClaim] = Field(default=[], description="Claims the thesis depends on.")
    ask_calibration: str = Field(default="direction", description="adopt|direction|review|poll|feedback|inform")


# -- Step 8: Verify output --------------------------------------------------


class GapResolution(BaseModel, frozen=True):
    """Evidence from a companion paper that closes a gap."""

    gap_id: int = Field(description="ID of the gap being closed.")
    evidence_quote: str = Field(description="Verbatim quote from companion paper.")
    evidence_line: int = Field(description="Line number in companion paper.")


class VerifyContradiction(BaseModel, frozen=True):
    """A specific contradiction found in a companion paper."""

    source_pid: str = Field(description="Companion paper ID containing the contradicting passage.")
    quote: str = Field(description="Verbatim quote from the companion paper.")
    line: int = Field(description="Line number in the companion paper.")
    refutes: str = Field(description="What the quote refutes (claim text or paraphrase).")
    claim_id: int | None = Field(default=None, description="Optional global claim ID this refutes.")


class VerifyOutput(BaseModel, frozen=True):
    """Structured output from companion-paper verification."""

    confirmations: list[str] = Field(
        default=[], description="Claims confirmed by the companion paper.")
    contradictions: list[VerifyContradiction] = Field(
        default=[], description="Structured contradictions from companion papers.")
    new_evidence: list[str] = Field(
        default=[], description="Relevant evidence not previously identified.")
    closes: list[GapResolution] = Field(
        default=[], description="Gaps closed by companion evidence.")


# -- Step 9: Research output ------------------------------------------------


class ResearchFinding(BaseModel, frozen=True):
    """A single research finding from web search."""

    source: str = Field(description="Source URL or title.")
    finding: str = Field(description="What was found.")
    relevance: str = Field(default="", description="How it relates to the paper.")


class ResearchLensOutput(BaseModel, frozen=True):
    """Structured output from one research lens sub-agent."""

    lens: str = Field(description="Which lens this covers.")
    findings: list[ResearchFinding] = Field(default=[], description="Research findings.")


# -- Step 11: Analyze output ------------------------------------------------


class FindingOutput(BaseModel, frozen=True):
    """A single finding from per-chunk analysis."""

    id: int = Field(default=0, description="Global unique ID assigned by pipeline.")
    title: str = Field(description="Finding title.")
    lens: str = Field(description="Performance|Design|Specification|Usability|Ecosystem|Rationale")
    severity: str = Field(description="critical|significant|minor")
    quote: str = Field(description="Verbatim quote from paper.")
    line: int = Field(description="Line number.")
    explanation: str = Field(description="Why this is a problem. 2-4 sentences.")
    test: str = Field(default="novel", description="Test number and name, or 'novel'.")
    from_gap_ids: list[int] = Field(
        default_factory=list,
        description="Global IDs of upstream gaps this finding derives from; empty if novel.",
    )
    external_evidence: str | None = Field(default=None, description="External evidence if any.")
    examiner: str = Field(default="", description="Committee role that would raise this.")
    damage: str = Field(default="", description="Structural consequence. 1-2 sentences.")
    confidence: str = Field(default="medium", description="high|medium|low")


class StrengthOutput(BaseModel, frozen=True):
    """A strength identified during analysis."""

    id: int = Field(default=0, description="Global unique ID assigned by pipeline.")
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

    id: int = Field(default=0, description="Global unique ID assigned by pipeline.")
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


# -- Step 13: Challenge output ----------------------------------------------


class CrossExamVerdict(BaseModel, frozen=True):
    """Per-finding verdict from LLM cross-examination."""

    finding_id: int = Field(description="ID of the finding being examined.")
    finding_title: str = Field(default="", description="Finding title for readability.")
    survived: bool = Field(description="True if finding survives all five challenges.")
    killed_by: ChallengeName | None = Field(default=None, description="Challenge that killed this finding, or null.")
    reasoning: str = Field(description="One sentence explaining the verdict.")


class CrossExamBatchOutput(BaseModel, frozen=True):
    """Structured output from one cross-examination batch."""

    verdicts: list[CrossExamVerdict] = Field(default=[], description="Per-finding verdicts.")


# -- Step 14: Couple output -------------------------------------------------


class CompoundOutput(BaseModel, frozen=True):
    """A compound dynamic where findings combine."""

    name: str = Field(description="Short lowercase phrase describing the causal chain.")
    constituents: list[int] = Field(description="IDs of findings involved.")
    mechanism: str = Field(description="One sentence per causal link: why A's consequence triggers or amplifies B.")
    cross_lens: bool = Field(default=False, description="True only if constituents span different lenses.")
    emergent_risk: str | None = Field(default=None, description="A new concrete consequence that neither finding produces alone, or null.")


class CoupleOutput(BaseModel, frozen=True):
    """Structured output from the couple sub-agent."""

    compounds: list[CompoundOutput] = Field(default=[], description="Compound dynamics found.")


# -- Pipeline state ----------------------------------------------------------


class PipelineState(BaseModel):
    """Mutable accumulator threaded through every assay pipeline step."""

    _next_id: int = 1

    # Step 0 - Receive
    paper_id: str = ""
    paper_md: str = ""
    paper_title: str = ""
    paper_date: str = ""
    audience: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    intent: str = ""

    # Step 1 - References
    ref_pids: list = Field(default_factory=list)
    ref_urls: list = Field(default_factory=list)

    # Step 2 - Index
    cited_paper_index: Any = None
    index_stats: Any = None

    # Step 3 - Survey
    chunk_map: Optional[list[ChunkEntry]] = None
    wording_lines: int = 0
    targets_cwg_lwg: bool = False

    # Step 4 - Extract
    raw_extractions: Optional[list[ChunkExtractOutput]] = None

    # Step 5 - Decide
    raw_decisions: Optional[list[ChunkDecideOutput]] = None
    # (chunk_index, local_claim_id) -> global claim ID. Populated at end of
    # Extract and consumed by the cross-chunk Decide follow-up.
    claim_global_id_map: dict[tuple[int, int], int] = Field(default_factory=dict)

    # Step 6 - Classify
    raw_classifications: Optional[BatchClassifyOutput] = None

    # Step 5/6 compat - legacy Scan (kept for migration)
    raw_scans: Optional[list[ScanOutput]] = None

    # Step 7 - Collect
    items: Optional[CollectedItems] = None
    gaps_by_lens: Optional[dict[str, list[GapOutput]]] = None
    asks: Optional[list[AskOutput]] = None
    active_lenses: Optional[list[str]] = None
    inactive_lenses: Optional[list[str]] = None

    # Step 8 - Derive
    derive: Optional[DeriveOutput] = None

    # Step 9 - Verify
    verify: Optional[VerifyOutput] = None

    # Step 10 - Research
    research: Optional[dict[str, ResearchLensOutput]] = None

    # Step 11 - Probe
    probe: Optional[ProbeResult] = None

    # Step 12 - Analyze
    findings: Optional[list[FindingOutput]] = None
    strengths: Optional[list[StrengthOutput]] = None

    # Step 13 - Rationale
    checklist: Optional[list[ChecklistItem]] = None

    # Step 14 - Challenge
    surviving: Optional[list[FindingOutput]] = None
    killed: Optional[list[KilledFinding]] = None

    # Step 15 - Couple
    compounds: Optional[list[CompoundOutput]] = None

    # Step 16 - Synthesize
    synthesis: Optional[SynthesisOutput] = None

    # Step 17 - Report
    report: Optional[str] = None
