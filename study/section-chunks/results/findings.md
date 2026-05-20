# Section-based chunking + classification study - findings

## Architecture

Section chunking + paragraph-level zero-shot classification as a
routing layer for downstream red team analysis. No LLM calls. The
classifier identifies which sections of a paper contain which types
of content, producing a score matrix that tells downstream steps
where to look.

## Best configuration

- **Classifier:** `zeroshot-base` (`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`)
- **Chunking:** 256-token paragraph chunks with sentence-boundary splitting (pysbd), min 64 tokens per chunk
- **Hypotheses:** 10 tuned hypotheses (see `hypothesis_tuning.md` for per-hypothesis details)
- **Hardware:** RTX 4090 GPU, ~25 seconds for P2300R10 (322 chunks x 10 hypotheses)

### Tuned hypotheses

| Short name | Hypothesis |
|---|---|
| design-goal | This text states what a proposal aims to achieve or what properties it should have |
| design-rationale | This text explains how an API, protocol, or language feature works, or discusses its design and behavior |
| wording | This text is written in the style of a C++ standard specification with Effects, Returns, and Mandates clauses |
| example | This text walks through a code example to illustrate how something works |
| evidence | This text describes real-world usage, adoption, or field testing of a software system |
| measurement | This text contains numerical data from benchmarks, profiling, or timing experiments |
| perf-claim | This text claims something is fast, efficient, zero-cost, low-overhead, or optimizable |
| comparison | This text evaluates the strengths and weaknesses of alternative designs or prior proposals |
| deferral | This text defers something to future work or another document |
| limitation | This text describes a limitation, concession, or known issue |

## Final scorecard (P2300R10)

Ground truth: 50 findings from the ad-hoc red team report
(`cursor-context/reports/red-team-p2300r10.md`), expanded to 74
entries with per-hypothesis expected labels.

| Metric | Count | Percent |
|---|---|---|
| HIT (all expected hypotheses fire) | 55 | 74% |
| PARTIAL (some hypotheses fire) | 9 | 12% |
| MISS | 10 | 13% |

### By finding type

| Type | Findings | Hit | Partial | Miss |
|---|---|---|---|---|
| local | 64 | 51 | 8 | 5 |
| cross-ref | 5 | 4 | 1 | 0 |
| absence | 5 | 0 | 0 | 5 |

Excluding absence findings (structurally undetectable by section
classification): **92% at least partially detected**.

### Progression through configurations

| Approach | HIT | PARTIAL | MISS | Chunks | Time |
|---|---|---|---|---|---|
| First 512 tokens only | 44 (59%) | 18 (24%) | 12 (16%) | 107 | 8.7s |
| Fixed 512-tok chunks | 47 (63%) | 17 (22%) | 10 (13%) | 147 | 10.3s |
| Paragraph 512-tok | 51 (68%) | 11 (14%) | 12 (16%) | 258 | 25.6s |
| **Paragraph 256-tok + sentence split** | **55 (74%)** | **9 (12%)** | **10 (13%)** | **322** | **24.9s** |

## Alternative classifiers tested

| Model | Result |
|---|---|
| `nli-small` (cross-encoder/nli-deberta-v3-small) | Scores too flat - no discrimination between hypotheses |
| `gliclass-base-v3.0` (knowledgator) | 4x slower, comparable discrimination. Focal loss did not materially help |
| `chkla/roberta-argument` | Trained on debate topics. Classifies all WG21 text as NON-ARGUMENT |
| ClaimBuster TinyBERT | Trained on political claims. No discrimination on technical text |
| `typeof/distilbert_base_uncased_csabstruct` | CS domain but labels too coarse (BACKGROUND/OBJECTIVE/METHOD/RESULT/OTHER) |

`zeroshot-base` wins because zero-shot lets you define exactly the
hypotheses you need. Specialized models are locked into training
labels that don't match the WG21 domain.

## Cross-validation on P4172R1

P4172R1 is a 1262-line design rationale paper - structurally different
from P2300R10's 6139-line specification. The classifier generalizes:

- `design-rationale` fires on 20/47 sections (correct for a rationale paper)
- `limitation` fires on 20/47 sections (correct - many trade-offs discussed)
- `measurement` fires on 5 sections (correct - P4172 has benchmark data)
- `perf-claim` fires on 4 sections (correct)
- `comparison` fires on 2 sections (Alternative and Complementary Designs, Decision Record)
- `evidence` fires on 0 sections (no "field experience" section)

The hypotheses were not overfit to P2300R10.

## Structural limits

Five findings are completely undetectable by section classification:

- **Absence findings** (5): "no debugging story," "zero benchmarks,"
  "compile-time costs unquantified," "missing convenience adaptors,"
  "thread safety unspecified." These require scanning the entire paper
  to confirm something is missing.

Five local findings are missed because the limitation is *implied*,
not stated. Sections like "All senders are typed" describe a feature;
the red team inferred "template error messages will be catastrophic."
No hypothesis wording catches this because the limitation isn't in the
text.

These 10 findings (13%) require the per-lens LLM step to discover.
The classifier's job is routing, not finding.
