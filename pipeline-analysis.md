# Assay Pipeline End-to-End Analysis

**Subject:** Comparative analysis of the assay pipeline on P4003R3 "Ask: A Minimal Coroutine Execution Model" using DeepSeek V4 Pro and Anthropic Opus 4.6.

**Inputs reviewed:**

- `wg21-data-dir/paperstore/p4003r3.md` (ground-truth paper, 570 lines)
- `wg21-data-dir/paperstore/p4003r3.assay.md` (Opus final report, 16:38 UTC)
- `wg21-data-dir/paperstore/p4003r3.assay.bak.md` (DeepSeek final report, 15:17 UTC)
- `wg21-data-dir/paperstore/p4003r3.trace.assay.md` + `.bak.md` (per-step traces)
- `wg21-data-dir/paperstore/p4003r3.debug.assay.md` + `.bak.md` (full prompt/response logs)
- `wg21-paperflow/packages/assay/src/assay/pipeline.py` (orchestration code)
- `wg21-paperflow/packages/assay/src/assay/harness.py` (pure-Python step logic)
- `wg21-paperflow/packages/assay/src/assay/assay.md` (prompts, contracts)
- `wg21-paperflow/packages/assay/src/assay/models.py` (Pydantic schemas)

---

## 1. Executive Summary

The two runs hit the same pipeline at the same revision against the same paper but produced wildly different verdicts:

|                         | DeepSeek (15:17)      | Opus (16:38)        |
| ----------------------- | --------------------- | ------------------- |
| Claims extracted        | 90                    | 58                  |
| Evidence extracted      | 74                    | 44                  |
| Unsupported claims      | 75 (83%)              | 21 (36%)            |
| Gaps (raw / by-lens)    | 75 / 96               | 21 / 42             |
| Verify closures         | 53                    | 18                  |
| Findings generated      | 45                    | 42                  |
| Findings survived       | 1                     | 17                  |
| Compounds               | 0                     | 1                   |
| **Verdict**             | **Weakened (Medium)** | **Undermined (High)** |
| **Thesis survives**     | Yes                   | No                  |

A C++-grounded technical review of Opus's 17 survivors found **2 invalid, 10 partially valid, 5 fully valid**. The single CRITICAL finding (#135, "Two-argument `await_suspend` requires language or ABI change") is technically wrong: paper line 246 describes the standard `await_transform`-proxy pattern, which is library-level and requires no language change. Because that finding overlaps thesis content words (via `_thesis_overlap`), `synthesize()` flipped the verdict from "Weakened" to "Undermined" on the strength of one incorrect Analyze output that Challenge failed to kill.

**Bottom line:** The pipeline algorithm is *structurally sound* but has eight specific data-flow defects that compound into verdict instability. The verdict swing between models is not noise; it is the pipeline amplifying a single bad reasoning step at the model level into a thesis-killing call. Fixing the defects below would have given both models the same verdict (Weakened, with the same residual concerns about table formatting, SD-4 cost acknowledgment, and `std::execution` integration story).

---

## 2. Step-by-Step Trace

### Step 0 - Receive

Pure mechanical. Both runs loaded the same `paper_md` (570 lines), title, authors, intent (`ask`), audience (`LEWG`). No issues.

### Step 1 - References

Extracted 12 P-numbers. Author-overlap was computed for each:

| Paper   | Overlap | In paperstore |
| ------- | ------- | ------------- |
| P4172R0 | 1.00    | yes           |
| P4100R0 | 1.00    | yes           |
| P4090R0 | 1.00    | yes           |
| P4091R0 | 1.00    | yes           |
| P4035R0 | 0.33    | yes           |
| P2300R10, P3482R1 | 0 | yes |
| others  | 0       | varies        |

No model-dependent behavior. No issues.

### Step 2 - Index

Built RAG index over the cited papers (excluding P4003R3 itself). No issues.

### Step 3 - Survey

Chunked the paper. Both models received the same chunks because chunking is purely a function of `chunk_tokens` and the source markdown. Chunk boundaries followed `### N.M` headings. Per the trace, the paper was chunked into ~7 chunks at `chunk_tokens=1000`.

### Step 4 - Extract (per chunk, model-driven)

This is the first model-divergence point. Same prompt, same chunks, dramatically different outputs.

| Category    | DeepSeek | Opus |
| ----------- | -------- | ---- |
| Claims      | 90       | 58   |
| Evidence    | 74       | 44   |
| Concessions | 0        | 0    |
| Questions   | 1        | 1    |
| Dependencies | 13      | 15   |

DeepSeek classifies 32 more "claim" items than Opus. Looking at the extracted items, DeepSeek pulls borderline sentences like *"The same three concerns apply"* (line 148) and *"Synchronous awaitables return `true` from `await_ready` and never suspend at all"* (line 150) as claims; Opus skips them as fragments. The Extract prompt's funnel has a catch-all *"claim - any remaining assertion the paper makes that is intelligible in isolation. Skip terse fragments..."* - and "intelligible in isolation" is a judgment call. Both models are defensible; they apply the threshold differently.

**Important consequence:** the claim count directly determines the input volume to Step 5 Decide and Step 6 Classify. Models that extract more aggressively get more downstream gap candidates.

**Pipeline issue P-1 (Extract prompt ambiguity):** The funnel's "skip terse fragments" criterion is too soft for reproducibility. Models with different liberality produce 50% different claim counts on the same paper.

### Step 5 - Decide (per chunk)

The per-chunk Decide prompt asks the LLM to judge whether the chunk contains support DISTINCT FROM each claim. Critically, *Decide receives only its own chunk* - it cannot see evidence in other chunks. For P4003R3 this is a serious limitation:

- Chunk 0 (Abstract) claims `"the *IoAwaitable* protocol provides exactly those three things - executor affinity, stop token propagation, and frame allocator delivery"` (line 24).
- The supporting evidence is the `io_env` struct at lines 224-229 in Chunk 2 (Section 4.1), which literally lists those three fields.
- Decide on Chunk 0 cannot see Chunk 2, so this claim is judged *unsupported*.

DeepSeek's 83% unsupported rate vs Opus's 36% reflects how the models handle this artificial scarcity: DeepSeek treats absence of in-chunk evidence as strict failure; Opus interprets explanatory prose as "explanatory mechanism with technical detail" (one of the support categories in the prompt) more generously.

**Pipeline issue P-2 (Decide is chunk-local):** Cross-chunk evidence is invisible to Decide. This drives false unsupported judgments for any claim that is substantiated later in the paper.

### Step 6 - Classify (single batch)

Receives the unsupported-claim list (quote + line + chunk_index + Decide's reason). Crucially, *Classify does not receive the chunk text*.

```python
items_block = "\n".join(
    f"- (chunk {u['chunk_index']}, line {u['line']}) \"{u['quote']}\" - reason unsupported: {u['reason']}"
    for u in unsupported
)
user_msg = (
    f"# Paper: {state.paper_id}\n\n"
    f"## Unsupported claims ({len(unsupported)} total)\n\n"
    f"{items_block}\n"
)
```

That is the entire user message. Classify must invent the gap question, why-important text, primary and secondary lens, and severity from one sentence of quote + one sentence of Decide's reason. There is no paper context, no thesis, no neighboring evidence to anchor the lens choice.

The schema also restricts Classify to `"significant" | "minor"` in Pass 1:

```python
severity: str = Field(default="minor", description="significant|minor (no critical in Pass 1).")
```

The critical severity is supposed to be assigned by `upgrade_gaps()` after Step 8 Derive.

**Pipeline issue P-3 (Classify is text-blind):** Severity and lens are decided with no paper context. This is the single biggest source of unstable gap counts between models.

### Step 7 - Collect (pure Python)

`harness.collect()` aggregates, dedups by exact quote and substring absorption, assigns global IDs, groups gaps by primary and secondary lens. The duplication in "gaps by lens" (96 listings for 75 unique DeepSeek gaps, 42 listings for 21 unique Opus gaps) is from secondary-lens cross-listing - not a bug, just a counting artifact in the trace.

No issues.

### Step 8 - Derive (single LLM call)

Both models compressed the paper into a similar thesis:

- **DeepSeek:** *"The IoAwaitable protocol is the minimal, necessary vocabulary..."*
- **Opus:** *"The IoAwaitable protocol - comprising exactly three concerns: executor affinity, stop token propagation, and frame allocator delivery - is the minimum sufficient vocabulary..."*

Both are accurate. Load-bearing claim counts differ (DeepSeek: 5; Opus: 12) which affects which claims downstream Analyze emphasizes.

After Derive, `upgrade_gaps()` runs. Here is the entire function:

```134:152:c:\Users\Vinnie\src\wg21-paperflow\packages\assay\src\assay\harness.py
def upgrade_gaps(
    gaps_by_lens: dict[str, list[GapOutput]],
    central_claim: str,
    problem_statement: str,
) -> dict[str, list[GapOutput]]:
    """Post-Derive: upgrade gap severity if gap touches thesis."""
    thesis_words = set((central_claim + " " + problem_statement).lower().split())
    result: dict[str, list[GapOutput]] = {}
    for lens, gaps in gaps_by_lens.items():
        new_list: list[GapOutput] = []
        for b in gaps:
            if b.severity in ("significant", "minor"):
                gap_words = set(b.gap.lower().split())
                overlap = thesis_words & gap_words
                if len(overlap) >= 3:
                    b = b.model_copy(update={"severity": "critical"})
            new_list.append(b)
        result[lens] = new_list
    return result
```

**This is a critical bug.** `_thesis_overlap()` elsewhere in the same file strips stop words, but `upgrade_gaps()` does not. With stop words included, *any* gap that mentions "the protocol provides" matches thesis "the protocol is" on three common words (`the`, `protocol`, etc.) and gets upgraded to critical. Result:

- DeepSeek: 75 gaps → **69 critical, 22 significant, 5 minor** (92% critical)
- Opus: 21 gaps → **36 critical, 2 significant, 4 minor** (86% critical)

Almost every gap is critical because `the` matches `the`. This noise feeds Step 12 Analyze's prompt (`Cross-chunk gaps from OTHER chunks`), inflating the perceived importance of unrelated gaps.

**Pipeline issue P-4 (upgrade_gaps stop-word bug):** `upgrade_gaps()` does bag-of-words intersection without stop-word filtering. Threshold of 3 with stop words is satisfied by almost everything.

### Step 9 - Verify (single LLM call with tool)

Only the *one* companion paper with the highest author overlap is loaded:

```612:613:c:\Users\Vinnie\src\wg21-paperflow\packages\assay\src\assay\pipeline.py
candidates = [r for r in state.ref_pids if r.in_paperstore and r.author_overlap >= 0.5]
best = max(candidates, key=lambda r: r.author_overlap)
```

For P4003R3 the candidates with `author_overlap >= 0.5` are P4172R0, P4100R0, P4090R0, P4091R0 (all with author_overlap=1.0; `max` picks the first one Python sees, which is P4172R0). The other three never get queried by Verify, *even though* they are the dedicated companions for the sender-composition (P4090R0) and error-channel (P4091R0) claims the paper makes at line 517.

DeepSeek's lone surviving finding is "Unsubstantiated Claim About Sender Composition and Data Loss" at line 517 - exactly the claim P4090R0/P4091R0 substantiate. Verify never searched those papers. So DeepSeek's only verdict-relevant survivor exists *because* of this bug.

Opus's Verify additionally produced a contradictions list including: *"P4003R3 claims exactly three concerns, but P4172R0 Section 9 (line 557) refers to 'Four requirements. One protocol.'"* That looked like a real contradiction at first reading, but P4172R0 is the companion paper that adds a fourth design dimension (cancellation propagation analysis) - it is not a contradiction, it is a more detailed taxonomy. The contradictions list is passed forward but is not currently used by Challenge or Synthesize, so this didn't materially affect the verdict.

**Pipeline issue P-5 (Verify uses one companion):** For author-overlap=1.0 ties, only one paper is searched. Co-author papers that are explicitly cited for specific claims are silently ignored.

**Pipeline issue P-6 (Verify contradictions are dead state):** `VerifyOutput.contradictions` is collected but no downstream step reads it. Detected contradictions never make it into Analyze or Challenge prompts.

### Step 10 - Research

Six per-lens calls. Worked correctly for both runs. Not a divergence driver.

### Step 11 - Probe

Pure Python. No issues.

### Step 12 - Analyze (per chunk)

This is the most consequential step. The user message includes:

```209:227:c:\Users\Vinnie\src\wg21-paperflow\packages\assay\src\assay\pipeline.py
parts = [
    f"# Paper: {pid}\n\n",
    f"## Thesis\n\nCentral claim: {derive.central_claim if derive else ''}\n",
    f"Problem: {derive.problem_statement if derive else ''}\n",
    f"Scope: {derive.scope_boundary if derive else ''}\n",
    f"Ask calibration: {derive.ask_calibration if derive else ''}\n\n",
    "## Load-bearing claims\n\n",
]
for lb in (derive.load_bearing_claims if derive else []):
    parts.append(f"- [{lb.id}] \"{lb.quote}\"\n")

if other_gaps[:20]:
    parts.append("\n## Cross-chunk gaps\n\n")
    for b in other_gaps[:20]:
        parts.append(f"- [{b.id}] [{b.severity}] {b.gap} (line {b.line})\n")

parts.append(f"\n## Chunk: {chunk.heading} (lines {chunk.start_line}-{chunk.end_line})\n\n")
parts.append(f"{ctx.inject_untrusted(numbered)}\n")
```

Things Analyze DOES receive:
- Thesis, problem, scope, ask_calibration
- Load-bearing claims (with IDs)
- Up to 20 gaps from *other* chunks (closed_by==0 only)
- The chunk text

Things Analyze does NOT receive:
- Gaps from the current chunk
- Research output from Step 10 (the prompt advertises it but the code does not pass it)
- Closed-gap evidence from Step 9 Verify
- Confirmations or contradictions from Step 9 Verify
- The cited-paper index for in-context search
- The list of evidence already collected (so it cannot tell when paper text supports a claim by referring to evidence elsewhere)

The prompt in `assay.md` claims:

> You have:
> - The thesis (central_claim, problem_statement, scope_boundary)
> - Load-bearing claims
> - Cross-chunk gaps from OTHER chunks
> - **Research context from all 6 lenses**
> - **The 25 test patterns**

The "Research context from all 6 lenses" promise is unfulfilled - the code never appends `state.research` to the user message. The "25 test patterns" reference points to nothing the prompt actually includes; the model has to know them by name (T01-T25) and there is no documentation of what each test is in the prompt either.

**Pipeline issue P-7 (Analyze drops Verify and Research):** The prompt promises research context and the implicit framework includes verified resolutions, but the code passes neither. This is a documentation/implementation contract violation.

**Pipeline issue P-8 (Analyze excludes own-chunk gaps):** Findings on chunk N cannot reference gap IDs from chunk N, even though those gaps are exactly the questions a reviewer would ask about that chunk. This causes Analyze to *re-discover* concerns Classify already raised, often with subtly different wording, then later all the duplicates get killed by Challenge as "phantom" or "substance" without anyone realizing they were Classify's gaps.

This is the root cause of the duplicated "Two-argument await_suspend" findings at IDs 135 and 192 in the Opus run. ID 135 came from Analyze on Chunk 2 (lines 219-249, where the IoAwaitable concept lives). ID 192 came from Rationale (line 13425 in the debug log, "Two-argument await_suspend requires core language change but impact is underspecified"). Both are the same complaint, framed slightly differently. Challenge killed 192 via "resolution" but kept 135 alive on different reasoning - two LLM calls with overlapping content reached opposite verdicts on the same underlying claim.

### Step 13 - Rationale (single LLM call)

The Rationale prompt receives thesis, ask_calibration, and lists of claims and evidence (up to 30 of each). It produces an SD-4 checklist plus additional findings/strengths. Both runs identified SD4-4 (cost acknowledgment) as failing and produced rationale findings overlapping with Analyze. The de-duplication between Analyze findings and Rationale findings is non-existent: both passes can generate findings about the same claim and both get added to `state.findings`.

**Pipeline issue P-9 (Rationale duplicates Analyze):** Findings from Rationale are appended to `state.findings` without any dedup against Analyze findings, doubling the input to Challenge.

### Step 14 - Challenge (per lens batch)

This is where verdicts crystallize. Each finding is judged against five challenges (concession, phantom, resolution, plausibility, substance). The prompt provides:

- The thesis and scope
- Concessions (list)
- For each finding: title, severity, lens, quote, line, explanation, damage
- Paper context: ±5 lines around the finding's line number
- Optionally, companion-paper evidence per finding title (queried via `query_for_challenge`)

Concrete numbers:

|              | DeepSeek | Opus |
| ------------ | -------- | ---- |
| Findings in  | 45       | 42   |
| Killed       | 44       | 25   |
| Survived     | 1        | 17   |
| Kill rate    | 98%      | 60%  |
| concession   | 12       | 0    |
| phantom      | 3        | 4    |
| resolution   | 18       | 12   |
| plausibility | 7        | 6    |
| substance    | 4        | 3    |

DeepSeek killed 12 findings as "concession" despite the paper having zero formal concession items. DeepSeek read scope statements like "This paper does not propose launch functions - the ecosystem provides them" (line 362) as concessions. Opus did not. The Challenge prompt does not list the formal concessions for that lens, nor does it explain that scope statements may function as concessions, so the two models took opposite readings.

For Opus finding #135 (the false critical), the paper context window was lines 241-251, which *includes* line 246 - the `await_transform` mechanism description. So the resolution was in the context. Opus simply rejected the resolution with this reasoning:

> "The paper claims this works via await_transform injecting the environment, but the standard's compiler-generated code calls await_suspend with exactly one argument (the handle); the paper never shows the library wrapper that bridges this gap, and if a language change is needed, the paper's explicit scope exclusion of language changes is violated - this is a genuine critical gap."

This is technically wrong (the proxy pattern is library-level), but the Challenge prompt does not arm the model to recognize "the paper says await_transform injects env" + "P4172R0 line N shows the wrapper code" as a complete resolution chain. The companion-paper evidence section *was* attached to this batch (it included P4172R0's IoAwaitable section), but the wrapper code itself is not in that excerpt.

**Pipeline issue P-10 (Challenge sees only ±5 lines):** A finding whose resolution spans more than 11 lines of paper text cannot be killed by "resolution" unless companion evidence happens to retrieve the right passage. For multi-paragraph resolutions, the window is too narrow.

**Pipeline issue P-11 (Challenge is concession-blind):** The prompt does not include the extracted concession items, only the inferred concept of "concession" as a category. Scope items, which functionally serve as concessions for many findings, are not passed at all.

**Pipeline issue P-12 (Challenge has no Verify state):** Closed-gap evidence from Step 9, contradictions, confirmations - none of this is shown to Challenge. A finding that re-raises a gap Verify already closed has no signal to identify itself as redundant.

### Step 15 - Couple

Only Opus produced a compound: *"two-argument await_suspend blocks ioawaitable concept completeness"* coupling findings 135 + 136. Since both component findings are technically wrong (findings 135 and 136 both rest on misreading the layered concept design, as the C++ review confirmed), the compound is also wrong. But the compound's existence promotes 135 to "Major" via `compound_map`, then `synthesize()` finds that 135 contradicts the thesis (bag-of-words overlap with "exactly", "protocol", etc.), and the verdict flips to Undermined.

DeepSeek produced no compounds. Its one survivor (the sender-composition finding) was promoted to Major via thesis overlap (it shares words like "sender", "composition" with the thesis), but its severity was "significant" so the verdict stayed at "Weakened".

### Step 16 - Synthesize

Pure Python. The verdict tree:

```python
if not surviving:
    verdict = "Sound"
elif contradicts_thesis:           # any critical finding overlaps thesis
    verdict = "Undermined"; verdict_confidence = "High"
elif critical_count > 0 or significant_count > 0:
    verdict = "Weakened"
    verdict_confidence = "High" if critical_count > 0 else "Medium"
else:
    verdict = "Sound"
```

The `contradicts_thesis` test uses `_thesis_overlap`, which DOES strip stop words and requires >=3 content-word overlap. Opus's finding #135 explanation contains "executor", "stop", "token", "frame", "allocator", "protocol" - more than 3 of which appear in the thesis. So `contradicts_thesis = True` and the verdict is Undermined.

**The critical observation:** *one* incorrect surviving critical finding is enough to flip the verdict from Weakened to Undermined. There is no robustness margin. The pipeline trusts the Analyze + Challenge chain unconditionally.

### Step 17 - Report

Pure rendering. No issues.

---

## 3. Finding-by-Finding Technical Review

A C++-grounded review of Opus's 17 surviving findings against the paper text and the C++20 coroutine standard found:

| Severity (per report) | INVALID | PARTIALLY VALID | VALID |
| --------------------- | ------- | --------------- | ----- |
| Critical (1)          | **1**   | 0               | 0     |
| Significant (13)      | 1       | 8               | 4     |
| Minor (3)             | 0       | 2               | 1     |
| **Total (17)**        | **2**   | **10**          | **5** |

### Detailed verdicts on the most consequential findings

**Finding #135 - "Two-argument await_suspend requires language or ABI change" - INVALID**

The paper at line 246 explicitly says: *"The caller's `await_transform` injects the environment as a pointer parameter."* This is the standard C++20 library-level mechanism:

1. User writes `co_await ioawaitable_thing`.
2. Compiler invokes `promise.await_transform(ioawaitable_thing)` because the promise defines it.
3. `await_transform` returns a proxy awaiter with the standard one-argument `await_suspend(h)`.
4. Inside the proxy, `await_suspend(h)` reads `env` from the promise (set by the launch function via `set_environment` per the `IoRunnable` concept at line 412) and calls the original `ioawaitable_thing.await_suspend(h, env)`.

This is identical to the pattern `std::execution`'s `as_awaitable` uses, and to generators, `task<T>` from cppcoro, etc. No language change. No ABI change. The compiler only sees the proxy's one-argument `await_suspend`. The IoAwaitable concept simply describes the new I/O entry point (`await_suspend(h, env)`) - the proxy bridges to the compiler's expected interface.

The Challenge step had the resolution in its context window but rejected it because "the paper never shows the library wrapper." This is a model-level reasoning error; the paper does not need to show the wrapper because `await_transform` is a standard C++20 mechanism. A competent reviewer would recognize the pattern.

**Finding #136 - "IoAwaitable concept omits await_ready and await_resume" - INVALID**

The `IoAwaitable` concept is the protocol contract for the I/O entry point. The `IoRunnable` concept at lines 416-421 *does* require `await_resume()` (via the `result()` access in the promise). The proxy awaiter returned by `await_transform` synthesizes `await_ready` (returns false) and either forwards or synthesizes `await_resume`. The omission is a layered-design choice, not a defect.

**Finding #283 (DeepSeek's only survivor) - "Unsubstantiated Claim About Sender Composition and Data Loss" - VALID, but resolvable**

The paper at line 517 states a strong technical claim about senders and `[ec, n]` composition without showing the work, citing [10,11] (P4090R0, P4091R0). The substantiation exists in those companion papers, but Verify did not search them (P-5 above). If Verify had queried P4090R0 for this claim, the finding would have been closed.

**The four genuinely actionable findings across both runs:**

1. **Benchmark methodology gap** (paper line 197). The frame-allocator speedup table has no workload, iteration count, hardware, or statistics.
2. **SD-4 cost acknowledgment absent**. The paper does not address implementation burden, teaching cost, ABI surface, or documentation cost.
3. **`std::execution` integration story missing**. "Complementary" is asserted repeatedly with no concrete interop sketch.
4. **Comparison table at lines 499-517 is garbled** (markdown formatting damage).

The remaining "partially valid" findings are presentation/rigor critiques that a real reviewer would phrase as suggestions, not as structural defects.

---

## 4. Pipeline Issues (consolidated)

### Critical (verdict-determining)

| ID  | Step      | Issue                                                                                              |
| --- | --------- | -------------------------------------------------------------------------------------------------- |
| P-4 | 8 (post)  | `upgrade_gaps` does not strip stop words. 80-90% of gaps become "critical" by accident.            |
| P-5 | 9 Verify  | Only one companion paper queried even when multiple have author_overlap=1.0.                       |
| P-6 | 9 Verify  | `contradictions` field is populated but never read downstream.                                     |
| P-7 | 12 Analyze | Prompt promises "Research context from all 6 lenses" but code never passes it.                    |
| P-10 | 14 Challenge | Paper context is only ±5 lines; multi-paragraph resolutions don't fit.                          |
| P-12 | 14 Challenge | Verify's closed-gap evidence, contradictions, and confirmations are not shown to Challenge.    |

### Important (correctness-relevant)

| ID  | Step      | Issue                                                                                              |
| --- | --------- | -------------------------------------------------------------------------------------------------- |
| P-2 | 5 Decide  | Decide cannot see other chunks' evidence. Forces false "unsupported" on cross-chunk claims.        |
| P-3 | 6 Classify | Classify is text-blind. Severity and lens assigned from one sentence of context.                  |
| P-8 | 12 Analyze | Own-chunk gaps excluded from Analyze input. Causes finding duplication and lost gap-finding link. |
| P-9 | 13 Rationale | No dedup between Rationale findings and Analyze findings.                                       |
| P-11 | 14 Challenge | Scope items (which functionally are concessions) not passed to Challenge.                      |

### Minor (quality of output)

| ID  | Step      | Issue                                                                                              |
| --- | --------- | -------------------------------------------------------------------------------------------------- |
| P-1 | 4 Extract | "Skip terse fragments" criterion is too soft; 50% variance in claim counts.                       |

---

## 5. Concrete Pipeline Fixes (prioritized)

### Fix 1 (highest impact): Replace `upgrade_gaps` with stop-word-aware overlap

`upgrade_gaps` currently fires on almost every gap. Replace with:

```python
def upgrade_gaps(
    gaps_by_lens: dict[str, list[GapOutput]],
    central_claim: str,
    problem_statement: str,
) -> dict[str, list[GapOutput]]:
    """Post-Derive: upgrade gap severity if gap touches thesis content."""
    thesis_words = (
        set((central_claim + " " + problem_statement).lower().split())
        - _STOP_WORDS
    )
    result: dict[str, list[GapOutput]] = {}
    for lens, gaps in gaps_by_lens.items():
        new_list: list[GapOutput] = []
        for b in gaps:
            if b.severity in ("significant", "minor"):
                gap_words = set(b.gap.lower().split()) - _STOP_WORDS
                # tighten threshold: 4 content words, not 3
                overlap = thesis_words & gap_words
                if len(overlap) >= 4:
                    b = b.model_copy(update={"severity": "critical"})
            new_list.append(b)
        result[lens] = new_list
    return result
```

Expected outcome: critical gap counts drop from ~90% to ~20% of total gaps, which is realistic for actual thesis-touching gaps.

### Fix 2 (highest impact): Verify queries all co-author papers, not just one

```python
async def _custom_verify(state, ctx, spec):
    if ctx.embedder is None: return
    # Query every in-paperstore paper with author_overlap >= 0.5, in priority order
    candidates = [r for r in state.ref_pids
                  if r.in_paperstore and r.author_overlap >= 0.5]
    if not candidates: return
    candidates.sort(key=lambda r: -r.author_overlap)
    # Per-paper budget of N gaps; stop when all gaps closed or budget exhausted
    for companion in candidates[:4]:  # query top 4 co-author papers
        if not any(g.closed_by == 0
                   for lens_list in state.gaps_by_lens.values()
                   for g in lens_list):
            break  # all gaps closed
        await _verify_against_one_companion(state, ctx, spec, companion)
```

This would have closed DeepSeek's sender-composition finding (via P4090R0/P4091R0) and given Opus's Challenge a richer evidence base for finding #135 (via P4172R0's wrapper code).

### Fix 3 (highest impact): Analyze receives Verify's resolutions

In `_build_analyze_user_message`, add:

```python
# After the cross-chunk gaps block, add closed-gap evidence:
if state.verify and state.items:
    closed_evidence = [e for e in state.items.evidence
                       if e.source_pid]  # companion-paper evidence
    if closed_evidence:
        parts.append("\n## Companion paper evidence (already resolved by Verify)\n\n")
        for e in closed_evidence[:10]:
            parts.append(f"- [{e.id}] ({e.source_pid}) \"{e.quote}\" (line {e.line})\n")
if state.verify and state.verify.contradictions:
    parts.append("\n## Contradictions found in companion papers\n\n")
    for c in state.verify.contradictions:
        parts.append(f"- {c}\n")
if state.research:
    parts.append("\n## Research context\n\n")
    for lens, r in state.research.items():
        if r.findings:
            parts.append(f"### {lens}\n")
            for f in r.findings[:3]:
                parts.append(f"- {f.finding} (source: {f.source})\n")
```

This delivers what the prompt already advertises (P-7) and breaks the duplicate-finding cycle (P-8).

### Fix 4 (high impact): Challenge receives Verify state + wider context

```python
def _build_cross_exam_user_message(findings_batch, state, ctx):
    derive = state.derive
    items = state.items or CollectedItems()
    paper_lines = state.paper_md.splitlines()

    parts = [f"# Paper: {state.paper_id}\n\n",
             f"## Thesis: {derive.central_claim if derive else ''}\n",
             f"Scope: {derive.scope_boundary if derive else ''}\n\n"]

    # NEW: include concessions AND scope items
    if items.concessions:
        parts.append("## Concessions\n\n")
        for c in items.concessions:
            parts.append(f"- \"{c.quote}\" (line {c.line})\n")
    if items.scope:
        parts.append("\n## Scope statements (function as concessions)\n\n")
        for s in items.scope:
            parts.append(f"- \"{s.quote}\" (line {s.line})\n")

    # NEW: include Verify's closed gaps and contradictions
    if state.verify and state.verify.closes:
        parts.append("\n## Already resolved by Verify (do not re-raise)\n\n")
        for r in state.verify.closes:
            parts.append(f"- gap [{r.gap_id}]: \"{r.evidence_quote}\" (line {r.evidence_line})\n")

    parts.append("\n## Findings to cross-examine\n\n")
    for f in findings_batch:
        # ...existing per-finding block...

        # CHANGED: ±15 lines instead of ±5
        if f.line > 0 and paper_lines:
            start = max(0, f.line - 16)
            end = min(len(paper_lines), f.line + 15)
            context = format_numbered_lines(paper_lines, start + 1, end)
            parts.append(f"\n**Paper context (lines {start + 1}-{end}):**\n\n"
                         f"{ctx.inject_untrusted(context)}\n")
```

This fixes P-10, P-11, P-12 in one pass.

### Fix 5 (high impact): Decide receives evidence from the whole paper, not just its chunk

The cleanest fix is to add a second sub-step: after per-chunk Decide produces its initial verdict, run a single follow-up call that takes only the *unsupported* claims plus an evidence index and judges whether any other chunk's evidence supports them.

```python
async def _custom_decide(state, ctx, spec):
    # ... existing per-chunk decide produces raw_decisions ...
    # New: cross-chunk pass
    unsupported_claims = []
    for dec_output in state.raw_decisions:
        for d in dec_output.decisions:
            if not d.supported:
                # ... map back to claim quote ...
                unsupported_claims.append({...})
    if unsupported_claims:
        # Single call: "here are claims judged unsupported by their own chunk.
        # Here is the full evidence list from the entire paper. For each
        # unsupported claim, does any of this evidence support it?"
        await _cross_chunk_decide(state, ctx, spec, unsupported_claims)
```

This fixes P-2 and would have closed the "exactly three things" gap for both models by linking the claim in chunk 0 to the `io_env` struct evidence in chunk 2.

### Fix 6 (medium impact): Classify receives the chunk text

Modify `_custom_classify` to include the chunk text for each unsupported claim, not just the quote and reason:

```python
# Group unsupported by chunk_index, then per chunk include the chunk text once
unsupported_by_chunk: dict[int, list] = {}
for u in unsupported:
    unsupported_by_chunk.setdefault(u["chunk_index"], []).append(u)

parts = [f"# Paper: {state.paper_id}\n\n"]
for ci, items in sorted(unsupported_by_chunk.items()):
    chunk = state.chunk_map[ci]
    numbered = format_numbered_lines(paper_lines, chunk.start_line, chunk.end_line)
    parts.append(f"## Chunk {ci}: {chunk.heading}\n\n")
    parts.append(f"{ctx.inject_untrusted(numbered)}\n\n")
    parts.append(f"### Unsupported claims in this chunk\n\n")
    for u in items:
        parts.append(f"- (line {u['line']}) \"{u['quote']}\" - {u['reason']}\n")
    parts.append("\n")
```

This fixes P-3 and gives Classify real context for severity and lens assignment.

### Fix 7 (medium impact): Analyze includes own-chunk gaps with a clear instruction

Replace the `if b.chunk_index != chunk.index` filter with a partitioned section that includes both:

```python
own_gaps = [g for g in (state.gaps_by_lens or {}).get_all_flat() if g.chunk_index == chunk.index and g.closed_by == 0]
other_gaps = [g for g in ... if g.chunk_index != chunk.index and g.closed_by == 0]

if own_gaps:
    parts.append("\n## Gaps already identified in THIS chunk (do not duplicate; use as inputs)\n\n")
    for g in own_gaps:
        parts.append(f"- [{g.id}] [{g.severity}] {g.gap} (line {g.line})\n")
if other_gaps[:20]:
    parts.append("\n## Cross-chunk gaps (for context only)\n\n")
    ...
```

Then in the Analyze prompt itself, add an instruction:

> If a gap from "Gaps already identified in THIS chunk" maps to a structural concern you would have raised, reference it by ID and elaborate, do not produce a duplicate finding.

This fixes P-8.

### Fix 8 (medium impact): Dedup findings between Analyze and Rationale

In `_custom_rationale`, after `RationaleOutput` returns, dedup against existing findings:

```python
new_findings = list(result.findings)
existing_titles = {f.title.lower() for f in (state.findings or [])}
new_findings = [f for f in new_findings if f.title.lower() not in existing_titles]
# also: detect substring duplicates the way _dedup_items does for items
```

This fixes P-9.

### Fix 9 (low impact, high consistency): Tighten the Extract funnel

In `assay.md`, change the "claim" branch of the Extract funnel to:

> 7. claim - any remaining declarative sentence that meets ALL of: (a) makes a verifiable assertion about behavior, performance, design, or specification, (b) is grammatically self-contained, (c) is not a definition or label, (d) is not a stage direction phrase like "What follows is..." or "Here we show...".

This adds objective tests and shrinks the gap between liberal and strict extraction.

---

## 6. Are the Right Pieces Reaching the Right Place?

Question by question:

**Are the gaps being paired with the claims correctly?**

In Step 6 Classify - yes structurally; the gap is built from the claim that failed Decide. But the gap-question and severity are produced with no paper context, so the *content* of the pairing is degraded (P-3).

In Step 12 Analyze - **no**. Own-chunk gaps are filtered out before Analyze sees them (P-8). The claim → gap → finding chain is broken at this step, causing finding duplication.

**Is all the evidence being presented?**

No.

- Decide can see only its own chunk's evidence (P-2).
- Classify sees no evidence at all (P-3).
- Analyze does not receive Verify's closed-gap evidence (P-7).
- Challenge does not receive Verify's closes/confirmations/contradictions (P-12).
- Verify itself only queries one companion paper out of potentially four (P-5).

The contradictions list from Verify is dead state (P-6).

**Is one of the prompts missing data?**

Several:

- The Step 12 Analyze prompt advertises "Research context from all 6 lenses" but the code doesn't pass it.
- The Step 12 Analyze prompt references "the 25 test patterns" by number but the patterns themselves are not in the prompt body.
- The Step 14 Challenge prompt advertises "concession" as a kill but no concession items or scope items are passed (only the `concessions` list which is typically empty).
- The Step 14 Challenge prompt does not pass Verify's resolution state, so "Resolution" challenges can only succeed when the resolution happens to fit in the ±5 line window.

---

## 7. Conclusion

**Is the pipeline algorithm correct?**

The shape of the algorithm is correct. The eighteen-step structure (mechanical Pass 1 → Verify against companions → analytical Pass 2 → cross-examine → synthesize) is sound and matches the structure of how a careful WG21 reviewer would actually read a paper.

**Are there bugs?**

Twelve, ranked above. Three are critical (P-4 stop-word bug, P-5 one-companion verify, P-7+P-12 broken Analyze+Challenge contracts). Five are important (P-2, P-3, P-8, P-9, P-11). The rest are quality issues.

**Why do the two model runs disagree so violently?**

The model-level reasoning differences (DeepSeek strict on Decide, Opus generous on Decide; Opus generous on Challenge Concession kills, DeepSeek strict; Opus willing to call critical, DeepSeek not) are real and expected. But the pipeline does not damp these differences - it amplifies them. A single model mistake at Step 12 + Step 14 (false-positive critical finding) becomes a verdict-altering Major finding in Step 16 via the stop-word-naive thesis-overlap heuristic.

**What would the verdicts have been with the proposed fixes?**

Re-running the trace under Fixes 1-4 inclusive (the verdict-determining ones):

- **DeepSeek:** Verify would query P4090R0/P4091R0 in addition to P4172R0 and likely close gap 280's "Unsubstantiated Claim About Sender Composition" because P4090R0 is *explicitly* the "Sender I/O: A Constructed Comparison" companion. Result: 0 surviving findings → **Sound (High)**. This may be too generous; tightening Decide via Fix 5 would surface the legitimate benchmark methodology and SD-4 cost gaps that DeepSeek's extract pipeline let slip. Realistic verdict under Fixes 1-5: **Weakened (Medium)** with 2-3 surviving "actionable" findings.

- **Opus:** With Verify state visible to Challenge (Fix 4), finding #135 likely survives only as significant (Opus would still flag it as under-explained but not language-changing). With expanded Challenge context (±15 lines, Fix 4), finding #135 could be killed by Resolution because lines 231-261 contain the full explanation. Even if it survives at significant severity, `contradicts_thesis` no longer fires (because no critical finding overlaps thesis). Result: **Weakened (Medium)** with ~3-5 surviving findings covering the benchmark, SD-4, integration story, and table-formatting concerns.

**Both runs converge on Weakened (Medium) with the same 3-5 actionable findings.** This is the correct verdict for P4003R3 given the source paper: the protocol claim is sound, the load-bearing benchmark is presentation-thin, SD-4 cost is unaddressed, and the std::execution integration story is asserted not demonstrated.

The current pipeline reaches that answer with DeepSeek by luck (Verify missing companions left an unresolved gap that became a "Weakened" finding) and misses it with Opus because of cascade amplification (one false critical finding plus stop-word-naive `upgrade_gaps` plus naive `contradicts_thesis` plus naive `_thesis_overlap`).

The fixes above are not about making the LLMs smarter. They are about making the pipeline robust enough that intelligent disagreement between models at one step does not propagate to opposite verdicts at the final step.
