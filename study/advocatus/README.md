# Advocatus prototype study

Single-call advocatus synthesis from red-team findings.  Takes the
findings produced by the red-team study, applies the six Defensor
challenges, and produces an advocatus report (seal, objections,
probationes, notae minores) in one LLM call.

## Prerequisites

The red-team study must have run for the target paper:

```bash
.venv/Scripts/python.exe study/red-team/cross_reference.py P2300R10
.venv/Scripts/python.exe study/red-team/analyze.py P2300R10
```

An API key for the configured LLM service (default: `ANTHROPIC_API_KEY`).

## Usage

```bash
.venv/Scripts/python.exe study/advocatus/synthesize.py P2300R10
.venv/Scripts/python.exe study/advocatus/report.py P2300R10
```

## Layout

```
study/advocatus/
  README.md
  synthesize.py     single LLM call: findings -> advocatus output
  report.py         render advocatus JSON to markdown
  data/             per-paper JSON output
  results/          per-paper advocatus reports
```
