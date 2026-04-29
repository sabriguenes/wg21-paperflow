# tomd HTML Converter Architecture

## Overview

The HTML converter transforms WG21 HTML papers to Markdown via DOM traversal. Unlike the PDF converter (which uses dual-path extraction with confidence scoring), the HTML converter has a single source of truth - the DOM tree. There is no dual-path comparison, no uncertainty, and no spatial analysis. Semantic HTML tags map directly to Markdown elements.

The converter handles six generator families (mpark/wg21, Bikeshed, hand-written, HackMD, unknown) with per-generator metadata extraction and boilerplate stripping. Problems (unrecognized generators, unconvertible structures) surface through the prompts file.

## Pipeline (6 steps)

| Step | What | Module |
|------|------|--------|
| 1 | Parse HTML (BeautifulSoup, forgiving of malformed HTML) | `extract.py` |
| 2 | Detect generator family from meta tags and DOM structure | `extract.py` |
| 3 | Extract metadata (title, document, date, audience, reply-to) | `extract.py` |
| 4 | Strip boilerplate (CSS, scripts, TOC, generator chrome) | `extract.py` |
| 5 | Walk DOM, render to Markdown | `render.py` |
| 6 | Assemble front matter + body, ASCII-encode, generate prompts | `__init__.py` |

## Techniques by Layer

### Layer 1: Parsing and Generator Detection (2 techniques)

**T1. Forgiving HTML parse**
- `extract.py:parse_html`
- Uses BeautifulSoup with `html.parser` (stdlib, handles malformed HTML)
- Input: raw HTML string. Output: `BeautifulSoup` tree.

**T2. Generator fingerprinting**
- `extract.py:detect_generator`
- 7 detection strategies in priority order:
  1. `<meta name="generator" content="mpark/wg21">` -> `"mpark"`
  2. `<meta name="generator">` containing "bikeshed" -> `"bikeshed"`
  3. `<link href>` matching "hackmd" or `<title>` containing "hackmd" -> `"hackmd"`
  4. `div.wg21-head` present -> `"wg21"`
  5. `<header id="title-block-header">` present -> `"mpark"` (fallback)
  6. `<address>` present -> `"hand-written"`
  7. None of the above -> `"unknown"`

### Layer 2: Metadata Extraction (5 techniques)

**T3. Metadata dispatch**
- `extract.py:extract_metadata`
- Routes to generator-specific extractor based on `detect_generator` result

**T4. mpark metadata**
- `extract.py:_extract_mpark_metadata`
- Title from `h1.title` in `header#title-block-header`
- Fields from metadata `<table>` rows: label in first `<td>`, value in second
- Label matching: normalized (strip, lower, drop trailing `:`) then substring (`"document"`, `"date"`, `"audience"`, `"reply"`)
- Author parsing (`_parse_mpark_authors`): `<br>` split, email regex pairing with preceding name lines

**T5. Bikeshed metadata**
- `extract.py:_extract_bikeshed_metadata`
- Title + document from `h1.p-name` (doc number regex at start splits from title)
- Date from `time.dt-updated` (`datetime` attribute or text content)
- Audience and authors from first `<dl>` via `dt`/`dd` walk with state tracking

**T6. Hand-written metadata**
- `extract.py:_extract_handwritten_metadata`
- Two DOM patterns: `<address>` block (line-by-line parsing) and `table.header` (`th`/`td` rows)
- `mailto:` links for author email extraction

**T7. Generic metadata fallback**
- `extract.py:_extract_generic_metadata`
- Title from first `<h1>`
- Scans all `<table>` rows for label/value pairs matching document/date/audience keywords

### Layer 3: Boilerplate Stripping (1 technique)

**T8. Generator-aware boilerplate removal**
- `extract.py:strip_boilerplate`
- Universal: removes all `<style>`, `<script>`, `<link>`, `<meta>` tags
- Universal: removes `#TOC`, `#toc`, `nav[data-fill-with="table-of-contents"]`
- Per-generator:
  - mpark: removes `header#title-block-header`
  - Bikeshed: removes all `div[data-fill-with]`, `h1.p-name`, `h2#profile-and-date`
  - Hand-written: removes `<address>`, `table.header`
  - wg21: removes `div.wg21-head`, `div.toc`
  - Unknown: appends problem description to prompts list
- Returns `list[str]` of problems for the prompts file

### Layer 4: DOM Rendering (8 techniques)

**T9. Heading rendering**
- `render.py:_render_heading`
- Maps `<h1>`-`<h6>` to ATX headers (`#` - `######`)
- Strips WG21 chrome: `span.header-section-number`, `span.secno`, `a.self-link`
- Strips leading dotted-decimal section numbers via regex
- Strips wrapping `**...**` bold markers (ATX prefix conveys weight)

**T10. Paragraph rendering**
- `render.py:_render_paragraph`
- Renders `<p>` content via `_inline_text`, then `_collapse_whitespace`
- Whitespace collapse: `strip_format_chars` (Unicode Cf removal) + `\s+` -> single space

**T11. Code block rendering**
- `render.py:_render_pre`, `_detect_code_language`
- `<pre><code>` -> fenced block with language tag
- Language detection: `sourceCode*` class prefix, `language-*` prefix, known language class names
- Default language: `"cpp"` for mpark generator, `""` otherwise
- Code text extracted via `get_text()` (strips all HTML spans/highlighting)

**T12. Table rendering**
- `render.py:_render_table`
- Four rendering paths, dispatched by table complexity:
  1. **Code tables** (`_render_code_table`): cells containing `<pre>` or `<code-block>` elements. Table structure is dropped; each code block is emitted as a fenced block.
  2. **Flat reconstruction** (`_render_table_flat`): nested `<table>` elements or parser-mangled DOM (html.parser nests unclosed `<td>` tags). Descendant-walking collects all cells in document order and reconstructs rows from `<tr>` boundaries.
  3. **Denormalized tables** (`_render_denormalized_table`): cells with `rowspan` or `colspan`. A two-pass algorithm builds a rectangular grid: first pass determines dimensions by summing colspans; second pass fills a `None`-initialized matrix, duplicating cell text into every spanned position.
  4. **Simple pipe tables** (default): direct `<tr>` / `<td>` iteration into rows.
- All four paths emit standard CommonMark pipe tables (`| col | col |`)
- Pipe characters in cells escaped as `\|`
- Short rows padded with empty cells
- Paths 1-3 are lossy (table structure cannot be represented in CommonMark). Each emits a `<!-- tomd:lossy-table -->` HTML comment marker before the table. The QA scorer (`qa.py`) counts these markers as `lossy_table_count` to flag papers for manual review.

**T13. List rendering**
- `render.py:_render_list`
- Dispatched by `<ul>` (marker `-`) or `<ol>` (marker `1.`)
- Only direct `<li>` children processed (no recursive descent into nested lists at this level)
- Nested `<ul>`/`<ol>` within `<li>` rendered recursively with 2-space indent
- Text via `_inline_text` + `_collapse_whitespace`

**T14. Wording section rendering**
- `render.py:_render_wording_div`
- Recognizes div classes: `wording`, `wording-add`, `wording-remove`
- Wraps content in Pandoc fenced divs: `:::wording` / `:::wording-add` / `:::wording-remove`
- `<ins>` and `<del>` elements pass through as HTML in the Markdown output

**T15. Inline formatting**
- `render.py:_inline_text`
- `<code>` -> backticks
- `<strong>`/`<b>` -> `**...**`
- `<em>`/`<i>` -> `*...*`
- `<a href>` -> `[text](url)`, anchor-only links (`#...`) rendered as plain text
- `<br>` -> newline
- `<ins>`/`<del>`/`<sub>`/`<sup>` -> pass through as HTML
- Container tags (span, div, etc.) -> transparent (render children)

**T16. Blockquote and note rendering**
- `render.py:_render_blockquote`, `_render_div` (note/example/advisement classes)
- `<blockquote>` and note-class divs rendered with `> ` prefix per line
- Definition lists (`<dl>`) rendered as `**term**` + `: definition`

### Layer 5: Output (2 techniques)

**T17. Front matter assembly**
- `__init__.py:convert_html`
- Fixed field order: title, document, date, audience, reply-to
- Title double-quoted. Reply-to as YAML list of double-quoted strings.
- Extra keys appended after the standard order.

**T18. Output encoding**
- `__init__.py:convert_html`
- Output is UTF-8 Unicode — non-ASCII characters are emitted directly
- `ascii_escape` in `lib/__init__.py` is kept for external use but is not called in the pipeline

## Module Map

| Module | Responsibility | Public API | Lines |
|--------|---------------|------------|------:|
| `__init__.py` | Pipeline orchestration | `convert_html` | 74 |
| `extract.py` | Parsing, generator detection, metadata, boilerplate | (internal to `convert_html`) | 248 |
| `render.py` | DOM-to-Markdown traversal | (internal to `convert_html`) | 341 |
| **Total** | | **1 public function** | **663** |

## Shared Dependencies

- `lib/__init__.py:ascii_escape` - ASCII-only output encoding (shared with PDF)
- `lib/pdf/cleanup.py:strip_format_chars` - Unicode Cf-category character removal (shared with PDF)
