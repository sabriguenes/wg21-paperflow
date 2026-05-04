# tomd

Convert WG21 committee papers from PDF or HTML to clean Markdown.

tomd is purpose-built for C++ standards committee paper conversion. It
understands WG21 metadata fields (document number, date, reply-to, audience),
detects structural elements (headings, lists, tables, code blocks, wording
sections), and produces Markdown that looks like a human wrote it, suitable
for version control, pull request diffs, and plain-text review workflows.

## Install

tomd is a member of the paperflow uv workspace; install it from the workspace
root:

```
uv sync                       # installs all four packages + dev deps
source .venv/bin/activate     # puts paperflow on PATH
```

Requires Python 3.12 or newer. Runtime dependencies (`pymupdf~=1.27`,
`beautifulsoup4~=4.14`, `mistune~=3.2`) are declared in `pyproject.toml`
and installed automatically. Outside an activated venv, prefix any command
with `uv run`.

## Usage

tomd is the conversion engine behind `paperflow convert`. Drive it through
the paperflow CLI; sources must be staged in a paperstore workspace first
(run `paperflow download` or `paperflow mailing` as needed). Workspace dir
defaults to `$WG21_DATA_DIR`; override with `--workspace-dir`.

```
paperflow convert P3642R4                       # one paper
paperflow convert P3642R4 P3700R0               # multiple
paperflow convert 2026                          # every paper in year 2026
paperflow convert all                           # everything not yet converted
paperflow convert P3642R4 --force               # re-convert
paperflow convert P3642R4 --no-prompts          # skip the prompts.json intermediate
```

Conversion is idempotent: papers already at `<pid>.md` are skipped unless
`--force` is set.

To use tomd directly from Python (e.g. for tests or custom pipelines),
import `tomd.api.convert_paper`.

### QA mode

Score conversion quality across a batch of papers without inspecting each
output by hand. QA reads the markdown already written by a previous
convert run, so convert first, then score:

```
paperflow convert 2026                                       # convert the corpus
paperflow convert 2026 --qa                                  # ranked report to stdout
paperflow convert 2026 --qa --qa-json report.json            # + detailed per-paper JSON
paperflow convert 2026 --qa --workers 16                     # parallel (16 processes)
paperflow convert 2026 --qa --workers 16 --timeout 180       # abort stragglers after 3m
```

Each paper's markdown is parsed with mistune and scored on heading structure,
code block detection, front-matter completeness, uncertain regions, and
unfenced code. The score is 0-100. Papers that haven't been converted yet
are skipped with a pointer to run convert first. QA is a dev/debug
affordance: it never reconverts, only reads existing `<pid>.md`.

### Output

In a paperstore-backed workspace, tomd produces:

- `<pid>.md` - always produced on success. Contains YAML front matter (title,
  document number, date, audience, reply-to) followed by the paper body
  rendered as Markdown.
- `<pid>.prompts.json` - produced only when the converter found uncertain
  regions. A JSON array; each element is a complete LLM prompt the operator
  can paste into any LLM verbatim, pairing one uncertain span with both
  extraction paths (MuPDF and spatial) plus surrounding context. If no
  uncertain regions exist, no prompts file is written (and any stale one at
  the output path is removed).

### Uncertain regions

tomd uses dual-extraction with confidence scoring. When the MuPDF and
spatial paths disagree on a page, the region is emitted in the output
marked with an HTML comment:

```
<!-- tomd:uncertain:L120-L145 -->
```

The accompanying `<pid>.prompts.json` file contains ready-to-feed LLM
prompts (one per marker, plus one per flagged wording-detection issue).
You resolve uncertain regions manually; the LLM fixes structure, never
content.

## Limitations

- **No OCR.** Scanned or image-only PDFs are not supported.
- **No vision fallback.** Papers that rely on non-extractable layout
  (complex equations, diagrams) will not convert cleanly.
- **HTML generator coverage.** Five generators are detected directly:
  mpark/wg21, Bikeshed, HackMD, wg21 cow-tool, and hand-written. Other
  sources fall back to a generic extractor that may miss metadata fields.
- **LLM auto-resolution is deferred to v2.** The `<pid>.prompts.json` file
  is produced; feeding each prompt to an LLM and applying the result is
  manual in this release.
- **Slide decks and standards drafts are detected and skipped.**
  Presentation-style PDFs (landscape pages smaller than standard paper) and
  long C++ standard documents (>= 200 pages) raise so the caller can report
  the skip rather than producing empty markdown.

## Design

Design and architecture documentation lives alongside the code:

- [`CLAUDE.md`](CLAUDE.md) - architecture rules and invariants (contributors
  and AI agents).
- [`lib/pdf/ARCHITECTURE.md`](lib/pdf/ARCHITECTURE.md) - PDF converter
  pipeline and the techniques it uses.
- [`lib/html/ARCHITECTURE.md`](lib/html/ARCHITECTURE.md) - HTML converter
  pipeline.

Read these in order if you are modifying tomd.

## Development

Run the suite from the workspace root:

```
uv run --package tomd pytest
```

## License

Boost Software License 1.0. See [`LICENSE`](LICENSE).
