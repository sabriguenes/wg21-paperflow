# metadata_yaml Rules

This module handles extraction and formatting of YAML front matter for
WG21 paper markdown output. The canonical field order and structure are
defined in the root `CLAUDE.md`.

## Field Order (strict contract)

1. `title`
2. `document`
3. `date`
4. `intent`
5. `audience`
6. `reply-to`

`revision` is excluded. It was removed because the revision number is
derivable from the document ID (PxxxxRy -> y) and does not need to be
stored as a separate field.

## Rules

- Missing keys are skipped (no placeholders, no blank lines).
- Unknown keys appear after `audience` so `reply-to` is always last.
- `title` is always double-quoted.
- `reply-to` is a YAML list of `"Name <email>"` strings.
- All author-like metadata (Reply-to, Authors, Editors, Co-Authors) is
  merged into the single `reply-to` field.

## Processing Order

1. **Extract**: metadata fields from page-0 blocks (wg21 label scan,
   section-line regex scan, email safety-net fallback).
2. **Strip**: remove page-0 headings that duplicate extracted metadata.
3. **Format**: serialize metadata dict into YAML front matter string.

Metadata extraction completes fully before body structuring begins.
