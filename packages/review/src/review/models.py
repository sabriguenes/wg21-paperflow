#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pydantic models for the review pipeline.

Domain models match the Classes section of review.md. Per-step output
models group the Writes fields for each step so Instructor can return
structured data.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


# -- Domain models -----------------------------------------------------------

class Claim(BaseModel, frozen=True):
    text: str
    section: str
    tag: Literal["factual", "normative"]


class Premise(BaseModel, frozen=True):
    text: str
    section: str


class ThinSection(BaseModel, frozen=True):
    section: str
    scope_stated: str
    audience_affected: str


class ArgumentStructure(BaseModel, frozen=True):
    type: Literal["elimination", "analogy", "induction"]
    section: str
    elements: list[str]


class EvidenceFinding(BaseModel, frozen=True):
    source: str
    date: str
    substance: str


class Evidence(BaseModel, frozen=True):
    paper_reception: list[EvidenceFinding]
    committee_history: list[EvidenceFinding]
    referenced_papers: list[EvidenceFinding]
    domain_landscape: list[EvidenceFinding]
    rehabilitated_alternatives: list[EvidenceFinding]


class Assumption(BaseModel, frozen=True):
    assumption: str
    status: Literal["verified", "plausible", "unsupported"]
    source: Optional[str] = None


class ConfirmedCounterexample(BaseModel, frozen=True):
    eliminated_option: str
    evidence: EvidenceFinding


class CandidateFinding(BaseModel, frozen=True):
    quoted_text: str
    section: str
    failed_test: Literal["accuracy", "logic", "citation_support", "internal_consistency"]
    contradicting_evidence: str
    core_complaint: str
    finding_type: Literal["miss", "inconsistency"]


class KilledFinding(BaseModel, frozen=True):
    finding: CandidateFinding
    killed_by: Literal[
        "paper_handles_it",
        "not_actually_claimed",
        "minimal_clarification",
        "not_credible",
        "self_defeating",
        "too_trivial",
    ]
    reason: str


class InterpretedFinding(BaseModel, frozen=True):
    finding: CandidateFinding
    who: str
    where: str
    what_damage: str


class CertifiedSection(BaseModel, frozen=True):
    section: str
    killed_finding: Optional[str] = None
    reason: str


class CitationEntry(BaseModel, frozen=True):
    link: str
    status: Literal["resolved", "unresolved_self", "unresolved_third_party"]
    target_url: Optional[str] = None
    quote_match: Optional[bool] = None
    notes: Optional[str] = None


# -- Pipeline state ----------------------------------------------------------

class PipelineState(BaseModel):
    """Mutable accumulator threaded through every step."""

    # Step 0
    title: Optional[str] = None
    document_number: Optional[str] = None
    author: Optional[str] = None
    audience: Optional[str] = None
    paper_type: Optional[Literal["ask", "inform"]] = None

    # Step 1
    thesis: Optional[str] = None
    claims: Optional[list[Claim]] = None
    boundaries: Optional[list[str]] = None
    premises: Optional[list[Premise]] = None
    thin_sections: Optional[list[ThinSection]] = None
    argument_structures: Optional[list[ArgumentStructure]] = None

    # Step 2
    evidence: Optional[Evidence] = None

    # Step 3
    verified_assumptions: Optional[list[Assumption]] = None
    confirmed_counterexamples: Optional[list[ConfirmedCounterexample]] = None

    # Step 4
    candidate_findings: Optional[list[CandidateFinding]] = None

    # Step 5
    surviving_findings: Optional[list[CandidateFinding]] = None
    killed_findings: Optional[list[KilledFinding]] = None
    minor_notes: Optional[list[str]] = None

    # Step 6
    interpreted_findings: Optional[list[InterpretedFinding]] = None
    certified_sections: Optional[list[CertifiedSection]] = None
    whole_paper_assessment: Optional[str] = None
    verdict: Optional[Literal["no_objections", "with_objections"]] = None

    # Step 7
    citation_table: Optional[list[CitationEntry]] = None


# -- Per-step output models --------------------------------------------------

class ClassifyOutput(BaseModel, frozen=True):
    """Step 0: extract metadata and classify."""

    title: str
    document_number: str
    author: str
    audience: str
    paper_type: Literal["ask", "inform"]


class ReadPaperOutput(BaseModel, frozen=True):
    """Step 1: four-reading analysis."""

    thesis: str
    claims: list[Claim]
    boundaries: list[str]
    premises: list[Premise]
    thin_sections: list[ThinSection]
    argument_structures: list[ArgumentStructure]


class GatherEvidenceOutput(BaseModel, frozen=True):
    """Step 2: evidence collection."""

    evidence: Evidence


class ResolveAssumptionsOutput(BaseModel, frozen=True):
    """Step 3: assumption resolution."""

    verified_assumptions: list[Assumption]
    confirmed_counterexamples: list[ConfirmedCounterexample] = []


class TestAndDraftOutput(BaseModel, frozen=True):
    """Step 4: test claims and draft findings."""

    candidate_findings: list[CandidateFinding] = []


class ChallengeFindingsOutput(BaseModel, frozen=True):
    """Step 5: challenge each finding."""

    surviving_findings: list[CandidateFinding] = []
    killed_findings: list[KilledFinding] = []
    minor_notes: list[str] = []


class InterpretResultsOutput(BaseModel, frozen=True):
    """Step 6: interpret and assess."""

    interpreted_findings: list[InterpretedFinding] = []
    certified_sections: list[CertifiedSection] = []
    whole_paper_assessment: str
    verdict: Literal["no_objections", "with_objections"]


class VerifyCitationsOutput(BaseModel, frozen=True):
    """Step 7: citation verification."""

    citation_table: list[CitationEntry] = []


class WriteOutputOutput(BaseModel, frozen=True):
    """Step 8: rendered markdown report."""

    report: str
