# Agora study

Generate Reddit-style discussion threads from red-team findings.

## Prerequisites

Red-team data must exist for the target paper:

```bash
.venv/Scripts/python.exe study/red-team/cross_reference.py P2300R10
.venv/Scripts/python.exe study/red-team/analyze.py P2300R10
```

## Usage

```bash
.venv/Scripts/python.exe study/agora/generate.py P2300R10
```

Output: `results/{pid}_thread.md`
