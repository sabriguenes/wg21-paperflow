# cpp-mcp - Agent Rules

## What this is

MCP server for the C++ standard (ISO/IEC 14882). Parses the LaTeX source from cplusplus/draft, stores sections in SQLite with FTS5, and serves them via FastMCP over HTTP.

## Module layout

- `backend.py` -- `StandardBackend` ABC, `SectionRow` and `DraftInfo` frozen dataclasses.
- `sqlite_backend.py` -- `SqliteStandardBackend` (production implementation, SQLite + FTS5).
- `parser.py` -- LaTeX section parser and macro expander. Extracts `\rSec` hierarchy, expands macros for `cleaned_text`, preserves raw LaTeX verbatim.
- `ingest.py` -- Clone cplusplus/draft at a git tag, parse, and load into the backend.
- `server.py` -- FastMCP server with tools: `lookup_section`, `search_standard`, `get_section_with_children`, `list_chapters`, `list_sections`, `list_drafts`, `diff_section`.
- `__main__.py` -- CLI entry point (`cpp-mcp ingest`, `cpp-mcp serve`).

## Invariants

- **`SqliteStandardBackend` is the default backend.** A Postgres backend is a future addition. New methods go on the `StandardBackend` ABC first.
- **Raw LaTeX is verbatim.** Never expand macros in `raw_latex`. Macro expansion only applies to `cleaned_text`.
- **Multi-version by design.** The `draft_tag` column scopes everything. Operations on one draft never touch another.
- **`itemdecl`/`itemdescr` pairs are atomic.** The parser must not split these across sections.
- **Local-first.** `cpp-mcp serve --no-auth` works with zero config. No Postgres, no Docker, no API keys required locally.
- **Auth is explicit.** The server requires either `--keys-file` (production) or `--no-auth` (local dev). Omitting both is an error.

## Database

Single file: `$CPP_MCP_DATA_DIR/standard.db` (default `~/.cpp-mcp/standard.db`).

Tables: `standard_sections` (content), `drafts` (version metadata), `sections_fts` (FTS5 virtual table).

## CLI

```bash
cpp-mcp ingest --tag n5008       # ingest a draft
cpp-mcp serve --no-auth          # HTTP on localhost:8001 (local dev)
cpp-mcp serve --port 9090        # custom port
cpp-mcp serve --transport stdio  # stdio mode
```
