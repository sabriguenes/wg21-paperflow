# pipeline - Agent Rules

## What this is

Shared framework for LLM review pipelines (dissect, advocatus, agora).
Contains step execution, agent runners, web tools, markdown utilities,
and error types. All three pipeline packages depend on `pipeline`.

## Modules

- `errors.py` - exception hierarchy rooted at `PipelineError`
- `prompt.py` - `StepHooks`, `StepMeta`, `StepSpec`, `build_pipeline`,
  `parse_step_meta`
- `runner.py` - `dispatch`, `load_sections`, `run_agent`, `StepContext`,
  `write_debug_file`, `DEFAULT_MODEL_SLOTS`
- `tasks.py` - `run_task`, `render_debug_md`
- `markdown.py` - `sections` (H2 splitter), `sanitize_md`
- `session.py` - `WebResearcher`, `SearchResult`, `SearchResponse`,
  `FetchResponse`, `SearchBackend` ABC
- `backends/` - `BraveBackend` (Brave Search API), `get_default_backend`

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

## Invariants

- **Status codes on everything.** `search()` returns `SearchResponse`
  with `status_code`. `fetch()` returns `FetchResponse` with
  `status_code`. No bare strings or lists.
- **Backends are self-contained.** Each search backend owns its own HTTP
  client. No shared client coupling between session and backend.
- **Backends are long-lived.** `BraveBackend` holds a persistent
  connection pool and rate limiter. Create once, share across
  `WebResearcher` instances for parallel runs.
- **Researcher borrows or owns.** Pass a backend to share it. Omit
  to auto-create one. `_owns_backend` tracks who closes it.
- **Fail loud.** Missing `BRAVE_API_KEY` raises `ValueError` at
  construction time, not at first search call.
- **No global state.** The researcher is an explicit object. Create
  it, pass it around, close it.
- **Errors are typed.** All pipeline errors inherit `PipelineError`.
  Downstream packages re-raise domain errors (e.g. `DissectError`)
  that also inherit `PipelineError`.

## How to add a search backend

1. Create `backends/<name>.py`
2. Subclass `SearchBackend`
3. Implement `async def search(self, query, max_results) -> SearchResponse`
4. Declare `name` class attribute
5. Add `close()` if the backend owns resources
6. Register in `backends/__init__.py`
