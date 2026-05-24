# Trace Synthesis: Review Pipeline as Foundation Layer

The `review` package pipeline produces a structured trace for each
WG21 paper: claims, evidence, support map, internal contradictions,
load-bearing classification. This document describes how that trace
feeds the Mod thread generator and what additional extraction step
makes the trace sufficient.

---

## The gap

The trace excels at **internal forensic analysis**. 118 deduplicated
claims, 185 evidence items, a full support map, load-bearing
classification. It covers the paper's argument structure thoroughly.

It has two blind spots:

1. **Cross-claim reasoning.** Claims are extracted per-chunk and
   verified against evidence one-to-one. The pipeline does not compare
   claims to each other. Asymmetric evidentiary standards between
   sections require noticing that two analogous assertions applied
   different levels of scrutiny. Both claims may be in the trace but
   neither is linked to the other.

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

The review pipeline's trace is the right foundation for the Mod.
One additional per-chunk extraction step
(rhetorical markers) plus one post-collection pattern detection call
closes the gap between forensic analysis and the cultural/rhetorical
intelligence both tools need. The per-chunk extraction uses a fast
model. The cross-section reasoning uses a frontier model on a small
input. External research (web search, committee history, author
profile) remains outside the trace and is handled by each downstream
tool's own sub-agents.
