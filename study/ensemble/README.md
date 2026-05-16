# Step 1 sentence-tagger ensemble study

This is a **design-time** ablation. Claude Opus 4.7 (the AI assistant
that produced this study) was the gold-label oracle. The resulting
runtime config is fixed and uses only local classifiers; **Opus is
never called at paper-processing time**.

The goal was to answer four questions that the runtime can't answer
on its own:

1. Which classifier is the most accurate baseline — `nli-small` or
   `zeroshot-large`?
2. Does an ensemble (mean / max / cascade) beat any single classifier?
3. What hypothesis labels should the zero-shot classifier consume?
4. What `target_margin` / `skip_margin` should the decision rule use?

**Final headline answer** (after closing-the-loop ablation on the
full 410-sentence apples-to-apples matrix in
[`results/final_ablation.md`](results/final_ablation.md)):

Three deployment tiers, choose by Advocatus per-claim cost:

| tier | config | acc | TARGET rec | SKIP rec | wall/paper |
| --- | --- | --: | --: | --: | --: |
| baseline (today) | `nli-small` alone, baseline hypothesis | 0.437 | 47% | 0% | ~70s |
| **Tier 1 (ship now)** | `nli-small` + alt hypothesis + prefilter | **0.676** | **96%** | **92%** | ~70s |
| Tier 2 (skip)    | `zeroshot-large` + alt hypothesis + prefilter | 0.676 | 94% | 92% | ~85min |
| Tier 3 (optional)| cascade + alt hypothesis + prefilter   | **0.688** | **98%** | **92%** | ~15min |

The two free wins:

1. **TARGET hypothesis label change** in `harness.py`:
   from `"A statement of fact or opinion."`
   to   `"A statement describing what something does, is, or proposes."`.
   Lifts TARGET recall from **43-62% → 96-98%** with zero T→S misses
   across P4003R3 / P2300R10 Phase 1 / P2300R10 Phase 2.

2. **Structural pre-filter** in `_decompose_sentences`: drop
   number-only / ellipsis-prefix / very-short / `[*Example*]` sentences
   before classification. Catches **33/34 SKIPs** the classifier
   completely fails on (target≈skip≈0.99 ties), with 1 borderline
   false-positive across 547 labeled sentences.

The cascade still earns its keep but as an **optional** upgrade: +1.2
acc points and +2% TARGET recall for a 13× wall-time cost. Ship Tier
1 first, measure Advocatus's per-claim cost, decide on Tier 3 later.

Reading order:
- [`results/final_findings.md`](results/final_findings.md) —
  **start here**: the definitive cascade-vs-single decision
- [`results/final_ablation.md`](results/final_ablation.md) — raw
  ablation matrix backing the final findings
- [`results/phase1_p2300r10.md`](results/phase1_p2300r10.md) — where
  the hypothesis-label finding first appeared
- [`results/phase2_p2300r10.md`](results/phase2_p2300r10.md) — where
  the SKIP-recall pre-filter finding first appeared
- [`results/findings.md`](results/findings.md) — original P4003R3-only
  analysis (superseded by `final_findings.md`)
- [`proposed_design.md`](proposed_design.md) — concrete runtime
  changes ready to translate to harness.py

## Layout

```
study/ensemble/
├── README.md                            this file
├── ablate.py                            score (sid, target, skip) -> tag for any combiner
├── extract_sentences.py                 dissect Step 0+1 stand-alone (per paper)
├── score_paper.py                       score one classifier over a paper's sentences
├── score_phase.py                       score gold labels against per-classifier scores
├── score_variants.py                    apply ablate.py scoring to alt-hypothesis outputs
├── run_alt_hypotheses.py                rerun a classifier against alternative hypothesis labels
├── test_strip_markers.py                test whether stripping "1." / "- " fixes T->C misses
├── test_alt_target_label.py             test broader TARGET hypotheses on P2300R10
├── test_alt_target_label_p4003r3.py     cross-validate the same on P4003R3
├── data/
│   ├── p4003r3_*                        P4003R3 sentences, scores, gold (137 sentences)
│   ├── p2300r10_sentences.{json,txt}    P2300R10 decomposed sentences (2797)
│   ├── p2300r10_nli-small_scores.json   nli-small scores for all 2797
│   ├── p2300r10_zeroshot-large_*        zeroshot-large scores (long-running; ~80 min on CPU)
│   ├── p2300r10_gold_phase1.json        sids 0..209 oracle labels (Phase 1 / prose)
│   ├── p2300r10_gold_phase2.json        sids 1500..1699 oracle labels (Phase 2 / wording)
│   └── alt_hypothesis_scores/
│       ├── nli-small/                   per-variant rescore (cheap; ~25s for all 7)
│       └── zeroshot-large/              per-variant rescore (expensive; ~12 min for 3)
├── results/
│   ├── baseline_ablation.md             full margins/combiner sweep (P4003R3 only)
│   ├── alt_hypothesis.md                hypothesis-label sweep (P4003R3 only)
│   ├── phase1_p2300r10.md               *** cross-paper TARGET-hypothesis test ***
│   └── phase2_p2300r10.md               *** wording-zone + SKIP pre-filter test ***
└── proposed_design.md                   concrete runtime config to bake in
```

## How to reproduce

All scripts run from the repo root with the venv active.

```bash
# 1. Sweep margin / combiner configs on the existing scores. Fast (<1s).
.venv/Scripts/python.exe study/ensemble/ablate.py > study/ensemble/results/baseline_ablation.md

# 2. Score the small model against alternative hypothesis labels. ~25s.
.venv/Scripts/python.exe study/ensemble/run_alt_hypotheses.py nli-small

# 3. Same for the large model (expensive, ~12 min on CPU). Optional.
.venv/Scripts/python.exe study/ensemble/run_alt_hypotheses.py zeroshot-large baseline v3_skip_fragment v5_multi_target

# 4. Score everything against gold. Fast.
.venv/Scripts/python.exe study/ensemble/score_variants.py > study/ensemble/results/alt_hypothesis.md
```

## Caveats

- **Single-paper gold corpus.** P4003R3 is dense, network-protocol-
  flavored, and has a long revision history. The gold labels and
  recommendations may not transfer perfectly to other paper styles
  (LEWG library-design papers, EWG core-language papers, etc.). A
  proper study should label 5–10 papers and re-run.
- **Oracle bias.** I (Opus) labeled the corpus in a single pass with
  a written rubric. There is no inter-annotator agreement check
  because there is only one annotator. The labels are intentionally
  conservative on SKIP and aggressive on TARGET because that matches
  the asymmetric cost the downstream LLM faces.
- **Hypothesis-label search was not exhaustive.** Six alternatives
  were tested. A grid search across phrasings would find more
  configurations; the current evidence is enough to rule out the
  multi-hypothesis fusion direction but does not prove the baseline
  is globally optimal.
- **Cascade thresholds are uncalibrated.** The fast-path cutoffs
  (`small.target > 0.50`, etc.) are hand-picked from inspection of the
  scores, not learned. They produce 0 T→S misses on this corpus, but
  a learned set may strictly dominate.

See `results/findings.md` § "Where the ceiling is" for the
follow-up work that would move the design past these caveats.
