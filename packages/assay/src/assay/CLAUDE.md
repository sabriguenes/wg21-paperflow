# assay

Two-pass structural analysis pipeline for WG21 proposals. Project-wide rules live in the root `CLAUDE.md`; framework rules in `packages/pipeline/src/pipeline/CLAUDE.md`.

## Authority

`assay.md` is the upstream authority for pipeline structure. It defines the step sequence, step metadata (model slot, max-output, thinking-budget, tools), and all LLM-facing instructions. Python conforms.

## What this pipeline does

Two-pass architecture: Pass 1 (Steps 0-4) extracts mechanically without a thesis. Pass 2 (Steps 5-11) re-scans chunks with the thesis and cross-chunk breadcrumbs injected, simulating whole-document analysis. Six lenses: Performance, Design, Specification, Usability, Ecosystem, Rationale. The pipeline produces a structural assay report and persists all intermediate artifacts (claims, evidence, breadcrumbs, thesis, findings) to paperstore for downstream consumption by agora.

13 steps (0-12), ~55 LLM calls for a 20-chunk paper (2C + V + K + 12), all serial.

## Layout

- `assay.md` - prompt document and pipeline authority.
- `models.py` - Pydantic models for domain types, LLM output types, and pipeline state.
- `harness.py` - pure Python: collect/dedup, breadcrumb upgrade, challenge kill filters, synthesize verdict. No LLM, no network I/O.
- `rag.py` - ephemeral RAG index: build vector index over cited papers, query for evidence injection. No LLM, embedder only.
- `pipeline.py` - async orchestration: step hooks, system prompt constants, dispatch loop, `assay_paper()` / `assay_since()` entry points.
- `render.py` - renders the assay report and diagnostic trace.

## Pipeline steps

```
 0. Receive        validate path, load metadata                   (pure Python)
 1. References     mechanical ref extraction, cross-check          (pure Python)
 2. Index          build RAG index over cited papers               (pure Python, embedder)
 3. Survey         blanking, chunking, wording signal, triage      (pure Python)
 4. Extract        per-chunk item extraction                       (fast agent, C calls)
 5. Scan           per-chunk breadcrumb detection                  (fast agent, C+ calls)
 6. Collect        dedup, group by lens, reference registry        (pure Python)
 7. Derive         thesis compression, load-bearing identification (default agent, 1 call)
 8. Research       per-lens external evidence + RAG injection      (tool agent, 6 calls)
 9. Probe          link verify, companion ingestion, cite verify   (tool agent, 1+K+V calls)
10. Analyze        per-chunk analysis with thesis                   (default agent, C calls)
11. Rationale      SD-4 checklist + quality findings               (default agent, 1 call)
12. Challenge      cross-examination + RAG injection               (default agent)
13. Couple         compound dynamic detection                      (default agent, 1 call)
14. Synthesize     promote Major, verdict derivation               (pure Python)
15. Report         render markdown                                 (pure Python)
```

Three named agents:

| Agent | Slot | Steps | Config |
|---|---|---|---|
| `extraction_agent` | `fast` | 4, 5 | `thinking_budget=2048` |
| `synthesis_agent` | `default` | 7, 10, 11, 12, 13 | `thinking_budget=4096` |
| `research_agent` | `tool` | 8, 9 | `thinking_budget=4096`, web tools |

## Invariants

- `assay.md` is the authority. Step metadata drives model slot and budget.
- No prompt strings in Python. All LLM-facing text comes from `assay.md` at runtime via `ctx.sections`. The step body text above `---` is the prompt; text below `---` is documentation.
- `harness.py` stays pure Python. No LLM calls, no network I/O.
- Fully batch. No interactive steps. No user identity.
- Paper text never enters the main context. All paper access through sub-agent calls with `wrap_source(format_numbered_lines(...))`.
- Serial execution. All LLM calls are sequential `for` loops with `await agent.run()`. No `asyncio.gather`, no `run_task`.
- Intermediate artifacts (claims, evidence, breadcrumbs, thesis, findings) are persisted to paperstore via `_persist_step` for downstream agora consumption.
- All LLM output types are frozen `BaseModel` with `output_type=`. Post-LLM fixup via `model_copy(update=...)`.
- Open-weight models are the production target. Prompts and schemas must work with `vllm_thinking` backends (Gemma, Qwen), not just Anthropic. Schema compliance issues on smaller models are solved with retries and prompt engineering. See root `CLAUDE.md` Model sovereignty.
