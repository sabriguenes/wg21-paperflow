#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pydantic models for the dissect pipeline.

Domain models are the sole schema authority. ``dissect.md`` provides
LLM instructions; these models enforce the output structure via Pydantic
AI's ``output_type``. Frozen domain models are updated via
``model_copy(update=...)``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from paperstore import SourceLoc

Stance = Literal["supports", "contradicts"]
ClaimKind = Literal["normative", "factual"]
ContradictionKind = Literal["evidence_vs_claim", "claim_vs_claim"]


# -- Domain models -----------------------------------------------------------

# SourceLoc is re-exported from paperstore (canonical home for the loc
# type at the storage layer). The LLM contract for ``line`` /
# ``start_char`` / ``end_char`` is documented in dissect.md's Global
# Directives "SourceLoc protocol" section.


class Chunk(BaseModel, frozen=True):
    """A contiguous slice of the paper, with its starting line number."""

    text: str
    line_offset: int = Field(description="1-based line number of the first line in this chunk.")


class CitationRef(BaseModel, frozen=True):
    """A WG21 paper number cited in the source, with occurrence count."""

    paper_id: str = Field(description="Uppercased paper number, e.g. 'P2300R10'.")
    count: int = Field(description="Number of times this paper is cited.")


class Claim(BaseModel, frozen=True):
    """An assertion extracted from the paper, normative or factual.

    Normative claims argue something ought to be true. Factual claims
    assert verifiable properties that the paper's argument depends on.
    The ``kind`` field distinguishes the two.
    """

    uid: int
    loc: SourceLoc
    text: str = Field(description="Exact quote from the paper.")
    original_quotes: list[str] = Field(
        description="Source quotes absorbed during dedup. Initially [text].",
    )
    section: str = Field(description="Section header where the claim appears.")
    question: str = Field(
        description="Question whose answer would constitute sufficient evidence.",
    )
    kind: ClaimKind = Field(
        default="normative",
        description="'normative': argues something ought to be true. "
        "'factual': asserts a verifiable property the argument depends on.",
    )
    depends_on: list[int] = Field(
        description="Uids of claims whose truth this claim requires. "
        "Resolved from text references in RawClaim.depends_on.",
    )
    merged_into: int | None = Field(
        default=None,
        description="Tombstone: uid of the survivor that absorbed this claim. "
        "None if this claim is alive.",
    )


class Evidence(BaseModel, frozen=True):
    """A statement offered in support of one or more claims."""

    uid: int
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
    merged_into: int | None = Field(
        default=None,
        description="Tombstone: uid of the survivor that absorbed this evidence. "
        "None if alive.",
    )


class SupportLink(BaseModel, frozen=True):
    """Maps a claim to the evidence that supports or fails to support it."""

    claim_uid: int
    evidence_uids: list[int]
    status: Literal["directly_supported", "transitively_supported", "unsupported"] = Field(
        description="'directly_supported': evidence explicitly addresses the claim. "
        "'transitively_supported': evidence supports a dependency of the claim. "
        "'unsupported': no matching evidence found.",
    )


class InternalContradiction(BaseModel, frozen=True):
    """A contradiction detected within the paper.

    When ``kind`` is ``evidence_vs_claim``, an evidence item undermines
    a claim. When ``kind`` is ``claim_vs_claim``, two claims assert
    incompatible things about the same or analogous subjects.
    """

    source_uid: int = Field(
        description="Uid of the contradicting item. "
        "An evidence uid when kind is evidence_vs_claim, "
        "a claim uid when kind is claim_vs_claim.",
    )
    claim_uid: int
    kind: ContradictionKind = Field(
        default="evidence_vs_claim",
        description="'evidence_vs_claim': evidence undermines a claim. "
        "'claim_vs_claim': two claims assert incompatible things.",
    )


class LoadBearingResult(BaseModel, frozen=True):
    """Load-Bearing step output: how critical a claim is to the paper's argument."""

    claim_uid: int
    dependents: list[int] = Field(
        description="Uids of claims that depend on this one (directly or transitively).",
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
    """Evidence found via citation verification or web search."""

    claim_uid: int
    source_url: str
    source_title: str
    text: str = Field(description="Extracted passage from the source.")
    finding: str = Field(description="One sentence, max 30 words, compressed result.")
    stance: Stance
    quantitative: bool
    cited: bool
    verifiable: bool
    normative: bool


MarkerType = Literal[
    "dismissal",
    "concession",
    "provocation",
    "scope_deflection",
    "political_signal",
]


class Rhetoric(BaseModel, frozen=True):
    """A rhetorical signal extracted from the paper."""

    uid: int
    loc: SourceLoc
    text: str = Field(description="Exact quote from the paper.")
    section: str = Field(description="Section header where the marker appears.")
    marker_type: MarkerType
    target: str = Field(description="What is being dismissed/conceded/deflected.")
    intensity: Literal["mild", "moderate", "strong"]


class WebResolution(BaseModel, frozen=True):
    """Result of resolving external evidence against load-bearing claims."""

    external_uid: int
    source_url: str
    stance: Stance
    finding: str
    resolved_claims: list[int] = Field(
        description="Uids of claims resolved by this external evidence.",
    )


class CaputCausae(BaseModel, frozen=True):
    """The paper's central thesis, derived from the convergence of anchored claims.

    Computed after external evidence resolution so the thesis reflects
    the full support picture. Stored in the database and injected as
    context into subsequent pipeline steps.
    """

    thesis: str = Field(description="One sentence stating what the paper's argument asserts.")
    anchored_claim_uids: list[int] = Field(
        default=[],
        description="Uids of anchored claims the thesis was derived from.",
    )
    evidence_root_uids: list[int] = Field(
        default=[],
        description="Uids of evidence items supporting multiple anchored claims.",
    )


class CitationAuditEntry(BaseModel, frozen=True):
    """Resolution and verification result for a single citation.

    Records whether the cited source was found, how it was resolved,
    and whether the paper accurately represents what the source says.
    """

    paper_id: str = Field(description="Cited paper number, e.g. 'P1928R15'.")
    resolution_method: Literal[
        "local_index", "wg21_link", "open_std", "not_found"
    ] = Field(
        description=(
            "How the citation was resolved: 'local_index' (paperstore-known "
            "URL), 'wg21_link' (wg21.link redirect), 'open_std' "
            "(open-std.org cascade), or 'not_found'."
        ),
    )
    resolved: bool = Field(description="True if the cited source was successfully fetched.")
    source_url: str = Field(default="", description="URL where the source was found.")
    quote_match: Literal["exact", "partial", "mismatch", "not_checked"] = Field(
        default="not_checked",
        description="Whether the paper's quotes match the cited source.",
    )
    discrepancy: str = Field(
        default="",
        description="Description of the mismatch, if any.",
    )


# -- Pre-loc models (LLM output before harness adds SourceLocs) --------------


class RawClaim(BaseModel, frozen=True):
    """LLM extraction output before the harness computes SourceLoc."""

    text: str
    start_line: int = Field(
        default=0,
        description="Line number reported by the LLM. 0 means unreported; "
        "the harness clamps to 1.",
    )
    original_quotes: list[str] = []
    section: str = ""
    question: str = ""
    kind: str = Field(
        default="normative",
        description="'normative' or 'factual'. Validated on promotion to Claim.",
    )
    depends_on: list[str] = Field(
        default=[],
        description="Quoted text of claims this one depends on. "
        "Resolved to SourceLocs by promote_claims.",
    )


class RawEvidence(BaseModel, frozen=True):
    """LLM extraction output before the harness computes SourceLoc."""

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


class RawRhetoric(BaseModel, frozen=True):
    """LLM extraction output before the harness computes SourceLoc."""

    text: str
    start_line: int = Field(
        default=0,
        description="Line number reported by the LLM. 0 means unreported.",
    )
    section: str = ""
    marker_type: str = ""
    target: str = ""
    intensity: str = "moderate"


# -- Per-step output models --------------------------------------------------


class ExtractAllOutput(BaseModel, frozen=True):
    """Extract Normative step output: combined per-chunk extraction."""

    claims: list[RawClaim] = []
    evidence: list[RawEvidence] = []
    markers: list[RawRhetoric] = []
    analysis_complete: bool = Field(
        default=False,
        description="Set to true when the chunk has been fully analyzed, "
        "even if no claims or evidence were found.",
    )


class ExtractFactualOutput(BaseModel, frozen=True):
    """Extract Factual step output: factual claims from a single chunk."""

    claims: list[RawClaim] = []
    analysis_complete: bool = Field(
        default=False,
        description="Set to true when the chunk has been fully analyzed, "
        "even if no factual claims were found.",
    )


class DedupGroupingOutput(BaseModel, frozen=True):
    """Dedup Claims / Dedup Factual / Dedup Evidence tier 2 output: semantic grouping indices."""

    groups: list[list[int]] = []


class VerifyOutput(BaseModel, frozen=True):
    """Verify step output: support map, internal contradictions, and cross-chunk dependencies."""

    support_map: list[SupportLink] = []
    internal_contradictions: list[InternalContradiction] = []


class LoadBearingOutput(BaseModel, frozen=True):
    """Load-Bearing step output: classification of each claim by structural importance."""

    results: list[LoadBearingResult] = []


class CitationTaskOutput(BaseModel, frozen=True):
    """Per-citation run_task return: audit entry plus opportunistic evidence."""

    audit: CitationAuditEntry
    evidence: list[ExternalEvidence] = []


class CitationAuditOutput(BaseModel, frozen=True):
    """Verify Citations step output: systematic verification of every citation."""

    entries: list[CitationAuditEntry] = []


class WebSearchOutput(BaseModel, frozen=True):
    """Web Search step output: external evidence for triggered claims."""

    external_evidence: list[ExternalEvidence] = []


class ResolveOutput(BaseModel, frozen=True):
    """Resolve External step output: resolved classifications and web resolutions."""

    load_bearing_claims: list[LoadBearingResult] = []
    web_resolutions: list[WebResolution] = []


class AsymmetryPattern(BaseModel, frozen=True):
    """A dismissal whose target appears as an unqualified positive claim elsewhere."""

    marker_uid: int
    claim_uid: int
    description: str


class ConcessionCluster(BaseModel, frozen=True):
    """Multiple concession markers targeting the same topic."""

    topic: str
    marker_uids: list[int]


class ScopeChain(BaseModel, frozen=True):
    """Scope deflection markers naming companion papers."""

    paper_id: str
    marker_uids: list[int]


class PatternDetectionOutput(BaseModel, frozen=True):
    """Detect Patterns step output: cross-marker pattern analysis."""

    asymmetries: list[AsymmetryPattern] = []
    concession_clusters: list[ConcessionCluster] = []
    scope_chains: list[ScopeChain] = []


class CaputCausaeOutput(BaseModel, frozen=True):
    """Caput Causae step output: the paper's central thesis and its derivation."""

    caput_causae: CaputCausae


# -- Pipeline state ----------------------------------------------------------


class PipelineState(BaseModel):
    """Mutable accumulator threaded through every pipeline step."""

    paper_source: Optional[str] = None
    next_uid: int = 1

    # Read
    chunks: Optional[list[Chunk]] = None
    citations: Optional[list[CitationRef]] = None

    # Extract Normative
    raw_claims: Optional[list[RawClaim]] = None
    raw_evidence: Optional[list[RawEvidence]] = None
    raw_rhetoric: Optional[list[RawRhetoric]] = None
    rhetoric: Optional[list[Rhetoric]] = None

    # Dedup Claims
    claims: Optional[list[Claim]] = None

    # Extract Factual
    raw_factual_claims: Optional[list[RawClaim]] = None

    # Dedup Evidence
    evidence: Optional[list[Evidence]] = None

    # Verify
    support_map: Optional[list[SupportLink]] = None
    internal_contradictions: Optional[list[InternalContradiction]] = None

    # Load-Bearing
    load_bearing_claims: Optional[list[LoadBearingResult]] = None

    # Verify Citations
    citation_audit: Optional[list[CitationAuditEntry]] = None

    # Web Search + Verify Citations (both contribute)
    external_evidence: Optional[list[ExternalEvidence]] = None

    # Resolve External
    web_resolutions: Optional[list[WebResolution]] = None

    # Detect Patterns
    marker_patterns: Optional[PatternDetectionOutput] = None

    # Caput Causae
    caput_causae: Optional[CaputCausae] = None

    # Report
    report: Optional[str] = None
