# PDF conversion rules (tomd)

This document describes how the **PDF** branch of tomd turns a WG21-style PDF into Markdown. It mirrors execution order in [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py) `_run_pipeline`, groups **multi-signal** decisions together, and adds **Why** only where intent is clear from code comments, module docstrings, [`lib/pdf/ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md), or [`CLAUDE.md`](CLAUDE.md).

For deeper technique tables and module maps, see [`lib/pdf/ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md).

## Contents

- [Goals](#goals)
- [Principles and corpus assumptions](#principles-and-corpus-assumptions)
- [Before changing behavior](#before-changing-behavior)
- [Pipeline](#pipeline) (includes [Figure detection](#figure-detection))
- [Appendix: rules by source file](#appendix-rules-by-source-file)

## Goals

1. Understand what the pipeline does without reading every module first.
2. Reduce corpus-breaking changes: WG21 mailings vary widely; thresholds encode tradeoffs, not universal truth.

## Principles and corpus assumptions

- **Dual extraction:** Every page is built from MuPDF dict grouping and from spatial raw-character rules; comparing them is the main confidence mechanism ([`CLAUDE.md`](CLAUDE.md), [`ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md)).
- **Honest output:** When paths disagree badly, the emitter marks **uncertain** regions and fills prompts with both texts rather than silently picking one ([`CLAUDE.md`](CLAUDE.md)).
- **MuPDF in the body:** For disagreements, MuPDF text is what ships in the Markdown; spatial text is for reconciliation prompts ([`ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md)).
- **Multi-signal structure:** Headings, lists, code, tables, and wording use several signals on purpose; single-signal tweaks are risky for the full corpus ([`CLAUDE.md`](CLAUDE.md)).
- **Shared post-emit helpers:** PDF and HTML paths both call [`lib/__init__.py`](lib/__init__.py) helpers such as `dedup_paragraphs`, `strip_redundant_body_meta`, and `strip_leading_h1` after assembly.

## Before changing behavior

- Extend or run golden and unit tests under `packages/tomd/tests/` after heuristic changes.
- Prefer surfacing **uncertainty** (markers + prompts) over silent rewriting when extraction is ambiguous.
- Exercise diverse shapes: wording-heavy PDFs, tables, papers with weak headings, and layouts near slide geometry.
- If you change [`lib/__init__.py`](lib/__init__.py) emit helpers, expect **HTML** output to move too.

### Corpus-risk hotspots

Tightening similarity without prompts; loosening TOC detection; aggressive paragraph deduplication; table orphan absorption across pages; wording gates and color-only deletion promotion; header or footer stripping on very short PDFs; structural code rescue regex false positives on flattened tables ([`structure.py`](lib/pdf/structure.py) notes narrower rescue regex than QA).

---

## Pipeline

### Early exits

**Slide deck detection**

- Treat the document as a slide deck when **at least 80%** of pages are landscape **and** page width is **under 600 pt**.
- Skip conversion and return a short explanatory prompt instead of Markdown.

**Standards draft detection**

- Skip conversion when page count is **200 or more**.

**Why:** Presentation geometry and huge drafts break dual-path assumptions and are out of scope for committee-paper conversion ([`pipeline.py`](lib/pdf/pipeline.py) docstrings).

**Sources:** `_is_slide_deck`, `_is_standards_draft` in [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py).

---

### Dual extraction

**MuPDF dict path**

- Use `page.get_text("dict")`, keep only **type 0** text blocks, and carry font name, size, bold or italic flags, bbox, origin, and color on spans ([`extract.py`](lib/pdf/extract.py)).

**Spatial rawdict path**

- Use `page.get_text("rawdict")`, walk characters, sort into bands by **y** then **x** using half line height with a floor, then flush words or lines when vertical or horizontal gaps exceed **font-size-relative** thresholds (`PARA_SPACING_RATIO`, `LINE_SPACING_RATIO`, `WORD_GAP_RATIO` in [`types.py`](lib/pdf/types.py)).

**Why:** Two independent paths surface layout disagreements; spatial geometry catches cases MuPDF grouping mis-segments ([`ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md), [`CLAUDE.md`](CLAUDE.md)).

**Sources:** `extract_mupdf`, `extract_spatial` in [`lib/pdf/extract.py`](lib/pdf/extract.py).

---

### Links

- Collect `page.get_links()`, keep only **http**, **https**, and **mailto** ([`ALLOWED_LINK_SCHEMES`](lib/__init__.py)).
- Attach each link to the span with the **best bbox overlap**.

**Sources:** `collect_links`, `attach_links` in [`lib/pdf/extract.py`](lib/pdf/extract.py).

---

### Line drawings

- Gather nearly horizontal drawing segments from `page.get_drawings()` with minimum width for later strikethrough correlation ([`wording.py`](lib/pdf/wording.py)).

**Sources:** `collect_line_drawings` in [`lib/pdf/wording.py`](lib/pdf/wording.py).

---

### Figure detection

**Box detection**

- Detect bordered boxes from `page.get_drawings()`: paths with **both** stroke color and fill color, width 30-80% of page, height 10-80pt. These indicate diagram boxes (flow charts, sequence diagrams, concept chains). Page-spanning rectangles are rejected ([`figures.py`](lib/pdf/figures.py)).

**Grouping and merging**

- Group spatially proximate boxes by y-tolerance (`_BOX_GROUP_Y_TOLERANCE`). Then merge groups bridged by connectors (Union-Find over connector endpoints) so multi-row diagrams (e.g. sequence diagrams with top and bottom participant headers) form a single region ([`figures.py`](lib/pdf/figures.py)).

**Arrow and connector classification**

- **Arrowheads:** Small filled triangles (3 line items, max size `_ARROWHEAD_MAX_SIZE`). Accepts both closed paths and open paths with fill+color (dashed return arrow arrowheads). Returns centroid, pointy vertex, and base midpoint ([`figures.py`](lib/pdf/figures.py)).
- **Solid connectors:** Stroked paths with 1-4 line or curve items exceeding `_CONNECTOR_MIN_LENGTH`. Must not have fill color (excludes box borders) ([`figures.py`](lib/pdf/figures.py)).
- **Dashed connectors:** Filled paths without stroke color, 20+ tiny line items forming a near-horizontal or near-vertical dashed line (common for UML return arrows). Direction reversed for return semantics ([`figures.py`](lib/pdf/figures.py)).

**Graph topology**

- **Sequence diagram detection:** When boxes cluster into columns with duplicate x-positions (top and bottom participant headers), collapse into logical nodes. Build edges from arrowheads matched to connectors, ordered by y-position. Dashed return arrows whose arrowhead tip lands inside an intermediate box are projected through to the next terminal node in the arrow direction ([`figures.py`](lib/pdf/figures.py)).
- **General topology:** Match arrowheads to nearest boxes (pointy vertex = target, base = source). Fall back to connector-only matching when no arrowheads are found. Detect bidirectional edges ([`figures.py`](lib/pdf/figures.py)).

**Pipeline integration**

- `pipeline.py` calls `detect_figure_regions()` per page after `get_drawings()`. Resulting `FigureRegion` objects (with optional `FigureGraph`) are passed to `structure_body()`.
- `structure.py` reclassifies sections overlapping figure regions as `SectionKind.FIGURE`. Consecutive FIGURE sections from the same region are merged. The `FigureGraph` is attached to the merged section ([`structure.py`](lib/pdf/structure.py)).

**Rendering**

- **Graph-based:** Sequence diagrams render as participant header + numbered steps. Linear chains render as `A -> label -> B -> label -> C`. Vertical flows render as numbered lists. Bidirectional flows are annotated ([`emit.py`](lib/pdf/emit.py)).
- **Positional fallback:** When no graph topology is extracted, text fragments are sorted by bbox coordinates and rendered as a blockquote ([`emit.py`](lib/pdf/emit.py)).
- Orphan labels (text between boxes, e.g. arrow labels) are matched to edges by spatial proximity or y-position ([`emit.py`](lib/pdf/emit.py)).

**Sources:** `detect_figure_regions`, `_is_bordered_box`, `_is_arrowhead`, `_is_connector`, `_is_dashed_connector`, `_detect_sequence_diagram`, `_match_topology`, `_project_through_box`, `_merge_connected_groups` in [`lib/pdf/figures.py`](lib/pdf/figures.py); `_render_figure_placeholder`, `_render_graph_figure`, `_render_sequence_figure`, `_render_positional_figure` in [`lib/pdf/emit.py`](lib/pdf/emit.py).

---

### Body-font census

- Count characters per lowercased font name across **MuPDF** blocks; keep the **top five** font names as `body_fonts` for hidden-region detection ([`pipeline.py`](lib/pdf/pipeline.py)).

**Sources:** `_run_pipeline` font loop in [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py).

---

### Hidden region stripping

**Detection**

- Scan `page.get_texttrace()` for spans whose font is **not** in `body_fonts`, color is **not** black, and font name suggests **Roboto**, **Google**, or **Material** widgets ([`cleanup.py`](lib/pdf/cleanup.py) docstring).

**Ignore mode 3**

- Skip invisible rendering mode in this pass because dict or rawdict already drop it; tracing mode 3 would false-positive on accessibility overlays ([`cleanup.py`](lib/pdf/cleanup.py) comment).

**Stripping**

- Remove blocks whose geometry lies entirely inside collected hidden rectangles ([`cleanup.py`](lib/pdf/cleanup.py)).

**Why:** Strip Google Docs style UI chrome that leaks into PDF text ([`cleanup.py`](lib/pdf/cleanup.py)).

**Sources:** `find_hidden_regions`, `strip_hidden_blocks` in [`lib/pdf/cleanup.py`](lib/pdf/cleanup.py).

---

### Readability gate

- After stripping hidden text, join MuPDF block text and reject conversion when text is **too short**, **alphanumeric ratio** in an early sample is **below 0.3**, or **slash density** is too high ([`types.py`](lib/pdf/types.py) `is_readable`). Failure returns `PipelineResult.for_skip(SkipReason.UNREADABLE, readable=False)` with empty markdown.

**Why:** Detect scanned or garbage extraction early ([`types.py`](lib/pdf/types.py)) and surface it as a typed skip, not a silent empty result.

**Sources:** `is_readable` in [`lib/pdf/types.py`](lib/pdf/types.py); skip construction in [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py).

---

### Header and footer stripping

**Sampling**

- Take the **top three** and **bottom three** text lines by **y** per page from **both** paths, dedupe by `(text, rounded y)` ([`cleanup.py`](lib/pdf/cleanup.py), [`types.py`](lib/pdf/types.py) `EDGE_ITEMS_PER_PAGE`).

**Repeating detection**

- Bucket by quantized **y** (`Y_TOLERANCE`). Mark repeating when the bucket appears on **at least half** of all pages **and** lines match **exact text**, **page-number patterns**, or **document-number patterns** ([`detect_repeating`](lib/pdf/cleanup.py)).

**Skip short PDFs**

- If **fewer than three** pages, do **not** detect repeating headers or footers ([`cleanup.py`](lib/pdf/cleanup.py)).

**Stripping**

- Remove matching whole lines or individual spans at repeating **y** bands; **preserve page 0** lines above labeled metadata band so WG21 fields survive ([`strip_repeating`](lib/pdf/cleanup.py)).

**Why:** Running headers and footers are not paper body ([`CLAUDE.md`](CLAUDE.md), [`ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md)).

**Sources:** `get_edge_items`, `detect_repeating`, `strip_repeating` in [`lib/pdf/cleanup.py`](lib/pdf/cleanup.py).

---

### Monospace

**Per-span classification**

- Combine **font-name keywords** after stripping modifiers and camel splits, **glyph width uniformity**, and **glyph spacing uniformity**; optionally reject when fat versus thin advance widths exceed a ratio; accept on **two or more** signals, or signal three alone, or signal one alone ([`mono.py`](lib/pdf/mono.py)).

**Propagation**

- After spatial extraction, collect fonts where **at least half** of characters classify monospace; drop the **dominant** font unless its **name** still passes monospace check; set `monospace=True` on MuPDF spans whose font is in that set ([`mono.py`](lib/pdf/mono.py)).

**Why:** Spatial path has glyph metrics; MuPDF dict often lacks them; propagation aligns code detection without relying on font flags alone ([`mono.py`](lib/pdf/mono.py), [`CLAUDE.md`](CLAUDE.md)).

**Sources:** `classify_monospace`, `propagate_monospace` in [`lib/pdf/mono.py`](lib/pdf/mono.py).

---

### Wording

**Block filter**

- Skip blocks that contain **chromatic colors outside** green, red, or blue so highlighted code is not treated as diff markup ([`wording.py`](lib/pdf/wording.py)).

**Line qualification**

- A line qualifies when **more than half** of non-link characters are green or red **or** green or red appears on an otherwise **black** line ([`wording.py`](lib/pdf/wording.py)).

**Span roles**

- Green spans become **insertions**. Red spans become **deletions** only when a **horizontal strikethrough** drawing overlaps enough span width; otherwise they are tracked as **unconfirmed** deletion ([`wording.py`](lib/pdf/wording.py)).

**Promotion**

- If there are **at least five** insertion spans, promote **unconfirmed** red deletions to full deletions so **color-only** diff styles still work ([`wording.py`](lib/pdf/wording.py), [`CLAUDE.md`](CLAUDE.md)). If there are **fewer than five** total ins or del spans after filtering, skip wording entirely ([`wording.py`](lib/pdf/wording.py)).
- **Page-gated promotion (Ticket F fix):** Unconfirmed deletions are promoted **only on pages at or after the first page containing an insertion span**. Pages before the first insertion (preamble: Abstract, TOC, Revision History) keep their red spans as unconfirmed and those spans are dropped. This prevents red code-styling in preamble pages from being falsely classified as wording deletions (e.g. p2583r3 had 432 red code spans in Abstract/early sections).

**Why:** WG21 frameworks use fixed hue bands; hyperlinks are blue and excluded; minimum span counts suppress noise ([`wording.py`](lib/pdf/wording.py) module docstring).

**Sources:** `classify_wording` in [`lib/pdf/wording.py`](lib/pdf/wording.py).

---

### Text cleanup

**Cleanup**

- Strip Unicode **Cf** format characters, replace NBSP, collapse spaces outside monospace, **dehyphenate** across lines with compound-prefix guards, **join blocks across pages** when punctuation and case indicate continuation ([`cleanup.py`](lib/pdf/cleanup.py)).

**Span normalization**

- Snap bold or italic boundaries to **word edges** for adjacent non-monospace spans ([`spans.py`](lib/pdf/spans.py)).

**Block sorting**

- After normalization, sort **both** block lists by **(page_num, y-midpoint)**. MuPDF sometimes delivers blocks out of visual order within a page, which disrupts section boundaries downstream (e.g. wording content landing inside Acknowledgements) ([`pipeline.py`](lib/pdf/pipeline.py)).

**Sources:** `cleanup_text` in [`lib/pdf/cleanup.py`](lib/pdf/cleanup.py); `normalize_spans` in [`lib/pdf/spans.py`](lib/pdf/spans.py); block sort in [`pipeline.py`](lib/pdf/pipeline.py).

---

### Page zero metadata

**WG21 block metadata**

- Scan early MuPDF blocks with **text color lightness** map from **space characters** in texttrace so Type 3 black glyphs still reveal watermark lightness ([`_get_page0_text_colors`](lib/pdf/pipeline.py), [`wg21.py`](lib/pdf/wg21.py)).
- Recognize labels both with colon (`Audience:`) via `_LABEL_RE` and without colon (`Audience` alone on a line) via `_BARE_LABEL_RE` (Scrivener-style PDFs).
- **Intent extraction:** `Intent:` (colon or bare) is recognized as a metadata label. The value is stored lowercase (e.g. `Inform` becomes `inform`, `Ask` becomes `ask`). Only the exact label `Intent` is accepted; no synonyms.
- `_LABEL_RE` includes non-metadata stop-labels (`Issues`, `Previous`, `Follow up to`, `Co-authors`, `Source`, `Reference`, `Contributor`) that terminate value collection without storing a field, preventing adjacent lines from bleeding into the preceding field.
- Strip author contamination from audience values: truncate at email addresses, angle brackets, or double-space boundaries that indicate merged PDF lines.
- **Target as audience fallback:** `Target:` maps to the audience field only when no explicit `Audience:` or `Subgroup:` was already extracted. This prevents Target (typically `C++26`) from overwriting the committee audience value.
- **Reply-to wins:** Explicit `Reply-to:` labels store directly into `reply-to`. `Author:`, `Editor:`, and `Co-author:` labels store into `_author_names` only and fill `reply-to` as fallback when no explicit Reply-to was extracted. When an explicit Reply-to entry has an email matching a bare-name fallback entry, the bare name is upgraded in place. Aligns with the HTML extractor bucket strategy ([`wg21.py`](lib/pdf/wg21.py)).

**Why:** Title versus watermark disambiguation uses lightness proxy ([`pipeline.py`](lib/pdf/pipeline.py) docstring).

**Sources:** `extract_metadata_from_blocks`, `_get_page0_text_colors`.

---

### Tables

**Two signals**

- **Columnar blocks:** consecutive blocks whose lines show **x gaps** above a threshold form candidate rows ([`table.py`](lib/pdf/table.py)).
- **Shared column profile:** x positions that appear **together** in the same **y-band** on **two or more** bands qualify as table columns; lone margin columns do not ([`table.py`](lib/pdf/table.py)).

**Orphans**

- Single-line blocks aligned to known columns can merge into the **next** row when lookahead confirms a table row; **same-page only** ([`table.py`](lib/pdf/table.py) module docstring).

**Side-by-side tables (Pass 2)**

- After standard columnar detection, scan remaining blocks for **side-by-side tables** where each cell is a separate MuPDF block (e.g. "Tony Tables" with multi-line code cells). Cluster body block x-positions into columns, group by y-overlap into rows, require **at least two** valid data rows ([`_detect_side_by_side_tables`](lib/pdf/table.py)).

**Spatial path**

- Drop spatial blocks whose vertical center falls inside table **y-ranges** so dual-path compare ignores table interiors ([`exclude_table_regions`](lib/pdf/table.py)).

**Sources:** `detect_tables`, `_detect_side_by_side_tables`, `exclude_table_regions` in [`lib/pdf/table.py`](lib/pdf/table.py).

---

### Dual-path confidence

**Per page**

- Build word multisets for MuPDF and spatial blocks on that page; similarity is overlap over the **larger** multiset count ([`structure.py`](lib/pdf/structure.py)).

**Threshold and NFC**

- If similarity is **below 0.82**, retry equality on **NFC-normalized** joined words ([`SIMILARITY_THRESHOLD`](lib/pdf/types.py)).

**Merged pages**

- For uncertain pages, merge **adjacent page pairs** and recompute similarity; promote both pages when similarity passes ([`compare_extractions`](lib/pdf/structure.py)).

**Pooled remainder**

- Pool **still uncertain** pages into one multiset pair; promote all if NFC strings match or similarity passes ([`compare_extractions`](lib/pdf/structure.py)).

**Tiny regions**

- Remaining uncertain sections with **fewer than ten words** on either side become **low-confidence paragraphs** instead of uncertain sections ([`compare_extractions`](lib/pdf/structure.py)).

**Why:** Recover unicode normalization drift, page-boundary splits, and systematic shifts without losing prompts for truly divergent pages ([`structure.py`](lib/pdf/structure.py) docstring, [`ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md)).

**Sources:** `compare_extractions` in [`lib/pdf/structure.py`](lib/pdf/structure.py).

---

### Table insertion

- Insert each `TABLE` section before the first later section on a later page, or before the first lower block on the **same** page ([`pipeline.py`](lib/pdf/pipeline.py)).

**Sources:** `_run_pipeline` loop over `table_sections`.

---

### Structure

**Early metadata scan**

- Strip leading metadata lines using patterns for document field, reply-to, audience, dates ([`extract_metadata`](lib/metadata_yaml/extract.py)).

**Body size and font ranks**

- Prefer **non-monospace** span sizes for body mode; fall back to all sizes when prose is sparse ([`_detect_body_size`](lib/pdf/structure.py)). Rank sizes **above ~1.05× body** for heading depth hints ([`_rank_font_sizes`](lib/pdf/structure.py)).

**Title**

- If WG21 did not supply a title, pick large-font blocks that are not numbered headings, known section titles, emails, dates, or overly long lines ([`structure_sections`](lib/pdf/structure.py)).

**Headings**

- Combine **section numbering depth**, **font-size rank**, **bold**, and **known section names** via `heading_confidence`; reject **long first-line prose** when confidence is only LOW ([`structure.py`](lib/pdf/structure.py), [`CLAUDE.md`](CLAUDE.md)).
- **Bold-only heading path:** When a section has no section number, no enlarged font, and is not a known section name, but its first line is bold, short (<= `_HEADING_MAX_WORDS`), and free of code-like characters (`_CODE_CHARS`), it enters the heading path at LOW confidence. Level is inferred as `last_heading_level + 1` (minimum 3). `_validate_nesting` then clamps to correct depth. This handles WG21 papers with bold-at-body-size sub-headings (e.g. P3373R4 "Operation States and Stack Frames" under "Background") ([`structure.py`](lib/pdf/structure.py)).
- **Multi-line heading recovery:** When MuPDF splits a heading's section number and title onto separate lines within one block (e.g. line 0 = "1", line 1 = "Abstract", both at the same font size), the emit step joins all same-font lines after a bare section number to recover the full heading text ([`emit.py`](lib/pdf/emit.py) `_render_heading_spans`).
- **Heading+body split at body font size:** `_split_heading_body` splits a multi-line heading section when line 0 is bold and line 1 is not. The bold-drop condition accepts headings at body font size (no size ratio requirement) when line 0 is short (<= `_HEADING_MAX_WORDS` words). This handles PDFs where "Abstract" is bold at 12pt body size with a non-bold prose paragraph merged into the same MuPDF block ([`structure.py`](lib/pdf/structure.py)).

**Lists**

- If every non-empty line matches bullet or numbered patterns, classify as **LIST**; otherwise use **x-indent** versus **body margin** ([`_detect_lists_by_position`](lib/pdf/structure.py)).

**Paragraph merge**

- Merge consecutive paragraphs when the first lacks **terminal punctuation** and the second starts **lowercase** ([`_merge_paragraphs`](lib/pdf/structure.py)).

**Code**

- Merge consecutive **all-monospace** sections into **CODE** with bridging empties; strip known **language labels** ([`_detect_code_blocks`](lib/pdf/structure.py)).

**Wording sections**

- Classify ins or del span runs into wording fenced regions ([`_classify_wording_sections`](lib/pdf/structure.py)).

**Code coalesce and rescue**

- Merge short consecutive **code-shaped** paragraphs with a **narrow** structural regex so rescue can run; then promote paragraphs where **three or more** lines match that regex to **CODE**, skipping wording sections ([`_coalesce_code_paragraphs`](lib/pdf/structure.py), [`_rescue_unfenced_code`](lib/pdf/structure.py)).

**Number demotion**

- Demote **LOW-confidence numbered headings** whose section numbers repeat **at least three** times (paragraph numbering pattern) ([`_demote_repeated_low_confidence_numbers`](lib/pdf/structure.py)).

**Nesting**

- Fix heading levels that **skip more than one** level relative to the previous heading ([`_validate_nesting`](lib/pdf/structure.py)).

**Sources:** `structure_sections` and helpers in [`lib/pdf/structure.py`](lib/pdf/structure.py).

---

### Metadata merge

**Merge**

- Union **structure metadata** with **WG21 block metadata**; WG21 keys **overwrite** on conflict ([`pipeline.py`](lib/pdf/pipeline.py) comment).

**Document id**

- If still missing, parse paper id from **filename** stem ([`DOC_NUM_RE`](lib/__init__.py)).

**Date**

- Date values are parsed via `normalize_date` ([`shared.py`](lib/shared.py)), which handles multiple formats in priority order: ISO `YYYY-MM-DD`, slash-separated `YYYY/MM/DD`, natural language `Month DD, YYYY` (full or abbreviated month names), and European `DD Month YYYY`.
- Pre-label blocks matching `_BARE_DATE_RE` (e.g. "March 26, 2026" alone on a line, without a `Date:` label) are parsed and stored as `date` before the labeled-field scan runs.
- If still missing after block extraction, parse **`creationDate`** from PDF info dict to **YYYY-MM-DD** when possible ([`_parse_pdf_info_date`](lib/pdf/pipeline.py)). This is a last-resort fallback and may reflect the PDF generation timestamp rather than the paper date.

**Revision**

- If stem revision differs from embedded **non-D** document id revision, **rewrite document id** from filename ([`override_revision_from_filename`](lib/metadata_yaml/extract.py)). The HTML path has an identical private copy in [`lib/html/convert.py`](lib/html/convert.py) to avoid a circular import chain.

**Title**

- Else use **first heading** text; else PDF **title** metadata when not boilerplate regex ([`pipeline.py`](lib/pdf/pipeline.py)).

**Reply-to**

- Else PDF **author** when not boilerplate regex ([`apply_pdf_metadata_fallbacks`](lib/metadata_yaml/extract.py)).

**Email enrichment**

- If **no** reply-to entry contained an email yet, scan **first 30** lines of page zero for emails; pair **bare emails** with previous line names; merge into `"Name <email>"` ([`enrich_pdf_reply_to`](lib/metadata_yaml/extract.py)).
- After pairing, remaining bare `<email>` entries are checked against `_author_names` (collected during `_store_field` in [`wg21.py`](lib/pdf/wg21.py)) via last-name heuristic ([`enrich_reply_to_names`](lib/shared.py)). Only matched names are paired; unmatched authors are dropped.

**Pre-heading metadata stripping (two passes)**

Pass 1 (`strip_pre_heading_fragments` in [`metadata_yaml/strip.py`](lib/metadata_yaml/strip.py)): After metadata merge and reply-to enrichment, all non-heading, non-title sections on page 0 that precede the first `HEADING` or `TITLE` section are removed from `sections`. These are metadata fragments (author names, doc numbers, affiliations) already captured in the YAML dict; stripping them prevents duplicate metadata appearing in the Markdown body.

Pass 2 (`strip_pre_content_paragraphs` in [`metadata_yaml/strip.py`](lib/metadata_yaml/strip.py)): After `strip_metadata_headings` runs (see below), a second pass strips all non-heading, non-title sections on page 0 that sit before the first **content** heading. Content headings are identified by `_is_content_heading`: numbered sections (`1. Foo`, `2.3 Bar`) or known section names (Abstract, Motivation, etc.). This catches metadata paragraphs that were originally positioned between the title heading and the first content heading; once `strip_metadata_headings` removes the title echo, these paragraphs become the leading sections and must be cleaned up. Example: `ISO/IEC JTC1 SC22 WG21 N5040 -- 2026-04-07 Braden Ganetsky...` in N5040.

**Metadata heading stripping**

- After pass 1 of pre-heading stripping, `strip_metadata_headings` ([`metadata_yaml/strip.py`](lib/metadata_yaml/strip.py)) removes page-0 HEADING sections before the first content heading (numbered section or KNOWN_SECTIONS name like Abstract, Revision History) whose text duplicates front-matter data. Patterns matched:
  - Document number headings (`Doc. no.:`, `Document Number:`, `Paper Number:`) containing the extracted document ID.
  - Date headings (`Date:`) when a date is already in front matter.
  - Bare date headings (`March 26, 2026`, `26 March 2026`, `2026-03-26`) without a label (`_BARE_DATE_HEADING_RE`).
  - Separator headings (lines of `=`, `-`, `~`, `*`).
  - WG21 category labels (`Programming Language C++`, `ISO/IEC JTC1`, `WG21 PROPOSAL`).
  - Title echo headings: word-stem overlap >= 50% with the front-matter title.
  - Author-list headings: 3+ comma-separated items where >= 80% look like person names (1-4 words, uppercase initial).
  - Single-author headings: 1-4 word names (possibly italic/bold) that match reply-to tokens from metadata (>= 50% token overlap).
- Pass 2 (post-boundary) applies the same patterns to page-0 headings **after** the first content heading. Title-echo matching in pass 2 is suppressed once a body paragraph (PARAGRAPH or LIST) has been seen, preventing legitimate sub-headings from being stripped when they share words with the title (e.g. P3373R4 "Operation States and Stack Frames" under title "Of Operation States and Their Lifetimes").
- This handles PDFs where `wg21.extract_metadata_from_blocks` extracts metadata correctly but the consumed block indices are not propagated to the section-level pipeline, leaving metadata blocks to be classified as headings by font-size-based heading detection.

**Body cleanup (UNCERTAIN sections)**

- After TOC stripping and abstract deduplication, `strip_metadata_from_uncertain` ([`body/abstract.py`](lib/body/abstract.py)) removes lines from UNCERTAIN sections on page 0 that echo front-matter metadata: labeled lines (`Document:`, `Date:`, etc.), bare values matching the title, document ID, audience, date formats, or reply-to author names with emails. Then `reorder_abstract_in_uncertain` moves Abstract heading + paragraph to the top of UNCERTAIN sections when two-column PDF extraction placed them after body text.

**Sources:** `_run_pipeline` in [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py); `_is_content_heading`, `strip_metadata_headings`, `strip_pre_heading_fragments`, `strip_pre_content_paragraphs` in [`lib/metadata_yaml/strip.py`](lib/metadata_yaml/strip.py); `strip_metadata_from_uncertain`, `reorder_abstract_in_uncertain` in [`lib/body/abstract.py`](lib/body/abstract.py).

---

### TOC stripping

**Structural hints**

- When there are **no** heading texts, mark sections whose **second** non-empty line is a bare **page number** and whose **x** clusters with peers ([`_toc_structural_hints`](lib/pdf/pipeline.py)).

**Matching**

- Normalize section first lines and headings (strip dot leaders, trailing page numbers, section prefixes).
- **Multi-line TOC entries:** When the first line of a section is a bare section number (e.g. `"3"`, `"10.5"`, `"A"`) and heading matching fails, the first two lines are joined (e.g. `"3" + "The Proposal"` becomes `"3 The Proposal"`) and re-tested. This handles PDFs where MuPDF splits the TOC section number and title onto separate lines.

**Dot leaders**

- Two forms exist in WG21 PDFs: compact (`....`) matched by `_DOT_LEADER_RE`, and spaced (`. . . .`) matched by `_SPACED_DOT_LEADER_RE`. Both forms count as TOC matches.
- Dot leaders may appear on **line 2+** of multi-line sections (e.g. section text `"6.1\npotentially-convertible-to . . . 5"`). The `full_texts` parameter passes the complete section text for dot-leader detection while `texts` (first-line only) remains the key for heading matching and deduplication.

**Runs**

- Require **three or more** matches with gap bridging up to **three** misses; stop duplicate first lines; include preceding **Contents** label ([`toc.py`](lib/toc.py)).

**Plausibility guard**

- After `find_toc_indices` returns, a plausibility guard rejects phantom TOC detections. A valid TOC must have at least one confirming signal: (a) dot leaders in at least one section, (b) a "Contents" / "Table of Contents" label in the document, or (c) a heading inside the detected block also exists outside it (the inside copy is a TOC reference, the outside is the real heading). Without any signal the detection is treated as a phantom from heading self-matching, common in short dense papers where the gap between headings is small. Rejected phantom TOCs are logged and the `toc_indices` set is cleared ([`pipeline.py`](lib/pdf/pipeline.py)).

**Protection**

- **Label-anchored TOC fallback:** When `find_toc_indices` returns empty but a paragraph-level "Contents" or "Table of Contents" label exists, the pipeline scans forward for numbered entries (lines matching `^\d+[.)]\s+\S`) until the next HEADING. If 3+ numbered lines are found, the label and intermediate sections are marked as TOC. This handles PDFs where TOC entry titles differ from actual section headings, preventing heading-match-based detection from working ([`pipeline.py`](lib/pdf/pipeline.py)).
- After `find_toc_indices` returns, the pipeline protects indices whose section is a `HEADING` with a `KNOWN_SECTIONS` name (e.g. Abstract, Motivation) **unless** the section text contains dot-leaders (`. . .` or `.....`), which marks it as a TOC entry rather than a real heading. Body paragraphs immediately after a protected heading are also protected: the **first** paragraph must meet `_TOC_BODY_PROTECT_MIN_WORDS` (10 words, counted across the full paragraph text, not just the first line) to confirm the heading has real content; once confirmed, all subsequent paragraphs are protected regardless of length until the next heading, table, dot-leader, or section-number entry. **Exception:** when the protected heading is `"abstract"`, the word-count threshold is bypassed for the first body paragraph, because short abstracts (e.g. a single sentence) are legitimate. This prevents short but valid prose paragraphs from being stripped as TOC artifacts. **Additionally**, if the first section after an Abstract heading is classified as a `HEADING` with `Confidence.LOW`, it is reclassified as a `PARAGRAPH` (heading_level reset to 0) and protected. This handles wording-heavy papers (e.g. LaTeX proposals with dominant small-font wording blocks) where `_detect_body_size` miscalibrates the body font size, causing the actual abstract prose to be misclassified as a heading.
- **Duplicate-aware guard (Ticket H fix):** Some PDFs use tab-based or space-aligned TOC formatting without dot-leaders. Before the protection loop, the pipeline collects KNOWN_SECTIONS heading names that exist **outside** the TOC range. If the same name appears both inside and outside, the inside copy is a TOC artifact and is **not** protected. This handles dot-leader-free TOCs without affecting single-occurrence headings that legitimately fall within the TOC range.

**Abstract deduplication**

- After TOC stripping, `_dedup_abstract` removes duplicate `## Abstract` headings. When both a metadata-zone Abstract and a TOC-protected Abstract survive, the empty one (no body paragraph) is removed while the one with body content is kept.
- **Known limitation (Ticket G):** Headings not in `KNOWN_SECTIONS` (e.g. "History") that appear between Abstract and the first numbered section may not be recognized as section-terminating boundaries. Their body text gets merged into the preceding Abstract section. Requires either adding "History" to `KNOWN_SECTIONS` or improving heading detection for non-standard section names.

**Fuzzy guard**

- Exact set handles common case; **fuzzy** `similar()` runs only when normalized heading count **does not exceed 200** ([`toc.py`](lib/toc.py) comment).

**Why:** Fuzzy on huge heading sets is **O(sections x headings)** and can hang large PDFs ([`toc.py`](lib/toc.py)).

**Sources:** `find_toc_indices` in [`lib/toc.py`](lib/toc.py); `_toc_structural_hints` in [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py).

---

### Emit

**Sections**

- YAML front matter via shared `format_front_matter` ([`emit.py`](lib/pdf/emit.py), [`lib/__init__.py`](lib/__init__.py)).

**Uncertainty**

- Prefix uncertain sections with HTML comments carrying **line ranges**; build prompts listing MuPDF and spatial excerpts ([`emit.py`](lib/pdf/emit.py)).

**Inline**

- Merge adjacent monospace spans into one backtick run; **suppress bold** inside headings ([`emit.py`](lib/pdf/emit.py)).

**Table cell rendering**

- When **all** non-whitespace spans in a table cell are monospace, merge into a **single** backtick pair instead of fragmenting per-span. Prevents output like `` `template``<``T``>` `` ([`_render_cell_spans`](lib/pdf/emit.py)).

**Wording sections**

- Wording sections (ins/del fenced divs) preserve **line breaks** when the section contains monospace (code) spans. Prose-only wording sections are collapsed to a single line. This keeps C++ standard wording code readable ([`_render_wording_section`](lib/pdf/emit.py)).

**Empty heading skip**

- Headings that render as bare ATX prefixes with no text (e.g. `## `) are skipped during emission. This handles multi-line heading sections where MuPDF places whitespace on line 0 and the actual title on a later line, and the heading renderer only uses line 0 ([`emit.py`](lib/pdf/emit.py)).

**Post-pass**

- Run `dedup_paragraphs`, strip redundant body metadata tables or lines, strip duplicate leading **H1** when it matches or is a prefix of the title ([`emit.py`](lib/pdf/emit.py), [`lib/__init__.py`](lib/__init__.py)). `strip_leading_h1` also handles plaintext title echoes (no `#` prefix) via fuzzy `_titles_match`, and promotes a bare "Abstract" line immediately after to `## Abstract` when the PDF structurer missed the heading markup ([`shared.py`](lib/shared.py)).
- **Freeform metadata stripping:** After emit, `strip_freeform_metadata_lines` (called from `api.py`) scans the first `_FREEFORM_SCAN_DEPTH` (15) non-blank body lines for leaked metadata labels (`_FREEFORM_META_LABEL_RE`). Labels include `Document`, `Document #`, `Date`, `Audience`, `Reply to`, `Authors`, etc. Also matches `Document PXXXXRN` (no colon) and `ISO/IEC JTC...` header lines via `_FREEFORM_DOC_ID_RE`. When `metadata` is provided (API layer), three content-aware checks fire before the structural-line break: (a) heading/list lines with metadata labels (`_HEADING_META_LABEL_RE`, `_LIST_META_LABEL_RE`), (b) bare date headings (`_HEADING_BARE_DATE_RE`), (c) author-name headings matching `reply-to` tokens, and (d) bare author+email lines matching `reply-to` names. Continuation detection handles both indented/comma-prefixed lines and "label-only" lines where the label ends with `:` and the value sits on the next line without indentation ([`shared.py`](lib/shared.py)).
- **Orphan TOC strip:** Remove bullet-list Tables of Contents that appear between front matter and the first `##` heading. Some WG21 PDFs use minimal TOCs (section names as bullets, no dot-leaders or page numbers) that survive TOC detection. The strip fires when at least 3 bullets exist and at least 50% match actual document headings. Applied in both PDF and HTML emit paths ([`shared.py`](lib/shared.py) `strip_orphan_toc_list`).

**Wording prompts**

- Append wording diagnostic prompts when the wording pass reported problems ([`pipeline.py`](lib/pdf/pipeline.py)).

**Sources:** `emit_markdown`, `emit_prompts`, [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py).

---

## Appendix: rules by source file

Links point to sections above.

| Module | Topics |
|--------|--------|
| [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py) | [Early exits](#early-exits), [Body-font census](#body-font-census), [Page zero metadata](#page-zero-metadata), [Table insertion](#table-insertion), [TOC hints](#toc-stripping), [Emit prompts](#emit) |
| [`lib/pdf/pipeline.py`](lib/pdf/pipeline.py) | [Metadata merge](#metadata-merge) (orchestration), [TOC stripping](#toc-stripping) (plausibility guard, label-anchored fallback, protection, dedup), [Body cleanup](#body-cleanup-uncertain-sections) |
| [`lib/metadata_yaml/extract.py`](lib/metadata_yaml/extract.py) | [Structure](#structure) (early metadata scan), [Metadata merge](#metadata-merge) (fallbacks, revision override, reply-to enrichment) |
| [`lib/metadata_yaml/strip.py`](lib/metadata_yaml/strip.py) | [Metadata merge](#metadata-merge) (pre-heading stripping, metadata heading stripping) |
| [`lib/body/abstract.py`](lib/body/abstract.py) | [TOC stripping](#toc-stripping) (abstract dedup), [Body cleanup](#body-cleanup-uncertain-sections) (metadata echo strip, abstract reorder) |
| [`lib/pdf/extract.py`](lib/pdf/extract.py) | [Dual extraction](#dual-extraction), [Links](#links) |
| [`lib/pdf/types.py`](lib/pdf/types.py) | Spatial ratios, regex helpers, [Readability](#readability-gate), similarity threshold |
| [`lib/pdf/cleanup.py`](lib/pdf/cleanup.py) | [Hidden stripping](#hidden-region-stripping), [Header and footer](#header-and-footer-stripping), [Text cleanup](#text-cleanup) |
| [`lib/pdf/mono.py`](lib/pdf/mono.py) | [Monospace](#monospace) |
| [`lib/pdf/wording.py`](lib/pdf/wording.py) | [Line drawings](#line-drawings), [Wording](#wording) |
| [`lib/pdf/spans.py`](lib/pdf/spans.py) | [Text cleanup](#text-cleanup) normalization |
| [`lib/pdf/wg21.py`](lib/pdf/wg21.py) | [Page zero metadata](#page-zero-metadata) |
| [`lib/pdf/figures.py`](lib/pdf/figures.py) | [Figure detection](#figure-detection) |
| [`lib/pdf/table.py`](lib/pdf/table.py) | [Tables](#tables) |
| [`lib/pdf/structure.py`](lib/pdf/structure.py) | [Dual-path confidence](#dual-path-confidence), [Structure](#structure), [Figure detection](#figure-detection) (FIGURE classification) |
| [`lib/toc.py`](lib/toc.py) | [TOC stripping](#toc-stripping) |
| [`lib/pdf/emit.py`](lib/pdf/emit.py) | [Emit](#emit) |
| [`lib/__init__.py`](lib/__init__.py) | Link schemes, front matter helpers, [Emit](#emit) post-pass |
