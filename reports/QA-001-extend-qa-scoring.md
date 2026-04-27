# Report: QA-001 Extend tomd Quality Scoring

Status: COMPLETE
Date: 2026-04-27
Plan: plans/QA-001-extend-qa-scoring.md
Owner: SG

## What was implemented

Two new QA checks added to `packages/tomd/src/tomd/lib/pdf/qa.py`:

1. **Mojibake detection** (`_count_mojibake`): two-layer approach using
   U+FFFD counting (zero false positives) and `ftfy.badness()` with
   threshold >= 3 (research-backed, ~1 false positive per 6M texts).
   Penalty capped at 20 points.

2. **Heading level skip detection** (`_heading_level_skips`): matches
   markdownlint MD001 semantics. Only ascending skips flagged (H2 -> H4),
   descending always allowed (H4 -> H2). Penalty capped at 15 points.

Both checks are purely additive: new fields on QAMetrics, new functions,
new penalty branches. No existing code was modified.

## Files changed

| File | Change |
|---|---|
| `packages/tomd/pyproject.toml` | Added `ftfy>=6.1` dependency (installed v6.3.1) |
| `packages/tomd/src/tomd/lib/pdf/qa.py` | Added `mojibake_count`, `heading_level_skips` fields, `_count_mojibake()`, `_heading_level_skips()` functions, 2 penalty branches in `_score()` |
| `packages/tomd/tests/test_qa.py` | Added `TestMojibake` (10 tests), `TestHeadingSkips` (6 tests) |

## Test results

37/37 tests passed. Zero failures, zero regressions.

| Test class | Tests | Status |
|---|---|---|
| TestPerfectScore | 4 | all pass |
| TestEmptyOutput | 2 | all pass |
| TestUncertainRegions | 2 | all pass |
| TestNoHeadings | 2 | all pass |
| TestUnfencedCode | 2 | all pass |
| TestMetadata | 3 | all pass |
| TestLowVariety | 2 | all pass |
| TestWordingExemption | 2 | all pass |
| TestTableDetection | 2 | all pass |
| **TestMojibake** (new) | **10** | **all pass** |
| **TestHeadingSkips** (new) | **6** | **all pass** |

### New test coverage

TestMojibake (10 tests):
- `test_replacement_char_detected`: U+FFFD always detected
- `test_latin1_mojibake_detected`: UTF-8-as-Latin-1 (e.g. Ã¤ for ä)
- `test_valid_umlauts_not_flagged`: real ä, ö, ü not triggered
- `test_valid_emoji_not_flagged`: ✅ ❌ 🔴 not triggered
- `test_math_symbols_not_flagged`: ≤ ≥ ∫ ∞ not triggered
- `test_cpp_template_syntax_not_flagged`: `<T>`, `<string>` not triggered
- `test_real_corpus_mojibake_pattern`: P3052R2 pattern (â + C1 controls for ✅)
- `test_single_unusual_char_not_flagged`: § not triggered (threshold boundary)
- `test_penalty_capped_at_20`: 50x FFFD, score stays >= 80
- `test_good_md_score_unchanged`: _GOOD_MD still scores 100

TestHeadingSkips (6 tests):
- `test_skip_detected`: ## -> #### is a skip
- `test_no_skip_ok`: ## -> ### is correct
- `test_descending_levels_ok`: ### -> ## allowed
- `test_multiple_skips_counted`: each skip counted independently
- `test_penalty_capped_at_15`: many skips, score stays >= 85
- `test_good_md_score_unchanged`: _GOOD_MD still scores 100

## Corpus verification (270 papers, 2026 mailing)

### Score distribution before vs. after

| Bucket | Before | After | Delta |
|---|---|---|---|
| 90-100 (good) | 235 (87.0%) | 232 (85.9%) | -3 |
| 70-89 | 32 (11.9%) | 34 (12.6%) | +2 |
| 50-69 | 3 (1.1%) | 4 (1.5%) | +1 |
| 0-49 | 0 (0.0%) | 0 (0.0%) | 0 |

### Real issues discovered

| Paper | Score before | Score after | New issues found |
|---|---|---|---|
| P3904R1 | 85 | 65 | 25 mojibake sequences (previously invisible) |
| P2728R11 | 100 | 80 | 16 mojibake sequences (previously invisible) |
| P3596R0 | 61 | 51 | 2 mojibake sequences |
| P2956R2 | 85 | 80 | 1 mojibake sequence |
| P3948R1 | 77 | 72 | 1 heading level skip |

Zero false positives on the 232 files that still score 90-100.

## Research findings

Two rounds of parallel web research (10 foragers total) grounded the
implementation decisions. Full details in `plans/QA-001-extend-qa-scoring.md`.

### Round 1 (pre-implementation)

1. **ftfy over custom regex**: ftfy.badness() uses ~400 character classes
   tuned over years. Custom byte-pattern regex false-positives on valid
   multi-byte Unicode. Source: https://ftfy.readthedocs.io/en/latest/heuristic.html

2. **U+FFFD as zero-cost first gate**: replacement character always means
   bytes were irrecoverably lost. Zero false positives.
   Source: https://bytetunnels.com/posts/some-characters-could-not-be-decoded-fixing-replacement-character-errors/

3. **Heading skip matches MD001**: our approach matches the canonical
   markdownlint rule exactly. Source: https://github.com/DavidAnson/markdownlint/blob/main/doc/md001.md

4. **Mistune AST complete for top-level headings**: ATX and setext headings
   appear as top-level tokens. Nested headings (in blockquotes) are in
   children arrays, but WG21 papers never use nested headings.

5. **ftfy not Markdown-aware**: math symbols could interact with mojibake
   patterns. Mitigated by threshold >= 3.

### Round 2 (hardening)

6. **U+FFFD inflates ftfy.badness()**: FFFD is in ftfy's "bad" category.
   Our code counts FFFD separately AND badness() counts it again. This is
   correlated double-counting. Both signals are real, and the penalty cap
   prevents runaway penalties.
   Source: https://github.com/LuminosoInsight/python-ftfy/blob/master/ftfy/badness.py

7. **ftfy.badness() is a raw match counter**: each regex match = +1, no
   weighting. Score 1-2 on long text is noise. Score 3+ is almost certainly
   real corruption. Our threshold of >= 3 is well calibrated.
   Source: https://ftfy.readthedocs.org/en/latest/heuristic.html

8. **MD001 front_matter_title deviation**: since markdownlint fix #1617,
   YAML `title:` counts as implicit h1. Our implementation does NOT do
   this. For WG21 papers this is correct (they always start with `##`).
   Source: https://github.com/DavidAnson/markdownlint/issues/1613

9. **Penalty capping is standard practice**: Gradescope, Autorubric, and
   rubric design literature all use per-category caps. Our 20/100 mojibake
   cap aligns with the 40%/10% rule.
   Source: https://help.gradescope.com/article/5uxa8ht1a2-instructor-assignment-grade-submissions

## Known limitations

- ftfy is not Markdown-aware (does not skip fenced code blocks)
- Only top-level headings checked (nested in blockquotes/lists skipped)
- No front_matter_title support (documented deviation from full MD001)
- Double-counting between FFFD count and ftfy.badness() (mitigated by cap)

## Next steps (out of scope for QA-001)

- Table integrity checks (ragged columns, header-only tables)
- mpark/wg21 PDF parsing support (see plans/QA-002-mpark-wording-support.md)
- Reference comparison against hand-written gold-standard papers
