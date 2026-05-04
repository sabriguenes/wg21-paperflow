# paperstore

Storage abstraction for paperflow artifacts. Every paperflow tool reads and writes through a `StorageBackend`; `SqliteBackend` is the local implementation.

## Layout

A `SqliteBackend` rooted at `<workspace>` holds:

```
<workspace>/
  paperstore.db                            # SQLite metadata database
  paperstore/
    <pid>.pdf | <pid>.html                 # staged by mailing.download
    <pid>.md                               # written by tomd
    <pid>.prompts.json                     # tomd, only when uncertain regions; JSON array of reconcile prompts
```

## Public surface

```python
from paperstore import (
    StorageBackend, SqliteBackend, from_uri,
    PaperstoreError,
    MissingSourceError, MissingPaperMdError,
    MissingMailingIndexError,
)
```

Backend methods (see `StorageBackend` for the ABC):

- writes: `write_paper_md`, `write_intermediate`, `upsert_mailing_index`, `put_source`
- reads: `get_source_path`, `get_paper_md`, `list_mailing`, `list_paper_ids`

`from_uri(uri, *, workspace_dir=None)` resolves `None`/`file://...` to a `SqliteBackend`. Other schemes (e.g. `postgres://`) are reserved for future backends.

## CLI

After `uv sync && source .venv/bin/activate` from the workspace root (or prefix with `uv run`). Workspace dir is `$WG21_DATA_DIR` (required); override per command with `--workspace-dir`.

```
paperstore show-paper P3642R4
paperstore list-mailings
paperstore --workspace-dir ./scratch list-mailings   # explicit override
```

## Tests

```
uv run pytest packages/paperstore/tests
```

The shared `sqlite_store` pytest fixture (`paperstore.testing`) returns a `SqliteBackend(tmp_path)`; import it for cross-package tests.
