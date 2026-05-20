# Hypothesis Tuning Log

Classifier: `zeroshot-base` (MoritzLaurer/deberta-v3-base-zeroshot-v2.0)
Paper: P2300R10 (107 leaf sections)
GPU: RTX 4090 (~5-11s per full run)

## design-rationale

**Problem:** Original hypothesis "argues for a design decision or discusses trade-offs" fired on only 4/107 sections. WG21 design sections don't argue - they explain.

**Tuning:**

| Hypothesis | Targets hit (>0.5) | Notes |
|---|---|---|
| "This text argues for a design decision or discusses trade-offs" | 5/14 | Too academic |
| "This text explains how a software feature works or why it was designed this way" | 11/14 | Better but misses code-heavy sections |
| **"This text explains how an API, protocol, or language feature works, or discusses its design and behavior"** | **14/14** | All targets above 0.5 |

**Final:** "This text explains how an API, protocol, or language feature works, or discusses its design and behavior"

Fires on 67 sections. Broad, but correctly covers the entire Design - user side and Design - implementer side blocks plus some spec sections that contain explanatory prose.

## limitation

**Problem:** "describes a limitation, concession, or known issue" fires on 43 sections (>0.5) but with heavy false positives in spec sections. More importantly, only 9/18 target sections contain explicitly stated limitations. The other 9 describe features from which the red team *inferred* limitations through domain reasoning.

**Variants tested:**

| Hypothesis | Targets >0.5 | FP >0.5 |
|---|---|---|
| v0: "describes a limitation, concession, or known issue" | 6/18 | 36 |
| v1: "describes a constraint, restriction, caveat, trade-off, or thing that does not work in all cases" | 1/18 | 15 |
| v2: "acknowledges something that is incomplete, provisional, unsupported, or not yet resolved" | 3/18 | 10 |
| v3: "discusses a difficulty, complexity, risk, or downside" | 4/18 | 2 |

Best union (any variant >0.5): 9/18 targets.

**Dual-filter test:** v0 broad (>0.5) AND "purely formal definition" reject (<0.5) = 4 TP, 3 FP. Precision 57%, recall 22%. Rejection filter kills too many valid targets.

**Ceiling analysis:** 9/18 targets contain limitation language. The other 9 are:

- sec 1 "Priorities" - promise section, not limitation
- sec 11 "Field experience" - evidence section
- sec 32 "Senders support cancellation" - limitation buried in subsections
- sec 34 "Schedulers advertise forward progress" - too coarse (implied)
- sec 37 "Senders can represent partial success" - competing approaches (implied)
- sec 38 "All awaitables are senders" - exception for await_transform (implied)
- sec 45 "Receivers serve as glue" - runtime contract, not static (implied)
- sec 47 "execution::connect" - lifetime is user's responsibility (implied)
- sec 48 "Sender algorithms are customizable" - invisible domain agreement (implied)
- sec 52 "All senders are typed" - template errors catastrophic (implied)
- sec 103 "Execution contexts" - run_loop no reset (implied)

These sections describe *features*. The limitations are inferred by a critic reading the design prose. No hypothesis wording will catch these because the limitation isn't in the text.

**Conclusion:** Keep v0 as-is. Accept ~50% recall ceiling. The implied-limitation sections are correctly caught by `design-rationale` (which fires on most of them). The per-lens LLM step (future work) handles the inference from design prose to limitation.

**Final:** "This text describes a limitation, concession, or known issue"

## perf-claim

**Problem:** Original "makes a performance or efficiency claim" fires on 1 section with zeroshot-base.

**Variants tested:**

| Hypothesis | Claim targets >0.5 | Total >0.5 |
|---|---|---|
| v0: "claims something is fast, efficient, zero-cost, low-overhead, or optimizable" | 2/6 | 4 |
| v1: "discusses allocation, overhead, latency, compile time, or runtime cost" | 0/6 | 0 |
| v2: "explains why a design avoids overhead or improves performance" | 0/6 | 0 |
| v3: "mentions speed, memory usage, or computational cost" | 0/6 | 1 |
| v4: "compares approaches based on their runtime cost, overhead, or resource usage" | 0/6 | 0 |
| v5: "justifies a design choice by arguing it avoids unnecessary work or allocation" | 1/6 | 2 |

**Analysis:** The 4 missed sections (Prior art, Field experience, Completion schedulers, Lazy senders) mention performance incidentally within sections primarily about other things (comparison, evidence, design). The classifier correctly identifies the primary topic. The performance vocabulary is present in the 512-token window but doesn't dominate the section.

**Ceiling:** ~2-3/6 claim sections. The other sections are correctly classified under their primary function (comparison, evidence, design-rationale). Performance claims in those sections would be surfaced by the per-lens LLM step reading all `design-rationale` sections together.

**Key insight for cross-referencing:** The real P-C4 finding ("zero benchmarks") comes from `perf-claim` firing on 2-4 sections while `measurement` fires on 0-2 sections. The finding is in the gap between claims and evidence, not in any single section's classification.

**Final:** "This text claims something is fast, efficient, zero-cost, low-overhead, or optimizable"

## deferral

**Problem:** Original "defers something to future work or another document" fires on 12 sections. 9 target sections need it.

**Variants tested:**

| Hypothesis | Targets >0.5 | Total >0.5 |
|---|---|---|
| v0: "defers something to future work or another document" | 2/9 | 18 |
| v1: "says something is not included, not proposed, or left for future work" | 0/9 | 0 |
| v2: "explicitly omits a feature, mechanism, or component from the proposal" | 0/9 | 2 |

**Analysis:** The key deferrals in P2300R10 are single bullet points within changelog-style sections. "Specific type erasure facilities are omitted, as per LEWG direction" is 15 words inside a 357-token design-changes list. The classifier correctly sees the section as a changelog, not a deferral.

**Ceiling:** ~2/9 with classifier. The rest are one-line mentions inside sections about other things.

**Recommendation:** Keep v0 for sections where deferral IS the primary topic (like "Composition with parallel algorithms" which discusses deferring parallel algorithm integration). Supplement with a keyword search for "omitted," "not included," "future work," "deferred," "not proposed" to catch the inline mentions that the classifier misses. The keyword search is deterministic and trivially fast.

**Final:** "This text defers something to future work or another document"

## design-goal

**Variants tested:**

| Hypothesis | Targets >0.5 | Total >0.5 | FP |
|---|---|---|---|
| v0: "introduces a problem or states design goals" | 1/2 | 18 | 17 |
| **v1: "states what a proposal aims to achieve or what properties it should have"** | **2/2** | **6** | **4** |
| v2: "describes the motivation for a proposed change or new feature" | 2/2 | 9 | 7 |

v1 hits both targets (Motivation=0.95, Priorities=0.97) with only 4 FP. v0 misses Priorities (0.36) and has 17 FP. v1's phrasing precisely captures intent/goal sections without catching design-explanation sections.

**Final:** "This text states what a proposal aims to achieve or what properties it should have"

## wording

**Problem:** v0 "contains formal specification with normative requirements" fires on 104/107 sections. 20/20 targets hit but 83 FP. The classifier thinks nearly everything in a standards paper is normative.

**Variants tested:**

| Hypothesis | Targets >0.5 | FP |
|---|---|---|
| v0: "contains formal specification with normative requirements" | 20/20 | 83 |
| v1: "defines types, functions, or concepts using shall, must, or preconditions" | 3/20 | 6 |
| v2: "is written in the style of a C++ standard specification with Effects, Returns, and Mandates clauses" | 18/20 | 46 |

v2 is the best trade-off: 18/20 recall with 46 FP (vs 83). Misses sec 51 "Execution resource transitions are two-step" and sec 81 "execution::transform_sender."

**Note:** For routing, even 46 FP is acceptable. The per-lens LLM reads all wording-tagged sections (~23k tokens) in one call and only finds problems in the real spec ones. Over-inclusion wastes LLM context but doesn't produce false findings.

**Final:** "This text is written in the style of a C++ standard specification with Effects, Returns, and Mandates clauses"
- Caveat: trade 2 missed targets for 37 fewer FPs. Use v0 if recall is paramount.

## example

**Variants tested:**

| Hypothesis | Targets >0.5 | Total >0.5 | FP |
|---|---|---|---|
| v0: "provides a code example or demonstration" | 4/4 | 52 | 48 |
| v1: "shows a usage example or tutorial for a programming interface" | 0/4 | 14 | 14 |
| v2: "demonstrates how to use an API with sample code" | 0/4 | 5 | 5 |
| v3: "is primarily a code listing with minimal prose explanation" | 0/4 | 4 | 4 |
| **v4: "walks through a code example to illustrate how something works"** | **4/4** | **40** | **36** |

v4 catches all 4 targets and reduces FP from 48 to 36 vs v0. v1/v2/v3 miss all targets - the example sections don't introduce themselves as tutorials, they just contain code. FPs are spec sections with embedded code snippets, which is technically correct.

**Final:** "This text walks through a code example to illustrate how something works"

## evidence

**Variants tested:**

| Hypothesis | Target score | Total >0.5 | FP |
|---|---|---|---|
| v0: "reports implementation experience or deployment data" | 1.00 | 53 | 52 |
| **v1: "describes real-world usage, adoption, or field testing of a software system"** | **0.99** | **1** | **0** |
| v2: "reports on production deployment or practical experience building something" | 0.99 | 1 | 0 |

v1 and v2 both achieve near-perfect scores on the single target (Field experience) with 0 FP. v0 has 52 FP because "reports deployment data" fires on every section that mentions any implementation detail. v1 is precise and clear.

**Final:** "This text describes real-world usage, adoption, or field testing of a software system"

## measurement

**Variants tested:**

| Hypothesis | Total >0.5 |
|---|---|
| v0: "presents benchmark data or performance measurements" | 2 |
| **v1: "contains numerical data from benchmarks, profiling, or timing experiments"** | **1** |
| v2: "includes tables or charts with measured performance numbers" | 0 |

No targets in ground truth (the finding is about absence of measurement). v1 is the most precise: fires only on sec 3 "Asynchronous inclusive scan" (0.66) which contains some numerical content. v0 also fires on R0. v2 fires on nothing. For cross-referencing, the key signal is that `perf-claim` fires on some sections while `measurement` fires on 0-1 sections.

**Final:** "This text contains numerical data from benchmarks, profiling, or timing experiments"

## comparison

**Variants tested:**

| Hypothesis | Targets >0.5 | Total >0.5 | FP |
|---|---|---|---|
| v0: "discusses related work or alternative approaches" | 1/1 | 3 | 2 |
| v1: "compares this approach with other libraries, languages, or frameworks" | 0/1 | 0 | 0 |
| **v2: "evaluates the strengths and weaknesses of alternative designs or prior proposals"** | **1/1** | **2** | **1** |

v2 hits the target (Prior art=0.98) with only 1 FP ("Sender factories and adaptors are lazy" which does compare eager vs lazy - arguably correct). v1 misses entirely. v0 has 2 FP.

**Final:** "This text evaluates the strengths and weaknesses of alternative designs or prior proposals"
