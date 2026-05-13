# dissect - Agent Rules

## Philosophy

`dissect.md` is the upstream authority for pipeline structure. It
defines the step sequence, step metadata (model slot, execution mode,
reads, writes, tools, conditions), and all LLM-facing instructions.
Python conforms to it.

The lifecycle: human authors `extractor.orig.md` once, normalizer
produces `dissect.md` once, frontier model generates Python once.
From there, `dissect.md` and Python co-evolve. The system validates
conformance at startup via `build_pipeline()`. When something drifts,
the human opens `dissect.md` and fixes it. It is readable markdown.

Claims and evidence are extracted in a single LLM pass per chunk
(Step 1). The `## Classes` section in `extractor.orig.md` was authored
for the frontier model that generated the initial Python. It is removed
from `dissect.md` because the Pydantic models in `models.py` are the
sole schema authority at runtime (enforced via `output_type`).

## Layout

- `dissect.md` - prompt document and pipeline authority. Step metadata
  (Model, Execution, Reads, Writes, Tools, Condition) is parsed by
  `prompt.py` at startup. LLM-facing instructions are loaded as section
  text. Editing this file changes prompts and pipeline config without
  touching Python.
- `prompt.py` - parses step metadata from `dissect.md` sections,
  validates against registered hooks (bidirectional completeness),
  builds the ordered `StepSpec` list. Raises `PromptFileError` subtypes
  on structural mismatches.
- `pipeline.py` - async orchestration: `StepContext`, hook registry
  (`_HOOKS`), generic runner (`_run_agent`), dispatch loop, and the
  public `dissect_paper()` / `dissect_since()` entry points. Hooks are
  small prepare/extract functions (~10-20 lines each).
- `render.py` - rendering functions: `render_report` (final dissect),
  `render_trace` (diagnostic trace), `render_debug_md` (LLM transcript).
- `parse.py` - **domain-free** H2 markdown section splitter. No imports
  from this package. No knowledge of steps, prompts, or pipelines.
- `harness.py` - **pure Python** code harness: paper chunking,
  SourceLoc computation, deterministic dedup (tiers 0-1), citation
  extraction. No LLM, no paperstore imports, no network I/O.
- `models.py` - Pydantic models for domain types, pre-loc types,
  per-step outputs, and pipeline state. Sole schema authority.
  `Field(description=...)` documents non-obvious semantics.
- `errors.py` - error hierarchy. `PromptFileError` (user-fixable: edit
  `dissect.md`), `StepError` (runtime: transient or validation).

## Pipeline architecture

14 steps (0-13):

```
Step 0   Read              chunk paper, extract citations          (pure Python)
Step 1   Extract Normative normative claims + evidence + markers   (parallel LLM)
Step 2   Dedup Claims      deterministic tiers 0-1 + LLM tier 2    (hybrid)
Step 3   Extract Factual   factual claims per chunk                (parallel LLM)
Step 4   Dedup Factual     deterministic tiers 0-1 + LLM tier 2    (hybrid)
Step 5   Dedup Evidence    deterministic tiers 0-1 + LLM tier 2    (hybrid)
Step 6   Verify            cross-ref claims/evidence, map support  (single LLM)
Step 7   Load-Bearing      graph analysis: which claims matter     (single LLM)
Step 8   Verify Citations  fetch and verify each cited paper       (parallel LLM + web_fetch)
Step 9   Web Search        search for evidence on critical gaps    (parallel LLM + web_search/fetch)
Step 10  Resolve External  integrate external evidence             (single LLM)
Step 11  Caput Causae      identify the load-bearing root cause    (single LLM)
Step 12  Detect Patterns   cross-marker pattern analysis           (single LLM)
Step 13  Report            render final dissect markdown           (pure Python)
```

Each step section in `dissect.md` declares structured metadata:

```
## Step 6 - Verify

- **Model:** default
- **Execution:** main
- **Reads:** claims, evidence
- **Writes:** support_map, internal_contradictions
```

At startup, `prompt.py` parses this into `StepMeta` dataclasses and
combines them with Python hooks from `_HOOKS` in `pipeline.py` to
produce an ordered list of `StepSpec` instances. Steps are sorted by
their numeric index, not by section position in the file.

The generic runner (`_run_agent`) handles Agent construction, model slot
lookup, usage limits, retries, retry-on-empty, tool registration, and
debug logging. Tool registration is prompt-driven: the runner reads
`meta.tools` and looks up each name in `ctx.tool_registry`.

Bespoke hooks provide HOW: `prepare` formats state fields into the
user message, `extract` stores the LLM output back into state. The
prompt file provides WHAT: which model, which fields, which tools.

## Error philosophy

- `PromptFileError` (and subclasses `MissingMetadataError`,
  `HookMismatchError`): user-fixable. Go edit `dissect.md`. The error
  message names the step and the expected format.
- `PaperNotFoundError`, `PaperNotConvertedError`: user-fixable. Run the
  paperflow command in the error message.
- `TransientStepError`: retryable. API timeout, rate limit, network.
- `ValidationStepError`: pipeline bug. LLM output did not match schema.

## Adding a step

1. Add a `## Step N` section to `dissect.md` with the metadata block
   (Model, Execution, Reads, Writes, and optionally Tools, Condition).
2. Define a Pydantic output model in `models.py`.
3. Write `_prepare_<name>` and `_extract_<name>` hooks in `pipeline.py`.
4. Register in `_HOOKS` under the exact section header text.
5. Add fields to `PipelineState` if the step writes new state.

## Invariants

- **`dissect.md` is the authority.** Step metadata in `dissect.md`
  drives model slot, execution mode, reads/writes, and tool
  registration. Python conforms. `build_pipeline()` validates at startup.
- **No hardcoded prompt strings.** All LLM-facing text comes from
  `dissect.md` at runtime.
- **`parse.py` is domain-free.** No imports from this package or
  paperstore.
- **`harness.py` is pure Python.** No LLM calls, no paperstore imports,
  no network I/O. Deterministic functions only.
- **Frozen domain models + model_copy.** Domain models are
  `frozen=True`. Steps that need to update a field use
  `model_copy(update=...)`.
- **Fully batch.** No interactive steps. No user identity.
- **Paperstore is the only storage interface.** Never construct paths
  or write files directly.
- **Three-way sync.** `dissect.md` (metadata + instructions),
  `models.py` (schema), and `pipeline.py` (hooks) form a contract.
  When any one changes, the other two must be checked.
- **No `default=str` in JSON serialization.** If pipeline state is not
  cleanly serializable, that is a bug to fix, not a condition to mask.
- **Library code uses `logging`, never `print()`.** No
  `print(file=sys.stderr)` in any package except `cli`.
