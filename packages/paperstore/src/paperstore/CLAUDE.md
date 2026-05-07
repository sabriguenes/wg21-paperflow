# paperstore - Agent Rules

## What this is

The storage abstraction for paperflow. Every other package depends on paperstore; paperstore depends only on the standard library. Adding cross-package imports here is a layering violation.

## On-disk layout (SqliteBackend)

Database at root, files in `paperstore/` subdirectory, lowercase paper id stem:

```text
WG21_DATA_DIR/
  paperstore.db
  paperstore/
    <pid>.pdf | <pid>.html         # mailing.download
    <pid>.md                       # tomd
    <pid>.prompts.json             # tomd, only on uncertain regions; JSON array of LLM reconcile prompts
```

## Module layout

- `backend.py` — `StorageBackend` ABC, `PaperRow` TypedDict, `parse_authors_raw`.
- `sqlite_backend.py` — `SqliteBackend` (production implementation).
- `tools.py` — `PaperstoreTools`: agent-facing read-only methods (`paper_meta`, `paper_meta_latest`, `read_file`) returning JSON strings for Pydantic AI `tool_plain` registration.
- `factory.py` — `from_uri`, `default_workspace_dir`.
- `errors.py` — typed exceptions.
- `progress.py` — `ProgressCallback`, `ProgressEvent`.
- `testing.py` — pytest fixture.

## Database columns

The `papers` table includes `line_count INTEGER DEFAULT 0`, written by `write_paper_md` at conversion time. This allows agents to know file size without reading the file. Backfilled by `reconcile()` for pre-existing rows.

## Invariants

- **`SqliteBackend` is the local backend.** A Postgres backend exists in `wg21-website` (private). New methods must be added to the `StorageBackend` ABC first; do not let `SqliteBackend`-specific behavior leak into call sites.
- **No path arithmetic outside the backend.** Callers must not build paths from `backend.workspace_dir / pid / "..."`. Use accessors: `get_source_path`, `get_paper_md`, `list_paper_ids`. Display sites use return values from `convert_paper` / `write_paper_md`.
- **`get_source_path -> Path` assumes a local filesystem.** Non-local backends must materialize bytes to a temp file before returning. Document this in any new backend.
- **Errors are typed.** Raise `MissingSourceError` / `MissingPaperMdError` / `MissingMailingIndexError` (all subclasses of `PaperstoreError`), not generic `FileNotFoundError`, so callers can distinguish stages.
- **Paper id casing is normalized.** Filesystem stems are lowercase; `list_paper_ids` returns uppercase. APIs accept any input casing.
- **`paperstore/` is the file subdirectory.** All artifact files live under `paperstore/`; the database (`paperstore.db`) lives at the workspace root alongside the `paperstore/` directory.
- **`tools.py` is stdlib-only.** It imports only from `paperstore.backend` and `paperstore.errors`. No LLM, no network I/O.

## When to bypass

Don't. Every CLI in the workspace uses `SqliteBackend` (or `from_uri`); writing files directly would defeat the abstraction.
