# HTML conversion rules (tomd)

This document describes how the **HTML** branch of tomd turns WG21-style HTML into Markdown. It follows [`convert_html`](lib/html/__init__.py) and [`render_body`](lib/html/render.py), groups multi-step behaviors into clusters, and adds **Why** only where intent is clear from comments, module docstrings, or [`lib/html/ARCHITECTURE.md`](lib/html/ARCHITECTURE.md).

Deep technique numbering lives in [`lib/html/ARCHITECTURE.md`](lib/html/ARCHITECTURE.md).

## Contents

- [Goals](#goals)
- [Principles and corpus assumptions](#principles-and-corpus-assumptions)
- [Before changing behavior](#before-changing-behavior)
- [Pipeline](#pipeline)
- [Appendix: rules by source file](#appendix-rules-by-source-file)

## Goals

1. Trace HTML conversion end to end without opening every helper first.
2. Highlight generator-specific paths and shared post-processing so corpus-wide regressions are easier to avoid.

## Principles and corpus assumptions

- **Single DOM truth:** Unlike PDF, there is no dual extraction or per-page uncertainty routing; structure comes from the parsed tree ([`ARCHITECTURE.md`](lib/html/ARCHITECTURE.md)).
- **Forgiving parse:** BeautifulSoup uses the stdlib **`html.parser`**, which tolerates malformed HTML but can mis-nest tags; the renderer applies explicit repairs ([`extract.py`](lib/html/extract.py), [`render.py`](lib/html/render.py)).
- **Problems become prompts:** Unknown generators and other issues are recorded as strings and wrapped into LLM-ready prompts ([`convert_html`](lib/html/__init__.py)).
- **Shared emit helpers:** PDF and HTML both call [`lib/__init__.py`](lib/__init__.py) `format_front_matter`, `dedup_paragraphs`, `strip_redundant_body_meta`, and `strip_leading_h1` after assembly.

## Before changing behavior

- Run or extend tests in `packages/tomd/tests/` (`test_html_extract.py`, `test_html_render.py`, goldens).
- **HackMD**, **dascandy/fiets**, and **unknown** share the **generic** metadata extractor (`extract_metadata` falls through after `schultke`); only **mpark**, **bikeshed**, **hand-written**, **wg21**, and **schultke** get dedicated extractors ([`extract_metadata`](lib/html/extract.py)).
- Table rendering has four paths; changing dispatch affects every generator that uses tables with complex cells.
- Editing [`strip_boilerplate`](lib/html/extract.py) affects all generators: universal removals always run first.

### Corpus-risk hotspots

Unknown generator prompt suppression when generic metadata still succeeded; lossy table markers and QA coupling; `_needs_flat_reconstruction` heuristics; list item extraction order (nested lists and code blocks); reply-to enrichment pairing **1:1** bare names and emails; paragraph deduplication shared with PDF.

---

## Pipeline

### Parse

- Read file as **UTF-8** with replacement on decode errors ([`convert_html`](lib/html/__init__.py)).
- Build a BeautifulSoup tree with **`html.parser`** ([`parse_html`](lib/html/extract.py)).

**Sources:** `parse_html`, [`lib/html/extract.py`](lib/html/extract.py).

---

### Generator detection

**Priority order**

1. Any `<meta name="generator">`: substring **`mpark/wg21`** yields **mpark**; **`bikeshed`** (case-insensitive) yields **bikeshed**; **`dascandy/fiets`** (case-insensitive) yields **dascandy/fiets**.
2. Any `<link href>` matching HackMD pattern yields **hackmd** (see `_HACKMD_RE` in source).
3. `<title>` text containing **hackmd** yields **hackmd**.
4. `<header id="title-block-header">` present yields **mpark** (structural fallback).
5. `<address>` present yields **hand-written**.
6. `<div class="wg21-head">` yields **wg21**.
7. `<code-block>` present yields **schultke**.
8. Otherwise **unknown**.

**Why:** Meta tag is authoritative when present; DOM fingerprints disambiguate stripped or odd exports ([`detect_generator`](lib/html/extract.py) docstring).

**Sources:** `detect_generator`, [`lib/html/extract.py`](lib/html/extract.py).

---

### Metadata extraction

**Dispatch**

- **mpark:** `_extract_mpark_metadata` (header table, optional mailto-only header path).
- **bikeshed:** `_extract_bikeshed_metadata`.
- **hand-written:** `_extract_handwritten_metadata`.
- **wg21:** `_extract_wg21_metadata` (`div.wg21-head` plus `<dl>`).
- **schultke:** `_extract_schultke_metadata` (generic table pass **plus** `<dl>` overlay for labels containing reply or author fields).
- **All others** (including **hackmd**, **dascandy/fiets**, **unknown**): `_extract_generic_metadata`.

**Shared label mapping**

- `_normalize_label` strips and lowercases labels; `_match_field` maps synonyms to **document**, **date**, **audience**, **reply-to** via `_FIELD_SYNONYMS` ([`extract.py`](lib/html/extract.py)).
- When no exact synonym matches, `_match_field` falls back to `similarity.fuzzy_match_label` against the inverted synonym keys (`_ALL_SYNONYMS`) with a threshold of 0.82. This recovers metadata from HTML sources with typos in label text (e.g. "Repy-to", "Auther"). A warning is logged on fuzzy matches.

**Reply-to enrichment post-pass**

- Always runs `_enrich_reply_to` after the generator-specific extractor ([`extract_metadata`](lib/html/extract.py)).
- **Bootstrap:** If there was no `reply-to`, seed from emails in the metadata region (before first `<h2>`) via mailto scan then plain-text `EMAIL_RE` in common containers ([`_collect_metadata_emails`](lib/html/extract.py)).
- **Merge in-list:** When bare names and `<email>` entries count **match 1:1**, zip into `"Name <email>"`.
- **External emails:** Pair remaining bare names with unassigned emails from the region when counts align; otherwise append `<email>` entries.
- **Context names:** For `<email>`-only entries, recover adjacent names from parent text (Pandoc-style `Name <a mailto>`) ([`_recover_name_from_context`](lib/html/extract.py)).

**Why:** Phase 1 is structural per generator; phase 2 fills gaps without removing existing data (`_enrich_reply_to` docstring).

**Sources:** `extract_metadata`, `_extract_*_metadata`, `_enrich_reply_to`, [`lib/html/extract.py`](lib/html/extract.py).

---

### Filename fallbacks

- If metadata lacks **document**, parse paper id from filename stem with [`DOC_NUM_RE`](lib/__init__.py) ([`convert_html`](lib/html/__init__.py)).
- **`_override_revision_from_filename`:** Same rules as PDF: align revision from stem when base number matches and embedded id is **not** a **D** draft ([`lib/html/__init__.py`](lib/html/__init__.py)).

**Sources:** `convert_html`, `_override_revision_from_filename`, [`lib/html/__init__.py`](lib/html/__init__.py).

---

### Boilerplate stripping

**Universal**

- Remove every **`style`**, **`script`**, **`link`**, and **`meta`** tag.
- Remove `#TOC`, `#toc`, and `nav[data-fill-with="table-of-contents"]`.

**Title block header**

- Remove `<header id="title-block-header">` whenever it exists (after universal passes).

**Per generator**

- **bikeshed:** Remove `div[data-fill-with]`, `h1.p-name`, `h2#profile-and-date`.
- **hand-written:** Remove `<address>` and `table.header`.
- **wg21:** Remove `div.wg21-head`, `div.toc`.

**Unknown generator**

- Append a problem string warning incomplete metadata and leftover boilerplate ([`strip_boilerplate`](lib/html/extract.py)).

**Sources:** `strip_boilerplate`, [`lib/html/extract.py`](lib/html/extract.py).

---

### Unknown generator prompts filter

- If generator is **unknown** but metadata dict is **non-empty**, drop problems whose text contains **"Unrecognized"** so a usable generic extraction does not noise the operator ([`convert_html`](lib/html/__init__.py)).

**Sources:** [`lib/html/__init__.py`](lib/html/__init__.py).

---

### DOM repair before render

**Misnested blocks**

- Iteratively hoist block-level tags (`p`, `pre`, headings, `table`, lists, etc.) out of inline parents (`p`, `span`, `a`, emphasis, headings) when `html.parser` nested them illegally, preserving inline fragments in wrapper siblings ([`_fix_misnested_blocks`](lib/html/render.py)).

**Misnested list items**

- Promote nested `<li>` elements to siblings under the same parent list ([`_fix_misnested_list_items`](lib/html/render.py)).

**Why:** stdlib parser does not auto-close inline context when blocks appear ([`render.py`](lib/html/render.py) docstrings).

**Sources:** `_fix_misnested_blocks`, `_fix_misnested_list_items`, `render_body`, [`lib/html/render.py`](lib/html/render.py).

---

### Render traversal

- `render_body` runs repairs, then uses **`body`** or the whole soup if missing, walks **direct children**, skips HTML comments, renders tags via `_render_element`, joins non-empty parts with **double newlines** ([`render_body`](lib/html/render.py)).

**Structural wrappers**

- `section`, `main`, `article`, `aside`, `figure`, `figcaption`, `header`, `footer`, `nav`, `details`, `summary` recurse children without adding extra markup.

**Sources:** `render_body`, `_render_children`, `_render_element`, [`lib/html/render.py`](lib/html/render.py).

---

### Headings

- Map `h1` through `h6` to ATX Markdown.
- Inline assembly skips elements whose class is **`header-section-number`**, **`secno`**, or **`self-link`**.
- Strip leading dotted-decimal section numbers via [`SECTION_NUM_PREFIX_RE`](lib/__init__.py).
- Strip a wrapping `**...**` around the whole heading text when present.
- Collapse internal whitespace newlines to spaces ([`_render_heading`](lib/html/render.py)).

**Sources:** `_render_heading`, [`lib/html/render.py`](lib/html/render.py).

---

### Paragraphs and code-shaped paragraphs

**Normal paragraph**

- Inline render plus `strip_format_chars`, collapse whitespace to single spaces ([`_render_paragraph`](lib/html/render.py)).

**Code paragraph (dascandy or fiets)**

- When `<p>` contains **only** whitespace and `<span class="code">` children, emit a **fenced code block** with language **cpp** from paragraph text ([`_is_code_paragraph`](lib/html/render.py)).

**Why:** Those generators use span-code paragraphs for declarations that must not flatten to prose ([`render.py`](lib/html/render.py) docstring).

**Sources:** `_render_paragraph`, `_is_code_paragraph`, [`lib/html/render.py`](lib/html/render.py).

---

### Fenced code blocks

**`<pre>`**

- Prefer nested `<code>`; detect language from `sourceCode*`, `language-*`, known short names, or **`cpp`** default when generator is **mpark** ([`_detect_code_language`](lib/html/render.py)).

**`<code-block>` (Schultke)**

- Custom element rendered as a fenced code block with language **cpp** ([`_render_code_block_custom`](lib/html/render.py)).

**Div shortcuts**

- `div.sourceCode` containing `pre` delegates to `_render_pre`; `div.code` becomes a fenced code block with language **cpp** ([`_render_div`](lib/html/render.py)).

**Sources:** `_render_pre`, `_render_code_block_custom`, `_render_div`, [`lib/html/render.py`](lib/html/render.py).

---

### Tables

**Dispatch**

1. If table contains `<pre>` or `<code-block>`, emit **code table**: each non-empty block becomes its own fenced code block with language **cpp**, prefixed with **`<!-- tomd:lossy-table -->`** ([`_render_code_table`](lib/html/render.py)).
2. Else if **nested `<table>`**, parser-mangled nested cells, or certain block content inside cells (`pre`, lists, blockquote, multi-paragraph cells), use **flat reconstruction**: collect cells in document order, split rows at `<tr>` boundaries, emit lossy pipe table with marker ([`_render_table_flat`](lib/html/render.py)).
3. Else if any **`colspan`** or **`rowspan`**, **denormalize** into a rectangular grid (two-pass fill), emit lossy pipe table with marker ([`_render_denormalized_table`](lib/html/render.py)).
4. Else **simple pipe table** from direct `tr` or `thead` or `tbody` or `tfoot` rows, **no** lossy marker ([`_render_table`](lib/html/render.py)).

**Cell text**

- Escape `|` as `\|`; collapse whitespace; header row bold wrappers stripped like headings.

**Why:** CommonMark cannot represent every HTML table; lossy paths are marked for QA ([`ARCHITECTURE.md`](lib/html/ARCHITECTURE.md), [`render.py`](lib/html/render.py)).

**Sources:** `_render_table`, `_needs_flat_reconstruction`, `_has_spans`, `_render_code_table`, [`lib/html/render.py`](lib/html/render.py).

---

### Lists

- Direct `<li>` children only at top level; nested `ul` or `ol` extracted before inline text so content is not duplicated.
- Nested lists re-rendered with two-space indent per line.
- `pre` and `<code-block>` inside `li` extracted and emitted as fenced blocks after the bullet line ([`_render_list`](lib/html/render.py)).

**Sources:** `_render_list`, [`lib/html/render.py`](lib/html/render.py).

---

### Divs: wording and notes

**Wording**

- Classes **`wording`**, **`wording-add`**, **`wording-remove`** map to Pandoc fenced divs `:::wording`, `:::wording-add`, `:::wording-remove` ([`_render_wording_div`](lib/html/render.py)).

**Notes**

- Classes **`note`**, **`example`**, **`advisement`** render as blockquotes (`>` lines).

**Custom blocks**

- **`example-block`**, **`note-block`**, **`bug-block`** render as blockquotes wrapping child content ([`_render_element`](lib/html/render.py)).

**Sources:** `_render_div`, `_render_element`, [`lib/html/render.py`](lib/html/render.py).

---

### Blockquotes and definition lists

**`<blockquote>`**

- Render children, prefix each line with `>` ([`_render_blockquote`](lib/html/render.py)).

**`<dl>`**

- `dt` becomes `**term**`; `dd` becomes `: definition` plus any extracted non-recursive code blocks ([`_render_dl`](lib/html/render.py)).

**Sources:** `_render_blockquote`, `_render_dl`, [`lib/html/render.py`](lib/html/render.py).

---

### Inline markup and links

**Formatting**

- `<code>` to backticks; `<strong>` or `<b>` to `**`; `<em>` or `<i>` to `*`; `<br>` to newline.
- `<ins>`, `<del>`, `<sub>`, `<sup>` pass through as inline HTML.
- `<tt->` and span alias render as backticks; transparent containers (`span`, many others, `h-`, `f-serif`, `c-`) concatenate child output ([`_inline_text`](lib/html/render.py)).

**Links**

- Anchors `#...` render as plain text.
- Other schemes: emit `[text](href)` only when URL scheme is in [`ALLOWED_LINK_SCHEMES`](lib/__init__.py) (`http`, `https`, `mailto`); otherwise plain text ([`_inline_text`](lib/html/render.py)).

**Sources:** `_inline_text`, `_inline_text_excluding`, [`lib/html/render.py`](lib/html/render.py).

---

### Horizontal rule

- `<hr>` becomes Markdown `---` on its own ([`_render_element`](lib/html/render.py)).

---

### Title fallback from body

- If metadata exists but **title** is missing, take first **`## ...`** line from rendered body Markdown ([`convert_html`](lib/html/__init__.py)).

**Sources:** [`lib/html/__init__.py`](lib/html/__init__.py).

---

### Assemble front matter and body

- If metadata exists, prepend `format_front_matter` output ([`convert_html`](lib/html/__init__.py)).
- Strip a leading standalone `---` line from body text repeatedly so embedded YAML-looking lines do not duplicate front matter.

**Sources:** [`lib/html/__init__.py`](lib/html/__init__.py), [`lib/__init__.py`](lib/__init__.py).

---

### Post-pass cleanup

- **`dedup_paragraphs`** on full markdown string.
- **`strip_leading_h1`** on body when front matter present (slice after first closing front matter newline).
- **`strip_redundant_body_meta`** removes redundant metadata lines or tables.
- **`strip_leading_h1`** again after redundant strip ([`convert_html`](lib/html/__init__.py)).

**Why:** Same finishing sequence as PDF emit keeps HTML and PDF outputs consistent ([`lib/pdf/emit.py`](lib/pdf/emit.py)).

**Sources:** [`lib/html/__init__.py`](lib/html/__init__.py), [`lib/__init__.py`](lib/__init__.py).

---

### Prompts

- Non-empty **problems** list becomes one prompt per entry with fixed framing text ([`convert_html`](lib/html/__init__.py)).

**Sources:** [`lib/html/__init__.py`](lib/html/__init__.py).

---

## Appendix: rules by source file

| Module | Topics |
|--------|--------|
| [`lib/html/__init__.py`](lib/html/__init__.py) | [Filename fallbacks](#filename-fallbacks), [Unknown filter](#unknown-generator-prompts-filter), [Title fallback](#title-fallback-from-body), [Assemble](#assemble-front-matter-and-body), [Post-pass](#post-pass-cleanup), [Prompts](#prompts) |
| [`lib/html/extract.py`](lib/html/extract.py) | [Parse](#parse), [Generator detection](#generator-detection), [Metadata extraction](#metadata-extraction), [Boilerplate stripping](#boilerplate-stripping) |
| [`lib/html/render.py`](lib/html/render.py) | [DOM repair](#dom-repair-before-render), [Render traversal](#render-traversal), [Headings](#headings), [Paragraphs](#paragraphs-and-code-shaped-paragraphs), [Fenced code](#fenced-code-blocks), [Tables](#tables), [Lists](#lists), [Divs](#divs-wording-and-notes), [Blockquotes and DL](#blockquotes-and-definition-lists), [Inline](#inline-markup-and-links), [Horizontal rule](#horizontal-rule) |
| [`lib/__init__.py`](lib/__init__.py) | Shared helpers, link schemes, front matter formatting |
