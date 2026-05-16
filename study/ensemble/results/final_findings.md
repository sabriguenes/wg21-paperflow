# Final findings — cascade vs single, closing the loop

This is the apples-to-apples ablation we couldn't run before. Every
configuration is scored on the **same 410 gold-labeled sentences**
(P2300R10 Phase 1 prose + Phase 2 wording), with both classifiers
re-scored against both hypotheses on this exact subset (see
`../score_labeled_subset.py`, ~20 min wall on CPU).

Raw matrix lives in [`final_ablation.md`](final_ablation.md). This
file extracts the decisions.

## The five questions and their answers

### 1. Does the new TARGET hypothesis really win across the board?

**Yes — by a lot.** Same 410 sentences, `nli-small` alone:

| TARGET hypothesis | acc | TARGET recall | SKIP recall | T→S misses |
| --- | --: | --: | --: | --: |
| baseline (`"A statement of fact or opinion."`) | 0.437 | 47% | 0% | 4 |
| alt (`"…what something does, is, or proposes."`) | 0.590 | **97%** | 0% | 0 |

**+15 acc points, +50% recall, kills all T→S misses** from changing
one string. The win comes mostly from the wording zone (Phase 2) but
nothing regresses on prose (Phase 1 holds at 98% recall).

### 2. Is `zeroshot-large` actually better than `nli-small` once both use the new hypothesis?

**No, not on TARGET recall.** With the alt hypothesis:

| classifier | TARGET recall | SKIP recall | acc | T→S misses | wall/paper* |
| --- | --: | --: | --: | --: | --: |
| `nli-small` alone | **97%** | 0% | 0.590 | 0 | ~70s |
| `zeroshot-large` alone | 94% | 10% | 0.595 | 1 | ~85min |
| cascade (small fast-path, large on ambiguous) | **99%** | 0% | 0.600 | 0 | ~15min |

\* extrapolated to a 2,797-sentence paper at observed per-sentence
latency.

Once the hypothesis is right, `nli-small` matches or beats the large
model on the metric that matters (TARGET recall). `zeroshot-large`'s
edge is on the *raw* scores (sharper margins), not on the final
decisions.

### 3. Is the structural pre-filter the right call?

**Yes — it's the single best free upgrade.** Same `nli-small` + alt
hypothesis:

| config | acc | TARGET recall | SKIP recall | T→S misses |
| --- | --: | --: | --: | --: |
| no prefilter | 0.590 | 97% | 0% | 0 |
| **+ prefilter** | **0.676** | **96%** | **92%** | **1** |

**+9 acc points, +92% SKIP recall** for zero ML cost. The 1 T→S miss
is sid=1698 — a pysbd-fragmented sentence that I myself labeled
TARGET-with-an-asterisk in the Phase 2 gold; in practice it's a SKIP
shape.

The 1 TARGET-recall point we lose (97%→96%) is a real claim that
happens to be short enough to trip the `< 3 words` length filter.
Worth it: one missed claim out of 155, in exchange for 31 fewer
mis-classified SKIPs.

### 4. Does the cascade pay for itself on top of the hypothesis + prefilter?

**Marginally yes.** Best three configs (alt hypothesis, prefilter on):

| config | acc | TARGET rec | SKIP rec | T→S | oracle calls | wall/paper |
| --- | --: | --: | --: | --: | --: | --: |
| prefilter + `nli-small` | 0.676 | 96% | 92% | 1 | 0/410 | ~70s |
| prefilter + `zeroshot-large` | 0.676 | 94% | 92% | 2 | 371/410 | ~85min |
| **prefilter + cascade** | **0.688** | **98%** | **92%** | **1** | **54/410** | ~15min |

The cascade adds:

- **+1.2 acc points** over `nli-small` alone
- **+2% TARGET recall** (recovers ~4–5 claims out of 155)
- **0 reduction in T→S misses** (already 1 in both)
- **+13× wall time** (~70s → ~15min on a real paper)

That's a real trade-off, not a free win. The cascade is appropriate
when downstream stages (especially Advocatus's verification loop)
cost more than 15 min per claim, because each recovered claim is
worth the upstream compute.

### 5. What's the right runtime config?

Three tiers, choose by Advocatus cost:

#### Tier 1 — Ship this now

`nli-small` + alt hypothesis + prefilter. No SERVICES.toml changes
needed beyond the existing `selector = "nli-small"`.

- **0.676 accuracy** vs 0.437 baseline (**+24 points**)
- **96% TARGET recall**, **92% SKIP recall**
- ~70s per 2,800-sentence paper
- Zero risk of regressing existing pipelines
- All gains are deterministic (hypothesis = string, prefilter = regex)

#### Tier 2 — Skip (don't ship)

`zeroshot-large` alone + alt hypothesis + prefilter. Same accuracy
as Tier 1 but **80× slower** and **1 extra T→S miss**. There is no
combination of margins that makes this preferable to Tier 1 on this
corpus.

#### Tier 3 — Optional, deferred

Cascade (small fast-path, large oracle on ambiguous) + alt
hypothesis + prefilter. Adds an `oracle` slot to SERVICES.toml.

- 0.688 accuracy (vs Tier 1's 0.676)
- 98% TARGET recall (recovers ~4 claims per 410 vs Tier 1)
- 13% oracle call rate → ~15 min added wall time per paper
- Worth it iff Advocatus verifies ≥ 15 min per recovered claim

I'd ship Tier 1 alone first, measure Advocatus's per-claim cost in
production, and only then decide whether Tier 3 pays.

## What this overturns

- **The original cascade-as-primary recommendation** (in
  `findings.md`, pre-Phase-1) is wrong. Most of the cascade's win
  came from compensating for the bad TARGET hypothesis. Fix the
  hypothesis and the cascade's marginal value drops to ~+1 acc point.
- **The assumption that `zeroshot-large` is the "best/most accurate"
  classifier** is wrong on TARGET recall once both classifiers use
  the alt hypothesis. The large model has sharper raw scores but the
  small model's *decisions* are equivalent or better at the post-
  prefilter resolution we actually use.
- **The Phase 1/2 conclusion that "the prefilter eliminates Phase 2's
  worst failure mode"** is confirmed and tightened: 92% SKIP recall
  on the full 410, with exactly 1 false-positive that's defensibly a
  SKIP anyway.

## Files

- `final_ablation.md` — the raw per-(config × phase × hypothesis)
  matrix this writeup distills.
- `../final_ablation.py` — the script.
- `../score_labeled_subset.py` — the data prep that made apples-to-
  apples possible.
- `../data/p2300r10_alt_scores.json` — re-scored 410 sentences across
  all (classifier × hypothesis) combinations.
