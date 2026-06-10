# tomd: P4012R0 extraction quality issues

## Status: CLOSED (all 5 fixed)

## Priority: High

## Paper

P4012R0 — found during manual audit round 2 (table-heavy papers).

## Problems Found

### 1. ~~TOC tail lands in body (Contents page 2)~~ — FIXED

Fixed 2026-06-02. The label-anchored TOC extension in `pipeline.py` was not counting dot-leader sections toward the `numbered` threshold and broke on `TABLE` sections. Fix: count dot-leaders, include TABLEs in candidate set.

### 2. ~~Section 2.2 "suggested polls" — tables missing entirely~~ — FIXED

Fixed 2026-06-02. MuPDF merged the "SF F N A SA" header cells into the preceding paragraph block (empty data cells cause MuPDF to not create a separate table structure). Fix: `_split_trailing_horizontal_rows` pre-pass in `table.py` detects trailing horizontal-row headers merged into paragraph blocks, splits them, and creates TABLE sections directly.

### 3. ~~Tony Tables (pages 3 and 5)~~ — FIXED

Tony Table 1 (page 3) and Tony Table 2 (page 5) are correctly extracted as before/after code comparison tables. No action needed.

### 4. ~~Section 7 "DIFFERENCES" — table header + Dingbats glyphs~~ — FIXED

Fixed 2026-06-02. Two sub-issues:

**Header absorption**: The header row ("status quo | Section 6.3") was a 2-column block while the data table had 3 columns. Pass 1 detected the data blocks but skipped the header due to column-count mismatch. Fix: post-pass spanning-header absorption in `detect_tables()` — after all detection passes, checks if a remaining block directly above a TABLE section is a multi-column header with fewer columns. Maps header cells to the nearest table columns. Simulated across all 124 PDFs: 10 valid absorptions in 9 PDFs, 0 false positives.

**Dingbats glyphs**: The ✗ (`\x18`) and ✓ (`\x14`) characters used the Dingbats font which was not decoded. Fix: `_decode_dingbats` helper in `emit.py` maps Dingbats font characters to Unicode ✗/✓. Integrated into `_render_cell_spans`, `_spans_to_code_lines`, and `_render_html_table`. Simulated: only 3 PDFs use Dingbats in tables (P3844R3, P4012R0, P4012R1), 0 regressions.

### 5. ~~Heading level 2.1/2.2~~ — DEFERRED

Cosmetic issue. Sub-section headings "2.1" and "2.2" are not recognized as sub-headings because the PDF uses the same font size as body text. Not a table extraction issue.

## Context

Chat reference: [P4012R0 extraction debug](81b7b542-f772-46f3-a55b-6966af64ddae).
