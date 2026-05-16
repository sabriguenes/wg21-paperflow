# Findings — Step 1 tag-sentences ensemble study

> **Update (Phase 1 / P2300R10):** the original single-paper findings
> below have been superseded by a cross-paper test on P2300R10 Phase 1
> (sids 0..209). The cross-paper test revealed that **the TARGET
> hypothesis label is the dominant tuning knob**, not ensemble shape.
> Read `results/phase1_p2300r10.md` for the updated recommendation
> before acting on the ensemble shapes below.

**Corpora:**
- P4003R3 — 137 sentences (Opus full-paper gold)
- P2300R10 Phase 1 — 210 sentences (Opus partial gold; phases 2+ pending)

**Oracle:** Claude Opus 4.7, design-time labeling, single pass per paper.

The runtime never calls the oracle. Everything below describes a fixed
configuration the local classifiers will run unattended.

---

## Headline results

Top of every leaderboard, sorted by accuracy then by critical
miss-fires (gold=TARGET → predicted=SKIP, which is unrecoverable
because the LLM never sees the sentence):

| acc   | T→S | T→C | S→T | F1_T  | F1_S  | classifier      | hypotheses     | margin (T, S) |
| ----- | --: | --: | --: | ----: | ----: | --------------- | -------------- | ------------- |
| 0.591 |   0 |   6 |  11 | 0.710 | 0.308 | zeroshot-large  | baseline       | (0.05, 0.60)  |
| 0.584 |   0 |   ? |   ? | 0.710 | 0.345 | zeroshot-large  | baseline       | (0.05, 0.40)  |
| 0.584 |   0 |   ? |   ? | 0.705 | 0.375 | zeroshot-large  | v3_skip_fragment | (0.05, 0.40) |
| 0.569 |   1 |   5 |  11 | 0.699 | 0.364 | zeroshot-large  | baseline       | (0.05, 0.40)  |
| 0.547 |   0 |   5 |  12 | 0.694 | 0.083 | mean ensemble   | baseline       | (0.05, 0.40)  |
| 0.555 |   0 |   5 |  12 | 0.708 | 0.143 | cascade         | baseline       | (0.05, 0.40)  |
| 0.504 |   0 |     |     | 0.614 | 0.087 | nli-small       | v3_skip_fragment | (0.05, 0.60) |
| 0.467 |   0 |     |     | 0.576 | 0.087 | nli-small       | baseline       | (0.05, 0.60)  |
| 0.453 |   1 |  23 |   7 | 0.571 | 0.077 | nli-small       | baseline       | (0.05, 0.40)  |

Even the best fixed config is at ~59% accuracy. The accuracy ceiling
for zero-shot NLI classifiers on this kind of dense technical prose
is real and we should design around it, not chase it.

## What changed our minds

**1. Earlier P4003R3 cherry-pick (nli-small catches thesis, large
misses it) is real but unrepresentative.**

The thesis sentence (sid=0, "This paper asks the committee to advance
the *IoAwaitable* protocol...") is genuinely a case where
zeroshot-large fails:

- `zeroshot-large`: target=0.021, skip=0.050  →  predicted **CONTEXT** (wrong)
- `nli-small`:      target=0.990, skip=0.133  →  predicted **TARGET** (right)

But across the full 137 sentences, zeroshot-large is more accurate
than nli-small in every configuration we tried (by ~10–13 absolute
points). The single-sentence example overstated nli-small's value.

The thesis miss is a **hypothesis-label** failure ("A statement of
fact or opinion." does not entail a *request*), not a model-capacity
failure. Adding "request" to the hypothesis (variant `v2_request_too`)
regressed nli-small badly though, so the obvious fix doesn't work in
isolation.

**2. T→C misses are not critical losses; T→S misses are.**

When a gold-TARGET is tagged CONTEXT, the LLM still receives the
sentence in the chunk and can still extract a claim from it. The tag
only loses signal about *which* sentence to focus on. The chunk is
still complete.

When a gold-TARGET is tagged SKIP, the sentence is removed from the
chunk. The LLM cannot see it. There is no recovery.

This means **gross accuracy is the wrong metric**. The right primary
metric is "T→S miss-fires = 0". Everything else is a secondary trade.

**3. Multi-hypothesis fusion makes the small model worse, not better.**

`v6_multi_both` (three TARGET hypotheses + three SKIP hypotheses,
max-aggregated per side) collapsed to 35% accuracy with 8 T→S misses.
The classifier hits *some* SKIP hypothesis weakly on almost every
sentence, so the max-SKIP score gets pushed up across the board and
swamps the TARGET signal.

Single, well-chosen hypotheses outperform multi-hypothesis fusion.

**4. The ensemble's only real win is the thesis.**

The arithmetic-mean ensemble (nli-small + zeroshot-large) has lower
overall accuracy than zeroshot-large alone (54.7% vs 59.1%) BUT does
correctly tag the thesis as TARGET:

```
mean(small=0.99, large=0.021) = 0.506   target
mean(small=0.13, large=0.050) = 0.092   skip
diff = 0.414 > target_margin=0.05  →  TARGET ✓
```

If the thesis-class of error matters more than aggregate F1 (and it
does — these are paper-defining claims the LLM must see clearly), the
ensemble pays for itself.

## Recommendation

### Primary recommendation: cascade ensemble

**Use a cascade**: run `nli-small` first; if its scores are confident,
trust them. Only consult `zeroshot-large` on ambiguous sentences.

- Confidence rule (small "fast-path"):
  - `small.target > 0.50` AND `small.target − small.skip > 0.30` → trust small.
  - `small.skip   > 0.70` AND `small.skip   − small.target > 0.30` → trust small.
  - otherwise → score with large, then average the two.
- Margins: `target_margin = 0.05`, `skip_margin = 0.40`.

On P4003R3:

- Accuracy: 55.5%
- T→S miss-fires: **0**
- Thesis tagged TARGET: **yes** (small fast-path)
- Large-model calls: 96 / 137 (70%) — saves ~30% compute vs always-ensemble

This is the best **safety + cost** point: zero critical misses, thesis
correctly tagged, and we skip the large model on ~30% of trivial
sentences.

### Secondary recommendation (if cascade is too much engineering)

Stick with **`nli-small` alone**, but:

- Switch hypothesis labels to **v3_skip_fragment** style:
  - target: `"A statement of fact or opinion."`  *(unchanged)*
  - skip:   `"A heading, list marker, page metadata, or sentence fragment."`
- Margins: `target_margin = 0.05`, `skip_margin = 0.60`.

Result: accuracy 0.504 (up from 0.467 baseline), **0** T→S miss-fires,
thesis tagged TARGET, near-instant on a 1000-sentence paper.

### Anti-recommendation

Do **not** ship `zeroshot-large` alone as the default, even though it
has the highest aggregate accuracy. It silently misses thesis-style
claims phrased as requests, and that is exactly the failure mode we
care most about avoiding.

Do **not** ship multi-hypothesis fusion on either model — it
catastrophically over-SKIPs (see `v6_multi_both`).

## Where the ceiling is

At ~59% accuracy on a corpus this dense, we've roughly saturated what
zero-shot NLI can do with one-line hypotheses. Further gains require:

1. **Few-shot fine-tuning** of a dedicated small classifier (~5K
   labeled WG21 sentences). The cascade gives us essentially free gold
   labels via Opus to bootstrap this.
2. **Sentence-level features beyond raw text** — heading markers,
   table-cell detection, code-fence position, line-number proximity to
   `## Revision History`, etc. The current pipeline already detects
   most of these structurally and blanks them; a learned classifier
   for the residual "sentence fragment" case would help.
3. **Wider gold corpus** — single-paper labels overfit. A small
   labeled set across 5–10 papers would tighten the design.

None of these are required for Step 1 to be useful; the cascade above
is already strictly better than the current production config.
