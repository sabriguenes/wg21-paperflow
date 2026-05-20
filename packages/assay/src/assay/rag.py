#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Ephemeral RAG index over cited papers.

Builds an in-memory vector index from paperstore markdown of cited papers.
Downstream pipeline steps query it for evidence injection into LLM prompts.
No LLM calls. Network I/O limited to EmbeddingBackend model loading (HF cache).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np

from pipeline.transformer_backend import EmbeddingBackend

from assay.models import BreadcrumbOutput, FindingOutput, ReferenceEntry
from assay.references import RefEntry

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
_CHARS_PER_TOKEN = 4

_RELATIONSHIP_ADJUSTMENTS: dict[str, float] = {
    "companion": 0.0,
    "dependency": -0.02,
    "citation": -0.05,
    "background": -0.1,
}


@dataclass
class RagChunk:
    paper_id: str
    text: str
    heading: str
    start_line: int
    end_line: int
    relationship: str


@dataclass
class RagHit:
    paper_id: str
    text: str
    heading: str
    start_line: int
    end_line: int
    relationship: str
    score: float


@dataclass
class CitedPaperIndex:
    chunks: list[RagChunk]
    embeddings: np.ndarray  # (N, dim), pre-normalized by model


@dataclass
class IndexStats:
    papers_indexed: int
    total_chunks: int
    embedding_dim: int
    embed_time_ms: float
    per_paper: list[tuple[str, str, int]]  # (pid, relationship, chunk_count)
    skipped: list[str]


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _chunk_markdown(
    source: str,
    paper_id: str,
    relationship: str,
    *,
    max_tokens: int = 400,
) -> list[RagChunk]:
    """Split markdown into heading-aware chunks within token budget.

    Strategy: split on ## / ### headings as natural boundaries. If a
    section exceeds max_tokens, split on paragraph boundaries. No
    mechanical overlap - headings are prepended to sub-chunks for context.
    """
    lines = source.splitlines()
    max_chars = max_tokens * _CHARS_PER_TOKEN

    sections: list[tuple[str, int, int]] = []
    heading_positions: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            heading_positions.append((i, m.group(2).strip()))

    if not heading_positions:
        heading_positions = [(0, "(untitled)")]

    for idx, (line_idx, heading) in enumerate(heading_positions):
        start = line_idx
        end = heading_positions[idx + 1][0] if idx + 1 < len(heading_positions) else len(lines)
        section_text = "\n".join(lines[start:end])
        sections.append((heading, start, end))

    chunks: list[RagChunk] = []

    for heading, start, end in sections:
        section_text = "\n".join(lines[start:end]).strip()
        if not section_text:
            continue

        if _estimate_tokens(section_text) <= max_tokens:
            chunks.append(RagChunk(
                paper_id=paper_id,
                text=section_text,
                heading=heading,
                start_line=start + 1,
                end_line=end,
                relationship=relationship,
            ))
        else:
            body_start = start + 1 if start < end else start
            body_text = "\n".join(lines[body_start:end]).strip()
            paragraphs = re.split(r"\n\n+", body_text)

            current_parts: list[str] = []
            current_chars = 0
            chunk_start_line = body_start + 1

            for para in paragraphs:
                para_chars = len(para)
                if current_chars + para_chars > max_chars and current_parts:
                    chunk_text = f"## {heading}\n\n" + "\n\n".join(current_parts)
                    para_lines = chunk_text.count("\n") + 1
                    chunks.append(RagChunk(
                        paper_id=paper_id,
                        text=chunk_text,
                        heading=heading,
                        start_line=chunk_start_line,
                        end_line=chunk_start_line + para_lines - 1,
                        relationship=relationship,
                    ))
                    chunk_start_line += sum(p.count("\n") + 2 for p in current_parts)
                    current_parts = []
                    current_chars = 0

                current_parts.append(para)
                current_chars += para_chars

            if current_parts:
                chunk_text = f"## {heading}\n\n" + "\n\n".join(current_parts)
                para_lines = chunk_text.count("\n") + 1
                chunks.append(RagChunk(
                    paper_id=paper_id,
                    text=chunk_text,
                    heading=heading,
                    start_line=chunk_start_line,
                    end_line=end,
                    relationship=relationship,
                ))

    return chunks


def build_cited_paper_index(
    reference_inventory: list[RefEntry],
    reference_registry: list[ReferenceEntry] | None,
    backend: Any,
    embedder: EmbeddingBackend,
    *,
    paper_id: str = "",
    max_tokens: int = 400,
) -> CitedPaperIndex | None:
    """Build ephemeral vector index over cited papers found in paperstore.

    Indexes all cited papers except the paper under analysis itself.
    Self-cites (same author) are included - they are often companion papers.
    """
    registry_map: dict[str, str] = {}
    if reference_registry:
        for entry in reference_registry:
            label = (entry.ref_label or "").upper()
            if label and entry.relationship:
                registry_map[label] = entry.relationship

    all_chunks: list[RagChunk] = []
    for ref in reference_inventory:
        if not ref.in_paperstore:
            continue
        if ref.paper_id.upper() == paper_id.upper():
            continue

        md = backend.try_read_paper_md(ref.paper_id)
        if not md:
            continue

        relationship = registry_map.get(ref.paper_id.upper(), "citation")
        chunks = _chunk_markdown(md, ref.paper_id, relationship, max_tokens=max_tokens)
        all_chunks.extend(chunks)

    if not all_chunks:
        return None

    texts = [c.text for c in all_chunks]
    raw_embeddings = embedder.embed(texts)
    embeddings = raw_embeddings.float().cpu().numpy()

    return CitedPaperIndex(chunks=all_chunks, embeddings=embeddings)


def query_index(
    index: CitedPaperIndex,
    query: str,
    embedder: EmbeddingBackend,
    *,
    top_k: int = 5,
    max_per_paper: int = 3,
    min_score: float = 0.3,
) -> list[RagHit]:
    """Query the index for chunks similar to query text."""
    if not index.chunks:
        return []

    raw_q = embedder.embed([query])
    q_vec = raw_q.float().cpu().numpy().squeeze(0)

    scores = index.embeddings @ q_vec

    above_threshold = [(i, float(scores[i])) for i in range(len(scores)) if scores[i] >= min_score]
    above_threshold.sort(key=lambda x: x[1], reverse=True)

    # Apply relationship adjustments for ranking (post-threshold)
    ranked: list[tuple[int, float]] = []
    for idx, raw_score in above_threshold:
        chunk = index.chunks[idx]
        adj = _RELATIONSHIP_ADJUSTMENTS.get(chunk.relationship, -0.05)
        ranked.append((idx, raw_score + adj))

    ranked.sort(key=lambda x: x[1], reverse=True)

    # Per-paper diversity cap
    paper_counts: Counter[str] = Counter()
    hits: list[RagHit] = []
    for idx, score in ranked:
        chunk = index.chunks[idx]
        if paper_counts[chunk.paper_id] >= max_per_paper:
            continue
        paper_counts[chunk.paper_id] += 1
        hits.append(RagHit(
            paper_id=chunk.paper_id,
            text=chunk.text,
            heading=chunk.heading,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            relationship=chunk.relationship,
            score=score,
        ))
        if len(hits) >= top_k:
            break

    return hits


def format_evidence(hits: list[RagHit], *, max_chars: int = 3000) -> str:
    """Format hits as markdown for prompt injection."""
    if not hits:
        return ""

    parts: list[str] = ["## Evidence from cited papers\n"]
    total_chars = len(parts[0])

    for hit in hits:
        header = f"\n### {hit.paper_id} ({hit.relationship}) - {hit.heading}\n\n"
        text_lines = hit.text.split("\n")
        preview = "\n".join(text_lines[:12])
        if len(text_lines) > 12:
            preview += "\n..."
        block = f"{header}> {preview.replace(chr(10), chr(10) + '> ')}\n\n(lines {hit.start_line}-{hit.end_line})\n"

        if total_chars + len(block) > max_chars:
            break
        parts.append(block)
        total_chars += len(block)

    return "".join(parts)


def query_for_research(
    index: CitedPaperIndex,
    embedder: EmbeddingBackend,
    lens: str,
    breadcrumbs: list[BreadcrumbOutput],
    thesis: str,
) -> str:
    """Query index with breadcrumbs for a given lens. Returns formatted evidence."""
    if not breadcrumbs:
        return ""

    lens_bcs = [b for b in breadcrumbs if (b.primary_lens or "") == lens]
    if not lens_bcs:
        lens_bcs = breadcrumbs[:3]

    all_hits: list[RagHit] = []
    seen_chunks: set[tuple[str, int]] = set()

    for bc in lens_bcs:
        query_text = f"{bc.gap} {thesis}"
        hits = query_index(index, query_text, embedder, top_k=3, max_per_paper=2)
        for h in hits:
            key = (h.paper_id, h.start_line)
            if key not in seen_chunks:
                seen_chunks.add(key)
                all_hits.append(h)

    all_hits.sort(key=lambda h: h.score, reverse=True)
    return format_evidence(all_hits[:5], max_chars=3000)


def query_for_challenge(
    index: CitedPaperIndex,
    embedder: EmbeddingBackend,
    findings: list[FindingOutput],
    *,
    min_score: float = 0.3,
    max_chars_per_finding: int = 600,
    max_chars_total: int = 4000,
) -> dict[str, str]:
    """Query index per finding. Returns {finding.title: formatted evidence}."""
    result: dict[str, str] = {}
    total_chars = 0

    for f in findings:
        if total_chars >= max_chars_total:
            break

        query_text = f.explanation or f.title or ""
        if not query_text:
            continue

        hits = query_index(index, query_text, embedder, top_k=2, max_per_paper=2, min_score=min_score)
        if not hits:
            continue

        evidence = format_evidence(hits, max_chars=max_chars_per_finding)
        if evidence:
            result[f.title] = evidence
            total_chars += len(evidence)

    return result
