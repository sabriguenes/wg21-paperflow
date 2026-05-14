# agora

LLM-driven thread planning pipeline (the Mod). Project-wide rules live in the root `CLAUDE.md`; framework rules in `packages/pipeline/src/pipeline/CLAUDE.md`; sampling and concurrency rationale in `MODELS.md`.

## Authority

`agora.md` is the upstream authority for pipeline structure. It defines the step sequence, step metadata (model slot, execution mode, tools, conditions), and all LLM-facing instructions. Python conforms.

## What this pipeline does

Plans a thread for a dissected WG21 paper. Researches the paper's public reception, calibrates discussion heat and intellectual interest, and lays out every reply slot with a brief describing what that reply must accomplish.

It does not generate reply text, characters, votes, or furniture. Those belong to a future generation phase in this same pipeline. The pipeline reads dissect output from paperstore and produces a `Thread` whose generation-phase fields (content, character_username, score, time_label, awards, etc.) are left as `None`. The brief on each `Reply` is a permanent audit trail: "this reply addresses anchor X from the Y domain lens."

`the-mod.md` is the creative reference: heat/interest tiers, Tables A-D, the noise palette, encounter rules, content rules, ad palette, mod roster. It ships as package data and is injected as context into the LLM calls that need it.

One-shot, fully batch. No `AskQuestion`, no human-in-the-loop, no resumable runs.

## Layout

- `agora.md` - prompt document and pipeline authority.
- `the-mod.md` - canonical creative reference. Injected into LLM call user messages where needed.
- `prompt.py` - parses step metadata, validates against registered hooks, builds the ordered `StepSpec` list. Copied from advocatus, domain-free.
- `pipeline.py` - async orchestration: `StepContext`, hook registry (`_HOOKS`), dispatch loop, public `agora_paper()` and `agora_since()` entry points. Sub-agent dispatch goes through `pipeline.tasks.run_task`, which serializes via the shared `_task_semaphore`.
- `render.py` - debug transcript and per-step trace renderers. No HTML.
- `parse.py` - domain-free H2 markdown section splitter.
- `models.py` - Pydantic models. One schema (`Thread`, `Reply`, `EncounterPlan` and friends) matches the eventual database; analysis-phase fields are required, generation-phase fields are `Optional`. Per-step LLM output classes and `PipelineState` live here too. `SourceLoc` imported from `paperstore`.
- `errors.py` - error hierarchy.

## Pipeline architecture

8 steps (0-7). Pure-Python steps: 0, 7. LLM `default` steps: 1, 3, 4, 5, 6. Step 2 is pure orchestration over 3 parallel sub-agents.

Step 6 (Encounter Briefs) has a guard that skips it when calibration produced zero encounters.

The pipeline writes the final `Thread` as `{pid}.agora.json` via `backend.write_agora_json`. Debug transcripts and per-step traces go to `backend.get_debug_md_path(pid)` and `backend.get_trace_md_path(pid)`.

## Invariants

- `agora.md` is the authority. Step metadata drives model slot, execution mode, and tool registration.
- No hardcoded prompt strings for main agent steps. Main-agent LLM-facing instructions come from `agora.md` at runtime. Sub-agent system prompts are short role strings in Python; the substantive instructions still come from `agora.md`.
- No human loop. The pipeline runs end-to-end and emits its best plan.
- Generation fields stay `None`. This package does not generate reply content, character assignments, vote scores, or furniture.
- Provenance bound at generation time. Every `TechnicalAnchor` carries a `SourceLoc` (from paperstore).
- D6 reminder: every step hook declares `output_type=<PydanticModel>`.
- D7 reminder: validation sets like `addressed`, `lens_used`, `orphan_encounter` in `_validate_blueprint` stay internal. If you start feeding such collections into a prompt, sort them first.
