# reviewstore - Agent Rules

## What this is

SQLite-backed cache of review pipeline analysis results. Shared by review, agora21, cpp-herald. Lives alongside paperstore.db in WG21_DATA_DIR.

## Layout

- `store.py` — `ReviewStore` class: open/close, write (delete+insert), read.
- `models.py` — Frozen dataclasses for row types. Stdlib only.

## Invariants

- **Stdlib only.** No Pydantic, no cross-package imports upward.
- **One process at a time.** No WAL, no busy timeout, no concurrent access.
- **Write methods accept domain objects** from the review pipeline (Claim, Evidence, CitationRef, ExternalEvidence) and convert internally.
- **Read methods return dataclass rows** defined in models.py.
- **`PRAGMA user_version`** tracks schema version. Refuse to open if DB version is higher than code version.
- **Delete+insert is atomic** via `with self._conn:` transaction blocks.
