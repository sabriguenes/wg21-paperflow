# wg21-paperflow - Architecture and Pipeline Design

_Internal design reference. Describes how the system is architected and how the pipeline works._

_Last updated May 3, 2026._

---

## 1. Overview

paperflow is the data-acquisition and transformation layer at the front of the WG21 paper pipeline. It scrapes open-std.org mailing metadata, downloads papers, and converts them to markdown. In production, after paperflow populates Postgres, additional apps read from Postgres. None of those apps ingest the mailing directly.

**Single public repo principle:** The repo must be self-contained. A user who clones it and nothing else must be able to replicate the scrape and conversion steps end to end. No file that is required for the result may live elsewhere.

Two modes of operation:

- **Local mode:** SQLite database + files in a workspace directory, no Postgres required. Any user can clone `cppalliance/wg21-paperflow`, run the CLI, and replicate the scrape and conversion steps.
- **Production mode:** Celery task in the Django app (`wg21-website`), Postgres + S3 backend.

---

## 2. Pipeline Ordering

```
open-std.org mailing
        |
        v
 paperflow mailing        (scrape index, idempotent)
        |
        v
 paperflow download       (fetch source files)
        |
        v
 paperflow convert        (tomd conversion, parallel)
        |
        v
     Postgres
        |
   additional apps
```

- Scrape is the serial bottleneck: one HTTP stream to open-std.org.
- After scrape, tomd conversion is embarrassingly parallel (minutes for a full mailing). tomd is lightweight - no ML, no OCR.
- Additional apps read from Postgres; they do not re-scrape the mailing.

---

## 3. Paper Data Model

The `Paper` dataclass in `models.py` is the canonical model. It holds what the system knows about a paper at convert time: title, authors, audience (subgroup), intent, and the path to the staged source file.

**`intent`** is a field on `Paper` with values `"ask"`, `"info"`, or `""` (unknown). It comes from two sources in order:
1. The mailing scraper: `"Info:"` at the start of the title -> `"info"`, `"Ask:"` -> `"ask"`.
2. tomd: if the converted markdown's YAML front matter carries an `intent` field, it patches the record. If it conflicts with the scraper value, tomd wins and a warning is emitted to stderr.

**Metadata authority rule:** The mailing index is the source of truth for identity fields (title, authors, date). What open-std.org publishes is what the website displays. tomd receives the mailing metadata at invocation time and uses it to fill in YAML front-matter fields that are absent from the source file; it never overrides fields the mailing index already provides.

---

## 4. CLI Contracts

Four commands, each independently runnable. Workspace dir is `$WG21_DATA_DIR` (required); pass `--workspace-dir` to override.

```bash
paperflow 2026                    # full pipeline for year (no-verb alias)
paperflow mailing 2026            # scrape mailing indexes only
paperflow mailing all             # scrape all years >= 2011
paperflow download 2026           # fetch source files
paperflow download P3642R4        # fetch a specific paper
paperflow download all            # fetch all not-yet-staged
paperflow convert 2026            # convert to markdown (no LLM)
paperflow convert all             # convert all staged but not converted
paperflow full 2026               # mailing + download + convert
paperflow full all                # everything not yet done
```

`paperflow` with no verb is an alias for `full`. All commands accept year, paper-id list, or `all`. Mixing years and paper-ids in one invocation is a hard error.

Flags: `--force` / `-f`, `--verify` (download/full), `--concurrency N`.

Running `paperflow` with no arguments prints full usage including all flags.

---

## 5. Backend Abstraction

Two concrete backends behind the same `StorageBackend` ABC (`packages/paperstore/src/paperstore/backend.py`):

**SQLite backend** (default - no external dependencies):
- Workspace directory: `$WG21_DATA_DIR`
- Metadata in `paperstore.db` (tables: `papers`, `years`)
- Source files and markdown in the `paperstore/` subdirectory; DB stores paths
- Used for local replication, testing, CI, debugging

**Postgres + S3 backend** (production):
- Structured metadata (paper_id, title, authors, intent, audience) stored in Postgres
- Blobs (PDF source, converted markdown) stored in S3
- `get_source_path` materializes from S3 to a local temp file before returning, per the `StorageBackend` ABC contract
- Implemented in `wg21-website` (private), not in this repo
- Django app calls paperflow functions directly as a Python library

The SQLite backend must work without Postgres installed, and the Postgres backend must never be a dependency of the public repo. Any user must be able to clone the repo, run the scraper, and get results into a local directory without configuring a database.

SQLite is preferred over flat JSON files for the local backend because metadata queries (idempotency checks, work-set selection) are SQL rather than file-glob-and-parse operations. Source files and markdown remain on disk because MuPDF and tomd need local paths.

S3 for blobs was chosen over all-in-Postgres for production because it decouples blob serving from the query path. PDFs and markdown can be served via direct S3 URLs without routing through the application tier. At archive scale (all mailings since 2011, roughly 10,000 papers), the database stays lean while blob storage scales independently.

---

## 6. tomd YAML Front-Matter Spec

Fields tomd emits and their canonical forms:

| Field | Correct form | Wrong form |
|---|---|---|
| `intent` | `intent: ask` or `intent: info` | `paper-type: informational` |
| `intent` position | after `date`, before `audience` | any other position |
| `title` | `title: "A Minimal Coroutine..."` (quoted) | `title: A Minimal Coroutine...` |
| Audience values | Short names, no hyphens: `LEWG`, `SG16` | Long names: `LEWG Library Evolution`, `SG-16` |

Canonical field order: `title`, `document`, `date`, `intent`, `audience`, `reply-to`.

Audience normalization: audience values from the mailing metadata must be normalized to short names without hyphens. "EWG Evolution" -> "EWG", "SG-16" -> "SG16". The exact normalization formula is not yet defined; this is tracked as an open item.

tomd's contract: extract what's in the source file; if a field is absent from the source, leave it absent and let the mailing metadata fill it in.

---

## 7. Repository Layout

uv workspace monorepo. Four packages, each independently installable:

```
cppalliance/wg21-paperflow/   (public - users clone this to replicate)
├── packages/
│   ├── mailing/              # scrape open-std.org, download paper sources
│   ├── tomd/                 # PDF/HTML -> Markdown converter
│   ├── paperstore/           # storage abstraction (SqliteBackend)
│   └── paperlint/            # ingestion + conversion CLI (paperflow)
├── tests/                    # cross-package integration tests
└── DESIGN.md                 # this file
```

`cppalliance/wg21-website` (private Django app) imports wg21-paperflow as a Git submodule. The Postgres + S3 backend lives in `wg21-website`, not here.

---

## 8. Django Integration

How `wg21-website` (private) calls into `cppalliance/wg21-paperflow` (public):

```python
# In wg21-website (private):
from paperlint.orchestrator import convert_one_paper
from mailing.scrape import fetch_papers_for_mailing

@app.task
def process_year(year: str):
    for paper in fetch_papers_for_year(year, ...):
        convert_one_paper(
            paper["paper_id"],
            source_url=paper["url"],
            mailing_meta=paper,
            storage=PostgresBackend(db),
        )
```

wg21-paperflow is installed as a Git submodule. Django imports it as a Python library, not via subprocess.

**Current state:** mailing detection (polling open-std.org for new mailings) lives in the Django app. The goal is to move it into paperflow so the full pipeline is runnable without Django. Not yet done.

---

## 9. Dependencies

```
pymupdf            # PDF text extraction
beautifulsoup4     # HTML parsing (mailing page scraper + HTML conversion)
requests           # HTTP (paper fetching, mailing scraper)
```

---

## 10. Environment

```
WG21_DATA_DIR=/path/to/data   # required, no fallback
```

---

## 11. Known Limitations

- **PDF extraction:** pymupdf quality varies by WG21 PDF toolchain; uncertain regions are flagged in `<pid>.prompts.json` for human or LLM review.

---

## 12. Open Questions

Decisions not yet finalized as of May 3, 2026:

- **Audience normalization formula:** Short names without hyphens are required (`LEWG`, `SG16`), but the exact normalization rules for all known subgroup name variants are not yet codified.
- **GitHub issues per paper:** Where does per-paper issue tracking live? `wg21.link/PXXXX/github` works as a URL pattern; hosting and linking unresolved.
- **Mailing detection in paperflow vs. Django:** The goal is to move mailing detection into the paperflow repo so the full pipeline can run without Django. Currently lives in `wg21-website`. Not yet scheduled.
