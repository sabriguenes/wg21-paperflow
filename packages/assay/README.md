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

Assay uses the cpp-mcp server at `https://mcpserver1.cpp.al/mcp` by
default to look up C++ standard sections, verify mechanisms, and
ground specification analysis in normative text.

Set your API key:

```bash
export CPP_MCP_API_KEY="<your-api-key>"
```

To override the server URL (e.g. for local development):

```bash
export CPP_MCP_URL="http://localhost:8001/mcp"
```

Or via CLI flag:

```bash
paperflow assay P4003R3 --cpp-mcp-url http://localhost:8001/mcp
```

To run without the MCP server entirely:

```bash
paperflow assay P4003R3 --no-cpp-mcp
```

When the MCP server is disabled, Specification-lens research falls
back to web search and mechanism existence checks in the Challenge
step rely on the LLM's training data.

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
