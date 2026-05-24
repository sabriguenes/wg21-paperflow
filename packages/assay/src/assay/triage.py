#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Paper triage: determine if a document is worth full structural analysis.

Classifies papers as proposals (analyze), reference documents (skip),
or wording-dominant papers (skip). The decision is deterministic,
based on frontmatter, heading structure, and size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from assay.models import ChunkEntry

_CLAUSE_REF_RE = re.compile(r"\[[\w.]+\]")

_PROPOSAL_HEADING_SIGNALS = {"abstract", "motivation", "design", "poll", "straw poll"}
_PROPOSAL_TITLE_SIGNALS = {"rationale", "proposal", "towards", "a plan for"}

_SIZE_THRESHOLD = 300_000


@dataclass
class TriageResult:
    """Result of paper triage."""

    analyze: bool
    reason: str
    paper_type: str
    stats: dict = field(default_factory=dict)


def should_analyze(
    chunk_map: list[ChunkEntry],
    title: str,
    intent: str,
    audience: list[str],
    paper_md: str,
) -> TriageResult:
    """Determine if a paper is worth full structural analysis.

    Returns TriageResult with analyze=True for proposals,
    analyze=False for reference documents and wording-dominant papers.
    """
    total_chars = len(paper_md)
    chunk_count = len(chunk_map)
    headings = [c.heading for c in chunk_map]
    largest_chunk = max((c.char_count for c in chunk_map), default=0)

    clause_headings = sum(1 for h in headings if _CLAUSE_REF_RE.search(h))
    total_headings = max(len(headings), 1)
    wording_ratio = clause_headings / total_headings

    headings_lower = [h.lower() for h in headings]
    has_heading_signal = any(
        any(signal in h for signal in _PROPOSAL_HEADING_SIGNALS)
        for h in headings_lower
    )

    title_lower = title.lower()
    has_title_signal = any(signal in title_lower for signal in _PROPOSAL_TITLE_SIGNALS)

    has_proposal_signal = has_heading_signal or has_title_signal

    intent_lower = intent.strip().lower()
    has_intent_ask = intent_lower in ("ask", "adopt", "direction", "review")

    audience_str = ", ".join(audience)

    stats = {
        "total_chars": total_chars,
        "chunk_count": chunk_count,
        "largest_chunk_chars": largest_chunk,
        "wording_ratio": round(wording_ratio, 3),
        "clause_headings": clause_headings,
        "total_headings": total_headings,
        "has_proposal_signal": has_proposal_signal,
        "has_intent_ask": has_intent_ask,
        "audience": audience_str,
    }

    if has_intent_ask or has_proposal_signal:
        return TriageResult(
            analyze=True,
            reason="",
            paper_type="proposal",
            stats=stats,
        )

    if total_chars > _SIZE_THRESHOLD and wording_ratio > 0.5:
        return TriageResult(
            analyze=False,
            reason=f"Wording-dominant: {clause_headings}/{total_headings} clause headings "
                   f"({wording_ratio:.0%}), {total_chars:,} characters.",
            paper_type="wording_dominant",
            stats=stats,
        )

    if total_chars > _SIZE_THRESHOLD:
        return TriageResult(
            analyze=False,
            reason=f"Reference document: no abstract, motivation, or design sections. "
                   f"{total_chars:,} characters.",
            paper_type="reference_document",
            stats=stats,
        )

    return TriageResult(
        analyze=True,
        reason="",
        paper_type="proposal",
        stats=stats,
    )
