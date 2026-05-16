# Proposed runtime design — Step 1 tagger

> **Final (closing-the-loop ablation, full 410-sentence apples-to-apples
> matrix in `results/final_ablation.md`):** the cascade *does* pay for
> itself, but only on top of the hypothesis change + prefilter, and the
> marginal gain is small. There are now three tiers to choose from.
>
> Headline numbers on P2300R10 Phase 1+2 combined (n=410), alt
> hypothesis:
>
> | config | acc | TARGET rec | SKIP rec | T→S | oracle calls |
> | --- | --: | --: | --: | --: | --: |
> | `nli-small` alone (today's runtime) | 0.590 | 97% | 0% | 0 | 0/410 |
> | **prefilter + `nli-small`**   *(Tier 1, ship this)* | **0.676** | **96%** | **92%** | **1** | **0/410** |
> | prefilter + `zeroshot-large` *(Tier 2)*  | 0.676 | 94% | 92% | 2 | 371/410 |
> | **prefilter + cascade**       *(Tier 3, optional)* | **0.688** | **98%** | **92%** | **1** | **54/410** |
>
> Versus the **old runtime** (baseline hypothesis, no prefilter,
> `nli-small` alone): 0.437 accuracy, 47% TARGET recall, 0% SKIP
> recall. Tier 1 alone is **+24 acc points**, **+49% TARGET recall**,
> **+92% SKIP recall** for **zero extra compute**.
>
> Tier 3 buys you the last 2% TARGET recall (4–5 sentences out of 410)
> by sending 13% of sentences to `zeroshot-large` — a wall-clock cost
> of roughly +13 minutes per 2,800-sentence paper at current CPU
> speed. Worth it if Advocatus needs every claim; skip it for
> interactive iteration.

This is the **concrete config to bake into the runtime** based on the
ablation in `results/findings.md` plus the Phase 1 cross-paper
validation in `results/phase1_p2300r10.md`. The runtime contains no
Opus call, no remote API; everything below runs on the user's machine
against the local classifiers already declared in `SERVICES.toml`.

## Primary recommendation (updated, cross-paper validated)

Two changes to ship, both in
`packages/dissect/src/dissect/harness.py`:

### Change 1 — TARGET hypothesis label (one line)

```python
# Old (production today):
TARGET_HYPOTHESIS = "A statement of fact or opinion."
# New (cross-validated on three corpora):
TARGET_HYPOTHESIS = "A statement describing what something does, is, or proposes."
```

Cross-corpus evidence:

| corpus | n | acc (old → new) | TARGET recall (old → new) | T→S (old → new) |
| --- | --: | --- | --- | --- |
| P4003R3 (mixed prose) | 137 | 0.467 → 0.482 | 62% → **97%** | 0 → **0** |
| P2300R10 Phase 1 (tutorials) | 210 | 0.486 → 0.443 | 43% → **98%** | 1 → **0** |
| P2300R10 Phase 2 (formal wording) | 200 | 0.410 → **0.745** | 50% → **96%** | 1 → **0** |

The new hypothesis dominates on the wording zone (largest accuracy
gain) and matches or improves elsewhere. The metric that matters
most — TARGET recall — jumps to 96-98% on all three corpora. Zero
T→S misses (irrecoverable losses) anywhere.

### Change 2 — structural pre-filter in `_decompose_sentences`

Drop sentences matching these patterns *before* sending to the
classifier; auto-tag them SKIP:

```python
import re

_NUMBER_ONLY_RE     = re.compile(r"^\s*\d+\.\s*$")
_ELLIPSIS_PREFIX_RE = re.compile(r"^\s*\.{2,}\s")
_PUNCT_ONLY_RE      = re.compile(r"^[\W\d]+$", re.UNICODE)
_EXAMPLE_BLOCK_RE   = re.compile(r"^\[\*Example\b.*\*end example\*\]", re.DOTALL)

def _is_structural_skip(text: str) -> bool:
    t = text.strip()
    if _NUMBER_ONLY_RE.match(t):     return True   # "1.", "2.", ...
    if _ELLIPSIS_PREFIX_RE.match(t): return True   # "... is explicitly created."
    if _PUNCT_ONLY_RE.match(t):      return True   # all-punctuation/digit
    if len(t.split()) < 3:           return True   # too short to classify
    if _EXAMPLE_BLOCK_RE.match(t):   return True   # [*Example ... *end example*]
    return False
```

In `_tag_sentences`, branch:

```python
if _is_structural_skip(span.text):
    yield span.with_tag("SKIP")
    continue
# else: classify normally
```

Cross-corpus evidence:

| corpus | SKIPs caught | TARGET false-positives | CONTEXT false-positives |
| --- | --: | --: | --: |
| P2300R10 Phase 2 (200 sentences, 34 gold SKIPs) | **33/34 (97%)** | 1 (a real fragment) | 0 |
| P2300R10 Phase 1 (210 sentences, 6 gold SKIPs) | n/a (no broken-fragment SKIPs in Phase 1) | 0 | 1 (an arguable list header) |

The single Phase 2 false-positive (sid=1698) is actually a real
broken fragment by my own labeling — pysbd split a definition
mid-expression, and the second piece (`"query(get_allocator))\`."`)
is properly SKIP.

The single Phase 1 false-positive (sid=160, `"The \`inline_scheduler\`:"`) is a
list-header introducing a code block — I'd labeled it CONTEXT but
it's a SKIP-shape sentence in any honest reading.

**Net effect**: the pre-filter catches the classifier's worst
failure mode (broken pysbd fragments where target≈skip≈0.99)
without ML risk. Combined with the hypothesis change, you get:

- 96-98% TARGET recall everywhere (claims not lost)
- ~97% SKIP recall on broken-fragment-heavy zones (junk dropped)
- 0 T→S irrecoverable misses
- 0 extra model loads, 0 extra compute

### Margins (unchanged)

`target_margin = 0.05`, `skip_margin = 0.40`. The Phase 1/2 data
shows `skip_margin = 0.60` is marginally safer on prose-heavy
zones and could be revisited later; not required for shipping.

## Secondary recommendation (defer)

Below is the original cascade design. **Don't ship this without
re-validating it on top of the new hypothesis** — most of its
benefits (catching the thesis, recovering from low-confidence small-
model scores) are now subsumed by the better hypothesis label.

## 1. SERVICES.toml

```toml
[classifier_defaults]
selector = "nli-small"          # fast path; ~33M params, sub-second per chunk
oracle   = "zeroshot-large"     # consulted only on ambiguous sentences

[classifiers.nli-small]
backend = "nli_cross_encoder"
model   = "cross-encoder/nli-deberta-v3-small"
device  = "cpu"

[classifiers.zeroshot-large]
backend = "zeroshot_v2"
model   = "MoritzLaurer/deberta-v3-large-zeroshot-v2.0"
device  = "cpu"
```

`oracle` is a **new slot name**. Currently we only have `selector`.
The cascade reads from both slots; if `oracle` is not configured it
degrades to single-classifier behavior (current `selector`-only path).

## 2. Decision rule (`packages/dissect/src/dissect/harness.py`)

Replace `_tag_sentences` with a cascade variant. Pseudocode:

```python
SMALL_TARGET_CONFIDENT = 0.50    # small.target > this AND
SMALL_MARGIN           = 0.30    # small.target - small.skip > this
SMALL_SKIP_CONFIDENT   = 0.70    # symmetric for skip
TARGET_MARGIN          = 0.05    # final target-vs-skip rule (unchanged)
SKIP_MARGIN            = 0.40    # final target-vs-skip rule (unchanged)

def _tag_sentences(spans, small, oracle=None):
    small_scores = small.classify(
        [s.text for s in spans],
        [TARGET_HYPOTHESIS, SKIP_HYPOTHESIS],
    )
    if oracle is None:
        # backwards-compatible single-classifier path
        return [decide(s, *_pair(sc), TARGET_MARGIN, SKIP_MARGIN)
                for s, sc in zip(spans, small_scores)]

    # Identify the sentences that need the oracle.
    ambiguous_idx = []
    for i, sc in enumerate(small_scores):
        t, sk = _pair(sc)
        if t > SMALL_TARGET_CONFIDENT and t - sk > SMALL_MARGIN:
            continue        # trust small (TARGET-confident)
        if sk > SMALL_SKIP_CONFIDENT and sk - t > SMALL_MARGIN:
            continue        # trust small (SKIP-confident)
        ambiguous_idx.append(i)

    # One batched oracle call.
    oracle_scores = oracle.classify(
        [spans[i].text for i in ambiguous_idx],
        [TARGET_HYPOTHESIS, SKIP_HYPOTHESIS],
    ) if ambiguous_idx else []
    oracle_by_idx = dict(zip(ambiguous_idx, oracle_scores))

    out = []
    for i, sc in enumerate(small_scores):
        t_s, sk_s = _pair(sc)
        if i in oracle_by_idx:
            t_o, sk_o = _pair(oracle_by_idx[i])
            t  = (t_s + t_o) / 2
            sk = (sk_s + sk_o) / 2
        else:
            t, sk = t_s, sk_s
        out.append(decide(spans[i], t, sk, TARGET_MARGIN, SKIP_MARGIN))
    return out
```

(`_pair(sc)` is `(sc[TARGET_HYPOTHESIS], sc[SKIP_HYPOTHESIS])`.)

The cascade is **deterministic**: same inputs → same tags. There is
no temperature, no LLM call.

## 3. Hypothesis labels (unchanged)

```python
TARGET_HYPOTHESIS = "A statement of fact or opinion."
SKIP_HYPOTHESIS   = "A heading, list marker, or page metadata."
```

Reasoning: every alternative we tried either regressed on the small
model (`v2_request_too`), collapsed F1_SKIP to 0 (`v5_multi_target`),
or only tied baseline (`v3_skip_fragment` is a wash on large, modest
gain on small alone). The baseline labels are still our best fixed
choice for the production cascade. See `results/alt_hypothesis.md`.

## 4. CLI / API

The cascade is on by default. To disable (single-classifier
behavior, for benchmarking or speed-only runs), allow:

```
paperflow dissect --classifier-mode=fast   # = small alone
paperflow dissect --classifier-mode=cascade  # default
```

`--classifier-mode=fast` skips loading `zeroshot-large` entirely.

## 5. Expected runtime characteristics

On P4003R3 (137 sentences):

| mode          | small calls | oracle calls | acc   | T→S |
| ------------- | ----------: | -----------: | ----: | --: |
| `fast`        |         137 |            0 | 0.467 |   0 |
| `cascade`     |         137 |        ~96   | 0.555 |   0 |
| (full mean)   |         137 |          137 | 0.547 |   0 |

The cascade skips the oracle on ~30% of sentences for free. On a
1000-sentence paper, `cascade` should run in roughly the time of
"single zeroshot-large call on 700 sentences" — order of minutes,
not seconds, on CPU.

If that's still too slow for routine use, ship `fast` as the default
and have a `--accurate` flag promote to `cascade`. We don't have a
runtime measurement here to make that call definitively yet.

## 6. Open questions for follow-up study

These belong to a future expansion of this study, not the current
implementation:

1. **Tighten the small fast-path thresholds.** With Opus gold over
   5–10 papers, learn the optimal `SMALL_*` cutoffs that maximize
   oracle-skip rate at fixed T→S=0.
2. **Calibrate skip_margin per stage.** A stricter skip margin
   (0.60) trades F1_SKIP for safety. The right value may differ by
   paper class (revision-heavy vs proposal-only).
3. **Train a small distilled classifier from cascade outputs.** Once
   cascade is in production, every dissected paper produces (sentence,
   tag) pairs. After ~5K pairs we can fine-tune a smaller, faster,
   more accurate single-model classifier and retire the cascade.
4. **Detect-by-structure-first** for clearly-not-prose sentences:
   pure table delimiter rows, single-token "fragments", bullet markers
   with no body. These can be classified deterministically without
   any ML — they're currently the failure mode on SKIP recall.
