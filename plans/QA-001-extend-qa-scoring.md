# QA-001: Extend tomd Quality Scoring

Status: PLANNING
Created: 2026-04-27
Updated: 2026-04-27 (post-research)
Owner: SG

## Objective

Extend the tomd QA scorer (`packages/tomd/src/tomd/lib/pdf/qa.py`) with new
checks for mojibake detection and heading level validation. Use a dry-run-first
approach: report findings without changing scores, then integrate after
verification on the 2026 corpus.

**Constraint: all changes are additive.** No existing test classes, methods,
functions, or scoring logic is modified. New checks are new fields, new
functions, new penalty branches, new test classes. Existing tests must pass
unchanged after the extension.

## Research Findings (2026-04-27)

Five parallel research foragers investigated detection methods, false-positive
risks, and tooling. Key findings that shaped this plan:

### 1. ftfy over custom regex (Decision)

ftfy's `badness()` function IS a hand-tuned regex over ~400 Unicode character
classes, optimized over years with a false-positive rate of ~1 per 6 million
texts. Writing our own byte-pattern regex (the original plan proposed
`[\xc0-\xdf][\x80-\xbf]`) is dangerous: byte-oriented regex on Unicode strings
can match continuation bytes of valid multi-byte characters, producing false
positives on author names with diacritics, math symbols, and C++ template
syntax.

Sources:
- https://ftfy.readthedocs.io/en/latest/heuristic.html
- https://github.com/rspeer/python-ftfy/blob/main/CHANGELOG.md
- https://ftfy.readthedocs.io/en/latest/config.html

### 2. U+FFFD as zero-cost first gate (Decision)

The Unicode replacement character U+FFFD appears when Python's codec hits
invalid bytes under errors='replace'. Its presence always means bytes were
irrecoverably lost. This is a zero-dependency, zero-false-positive check
that runs before the heavier ftfy analysis.

Source: https://bytetunnels.com/posts/some-characters-could-not-be-decoded-fixing-replacement-character-errors/

### 3. Heading skip matches markdownlint MD001 (Confirmed)

Our heading-skip approach (flag when level increases by >1, allow decreases)
matches exactly the canonical markdownlint MD001 rule and W3C WAI guidance.
The rule is binary (on/off), no tolerance levels. Decreasing levels (closing
a subsection, e.g. H4 -> H2) is always allowed.

Sources:
- https://github.com/DavidAnson/markdownlint/blob/main/doc/md001.md
- https://www.w3.org/WAI/tutorials/page-structure/headings/

### 4. Mistune AST is complete for top-level headings (Confirmed)

Mistune v3 with renderer='ast' yields all ATX and setext headings as
{'type': 'heading'} tokens in document order. Edge case: headings inside
blockquotes or list items are nested in 'children', not at top level.
For WG21 papers this is irrelevant (headings never appear inside
blockquotes), but the code comment documents this limitation.

Sources:
- https://github.com/lepture/mistune/blob/master/src/mistune/block_parser.py
- https://github.com/lepture/mistune/issues/217

### 5. ftfy is NOT Markdown-aware (Risk noted)

ftfy's badness regex has no concept of fenced code blocks or inline code.
Math symbols (<=, >=, infinity, integral) are in ftfy's 'numeric' category
and could theoretically interact with mojibake patterns. Mitigation: we use
badness() as a score (int), not is_bad() (bool). A badness of 1-2 on a
large document is noise; we only penalize when badness >= 3.

Source: https://ftfy.readthedocs.io/en/latest/_modules/ftfy/badness.html

## Reference Fixtures

Two hand-written papers from `cppalliance/wg21-papers` serve as gold-standard
references. They follow the conventions documented in
`source/CLAUDE.md` (37 rules) and `paperworks/README.md`.

### Fixture 1: D4036 (info paper, general structure)

- Source: https://raw.githubusercontent.com/cppalliance/wg21-papers/master/source/05-may/d4036-why-not-span.md
- GitHub: https://github.com/cppalliance/wg21-papers/blob/master/source/05-may/d4036-why-not-span.md
- Size: ~22 KB
- Covers: YAML front matter (6 fields), heading hierarchy (## and ###),
  5 markdown tables, fenced C++ code blocks, superscript citations,
  numbered references, unordered lists, --- dividers
- Does NOT cover: wording sections (not applicable to info papers)
- Selection rationale: exercises every structural element relevant to QA
  validation in a single file of manageable size

### Fixture 2: P2583R3 (ask paper, wording sections)

- Source: https://raw.githubusercontent.com/cppalliance/wg21-papers/master/archive/p2583r3-symmetric-transfer.md
- GitHub: https://github.com/cppalliance/wg21-papers/blob/master/archive/p2583r3-symmetric-transfer.md
- Published PDF: https://isocpp.org/files/papers/P2583R3.pdf
- Size: ~58 KB
- Authors: Mungo Gill, Vinnie Falco
- Covers: :::wording divs (unchanged spec text), :::wording-add divs
  (new text), `<ins>` / `<del>` inline markup, `<pre><code>` code diffs
  with inline changes, all general structure elements from Fixture 1
- Note from Mungo (2026-04-27): "not every paper uses strikeout font for
  removals - they just use red and green text"
- Selection rationale: recommended by Mungo Gill to cover wording section
  format that many WG21 papers use

### Style Rules Reference

- source/CLAUDE.md in wg21-papers repo (37 rules for paper formatting)
- Key rules for QA: Rule 1 (ASCII only), Rule 2 (HTML entities for diacritics),
  Rule 3 (no em-dashes), Rule 9 (YAML front matter), Rule 21-23 (wording divs),
  Rule 26 (abstract brutal summary), Rule 30-32 (references format)
- ASCII/entity rules apply to hand-written papers only, NOT to tomd converter
  output. tomd preserves original Unicode from source PDFs.

## Phase 1: Dry-Run Script

Create `packages/tomd/scripts/qa_dryrun.py` that:

1. Loads all `.md` files from `data/` (or --workspace-dir)
2. Runs new checks only (mojibake via ftfy, heading skips) against each file
3. Prints report: which files flagged, what detected, what penalty would apply
4. Does NOT modify qa.py or any scores

Purpose: see what the new checks catch on real data before touching
production code. Identify false positives before integration.

## Phase 2: Mojibake Detection

### What it catches (broken characters only)

| Broken Input | Expected (correct) | Pattern |
|---|---|---|
| U+FFFD | (any) | Unicode replacement character (bytes lost) |
| Ã¤ | ä | UTF-8 bytes decoded as Latin-1 |
| Ã¶ | ö | UTF-8 bytes decoded as Latin-1 |
| Ã¼ | ü | UTF-8 bytes decoded as Latin-1 |
| â✅ | ✅ | 3-byte mojibake (as seen in P3052R2) |

### What it does NOT flag

- Valid ä, ö, ü, ß in German text (correct Unicode)
- Valid emoji, arrows, mathematical symbols (correct Unicode)
- CJK, Cyrillic, Greek characters (correct Unicode)
- C++ template syntax like `<T>`, `<string>` (not encoding issues)

### Implementation

File: `packages/tomd/src/tomd/lib/pdf/qa.py`

New dependency: `ftfy` (add to `packages/tomd/pyproject.toml`)

New field on QAMetrics:
- `mojibake_count: int = 0`

New function:
```python
def _count_mojibake(md_text: str) -> int:
    """Count encoding corruption signals in the markdown text.

    Two-layer detection:
    1. U+FFFD (replacement character): always means bytes were lost
       during decoding. Zero false positives.
    2. ftfy.badness(): scores unlikely Unicode sequences that indicate
       UTF-8 decoded as Latin-1/CP-1252. Uses ~400 character classes
       tuned over years with ~1 false positive per 6M texts.
       We use the integer score, not the boolean is_bad(), because
       is_bad() has length-dependent false-positive rate on long
       technical documents.

    We require badness >= 3 to flag, because a score of 1-2 on a
    large document with math symbols or diacritics can be noise.
    Research: https://ftfy.readthedocs.io/en/latest/heuristic.html
    """
```

New penalty in `_score()`:
```python
# Mojibake: encoding corruption is always a conversion bug.
# Capped at 20 to avoid dominating the score on documents
# with a single corrupted paragraph.
if m.mojibake_count > 0:
    penalty = min(20, 5 * m.mojibake_count)
    score -= penalty
    issues.append(f"{m.mojibake_count} mojibake sequences")
```

### Tests

File: `packages/tomd/tests/test_qa.py`, new class `TestMojibake`:

```python
class TestMojibake:
    """Mojibake detection: broken encoding, not valid Unicode.

    Uses ftfy.badness() for detection. Research showed custom
    byte-pattern regex produces false positives on multi-byte
    Unicode (diacritics, math symbols, C++ templates).
    See plans/QA-001-extend-qa-scoring.md, Research Finding #1.
    """

    def test_replacement_char_detected(self):
        """U+FFFD always means bytes were irrecoverably lost."""

    def test_latin1_mojibake_detected(self):
        """UTF-8 decoded as Latin-1 produces sequences like Ã¤ for ä."""

    def test_valid_umlauts_not_flagged(self):
        """Real ä, ö, ü are valid Unicode, not mojibake."""

    def test_valid_emoji_not_flagged(self):
        """Emoji like ✅ are valid Unicode, not mojibake."""

    def test_math_symbols_not_flagged(self):
        """Math symbols (≤, ≥, ∞, ∫) must not trigger mojibake.
        ftfy classifies these as 'numeric' category which could
        interact with mojibake patterns on long documents.
        See plans/QA-001-extend-qa-scoring.md, Research Finding #5."""

    def test_cpp_template_syntax_not_flagged(self):
        """C++ template syntax like <T>, <string> must not trigger.
        Research showed generic Unicode heuristics false-positive
        on angle brackets in technical text."""

    def test_good_md_score_unchanged(self):
        """Existing _GOOD_MD fixture must still score 100."""
```

### Code Comments Required

Every new function and penalty branch MUST include a comment explaining:
- WHY this approach was chosen (reference the research finding number)
- WHAT it does NOT catch (known limitations)
- WHERE the decision is documented (link to this plan file)

## Phase 3: Heading Level Skip Detection

### What it catches

Heading jumps like `## H2` followed by `#### H4` (skipping H3).
This matches markdownlint MD001 (heading-increment) exactly.

Rules (from MD001 + W3C WAI):
- Only ASCENDING skips are flagged (H2 -> H4 is a skip)
- DESCENDING is always allowed (H4 -> H2 closes a subsection)
- First heading in document is exempt (no prior heading to compare)

### Implementation

File: `packages/tomd/src/tomd/lib/pdf/qa.py`

New field on QAMetrics:
- `heading_level_skips: int = 0`

New function:
```python
def _heading_level_skips(tokens: list[dict]) -> int:
    """Count heading level skips (ascending only).

    Matches markdownlint MD001 (heading-increment) semantics:
    only flags when heading level increases by more than 1.
    Decreasing levels (closing a subsection) are always allowed.
    Source: https://github.com/DavidAnson/markdownlint/blob/main/doc/md001.md

    Limitation: only scans top-level tokens. Headings nested inside
    blockquotes or list items (in 'children' arrays) are not checked.
    For WG21 papers this is acceptable because headings never appear
    inside blockquotes. If this assumption changes, add recursive
    traversal of 'children' arrays.
    Source: https://github.com/lepture/mistune/issues/217
    """
```

New penalty in `_score()`:
```python
# Heading level skips: matches markdownlint MD001.
# A skip usually means the converter mis-detected heading depth.
# Capped at 15 because heading structure is important but not
# as severe as encoding corruption (mojibake).
if m.heading_level_skips > 0:
    penalty = min(15, 5 * m.heading_level_skips)
    score -= penalty
    issues.append(f"{m.heading_level_skips} heading level skips")
```

### Tests

File: `packages/tomd/tests/test_qa.py`, new class `TestHeadingSkips`:

```python
class TestHeadingSkips:
    """Heading level skip detection, matching markdownlint MD001.

    Only ascending skips are flagged (H2 -> H4). Descending is
    always allowed (H4 -> H2 closes a subsection).
    See plans/QA-001-extend-qa-scoring.md, Research Finding #3.
    """

    def test_skip_detected(self):
        """## followed by #### (skipping ###) is a skip."""

    def test_no_skip_ok(self):
        """## followed by ### is correct, no penalty."""

    def test_descending_levels_ok(self):
        """### followed by ## (going back up) is always allowed.
        W3C WAI explicitly permits skipping ranks when closing
        subsections."""

    def test_multiple_skips_counted(self):
        """Each skip is counted independently."""

    def test_good_md_score_unchanged(self):
        """Existing _GOOD_MD fixture must still score 100."""
```

## Phase 4: Integration and Verification

1. Add `ftfy` to `packages/tomd/pyproject.toml` dependencies
2. Add new fields to QAMetrics (defaults ensure backward compatibility)
3. Wire checks into compute_metrics()
4. Add penalties to _score()
5. Run: `uv run pytest packages/tomd/tests/` (ALL existing tests must pass)
6. Run QA on full 2026 corpus, compare scores before/after
7. Document results in `reports/QA-001-results.md`

## Safety

The QA scorer is purely functional: compute_metrics(md_text) takes a string,
returns a dataclass. No side effects, no file writes, no database access.

New checks are safe because:
- New fields get default values (0), so existing code is unaffected
- New penalties only fire on new conditions
- The _GOOD_MD test fixture has no mojibake and no heading skips
- Existing tests continue to pass unchanged
- Dry-run validates on real data before integration
- ftfy.badness() threshold of >= 3 avoids noise on long documents

**No existing code is modified.** All changes are additive:
- New fields appended to QAMetrics dataclass
- New functions added (not replacing existing ones)
- New penalty branches added to _score() (not modifying existing branches)
- New test classes added (not touching existing test classes)

## Files Changed

| File | Change | Existing Code Touched? |
|---|---|---|
| `packages/tomd/pyproject.toml` | Add ftfy dependency | No (append to deps list) |
| `packages/tomd/src/tomd/lib/pdf/qa.py` | Add fields, functions, penalties | No (all additive) |
| `packages/tomd/tests/test_qa.py` | Add TestMojibake, TestHeadingSkips | No (new classes only) |
| `packages/tomd/scripts/qa_dryrun.py` | New file | N/A (new file) |
| `reports/QA-001-results.md` | New file | N/A (new file) |

## Out of Scope (separate plans)

- Table integrity checks (ragged columns, header-only tables)
- Reference comparison (compare_to_reference function)
- mpark/wg21 PDF parsing (see QA-002)
- ASCII compliance mode (wg21-papers style enforcement)
