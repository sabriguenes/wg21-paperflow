#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pydantic models for the advocatus pipeline.

Domain models are the sole schema authority. ``advocatus.md`` provides
LLM instructions; these models enforce the output structure via Pydantic
AI's ``output_type``. Frozen domain models are updated via
``model_copy(update=...)``.

``SourceLoc`` is imported from ``paperstore`` (the canonical home for
the loc type at the storage layer). ``Articulus`` and ``Boundary``
retain a ``SourceLoc`` field for provenance; all other models
reference articuli by ``uid`` (an integer assigned at the storage
layer).
"""

from __future__ import annotations

from typing import Literal, Optional

from paperstore import SourceLoc
from pydantic import BaseModel, Field

# -- Enumerations ------------------------------------------------------------

ClaimKind = Literal["normative", "factual"]
StakeholderStance = Literal["opponent", "ally", "neutral"]
DossierLabel = Literal["public_record", "indexed", "operator_provided"]
ChallengeName = Literal[
    "confessio",
    "articulus",
    "testimonium",
    "humanitas",
    "prudentia",
    "dignitas",
]
ChallengeVerdict = Literal["killed", "relegated", "survived"]
ForumKind = Literal["lewg", "reflector", "nb_comment", "hallway", "other"]
DamageKind = Literal[
    "paper_killing",
    "section_weakening",
    "revision_forcing",
    "capital_cost",
]
SeverityKind = Literal["high", "medium", "low"]
Seal = Literal["sine_causa", "cum_objectionibus", "nihil_obstat"]


# -- Domain models -----------------------------------------------------------


class Articulus(BaseModel, frozen=True):
    """A claim mapped for examination by the tribunal.

    Articuli are derived from dissect's claims. The boundaries of the
    cause are the boundaries of the paper's own words; nothing outside
    the articuli may be examined.
    """

    uid: int
    loc: SourceLoc
    text: str = Field(description="Exact quote from the paper.")
    section: str = Field(description="Section header where the claim appears.")
    kind: ClaimKind = Field(default="normative")
    question: str = Field(
        description="Question whose answer would constitute sufficient evidence.",
    )


class Boundary(BaseModel, frozen=True):
    """What the paper explicitly disclaims, concedes, or leaves to the reader.

    The fourth reading in Phase I (Read Scripta) draws the boundaries.
    They are sacred: a charge that crosses them has assumed facts not
    in evidence.
    """

    uid: int
    loc: SourceLoc
    text: str = Field(description="Exact quote from the paper.")
    kind: Literal["disclaim", "concede", "defer"] = Field(
        description="'disclaim': the paper says X is not its concern. "
        "'concede': the paper acknowledges X is a limitation. "
        "'defer': the paper leaves X to the reader / another paper.",
    )


class Stakeholder(BaseModel, frozen=True):
    """A named person or body with a position on the cause."""

    name: str = Field(description="Specific person, faction, NB, or constituency.")
    position: str = Field(description="One-sentence summary of their published stance.")
    source_url: str = Field(default="", description="Where the position was found.")
    stance: StakeholderStance = Field(
        description="'opponent' / 'ally' / 'neutral' relative to the paper's thesis.",
    )


class DossierEntry(BaseModel, frozen=True):
    """Labeled evidence assembled in Phase II (Inquisitio).

    The label travels with the evidence through every subsequent
    phase. Only ``public_record`` and ``indexed`` are citable; the
    ``operator_provided`` label is admissible only for informing the
    Advocatus's own judgment.
    """

    label: DossierLabel
    text: str = Field(description="The finding or claim from the source.")
    source_url: str = Field(default="", description="Where it came from.")
    relevance: str = Field(
        default="",
        description="One-sentence note on how this bears on the cause.",
    )


class ExamOutcome(BaseModel, frozen=True):
    """Outcome of a single Examen test on an articulus.

    Three tests per articulus: Veritas (factual accuracy), Ratio
    (logical soundness), Auctoritas (citation support). Each test
    yields one ``ExamOutcome``.
    """

    passed: bool
    reasoning: str = Field(
        description="Brief, specific. If failed, name what contradicts the claim.",
    )


class ArticulusExam(BaseModel, frozen=True):
    """Three test results plus a confidence signal for one articulus."""

    articulus_uid: int
    veritas: ExamOutcome
    ratio: ExamOutcome
    auctoritas: ExamOutcome
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Self-reported confidence in this exam (0.0 - 1.0).",
    )


class CandidateCharge(BaseModel, frozen=True):
    """A draft objection from the Diaboli.

    Every candidate charge must include four elements. A charge missing
    any element is noise, not prosecution.
    """

    articulus_uid: int
    quoted_text: str = Field(description="Exact quote from the paper being challenged.")
    failed_test: Literal["veritas", "ratio", "auctoritas"]
    contradicting_uid: Optional[int] = Field(
        default=None,
        description="UID of contradicting evidence in the paper, if any.",
    )
    contradicting_evidence: str = Field(
        description="The specific source, testimony, or logical gap that "
        "contradicts the claim.",
    )
    gravamen: str = Field(
        description="The essential complaint, in one sentence. The load-bearing "
        "core of the objection.",
    )


class DefensorChallenge(BaseModel, frozen=True):
    """One Defensor challenge applied to a candidate charge.

    Six challenges in order: Confessio, Articulus, Testimonium,
    Humanitas, Prudentia, Dignitas. The order is a funnel; each
    challenge is cheaper than the next.
    """

    challenge: ChallengeName
    verdict: ChallengeVerdict = Field(
        description="'killed': the charge does not survive this challenge. "
        "'relegated': the charge belongs in Notae Minores (Dignitas only). "
        "'survived': the charge passes this challenge; proceed to the next.",
    )
    reasoning: str = Field(description="Why the verdict was reached.")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Self-reported confidence in this verdict (0.0 - 1.0).",
    )


class DefensorChargeOutput(BaseModel, frozen=True):
    """Per-charge sub-agent return.

    The sub-agent runs the six challenges in order and stops at the
    first ``killed`` or ``relegated`` verdict, otherwise reports
    ``survived`` for all six.
    """

    charge_uid: int
    challenges: list[DefensorChallenge]
    final: ChallengeVerdict = Field(
        description="The disposition of the charge: killed / relegated / survived.",
    )


class SurvivingCharge(BaseModel, frozen=True):
    """A candidate charge that passed all six Defensor challenges."""

    articulus_uid: int
    charge: CandidateCharge
    defensor_chain: list[DefensorChallenge] = Field(
        description="The six DefensorChallenge results that cleared this charge.",
    )


class Probatio(BaseModel, frozen=True):
    """A section certified strong because the Defensor killed every charge.

    The probatio tells the postulator which parts of the paper are
    battle-hardened.
    """

    articulus_uid: int
    killed_charge: CandidateCharge
    killing_challenge: ChallengeName
    explanation: str = Field(
        description="Brief: why the section withstands opposition.",
    )


class Motivatio(BaseModel, frozen=True):
    """The reasoning that connects an objection to a real consequence."""

    adversary: str = Field(
        description="Specific person, faction, NB, or constituency who would "
        "actually raise this objection.",
    )
    forum: ForumKind = Field(description="Where this attack would land.")
    damage: DamageKind = Field(description="What happens if the attack lands.")
    explanation: str = Field(
        description="One sentence connecting adversary, forum, and damage.",
    )


class Objection(BaseModel, frozen=True):
    """A surviving charge with motivatio attached, ready for the Relatio."""

    articulus_uid: int
    charge: SurvivingCharge
    motivatio: Motivatio
    severity: SeverityKind = Field(
        description="'high' / 'medium' / 'low' for ordering in the Relatio.",
    )


class NotaMinor(BaseModel, frozen=True):
    """An editorial observation relegated by the Dignitas challenge.

    Typos, formatting, word-choice quibbles, citation formatting -
    housekeeping, not charges.
    """

    uid: Optional[int] = Field(default=None)
    text: str = Field(description="The observation, in one short sentence.")


# -- Per-step outputs --------------------------------------------------------


class ScriptaOutput(BaseModel, frozen=True):
    """Step 1 (Read Scripta) output."""

    central_thesis_recap: str = Field(
        description="One-sentence recap of the caput causae as the tribunal reads it.",
    )
    articuli: list[Articulus] = []
    boundaries: list[Boundary] = []


class PublicRecordOutput(BaseModel, frozen=True):
    """Step 2 (Survey Public Record) output."""

    dossier_entries: list[DossierEntry] = []


class StakeholdersOutput(BaseModel, frozen=True):
    """Step 3 (Map Stakeholders) output."""

    stakeholders: list[Stakeholder] = []


class TabulaFontiumEntry(BaseModel, frozen=True):
    """One row in the citation resolution table."""

    paper_id: str
    resolution_method: str
    resolved: bool
    source_url: str = ""
    quote_match: Literal["exact", "partial", "mismatch", "not_checked"] = "not_checked"
    discrepancy: str = ""


class CitationVerificationOutput(BaseModel, frozen=True):
    """Step 4 (Verify Citations) output."""

    tabula_fontium: list[TabulaFontiumEntry] = []


class ExamenOutput(BaseModel, frozen=True):
    """Step 5 (Examine Articuli) output for one articulus."""

    exam: ArticulusExam


class ChargesOutput(BaseModel, frozen=True):
    """Step 6 (File Charges) output."""

    candidate_charges: list[CandidateCharge] = []


class MotivatioOutput(BaseModel, frozen=True):
    """Step 8 (Motivatio) output."""

    objections: list[Objection] = []


class WeighCauseOutput(BaseModel, frozen=True):
    """Step 9 (Weigh the Cause) output."""

    seal: Seal
    central_thesis_survives: bool = Field(
        description="True if the caput causae withstands the examination.",
    )
    one_sentence_assessment: str = Field(
        description="The single sentence that closes the Relatio.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Overall confidence in the Relatio (0.0 - 1.0).",
    )


# -- Pipeline state ----------------------------------------------------------


class PipelineState(BaseModel):
    """Mutable accumulator threaded through every advocatus pipeline step."""

    paper_source: Optional[str] = None
    paper_id: str = ""
    paper_title: str = ""
    paper_audience: str = ""
    paper_authors: list[str] = []

    # Step 0 (Load) - dissect data converted to domain models
    dissect_articuli_seed: Optional[list[Articulus]] = None
    dissect_evidence: Optional[list[DossierEntry]] = None
    dissect_markers: Optional[list[Articulus]] = None
    dissect_caput_causae: Optional[str] = None
    dissect_citation_audit: Optional[list[TabulaFontiumEntry]] = None
    dissect_external_evidence: Optional[list[DossierEntry]] = None

    # Step 1 (Read Scripta)
    central_thesis_recap: Optional[str] = None
    articuli: Optional[list[Articulus]] = None
    boundaries: Optional[list[Boundary]] = None

    # Step 2 (Survey Public Record)
    # Step 3 (Map Stakeholders)
    # Step 4 (Verify Citations) - all contribute to the dossier
    dossier: Optional[list[DossierEntry]] = None
    stakeholders: Optional[list[Stakeholder]] = None
    tabula_fontium: Optional[list[TabulaFontiumEntry]] = None

    # Step 5 (Examine Articuli)
    exams: Optional[list[ArticulusExam]] = None

    # Step 6 (File Charges)
    candidate_charges: Optional[list[CandidateCharge]] = None

    # Step 7 (Defensor Cross-Examination)
    defensor_results: Optional[list[DefensorChargeOutput]] = None
    surviving_charges: Optional[list[SurvivingCharge]] = None
    probationes: Optional[list[Probatio]] = None
    notae_minores: Optional[list[NotaMinor]] = None

    # Step 8 (Motivatio)
    objections: Optional[list[Objection]] = None

    # Step 9 (Weigh the Cause)
    seal: Optional[Seal] = None
    central_thesis_survives: Optional[bool] = None
    one_sentence_assessment: Optional[str] = None
    confidence: Optional[float] = None

    # Step 10 (Render Relatio)
    relatio: Optional[str] = None
