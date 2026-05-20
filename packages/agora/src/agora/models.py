#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pydantic models for the agora pipeline.

One schema, fields filled progressively. The analysis-phase steps in
this package populate every structural and analytical field of
``Thread`` / ``Reply`` / ``EncounterPlan``. Generation-phase fields
(``content``, ``character_username``, ``score``, furniture flags,
vote counts) stay ``None`` until a future generation phase fills
them in.

``SourceLoc`` is imported from ``paperstore`` (the canonical home for
the loc type at the storage layer). Each ``TechnicalAnchor`` carries
a ``claim_uid`` (the paperstore integer key) and an optional
``SourceLoc`` for display.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from paperstore import SourceLoc
from pydantic import BaseModel, Field

# -- Enumerations ------------------------------------------------------------

PaperType = Literal["wording", "proposal", "directional"]
HeatTier = Literal["cold", "warm", "hot", "thermonuclear"]
InterestTier = Literal["niche", "relevant", "magnetic", "gravitational"]
Subreddit = Literal["r/ewg", "r/lewg", "r/cwg", "r/lwg"]
AnchorKind = Literal["load_bearing", "conflicted", "critical_gap"]
ReplyRole = Literal[
    "signal",
    "noise",
    "encounter",
    "tangent",
    "teaser",
    "mod",
    "deleted",
]
EncounterResolution = Literal["concession", "narrowing", "stalemate"]
RevisionCase = Literal["A", "B", "C"]
"""``A``: new paper, no prior thread. ``B``: re-run of an existing
revision (regenerate same thread). ``C``: new revision; the prior
thread is referenced and the submission body calls out the delta."""


# -- Domain models -----------------------------------------------------------


class TechnicalAnchor(BaseModel, frozen=True):
    """A load-bearing claim, an internally-contested claim, or a critical gap.

    Derived from paperstore extract tables in Step 1 (Smell Test).
    Every signal slot must address at least one anchor; every anchor
    must be addressed by at least one slot. The ``loc`` ties the
    anchor back to the exact line of the paper that prompted it.
    """

    id: str = Field(description="Stable id within the thread, e.g. ``a01``.")
    kind: AnchorKind
    summary: str = Field(
        description="One-line description of the anchor (what makes it load-bearing,"
        " contested, or gap-shaped).",
    )
    claim_text: str = Field(
        description="Exact quote from the paper that the anchor crystallises.",
    )
    claim_uid: int
    claim_loc: SourceLoc | None = Field(
        default=None,
        description="Source location for display. Not used for identity.",
    )
    supports: list[str] = Field(
        default_factory=list,
        description="Optional list of evidence ids or external references"
        " supporting / contradicting this anchor.",
    )


class ResearchAgentReport(BaseModel, frozen=True):
    """Return from one Step 2 research sub-agent.

    Three sub-agents run in parallel: public reception, committee
    history, author + ecosystem. Each returns ``findings`` capped at
    roughly 200 words plus a coarse heat / interest signal that
    Step 3 will calibrate against.
    """

    agent: Literal["public_reception", "committee_history", "author_ecosystem"]
    findings: str = Field(description="Compressed research summary (~200 words max).")
    sources: list[str] = Field(
        default_factory=list,
        description="URLs the agent considered most relevant.",
    )
    heat_signal: HeatTier = Field(
        description="Coarse heat suggestion from this agent's slice of the record.",
    )
    interest_signal: InterestTier = Field(
        description="Coarse interest suggestion from this agent's slice of the record.",
    )


class ResearchSummary(BaseModel, frozen=True):
    """The three research sub-agent reports collected for Step 3 to read."""

    public_reception: ResearchAgentReport
    committee_history: ResearchAgentReport
    author_ecosystem: ResearchAgentReport


class DesignTension(BaseModel, frozen=True):
    """A genuine disagreement seeded by stored rhetorical markers.

    Each design tension is a candidate for an Encounter in Step 6.
    Step 3 decides how many encounters this thread will run; Step 5
    pre-allocates encounter slots; Step 6 fills the position pair and
    resolution path.
    """

    id: str = Field(description="Stable id within the thread, e.g. ``t01``.")
    description: str = Field(description="One-line description of the tension.")
    anchor_id: Optional[str] = Field(
        default=None,
        description="Anchor this tension grows from, if any.",
    )


class EncounterPlan(BaseModel, frozen=True):
    """A planned multi-turn back-and-forth between two named positions.

    Step 6 emits one ``EncounterPlan`` per allocated encounter and
    links its ``slot_ids`` to the encounter-role replies that Step 5
    pre-allocated.
    """

    encounter_id: str
    design_tension_id: str
    design_tension: str = Field(description="One-line tension description.")
    position_a: str = Field(description="First substantive position.")
    position_b: str = Field(description="Second substantive position.")
    resolution: EncounterResolution
    slot_ids: list[str] = Field(
        description="Ordered ``Reply.slot_id`` values for this encounter's turns.",
    )


class Reply(BaseModel):
    """A single planned reply in the thread.

    Analysis-phase fields are required and describe what the reply
    must accomplish; generation-phase fields (``content``,
    ``character_username``, ``score``, furniture flags) are
    ``Optional`` and stay ``None`` until a future generation phase
    runs. ``brief`` is permanent - the audit trail for why this reply
    was planned.

    Not frozen: the generation phase mutates these in place.
    """

    # -- Analysis phase (required) -------------------------------------------

    slot_id: str = Field(description="Unique within the thread, e.g. ``s01``.")
    parent_slot_id: Optional[str] = Field(
        default=None,
        description="``None`` for top-level slots; otherwise a sibling's ``slot_id``.",
    )
    depth: int = Field(ge=0, le=6, description="Reply depth (0 = top-level).")
    role: ReplyRole
    brief: str = Field(
        description="1-3 sentences. What this reply must accomplish."
        " Permanent audit trail; survives generation.",
    )

    anchor_id: Optional[str] = Field(
        default=None,
        description="``TechnicalAnchor.id`` this reply addresses (signal / encounter).",
    )
    domain_lens: Optional[int] = Field(
        default=None, ge=1, le=13,
        description="Table C domain index (1-13) for signal / encounter roles.",
    )
    encounter_id: Optional[str] = Field(
        default=None,
        description="``EncounterPlan.encounter_id`` for encounter turns.",
    )
    noise_tone: Optional[str] = Field(
        default=None,
        description="Tone label for noise slots (e.g. ``snark``, ``earnest``).",
    )
    noise_stance: Optional[str] = Field(
        default=None,
        description="Stance label for noise slots (e.g. ``pro``, ``con``, ``baffled``).",
    )

    carries_quote: bool = False
    carries_code: bool = False
    carries_link: bool = False

    # -- Generation phase (Optional / None for now) --------------------------

    content: Optional[str] = None
    character_username: Optional[str] = None
    score: Optional[int] = None
    ordering: Optional[int] = None
    time_label: Optional[str] = None
    controversial: bool = False
    awards: list[str] = Field(default_factory=list)
    edited: Optional[str] = None
    collapsed: bool = False
    deleted: bool = False
    removed: bool = False
    is_mod: bool = False
    is_op: bool = False
    flair: Optional[str] = None


class Thread(BaseModel):
    """A planned r/wg21 thread for one WG21 paper.

    Analysis-phase fields are populated by Steps 0-7 in this package.
    Generation-phase fields (``submission_poster_id``,
    ``submission_votes``, ``submission_upvote_pct``, ``generated_at``)
    stay ``None`` until a future generation phase runs.

    Not frozen: the generation phase mutates these in place.
    """

    # -- Step 0: paper identity (required) -----------------------------------

    document: str = Field(description="Full revisioned paper id, e.g. ``P2900R14``.")
    paper: str = Field(description="Paper id without revision, e.g. ``P2900``.")
    revision: int = Field(ge=0, description="Numeric revision (``0`` for ``R0``).")
    title: str
    authors: str = Field(description="Author list as a single display string.")
    audience: str = Field(description="Comma-joined target groups from paperstore.")
    date: str = Field(description="Document date as stored in paperstore.")
    subreddit: Subreddit
    prior_revision: Optional[str] = Field(
        default=None,
        description="``Pnnnnn.Rk-1`` document if this is a re-revision (Case C).",
    )
    revision_case: RevisionCase = Field(
        default="A",
        description="``A`` new paper, ``B`` re-run of same revision, ``C`` new revision.",
    )

    # -- Step 1 (Smell Test) + Step 2 (Research) -----------------------------

    paper_type: PaperType
    technical_anchors: list[TechnicalAnchor] = Field(default_factory=list)
    tangent_magnets: list[str] = Field(
        default_factory=list,
        description="Topics likely to attract off-topic but plausible reply chains.",
    )
    hot_takes: list[str] = Field(
        default_factory=list,
        description="Inflammatory but plausible takes seeded from rhetorical markers.",
    )
    misconception_traps: list[str] = Field(
        default_factory=list,
        description="Predictable misreadings the thread should anticipate and dismantle.",
    )
    design_tensions: list[DesignTension] = Field(default_factory=list)
    research_summary: ResearchSummary

    # -- Step 3 (Calibrate) --------------------------------------------------

    heat: HeatTier
    interest: InterestTier
    target_comment_count: int = Field(
        ge=0, description="Planned total reply count (heat baseline * interest mult)."
    )
    encounter_count: int = Field(ge=0, description="How many encounters Step 6 runs.")
    signal_count: int = Field(ge=0, description="Planned signal reply count.")
    noise_count: int = Field(ge=0, description="Planned noise reply count.")

    # -- Step 4 (Submission) -------------------------------------------------

    submission_title: str
    submission_body: str
    submission_link: str = Field(description="Best canonical paper link.")
    submission_flair: str = Field(default="")

    # -- Steps 5-6 (Structure) -----------------------------------------------

    replies: list[Reply] = Field(default_factory=list)
    encounters: list[EncounterPlan] = Field(default_factory=list)

    # -- Generation phase (Optional / None for now) --------------------------

    submission_poster_id: Optional[str] = None
    submission_votes: Optional[int] = None
    submission_upvote_pct: Optional[str] = None
    generated_at: Optional[datetime] = None


# -- Per-step LLM output classes ---------------------------------------------


class SmellTestOutput(BaseModel, frozen=True):
    """Step 1 (Smell Test) output."""

    paper_type: PaperType
    technical_anchors: list[TechnicalAnchor] = Field(default_factory=list)
    hot_takes: list[str] = Field(default_factory=list)
    tangent_magnets: list[str] = Field(default_factory=list)
    misconception_traps: list[str] = Field(default_factory=list)
    design_tensions: list[DesignTension] = Field(default_factory=list)


class CalibrationOutput(BaseModel, frozen=True):
    """Step 3 (Calibrate) output."""

    heat: HeatTier
    interest: InterestTier
    target_comment_count: int = Field(ge=0)
    encounter_count: int = Field(ge=0)
    signal_count: int = Field(ge=0)
    noise_count: int = Field(ge=0)
    rationale: str = Field(
        description="One paragraph: why this heat/interest combination, citing "
        "the paper-type floors and any author-gravity adjustments.",
    )


class SubmissionOutput(BaseModel, frozen=True):
    """Step 4 (Submission) output."""

    submission_title: str
    submission_body: str
    submission_link: str
    submission_flair: str = ""
    revision_case: RevisionCase = "A"


class SkeletonOutput(BaseModel, frozen=True):
    """Step 5 (Skeleton) output.

    Carries the planned replies plus pre-allocated encounter slot
    pointers for Step 6 to fill in.
    """

    replies: list[Reply] = Field(default_factory=list)
    encounter_slot_groups: list[list[str]] = Field(
        default_factory=list,
        description="One list of ``slot_id`` strings per allocated encounter, "
        "in turn order. Length must equal ``encounter_count`` from Step 3.",
    )


class EncountersOutput(BaseModel, frozen=True):
    """Step 6 (Encounters) output."""

    encounters: list[EncounterPlan] = Field(default_factory=list)


# -- Pipeline state ----------------------------------------------------------


class PipelineState(BaseModel):
    """Mutable accumulator threaded through every agora pipeline step.

    Steps read by attribute name (matching their ``Reads:`` metadata in
    ``agora.md``) and write by attribute name (matching ``Writes:``).
    The runner enforces nothing about field types here; Pydantic
    validates per-step LLM outputs separately.
    """

    # -- Step 0 (Load): paper identity + paperstore extract data --------------

    paper_id: str = ""
    paper_source: Optional[str] = None
    paper_title: str = ""
    paper_authors: list[str] = Field(default_factory=list)
    paper_audience: str = ""
    paper_date: str = ""
    paper_url: str = ""
    paper_number: str = ""
    paper_revision: int = 0
    subreddit: Optional[Subreddit] = None
    prior_revision: Optional[str] = None
    revision_case: RevisionCase = "A"

    # Field names retain "dissect_" prefix for backward compatibility with
    # pipeline.py and serialized state; data comes from paperstore extract tables.
    dissect_claims: Optional[list[dict]] = None
    dissect_evidence: Optional[list[dict]] = None
    dissect_markers: Optional[list[dict]] = None
    dissect_caput_causae: Optional[str] = None
    dissect_citation_audit: Optional[list[dict]] = None
    dissect_external_citations: Optional[list[dict]] = None

    # -- Step 1 (Smell Test) -------------------------------------------------

    paper_type: Optional[PaperType] = None
    technical_anchors: Optional[list[TechnicalAnchor]] = None
    hot_takes: Optional[list[str]] = None
    tangent_magnets: Optional[list[str]] = None
    misconception_traps: Optional[list[str]] = None
    design_tensions: Optional[list[DesignTension]] = None

    # -- Step 2 (Research) ---------------------------------------------------

    research_summary: Optional[ResearchSummary] = None

    # -- Step 3 (Calibrate) --------------------------------------------------

    heat: Optional[HeatTier] = None
    interest: Optional[InterestTier] = None
    target_comment_count: Optional[int] = None
    encounter_count: Optional[int] = None
    signal_count: Optional[int] = None
    noise_count: Optional[int] = None

    # -- Step 4 (Submission) -------------------------------------------------

    submission_title: Optional[str] = None
    submission_body: Optional[str] = None
    submission_link: Optional[str] = None
    submission_flair: Optional[str] = None

    # -- Step 5 (Skeleton) ---------------------------------------------------

    replies: Optional[list[Reply]] = None
    encounter_slot_groups: Optional[list[list[str]]] = None

    # -- Step 6 (Encounters) -------------------------------------------------

    encounters: Optional[list[EncounterPlan]] = None

    # -- Step 7 (Serialize) --------------------------------------------------

    thread: Optional[Thread] = None
