# check-content Algorithm

## Overview

`check_content.py` answers a focused question: **is the converted
Markdown a faithful representation of its source?** It reads both
`<pid>.pdf|.html` and `<pid>.md`, normalizes the two streams the same
way, and compares them. The headline number is *coverage* — the share
of source-text tokens whose 5-gram shingles appear in the Markdown. A
companion number, *drift*, measures the opposite (tokens in Markdown
that have no counterpart in the source).

The tool does not score formatting. Heading nesting, code-block
fencing, mojibake, and front-matter shape are out of scope: the
question here is whether the *text* survived conversion, not whether
the markup around it is well-formed.

The tool is also not LLM-driven. Source extraction, normalization,
alignment, and reporting are deterministic Python. Anyone can clone
the repo and reproduce the same coverage numbers.

## Core Principles

- **Library returns data; CLI persists.** `check_paper_content(pid,
  backend)` returns a `ContentCheckResult` dataclass. `cli.convert`
  owns stdout and the JSON writer.
- **Tunable thresholds are named constants.** No bare numeric
  literals for shingle width, window size, coverage floor, region
  minimum, bucket edges, or repeat ratio. All at module top.
- **Calibration-first thresholds.** `_COVERAGE_NEEDS_REVIEW` ships
  unset (`None`). The distribution histogram always prints; the
  "Files needing review" gate is omitted until a calibration pass
  commits a value from real workspace data.
- **No silent degradation.** A paper missing source or Markdown is
  reported as `skipped` with a reason, never as `coverage=1.0`. A
  paper with an unsupported source suffix raises `ValueError`.
- **No new runtime dependencies.** Reuses PyMuPDF (already in tomd),
  mistune (already in tomd), and BeautifulSoup4 + tomd's HTML
  `strip_boilerplate` for HTML inputs. Adding a second PDF parser
  (e.g. `pdftotext`) for true extractor-level independence is
  deferred until there is evidence that the glyph-layer blind spot
  matters.

## Data Model

```
MisalignedRegion
    side               source | markdown
    token_start, token_end    half-open token range
    sample             60-char text excerpt
    page               source-side PDFs only

ContentCheckResult
    paper_id
    source_format      pdf | html
    coverage           0.0-1.0
    drift              0.0-1.0
    source_token_count
    markdown_token_count
    missing_regions    in source, not in markdown
    extra_regions      in markdown, not in source
```

Both dataclasses are `frozen=True`. The tuples in `missing_regions` /
`extra_regions` are sorted by `token_start` after the dedup pass.

## Pipeline (7 steps)

| Step | What | Function |
|------|------|----------|
| 1 | Extract source text per page | `_extract_pdf_stream` / `_extract_html_stream` |
| 2 | Strip repeating page lines (headers, footers, page numbers) | `_strip_repeating_lines` |
| 3 | Extract Markdown plain text from mistune AST | `_extract_markdown_stream` |
| 4 | Normalize each stream identically and tokenize | `_normalize`, `_tokenize` |
| 5 | Compute shingled-hash coverage and drift (headline numbers) | `_shingle_hashes`, `_multiset_coverage` |
| 6 | Scan windowed local coverage on both sides; run targeted LCS in low-coverage windows | `_local_coverage_windows`, `_regions_from_opcodes` |
| 7 | Dedup overlapping regions, return `ContentCheckResult` | `_dedup_regions`, `check_paper_content` |

Step 6 is the only step with a pairwise cost. It only fires inside
windows whose local shingle coverage is below
`_LOCAL_COVERAGE_FLOOR`, so the total pairwise work is bounded
per-window, not per-paper.

## Techniques by Layer

### Layer 1: Source extraction (3 techniques)

**T1. PDF flat-text extraction**
- `_extract_pdf_pages`
- Uses PyMuPDF `page.get_text()` per page (the flat string form, not
  the dict/rawdict). This deliberately bypasses tomd's classification
  pipeline: dual-path extraction, spatial thresholding, monospace
  detection, header/footer stripping, table region detection, heading
  inference, code-block detection, wording detection, span
  normalization, TOC stripping.
- The two share only the underlying glyph→unicode mapping. CMap
  bugs, ligature errors, and encoding corruption that survive into
  both extractions cancel out in the comparison and are recorded as
  a known blind spot.
- `fitz.open()` is paired with `doc.close()` in a `finally` block,
  per tomd convention.

**T2. HTML body extraction with generator boilerplate strip**
- `_extract_html_stream`
- Reads the file as UTF-8 (errors="replace"), parses with
  BeautifulSoup, runs `detect_generator` and `strip_boilerplate`
  from `tomd.lib.html.extract` (reusing the per-generator strip for
  bikeshed, mpark, dascandy/fiets, schultke, wg21, etc.), then calls
  `.get_text(separator=" ")` on what remains.
- This sacrifices full extractor-level independence on the HTML side
  by design. The alternative — keeping nav, generator headers, and
  auto-TOCs — makes HTML coverage uninterpretable. The trade-off is
  recorded as a known limitation: HTML inputs produce a narrower
  "did tomd's renderer drop body content the stripper kept?" check,
  not a fully independent comparison.

**T3. Page-line repeat scrubber**
- `_strip_repeating_lines`
- Header / footer / page-number mitigation without positional data.
  Splits each page into lines, counts unique line-text occurrences
  across the document, drops any line text appearing on at least
  `_REPEAT_RATIO` (default 0.5) of pages.
- Crudely position-free: works on the flat `get_text()` output
  without escalating to `get_text("dict")`. Cruder than tomd's
  vertical-band rule, but catches running titles, running document
  numbers, and "Page N of M" patterns.

### Layer 2: Markdown extraction (2 techniques)

**T4. Mistune AST walk**
- `_extract_markdown_stream`
- Strips front matter (`_FRONT_MATTER_RE`) and tomd HTML markers
  (`_TOMD_HTML_MARKER_RE`, e.g. `<!-- tomd:uncertain:L10-L20 -->`)
  before parsing. Otherwise these would appear as drift tokens.
- Walks the AST collecting text from inline nodes (`text`, `codespan`,
  `linebreak`, `softbreak`), block code, and recursing through block
  wrappers (paragraphs, headings, list items, table cells, block
  quotes).
- Block / inline HTML survives as text after a second pass strips
  any embedded tomd markers.

**T5. Shared normalization**
- `_normalize`, `_tokenize`, `_dehyphenate`
- The same normalizer runs on both streams. Sequence: NFKC, smart-quote
  fold (single and double, including U+2018-U+201F, U+2032-U+2033,
  U+00AB-U+00BB), em-dash and en-dash fold, NBSP and other Unicode
  spaces fold, line-break dehyphenation (skipping compound prefixes
  `self-`, `non-`, `well-`, `cross-`), lowercase, punctuation strip
  (keeps `\w` and whitespace), whitespace collapse.
- Tokenization is whitespace-based on the normalized stream.

### Layer 3: Shingled-hash coverage (2 techniques)

**T6. Sliding-window shingles**
- `_shingle_hashes`
- For each window of `_SHINGLE_WIDTH` (default 5) consecutive tokens,
  compute `blake2b(digest_size=_HASH_DIGEST_SIZE)` of the
  space-joined window text. Pack into a Python `int` for cheap set
  operations.
- A duplicated shingle in the stream produces a duplicated hash;
  downstream uses multiset semantics, so repetition counts toward
  coverage proportionally rather than collapsing.

**T7. Multiset coverage**
- `_multiset_coverage`
- `coverage = |source_shingles ∩ md_shingles| / |source_shingles|`,
  using a `dict[int, int]` counter on the target side and
  decrementing matched entries. O(n) time and memory in the larger
  stream.
- `drift = 1 - multiset_coverage(md_shingles, source_shingles)` when
  the Markdown has shingles; 0.0 when it doesn't.
- Shingles are robust to small reorderings: a swapped word inside a
  sentence is still 4 of 5 matching shingles. Right behaviour for
  coverage, not alignment.

### Layer 4: Local mismatch detection (2 techniques)

**T8. Windowed local coverage scan**
- `_local_coverage_windows`
- Slides a window of `_LOCAL_WINDOW_TOKENS` (default 200) over the
  shingle stream, step size = half the window. For each window,
  computes the fraction of shingles present in the opposite-side
  hash *set*. Windows where the fraction is below
  `_LOCAL_COVERAGE_FLOOR` (default 0.6) become candidates for
  pairwise alignment.
- Runs on both sides:
  - Source-side scan: surfaces missing regions (dropped paragraphs,
    truncated content).
  - Markdown-side scan: surfaces extra regions (hallucinated content,
    tomd-emitted artefacts in the Markdown without a counterpart in
    the source). The source-side scan alone misses this case because
    the source is fully present in Markdown.
- Adjacent low-coverage windows are merged.

**T9. Targeted SequenceMatcher inside low-coverage windows**
- `_regions_from_opcodes` driven from `check_paper_content`
- For each low-coverage window, runs `difflib.SequenceMatcher`
  comparing the window's tokens against the full opposite-side
  token stream. Walks the resulting opcodes and emits
  `MisalignedRegion` entries for `delete` / `insert` / `replace`
  spans that exceed `_MIN_REGION_TOKENS` (default 8).
- For source-side regions, looks up the page number from the
  source extraction's per-token page map.
- Pairwise cost is bounded per window, not per paper. Papers without
  any low-coverage windows skip step 6 entirely.

### Layer 5: Region cleanup (1 technique)

**T10. Overlap dedup**
- `_dedup_regions`
- Adjacent windows overlap by 50%, so the same gap can produce two
  region entries. After collection, regions are sorted by
  `(token_start, -token_end)` and overlapping entries on the same
  side are merged into the longer enclosing region.

## Named constants

All tunables live at the module top so calibration is a one-place
edit.

| Constant | Default | Purpose |
|---|---|---|
| `_SHINGLE_WIDTH` | 5 | Shingle size in tokens. Smaller = more brittle to word swaps; larger = misses small overlaps. |
| `_HASH_DIGEST_SIZE` | 8 | blake2b digest bytes. 64 bits of hash space is well clear of collision risk at paper scale. |
| `_LOCAL_WINDOW_TOKENS` | 200 | Sliding window size for local-coverage scan. |
| `_LOCAL_COVERAGE_FLOOR` | 0.6 | Local windows below this fire the SequenceMatcher. |
| `_MIN_REGION_TOKENS` | 8 | Smallest gap surfaced as a `MisalignedRegion`. Below this, the noise floor exceeds the signal. |
| `_COVERAGE_BUCKETS` | (0.95, 0.85, 0.70) | Histogram bucket edges for the stdout summary. |
| `_COVERAGE_NEEDS_REVIEW` | `None` | Review-gate threshold. `None` today (distribution-only output); a calibration pass will commit a value per source format from real data. |
| `_REPEAT_RATIO` | 0.5 | Page-line repeat threshold for header/footer scrubbing. |
| `_REGION_SAMPLE_CHARS` | 60 | Character cap on the surfaced sample text. |
| `_WORST_FILES_DISPLAY_LIMIT` | 30 | Cap for the stdout "worst N" list. |
| `_CHECK_BATCH_TIMEOUT_SEC` | 120 | Per-paper straggler timeout in batch mode. |
| `_JSON_SCHEMA_VERSION` | 1 | Bumped when the JSON shape changes incompatibly. |

The JSON output echoes the constants block so calibration analyses
remain reproducible across runs.

## Edge cases

- **Headers, footers, page numbers** — tomd strips them; flat
  extraction keeps them. *Mitigation:* T3 (page-line repeat
  scrubbing).
- **Table of contents** — tomd strips them; flat extraction keeps
  them. *Acceptable false-OK:* the TOC duplicates headings that
  appear later, so a token-bag alignment treats it as matched
  against those headings. Not handled separately.
- **Smart quotes, ligatures, em-dashes** — normalized aggressively
  before tokenization.
- **Hyphenated line breaks** — `_dehyphenate` joins the broken word
  unless the prefix is in `_KEEP_HYPHEN_PREFIXES` (`self`, `non`,
  `well`, `cross`).
- **Table cell order** — PyMuPDF returns table text roughly
  top-to-bottom, left-to-right; Markdown tables are row-major. LCS
  over a long stream is order-tolerant within a few tokens but not
  across full table rows. The tool accepts local noise here; if it
  becomes problematic, segment both streams by structural boundary
  before alignment.
- **Code blocks** — kept on both sides. Normalization runs on code
  text too, which collapses formatting differences cleanly.
- **N-papers (`N5028` etc.)** — often have unusual layouts. The
  tool runs but thresholds should not be tuned against them.

## Performance budget

p95 < 5 s per paper, including 50k-token standards drafts. Step 5
is O(n); step 6 only fires on low-coverage windows. Verified by a
unit test that synthesises a 53k-token paper with a 1k-token drop
and asserts elapsed < 5 s; observed ~1 s on the development
machine.

## Thresholds and calibration

The headline question — "where does the review line go?" — should
be set empirically, from real distribution data, not by guess. The
realistic noise floor is unknown until measured, and is expected to
differ between PDF and HTML inputs: the HTML path uses tomd's
generator stripper, the PDF path does not, so each format has a
different maximum achievable coverage on a clean conversion.

Current behaviour:

- `_COVERAGE_BUCKETS = (0.95, 0.85, 0.70)` — bucket edges. The
  distribution histogram always prints.
- `_COVERAGE_NEEDS_REVIEW = None` — no review line yet. The report
  prints the distribution and the worst-N list, but omits the
  "Files needing review" / "Files probably OK" counts. The report
  is informative; nothing is gated.

Calibration (deferred): a dedicated pass should run the tool on the
workspace, plot per-format distributions (PDF vs HTML separately),
pick the elbow per format, and commit the constants. If the
distributions diverge enough, split into `_COVERAGE_NEEDS_REVIEW_PDF`
and `_COVERAGE_NEEDS_REVIEW_HTML`. The chosen values live in code;
the plot and the rationale belong in
`notes/check-content-calibration.md`.

The `drift` score is reported per paper but does not gate on a
threshold either, for the same reason: data first.

### Why coverage never reaches 1.0

Stdout prints an explicit upper-bound preamble at the top of the
report:

> Coverage measures the share of source-text tokens that align
> with tokens in the produced markdown. Perfect coverage (1.00)
> is not achievable: tomd intentionally strips headers, footers,
> page numbers, and tables of contents, all of which appear in
> the source extraction but cannot appear in the markdown.
> Expect clean papers to land between 0.90 and 0.97.

Without it, a 0.93 reads as failure when it is actually a clean
conversion.

## What check-content catches, and what it doesn't

The tool's strength is dropped or extra *content*; it has nothing to
say about how that content is marked up.

| Bug class                                       | `--check-content` catches                       |
| ----------------------------------------------- | ----------------------------------------------- |
| Missing paragraph / dropped footnote            | yes                                             |
| Dropped figure caption                          | yes                                             |
| Truncated content                               | yes                                             |
| Lossy table parse (cells lost)                  | yes                                             |
| Tool-emitted artefacts in Markdown              | yes (reported as `extra_regions`)               |
| Reordered sections (content swapped, not lost)  | partial (sees the mismatch, not the movement)   |
| Mojibake                                        | partial (some sequences collapse in normalization) |
| Heading level skip                              | no (formatting, not content)                    |
| Unfenced code (paragraph that's actually C++)   | no (formatting, not content)                    |
| Hallucinated text                               | n/a (tomd is deterministic; concern for any future LLM-driven converter) |
| Glyph-layer bugs (ligatures, CMap, encoding)    | no (shared blind spot with tomd's extractor)    |

The high-value case is the **silent-drift cohort**: papers whose
Markdown looks well-formed by every structural measure but whose
coverage is low because a chunk of source text never made it through.
Visual inspection misses these; only a side-by-side token comparison
surfaces them. That cohort is what justifies the tool's existence.

## Out of scope

- **LLM-based semantic equivalence.** A wrong answer here destroys
  credibility in a way that cannot be regained; deferred until
  there is a stable ground-truth corpus to evaluate against.
- **Visual-fidelity comparison** (rendered PDF vs rendered
  Markdown).
- **Auto-suggesting fixes** for low-coverage regions.
- **Bidirectional re-conversion roundtrip** (Markdown → PDF →
  re-extract).
- **True extractor-level independence** for the PDF path (e.g.
  adding `pdftotext` / Poppler as a second parser). Would close
  the glyph-layer blind spot, which is small in practice. Adds a
  dependency for marginal gain; reconsider if systematic mojibake
  starts slipping past the comparison.
- **A standalone `paperflow check-content` verb.** Currently a
  flag on `convert`. If the report-only-flag-on-convert pattern
  proves unwieldy, promote it then.
