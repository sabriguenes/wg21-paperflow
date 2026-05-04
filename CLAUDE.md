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
  cli/          -> ingestion + conversion CLI; `paperflow full` is the end-to-end command, `paperflow` is its alias
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

The Click group entry point is `__main__.py`.

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

## Invariants

- **All storage goes through `paperstore.StorageBackend`.** Never write files directly. Never construct paths from `backend.workspace_dir`.
- **`convert` never re-downloads.** It reads the staged source via the backend.

## Tests

```bash
uv run pytest                                  # full workspace
uv run --package paperstore pytest             # one package
uv run pytest tests/test_end_to_end_convert.py # integration
```

Stub seams: `requests.get` on `mailing.download` for downloads.

## Style

- No em dashes. Use commas, periods, or colons.
- BSL-1.0 copyright headers on new `.py` files. Attribute to whoever authors the file. Leave existing headers alone.
