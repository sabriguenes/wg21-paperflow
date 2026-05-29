# assay

Two-pass structural analysis pipeline for WG21 proposals.

## Usage

```bash
paperflow assay P4003R3
paperflow assay P4003R3 --debug --trace
paperflow assay P4003R3 --step 4          # stop after Derive, implies --trace
paperflow assay P4003R3 --force           # re-run even if already complete
```

## C++ Standard Access

Assay requires the cpp-mcp server for normative text lookups,
mechanism verification, and specification analysis grounding. The
server URL is configured in `SERVICES.toml` under `[services.cpp-mcp]`.

Set the API key in your environment:

```bash
export CPP_MCP_API_KEY="<your-api-key>"
```

Assay will hard-error if the MCP server is not reachable or the API
key is missing. This is intentional: running without standard access
degrades finding quality in ways that cannot be detected downstream.

## Architecture

Pass 1 (Steps 0-4) extracts claims, evidence, gaps, and asks per chunk
without a thesis. Step 4 compresses claims into a thesis and identifies
load-bearing claims.

Pass 2 (Steps 5-11) re-scans every chunk with the thesis, cross-chunk
gaps, external research, and companion paper summaries injected.
Produces findings, challenges them against concessions/evidence/scope, detects
compound dynamics, and derives a verdict.

Output: `{pid}.assay.md` in paperstore. Intermediate artifacts (claims,
evidence, gaps, thesis, findings) stored in DB for downstream use by
agora.

## Lenses

Performance, Design, Specification, Usability, Ecosystem, Rationale.

## Verdict scale

Sound > Weakened > Undermined > Insufficient.
