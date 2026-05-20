# pipeline

Shared framework for the LLM analytical pipelines (`advocatus`, `agora`). Both depend on `pipeline`. Project-wide rules and the determinism rule numbering live in the root `CLAUDE.md`. Sampling and concurrency rationale lives in `MODELS.md`.

## Modules

- `model_backends.py` - `ModelBackend` ABC and concrete backends (`VllmThinkingBackend`, `Llama3Backend`, `Qwen3Backend`, `AnthropicBackend`), `BACKEND_REGISTRY`. One class per model family; encapsulates structured output strategy, BPE cleanup, thinking-block stripping, tool-calling workarounds.
- `classifier_backends.py` - `ClassifierBackend` ABC and concrete backends (`ZeroShotV2Backend`, `NliCrossEncoderBackend`), `CLASSIFIER_BACKEND_REGISTRY`. Local zero-shot text classifiers wrapping HF Transformers / sentence_transformers. Parallel namespace to `model_backends.py`; no interaction.
- `agents.py` - `AgentBackend`: wraps a `ModelBackend` with pipeline-level config (`thinking_budget`) and the slot/service identity (`slot_name`, `service_name`, `backend_class_name`) used by capability-mismatch error messages. The call-time `tools_capable` check remains as defense-in-depth for tools passed via `run_task` outside `meta.tools`.
- `services.py` - `load_services()` / `resolve_slots()` for LLM `[services.NAME]` slots, and `load_classifiers()` / `resolve_classifier_slots()` for local `[classifiers.NAME]` slots. Both parse SERVICES.toml; the two namespaces are independent. `resolve_slots` returns `dict[str, tuple[str, ModelBackend]]` so callers can thread the resolved service name into each `AgentBackend`.
- `errors.py` - exception hierarchy rooted at `PipelineError`. Includes `CapabilityMismatchError` for pipeline-construction-time slot/capability mismatches.
- `prompt.py` - `StepHooks`, `StepMeta`, `StepSpec`, `build_pipeline`, `parse_step_meta`. Owns prompt-to-hook conformance only; capability validation lives in `validate.py`. Step-meta field-name convention: single-word fields are TitleCase (`**Model:**`, `**Execution:**`, `**Tools:**`, `**Condition:**`); new multi-word fields are kebab-case (`**max-output:**`). Pre-existing `**System prompt:**` (TitleCase + space) is grandfathered. Lookup is case-insensitive (the parser lowercases keys). `_META_RE` allows `[\w \-]+` so hyphens in field names parse correctly; adding a new punctuation character requires updating that regex.
- `validate.py` - `validate_capabilities(specs, *, stop_after=None)`. Primary gate for capability mismatches; called by each pipeline's entry function right after `build_pipeline`.
- `runner.py` - `dispatch`, `load_sections`, `run_agent`, `StepContext`, `write_debug_file`.
- `tasks.py` - `run_task`, `render_debug_md`, `_task_semaphore`.
- `markdown.py` - `sections` (H2 splitter), `sanitize_md`.
- `session.py` - `WebResearcher`, `SearchResult`, `SearchResponse`, `FetchResponse`, `SearchBackend` ABC.
- `backends/` - `BraveBackend` (Brave Search API), `get_default_backend`.

## Public surface

Everything downstream needs is re-exported from `pipeline.__init__`:

```python
from pipeline import (
    AgentBackend, ModelBackend,
    ClassifierBackend, ZeroShotV2Backend, NliCrossEncoderBackend,
    CLASSIFIER_BACKEND_REGISTRY,
    load_services, resolve_slots, ServiceRegistry,
    load_classifiers, resolve_classifier_slots,
    PipelineError, StepError, HookMismatchError, MissingMetadataError,
    CapabilityMismatchError,
    PaperNotFoundError, PaperNotConvertedError,
    StepHooks, StepMeta, StepSpec, build_pipeline,
    validate_capabilities,
    dispatch, load_sections, run_agent, run_task, StepContext,
    sections, sanitize_md,
    WebResearcher, SearchBackend, SearchResult, SearchResponse, FetchResponse,
    write_debug_file,
)
```

## ModelBackend contract

Each backend implements `async def run(system_prompt, user_message, output_type, *, max_tokens, tools, thinking_budget, label, debug_log, request_limit) -> T`. The contract:

- Return a validated instance of `output_type` (a Pydantic `BaseModel`).
- Apply all model-family-specific workarounds internally (BPE cleanup, `<think>` stripping, schema-in-prompt, JSON extraction, retry).
- Raise on unrecoverable failure after exhausting internal retries.
- Append debug entries to `debug_log` when provided.
- Honor `request_limit` as a cap on total model requests issued in this call (model calls + tool calls + output-validator retries). Backends without an agentic loop bound their internal retry budget by this value; pydantic-ai-backed backends pass it through as `UsageLimits.request_limit`. Reject `request_limit < 1` at entry. Default value is `DEFAULT_REQUEST_LIMIT` from `model_backends.py`.

### Adding a backend

1. Subclass `ModelBackend` in `model_backends.py`.
2. Set `thinking_capable` and `tools_capable` class attributes.
3. Implement `async def run(...)`.
4. Register in `BACKEND_REGISTRY` at the bottom of the file.
5. Add the registry key to `SERVICES.toml` documentation.

## ClassifierBackend contract

Each `ClassifierBackend` subclass wraps one local zero-shot classification framework (HF Transformers `zero-shot-classification` pipeline, `sentence_transformers` CrossEncoder NLI, future ClaimBuster, future custom fine-tunes) under one common API:

```python
classify(
    texts: list[str],
    candidate_labels: list[str],
    *,
    multi_label: bool = True,
) -> list[dict[str, float]]
```

Per text: returns `{label: score}` for every candidate label. With `multi_label=True` (the default), each label is scored independently via per-label binary entailment-vs-contradiction softmax; scores do NOT sum to 1, each is a per-label probability suitable for an absolute threshold. This is the only correct mode for non-mutually-exclusive labels (e.g. TARGET and SKIP labels that can both be weakly true).

Determinism contract: offline-first weight loading, per-instance pipeline singleton, CPU only by default, `eval()` mode (HF pipeline applies on construction). `HF_HUB_OFFLINE` defaults to off so first-run downloads succeed; offline-first is achieved by trying `local_files_only=True` first inside each backend's `_load()`.

### Adding a classifier backend

1. Subclass `ClassifierBackend` in `classifier_backends.py`. Accept `model: str` and `device: str` as canonical kwargs; forward any extra per-entry fields via `**kwargs`.
2. Implement `classify(...)` matching the signature above.
3. Register in `CLASSIFIER_BACKEND_REGISTRY` at the bottom of the file.
4. Add a `[classifiers.NAME]` entry to `SERVICES.toml` referencing the registry key.
5. Document the registry key in the `SERVICES.toml` header comment.

## Determinism rules anchored here

- D2. Every backend class applies sampling pins (temperature=0, seed=0, top_k=1) internally. Adding a backend that omits these pins is forbidden.
- D3. Parallel fan-out goes through one of the two semaphores: `pipeline.runner._parallel_semaphore` for parallel-step dispatch in `dispatch()`, `pipeline.tasks._task_semaphore` for sub-agent dispatch in `run_task`. Both default to `asyncio.Semaphore(1)`. The `advocatus` pipeline depends on this serial default for verdict reproducibility (D11 in the root `CLAUDE.md`). The framework permits future packages to widen these by switching from a module-global semaphore to a per-dispatch / per-`run_task` parameter; any such widening must leave `advocatus` at concurrency 1. A bare bump of the module-global constants is forbidden because it silently breaks that pipeline.

## Invariants

- `ClassifierBackend` and `ModelBackend` are parallel namespaces. `[services.NAME]` / `[classifiers.NAME]` and `[defaults]` / `[classifier_defaults]` do not mix; slot resolution is independent. Override flags map to different `StepContext` dicts: `--service` populates `ctx.agents` (via `AgentBackend(slots[slot_name][1], slot_name=slot_name, service_name=slots[slot_name][0])`), `--classifier` populates `ctx.classifiers`.
- Capability validation runs once at pipeline-construction time. `validate_capabilities()` rejects any step whose declared `meta.tools` or assigned `thinking_budget` would land on a backend whose class attributes do not support it. The runtime `NotImplementedError` in `AgentBackend.run` is secondary defense, retained for custom hooks that pass ad-hoc tools via `run_task` outside `meta.tools`.
- `dispatch()` and `validate_capabilities()` must use identical `stop_after` scoping logic. Today both filter by `enumerate` index against the step list; if you switch one site to `spec.meta.number`, switch both in the same commit.
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
- Fail loud. `resolve_slots` raises `ValueError` when a bound service's declared `api_key_env` env var is missing, empty, or whitespace-only. The check fires at slot-binding time, not at config load, so unbound entries in `SERVICES.toml` stay inert.
- Backend env-var contracts are declared on the class. A `ModelBackend` subclass that reads its env var directly from the environment (rather than receiving it as a kwarg) sets `required_api_key_env: ClassVar[str]`. `load_services` rejects `[services.NAME]` entries whose `api_key_env` does not match this value, so the loader and the SDK cannot drift apart on which variable the user must export.
- No global state. The researcher is an explicit object. Create it, pass it around, close it.
- Errors are typed. All pipeline errors inherit `PipelineError`. Downstream packages re-raise domain errors that also inherit `PipelineError`.
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
