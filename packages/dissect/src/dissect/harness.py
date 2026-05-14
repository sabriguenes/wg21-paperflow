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

_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did has have had "
    "will would shall should may might can could of in to for on with "
    "at by from as into through during before after above below between "
    "and or not no nor but if then else when where how what which who "
    "that this these those it its they their them he she his her we our "
    "my your".split()
)


def _content_words(text: str) -> set[str]:
    """Extract content words (nouns/verbs/adjectives) by dropping stopwords."""
    return {w for w in re.findall(r"[a-z][a-z_]+", text.lower()) if w not in _STOPWORDS}


def dedup_overlap_candidates(questions: list[str], min_overlap: int = 2) -> set[frozenset[int]]:
    """Return pairs of question indices that share enough content words.

    Only these pairs are eligible for LLM semantic grouping. Pairs
    below the threshold are never merged -- this prevents the LLM from
    grouping questions that share a topic but require different evidence.
    """
    word_sets = [_content_words(q) for q in questions]
    pairs: set[frozenset[int]] = set()
    for i in range(len(questions)):
        for j in range(i + 1, len(questions)):
            if len(word_sets[i] & word_sets[j]) >= min_overlap:
                pairs.add(frozenset((i, j)))
    return pairs


_TIER2_MIN_OVERLAP = 5


def _dedup_tier2_groups(
    keys: list[str],
    min_overlap: int = _TIER2_MIN_OVERLAP,
) -> list[list[int]]:
    """Connected components of indices sharing >= min_overlap content words."""
    pairs = dedup_overlap_candidates(keys, min_overlap=min_overlap)
    parent = list(range(len(keys)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        a, b = tuple(pair)
        parent[find(a)] = find(b)

    components: dict[int, list[int]] = {}
    for i in range(len(keys)):
        components.setdefault(find(i), []).append(i)
    return [g for g in components.values() if len(g) >= 2]


_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+(.*)")
_LINE_PREFIX_RE = re.compile(r"^\d+\|\s?")


def _section_for_line(lines: list[str], line_num: int) -> str:
    """Find the nearest heading at or above ``line_num`` (1-based)."""
    for i in range(min(line_num - 1, len(lines) - 1), -1, -1):
        m = _HEADING_LINE_RE.match(lines[i])
        if m:
            return m.group(1).strip()
    return ""


def _strip_line_prefix(text: str) -> str:
    """Remove leading line-number prefix (e.g. '47| ') if the LLM copied it."""
    return _LINE_PREFIX_RE.sub("", text)


def _number_lines(chunk: Chunk) -> str:
    """Prepend absolute line numbers to each line of a chunk."""
    lines = chunk.text.splitlines()
    return "\n".join(
        f"{chunk.line_offset + i}| {line}" for i, line in enumerate(lines)
    )


_FENCE_RE = re.compile(r"^```")
_WORDING_OPEN_RE = re.compile(r"^:{3,}wording")
_WORDING_CLOSE_RE = re.compile(r"^:{3,}\s*$")


def _blank_non_prose(source: str) -> tuple[str, int]:
    """Replace fenced code blocks and wording divs with empty lines.

    Preserves line count so SourceLoc line numbers still map to the
    original paper.md. Returns ``(blanked_source, blanked_line_count)``.
    """
    lines = source.splitlines(keepends=True)
    in_fence = False
    in_wording = False
    blanked = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if in_fence:
            lines[i] = "\n"
            blanked += 1
            if _FENCE_RE.match(stripped):
                in_fence = False
        elif in_wording:
            lines[i] = "\n"
            blanked += 1
            if _WORDING_CLOSE_RE.match(stripped):
                in_wording = False
        elif _FENCE_RE.match(stripped):
            in_fence = True
            lines[i] = "\n"
            blanked += 1
        elif _WORDING_OPEN_RE.match(stripped):
            in_wording = True
            lines[i] = "\n"
            blanked += 1
    return "".join(lines), blanked


def _chunk_paper(source: str, max_chars: int = 16_000) -> list[Chunk]:
    """Split paper into chunks of <= max_chars at markdown heading boundaries.

    Non-prose content (fenced code blocks, wording divs) is blanked
    before chunking to keep the LLM focused on prose argument.

    Adjacent chunks overlap by 30 lines: the next chunk starts 30 lines
    before the split point. Single-chunk papers return one Chunk with
    line_offset=1. The default keeps small models on focused slices
    instead of asking them to extract from an entire paper at once.
    """
    source, _ = _blank_non_prose(source)

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
            overlap_start = max(0, h - 30)
            chunk_start = overlap_start
            target = (cum_chars[overlap_start - 1] if overlap_start > 0 else 0) + max_chars

    if chunk_start < len(lines):
        chunk_text = "".join(lines[chunk_start:])
        chunks.append(Chunk(text=chunk_text, line_offset=chunk_start + 1))

    return chunks


def _promote_claims(
    raws: list[RawClaim], source: str, start_uid: int = 1,
    *, chunk_indices: list[int] | None = None,
) -> tuple[list[Claim], int]:
    """Convert RawClaims to Claims using start_line for location.

    Assigns sequential uids starting from start_uid. ``source`` is
    the full paper text (not the chunk), so section headings resolve
    correctly even when a claim's heading is in a previous chunk.
    Returns (claims, next_uid).
    """
    lines = source.splitlines()
    claims: list[Claim] = []
    text_to_uid: dict[str, int] = {}
    uid = start_uid

    for i, raw in enumerate(raws):
        line = raw.start_line if raw.start_line > 0 else 1
        line_text = lines[line - 1] if line <= len(lines) else ""
        text = _strip_line_prefix(raw.text)
        pos = line_text.find(text)
        if pos < 0:
            pos = 0
        loc = SourceLoc(line=line, start_char=pos, end_char=pos + len(text))
        text_to_uid[text] = uid
        claims.append(Claim(
            uid=uid,
            loc=loc,
            text=text,
            original_quotes=[text],
            section=_section_for_line(lines, line),
            question=raw.question,
            kind="normative",
            chunk_index=chunk_indices[i] if chunk_indices else 0,
            depends_on=[],
            merged_into=None,
        ))
        uid += 1

    return (claims, start_uid + len(raws))


def _promote_evidence(
    raws: list[RawEvidence], source: str, start_uid: int = 1,
    *, chunk_indices: list[int] | None = None,
) -> tuple[list[Evidence], int]:
    """Convert RawEvidence to Evidence using start_line for location.

    Assigns sequential uids starting from start_uid. Returns
    (evidence, next_uid).
    """
    lines = source.splitlines()
    evidence: list[Evidence] = []
    uid = start_uid

    for i, raw in enumerate(raws):
        line = raw.start_line if raw.start_line > 0 else 1
        line_text = lines[line - 1] if line <= len(lines) else ""
        text = _strip_line_prefix(raw.text)
        pos = line_text.find(text)
        if pos < 0:
            pos = 0
        loc = SourceLoc(line=line, start_char=pos, end_char=pos + len(text))
        evidence.append(Evidence(
            uid=uid,
            loc=loc,
            text=text,
            original_quotes=[text],
            section=_section_for_line(lines, line),
            supports=raw.supports,
            quantitative=raw.quantitative,
            cited=raw.cited,
            verifiable=raw.verifiable,
            normative=raw.normative,
            chunk_index=chunk_indices[i] if chunk_indices else 0,
            merged_into=None,
        ))
        uid += 1

    return (evidence, start_uid + len(raws))


def _promote_rhetoric(
    raws: list[RawRhetoric], source: str, start_uid: int = 1,
    *, chunk_indices: list[int] | None = None,
) -> tuple[list[Rhetoric], int]:
    """Convert RawRhetoric to Rhetoric using start_line for location.

    Assigns sequential uids starting from start_uid. Returns
    (items, next_uid).
    """
    lines = source.splitlines()
    items: list[Rhetoric] = []
    uid = start_uid

    for i, raw in enumerate(raws):
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
            chunk_index=chunk_indices[i] if chunk_indices else 0,
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


def _absorb_update(survivor: T, tombstoned: T) -> dict:
    """Build the model_copy update dict for absorbing tombstoned into survivor.

    Always merges original_quotes. For Evidence, also unions supports
    (order-preserving, dedup) and OR-merges quantitative/cited/
    verifiable/normative. Latent-bug guard: pre-removal, only the LLM
    Tier 2 path carried supports and flags. Tier 1 silently dropped
    them. With evidence Tier 2 removed, Tier 1 owns absorbing them.
    """
    merged_quotes = list(survivor.original_quotes) + list(tombstoned.original_quotes)
    update: dict = {"original_quotes": merged_quotes}
    if isinstance(survivor, Evidence) and isinstance(tombstoned, Evidence):
        all_supports = list(survivor.supports)
        for sup in tombstoned.supports:
            if sup not in all_supports:
                all_supports.append(sup)
        update["supports"] = all_supports
        update["quantitative"] = survivor.quantitative or tombstoned.quantitative
        update["cited"] = survivor.cited or tombstoned.cited
        update["verifiable"] = survivor.verifiable or tombstoned.verifiable
        update["normative"] = survivor.normative or tombstoned.normative
    return update


def _dedup_tier1(items: list[T]) -> list[T]:
    """Tier 1: tombstone substring matches, absorb metadata.

    For survivors of tier 0: when one item's text is a substring of
    another's, the shorter becomes a tombstone. The longer absorbs the
    shorter's original_quotes (always), plus supports and boolean flags
    when both items are Evidence. Returns a new list.
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
                result[idx_b] = cur_b.model_copy(update=_absorb_update(cur_b, a))
                result[idx_a] = a.model_copy(update={"merged_into": cur_b.uid})
                break
            elif b.text in a.text and a.text != b.text:
                cur_a = result[idx_a]
                result[idx_a] = cur_a.model_copy(update=_absorb_update(cur_a, b))
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

    # Sort by pid first so equal-count items tie-break alphabetically
    # rather than by regex iteration order. Final sort below is stable.
    refs = [CitationRef(paper_id=pid, count=c) for pid, c in sorted(counts.items())]
    refs.sort(key=lambda r: r.count, reverse=True)
    return refs
