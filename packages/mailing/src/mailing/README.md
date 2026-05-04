# mailing

Scrape the open-std.org WG21 mailing index and download paper sources into a paperstore-backed workspace.

## What's here

- `scrape.py` - `fetch_papers_for_mailing(mailing_id) -> list[dict]` and helpers. Pure HTTP + parsing; no storage dependency.
- `download.py` - `download_paper(paper_id, store, *, source_url) -> Path`. Fetches one source URL and stages it via `store.put_source`. Idempotent on identical bytes (the HTTP call still happens; skip-by-existence is the CLI's job).
- `batch.py` - `stage_mailing(mailing_id, store, *, force=False, papers=None)`. Corpus-level helper used by the CLI. Idempotent; only downloads sources that are not already staged. Test seams (`fetch_papers`, `download`) are exposed as kwargs.

The split is deliberate: scrape is reused outside any storage context (one-off mailing inspections); download is storage-coupled; batch is the idempotent corpus driver.

## CLI

After `uv sync && source .venv/bin/activate` from the workspace root (or prefix with `uv run`). Workspace dir is `$WG21_DATA_DIR` (required); override per command with `--workspace-dir`.

```
# Default: fetch index + download every paper's source. Idempotent;
# re-running is a no-op when there are no new papers.
mailing 2026-04

# Index only, no downloads
mailing 2026-04 --index-only

# Force re-download of every source (use after a tomd-side bytes change)
mailing 2026-04 --force

# Subset (repeatable -p, or comma-separated --papers)
mailing 2026-04 -p P3642R4 -p P3700R0

# Single paper (also idempotent unless --force)
mailing 2026-04/P3642R4

# Explicit workspace override (alternative to $WG21_DATA_DIR)
mailing 2026-04 --workspace-dir ./scratch
```

The mailing index is authoritative for paper title/authors/audience/paper-type and is upserted; existing rows keep their original `added` timestamps. Already-staged sources are detected via `paperstore.StorageBackend.get_source_path` and skipped. Filtering with `--paper`/`--papers` is mailing-only; pair it with `--force` to re-download a specific subset.

The single-paper form (`<mailing-id>/<paper-id>`) does not accept the filter or `--index-only` flags.

For corpus-level scripting, `mailing.batch.stage_mailing(mailing_id, store, *, force=False, papers=None)` is the underlying helper; the CLI is a thin wrapper. It returns a counts dict (`papers_in_index`, `downloaded`, `skipped`, `no_url`, `filtered_out`).

## Tests

```
uv run pytest packages/mailing/tests
```

`requests.get` is monkeypatched in the suite so tests run hermetically. See `tests/test_download.py` for the stub pattern.
