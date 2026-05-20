# assay

Two-pass structural analysis pipeline for WG21 proposals.

## Usage

```bash
paperflow assay P4003R3
paperflow assay P4003R3 --debug --trace
paperflow assay P4003R3 --step 4          # stop after Derive, implies --trace
paperflow assay P4003R3 --force           # re-run even if already complete
paperflow assay P4003R3 --service fast=h200-qwen3-32b
```

## Architecture

Pass 1 (Steps 0-4) extracts claims, evidence, breadcrumbs, and asks per chunk
without a thesis. Step 4 compresses claims into a thesis and identifies
load-bearing claims.

Pass 2 (Steps 5-11) re-scans every chunk with the thesis, cross-chunk
breadcrumbs, external research, and companion paper summaries injected.
Produces findings, challenges them against concessions/evidence/scope, detects
compound dynamics, and derives a verdict.

Output: `{pid}.assay.md` in paperstore. Intermediate artifacts (claims,
evidence, breadcrumbs, thesis, findings) stored in DB for downstream use by
agora.

## Lenses

Performance, Design, Specification, Usability, Ecosystem, Rationale.

## Verdict scale

Sound > Weakened > Undermined > Insufficient.
