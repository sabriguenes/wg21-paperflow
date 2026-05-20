# Section-based chunking + classification study

Can we replace dissect's sentence-level claim extraction with
section-level classification to identify where findings live in a
paper?  This study validates the approach against the ad-hoc red team
report for P2300R10 (50 findings across 5 dimensions).

The study is **self-contained**.  It does NOT modify any existing
packages (dissect, paperstore, advocatus, pipeline, etc.).  It reads
paper markdown from the paperstore directory and uses the pipeline
classifier infrastructure for zero-shot scoring.

## Architecture under test

1. **Section chunking** - recursive heading-based split until each
   leaf section is under `max_tokens` (~2000).
2. **Section classification** - zero-shot NLI classifier on the first
   512 tokens of each section body, scored against 10 hypotheses.
   Produces a sections-by-hypotheses score matrix.
3. **Validation** - compare the score matrix against hand-labeled
   ground truth to check whether the classifier correctly identifies
   sections containing known findings.

No LLM calls.  The entire study runs on CPU using the local NLI
classifier (~5 seconds for a 6000-line paper).

## Layout

```
study/section-chunks/
- README.md                       this file
- section_chunker.py              recursive heading-based chunking
- section_classifier.py           zero-shot scoring (first 512 tok x 10 hypo)
- validate.py                     ground truth comparison
- data/
  - {pid}_sections.json           chunker output
  - {pid}_score_matrix.json       classifier score matrix
  - {pid}_ground_truth.json       hand-labeled findings
- results/
  - {pid}_findings.md             validation scorecard
```

## How to run

All scripts run from the repo root with the venv active.

```bash
# 1. Chunk the paper into semantic sections
python study/section-chunks/section_chunker.py P2300R10

# 2. Classify each section (~3-5s on CPU)
python study/section-chunks/section_classifier.py P2300R10

# 3. Validate against red team ground truth
python study/section-chunks/validate.py P2300R10

# All at once
python study/section-chunks/section_chunker.py P2300R10 && python study/section-chunks/section_classifier.py P2300R10 && python study/section-chunks/validate.py P2300R10

# Cross-validate on a different paper
python study/section-chunks/section_chunker.py P4003R3 && python study/section-chunks/section_classifier.py P4003R3
```

## Questions to answer

1. Does the 512-token zero-shot classifier correctly categorize
   P2300R10 sections?
2. Does the score matrix show clean separation between section types?
3. For each red team finding, does the score matrix flag the correct
   section(s) with the correct hypothesis(es)?
4. What fraction of the 50 ground truth findings have their source
   sections correctly identified by the classifier?
5. What is the minimum viable set of hypotheses?

## Ground truth

Derived from `cursor-context/reports/red-team-p2300r10.md`.  Each
finding is annotated with source line(s), finding type (local,
cross-ref, or absence), and which hypotheses should fire on the
source section(s).
