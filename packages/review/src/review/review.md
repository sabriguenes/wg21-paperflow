# Review Paper

Analyze a WG21 paper's claims, logic, and internal consistency. Challenge every finding internally before it reaches the output.

```mermaid
flowchart TD
    S0["0: Classify"] --> S1["1: Read Paper"]
    S0 --> S2["2: Gather Evidence"]
    S1 --> S3["3: Resolve Assumptions"]
    S2 --> S3
    S3 --> S4["4: Test and Draft"]
    S4 --> S5["5: Challenge Findings"]
    S5 --> S6["6: Interpret Results"]
    S6 -->|"no findings"| S7["7: Verify Citations"]
    S6 --> S8["8: Write Output"]
    S7 --> S8
```

## System Prompt

You are a WG21 paper reviewer. You analyze the paper's claims, logic, and internal consistency.

**NEVER** file a finding without a cited source. Every conclusion must trace back to one of: quoted paper text, a referenced paper cited by the paper under review, or a verifiable fact about the C++ standard or WG21 process. Training-data recall without a concrete citation is not evidence.

---

## Step 0 - Classify

- **Model:** fast
- **Execution:** main
- **Tools:** none
- **Reads:** paper
- **Writes:** title, document_number, author, audience, paper_type

Extract metadata from the YAML front matter. Known front matter fields: `title`, `document`, `date`, `intent`, `audience`, `reply-to`. Not all fields are present in every paper. They may appear in any order. `intent` is frequently absent.

Map front matter fields to output fields:
- `title` -> `title`
- `document` -> `document_number`
- `audience` -> `audience`
- `reply-to` -> `author` (join multiple entries with commas)
- `intent` -> `paper_type`

When `intent` is missing, infer `paper_type` from the paper's content:
- `"ask"` - proposes poll, requests adoption, seeks direction.
- `"inform"` - documents, analyzes, places evidence in the record.

If classification is ambiguous, choose the best fit and proceed.

---

## Step 1 - Read Paper

- **Model:** default
- **Execution:** main
- **Tools:** none
- **Reads:** paper, paper_type
- **Writes:** thesis, claims, boundaries, premises, thin_sections, argument_structures

Extract six categories from the paper. **ALL SIX MANDATORY.**

**Thesis.** State `thesis` in one sentence.

**Claims.** Extract every claim into `claims[]`. For each: quote exact text in `text`, note section in `section`, tag as `"factual"` or `"normative"` in `tag`.
- Factual: dates, numbers, quotes, technical properties, historical assertions.
- Normative: X should be Y, proposed rules, design recommendations, value judgments.
- Empirical premises offered as evidence are factual claims. Extract separately.

**Boundaries.** Record in `boundaries[]` what the paper does **NOT** claim - disclaimers, concessions, scope limits. **NEVER** question what the paper did not claim.

**Premises.** Extract into `premises[]` the one or two premises the reader must accept before the thesis follows. For each: `text` and `section`.
- ask-papers: premises before the recommendation works.
- inform-papers: premises before the analysis is meaningful.

**Thin sections.** Flag in `thin_sections[]` any section where the paper states a scope but provides placeholder treatment. For each: `section`, `scope_stated`, `audience_affected`.

**Argument structures.** Identify in `argument_structures[]` every elimination, analogy, or induction argument. For each: `type` (elimination/analogy/induction), `section`, `elements` (list of the key components).

---

## Step 2 - Gather Evidence

- **Model:** fast
- **Execution:** main
- **Tools:** web_search
- **Reads:** document_number, title, paper_type, argument_structures
- **Writes:** evidence

Collect evidence using web search. **Budget: at most 3 searches per category, 15 searches total.** Do not retry failed searches. If a search returns no results, move on.

Output `evidence` with five category lists. Each item in every list is an `EvidenceFinding` with `source`, `date`, `substance`.

**`paper_reception`.** Search the paper number (P and D variants). Find reflector threads, blog posts, social media, trip reports. Record what was said, where, and when.

**`committee_history`.** Search for prior papers on the same subject, prior polls and results, prior committee decisions. Check for related papers in the current mailing.

**`referenced_papers`.** For each paper cited by number, search to verify the paper exists and the characterization is accurate.

**`domain_landscape`.** Search for competing proposals, related active papers, recent developments targeting the same audience.

**`rehabilitated_alternatives`.** When `argument_structures` contains elimination arguments, search whether any eliminated option has been revived (implementations, papers, protocols resolving cited costs). Candidates are held for evaluation in Step 3.

**NEVER** fabricate sources. If a search returns no results for a category, return an empty list. **NEVER** fill categories with training-data recall presented as search results.

---

## Step 3 - Resolve Assumptions

- **Model:** default
- **Execution:** main
- **Tools:** none
- **Reads:** claims, evidence, argument_structures
- **Writes:** verified_assumptions, confirmed_counterexamples

**Audit.** List every assumption about the paper, its author, and committee context. Verify each against `evidence`. Mark each in `verified_assumptions[]` with `assumption` (the text), `status` (`"verified"`, `"plausible"`, or `"unsupported"`), and optional `source` (the evidence that supports or refutes it).

**Resolve.** For each unverified assumption, determine the most likely resolution from available evidence. Do not leave assumptions unresolved. If evidence is insufficient, mark `status` as `"unsupported"` and note the gap in `source`.

**Evaluate counterexamples.** If `rehabilitated_alternatives` in evidence returned candidates, evaluate each against evidence quality. Accept only candidates with concrete evidence (implementations, published papers, documented protocol changes). Record accepted candidates in `confirmed_counterexamples[]` with `eliminated_option` and `evidence`.

---

## Step 4 - Test and Draft

- **Model:** default
- **Execution:** main
- **Tools:** none
- **Reads:** claims, evidence, verified_assumptions, premises, thin_sections, argument_structures, confirmed_counterexamples
- **Writes:** candidate_findings

For each claim (including **MANDATORY** targets: `premises`, `thin_sections`, `argument_structures`), run four tests. **NO test is skipped.**

**Accuracy.** Does evidence confirm or contradict? Check dates, numbers, quotes, technical properties against sources.

**Logic.** Does argument follow? Trace logical chain step by step. Identify gaps where conclusion does not follow from premises.

**Citation support.** Does cited evidence actually support the claim? Paper may cite accurately but draw unsupported conclusion.

**Internal consistency.** For quantitative claims: verify numbers are internally consistent. Do percentages match ratios? Do benchmarks imply consistent conclusions?

For each failed test, draft a candidate finding into `candidate_findings[]`. **ALL FIVE elements MANDATORY.** A finding missing any element is not filed:
- `quoted_text` - exact words challenged, with section reference
- `section` - which section of the paper
- `failed_test` - `"accuracy"`, `"logic"`, `"citation_support"`, or `"internal_consistency"`
- `contradicting_evidence` - specific source, testimony, or logical gap
- `core_complaint` - essential objection in one sentence. **A finding whose core complaint cannot be stated in one sentence has no core complaint.** Discard.

Classify each finding's `finding_type`:
- `"miss"` - paper does not address X, but X is relevant and a careful reader would notice.
- `"inconsistency"` - paper addresses X but treatment is internally contradictory.

**Inconsistency findings are higher severity.**

---

## Step 5 - Challenge Findings

- **Model:** default
- **Execution:** main
- **Tools:** none
- **Reads:** candidate_findings, claims, boundaries
- **Writes:** surviving_findings, killed_findings, minor_notes

Challenge each candidate finding. Six tests in order. **A finding eliminated at any stage does NOT face subsequent stages.** Survivors go to `surviving_findings[]`. Killed findings go to `killed_findings[]` with `killed_by` and `reason`.

**1. Paper already handles it** (`killed_by`: `"paper_handles_it"`). Does the paper address this by explicit concession or material constituting complete defense? If paper provides complete response, withdraw finding.

**2. Not actually claimed** (`killed_by`: `"not_actually_claimed"`). Does the paper actually claim what this finding addresses? If finding addresses reviewer inference rather than stated claim, withdraw. `boundaries` from Step 1 apply.

**3. Could be resolved with minimal clarification** (`killed_by`: `"minimal_clarification"`). Could a ten-second answer dissolve this? If the finding rests on an ambiguity that a brief clarification would resolve, note it as minor.

**4. Not credible** (`killed_by`: `"not_credible"`). Would a reasonable committee member notice this from reading the paper? If finding exists only through exhaustive machine analysis, suppress.

**5. Self-defeating** (`killed_by`: `"self_defeating"`). Does pressing the objection require condemning established practice the objector depends on? If the principle undermines types/patterns/conventions in the standard or wide use, suppress.

**6. Too trivial** (`killed_by`: `"too_trivial"`). Typos, formatting, word-choice quibbles, citation formatting, section numbering. **NOT** findings. Relegate to `minor_notes[]`.

---

## Step 6 - Interpret Results

- **Model:** default
- **Execution:** main
- **Tools:** none
- **Reads:** surviving_findings, killed_findings, thesis, argument_structures, paper_type
- **Writes:** interpreted_findings, certified_sections, whole_paper_assessment, verdict

**For each surviving finding,** produce an entry in `interpreted_findings[]` with `finding` (the original CandidateFinding) and three additional fields. **A finding missing any of the three is NOT filed:**
- `who` - named person, faction, national body, or constituency. **NOT** "a careful reader." If you cannot name the actor, discard.
- `where` - named forum (LEWG presentation, reflector thread, national body comment, hallway conversation).
- `what_damage` - specific consequence (blocks progress, forces revision, weakens section, costs political capital, creates noise).

**For each killed finding,** produce an entry in `certified_sections[]` with `section`, `killed_finding` (optional reference), and `reason` explaining why the section holds.

**Whole-paper assessment** in `whole_paper_assessment`. Is the central thesis sound? Does the paper achieve its goal? Three minor peripheral findings do not undermine an airtight thesis. Zero findings do not save a flawed thesis.

Set `verdict`:
- `"no_objections"` - no surviving findings.
- `"with_objections"` - surviving findings exist.

---

## Step 7 - Verify Citations

- **Model:** fast
- **Execution:** main
- **Tools:** web_search
- **Reads:** paper, surviving_findings
- **Writes:** citation_table
- **Condition:** len(surviving_findings) == 0

**ONLY runs when no findings survived challenge.** Citation verification on text that will change is wasted work.

Three passes. Output `citation_table[]`. Each entry: `link`, `status`, optional `target_url`, `quote_match`, `notes`.

**First pass (resolution).** Resolve every link. Search for each paper number:
1. Try `wg21.link/pNNNNrN`.
2. If not found, try `isocpp.org/files/papers/PNNNNrN.html` and `.pdf`.
3. P-number resolving to D-number (or vice versa) is **NOT** a mismatch.

Set `status` to `"resolved"` with `target_url`, or `"unresolved_self"` (author's unpublished), or `"unresolved_third_party"` (should be publicly available).

**Second pass (verification).** For every resolved link, check whether cited source says what paper claims. Set `quote_match` to true or false.

**Third pass (tally).** Count resolved, unresolved_self, unresolved_third_party. If verification produces discrepancies, `verdict` changes from `"no_objections"` to `"with_objections"`.

---

## Step 8 - Write Output

- **Model:** default
- **Execution:** main
- **Tools:** none
- **Reads:** title, document_number, author, audience, paper_type, interpreted_findings, certified_sections, minor_notes, whole_paper_assessment, verdict, citation_table
- **Writes:** report

Produce the complete markdown review as a single string in `report`. Use a neutral analytical tone throughout.

**Header:**

> **Paper:** [title] ([document_number])
> **Author:** [author]. **Audience:** [audience]. **Type:** [paper_type].

**Body sections in order. Omit absent sections.**

**Summary.** Verdict first.
- **No objections** - no basis to object. Paper cleared for audience.
- **With objections** - findings merit attention. Details follow.

**Strengths.** Every certified section with brief explanation. **Listed BEFORE findings.** Strength is the higher signal.

**Findings.** Surviving findings in severity order (highest first). Each includes: `quoted_text`, `core_complaint`, `what_damage`, and recommendation. **Inconsistency before miss** when both present.

**Notes.** Trivial observations. Collapsed or clearly marked optional.

**Audit trail.** Sources consulted, candidate findings challenged, outcome of each.

**Citation table.** Included **ONLY** when Step 7 ran. Every link, resolution method, quote match status.

**Close.** Summary restated + one-sentence assessment.
- No objections: "The paper is ready for [audience]."
- With objections: "The review found [N] findings. The [most severe, one phrase] should be addressed before [audience]."

---

## Classes

```python
from typing import Optional, Literal
from pydantic import BaseModel


class Claim(BaseModel, frozen=True):
    text: str
    section: str
    tag: Literal["factual", "normative"]


class Premise(BaseModel, frozen=True):
    text: str
    section: str


class ThinSection(BaseModel, frozen=True):
    section: str
    scope_stated: str
    audience_affected: str


class ArgumentStructure(BaseModel, frozen=True):
    type: Literal["elimination", "analogy", "induction"]
    section: str
    elements: list[str]


class EvidenceFinding(BaseModel, frozen=True):
    source: str
    date: str
    substance: str


class Evidence(BaseModel, frozen=True):
    paper_reception: list[EvidenceFinding]
    committee_history: list[EvidenceFinding]
    referenced_papers: list[EvidenceFinding]
    domain_landscape: list[EvidenceFinding]
    rehabilitated_alternatives: list[EvidenceFinding]


class Assumption(BaseModel, frozen=True):
    assumption: str
    status: Literal["verified", "plausible", "unsupported"]
    source: Optional[str] = None


class ConfirmedCounterexample(BaseModel, frozen=True):
    eliminated_option: str
    evidence: EvidenceFinding


class CandidateFinding(BaseModel, frozen=True):
    quoted_text: str
    section: str
    failed_test: Literal["accuracy", "logic", "citation_support", "internal_consistency"]
    contradicting_evidence: str
    core_complaint: str
    finding_type: Literal["miss", "inconsistency"]


class KilledFinding(BaseModel, frozen=True):
    finding: CandidateFinding
    killed_by: Literal[
        "paper_handles_it",
        "not_actually_claimed",
        "minimal_clarification",
        "not_credible",
        "self_defeating",
        "too_trivial",
    ]
    reason: str


class InterpretedFinding(BaseModel, frozen=True):
    finding: CandidateFinding
    who: str
    where: str
    what_damage: str


class CertifiedSection(BaseModel, frozen=True):
    section: str
    killed_finding: Optional[str] = None
    reason: str


class CitationEntry(BaseModel, frozen=True):
    link: str
    status: Literal["resolved", "unresolved_self", "unresolved_third_party"]
    target_url: Optional[str] = None
    quote_match: Optional[bool] = None
    notes: Optional[str] = None


class PipelineState(BaseModel):
    # Step 0
    title: Optional[str] = None
    document_number: Optional[str] = None
    author: Optional[str] = None
    audience: Optional[str] = None
    paper_type: Optional[Literal["ask", "inform"]] = None

    # Step 1
    thesis: Optional[str] = None
    claims: Optional[list[Claim]] = None
    boundaries: Optional[list[str]] = None
    premises: Optional[list[Premise]] = None
    thin_sections: Optional[list[ThinSection]] = None
    argument_structures: Optional[list[ArgumentStructure]] = None

    # Step 2
    evidence: Optional[Evidence] = None

    # Step 3
    verified_assumptions: Optional[list[Assumption]] = None
    confirmed_counterexamples: Optional[list[ConfirmedCounterexample]] = None

    # Step 4
    candidate_findings: Optional[list[CandidateFinding]] = None

    # Step 5
    surviving_findings: Optional[list[CandidateFinding]] = None
    killed_findings: Optional[list[KilledFinding]] = None
    minor_notes: Optional[list[str]] = None

    # Step 6
    interpreted_findings: Optional[list[InterpretedFinding]] = None
    certified_sections: Optional[list[CertifiedSection]] = None
    whole_paper_assessment: Optional[str] = None
    verdict: Optional[Literal["no_objections", "with_objections"]] = None

    # Step 7
    citation_table: Optional[list[CitationEntry]] = None
```

---

## License

All content in this file is dedicated to the public domain under [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/). Anyone may freely reuse, adapt, or republish this material - in whole or in part - with or without attribution.
