# review - Agent Rules

## What this is

LLM-driven paper extraction pipeline for WG21 papers. Takes a paper ID,
pulls markdown from paperstore, runs a multi-step extractor workflow via
Pydantic AI + Anthropic, returns two bulleted question lists identifying
unsupported claims.

## Layout

- `extractor.md` - prompt document. Loaded whole at runtime, split on H2
  headers by `parse.py`. All LLM-facing text lives here. Editing this
  file changes the prompts without touching Python.
- `parse.py` - **logically isolated, general-purpose** markdown section
  splitter. Not specific to review, papers, or LLM pipelines.
- `harness.py` - pure Python code harness: SourceLoc computation (bisect
  on precomputed newline offsets), paper chunking, deterministic dedup
  (tiers 0-1). No LLM, no paperstore imports, no network I/O.
- `models.py` - Pydantic models for domain types, pre-loc types, per-step
  outputs, and pipeline state.
- `pipeline.py` - async `review_paper()` entry point with step-function
  array and dispatch loop (rich progress).
- `errors.py` - `ReviewError` exception.

## Invariants

- **No hardcoded prompt strings.** All LLM-facing text comes from
  `extractor.md` at runtime. Python code contains only structural strings
  (section key names, field names, model slot keys, log messages, error
  messages).
- **`parse.py` is domain-free.** It accepts a `str | Path` and returns
  `dict[str, str]`. No imports from this package or paperstore. No
  knowledge of steps, prompts, or pipelines.
- **`harness.py` is pure Python.** No LLM calls, no paperstore imports,
  no network I/O. Deterministic functions only.
- **Frozen domain models + model_copy.** Domain models (`Claim`,
  `Evidence`, etc.) are `frozen=True`. Steps that need to update a field
  (e.g., `merged_into`) use `model_copy(update=...)` and replace the list
  wholesale on `PipelineState`.
- **Fully batch.** No interactive steps. No user identity.
- **Paperstore is the only storage interface.** Never construct paths
  or write files directly.
- **Three-way sync.** `extractor.md`, `models.py`, and `pipeline.py`
  form a contract. When any one changes, the other two must be updated
  in the same commit. Step section keys in `extractor.md` must match
  `_STEPS` in `pipeline.py`. Domain model fields must match the Classes
  section of `extractor.md`.
