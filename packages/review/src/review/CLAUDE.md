# review - Agent Rules

## What this is

LLM-driven paper review pipeline for WG21 papers. Takes a paper ID,
pulls markdown from paperstore, runs a multi-step review workflow via
Instructor + Anthropic, returns a rendered markdown report.

## Layout

- `review.md` - prompt document. Loaded whole at runtime, split on H2
  headers by `parse.py`. All LLM-facing text lives here. Editing this
  file changes the prompts without touching Python.
- `parse.py` - **logically isolated, general-purpose** markdown section
  splitter. Not specific to review, papers, or LLM pipelines. Will be
  built up over time. Other modules must not leak domain concepts into
  this file.
- `models.py` - Pydantic models for pipeline state and per-step outputs.
- `pipeline.py` - async `review_paper()` entry point.
- `errors.py` - `ReviewError` exception.

## Invariants

- **No hardcoded prompt strings.** All LLM-facing text comes from
  `review.md` at runtime. Python code contains only structural strings
  (section key names, field names, model slot keys, log messages, error
  messages).
- **`parse.py` is domain-free.** It accepts a `str | Path` and returns
  `dict[str, str]`. No imports from this package or paperstore. No
  knowledge of steps, prompts, or pipelines.
- **Fully batch.** No interactive steps. No user identity. No posture.
  The pipeline has no notion of who is running it.
- **Paperstore is the only storage interface.** Never construct paths
  or write files directly.
- **Three-way sync.** `review.md`, `models.py`, and `pipeline.py` form
  a contract. When any one changes, the other two must be updated in
  the same commit. Field names in step prose (`review.md`) must match
  Pydantic model field names (`models.py`). Step section keys in
  `review.md` must match `_STEP_KEYS` in `pipeline.py`. Per-step
  output models in `models.py` must match the Writes metadata in
  `review.md`. A change to any of these without updating the others
  is a bug.
