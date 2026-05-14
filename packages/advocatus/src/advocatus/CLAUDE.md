# advocatus

LLM-driven paper examination pipeline (the two-office tribunal). Project-wide rules live in the root `CLAUDE.md`; framework rules in `packages/pipeline/src/pipeline/CLAUDE.md`; sampling and concurrency rationale in `MODELS.md`.

## Authority

`advocatus.md` is the upstream authority for pipeline structure. It defines the step sequence, step metadata (model slot, execution mode, tools, conditions), and all LLM-facing instructions. Python conforms.

## What this pipeline does

The Advocatus Diaboli drafts candidate charges; the Defensor Causae cross-examines each through six challenges (Confessio, Articulus, Testimonium, Humanitas, Prudentia, Dignitas). Surviving charges become objections in the Relatio; killed charges earn the section a probatio. The pipeline reads dissect output from paperstore and adds what dissect did not produce: stakeholder positions, candidate charges, Defensor verdicts, motivatio, the Relatio.

One-shot, fully batch. No `AskQuestion`, no human-in-the-loop, no resumable runs. A single overall `confidence: float` on the Relatio is the transparency signal.

## Layout

- `advocatus.md` - prompt document and pipeline authority.
- `about.md` - canonical office description. Mirrors `dissect/about.md`. Source of voice and rubric for `advocatus.md`.
- `prompt.py` - parses step metadata, validates against registered hooks, builds the ordered `StepSpec` list.
- `pipeline.py` - async orchestration: `StepContext`, hook registry (`_HOOKS`), dispatch loop, public `advocatus_paper()` entry point. Sub-agent dispatch goes through `pipeline.tasks.run_task`, which serializes via the shared `_task_semaphore`.
- `render.py` - renders the Relatio (Seal, Objections, Probationes, Tabula Fontium, Acta, Notae Minores).
- `parse.py` - domain-free H2 markdown section splitter.
- `models.py` - Pydantic models for domain types and per-step outputs. `SourceLoc` imported from `paperstore`.
- `errors.py` - error hierarchy.

## Pipeline architecture

11 steps (0-10) grouped into four phases (Citatio, Inquisitio, Examen, Relatio). Pure-Python steps: 0, 2, 3, 4, 10. LLM `default` steps: 1, 5, 6, 7, 8, 9.

Step 7 (Defensor Cross-Examination) spawns one isolated sub-agent per candidate charge via `run_task`. Each sub-agent receives only the candidate charge, the paper quote it attacks (with `SourceLoc`), the relevant dossier slice, the boundaries, the rhetoric, and the six-challenge rubric - never the prosecution's chain-of-thought. This is the structural adversarial separation.

## Invariants

- `advocatus.md` is the authority. Step metadata drives model slot, execution mode, and tool registration.
- No hardcoded prompt strings. All LLM-facing text comes from `advocatus.md` at runtime.
- No human loop. No `AskQuestion`, no input prompts, no resumable runs. The pipeline runs end-to-end and emits its best judgment.
- Provenance bound at generation time. Every charge, objection, probatio, nota minor carries a `SourceLoc` (from paperstore).
- Serial LLM dispatch. This pipeline depends on `pipeline.runner._parallel_semaphore` and `pipeline.tasks._task_semaphore` both being `asyncio.Semaphore(1)`. Step 7 (Defensor Cross-Examination) spawns one `run_task` per candidate charge; those sub-agents must execute sequentially so the Relatio is reproducible across runs. See D11 in the root `CLAUDE.md`. A future package that opts into concurrent dispatch must do so without raising those semaphores from this pipeline's perspective.
- D6 reminder: every step hook declares `output_type=<PydanticModel>`.
- D7 reminder: when feeding state collections into prompts, sort first. The `_dossier_slice_for_charge` heuristic already sorts by overlap score; preserve that pattern when extending it.
