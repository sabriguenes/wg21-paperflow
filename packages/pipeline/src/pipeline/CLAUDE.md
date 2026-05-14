# pipeline

Shared framework for the LLM analytical pipelines (`dissect`, `advocatus`, `agora`). All three depend on `pipeline`. Project-wide rules and the determinism rule numbering live in the root `CLAUDE.md`. Sampling and concurrency rationale lives in `MODELS.md`.

## Modules

- `errors.py` - exception hierarchy rooted at `PipelineError`.
- `prompt.py` - `StepHooks`, `StepMeta`, `StepSpec`, `build_pipeline`, `parse_step_meta`.
- `runner.py` - `dispatch`, `load_sections`, `run_agent`, `StepContext`, `write_debug_file`, `DEFAULT_MODEL_SLOTS`, `MODEL_SETTINGS_BY_SLOT`, `_BASE_MODEL_SETTINGS`, `_parallel_semaphore`.
- `tasks.py` - `run_task`, `render_debug_md`, `_task_semaphore`.
- `markdown.py` - `sections` (H2 splitter), `sanitize_md`.
- `session.py` - `WebResearcher`, `SearchResult`, `SearchResponse`, `FetchResponse`, `SearchBackend` ABC.
- `backends/` - `BraveBackend` (Brave Search API), `get_default_backend`.

## Public surface

Everything downstream needs is re-exported from `pipeline.__init__`:

```python
from pipeline import (
    PipelineError, StepError, HookMismatchError, MissingMetadataError,
    PaperNotFoundError, PaperNotConvertedError, PaperNotDissectedError,
    StepHooks, StepMeta, StepSpec, build_pipeline,
    dispatch, load_sections, run_agent, run_task, StepContext,
    sections, sanitize_md,
    WebResearcher, SearchBackend, SearchResult, SearchResponse, FetchResponse,
    write_debug_file, DEFAULT_MODEL_SLOTS,
)
```

## Determinism rules anchored here

- D2. Every `Agent` constructed under `pipeline` uses `MODEL_SETTINGS_BY_SLOT` (or a fresh `ModelSettings(**_BASE_MODEL_SETTINGS, ...)` override) - never hand-builds a `ModelSettings` that omits the pins.
- D3. Parallel fan-out goes through one of the two semaphores: `pipeline.runner._parallel_semaphore` for parallel-step dispatch in `dispatch()`, `pipeline.tasks._task_semaphore` for sub-agent dispatch in `run_task`. Both default to `asyncio.Semaphore(1)`. The `dissect` and `advocatus` pipelines depend on this serial default for verdict reproducibility (D11 in the root `CLAUDE.md`). The framework permits future packages to widen these by switching from a module-global semaphore to a per-dispatch / per-`run_task` parameter; any such widening must leave `dissect` and `advocatus` at concurrency 1. A bare bump of the module-global constants is forbidden because it silently breaks those two pipelines.

## Invariants

- Three-layer system prompts. Every LLM step receives the framework floor, plus the pipeline `## System Prompt`, plus an optional per-step `### System Prompt`. Per-step mode is `append` by default, or `replace` for floor + step only. The floor always applies.
- Source delimiter contract. Source material is wrapped with the configured `WG21_SOURCE_TAG` delimiters. `pipeline.tools.wrap_source` is the only legal way to introduce those markers; it escapes forged delimiter text before wrapping.
- Step failures fail the pipeline. `dispatch()` preserves failures as `StepError`, flushes trace/debug diagnostics, and does not call `on_step_complete` for a failed step.
- Fan-out thresholds are explicit. Custom fan-out steps may tolerate item failures only under a named threshold. Above the threshold they raise `StepError`.
- PaperRow failure persistence. Pipeline failure updates the paper row with `status = -(stage + 1)`, stores `error = str(exc)`, and refreshes `updated_at`.
- Status codes on everything. `search()` returns `SearchResponse` with `status_code`. `fetch()` returns `FetchResponse` with `status_code`. No bare strings or lists.
- Backends are self-contained. Each search backend owns its own HTTP client. No shared client coupling between session and backend.
- Backends are long-lived. `BraveBackend` holds a persistent connection pool and rate limiter. Create once, share across `WebResearcher` instances for parallel runs.
- Researcher borrows or owns. Pass a backend to share it. Omit to auto-create one. `_owns_backend` tracks who closes it.
- Fail loud. Missing `BRAVE_API_KEY` raises `ValueError` at construction time, not at first search call.
- No global state. The researcher is an explicit object. Create it, pass it around, close it.
- Errors are typed. All pipeline errors inherit `PipelineError`. Downstream packages re-raise domain errors (e.g. `DissectError`) that also inherit `PipelineError`.
- `fetch` streams bodies with a hard cap. Bodies are consumed through `client.stream(...)` + `aiter_bytes()`. Any response whose accumulated size exceeds `_MAX_FETCH_BYTES` (25 MB) is aborted mid-read. The cap fires before any extractor runs, so neither trafilatura nor any binary extractor ever sees an oversized body.
- Binary content routes through `binary_extractors`. Passed at `WebResearcher` construction, keyed on the response's `Content-Type` (lowercased, charset stripped). If no extractor matches, the body falls through to the trafilatura HTML path. Detection is Content-Type only - no URL-suffix fallback, no magic-byte sniffing.
- `pipeline` ships no binary extractors. The package keeps a permissive dep set (httpx + trafilatura). Consumers register extractors at the `WebResearcher` construction site so AGPL or other restrictively licensed libraries stay out of `pipeline`.

## Adding a search backend

1. Create `backends/<name>.py`.
2. Subclass `SearchBackend`.
3. Implement `async def search(self, query, max_results) -> SearchResponse`.
4. Declare `name` class attribute.
5. Add `close()` if the backend owns resources.
6. Register in `backends/__init__.py`.
