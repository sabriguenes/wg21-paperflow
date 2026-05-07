#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pure-Python code harness for the extractor pipeline.

Handles SourceLoc computation, paper chunking, and deterministic dedup
(tiers 0 and 1). No LLM calls, no paperstore imports, no network I/O.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import TypeVar

from review.models import (
    Chunk,
    CitationRef,
    Claim,
    Evidence,
    RawClaim,
    RawEvidence,
    SourceLoc,
)

T = TypeVar("T", Claim, Evidence)

_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


def build_newline_offsets(source: str) -> list[int]:
    """Return sorted list of character offsets where newlines occur.

    O(n) scan, used once per paper to enable O(log n) line lookups.
    """
    return [i for i, ch in enumerate(source) if ch == "\n"]


def find_loc(
    text: str, source: str, newline_offsets: list[int]
) -> SourceLoc | None:
    """Find exact quote in source and return its SourceLoc.

    Uses str.find for the match, then bisect on precomputed newline
    offsets for O(log n) line/column computation.
    """
    idx = source.find(text)
    if idx == -1:
        return None

    line = bisect_right(newline_offsets, idx) + 1
    line_start = (newline_offsets[line - 2] + 1) if line > 1 else 0
    start_char = idx - line_start
    end_char = start_char + len(text) - 1

    return SourceLoc(line=line, start_char=start_char, end_char=end_char)


def chunk_paper(source: str, max_chars: int = 70_000) -> list[Chunk]:
    """Split paper into chunks of <= max_chars at markdown heading boundaries.

    Adjacent chunks overlap by 5 lines: the next chunk starts 5 lines
    before the split point. Single-chunk papers return one Chunk with
    line_offset=1.
    """
    if len(source) <= max_chars:
        return [Chunk(text=source, line_offset=1)]

    lines = source.splitlines(keepends=True)
    heading_lines: list[int] = []
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            heading_lines.append(i)

    cum_chars: list[int] = []
    total = 0
    for line in lines:
        total += len(line)
        cum_chars.append(total)

    chunks: list[Chunk] = []
    chunk_start = 0
    target = max_chars

    for h in heading_lines:
        char_at_h = cum_chars[h - 1] if h > 0 else 0
        if char_at_h >= target and h > chunk_start:
            chunk_text = "".join(lines[chunk_start:h])
            chunks.append(Chunk(text=chunk_text, line_offset=chunk_start + 1))
            overlap_start = max(0, h - 5)
            chunk_start = overlap_start
            target = (cum_chars[overlap_start - 1] if overlap_start > 0 else 0) + max_chars

    if chunk_start < len(lines):
        chunk_text = "".join(lines[chunk_start:])
        chunks.append(Chunk(text=chunk_text, line_offset=chunk_start + 1))

    return chunks


def promote_claims(raws: list[RawClaim], source: str) -> list[Claim]:
    """Convert RawClaims to Claims by computing SourceLocs.

    Resolves depends_on text references to SourceLocs. Items whose
    text cannot be found in the source are silently dropped.
    """
    offsets = build_newline_offsets(source)
    claims: list[Claim] = []
    text_to_loc: dict[str, SourceLoc] = {}

    for raw in raws:
        loc = find_loc(raw.text, source, offsets)
        if loc is None:
            continue
        text_to_loc[raw.text] = loc
        quotes = raw.original_quotes if raw.original_quotes else [raw.text]
        claims.append(Claim(
            loc=loc,
            text=raw.text,
            original_quotes=quotes,
            section=raw.section,
            question=raw.question,
            depends_on=[],
            merged_into=None,
        ))

    for i, raw in enumerate(raws):
        if i >= len(claims):
            break
        resolved_deps: list[SourceLoc] = []
        for dep_text in raw.depends_on:
            dep_loc = text_to_loc.get(dep_text)
            if dep_loc is not None:
                resolved_deps.append(dep_loc)
        if resolved_deps:
            claims[i] = claims[i].model_copy(update={"depends_on": resolved_deps})

    return claims


def promote_evidence(raws: list[RawEvidence], source: str) -> list[Evidence]:
    """Convert RawEvidence to Evidence by computing SourceLocs.

    Items whose text cannot be found in the source are silently dropped.
    """
    offsets = build_newline_offsets(source)
    evidence: list[Evidence] = []

    for raw in raws:
        loc = find_loc(raw.text, source, offsets)
        if loc is None:
            continue
        quotes = raw.original_quotes if raw.original_quotes else [raw.text]
        evidence.append(Evidence(
            loc=loc,
            text=raw.text,
            original_quotes=quotes,
            section=raw.section,
            supports=raw.supports,
            quantitative=raw.quantitative,
            cited=raw.cited,
            verifiable=raw.verifiable,
            normative=raw.normative,
            merged_into=None,
        ))

    return evidence


def dedup_tier0(items: list[T]) -> list[T]:
    """Tier 0: tombstone exact SourceLoc duplicates.

    When two items have identical loc, the second becomes a tombstone
    (merged_into points to the first). Returns a new list.
    """
    seen: dict[SourceLoc, int] = {}
    result: list[T] = []

    for item in items:
        if item.merged_into is not None:
            result.append(item)
            continue
        if item.loc in seen:
            survivor_idx = seen[item.loc]
            result.append(item.model_copy(update={"merged_into": items[survivor_idx].loc}))
        else:
            seen[item.loc] = len(result)
            result.append(item)

    return result


def dedup_tier1(items: list[T]) -> list[T]:
    """Tier 1: tombstone substring matches, absorb original_quotes.

    For survivors of tier 0: when one item's text is a substring of
    another's, the shorter becomes a tombstone. The longer absorbs the
    shorter's original_quotes. Returns a new list.
    """
    result = list(items)
    survivors = [(i, item) for i, item in enumerate(result) if item.merged_into is None]

    for i, (idx_a, a) in enumerate(survivors):
        if result[idx_a].merged_into is not None:
            continue
        for j, (idx_b, b) in enumerate(survivors):
            if i == j or result[idx_b].merged_into is not None:
                continue
            if a.text in b.text and a.text != b.text:
                merged_quotes = list(b.original_quotes) + list(a.original_quotes)
                result[idx_b] = b.model_copy(update={"original_quotes": merged_quotes})
                result[idx_a] = a.model_copy(update={"merged_into": b.loc})
                break
            elif b.text in a.text and a.text != b.text:
                merged_quotes = list(a.original_quotes) + list(b.original_quotes)
                result[idx_a] = a.model_copy(update={"original_quotes": merged_quotes})
                result[idx_b] = b.model_copy(update={"merged_into": a.loc})

    return result


_CITATION_PD_RE = re.compile(r"\b([PD]\d{4,5}R\d{1,2})\b", re.IGNORECASE)
_CITATION_N_RE = re.compile(r"\b(N\d{4,5})\b", re.IGNORECASE)
_LINK_URL_RE = re.compile(r"\]\([^)]*\)")


def extract_citations(paper_source: str) -> list[CitationRef]:
    """Extract and deduplicate WG21 paper number citations from markdown.

    Returns a list sorted by citation count descending. Pure Python,
    deterministic, no network I/O.
    """
    stripped = _LINK_URL_RE.sub("]", paper_source)

    counts: dict[str, int] = {}
    for m in _CITATION_PD_RE.finditer(stripped):
        pid = m.group(1).upper()
        counts[pid] = counts.get(pid, 0) + 1
    for m in _CITATION_N_RE.finditer(stripped):
        pid = m.group(1).upper()
        counts[pid] = counts.get(pid, 0) + 1

    refs = [CitationRef(paper_id=pid, count=c) for pid, c in counts.items()]
    refs.sort(key=lambda r: r.count, reverse=True)
    return refs
