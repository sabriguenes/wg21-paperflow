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

from typing import Literal, Optional, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import ModelRetry

from paperstore import SourceLoc

Stance = Literal["supports", "contradicts"]
ClaimKind = Literal["normative", "factual"]


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
    chunk_index: int = Field(
        default=0,
        description="0-based index of the chunk that produced this claim. "
        "In-memory only; not persisted to the database.",
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
    chunk_index: int = Field(
        default=0,
        description="0-based index of the chunk that produced this evidence. "
        "In-memory only; not persisted to the database.",
    )
    merged_into: int | None = Field(
        default=None,
        description="Tombstone: uid of the survivor that absorbed this evidence. "
        "None if alive.",
    )


class ClaimVerdict(BaseModel, frozen=True):
    """A single finding from the verify step.

    Multiple verdicts per claim are expected. A claim can be ``proven``
    by one evidence item and ``disproven`` by another. The full list is
    the record; consumers decide what the combination means.
    """

    claim_uid: int
    related_uid: int = Field(
        default=-1,
        description="Evidence uid for proven/disproven, claim uid for "
        "implied/disclaimed, -1 for unproven.",
    )
    status: Literal["proven", "implied", "unproven", "disproven", "disclaimed"] = Field(
        description="'proven': evidence answers the claim affirmatively. "
        "'implied': supported through dependency chain. "
        "'unproven': no evidence addresses the claim. "
        "'disproven': evidence, if correct, would falsify the claim. "
        "'disclaimed': another claim in the paper contradicts this one.",
    )


class LoadBearingResult(BaseModel, frozen=True):
    """Load-Bearing step output: how critical a claim is to the paper's argument."""

    claim_uid: int
    dependents: list[int] = Field(
        description="Uids of claims that depend on this one (directly or transitively).",
    )
    classification: Literal[
        "conflicted",
        "externally_contested",
        "externally_anchored",
        "critical_gap",
        "anchored",
        "depends_on_contested",
        "peripheral",
    ] = Field(
        description="'conflicted': load-bearing + contradicted by internal evidence. "
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
    "scope_boundary",
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
    intensity: Literal["low", "medium", "high"]
    chunk_index: int = Field(
        default=0,
        description="0-based index of the chunk that produced this marker. "
        "In-memory only; not persisted to the database.",
    )


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
    resolution_method: Literal["local_index", "not_found"] = Field(
        description=(
            "How the citation was resolved: 'local_index' (paperstore-known "
            "URL) or 'not_found'."
        ),
    )
    resolved: bool = Field(description="True if the cited source was successfully fetched.")
    source_url: str = Field(default="", description="URL where the source was found.")
    quote_match: Literal["exact", "partial", "mismatch", "not_checked", "unreadable"] = Field(
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
    question: str = ""
    # Constrained decoding stability: Qwen 30B produces non-deterministic
    # output with fewer than 5 schema fields (20-28 claims across runs).
    # At 5 fields, output stabilizes (19 claims in 9/10 runs). These
    # fields exist solely to anchor the generation; values are discarded.
    unused1: str = ""
    unused2: str = ""


class RawEvidence(BaseModel, frozen=True):
    """LLM extraction output before the harness computes SourceLoc."""

    text: str
    start_line: int = Field(
        default=0,
        description="Line number reported by the LLM. 0 means unreported.",
    )
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
    intensity: str = "medium"


# -- Per-step output models --------------------------------------------------


class ExtractClaimsOutput(BaseModel, frozen=True):
    """Extract Claims step output: normative claims from a single chunk."""

    claims: list[RawClaim] = []

    # @model_validator(mode="after")
    # def _reject_empty(self) -> Self:
    #     if not self.claims:
    #         raise ModelRetry(
    #             "Empty claim output is rejected. Re-read the chunk and extract "
    #             "every statement that argues something should be true, ought "
    #             "to be true, or is better or worse than an alternative."
    #         )
    #     return self


class ExtractEvidenceOutput(BaseModel, frozen=True):
    """Extract Evidence step output: supporting evidence from a single chunk."""

    evidence: list[RawEvidence] = []

    # @model_validator(mode="after")
    # def _reject_empty(self) -> Self:
    #     if not self.evidence:
    #         raise ModelRetry(
    #             "Empty evidence output is rejected. Re-read the chunk and "
    #             "extract statements offered in support of another assertion, "
    #             "including concessions and cited or verifiable support."
    #         )
    #     return self


class ExtractRhetoricOutput(BaseModel, frozen=True):
    """Extract Rhetoric step output: rhetorical markers from a single chunk."""

    markers: list[RawRhetoric] = []

    # @model_validator(mode="after")
    # def _reject_empty(self) -> Self:
    #     if not self.markers:
    #         raise ModelRetry(
    #             "Empty rhetoric output is rejected. Re-read the chunk and "
    #             "extract statements that dismiss, concede, provoke, deflect "
    #             "scope, or signal committee politics."
    #         )
    #     return self


class ExtractFactualOutput(BaseModel, frozen=True):
    """Extract Factual step output: factual claims from a single chunk."""

    claims: list[RawClaim] = []


class DedupGroupingOutput(BaseModel, frozen=True):
    """Dedup Claims / Dedup Factual / Dedup Evidence tier 2 output: semantic grouping indices."""

    groups: list[list[int]] = []


class VerifyProposition(BaseModel, frozen=True):
    """One (claim, evidence) judgement for the batched verify sub-prompt.

    Step 8 packs a small number of these into each LLM call. The model
    returns three-valued verdicts; ``_custom_verify`` translates them
    into the canonical ``ClaimVerdict`` statuses (``support`` →
    ``proven``, ``contradict`` → ``disproven``, ``unrelated`` → drop).
    """

    claim_uid: int
    evidence_uid: int
    verdict: Literal["support", "contradict", "unrelated"]


class BatchVerifyOutput(BaseModel, frozen=True):
    """One LLM call's worth of batched verify judgements.

    ``judgements`` is order-preserving for diagnostic readability; the
    custom hook re-sorts before writing ``state.verdicts``.
    """

    judgements: list[VerifyProposition] = []


class DisclaimPairOutput(BaseModel, frozen=True):
    """Result of one disclaim-detection LLM call over a single claim pair.

    The four-valued relation lets the hook record a directional
    disclaim, a mutual contradiction, or no relationship at all. Only
    propositional opposition counts; shared topic alone is ``none``.
    """

    claim_a_uid: int
    claim_b_uid: int
    relation: Literal["a_disclaims_b", "b_disclaims_a", "mutual", "none"]


class LoadBearingBinaryOutput(BaseModel, frozen=True):
    """Result of one per-claim load-bearing decision.

    ``reason`` is free text retained for the trace; it never influences
    downstream classification.
    """

    claim_uid: int
    load_bearing: bool
    reason: str = ""


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
    blanked_lines: int = 0

    # Extract
    raw_claims: Optional[list[RawClaim]] = None
    raw_evidence: Optional[list[RawEvidence]] = None
    raw_rhetoric: Optional[list[RawRhetoric]] = None
    rhetoric: Optional[list[Rhetoric]] = None

    # Dedup Claims
    normative_claims: Optional[list[Claim]] = None

    # Extract Factual
    raw_factual: Optional[list[RawClaim]] = None

    # Dedup Evidence
    deduped_evidence: Optional[list[Evidence]] = None

    # Verify triage (populated by _custom_verify before any LLM call so
    # the trace can show what the embedding pre-filter handed to the
    # LLM). None until Step 8 has run.
    centrality_scores: Optional[dict[int, float]] = None
    triaged_evidence: Optional[dict[int, list[int]]] = None
    disclaim_candidates: Optional[list[tuple[int, int]]] = None
    verify_batch_count: int = 0
    self_pair_dropped: int = 0

    # Verify
    verdicts: Optional[list[ClaimVerdict]] = None

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

    # Embedding shadow (observational, never applied). Each list is a
    # candidate semantic merge group of survivor uids. None until the
    # corresponding dedup step has run; ``[]`` after if no candidate
    # clusters cleared the threshold.
    shadow_claim_groups: Optional[list[list[int]]] = None
    shadow_evidence_groups: Optional[list[list[int]]] = None

    # Report
    report: Optional[str] = None
