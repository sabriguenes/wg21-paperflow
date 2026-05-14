#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pure-Python code harness for the extractor pipeline.

Handles line-numbered chunk formatting, SourceLoc computation from
LLM-reported start_line, paper chunking, deterministic dedup (tiers 0
and 1), and WG21 citation extraction. No LLM calls, no paperstore
imports, no network I/O.

All helper functions are module-private (underscore-prefixed).
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import chain
from typing import TypeVar

from dissect.models import (
    Chunk,
    CitationRef,
    Claim,
    Evidence,
    RawClaim,
    RawEvidence,
    RawRhetoric,
    Rhetoric,
    SourceLoc,
)

T = TypeVar("T", Claim, Evidence)

_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LINE_PREFIX_RE = re.compile(r"^\d+\|\s?")


def _strip_line_prefix(text: str) -> str:
    """Remove leading line-number prefix (e.g. '47| ') if the LLM copied it."""
    return _LINE_PREFIX_RE.sub("", text)


def _number_lines(chunk: Chunk) -> str:
    """Prepend absolute line numbers to each line of a chunk."""
    lines = chunk.text.splitlines()
    return "\n".join(
        f"{chunk.line_offset + i}| {line}" for i, line in enumerate(lines)
    )


def _chunk_paper(source: str, max_chars: int = 40_000) -> list[Chunk]:
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


def _promote_claims(
    raws: list[RawClaim], source: str, start_uid: int = 1,
) -> tuple[list[Claim], int]:
    """Convert RawClaims to Claims using start_line for location.

    Assigns sequential uids starting from start_uid. Returns
    (claims, next_uid).
    """
    lines = source.splitlines()
    claims: list[Claim] = []
    text_to_uid: dict[str, int] = {}
    uid = start_uid

    for raw in raws:
        line = raw.start_line if raw.start_line > 0 else 1
        line_text = lines[line - 1] if line <= len(lines) else ""
        text = _strip_line_prefix(raw.text)
        pos = line_text.find(text)
        if pos < 0:
            pos = 0
        loc = SourceLoc(line=line, start_char=pos, end_char=pos + len(text))
        text_to_uid[text] = uid
        quotes = raw.original_quotes if raw.original_quotes else [text]
        claims.append(Claim(
            uid=uid,
            loc=loc,
            text=text,
            original_quotes=quotes,
            section=raw.section,
            question=raw.question,
            kind=raw.kind,
            depends_on=[],
            merged_into=None,
        ))
        uid += 1

    for i, raw in enumerate(raws):
        resolved_deps: list[int] = []
        for dep_text in raw.depends_on:
            dep_uid = text_to_uid.get(dep_text)
            if dep_uid is not None:
                resolved_deps.append(dep_uid)
        if resolved_deps:
            claims[i] = claims[i].model_copy(update={"depends_on": resolved_deps})

    return (claims, start_uid + len(raws))


def _promote_evidence(
    raws: list[RawEvidence], source: str, start_uid: int = 1,
) -> tuple[list[Evidence], int]:
    """Convert RawEvidence to Evidence using start_line for location.

    Assigns sequential uids starting from start_uid. Returns
    (evidence, next_uid).
    """
    lines = source.splitlines()
    evidence: list[Evidence] = []
    uid = start_uid

    for raw in raws:
        line = raw.start_line if raw.start_line > 0 else 1
        line_text = lines[line - 1] if line <= len(lines) else ""
        text = _strip_line_prefix(raw.text)
        pos = line_text.find(text)
        if pos < 0:
            pos = 0
        loc = SourceLoc(line=line, start_char=pos, end_char=pos + len(text))
        quotes = raw.original_quotes if raw.original_quotes else [text]
        evidence.append(Evidence(
            uid=uid,
            loc=loc,
            text=text,
            original_quotes=quotes,
            section=raw.section,
            supports=raw.supports,
            quantitative=raw.quantitative,
            cited=raw.cited,
            verifiable=raw.verifiable,
            normative=raw.normative,
            merged_into=None,
        ))
        uid += 1

    return (evidence, start_uid + len(raws))


def _promote_rhetoric(
    raws: list[RawRhetoric], source: str, start_uid: int = 1,
) -> tuple[list[Rhetoric], int]:
    """Convert RawRhetoric to Rhetoric using start_line for location.

    Assigns sequential uids starting from start_uid. Returns
    (items, next_uid).
    """
    lines = source.splitlines()
    items: list[Rhetoric] = []
    uid = start_uid

    for raw in raws:
        line = raw.start_line if raw.start_line > 0 else 1
        line_text = lines[line - 1] if line <= len(lines) else ""
        text = _strip_line_prefix(raw.text)
        pos = line_text.find(text)
        if pos < 0:
            pos = 0
        loc = SourceLoc(line=line, start_char=pos, end_char=pos + len(text))
        items.append(Rhetoric(
            uid=uid,
            loc=loc,
            text=text,
            section=raw.section,
            marker_type=raw.marker_type,
            target=raw.target,
            intensity=raw.intensity,
        ))
        uid += 1

    return (items, start_uid + len(raws))


def _dedup_tier0(items: list[T]) -> list[T]:
    """Tier 0: tombstone exact SourceLoc duplicates.

    When two items have identical loc, the second becomes a tombstone
    (merged_into points to the survivor's uid). Returns a new list.
    """
    seen: dict[SourceLoc, int] = {}
    result: list[T] = []

    for item in items:
        if item.merged_into is not None:
            result.append(item)
            continue
        if item.loc in seen:
            survivor_idx = seen[item.loc]
            result.append(item.model_copy(update={"merged_into": items[survivor_idx].uid}))
        else:
            seen[item.loc] = len(result)
            result.append(item)

    return result


def _dedup_tier1(items: list[T]) -> list[T]:
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
                cur_b = result[idx_b]
                merged_quotes = list(cur_b.original_quotes) + list(a.original_quotes)
                result[idx_b] = cur_b.model_copy(update={"original_quotes": merged_quotes})
                result[idx_a] = a.model_copy(update={"merged_into": cur_b.uid})
                break
            elif b.text in a.text and a.text != b.text:
                cur_a = result[idx_a]
                merged_quotes = list(cur_a.original_quotes) + list(b.original_quotes)
                result[idx_a] = cur_a.model_copy(update={"original_quotes": merged_quotes})
                result[idx_b] = b.model_copy(update={"merged_into": cur_a.uid})

    return result


_CITATION_PD_RE = re.compile(r"\b([PD]\d{4,5}R\d{1,2})\b", re.IGNORECASE)
_CITATION_N_RE = re.compile(r"\b(N\d{4,5})\b", re.IGNORECASE)
_LINK_URL_RE = re.compile(r"\]\([^)]*\)")


def _extract_citations(paper_source: str) -> list[CitationRef]:
    """Extract and deduplicate WG21 paper number citations from markdown.

    Returns a list sorted by citation count descending. Pure Python,
    deterministic, no network I/O.
    """
    stripped = _LINK_URL_RE.sub("]", paper_source)

    counts = Counter(
        m.group(1).upper()
        for m in chain(_CITATION_PD_RE.finditer(stripped), _CITATION_N_RE.finditer(stripped))
    )

    refs = [CitationRef(paper_id=pid, count=c) for pid, c in counts.items()]
    refs.sort(key=lambda r: r.count, reverse=True)
    return refs
