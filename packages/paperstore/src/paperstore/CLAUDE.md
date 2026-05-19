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
    <pid>-fig{page}-{n}.{ext}      # extracted raster images. page=0 is the
                                   # HTML "no page concept" sentinel.
    <pid>.html-images.json         # typed manifest written by mailing,
                                   # consumed by tomd's HTML path. Schema:
                                   # paperstore.html_manifest.HtmlImagesManifest
    <pid>.dissect.md               # dissect (paperflow dissect)
    <pid>.advocatus.md             # advocatus Relatio (paperflow advocatus)
    <pid>.<tool>.debug.md          # per-tool debug transcript (--debug)
    <pid>.<tool>.trace.md          # per-tool pipeline trace (--trace)
```

Per-tool debug/trace artifacts are namespaced by `<tool>` (e.g. `dissect`, `advocatus`, future `agora`, `herald`) so multiple pipelines coexist without filename collisions. Every consumer routes through `backend.get_debug_md_path(pid, tool)` and `backend.get_trace_md_path(pid, tool)`; no tool reinvents path construction.

## Module layout

- `backend.py` — `StorageBackend` ABC, `PaperRow` frozen dataclass, `parse_authors_raw`.
- `sqlite_backend.py` — `SqliteBackend` (production implementation).
- `tools.py` — `PaperstoreTools`: agent-facing read-only methods (`paper_meta`, `paper_meta_latest`, `read_file`) returning JSON strings for Pydantic AI `tool_plain` registration.
- `factory.py` — `from_uri`, `default_workspace_dir`.
- `extract_rows.py` — frozen dataclasses returned by extract read methods.
- `html_manifest.py` — `HtmlImageEntry`, `HtmlImagesManifest`, `HtmlManifestError`. Stdlib-only typed schema (frozen dataclasses + manual JSON), parallel to `extract_rows.py`. Written by mailing's HTML image fetcher, read by tomd's HTML extractor. `from_json` is forward-compatible up to `_MAX_FORWARD_COMPATIBLE_VERSION`; explicit error past that.
- `errors.py` — typed exceptions.
- `progress.py` — `ProgressCallback`, `ProgressEvent`.
- `testing.py` — pytest fixture.

## Database columns

The `papers` table includes `line_count INTEGER DEFAULT 0`, written by `write_paper_md` at conversion time. This allows agents to know file size without reading the file. Backfilled by `reconcile()` for pre-existing rows.

## Extract tables

Six tables in `paperstore.db` store structured results from the dissect pipeline:

- `claims` -- extracted normative assertions (PK: paper_id + loc triple)
- `evidence` -- supporting facts (PK: paper_id + loc triple)
- `paper_citations` -- WG21 paper numbers cited (PK: paper_id + cited_paper_id)
- `external_citations` -- web search results (autoincrement PK)
- `questions` -- questions for unsupported claims (PK: paper_id + claim_text)
- `rhetorical_markers` -- dismissals, concessions, provocations, scope deflections, political signals (PK: paper_id + loc triple)

Write methods accept duck-typed domain objects (no Pydantic import in paperstore). Read methods return frozen dataclasses from `extract_rows.py`. Atomic delete+insert per paper_id.

## Image and downstream-invalidation accessors

The image-extraction work added six accessors that callers route through; no consumer builds image filenames or manifest paths by hand.

- `get_paper_image_path(pid, page, index, ext) -> Path` and `write_paper_image(pid, page, index, ext, data)` for `<pid>-fig{page}-{index}.{ext}`. `page=0` is the HTML "no page concept" sentinel.
- `iter_paper_image_paths(pid)` and `delete_paper_images(pid)` filter via a compiled stem regex (`_IMAGE_FILENAME_RE`), not glob: so `delete_paper_images("P30")` can never reach `p301-fig...` or unrelated `p30-meta.json` artifacts. The regex is the authoritative gate.
- `get_html_images_manifest_path(pid)` for the mailing-to-tomd handoff sidecar.
- `clear_downstream_outputs(pid) -> ClearedSet` wipes the `.dissect.md` / `.advocatus.md` / `.agora.json` files AND the dissect-pipeline extract rows (claims, evidence, paper_citations, external_citations, questions, rhetoric, caput_causae, citation_audit). Called by `paperflow convert` after a re-convert that changed the markdown, because stored `loc.line` offsets become stale on any content change. `--keep-downstream` opts out and logs a warning.

`try_read_paper_md(pid) -> str | None` is the non-raising read of the current markdown, used for the byte-equality check that gates downstream invalidation.

## Invariants

- **`SqliteBackend` is the local backend.** A Postgres backend exists in `wg21-website` (private). New methods must be added to the `StorageBackend` ABC first; do not let `SqliteBackend`-specific behavior leak into call sites.
- **No path arithmetic outside the backend.** Callers must not build paths from `backend.workspace_dir / pid / "..."`. Use accessors: `get_source_path`, `get_paper_md`, `list_paper_ids`, `get_paper_image_path`, `get_html_images_manifest_path`. Display sites use return values from `convert_paper` / `write_paper_md` / `write_paper_image`.
- **`get_source_path -> Path` assumes a local filesystem.** Non-local backends must materialize bytes to a temp file before returning. Document this in any new backend.
- **Errors are typed.** Raise `MissingSourceError` / `MissingPaperMdError` / `MissingMailingIndexError` (all subclasses of `PaperstoreError`), not generic `FileNotFoundError`, so callers can distinguish stages.
- **Paper id casing is normalized.** Filesystem stems are lowercase; `list_paper_ids` returns uppercase. APIs accept any input casing.
- **`paperstore/` is the file subdirectory.** All artifact files live under `paperstore/`; the database (`paperstore.db`) lives at the workspace root alongside the `paperstore/` directory.
- **`tools.py` is stdlib-only.** It imports only from `paperstore.backend` and `paperstore.errors`. No LLM, no network I/O.
- **PaperRow is a frozen dataclass.** Access fields via attributes (`row.paper_id`), not brackets. Use `vars(row)` for JSON serialization.
- **Extract write methods are duck-typed.** Parameters access attributes like `.loc.line`, `.text`, `.section` without importing domain types.

## When to bypass

Don't. Every CLI in the workspace uses `SqliteBackend` (or `from_uri`); writing files directly would defeat the abstraction.
