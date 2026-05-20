# Models

How the analytical pipelines (`dissect`, `advocatus`, `agora`) configure LLMs for semantic stability across runs.

## Goal

Same paper in, semantically equivalent trace out, run after run. Not bit-exact. Bit-exact reproducibility on hosted endpoints is impossible without server-side batch-invariant kernels. We aim for "no random extra/missing items, no decision boundaries flipping" because each downstream step consumes the previous one's output.

## Two-layer abstraction

Model-specific concerns are isolated in backend classes. Each backend encapsulates the full mechanical contract for one model family on one infrastructure stack.

| Backend class | Registry key | Capabilities | Workarounds |
|---|---|---|---|
| `VllmThinkingBackend` | `vllm_thinking` | thinking, no tools | `<think>` strip, BPE cleanup, schema-in-prompt, JSON extract + retry |
| `Llama3Backend` | `llama3` | tools, no thinking | pydantic-ai Agent with `ModelProfile(supports_json_schema_output=True)`, `llama3_json` parser |
| `Qwen3Backend` | `qwen3` | thinking toggle, tools | `hermes` parser, `enable_thinking` toggle, `unused1`/`unused2` stability fields |
| `AnthropicBackend` | `anthropic` | thinking, tools | pydantic-ai Agent via native API |

`AgentBackend` wraps a `ModelBackend` with pipeline-level config (`thinking_budget`). Validates `tools_capable` at call time.

## Sampling pins

All backends use greedy decoding. Per-backend `ModelSettings` enforce the pins internally.

| Setting | Value | Why |
|---|---|---|
| `temperature` | 0.0 | Greedy decoding. Deterministic argmax per token. |
| `top_p` | 1.0 | Redundant under `temperature=0`; set explicitly for documentation. |
| `top_k` | 1 (via `extra_body`) | Constrains decode space. Redundant under greedy; documents intent. |
| `seed` | 0 | Tie-break determinism where the provider honors it. |
| `parallel_tool_calls` | False | Force one tool call per assistant turn. |
| `max_tokens` | Per backend | Configured in SERVICES.toml. |

## Schema field count and output stability

When an LLM produces structured JSON output via constrained decoding (tool calling / function calling), the number of fields in the output schema affects generation determinism — independent of what those fields contain.

**The finding.** With Qwen 30B on Fireworks (temperature=0, seed=0), we measured claim extraction across 10 identical runs of the same paper:

| Schema fields | Claim counts across 10 runs |
|---|---|
| 3 fields (`text`, `start_line`, `question`) | 20, 21, 22, 28 — high variance |
| 5 fields (same 3 + `alignment1`, `alignment2`) | 20, 20, 20, 20, 20, 20, 20, 20, 20, 19 — stable |
| 5 fields (same 3 + `section`, `kind`) | 19, 19, 19, 19, 19, 19, 19, 19, 19, 20 — stable |

The field names do not matter. `alignment1`/`alignment2` (meaningless) and `section`/`kind` (semantic) produce identical stability. The count is what matters.

**Why it works.** Constrained decoding masks invalid tokens at each generation step. More fields = more structural checkpoints in the JSON = a narrower corridor of valid token sequences. With 3 fields, the model has wide latitude in how it distributes attention between the opening `{` and the closing `}`. With 5 fields, the schema forces it through more gates, reducing the probability mass available for variation.

**What we do about it.** `RawClaim` carries two fields (`unused1`, `unused2`) that exist solely to anchor constrained decoding. They have default values, the model fills them with whatever it wants, and the harness discards the values. The comment in `models.py` documents this. If a future model or provider achieves deterministic output with fewer fields, remove them.

This finding is model-specific and may not apply to other architectures or providers. It was measured on Qwen 30B (qwen3-30b-a3b-instruct) via Fireworks dedicated deployment in May 2026.

## Concurrency pins

| Scope | Symbol | Value | Why |
|---|---|---|---|
| Parallel-step fan-out | `pipeline.runner._parallel_semaphore` | `asyncio.Semaphore(1)` | One in-flight request per parallel step. |
| Sub-task fan-out | `pipeline.tasks._task_semaphore` | `asyncio.Semaphore(1)` | One in-flight request per `run_task` (e.g. per-citation, per-claim). |
| In-turn tool calls | `parallel_tool_calls=False` | n/a | Model emits tool calls one at a time. |

Cost: longer wall clock on dissect Steps 10 (verify citations) and 11 (web search). Benefit: no cross-request batch interference at the inference layer.

Raising either semaphore needs a documented variance budget. Same goes for `parallel_tool_calls=True`.

## Small-model caveats

- **MoE routing.** Qwen3-30B-A3B is Mixture-of-Experts (30B params, ~3B active). Under concurrent load, batch composition affects which experts route. Serializing requests via the semaphores removes this entirely.
- **Eagle3 speculative decoding.** Semantically equivalent to greedy in theory; verify-step has tie-break edges that can flip tokens at decision boundaries. Acceptable for "semantic pretty-darn-close"; not acceptable for bit-exact.
- **Schema discipline.** Smaller models drift more with vague prompts. `output_type=<PydanticModel>` (D6) is non-negotiable.

## Fireworks specifics

- **Unset sampling fields silently take HuggingFace `generation_config.json` defaults.** Always send every field explicitly. The `_BASE_MODEL_SETTINGS` cascade enforces this.
- **No documented `seed` parameter on the public API.** `ModelSettings.seed=0` is a harmless no-op on Fireworks; honored elsewhere (OpenAI).
- **Hosted inference is best-effort reproducible, not bit-exact.** vLLM/SGLang-class servers (which Fireworks resembles) flip tokens because matmul/attention/RMSNorm kernels are not batch-invariant — server load changes batch size, batch size changes floating-point reduction order, occasional argmax flip. Server-side fixes exist (vLLM `VLLM_BATCH_INVARIANT_LEVEL`, SGLang `--enable-deterministic-inference`); Fireworks does not currently expose them.

## Deployment invariants

For Fireworks deployments hosting our pipeline models:

```
--min-replica-count 1 --max-replica-count 1
--enable-session-affinity
--direct-route-type DIRECT_ROUTE_TYPE_LOCAL
```

Verify:

```
firectl deployment get <id> | rg 'replica|affinity|route|draft'
```

Eagle3 (`Draft Token Count > 0`, `Draft Model: ...-eagle3-...`) is acceptable for the semantic-stability bar. Drop it (`--draft-token-count 0`) if you ever need bit-exact behavior, which you won't get anyway without server-side batch-invariant kernels.

## Service configuration

Infrastructure is declared in `SERVICES.toml` at the repo root. Each `[services.NAME]` section maps to a `ModelBackend` instance via the `backend` field. API keys come from environment variables (the `api_key_env` field names the env var). The `[defaults]` section maps slot names to service names. CLI `--service` overrides beat defaults.

## Token-to-character ratios

Measured on four WG21 papers (P4003R3, P4172R1, P2300R10, P2900R14) via `study/token-ratio/`. Each service in `SERVICES.toml` carries a `chars_per_token` field used by `pipeline.tokens.est_tokens()` for context budget math.

| Model | Measured avg | Conservative (in config) | Stdev (256-tok windows) |
|---|---|---|---|
| Claude Opus 4.6 | 3.51 | 3.25 | n/a (aggregate only) |
| Qwen3-32B / 235B | 4.05 | 4.0 | 0.70 |
| DeepSeek-R1-Distill-70B | 4.20 | 4.0 | 0.36 |

Code-heavy windows drop to ~2.5 chars/token; prose-heavy sections reach ~5.5. The conservative values ensure context budgets never overflow. `token_multiplier` (words-to-tokens, used by dissect batching) is a separate field.

## Workaround inventory

| Workaround | Backend | Retire when |
|---|---|---|
| BPE `Ġ`/`Ċ` cleanup | `VllmThinkingBackend` | HF Transformers [#45920](https://github.com/huggingface/transformers/issues/45920) fixed |
| `<think>` block stripping | `VllmThinkingBackend` | vLLM unified parser [#32713](https://github.com/vllm-project/vllm/issues/32713) ships |
| Schema-in-prompt JSON | `VllmThinkingBackend` | vLLM unified parser OR pydantic-ai VLLMProvider [#3515](https://github.com/pydantic/pydantic-ai/issues/3515) |
| Raw JSON + retry | `VllmThinkingBackend` | Same as above |
| `unused1`/`unused2` on RawClaim | `Qwen3Backend` | vLLM [#39677](https://github.com/vllm-project/vllm/issues/39677) fixed |
| `extra_body={"top_k": 1}` | Per-backend | N/A (provider-specific) |
| `parallel_tool_calls=False` | Per-backend | N/A |
| pydantic-ai + vLLM tool loops | `Llama3Backend` docs | pydantic-ai [#1414](https://github.com/pydantic/pydantic-ai/issues/1414) or VLLMProvider lands |

## Structured outputs

Every step that calls an LLM uses pydantic-ai with `output_type=<PydanticModel>`. The schema is injected automatically — no free-text → regex parsing. If you add a step, the model goes in `<package>/models.py`.

Pair every `output_type` with a finite retry budget (`output_retries=N` on `Agent` or `run`). Use `ModelRetry(...)` in `output_validator`s when self-correction is desired; raw `ValidationError` feedback to the model has been lossy in some pydantic-ai versions, so restate the bad arguments explicitly.

## When variance increases

Checklist in order of likelihood:

1. New LLM call site bypassed `MODEL_SETTINGS_BY_SLOT`. Grep for `Agent(`, `ModelSettings(`, `chat.completions.create`.
2. `output_type=str` snuck in instead of a Pydantic model.
3. An unsorted collection (`set`, `dict.keys/values/items`) ends up in a prompt.
4. New tool that hits a non-deterministic external resource (web search, RNG-based retry).
5. `parallel_tool_calls` was unset or overridden somewhere.
6. Deployment lost `min=max=1` or session affinity.
7. `fireworks-sampling-options` response header changed between runs.
8. OpenAI `system_fingerprint` changed between runs.
