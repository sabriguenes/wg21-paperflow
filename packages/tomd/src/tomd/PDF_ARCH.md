# PDF conversion rules (tomd)

This document describes how the **PDF** branch of tomd turns a WG21-style PDF into Markdown. It mirrors execution order in [`lib/pdf/__init__.py`](lib/pdf/__init__.py) `_run_pipeline`, groups **multi-signal** decisions together, and adds **Why** only where intent is clear from code comments, module docstrings, [`lib/pdf/ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md), or [`CLAUDE.md`](CLAUDE.md).

For deeper technique tables and module maps, see [`lib/pdf/ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md).

## Contents

- [Goals](#goals)
- [Principles and corpus assumptions](#principles-and-corpus-assumptions)
- [Before changing behavior](#before-changing-behavior)
- [Pipeline](#pipeline)
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

**Why:** Presentation geometry and huge drafts break dual-path assumptions and are out of scope for committee-paper conversion ([`__init__.py`](lib/pdf/__init__.py) docstrings).

**Sources:** `_is_slide_deck`, `_is_standards_draft` in [`lib/pdf/__init__.py`](lib/pdf/__init__.py).

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

### Body-font census

- Count characters per lowercased font name across **MuPDF** blocks; keep the **top five** font names as `body_fonts` for hidden-region detection ([`__init__.py`](lib/pdf/__init__.py)).

**Sources:** `_run_pipeline` font loop in [`lib/pdf/__init__.py`](lib/pdf/__init__.py).

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

- After stripping hidden text, join MuPDF block text and reject conversion when text is **too short**, **alphanumeric ratio** in an early sample is **below 0.3**, or **slash density** is too high ([`types.py`](lib/pdf/types.py) `is_readable`).

**Why:** Detect scanned or garbage extraction early ([`types.py`](lib/pdf/types.py)).

**Sources:** `is_readable` in [`lib/pdf/types.py`](lib/pdf/types.py).

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

**Why:** WG21 frameworks use fixed hue bands; hyperlinks are blue and excluded; minimum span counts suppress noise ([`wording.py`](lib/pdf/wording.py) module docstring).

**Sources:** `classify_wording` in [`lib/pdf/wording.py`](lib/pdf/wording.py).

---

### Text cleanup

**Cleanup**

- Strip Unicode **Cf** format characters, replace NBSP, collapse spaces outside monospace, **dehyphenate** across lines with compound-prefix guards, **join blocks across pages** when punctuation and case indicate continuation ([`cleanup.py`](lib/pdf/cleanup.py)).

**Span normalization**

- Snap bold or italic boundaries to **word edges** for adjacent non-monospace spans ([`spans.py`](lib/pdf/spans.py)).

**Sources:** `cleanup_text` in [`lib/pdf/cleanup.py`](lib/pdf/cleanup.py); `normalize_spans` in [`lib/pdf/spans.py`](lib/pdf/spans.py).

---

### Page zero metadata

**WG21 block metadata**

- Scan early MuPDF blocks with **text color lightness** map from **space characters** in texttrace so Type 3 black glyphs still reveal watermark lightness ([`_get_page0_text_colors`](lib/pdf/__init__.py), [`wg21.py`](lib/pdf/wg21.py)).
- Recognize labels both with colon (`Audience:`) via `_LABEL_RE` and without colon (`Audience` alone on a line) via `_BARE_LABEL_RE` (Scrivener-style PDFs).
- **Intent extraction:** `Intent:` (colon or bare) is recognized as a metadata label. The value is stored lowercase (e.g. `Inform` becomes `inform`, `Ask` becomes `ask`). Only the exact label `Intent` is accepted; no synonyms.
- `_LABEL_RE` includes non-metadata stop-labels (`Issues`, `Previous`, `Follow up to`, `Co-authors`, `Source`, `Reference`, `Contributor`) that terminate value collection without storing a field, preventing adjacent lines from bleeding into the preceding field.
- Strip author contamination from audience values: truncate at email addresses, angle brackets, or double-space boundaries that indicate merged PDF lines.
- **Target as audience fallback:** `Target:` maps to the audience field only when no explicit `Audience:` or `Subgroup:` was already extracted. This prevents Target (typically `C++26`) from overwriting the committee audience value.
- **Reply-to wins:** Explicit `Reply-to:` labels store directly into `reply-to`. `Author:`, `Editor:`, and `Co-author:` labels store into `_author_names` only and fill `reply-to` as fallback when no explicit Reply-to was extracted. When an explicit Reply-to entry has an email matching a bare-name fallback entry, the bare name is upgraded in place. Aligns with the HTML extractor bucket strategy ([`wg21.py`](lib/pdf/wg21.py)).

**Why:** Title versus watermark disambiguation uses lightness proxy ([`__init__.py`](lib/pdf/__init__.py) docstring).

**Sources:** `extract_metadata_from_blocks`, `_get_page0_text_colors`.

---

### Tables

**Two signals**

- **Columnar blocks:** consecutive blocks whose lines show **x gaps** above a threshold form candidate rows ([`table.py`](lib/pdf/table.py)).
- **Shared column profile:** x positions that appear **together** in the same **y-band** on **two or more** bands qualify as table columns; lone margin columns do not ([`table.py`](lib/pdf/table.py)).

**Orphans**

- Single-line blocks aligned to known columns can merge into the **next** row when lookahead confirms a table row; **same-page only** ([`table.py`](lib/pdf/table.py) module docstring).

**Spatial path**

- Drop spatial blocks whose vertical center falls inside table **y-ranges** so dual-path compare ignores table interiors ([`exclude_table_regions`](lib/pdf/table.py)).

**Sources:** `detect_tables`, `exclude_table_regions` in [`lib/pdf/table.py`](lib/pdf/table.py).

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

- Insert each `TABLE` section before the first later section on a later page, or before the first lower block on the **same** page ([`__init__.py`](lib/pdf/__init__.py)).

**Sources:** `_run_pipeline` loop over `table_sections`.

---

### Structure

**Early metadata scan**

- Strip leading metadata lines using patterns for document field, reply-to, audience, dates ([`_extract_metadata`](lib/pdf/structure.py)).

**Body size and font ranks**

- Prefer **non-monospace** span sizes for body mode; fall back to all sizes when prose is sparse ([`_detect_body_size`](lib/pdf/structure.py)). Rank sizes **above ~1.05× body** for heading depth hints ([`_rank_font_sizes`](lib/pdf/structure.py)).

**Title**

- If WG21 did not supply a title, pick large-font blocks that are not numbered headings, known section titles, emails, dates, or overly long lines ([`structure_sections`](lib/pdf/structure.py)).

**Headings**

- Combine **section numbering depth**, **font-size rank**, **bold**, and **known section names** via `heading_confidence`; reject **long first-line prose** when confidence is only LOW ([`structure.py`](lib/pdf/structure.py), [`CLAUDE.md`](CLAUDE.md)).

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

- Union **structure metadata** with **WG21 block metadata**; WG21 keys **overwrite** on conflict ([`__init__.py`](lib/pdf/__init__.py) comment).

**Document id**

- If still missing, parse paper id from **filename** stem ([`DOC_NUM_RE`](lib/__init__.py)).

**Date**

- Date values are parsed via `normalize_date` ([`shared.py`](lib/shared.py)), which handles multiple formats in priority order: ISO `YYYY-MM-DD`, slash-separated `YYYY/MM/DD`, natural language `Month DD, YYYY` (full or abbreviated month names), and European `DD Month YYYY`.
- Pre-label blocks matching `_BARE_DATE_RE` (e.g. "March 26, 2026" alone on a line, without a `Date:` label) are parsed and stored as `date` before the labeled-field scan runs.
- If still missing after block extraction, parse **`creationDate`** from PDF info dict to **YYYY-MM-DD** when possible ([`_parse_pdf_info_date`](lib/pdf/__init__.py)). This is a last-resort fallback and may reflect the PDF generation timestamp rather than the paper date.

**Revision**

- If stem revision differs from embedded **non-D** document id revision, **rewrite document id** from filename ([`_override_revision_from_filename`](lib/pdf/__init__.py)).

**Title**

- Else use **first heading** text; else PDF **title** metadata when not boilerplate regex ([`__init__.py`](lib/pdf/__init__.py)).

**Reply-to**

- Else PDF **author** when not boilerplate regex ([`__init__.py`](lib/pdf/__init__.py)).

**Email enrichment**

- If **no** reply-to entry contained an email yet, scan **first 30** lines of page zero for emails; pair **bare emails** with previous line names; merge into `"Name <email>"` ([`_enrich_pdf_reply_to`](lib/pdf/__init__.py)).
- After pairing, remaining bare `<email>` entries are checked against `_author_names` (collected during `_store_field` in [`wg21.py`](lib/pdf/wg21.py)) via last-name heuristic ([`enrich_reply_to_names`](lib/shared.py)). Only matched names are paired; unmatched authors are dropped.

**Sources:** `_run_pipeline` metadata section in [`lib/pdf/__init__.py`](lib/pdf/__init__.py).

---

### TOC stripping

**Structural hints**

- When there are **no** heading texts, mark sections whose **second** non-empty line is a bare **page number** and whose **x** clusters with peers ([`_toc_structural_hints`](lib/pdf/__init__.py)).

**Matching**

- Normalize section first lines and headings (strip dot leaders, trailing page numbers, section prefixes).

**Runs**

- Require **three or more** matches with gap bridging up to **three** misses; stop duplicate first lines; include preceding **Contents** label ([`toc.py`](lib/toc.py)).

**Fuzzy guard**

- Exact set handles common case; **fuzzy** `similar()` runs only when normalized heading count **does not exceed 200** ([`toc.py`](lib/toc.py) comment).

**Why:** Fuzzy on huge heading sets is **O(sections × headings)** and can hang large PDFs ([`toc.py`](lib/toc.py)).

**Sources:** `find_toc_indices` in [`lib/toc.py`](lib/toc.py); `_toc_structural_hints` in [`lib/pdf/__init__.py`](lib/pdf/__init__.py).

---

### Emit

**Sections**

- YAML front matter via shared `format_front_matter` ([`emit.py`](lib/pdf/emit.py), [`lib/__init__.py`](lib/__init__.py)).

**Uncertainty**

- Prefix uncertain sections with HTML comments carrying **line ranges**; build prompts listing MuPDF and spatial excerpts ([`emit.py`](lib/pdf/emit.py)).

**Inline**

- Merge adjacent monospace spans into one backtick run; **suppress bold** inside headings ([`emit.py`](lib/pdf/emit.py)).

**Post-pass**

- Run `dedup_paragraphs`, strip redundant body metadata tables or lines, strip duplicate leading **H1** when it matches or is a prefix of the title ([`emit.py`](lib/pdf/emit.py), [`lib/__init__.py`](lib/__init__.py)).

**Wording prompts**

- Append wording diagnostic prompts when the wording pass reported problems ([`__init__.py`](lib/pdf/__init__.py)).

**Sources:** `emit_markdown`, `emit_prompts`, [`lib/pdf/__init__.py`](lib/pdf/__init__.py).

---

## Appendix: rules by source file

Links point to sections above.

| Module | Topics |
|--------|--------|
| [`lib/pdf/__init__.py`](lib/pdf/__init__.py) | [Early exits](#early-exits), [Body-font census](#body-font-census), [Page zero metadata](#page-zero-metadata), [Table insertion](#table-insertion), [Metadata merge](#metadata-merge), [TOC hints](#toc-stripping), [Emit prompts](#emit) |
| [`lib/pdf/extract.py`](lib/pdf/extract.py) | [Dual extraction](#dual-extraction), [Links](#links) |
| [`lib/pdf/types.py`](lib/pdf/types.py) | Spatial ratios, regex helpers, [Readability](#readability-gate), similarity threshold |
| [`lib/pdf/cleanup.py`](lib/pdf/cleanup.py) | [Hidden stripping](#hidden-region-stripping), [Header and footer](#header-and-footer-stripping), [Text cleanup](#text-cleanup) |
| [`lib/pdf/mono.py`](lib/pdf/mono.py) | [Monospace](#monospace) |
| [`lib/pdf/wording.py`](lib/pdf/wording.py) | [Line drawings](#line-drawings), [Wording](#wording) |
| [`lib/pdf/spans.py`](lib/pdf/spans.py) | [Text cleanup](#text-cleanup) normalization |
| [`lib/pdf/wg21.py`](lib/pdf/wg21.py) | [Page zero metadata](#page-zero-metadata) |
| [`lib/pdf/table.py`](lib/pdf/table.py) | [Tables](#tables) |
| [`lib/pdf/structure.py`](lib/pdf/structure.py) | [Dual-path confidence](#dual-path-confidence), [Structure](#structure) |
| [`lib/toc.py`](lib/toc.py) | [TOC stripping](#toc-stripping) |
| [`lib/pdf/emit.py`](lib/pdf/emit.py) | [Emit](#emit) |
| [`lib/__init__.py`](lib/__init__.py) | Link schemes, front matter helpers, [Emit](#emit) post-pass |
