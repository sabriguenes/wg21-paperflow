# dissect

LLM-driven paper dissection pipeline. Project-wide rules live in the root `CLAUDE.md`; framework rules in `packages/pipeline/src/pipeline/CLAUDE.md`; sampling rationale in `MODELS.md`.

## Authority

`dissect.md` is the upstream authority for pipeline structure. It defines the step sequence, step metadata (model slot, execution mode, tools, conditions), and all LLM-facing instructions. Python conforms to it. `build_pipeline()` validates the conformance at startup.

When something drifts, edit `dissect.md` and fix Python to match. Three things stay in sync: `dissect.md` (metadata + instructions), `models.py` (schema), `pipeline.py` (hooks). When one changes, check the other two.

## Layout

- `dissect.md` - prompt document and pipeline authority. Edit this to change prompts or pipeline config without touching Python.
- `prompt.py` - parses step metadata from `dissect.md` sections, validates against registered hooks (bidirectional completeness), builds the ordered `StepSpec` list. Raises `PromptFileError` subtypes on structural mismatches.
- `pipeline.py` - async orchestration: `StepContext`, hook registry (`_HOOKS`), generic runner (`_run_agent`), dispatch loop, and the public `dissect_paper()` / `dissect_since()` entry points. Hooks are small prepare/extract functions (~10-20 lines each); the custom hooks for Steps 8 and 9 are longer because they run multiple sub-LLM calls per step.
- `render.py` - `render_report` (final dissect), `render_trace` (diagnostic trace), `render_debug_md` (LLM transcript).
- `parse.py` - domain-free H2 markdown section splitter. No imports from this package.
- `harness.py` - pure Python: paper chunking, SourceLoc computation, deterministic dedup (tiers 0-1, tier 2 grouping helper), citation extraction. No LLM, no paperstore imports, no network I/O.
- `shadow.py` - owns the shared `BAAI/bge-small-en-v1.5` embedding model singleton plus the observational dedup-shadow merge proposals (centroid-radius clustering via `util.community_detection`). The shadow's clustering output is stored on `PipelineState` and rendered to the trace, never applied. First call downloads ~120MB to the HF cache; `dissect-prefetch-embedding-model` warms it deterministically.
- `triage.py` - pure Python embedding helpers used by Steps 8 and 9. Reuses the `shadow._load_model()` singleton (no second model load). Provides `embed_claims`, `embed_evidence`, `cosine_matrix`, `top_k_per_row`, `above_threshold_pairs`, `centrality_scores`, `tier_split`, `interleave_propositions`. No LLM calls, no network I/O.
- `pdf_extract.py` - PDF binary extractor for `WebResearcher.binary_extractors`. Wraps pymupdf (`fitz`). Registered by `pipeline.py` at construction time.
- `models.py` - Pydantic models for domain types, pre-loc types, per-step outputs, and pipeline state. Sole schema authority. `Field(description=...)` documents non-obvious semantics.
- `errors.py` - error hierarchy. `PromptFileError` (user-fixable), `StepError` (runtime).

## Steps

16 steps (0-15):

```
 0. Read              chunk paper, extract citations          (pure Python)
 1. Extract Claims    normative claims per chunk              (parallel LLM)
 2. Dedup Claims      deterministic tiers 0-1-2 + shadow      (pure Python)
 3. Extract Evidence  supporting evidence per chunk           (parallel LLM)
 4. Dedup Evidence    deterministic tiers 0-1 + shadow        (pure Python)
 5. Extract Factual   factual claims per chunk                (parallel LLM)
 6. Dedup Factual     deterministic tiers 0-1-2               (pure Python)
 7. Extract Rhetoric  rhetoric markers per chunk              (parallel LLM)
 8. Verify            custom: triage + per-batch propositions + per-pair disclaim
 9. Load-Bearing      custom: deterministic classify + per-claim binary
10. Verify Citations  fetch and verify each cited paper       (parallel LLM + web_fetch)
11. Web Search        search for evidence on critical gaps    (parallel LLM + web_search/fetch)
12. Resolve External  integrate external evidence             (single LLM)
13. Caput Causae      identify the load-bearing root cause    (single LLM)
14. Detect Patterns   cross-rhetoric pattern analysis         (single LLM)
15. Report            render final dissect markdown           (pure Python)
```

The generic runner (`_run_agent`) handles Agent construction, model-slot lookup, usage limits, retries, retry-on-empty, tool registration, and debug logging. Tool registration is prompt-driven: the runner reads `meta.tools` and looks up each name in `ctx.tool_registry`.

Hooks split responsibility: `prepare` formats state fields into the user message; `extract` stores the LLM output back into state. The prompt file declares which model and which tools.

## Errors

- `PromptFileError` (and subclasses `MissingMetadataError`, `HookMismatchError`): user-fixable. Edit `dissect.md`. The error message names the step and the expected format.
- `PaperNotFoundError`, `PaperNotConvertedError`: user-fixable. Run the paperflow command in the error message.
- `TransientStepError`: retryable. API timeout, rate limit, network.
- `ValidationStepError`: pipeline bug. LLM output did not match schema.

## Adding a step

1. Add a `## N. Name` section to `dissect.md` with the metadata block (Model, Execution, optionally Tools, Condition).
2. Define a Pydantic output model in `models.py`.
3. Write `_prepare_<name>` and `_extract_<name>` hooks in `pipeline.py`.
4. Register in `_HOOKS` under the exact section header text.
5. Add fields to `PipelineState` if the step writes new state.

## Invariants

- `dissect.md` is the authority. Step metadata in `dissect.md` drives model slot, execution mode, and tool registration.
- No hardcoded prompt strings. All LLM-facing text comes from `dissect.md` at runtime.
- `parse.py` stays domain-free. No imports from this package or paperstore.
- `harness.py` stays pure Python. Deterministic functions only.
- Frozen domain models. Steps that need to update a field use `model_copy(update=...)`.
- Fully batch. No interactive steps. No user identity.
- Serial LLM dispatch. This pipeline depends on `pipeline.runner._parallel_semaphore` and `pipeline.tasks._task_semaphore` both being `asyncio.Semaphore(1)`. Steps that declare `Execution: parallel` in `dissect.md` (1, 3, 5, 7, 10, 11) fan out per chunk or per item, but the semaphore serialises them. Verify (8) and Load-Bearing (9) are custom hooks that internally fan out many small `run_task` calls (one per batch of verify propositions, one per disclaim candidate pair, one per Tier 1 claim) -- those also rely on the `_task_semaphore`. Resolve External (12), Caput Causae (13), and Detect Patterns (14) each take the full deduped state in a single LLM call; their reproducibility depends on the upstream extraction steps having run in deterministic order. See D11 in the root `CLAUDE.md`. A future package that opts into concurrent dispatch must do so without raising those semaphores from this pipeline's perspective.
- Steps 8 and 9 are custom hooks owning their own LLM choreography.
  - Step 8 (Verify) runs five phases: embedding triage, centrality scoring + tier split, batched (claim, evidence) verify propositions over Tier 1 claims only, per-pair disclaim detection over cosine-filtered claim pairs, then aggregation into `state.verdicts`. Tier 2 claims default to `unproven` with no LLM call.
  - Step 9 (Load-Bearing) runs a deterministic classification pass over the verdicts and dependency graph, then one short LLM binary call per Tier 1 claim asking whether the claim is load-bearing. Tier 2 claims auto-classify as `peripheral`. A second pass downgrades `anchored` claims whose dependencies are `conflicted` or `critical_gap` into `depends_on_contested`.
  - The centrality pre-filter is biased *generously* in favour of Tier 1: a small paper sends every claim to the LLM; only when claim count exceeds `_CENTRALITY_TOP_K` (default 30) and `_CENTRALITY_TOP_FRACTION` (default 0.30) of the alive set does the Tier 2 auto-`peripheral` shortcut kick in. The trade-off: false positives in the embedding pre-filter (a non-peripheral claim mislabelled Tier 2 and silently demoted to `peripheral`/`unproven`) are much more harmful than false negatives (a peripheral claim treated as Tier 1 just wastes one LLM call).
- Tunable constants for Steps 8 and 9 live in `pipeline.py` (`_VERIFY_BATCH_CLAIMS`, `_VERIFY_BATCH_EVIDENCE`, `_DISCLAIM_COSINE_THRESHOLD`, `_CENTRALITY_TOP_K`, `_CENTRALITY_TOP_FRACTION`, `_CENTRALITY_EVIDENCE_THRESHOLD`, `_CENTRALITY_PEER_THRESHOLD`). They are intentionally module-level so the trade-off can be inspected and tuned in one place. Raising `_VERIFY_BATCH_CLAIMS * _VERIFY_BATCH_EVIDENCE` re-introduces the monolithic-prompt attention-degradation regression these hooks were built to avoid; raise only after re-evaluating against the synthetic-paper integration test.
- Adding a new step that needs the embedding triage: import `dissect.triage`, reuse `_load_model()` via the wrappers there. Do not re-load the embedding model or re-implement cosine similarity. The model is a hard dependency (`sentence-transformers` in `pyproject.toml`); consistent quality of Steps 8 and 9 depends on having real embeddings rather than a fallback.
- D6 reminder: every step hook declares `output_type=<PydanticModel>`. Free-text → regex parsing of LLM output is forbidden.
- D7 reminder: sort unordered collections (e.g. `Counter.items()`) before they feed a prompt. `_extract_citations` in `harness.py` is the canonical pattern.
- PDF extraction lives in `pdf_extract.py`. Registered into `WebResearcher.binary_extractors` by `pipeline.py` at construction time so the `pipeline` package itself stays free of pymupdf (AGPL). Any future binary extractors (Word docs, etc.) follow the same pattern.
- `fitz.open()` is paired with `doc.close()` in a `finally` block. Never rely on GC: pymupdf holds C-level resources, and orphaned documents accumulate FDs and memory under sustained load.
- `pymupdf` pins in `dissect/pyproject.toml` and `tomd/pyproject.toml` move in lockstep. Mismatched pins between sibling editable installs in the same venv let `uv` resolve one version while partial rebuilds drift. `tests/test_pin_lockstep.py` enforces this mechanically.

## Fidelity invariant

If full fidelity cannot be achieved, stop. Set the paper status to failed with a clear error message. Preserve the debug transcript for diagnosis. Never produce a partial result that could be mistaken for a complete one.

Every citation must be verified or honestly reported as not_found or unreadable. If the LLM is unreachable or a critical step produces invalid output, the paper must fail.

## Prompt injection defense

Use the shared pipeline source delimiter invariant. Source text entering LLM prompts must pass through `pipeline.tools.wrap_source`; the framework floor tells agents to treat delimited content as data, not instructions.
