# CLAUDE.md

## Spellings

- **cli** (package), **paperflow** (CLI alias, system, repo)
- `paperflow full` is the end-to-end command (mailing + download + convert); `paperflow` bare is an alias for it
- **tomd** (lowercase always), **WG21** (no space)

## Layout

```
packages/
  paperstore/   -> storage abstraction (SqliteBackend)
  mailing/      -> scrape open-std.org + download paper sources
  tomd/         -> PDF/HTML to Markdown
  dissect/      -> LLM-driven paper dissect pipeline (Pydantic AI + web search)
  cli/          -> ingestion + conversion + dissect CLI
tests/          -> cross-package integration test
```

Per-package rules: `packages/<name>/src/<name>/CLAUDE.md`. Consult those when working inside a package.

## CLI commands

```bash
# Index fetching (only command that hits the internet for metadata)
paperflow mailing [YEAR ...]              # fetch mailing indexes from open-std.org
paperflow mailing [YEAR ...] --force      # re-fetch even years already indexed (preserves sources/markdown)

# Per-stage commands - each accepts one or more paper ids OR a mailing id, not both
paperflow download P3642R4 [P2900R15 ...]  # download paper source only
paperflow convert P3642R4 [P2900R15 ...]   # convert downloaded source to paper.md (no LLM)

# End-to-end command - requires paper to exist in local mailing metadata
paperflow full    P3642R4 [P2900R15 ...]   # mailing + download + convert in sequence
paperflow         P3642R4 [P2900R15 ...]   # alias for `full`

# Mailing-scoped variants (replace paper ids with a mailing id)
paperflow download 2026-04
paperflow convert  2026-04
paperflow full     2026-04
paperflow          2026-04

# Dissect - single paper or mailing batch
paperflow dissect P4003R2
paperflow dissect 2026-01

# Idempotent batch - skips papers already at or past the target stage
paperflow download all
paperflow convert  all
paperflow full     all
paperflow          all
```

**Argument rules:**
- Paper ids and mailing ids cannot be mixed in the same invocation.
- Multiple paper ids are accepted by all commands.
- `all` processes every paper not already at the target stage (idempotent).
- `full` / bare `paperflow` require the paper to be present in a local mailing index. Run `paperflow mailing` first.

Each subcommand is implemented in its own module inside `packages/cli/src/cli/`:

| Command | Module |
|---|---|
| `full` (end-to-end, entry-point alias) | `full.py` |
| `mailing` | `mailing.py` |
| `download` | `download.py` |
| `convert` | `convert.py` |
| `dissect` | `dissect.py` |

The argparse entry point is `__main__.py`.

Outside a venv, prefix with `uv run`. Workspace directory is `$WG21_DATA_DIR` (required).

## On-disk layout

```
WG21_DATA_DIR/
  paperstore.db
  paperstore/
    <pid>.pdf | <pid>.html
    <pid>.md
    <pid>.prompts.json             # tomd, uncertain regions only
```

## Canonical front matter (tomd output)

Every converted paper gets this YAML block. Field order is fixed.

```yaml
---
title: "Paper Title"
document: P2583R3
revision: 3
date: 2024-01-15
intent: info
audience: SG1, LEWG
reply-to:
  - "Author Name <email@example.com>"
---
```

- `title`: double-quoted. Extracted from source metadata or first heading.
- `document`: unquoted paper number (e.g. `P4036R0`).
- `revision`: integer from PID (`PxxxxRy` -> `y`). Omit for N-papers.
- `date`: unquoted ISO 8601.
- `intent`: `info` or `ask`. Default `info` for external papers.
- `audience`: unquoted, comma-separated (e.g. `SG1, LEWG`).
- `reply-to`: YAML list of `"Name <email>"` strings. All author-like metadata (Reply-to, Authors, Editors, Co-Authors) is merged into this single field. Field name chosen for consistency with cppalliance/wg21-papers `source/CLAUDE.md` (Mungo/Vinnie decision, April 2026).
- Body headings start at H2. The front-matter `title` renders as H1; no `# H1` in body.

## On-disk layout (dissect output)

```
WG21_DATA_DIR/
  paperstore.db                   # extract tables: claims, evidence,
                                  #   paper_citations, external_citations,
                                  #   questions, rhetorical_markers
  paperstore/
    <pid>.dissect.md              # dissect report
```

## Invariants

- **All storage goes through `paperstore.StorageBackend`.** Never write files directly. Never construct paths from `backend.workspace_dir` or from DB column strings. Use backend accessors.
- **`convert` never re-downloads.** It reads the staged source via the backend.
- **Public and reproducible.** This repository is designed to be inspectable. Anyone can clone it, run `paperflow dissect <pid>`, and replicate the same dissect findings. No external proprietary dependencies. No black boxes. The dissect pipeline, prompt text, and models are all visible in this repo. The dissect package must remain self-contained within wg21-paperflow with no dependencies on cppa-forge or other private repos.
- **Library functions return data. Callers persist.** Never call `write_*` inside a pipeline or conversion function. The CLI module owns persistence.
- **Dissect accuracy over availability.** A wrong redteam analysis destroys credibility in a way that cannot be regained. A crashed pipeline that fails to publish preserves reputation. In the dissect package, prefer crashing on bad data over silently degrading: no `default=str` in JSON serialization, no silent type coercion, no swallowed errors. If the pipeline state is not cleanly serializable, that is a bug to fix, not a condition to mask.
- **Broad catches must be commented.** `except Exception` in batch workers and callback firewalls is acceptable. Uncommented broad catches are treated as bugs during review.
- **Tunable thresholds are named constants.** No bare numeric literals for scoring penalties, timeouts, display limits, or heuristic cutoffs. Module-level constant with a descriptive name.
- **Library code uses `logging`, never `print()`.** No `print(file=sys.stderr)` in any package except `cli`.

## Tests

```bash
uv run pytest                                  # full workspace
uv run --package paperstore pytest             # one package
uv run pytest tests/test_end_to_end_convert.py # integration
```

Stub seams: `httpx.AsyncClient` on `mailing.download` for downloads; `httpx.get` on `mailing.scrape` for scraping.

## `__init__.py`

`__init__.py` files must contain only:
- Re-exports (`from module import Name`)
- `__all__` definitions
- Version strings (`__version__`)

All logic, factories, utilities, and pipeline code must live in named
modules. If you find logic in an `__init__.py`, move it out before
adding to it.

## Style

- No em dashes. Use commas, periods, or colons.
- BSL-1.0 copyright headers on new `.py` files. Attribute to whoever authors the file. Leave existing headers alone.
- When renaming the project or swapping a dependency, grep the entire repo for the old name. Headers, user-agents, env vars, docstrings, CLAUDE.md files. One pass, same commit.
