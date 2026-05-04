# mailing - Agent Rules

## What this is

Two narrow modules for getting WG21 paper sources off open-std.org and into a paperstore workspace.

## Invariants

- **`scrape.py` has no storage dependency.** It returns plain dicts. Don't import `paperstore` from `scrape.py` - downstream callers add storage.
- **`download.py` returns `(bytes, suffix)` and never writes.** Callers persist via `store.put_source`, which owns the workspace path layout and atomic write. There is no side-channel cache directory; the legacy `.cli_cache/` is gone, do not reintroduce it.
- **`httpx.AsyncClient` is the stubbing seam.** Tests monkeypatch `mailing.download.httpx.AsyncClient`; keep the `import httpx` at module level so the seam stays in one location.
- **`source_url` is required.** `download_paper` returns `None` when missing and refuses unknown suffixes; the URL must come from the authoritative mailing-index row, not be reconstructed locally.
- **`upsert_mailing_index` preserves `added` timestamps** for rows already on disk. The first time we see a paper is information; later refetches must not clobber it.
