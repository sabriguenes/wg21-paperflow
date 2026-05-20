# Red team study

Produces a red team report from a WG21 paper using classifier-routed
per-lens LLM analysis.  Depends on the section-chunks study for
chunking and classification.

## Prerequisites

The section-chunks study must have run for the target paper:

```bash
.venv/Scripts/python.exe study/section-chunks/section_chunker.py P2300R10
.venv/Scripts/python.exe study/section-chunks/section_classifier.py P2300R10
```

An API key for the configured LLM service (default: `ANTHROPIC_API_KEY`).

## Usage

```bash
.venv/Scripts/python.exe study/red-team/cross_reference.py P2300R10
.venv/Scripts/python.exe study/red-team/analyze.py P2300R10
.venv/Scripts/python.exe study/red-team/report.py P2300R10
```

## Layout

```
study/red-team/
  README.md
  cross_reference.py    deterministic rules on score matrix
  analyze.py            per-lens LLM calls via AgentBackend
  report.py             assemble findings into markdown
  data/                 per-paper intermediate files
  results/              per-paper red team reports
```
