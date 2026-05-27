# paperflow

Paperflow fetches WG21 C++ standards papers from open-std.org, converts them to markdown, and stores the results in a local SQLite-backed workspace. It is a uv-managed monorepo of four packages that share a common storage backend.

## Commands

```bash
# Full pipeline for a year (scrape + download + convert)
paperflow 2026

# Individual stages
paperflow mailing 2026          # scrape mailing indexes (no downloads)
paperflow mailing all           # scrape all years >= 2011
paperflow download 2026         # fetch source files (PDF/HTML)
paperflow download P3642R4      # fetch a specific paper
paperflow convert 2026          # convert staged sources to markdown
paperflow full 2026             # all three stages
paperflow full all              # everything not yet done

# Idempotency: re-running any command skips already-complete work
paperflow download all          # downloads only what's not yet staged
paperflow convert all           # converts only what's not yet converted
```

### Flags

| Flag | Commands | Description |
|------|----------|-------------|
| `--force` / `-f` | mailing, download, convert, full | Redo stage even if already complete |
| `--verify` | download, full | HEAD-check staged files against Content-Length |
| `--concurrency N` | download, convert, full | Parallel workers (defaults vary) |
| `--extract-vector-images` | convert, full | Opt in to vector-figure extraction (heuristic; see Images below) |
| `--vector-whiteout-text` | convert, full | When extracting vector figures, paint over text inside each cluster |
| `--workspace-dir DIR` | all | Backend root (default: `$WG21_DATA_DIR`) |

All commands and flags are shown by running `paperflow` with no arguments.

## Install

```bash
uv sync && source .venv/bin/activate
```

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Outside an activated venv, prefix commands with `uv run`.

Set `WG21_DATA_DIR` to point to your workspace directory.

## Tests

```bash
uv run pytest
```

## Packages

- **paperstore** - SQLite storage backend (`SqliteBackend`). All metadata in `paperstore.db`; source files and markdown in the `paperstore/` subdirectory.
- **mailing** - Scrapes the open-std.org mailing index and downloads paper sources.
- **tomd** - Converts paper PDFs and HTML to clean markdown.
- **cli** - Ingestion and conversion CLI (`paperflow`).

## Images

PDFs and HTML papers with embedded raster images get those images extracted to `paperstore/<pid>-fig{page}-{n}.{ext}` and referenced in the converted markdown as `![caption](file)`. PDF captions come from "Figure N: ..."-style labels near the image; HTML captions come from `<figcaption>` or the `alt` attribute. HTML papers also get a `<pid>.html-images.json` sidecar manifest that records the mailing-to-tomd handoff.

**Vector diagrams** drawn with PDF path/line operators (flowcharts, graph diagrams) can be extracted under the opt-in `--extract-vector-images` flag. The extractor clusters spatially adjacent path operators per page, rejects clusters that look like decoration (table borders, running-header rules, ins/del-coloured strokes, regions overlapping text blocks), and rasterises survivors to PNG via `page.get_pixmap`. The output is heuristic by design: each converted paper carries a trailing `<!-- tomd:vector-extraction-uncertain: ... -->` HTML comment disclosing per-paper rejection counts so a reader can see why a diagram might be missed. The opt-in default is deliberate; flip when a fresh corpus re-survey or the layout-aware path (see `packages/tomd/improvements.md` §4) justifies it.

**Out of scope:**

- **Scanned-page PDFs** whose body is one image per page. See `packages/tomd/improvements.md` §4.

Papers with more than 20 unique embedded images keep the first 20 in source order and append a `<!-- tomd:images-truncated: ... -->` HTML comment at end-of-body recording the cap.

## License

Boost Software License 1.0
