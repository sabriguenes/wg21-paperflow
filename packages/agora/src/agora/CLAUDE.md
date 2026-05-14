# agora - Agent Rules

## Philosophy

`agora.md` is the upstream authority for pipeline structure. It defines
the step sequence, step metadata (model slot, execution mode, reads,
writes, tools, conditions), and all LLM-facing instructions. Python
conforms to it.

The agora pipeline plans a thread for a dissected WG21 paper. It
researches the paper's public reception, calibrates the discussion
heat and intellectual interest, and lays out every reply slot with a
brief describing what that reply must accomplish. It does **not**
generate reply text, characters, votes, or furniture. Those belong to
a future generation phase that will be added to this same pipeline.

The pipeline reads dissect output from paperstore and produces a
`Thread` whose generation-phase fields (content, character_username,
score, time_label, awards, etc.) are left as `None`. The brief on
each `Reply` is a permanent audit trail: "this reply addresses
anchor X from the Y domain lens." Inspectable forever.

The-mod.md is the creative reference: heat/interest tiers, Tables A-D,
the noise palette, encounter rules, content rules, ad palette, mod
roster. It ships as package data and is injected as context into the
LLM calls that need it.

**One-shot, fully batch.** No `AskQuestion`, no human-in-the-loop, no
resumable runs. The pipeline runs end-to-end and emits its planned
thread.

## Layout

- `agora.md` - prompt document and pipeline authority. Step metadata
  (Model, Execution, Reads, Writes, Tools, Condition) is parsed by
  `prompt.py` at startup. LLM-facing instructions are loaded as
  section text. Editing this file changes prompts and pipeline config
  without touching Python.
- `the-mod.md` - canonical creative reference. Tables, palettes,
  rules, voice. Injected into LLM call user messages where needed.
- `prompt.py` - parses step metadata from `agora.md`, validates against
  registered hooks, builds the ordered `StepSpec` list. Raises
  `PromptFileError` subtypes on structural mismatches. Copied from
  advocatus, domain-free.
- `pipeline.py` - async orchestration: `StepContext`, hook registry
  (`_HOOKS`), generic runner (`_run_agent`), dispatch loop, and the
  public `agora_paper()` and `agora_since()` entry points. Module-level
  `_task_semaphore = asyncio.Semaphore(5)` caps every parallel
  sub-agent dispatch.
- `render.py` - debug transcript and per-step trace renderers. No HTML.
- `parse.py` - **domain-free** H2 markdown section splitter. Copied
  from advocatus.
- `models.py` - Pydantic models. One schema (`Thread`, `Reply`,
  `EncounterPlan` and friends) matches the eventual database;
  analysis-phase fields are required, generation-phase fields are
  `Optional`. Per-step LLM output classes and `PipelineState` live
  here too. `SourceLoc` imported from `paperstore`.
- `errors.py` - error hierarchy.

## Pipeline architecture

8 steps (0-7). Pure-Python steps: 0, 7. LLM `default` steps: 1, 3, 4,
5, 6. Step 2 is pure orchestration over 3 parallel sub-agents.

Step 6 (Encounter Briefs) has a guard that skips it when the
calibration produced zero encounters.

The pipeline writes the final `Thread` as `{pid}.agora.json` via
`backend.write_agora_json`. Debug transcripts and per-step traces go
to `backend.get_debug_md_path(pid, "agora")` and
`backend.get_trace_md_path(pid, "agora")`.

## Invariants

- **`agora.md` is the authority.** Step metadata drives model slot,
  execution mode, reads/writes, and tool registration.
- **No hardcoded prompt strings (for main agent steps).** All
  LLM-facing instructions for the main-agent steps come from `agora.md`
  at runtime. Sub-agent system prompts are short role strings in
  Python; the substantive instructions still come from `agora.md`.
- **No human loop.** No `AskQuestion`, no input prompts, no resumable
  runs. The pipeline runs end-to-end and emits its best plan.
- **Generation fields stay None.** This package does not generate
  reply content, character assignments, vote scores, or furniture.
  Those fields exist on the models but are populated by a future
  generation phase.
- **Provenance bound at generation time.** Every `TechnicalAnchor`
  carries a `SourceLoc` (from paperstore).
- **Concurrency capped at 5.** Single module-level
  `asyncio.Semaphore(5)` covers every parallel sub-agent dispatch.
- **Paperstore is the only storage interface.** Never construct paths
  or write files directly.
- **Library code uses `logging`, never `print()`.** No
  `print(file=sys.stderr)` in any package except `cli`.

## Fidelity invariant

If full fidelity cannot be achieved, stop. Set the paper status to failed with a clear error message. Preserve the debug transcript for diagnosis. Never produce a partial result that could be mistaken for a complete one.

The thread plan depends on accurate dissect and advocatus output. If either is incomplete or absent, the thread plan would misrepresent the paper.

## Prompt injection defense

Content returned by tools (read_paper, web_fetch) is untrusted data. Mitigations:
- Structured output via pydantic-ai enforces the output schema
- Tool returns are wrapped in <<<SOURCE>>>/<<<END_SOURCE>>> delimiters
- System prompts instruct agents to treat delimited content as data, not instructions
- read_paper is scoped to one paper's markdown with a 500-line cap per call
