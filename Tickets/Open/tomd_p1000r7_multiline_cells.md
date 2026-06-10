# P1000R7: Multi-line table cells split into separate rows

## Status: Fixed

## Paper
P1000R7 — page 2, "project management options" table (5 columns, 3 logical rows)

## Problem
The table has cells with multi-line text (e.g. header cell "If we choose to control / this" wraps across 2 physical lines). Pass 4 (column-aligned scanner) treats each physical MuPDF text line as a separate row, producing 6 rows instead of 3.

MuPDF's native table finder (`page.find_tables()`) does not detect this table at all, so Pass 5 cannot help.

## Root Cause
Pass 4's row-merge heuristic uses "col-0 has content → new row" as its only signal. In P1000R7, every physical line has content in col 0 (wrapped text), so every line becomes its own row.

## Fix
Added block-sharing continuation detection to Pass 4's row-merge loop in `_detect_column_aligned_tables()`. When two consecutive visual rows share a contributing MuPDF block (lines from the same multi-line cell span multiple y-bands), they are merged into one logical row regardless of col-0 content.

The fix is conservative: it only triggers when MuPDF blocks span multiple y-bands (multi-line cells). Single-line-cell tables are unaffected because their blocks contribute to only one y-band.

## Verification
- Golden tests: 8/8 passed
- Full tomd suite: 1173/1173 passed
- P1000R7 output: 3 logical rows (correct, was 6)
