#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pydantic models for the review pipeline.

Domain models match the Classes section of review.md. Per-step output
models group the Writes fields for each step so Pydantic AI can return
structured data.
"""

from __future__ import annotations

from typing import Literal

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
    source: str | None = None


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
    killed_finding: str | None = None
    reason: str


class CitationEntry(BaseModel, frozen=True):
    link: str
    status: Literal["resolved", "unresolved_self", "unresolved_third_party"]
    target_url: str | None = None
    quote_match: bool | None = None
    notes: str | None = None


# -- Pipeline state ----------------------------------------------------------

class PipelineState(BaseModel):
    """Mutable accumulator threaded through every step."""

    # Step 0
    title: str | None = None
    document_number: str | None = None
    author: str | None = None
    audience: str | None = None
    paper_type: Literal["ask", "inform"] | None = None

    # Step 1
    thesis: str | None = None
    claims: list[Claim] | None = None
    boundaries: list[str] | None = None
    premises: list[Premise] | None = None
    thin_sections: list[ThinSection] | None = None
    argument_structures: list[ArgumentStructure] | None = None

    # Step 2
    evidence: Evidence | None = None

    # Step 3
    verified_assumptions: list[Assumption] | None = None
    confirmed_counterexamples: list[ConfirmedCounterexample] | None = None

    # Step 4
    candidate_findings: list[CandidateFinding] | None = None

    # Step 5
    surviving_findings: list[CandidateFinding] | None = None
    killed_findings: list[KilledFinding] | None = None
    minor_notes: list[str] | None = None

    # Step 6
    interpreted_findings: list[InterpretedFinding] | None = None
    certified_sections: list[CertifiedSection] | None = None
    whole_paper_assessment: str | None = None
    verdict: Literal["no_objections", "with_objections"] | None = None

    # Step 7
    citation_table: list[CitationEntry] | None = None


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
