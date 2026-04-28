# tomd - Agent Rules

## What This Is

tomd is a hybrid PDF-and-HTML-to-Markdown converter. It uses deterministic text extraction and multi-signal classification to produce Markdown, with optional LLM resolution for ambiguous sections. PDF conversion uses dual-path extraction with confidence scoring; HTML conversion uses DOM traversal with generator-specific metadata extraction.

## Architecture

Pipeline execution order:

1. Per-page: dual extract (MuPDF + spatial) + edge items + link collection + hidden region scan + page 0 color extraction + line drawing collection
2. Close document
3. Slide-deck detection (early exit for landscape small-page presentations)
4. Standards-draft detection (early exit for documents >= 200 pages)
5. Hidden block stripping + readability check (early exit if garbage)
6. Header/footer detection and stripping (both paths)
7. Monospace propagation: spatial path's glyph-width classifications applied to MuPDF spans
8. Wording detection: HSV color analysis + drawing decoration correlation for ins/del markup
9. Text cleanup: NBSP, whitespace, dehyphenation, cross-page join (both paths)
10. Span normalization: snap bold/italic boundaries to word edges (both paths)
11. WG21 metadata extraction from page 0 blocks
12. Table detection from MuPDF block positions; exclude table regions from spatial
13. Dual-path comparison -> Sections (confident or uncertain per page)
14. Merge table sections into position
15. Structure: metadata extraction, heading/list/paragraph classification, position-based list detection, paragraph merging, code block detection, wording section detection, language label stripping, nesting validation
16. TOC stripping (exact-match fast path + fuzzy match against headings)
17. Emit markdown + optional `<pid>.prompts.json` (JSON array of self-contained LLM reconcile prompts)

## Multi-Signal Confidence (Critical)

Never classify based on a single signal. Every structural decision (heading, paragraph, list, code, table) must consider all available signals and produce a confidence level.

Available signals and their reliability:
- **Section numbering** (highest) - dotted decimal numbers give unambiguous depth
- **Font size** (high) - relative to the most common (body) size
- **Font weight/style** (medium) - bold/italic flags from font metadata
- **Known section names** (high for WG21) - `Abstract`, `References`, `Wording`, etc.
- **Line geometry** (medium) - length, indentation, vertical gaps
- **Dual-path agreement** (high) - MuPDF and spatial rules agree on boundaries

When signals agree, confidence is high. When they disagree, flag for LLM review. Never silently pick one signal over another - the disagreement is the data.

## Preserve All Metadata

Never discard information from the PDF during extraction. Text is the primary output, but font size, font name, font flags, coordinates, and page boundaries are preserved as annotations. Downstream phases use this metadata for confidence scoring and LLM prompt context.

Discard nothing. Use everything.

## Dual Extraction Path

Every PDF page is processed through two independent extraction paths:
1. **MuPDF path** - `page.get_text("dict")` for MuPDF's block/line/span grouping
2. **Spatial path** - `page.get_text("rawdict")` with four elif branches keyed on
   font-size-relative thresholds (named constants from `types.py`):
   - `dy > PARA_SPACING_RATIO * avg_fs` - flush block (paragraph break)
   - `dy > LINE_SPACING_RATIO * avg_fs` - flush line (new line, same block)
   - `dy > WORD_GAP_RATIO    * avg_fs` - flush line (large vertical gap)
   - `dx > WORD_GAP_RATIO    * avg_fs` - flush word + insert space span

Both produce the same intermediate format. Agreement = confident. Disagreement = uncertain. Never skip one path. The comparison is the confidence mechanism.

When paths disagree: MuPDF version goes in the output (it's more battle-tested). Both versions go in the LLM prompt for reconciliation. The prompt must require all data verbatim - the LLM fixes structure, never content.

## Heading Rules

- Heading level is derived from section numbering depth: `2.1.3` = depth 3 = `####` (depth + 1 because `#` is reserved for the document title)
- Font size provides an independent heading level estimate by ranking sizes larger than body
- All signals are evaluated; confidence depends on agreement count
- Nesting must be validated: no heading may skip more than one level deeper than its predecessor
- When signals conflict, section number wins if present; font-size ranking wins otherwise at lower confidence
- Known unnumbered sections (`Abstract`, `Revision History`, `References`, `Acknowledgements`, `Motivation`, `Wording`, `Proposed Wording`, `Design Decisions`) are top-level (`##`)
- Title is the largest-font non-metadata text block before any numbered section, confirmed by color darkness when available (multi-signal). Category labels (short all-uppercase text like "WG21 PROPOSAL") are consumed, not treated as titles

## Honest Output

The tool must never silently produce bad Markdown.

- If a region is uncertain, emit the MuPDF version in the output marked with `<!-- tomd:uncertain:L{start}-L{end} -->`
- The companion prompts file is a JSON array; each element is a self-contained prompt that includes BOTH extraction versions, surrounding context, and the framing instructions
- LLM prompts must require verbatim data preservation - the LLM fixes structure, never content
- If no prompts file is needed (zero uncertain regions), don't write one.
- High-confidence output should look like a human wrote the Markdown - proper heading nesting, unwrapped paragraph lines, correct list formatting, blank lines between blocks

## Markdown Quality

The output Markdown must be clean and readable:
- Paragraphs are single unwrapped lines (no hard wraps from PDF line breaks)
- One blank line between all block elements (paragraphs, headings, lists, code blocks)
- Headings use ATX style (`##` not underlines)
- Lists use the marker from the source when detectable (`-`, `*`, `1.`)
- No trailing whitespace on lines
- No redundant blank lines (max one between blocks)
- Dehyphenate broken words across lines (`imple-` + `mentation` -> `implementation`)
- Join paragraphs that span page breaks (no terminal punctuation + lowercase continuation = same paragraph)
- Hyperlinks become `[text](url)` - only http, https, mailto schemes
- WG21 metadata block becomes YAML front matter
- Collapse multiple spaces, replace non-breaking spaces, normalize whitespace

## LLM Integration (v2)

Auto-resolution via `--llm` flag is deferred to v2. For v1, the tool produces a companion `<pid>.prompts.json` file: a JSON array where each element is a complete LLM prompt the operator can paste into any LLM verbatim. One element per uncertain region (plus one per flagged wording-detection issue).

## File Map

- `__main__.py` - CLI entry point. Argparse + paperstore-backed `convert_paper` invocation. Takes a paper id and a workspace dir; supports `--qa` mode with `--qa-json`, `--workers`, and `--timeout` flags for batch quality scoring. The pre-0.2 file-path interface (`tomd input.pdf`) is removed; sources must be staged via `mailing` first.
- `api.py` - Public API. `convert_paper(paper_id, store, *, write_prompts=True) -> Path` reads the staged source + mailing-index row through paperstore, dispatches by suffix to `lib.pdf.convert_pdf` / `lib.html.convert_html`, applies YAML front-matter fallback from the mailing row, strips TOC blocks, writes the markdown (and an optional `<pid>.prompts.json` intermediate, a JSON array of LLM reconcile prompts) back through the store, and returns the path of the written markdown.
- `lib/__init__.py` - Shared text utilities and constants for PDF and HTML converters: `ascii_escape` (kept for external use, no longer called in pipeline), `strip_format_chars`, `format_front_matter`, `parse_author_lines`, `ALLOWED_LINK_SCHEMES`, shared regex patterns (`EMAIL_RE`, `DATE_RE`, `DOC_NUM_RE`, `SECTION_NUM_PREFIX_RE`), and their reusable shape strings (`DOC_NUM_PATTERN`, `SECTION_NUM_PATTERN`) consumed by `lib/pdf/types.py` to build `DOC_FIELD_RE` and `SECTION_NUM_RE`.
- `lib/similarity.py` - Dual-algorithm string similarity (SequenceMatcher + Jaccard). Per-algorithm thresholds, 200-char circuit breaker. Format-agnostic.
- `lib/toc.py` - Table of Contents detection. Primary path: exact-match set lookup against known headings (O(1) per section). Fuzzy fallback (SequenceMatcher + Jaccard) only when heading count is below `_MAX_FUZZY_HEADINGS` (200). Fallback structural-hints path for headingless wording-only papers. Bridges small gaps. Format-agnostic.
- `lib/pdf/__init__.py` - Exports `convert_pdf()` and `PipelineResult`. Orchestrates the full pipeline via `_run_pipeline()` which returns all intermediate data including skip status. Early exits: `_is_slide_deck` (landscape + small pages), `_is_standards_draft` (>= 200 pages). Includes monospace propagation, wording classification, page 0 color extraction via space-color proxy, and `_toc_structural_hints()` for headingless wording papers. Output is UTF-8 Unicode.
- `lib/pdf/wording.py` - Three-layer wording detection with two-pass deletion. Layer 1: block-level color contamination filter (non-green/red/blue chromatic color = syntax-highlighted code, skip block). Layer 2: line-level majority filter (>50% of non-link characters must be green or red), plus partial-line pattern (green/red spans on otherwise-black line). Layer 3: green on qualifying line = ins; red + confirmed strikethrough drawing = del. Two-pass del: red spans without strikethrough are collected as `del_unconfirmed`; if >= 5 green ins spans exist in the document, unconfirmed deletions are promoted to del (handles mpark/wg21 color-only wording without strikethrough). Hyperlinks always excluded.
- `lib/pdf/types.py` - Data classes (`Block`, `Span`, `Line`, `Section`, `PageEdgeItem`), enums (`Confidence`, `SectionKind`), named constants (all public, no underscore prefix), precompiled regex, `is_readable()`.
- `lib/pdf/extract.py` - Dual extraction: `extract_mupdf()` (dict API) and `extract_spatial()` (rawdict + four spatial threshold branches). Link collection and attachment. Calls `classify_monospace` during span construction.
- `lib/pdf/mono.py` - Triple-signal monospace detection. Font name decomposition (strip modifiers, split camelCase, strip trailing digits from tokens for fonts like LMTT10, check keywords), glyph width uniformity, glyph spacing uniformity.
- `lib/pdf/cleanup.py` - Header/footer detection (edge items per page), repeating strip, span whitespace (NBSP, multi-space on non-mono), dehyphenation, cross-page join, hidden region detection.
- `lib/pdf/spans.py` - Span normalization. Snaps bold/italic style boundaries to word edges. Monospace exempt.
- `lib/pdf/table.py` - Two-signal table detection. Signal 1: MuPDF block/line columnar layout (x-gap > 50). Signal 2: geometric column profile (x positions co-occurring in the same y-band across 2+ rows). Orphan absorption for wrapped cell first lines (same-page only). Merges orphan partial rows into the next row's first cell. Extracts as high-confidence TABLE sections, excludes table regions from spatial path.
- `lib/pdf/structure.py` - Dual-path comparison, metadata extraction, heading intelligence (multi-signal, `heading_confidence` public), position-based list detection (x-coordinates), paragraph merging, code block detection (including absorption of all-monospace UNCERTAIN sections to resolve code blocks split across page boundaries), content-based code rescue pass after wording classification (promotes PARAGRAPH sections with 3+ code-like lines to CODE/MEDIUM, skipping WORDING sections), language label detection, nesting validation.
- `lib/pdf/emit.py` - Markdown generation (headings, paragraphs, code blocks, tables, nested lists) with span-level formatting (inline code, bold, italic, links). Prompts file generation for uncertain regions.
- `lib/pdf/qa.py` - Markdown-based quality assurance scoring. `compute_metrics()` takes ONLY a Markdown string — no page count, no file format, no pipeline internals. All signals are derived from the text via mistune AST parsing: headings, code blocks, front-matter fields, uncertain region markers, unfenced code detection. This constraint is intentional: it keeps scoring format-agnostic and prevents coupling to the converter. Do not add parameters that leak converter state into the scorer. `run_qa_report()` batch-processes files with parallel workers and straggler timeout. Invoked via `tomd --qa`.
- `lib/html/__init__.py` - Exports `convert_html()`. Six-step HTML pipeline: parse, detect generator, extract metadata, strip boilerplate, render DOM, assemble output.
- `lib/html/extract.py` - Generator detection (mpark, bikeshed, hand-written, hackmd, wg21, schultke, dascandy/fiets, unknown). Per-generator metadata extraction. `_FIELD_SYNONYMS` + `_match_field` provide a shared fuzzy label matcher reused across extractors. Boilerplate stripping per generator. Eight generator families supported. Schultke detected by `<code-block>` custom element presence; dascandy/fiets by meta generator tag.
- `lib/html/render.py` - Recursive DOM-to-Markdown traversal. Handles headings, paragraphs, lists, tables, code blocks, wording divs, blockquotes, and inline formatting. Custom element support: `<code-block>` (Schultke) rendered as fenced C++ code, `<example-block>/<note-block>/<bug-block>` as blockquotes, `<tt->` as inline code, `<h->/<f-serif>` as inline pass-through. dascandy/fiets `<code><div class="code">` pattern detected and rendered as fenced code.

## Header/Footer Stripping

Before dual extraction, scan all pages for repeating content at page edges.

- For each page, capture the top 3 and bottom 3 text items by y-coordinate
- Compare across pages: same text at same y on 50%+ of pages = repeating = strip
- Page numbers: same y, content is a bare number or "Page N" or "N of M" = strip
- Running doc numbers: same y, content matches document number pattern = strip
- Strip these items from page data before extraction runs. They are not content.

## Text Cleanup Rules

- **Dehyphenation**: line ends with `-`, next line starts lowercase -> join word, remove hyphen. Skip known compound prefixes (`self-`, `non-`, `well-`, `cross-`).
- **Cross-page join**: last block on page N has no terminal punctuation, first block on page N+1 starts lowercase -> same paragraph, join.
- **Link extraction**: collected during Phase 2 via `page.get_links()`, matched to text by bounding rect -> `[text](url)`. Only http/https/mailto.
- **Whitespace**: collapse runs, replace non-breaking spaces, strip trailing.
- **WG21 metadata**: Document Number / Date / Reply-to / Audience at top of page 1 -> YAML front matter.

## tomd-Specific Extensions

These extend general rules in the root CLAUDE.md with project-specific instances.

- `fitz.open()` must always be paired with `doc.close()` in a `finally` block. Never rely on garbage collection.
- Font metadata thresholds (what counts as "larger than body," "horizontal close," etc.) must be named constants, not magic numbers scattered in code.
- The four spatial threshold branches (PARA_SPACING, LINE_SPACING, WORD_GAP for dy, WORD_GAP for dx) are the foundation of `extract_spatial`. Changes to these constants affect everything downstream. Test thoroughly.
- Regex patterns for section numbers, known section names, list markers, and metadata fields must be precompiled at module level and defined in one place.
- Runtime dependencies are `pymupdf`, `beautifulsoup4`, and `mistune`. All three must be declared in `pyproject.toml`.
