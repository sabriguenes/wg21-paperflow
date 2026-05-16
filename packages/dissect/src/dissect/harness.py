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
    SentenceSpan,
    SentenceTag,
    SourceLoc,
    TaggedSentence,
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
# Strip leading Step 1 classifier tags if the LLM accidentally copies
# them into the emitted ``text`` field. Tag glyphs are stable strings
# (``[TARGET]`` / ``[CONTEXT]``) chosen so the LLM recognizes them as
# metadata, but defense-in-depth: if a tag bleeds into ``raw.text``,
# strip it before SourceLoc computation so ``line_text.find(text)``
# still locates the verbatim source span. The prompt preface in Step 2
# instructs the LLM not to include tags in ``text``; this regex is the
# safety net.
_TAG_PREFIX_RE = re.compile(r"^\[(?:TARGET|CONTEXT)\]\s+")

# Structural-SKIP patterns. These match sentences that pysbd
# fragments off as standalone "sentences" but which carry no
# classifiable signal -- list markers, ellipsis-continuations, and
# all-punctuation/digit shrapnel. Cross-validated on P2300R10
# (study/ensemble): catches 33/34 broken fragments the classifier
# scored at target ~= skip ~= 0.99 (i.e. unclassifiable noise) with 1
# defensible false-positive across 547 labeled sentences.
_STRUCTURAL_NUMBER_ONLY_RE = re.compile(r"^\s*\d+\.\s*$")
_STRUCTURAL_ELLIPSIS_PREFIX_RE = re.compile(r"^\s*\.{2,}\s")
_STRUCTURAL_PUNCT_ONLY_RE = re.compile(r"^[\W\d]+$", re.UNICODE)
_STRUCTURAL_EXAMPLE_BLOCK_RE = re.compile(
    r"^\[\*Example\b.*\*end example\*\]", re.DOTALL,
)
_STRUCTURAL_MIN_WORDS = 3


def _is_structural_skip(text: str) -> bool:
    """Return True for sentences that are deterministically SKIP-shaped.

    These are pysbd fragmentation artefacts (numbered-list markers,
    ellipsis continuations) and zero-information shrapnel
    (punctuation-only, very short). The classifier produces useless
    scores on them (target ~= skip ~= 0.99), so we short-circuit them
    to SKIP before reaching the model. Deterministic, regex-only;
    handle this with care -- false positives become irrecoverable
    TARGET losses.
    """
    t = text.strip()
    if not t:
        return True
    if _STRUCTURAL_NUMBER_ONLY_RE.match(t):
        return True
    if _STRUCTURAL_ELLIPSIS_PREFIX_RE.match(t):
        return True
    if _STRUCTURAL_PUNCT_ONLY_RE.match(t):
        return True
    if len(t.split()) < _STRUCTURAL_MIN_WORDS:
        return True
    if _STRUCTURAL_EXAMPLE_BLOCK_RE.match(t):
        return True
    return False


def _section_for_line(lines: list[str], line_num: int) -> str:
    """Find the nearest heading at or above ``line_num`` (1-based)."""
    for i in range(min(line_num - 1, len(lines) - 1), -1, -1):
        m = _HEADING_LINE_RE.match(lines[i])
        if m:
            return m.group(1).strip()
    return ""


def _strip_line_prefix(text: str) -> str:
    """Remove leading line-number prefix and any leaked Step 1 tag.

    Strips a leading ``N| `` (line-number prefix the LLM may copy from
    the chunk render) and any leading ``[TARGET] `` / ``[CONTEXT] ``
    classifier tag (Step 1 metadata the LLM is told not to include in
    ``text`` but may bleed through). Both strips are anchored at the
    start of the string. Called from ``_promote_claims`` /
    ``_promote_evidence`` / ``_promote_rhetoric`` before
    ``line_text.find(text)`` so SourceLoc computation always sees a
    clean span.
    """
    text = _LINE_PREFIX_RE.sub("", text)
    text = _TAG_PREFIX_RE.sub("", text)
    return text


def _number_lines(chunk: Chunk) -> str:
    """Prepend absolute line numbers to each line of a chunk.

    Runs of 2+ consecutive blank lines collapse to a single sentinel
    carrying the last line number of the run. This reduces token count
    on papers with large whitespace gaps (e.g. after blanked code
    blocks) without losing line-number fidelity: the sentinel's line
    number lets downstream SourceLoc computation stay accurate.
    """
    lines = chunk.text.splitlines()
    out: list[str] = []
    blank_run = 0
    last_blank_num = 0
    for i, line in enumerate(lines):
        line_num = chunk.line_offset + i
        if not line.strip():
            blank_run += 1
            last_blank_num = line_num
            continue
        if blank_run > 0:
            out.append(f"{last_blank_num}|")
            blank_run = 0
        out.append(f"{line_num}| {line}")
    if blank_run > 0:
        out.append(f"{last_blank_num}|")
    return "\n".join(out)


_FENCE_RE = re.compile(r"^```")
_WORDING_OPEN_RE = re.compile(r"^:{3,}wording")
_WORDING_CLOSE_RE = re.compile(r"^:{3,}\s*$")
_YAML_FENCE_RE = re.compile(r"^---\s*$")


def _blank_yaml_frontmatter(lines: list[str]) -> int:
    """Blank a leading YAML frontmatter block (delimited by ``---``).

    Mutates ``lines`` in place: replaces each frontmatter line
    (including both fence delimiters) with ``"\\n"``. Returns the
    number of lines blanked.

    Only acts when the first non-empty line is exactly ``---``. If
    there is no matching closing ``---``, returns 0 without
    modification (defensive: don't accidentally blank the whole paper
    when the source happens to start with a thematic break).
    """
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "":
            continue
        if _YAML_FENCE_RE.match(line.lstrip()):
            start = i
        break
    if start is None:
        return 0

    end: int | None = None
    for j in range(start + 1, len(lines)):
        if _YAML_FENCE_RE.match(lines[j].lstrip()):
            end = j
            break
    if end is None:
        return 0

    for k in range(start, end + 1):
        lines[k] = "\n"
    return end - start + 1


def _blank_non_prose(source: str) -> tuple[str, int]:
    """Replace YAML frontmatter, fenced code blocks, and wording divs
    with empty lines.

    Preserves line count so SourceLoc line numbers still map to the
    original paper.md. Returns ``(blanked_source, blanked_line_count)``.
    """
    lines = source.splitlines(keepends=True)
    blanked = _blank_yaml_frontmatter(lines)
    in_fence = False
    in_wording = False
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
    """Split paper into approximately even chunks at heading boundaries.

    Two-pass algorithm:

    1. Blank non-prose (fenced code blocks, wording divs) to keep the
       LLM focused on prose argument.
    2. Compute the minimum chunk count ``N = ceil(total / max_chars)``
       and the per-chunk target ``total / N``. Place ``N-1`` split
       points at the headings whose cumulative-character offset is
       closest to ``k * target`` for ``k = 1..N-1``. This produces
       chunks of roughly equal size instead of one large + one tail.

    Adjacent chunks overlap by ``OVERLAP_NONBLANK`` *nonblank* lines:
    each subsequent chunk starts far enough before its split point to
    include that many nonblank lines from the previous section. The
    intent is to preserve the last paragraph or two of the prior
    section so chain-of-reasoning sentences ("Therefore...", "This
    motivates...") retain their antecedent in the later chunk. We
    count nonblank lines rather than raw lines because the input
    markdown is unwrapped (one paragraph per line) and frequently
    abuts blanked-out code fences before a heading; raw-line counts
    would land inside dead regions instead of grabbing prose.
    Single-chunk papers (total <= max_chars) return one Chunk with
    line_offset=1.

    Greedy fallback: if the paper has fewer than ``N-1`` headings, the
    function packs as many balanced splits as headings allow and lets
    the final chunk absorb any remainder. A degenerate paper with no
    headings degrades to one chunk (the full source) even when total
    exceeds max_chars; small models will see more text than intended
    but the alternative -- splitting mid-prose -- breaks coreference
    silently.
    """
    source, _ = _blank_non_prose(source)
    total = len(source)
    if total <= max_chars:
        return [Chunk(text=source, line_offset=1)]

    lines = source.splitlines(keepends=True)
    cum_chars: list[int] = []
    running = 0
    for line in lines:
        running += len(line)
        cum_chars.append(running)

    heading_lines: list[int] = [
        i for i, line in enumerate(lines) if _HEADING_RE.match(line)
    ]
    if not heading_lines:
        return [Chunk(text=source, line_offset=1)]

    # Pass 1: decide how many chunks we want.
    import math
    n_chunks = max(2, math.ceil(total / max_chars))
    target_size = total / n_chunks

    # Pass 2: place N-1 split points at headings nearest to k*target.
    def _chars_before(line_idx: int) -> int:
        return cum_chars[line_idx - 1] if line_idx > 0 else 0

    split_points: list[int] = []  # line indices where a new chunk starts
    used_headings: set[int] = set()
    for k in range(1, n_chunks):
        ideal = k * target_size
        # Pick the unused heading whose pre-heading char offset is
        # closest to ``ideal`` and stays after the previous split.
        best: int | None = None
        best_dist: float = float("inf")
        for h in heading_lines:
            if h in used_headings:
                continue
            if split_points and h <= split_points[-1]:
                continue
            dist = abs(_chars_before(h) - ideal)
            if dist < best_dist:
                best_dist = dist
                best = h
        if best is None:
            break
        used_headings.add(best)
        split_points.append(best)

    if not split_points:
        return [Chunk(text=source, line_offset=1)]

    # Emit chunks with a small nonblank-line backward overlap on every
    # chunk after the first. The overlap region is shared between
    # adjacent chunks. We count nonblank lines because input markdown
    # is unwrapped (one paragraph == one line) and blanked code fences
    # often sit immediately before headings; a fixed raw-line overlap
    # would frequently land inside a dead region.
    OVERLAP_NONBLANK = 3
    chunks: list[Chunk] = []
    chunk_start = 0
    for split in split_points:
        chunk_text = "".join(lines[chunk_start:split])
        if chunk_text:
            chunks.append(Chunk(text=chunk_text, line_offset=chunk_start + 1))
        # Walk backward from the split point, counting nonblank lines
        # until we have OVERLAP_NONBLANK of them.
        start = split
        seen = 0
        while start > 0 and seen < OVERLAP_NONBLANK:
            start -= 1
            if lines[start].strip():
                seen += 1
        chunk_start = start

    tail = "".join(lines[chunk_start:])
    if tail:
        chunks.append(Chunk(text=tail, line_offset=chunk_start + 1))

    return chunks


# -- Step 1: Tag Sentences ---------------------------------------------------
#
# Sentence boundary disambiguation. Used by ``_custom_tag_sentences`` in
# pipeline.py: it decomposes each chunk into sentences, classifies each
# one, and writes the result to ``state.tagged_sentences`` for the
# renumbered Step 2 (Extract Claims). ``_decompose_sentences`` and
# ``_split_tagged_by_chunk`` are deterministic (rule-based) and do not
# touch the classifier; ``_tag_sentences`` and ``_render_tagged_chunk``
# (defined below) are the classifier-facing halves.

_SBD_SEGMENTER = None  # lazy-loaded pysbd segmenter, cached per process


def _get_sbd() -> object:
    """Return the cached pysbd Segmenter, loading on first call."""
    global _SBD_SEGMENTER
    if _SBD_SEGMENTER is None:
        import pysbd
        _SBD_SEGMENTER = pysbd.Segmenter(language="en", clean=False, char_span=True)
    return _SBD_SEGMENTER


def _decompose_sentences(chunk: Chunk) -> list[SentenceSpan]:
    """Decompose ``chunk`` into per-sentence ``SentenceSpan`` objects.

    Iterates the chunk line by line. Blank lines and markdown headings
    emit no spans (they are not prose to classify). Each remaining line
    is segmented by pysbd; the segmenter handles abbreviations, decimals,
    citations, and inline code conservatively enough for WG21 prose.
    Bullet markers (`- `, `* `, `1. `, etc.) are preserved as part of
    the sentence text rather than stripped, so the classifier sees the
    list item verbatim.

    ``line`` in the returned spans is the 1-based absolute source line
    in the original paper (``chunk.line_offset`` + offset within the
    chunk). ``start_char`` / ``end_char`` are 0-based offsets within
    that line. Decomposition is purely textual; the LLM-facing render is
    done later by ``_render_tagged_chunk``.
    """
    seg = _get_sbd()
    spans: list[SentenceSpan] = []
    lines = chunk.text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if _HEADING_RE.match(line):
            continue
        line_num = chunk.line_offset + i
        try:
            results = seg.segment(line)  # type: ignore[attr-defined]
        except Exception:
            # Defensive: if pysbd hiccups on weird input, treat the
            # whole line as one sentence rather than dropping it.
            results = [type("R", (), {"sent": line, "start": 0, "end": len(line)})()]
        for r in results:
            sent_text = getattr(r, "sent", None)
            if sent_text is None:
                continue
            sent_text = sent_text.strip()
            if not sent_text:
                continue
            start = getattr(r, "start", 0)
            end = getattr(r, "end", start + len(sent_text))
            spans.append(SentenceSpan(
                text=sent_text,
                line=line_num,
                start_char=start,
                end_char=end,
            ))
    return spans


# ---- Classifier-facing helpers -------------------------------------------
#
# Hypothesis-label wording matters for zero-shot models trained on
# synthetic NLI pairs (Laurer et al.). Two design rules emerge from
# experimenting against real WG21 prose with DeBERTa-v3-base:
#
# 1. **Short, declarative hypotheses.** Multi-clause hypotheses
#    ("argues X, asserts Y, or states Z") confuse the model and produce
#    uniformly low probabilities. Single-predicate hypotheses score
#    much more decisively.
# 2. **Compare margin, not absolute threshold.** With multi_label=True,
#    DeBERTa-v3-base on out-of-domain technical text routinely produces
#    independent probabilities in the 0.05-0.4 range -- both labels
#    score low. An absolute threshold rejects everything; a
#    *margin*-based comparison (TARGET if t-s > margin, SKIP if s-t >
#    margin, CONTEXT otherwise) recovers the model's actual signal
#    even when absolute confidence is low.

# Cross-validated on three corpora (P4003R3, P2300R10 Phase 1 prose,
# P2300R10 Phase 2 formal wording); see ``study/ensemble``. The
# previous wording ("A statement of fact or opinion.") matched
# free-form opinion prose well but bombed on formal-wording prose
# (43-50% TARGET recall on P2300R10). The phrasing below lifts TARGET
# recall to 96-98% across all three corpora with zero T->S misses,
# because "describes what something does, is, or proposes" matches
# the dominant claim shapes in WG21 papers: definitions, behavioural
# claims, and proposals.
_TAG_TARGET_LABEL = "A statement describing what something does, is, or proposes."
_TAG_SKIP_LABEL = "A heading, list marker, or page metadata."

# Asymmetric margins reflecting the recall-priority regime: TARGET on
# small margins (any signal that this is a claim survives), SKIP only
# when the model is confident (large margin). The remainder lands in
# CONTEXT, kept in the LLM input for coreference. Rationale: dissect's
# downstream Advocatus + Dei agents filter false positives. False
# negatives (missing claims) are the real harm, so the default biases
# heavily against discarding sentences.
_DEFAULT_TARGET_MARGIN = 0.05
_DEFAULT_SKIP_MARGIN = 0.40


def _tag_sentences(
    spans: list[SentenceSpan],
    classifier: object,
    *,
    target_margin: float = _DEFAULT_TARGET_MARGIN,
    skip_margin: float = _DEFAULT_SKIP_MARGIN,
) -> list[TaggedSentence]:
    """Tag ``spans`` via the resolved ``ClassifierBackend``.

    One batched ``classifier.classify(texts, [target, skip],
    multi_label=True)`` call scores both labels for every sentence. Per
    sentence:

    - if ``P(target) - P(skip) > target_margin``: ``TARGET``
    - elif ``P(skip) - P(target) > skip_margin``: ``SKIP``
    - else: ``CONTEXT`` (fallback; protects recall)

    Margins are asymmetric on purpose: TARGET fires on any positive
    signal (~5 pp), SKIP requires a strong skip-vs-target lead (~40
    pp). This biases toward TARGET / CONTEXT and only drops a sentence
    when the model is confident it is boilerplate. The downstream LLM
    can still skip it; the irreversible loss is dropping a real claim
    before the LLM sees it.

    ``multi_label=True`` is required: target and skip are not mutually
    exclusive (a sentence can be weak on both, defaulting to context),
    so softmax-across-labels (``multi_label=False``) would fabricate a
    winner from noise. Empty input short-circuits to an empty list with
    no classifier call.

    Structural pre-filter: spans matching ``_is_structural_skip`` are
    tagged SKIP directly with sentinel scores ``target=0.0,
    skip=1.0`` and excluded from the batched classifier call. This
    saves compute proportional to the structural-junk rate (~17% on
    P2300R10) and avoids a known failure mode where the classifier
    scores fragmented pysbd output at target ~= skip ~= 0.99.
    """
    if not spans:
        return []

    structural_skips: dict[int, TaggedSentence] = {}
    classify_indices: list[int] = []
    for i, span in enumerate(spans):
        if _is_structural_skip(span.text):
            structural_skips[i] = TaggedSentence(
                span=span, tag=SentenceTag.SKIP,
                target_score=0.0, skip_score=1.0,
            )
        else:
            classify_indices.append(i)

    raw_by_idx: dict[int, dict[str, float]] = {}
    if classify_indices:
        texts = [spans[i].text for i in classify_indices]
        raw = classifier.classify(  # type: ignore[attr-defined]
            texts,
            [_TAG_TARGET_LABEL, _TAG_SKIP_LABEL],
            multi_label=True,
        )
        raw_by_idx = dict(zip(classify_indices, raw))

    tagged: list[TaggedSentence] = []
    for i, span in enumerate(spans):
        if i in structural_skips:
            tagged.append(structural_skips[i])
            continue
        scores = raw_by_idx[i]
        t = float(scores.get(_TAG_TARGET_LABEL, 0.0))
        s = float(scores.get(_TAG_SKIP_LABEL, 0.0))
        diff = t - s
        if diff > target_margin:
            tag = SentenceTag.TARGET
        elif -diff > skip_margin:
            tag = SentenceTag.SKIP
        else:
            tag = SentenceTag.CONTEXT
        tagged.append(TaggedSentence(
            span=span, tag=tag, target_score=t, skip_score=s,
        ))
    return tagged


def _render_tagged_chunk(
    chunk: Chunk,
    tagged: list[TaggedSentence],
    *,
    drop_skip: bool = True,
) -> str:
    """Render ``chunk`` for the Step 2 (Extract Claims) LLM, with each
    sentence prefixed by its tag.

    Output shape (per line in the chunk's absolute line range):

    - ``<N>| [TARGET] <sentence text>``  -- sentence tagged TARGET
    - ``<N>| [CONTEXT] <sentence text>`` -- sentence tagged CONTEXT
    - omitted entirely                     -- sentence tagged SKIP and
      ``drop_skip=True``
    - ``<N>| <line text>``                 -- non-sentence line (heading
      or blank, no tag applies)

    A single source line can carry multiple sentences with mixed tags;
    they are emitted in document order separated by spaces, each with
    its own tag prefix. Blank-line collapsing from ``_number_lines`` is
    preserved for runs of blanks. Line numbers are preserved so
    ``_promote_claims`` can map LLM-emitted ``start_line`` values back
    to ``SourceLoc``.
    """
    tagged_by_line: dict[int, list[TaggedSentence]] = {}
    for ts in tagged:
        tagged_by_line.setdefault(ts.span.line, []).append(ts)

    lines = chunk.text.splitlines()
    out: list[str] = []
    blank_run = 0
    last_blank_num = 0

    for i, line in enumerate(lines):
        line_num = chunk.line_offset + i
        if not line.strip():
            blank_run += 1
            last_blank_num = line_num
            continue
        if blank_run > 0:
            out.append(f"{last_blank_num}|")
            blank_run = 0

        line_tags = tagged_by_line.get(line_num)
        if not line_tags:
            # Non-prose (heading) or unclassified line: pass through.
            out.append(f"{line_num}| {line}")
            continue

        # Stable ordering: by start_char within the line.
        line_tags = sorted(line_tags, key=lambda t: t.span.start_char)
        pieces: list[str] = []
        for ts in line_tags:
            if ts.tag == SentenceTag.SKIP and drop_skip:
                continue
            prefix = "[TARGET]" if ts.tag == SentenceTag.TARGET else "[CONTEXT]"
            pieces.append(f"{prefix} {ts.span.text}")
        if not pieces:
            # Whole line was SKIP. Drop it entirely from the LLM input.
            continue
        out.append(f"{line_num}| " + " ".join(pieces))

    if blank_run > 0:
        out.append(f"{last_blank_num}|")
    return "\n".join(out)


def _split_tagged_by_chunk(
    chunks: list[Chunk],
    tagged: list[TaggedSentence],
) -> list[list[TaggedSentence]]:
    """Partition a flat ``tagged`` list by chunk membership.

    Each ``TaggedSentence.span.line`` falls inside exactly one chunk's
    absolute line range. Used by ``_prepare_extract_claims_chunks`` to
    pass each chunk its own tagged sentences without re-decomposing.

    Adjacent chunks overlap by a small backward window (see
    ``_chunk_paper``); a sentence whose line falls in the overlap
    region is assigned to the *later* chunk (the one that owns the
    line in its own range starting at ``line_offset``). This mirrors
    the LLM's view: the earlier chunk truncates at the split point.
    """
    if not chunks:
        return []
    # Sort chunks by line_offset so binary lookup makes sense (they
    # already are, but be defensive).
    sorted_chunks = sorted(enumerate(chunks), key=lambda x: x[1].line_offset)
    # Build (line_offset, end_line_exclusive, original_index) tuples.
    ranges: list[tuple[int, int, int]] = []
    for idx, (orig_idx, c) in enumerate(sorted_chunks):
        n_lines = len(c.text.splitlines())
        ranges.append((c.line_offset, c.line_offset + n_lines, orig_idx))

    buckets: list[list[TaggedSentence]] = [[] for _ in chunks]
    for ts in tagged:
        line = ts.span.line
        # Pick the latest chunk whose line_offset <= line < end. With
        # overlapping chunks, multiple ranges may contain the line; the
        # latest-starting one is the canonical owner.
        owner: int | None = None
        for start, end, orig_idx in ranges:
            if start <= line < end:
                owner = orig_idx
        if owner is not None:
            buckets[owner].append(ts)
    return buckets


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
        standalone = _strip_line_prefix(raw.standalone) if raw.standalone else ""
        claims.append(Claim(
            uid=uid,
            loc=loc,
            text=text,
            standalone=standalone,
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
