# Trace Synthesis: Review Pipeline as Foundation Layer

The `review` package pipeline produces a structured trace for each
WG21 paper: claims, evidence, support map, internal contradictions,
load-bearing classification. This document describes how that trace
feeds downstream tools (the advocatus red-team and the Mod thread
generator) and what additional extraction step makes the trace
sufficient for both.

---

## The gap

The trace excels at **internal forensic analysis**. 118 deduplicated
claims, 185 evidence items, a full support map, load-bearing
classification. It covers the paper's argument structure thoroughly.

It has two blind spots:

1. **Cross-claim reasoning.** Claims are extracted per-chunk and
   verified against evidence one-to-one. The pipeline does not compare
   claims to each other. The advocatus's strongest finding on P2300R10
   (asymmetric evidentiary standards between Sections 1.9.2 and 5.6)
   required noticing that two analogous assertions applied different
   levels of scrutiny. Both claims were in the trace. Neither was
   linked to the other.

2. **Rhetorical posture.** The trace extracts what the paper *asserts*
   and what *evidence* it offers. It does not extract how the paper
   *persuades*, *dismisses*, *concedes*, or *frames scope*. These
   are the signals that hot-take commenters and red-team examiners
   actually react to.

---

## The fix: rhetorical marker extraction

Add a per-chunk extraction step to the review pipeline, parallel to
claim and evidence extraction. One Haiku call per chunk. The prompt
asks for five marker types:

| Type | What it catches | Lexical signal |
|------|----------------|----------------|
| `dismissal` | Paper rejects an alternative approach | "poor choice", "deal-breaker", "unacceptable", "rule out" |
| `concession` | Paper acknowledges a limitation or defers | "we acknowledge", "still being investigated", "not yet addressed" |
| `provocation` | Strong language or unqualified optimism | superlatives, "entirely", "must be considered a bug" |
| `scope_deflection` | Paper shifts responsibility elsewhere | "as per LEWG direction", "omitted", "left to companion paper" |
| `political_signal` | Committee votes, SG references, status | "SG1 concerns", "committee approved", "per SG direction" |

### Output model

```python
class RhetoricalMarker(BaseModel, frozen=True):
    loc: SourceLoc
    text: str
    section: str
    marker_type: Literal[
        "dismissal",
        "concession",
        "provocation",
        "scope_deflection",
        "political_signal",
    ]
    target: str       # what is being dismissed/conceded/deflected
    intensity: Literal["mild", "moderate", "strong"]
```

### Expected volume

10-30 markers per paper. Far fewer than claims or evidence. No dedup
needed — rhetorical markers are typically unique.

### Confidence by marker type

- **Dismissals, concessions, scope deflections, political signals:**
  High. These have distinctive lexical patterns. Haiku catches them
  reliably.
- **Provocation:** Medium (~60% recall). Loud cases are easy. Subtle
  cases where the provocation is the *absence* of qualification (an
  unqualified optimistic claim in a context that qualifies analogous
  claims) require cross-section reasoning the per-chunk extractor
  cannot do.

### Pattern detection (post-collection)

After all markers are collected across chunks, a single Opus call on
the full marker list (~30 items) identifies cross-section patterns:

- Dismissals whose target also appears as an unqualified positive
  claim elsewhere (the asymmetry pattern).
- Concessions that cluster around a single topic (signals an
  acknowledged weak area).
- Scope deflections that name companion papers (dependency graph).

This two-layer approach (Haiku extracts, Opus reasons over the
collection) mirrors the existing claim pipeline (Haiku extracts
per-chunk, Opus verifies and maps in step 5).

---

## How the trace feeds the advocatus

The advocatus red-team has five phases. The trace covers most of the
analytical work:

| Advocatus phase | What it needs | Trace coverage |
|----------------|---------------|----------------|
| **I. Citatio** (read the paper) | Paper metadata, full read | Step 0 (chunks, citations, metadata from paperstore) |
| **III. Interrogatio** (assess articuli) | Load-bearing claims, support status | Steps 5-6 (support map, load-bearing classification) |
| **IV. Examen** (test charges, Dei filter) | Candidate charges + evidence for/against | Internally contested + critical gap claims from step 6; concession markers answer the Confessio challenge; rhetorical markers provide Motivatio |
| **II. Inquisitio** (external research) | Public record, prior causes, citation verification | Steps 7-8 (web search, resolve) + external web search |
| **V. Animadversiones** (render verdict) | All of the above | Synthesis step |

The **Dei filter** maps to trace data as follows:

| Dei challenge | Trace source |
|---------------|-------------|
| Confessio (author already conceded?) | Concession markers targeting the same topic as the charge |
| Articulus (paper actually says this?) | Original quotes in claim data |
| Testimonium (resolvable by one question?) | Whether the claim's evidence is tagged `verifiable` |
| Humanitas (real opponent would raise this?) | External — needs web search |
| Prudentia (adversary self-harms by pressing?) | Judgment call — synthesis model |
| Dignitas (beneath notice?) | Marker intensity + load-bearing classification |

### The synthesis call

A single frontier-model call receives:

- Deduplicated claims (section 2 of trace), tombstones stripped
- Deduplicated evidence (section 4), compressed to
  `{loc, supports, tags}` without full quotes
- Support map (section 5)
- Load-bearing classifications (section 6)
- Rhetorical markers (new step)
- Pattern detection output (Opus post-collection pass)
- Paper metadata (title, authors, revision, audience)

The prompt instructs it to: (1) identify charges from internally
contested, critical gap, and asymmetry-pattern markers; (2) apply the
six Dei challenges to each; (3) render a verdict with surviving
objections, approbatio sections, and notae minores.

Expected input size: 15-25K tokens for a typical paper. P2300-scale
papers: 40-60K tokens. Fits in a single context window.

---

## How the trace feeds the Mod

The Mod's Phase I (Intelligence) has two parts: the smell test
(analytical) and the heat check (cultural/external). The trace
replaces the analytical part.

| Mod requirement | Trace source |
|----------------|-------------|
| Load-bearing claims (§1.1) | Non-peripheral claims from step 6 |
| "Does it add up" (§1.2a) | Unsupported load-bearing claims from support map |
| "Does it follow" (§1.2b) | Internal contradictions + asymmetry patterns from marker analysis |
| "Do receipts match" (§1.2c) | Steps 7-8 (web search, resolve) |
| "Author already copped to it" (§1.3a) | Concession markers targeting the same topic |
| "Is it a strawman" (§1.3b) | Original quotes in claim data |
| Inconsistency anchors (§1.4d) | `conflicted` claims + asymmetry patterns |
| Miss anchors (§1.4d) | `critical_gap` claims |
| Hot takes | Provocation and dismissal markers with `strong` intensity |
| Tangent magnets | Technology keywords in claims and evidence (CUDA, coroutines, Rust, etc.) + dismissal targets |
| Routing (§1.3c visibility) | Section location + classification + evidence tags → high/moderate/subtle |
| Design tension (§1.4b) | Scope deflections naming alternatives |
| Framing audit (§1.4e) | Rhetorical markers of type `dismissal` whose targets are the paper's premises |
| Benchmark consistency (§10c) | Evidence items tagged `quantitative` |

**Not covered by the trace** (still requires sub-agents):

- Public reception (Agent 1)
- Committee history (Agent 2)
- Author/ecosystem profile (Agent 3)
- Heat and interest tier calibration

The Mod still needs the paper text for quotes and Reddit voice. The
trace replaces the forensic analysis, not the generation.

---

## Pipeline cost model

For a typical 2-page paper (1 chunk):

| Step | Model | Calls | Purpose |
|------|-------|-------|---------|
| Extract claims | Haiku | 1 | Per-chunk claim extraction |
| Extract evidence | Haiku | 1 | Per-chunk evidence extraction |
| **Extract markers** | **Haiku** | **1** | **Per-chunk rhetorical marker extraction** |
| Dedup claims | Haiku | 1 | Semantic grouping |
| Dedup evidence | Haiku | 1 | Semantic grouping |
| Verify + map | Opus | 1 | Support map, contradictions |
| Load-bearing | Opus | 1 | Classification |
| **Pattern detection** | **Opus** | **1** | **Cross-marker asymmetry/cluster analysis** |
| Web search | Opus | 0-1 | Only if critical_gap claims exist |
| Resolve | Opus | 0-1 | Only if external evidence found |

For P2300 (6 chunks): 6 extra Haiku calls + 1 Opus call. The marginal
cost of the rhetorical extraction is ~10% of the total pipeline cost.

---

## Summary

The review pipeline's trace is the right foundation for both the
advocatus and the Mod. One additional per-chunk extraction step
(rhetorical markers) plus one post-collection pattern detection call
closes the gap between forensic analysis and the cultural/rhetorical
intelligence both tools need. The per-chunk extraction uses a fast
model. The cross-section reasoning uses a frontier model on a small
input. External research (web search, committee history, author
profile) remains outside the trace and is handled by each downstream
tool's own sub-agents.
