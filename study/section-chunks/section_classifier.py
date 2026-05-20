#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Classify each section using zero-shot NLI on paragraph-aligned chunks.

Reads ``data/{pid}_sections.json`` (from section_chunker.py) and the
paper markdown from paperstore.  For each leaf section, splits the body
into paragraph-aligned chunks (min 64 tokens, max 512 tokens).  Small
paragraphs are coalesced; oversized paragraphs are split into equal
pieces.  Scores each chunk against 10 hypotheses.  The per-section
score is the max across all chunks for each hypothesis.

Two phases: chunking (fast, prints stats), then classification (GPU,
rich progress bar).

Usage:
    python section_classifier.py P2300R10
    python section_classifier.py P2300R10 --classifier zeroshot-base
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from pipeline.tokens import est_tokens

DATA_DIR = Path("c:/Users/Vinnie/wg21-data-dir/paperstore")
OUT_DIR = Path(__file__).parent / "data"

CHUNK_TOKENS = 256
MIN_CHUNK_TOKENS = 64

HYPOTHESES = [
    "This text states what a proposal aims to achieve or what properties it should have",
    "This text explains how an API, protocol, or language feature works, or discusses its design and behavior",
    "This text is written in the style of a C++ standard specification with Effects, Returns, and Mandates clauses",
    "This text walks through a code example to illustrate how something works",
    "This text describes real-world usage, adoption, or field testing of a software system",
    "This text contains numerical data from benchmarks, profiling, or timing experiments",
    "This text claims something is fast, efficient, zero-cost, low-overhead, or optimizable",
    "This text evaluates the strengths and weaknesses of alternative designs or prior proposals",
    "This text defers something to future work or another document",
    "This text describes a limitation, concession, or known issue",
]

HYPO_SHORT = [
    "design-goal",
    "design-rationale",
    "wording",
    "example",
    "evidence",
    "measurement",
    "perf-claim",
    "comparison",
    "deferral",
    "limitation",
]


import pysbd

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)




def _split_sentences(text: str, max_tokens: int) -> list[str]:
    """Split *text* at sentence boundaries into chunks under *max_tokens*.

    Sentences are accumulated until adding the next would exceed the
    limit.  If a single sentence exceeds *max_tokens*, it is split
    into roughly equal word-count pieces as a fallback.
    """
    sentences = _SEGMENTER.segment(text)
    chunks: list[str] = []
    buf: list[str] = []
    buf_wc = 0
    for sent in sentences:
        sw = est_tokens(sent)
        if sw == 0:
            continue
        if buf_wc > 0 and buf_wc + sw > max_tokens:
            chunks.append(" ".join(buf))
            buf = []
            buf_wc = 0
        if sw > max_tokens:
            if buf:
                chunks.append(" ".join(buf))
                buf = []
                buf_wc = 0
            words = sent.split()
            k = (sw + max_tokens - 1) // max_tokens
            ps = (sw + k - 1) // k
            for i in range(0, sw, ps):
                chunks.append(" ".join(words[i : i + ps]))
        else:
            buf.append(sent)
            buf_wc += sw
    if buf:
        chunks.append(" ".join(buf))
    return chunks if chunks else [""]


def _chunk_by_paragraph(text: str, max_tokens: int) -> list[str]:
    """Split *text* into paragraph-aligned chunks, min 64 / max tokens.

    1. Split on blank lines into raw paragraphs.
    2. Coalesce: accumulate adjacent small paragraphs until the next
       would exceed *max_tokens*.
    3. Split: oversized chunks are split at sentence boundaries.
    """
    raw: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            if current:
                raw.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        raw.append("\n".join(current))

    if not raw:
        return [""]

    # Coalesce small paragraphs
    coalesced: list[str] = []
    buf: list[str] = []
    buf_wc = 0
    for para in raw:
        pw = est_tokens(para)
        if pw == 0:
            continue
        if buf_wc > 0 and buf_wc + pw > max_tokens:
            coalesced.append("\n\n".join(buf))
            buf = []
            buf_wc = 0
        buf.append(para)
        buf_wc += pw
        if buf_wc >= MIN_CHUNK_TOKENS and pw >= MIN_CHUNK_TOKENS:
            coalesced.append("\n\n".join(buf))
            buf = []
            buf_wc = 0
    if buf:
        if coalesced and buf_wc < MIN_CHUNK_TOKENS:
            coalesced[-1] += "\n\n" + "\n\n".join(buf)
        else:
            coalesced.append("\n\n".join(buf))

    # Split oversized chunks at sentence boundaries
    chunks: list[str] = []
    for chunk in coalesced:
        if est_tokens(chunk) <= max_tokens:
            chunks.append(chunk)
        else:
            chunks.extend(_split_sentences(chunk, max_tokens))

    return chunks if chunks else [""]


def classify_sections(
    pid: str, classifier_name: str = "zeroshot-base",
) -> list[dict]:
    """Classify all sections of a paper. Returns score matrix (list of dicts).

    Reads ``data/{pid}_sections.json`` (must exist) and the paper
    markdown from paperstore. Writes ``data/{pid}_score_matrix.json``
    and returns the matrix.
    """
    pid = pid.upper()
    sections_path = OUT_DIR / f"{pid.lower()}_sections.json"
    if not sections_path.is_file():
        raise FileNotFoundError(f"missing: {sections_path}")
    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    md_path = DATA_DIR / f"{pid.lower()}.md"
    if not md_path.is_file():
        raise FileNotFoundError(f"not found: {md_path}")
    all_lines = md_path.read_text(encoding="utf-8").splitlines()

    all_chunks: list[str] = []
    chunk_map: list[tuple[int, int]] = []
    chunks_per_sec: dict[int, int] = {}
    for sec_idx, sec in enumerate(sections):
        start = sec["start_line"] - 1
        end = sec["end_line"]
        body = "\n".join(all_lines[start:end])
        chunks = _chunk_by_paragraph(body, CHUNK_TOKENS)
        chunks_per_sec[sec_idx] = len(chunks)
        for ci, chunk_text in enumerate(chunks):
            all_chunks.append(chunk_text)
            chunk_map.append((sec_idx, ci))

    from pipeline.services import load_classifiers, resolve_classifier_slots
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": classifier_name})
    classifier = slots["selector"]

    BATCH_SIZE = 64
    raw_scores: list[dict[str, float]] = []
    for b_start in range(0, len(all_chunks), BATCH_SIZE):
        batch = all_chunks[b_start : b_start + BATCH_SIZE]
        raw_scores.extend(classifier.classify(batch, HYPOTHESES, multi_label=True))

    sec_scores: dict[int, dict[str, float]] = {}
    for chunk_idx, (sec_idx, _ci) in enumerate(chunk_map):
        scores = raw_scores[chunk_idx]
        if sec_idx not in sec_scores:
            sec_scores[sec_idx] = {short: 0.0 for short in HYPO_SHORT}
        for short, hypo in zip(HYPO_SHORT, HYPOTHESES):
            s = scores[hypo]
            if s > sec_scores[sec_idx][short]:
                sec_scores[sec_idx][short] = s

    matrix: list[dict] = []
    for sec_idx, sec in enumerate(sections):
        scores = sec_scores.get(sec_idx, {short: 0.0 for short in HYPO_SHORT})
        matrix.append({
            "idx": sec_idx,
            "heading": sec["heading"],
            "start_line": sec["start_line"],
            "end_line": sec["end_line"],
            "token_est": sec["token_est"],
            "n_chunks": chunks_per_sec.get(sec_idx, 1),
            "scores": {k: round(v, 4) for k, v in scores.items()},
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{pid.lower()}_score_matrix.json"
    out_path.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    return matrix


def main() -> None:
    classifier_name = "zeroshot-base"

    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(
            "usage: section_classifier.py <paper_id> [--classifier NAME]",
            file=sys.stderr,
        )
        sys.exit(2)

    pid = args[0].upper()
    for i, a in enumerate(args):
        if a == "--classifier" and i + 1 < len(args):
            classifier_name = args[i + 1]

    sections_path = OUT_DIR / f"{pid.lower()}_sections.json"
    if not sections_path.is_file():
        print(
            f"missing: {sections_path} (run section_chunker.py first)",
            file=sys.stderr,
        )
        sys.exit(1)
    sections = json.loads(sections_path.read_text(encoding="utf-8"))

    md_path = DATA_DIR / f"{pid.lower()}.md"
    if not md_path.is_file():
        print(f"not found: {md_path}", file=sys.stderr)
        sys.exit(1)
    all_lines = md_path.read_text(encoding="utf-8").splitlines()

    # --- Phase 1: Chunk all sections ---
    all_chunks: list[str] = []
    chunk_map: list[tuple[int, int]] = []
    chunks_per_sec: dict[int, int] = {}
    for sec_idx, sec in enumerate(sections):
        start = sec["start_line"] - 1
        end = sec["end_line"]
        body = "\n".join(all_lines[start:end])
        chunks = _chunk_by_paragraph(body, CHUNK_TOKENS)
        chunks_per_sec[sec_idx] = len(chunks)
        for ci, chunk_text in enumerate(chunks):
            all_chunks.append(chunk_text)
            chunk_map.append((sec_idx, ci))

    total_chunks = len(all_chunks)
    chunk_sizes = [est_tokens(c) for c in all_chunks]
    single = sum(1 for v in chunks_per_sec.values() if v == 1)
    multi = sum(1 for v in chunks_per_sec.values() if v > 1)
    max_ch = max(chunks_per_sec.values()) if chunks_per_sec else 0

    print(f"Paper: {pid}", file=sys.stderr)
    print(f"Sections: {len(sections)}", file=sys.stderr)
    print(f"Paragraph chunks: {total_chunks} (single-sec: {single}, multi-sec: {multi}, max {max_ch}/sec)", file=sys.stderr)
    print(f"Chunk sizes: min={min(chunk_sizes)} med={sorted(chunk_sizes)[len(chunk_sizes)//2]} max={max(chunk_sizes)} tokens", file=sys.stderr)
    print(f"Hypotheses: {len(HYPOTHESES)}", file=sys.stderr)
    print(f"Classifier: {classifier_name}", file=sys.stderr)

    # --- Phase 2: Classify with progress ---
    from pipeline.services import load_classifiers, resolve_classifier_slots

    print(f"Loading classifier '{classifier_name}'...", file=sys.stderr)
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": classifier_name})
    classifier = slots["selector"]

    BATCH_SIZE = 64
    print(f"Classifying {total_chunks} chunks x {len(HYPOTHESES)} hypotheses ({(total_chunks + BATCH_SIZE - 1)//BATCH_SIZE} batches)...", file=sys.stderr)
    raw_scores: list[dict[str, float]] = []
    t0 = time.time()
    for b_start in range(0, total_chunks, BATCH_SIZE):
        batch = all_chunks[b_start : b_start + BATCH_SIZE]
        batch_scores = classifier.classify(batch, HYPOTHESES, multi_label=True)
        raw_scores.extend(batch_scores)
        done = min(b_start + BATCH_SIZE, total_chunks)
        elapsed_so_far = time.time() - t0
        print(f"  {done}/{total_chunks} chunks ({elapsed_so_far:.1f}s)", file=sys.stderr)
    elapsed = time.time() - t0

    print(f"Done in {elapsed:.1f}s ({elapsed / total_chunks * 1000:.0f}ms/chunk)", file=sys.stderr)

    # --- Aggregate: per-section max score across all chunks ---
    sec_scores: dict[int, dict[str, float]] = {}
    for chunk_idx, (sec_idx, ci) in enumerate(chunk_map):
        scores = raw_scores[chunk_idx]
        if sec_idx not in sec_scores:
            sec_scores[sec_idx] = {short: 0.0 for short in HYPO_SHORT}
        for short, hypo in zip(HYPO_SHORT, HYPOTHESES):
            s = scores[hypo]
            if s > sec_scores[sec_idx][short]:
                sec_scores[sec_idx][short] = s

    matrix: list[dict] = []
    for sec_idx, sec in enumerate(sections):
        scores = sec_scores.get(sec_idx, {short: 0.0 for short in HYPO_SHORT})
        row = {
            "idx": sec_idx,
            "heading": sec["heading"],
            "start_line": sec["start_line"],
            "end_line": sec["end_line"],
            "token_est": sec["token_est"],
            "n_chunks": chunks_per_sec.get(sec_idx, 1),
            "scores": {k: round(v, 4) for k, v in scores.items()},
        }
        matrix.append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{pid.lower()}_score_matrix.json"
    out_path.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}", file=sys.stderr)

    # --- Report ---
    print(f"\n# {pid} - Section Classification Report\n")
    print(f"- Classifier: {classifier_name}")
    print(f"- Sections: {len(sections)}")
    print(f"- Paragraph chunks: {total_chunks}")
    print(f"- Chunk sizes: min={min(chunk_sizes)} med={sorted(chunk_sizes)[len(chunk_sizes)//2]} max={max(chunk_sizes)}")
    print(f"- Hypotheses: {len(HYPOTHESES)}")
    print(f"- Time: {elapsed:.1f}s ({elapsed / total_chunks * 1000:.0f}ms/chunk)")
    print()

    print("## Per-Section Top Hypotheses (max across chunks)\n")
    for row in matrix:
        top = sorted(row["scores"].items(), key=lambda x: -x[1])
        top3 = [(k, v) for k, v in top[:3] if v > 0.3]
        tags = ", ".join(f"{k}={v:.2f}" for k, v in top3) if top3 else "(none above 0.3)"
        nc = f"[{row['n_chunks']}ch]" if row["n_chunks"] > 1 else ""
        print(f"  {row['heading'][:55]:<55s} {nc:>5s} [{tags}]")

    print("\n## Hypothesis Coverage\n")
    threshold = 0.5
    print(f"(sections with max-chunk score > {threshold})\n")
    for short in HYPO_SHORT:
        hits = [r for r in matrix if r["scores"].get(short, 0) > threshold]
        hit_list = ", ".join(str(r["idx"]) for r in hits[:10])
        more = f" (+{len(hits) - 10} more)" if len(hits) > 10 else ""
        print(f"  {short:<20s}: {len(hits):3d} sections  [{hit_list}{more}]")


if __name__ == "__main__":
    main()
