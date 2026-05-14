# advocatus - Agent Rules

## Philosophy

`advocatus.md` is the upstream authority for pipeline structure. It
defines the step sequence, step metadata (model slot, execution mode,
reads, writes, tools, conditions), and all LLM-facing instructions.
Python conforms to it.

The advocatus pipeline implements the two-office tribunal described in
`about.md`: the **Advocatus Diaboli** drafts candidate charges; the
**Defensor Causae** cross-examines each one through six challenges.
Surviving charges become objections in the *Relatio*; killed charges
earn the section a *probatio*. The pipeline reads dissect output from
paperstore and adds what dissect did not produce: stakeholder
positions, candidate charges, Defensor verdicts, motivatio, the
Relatio.

**One-shot, fully batch.** No `AskQuestion`, no human-in-the-loop, no
resumable runs. Single overall `confidence: float` on the Relatio is
the transparency signal.

## Layout

- `advocatus.md` - prompt document and pipeline authority. Step
  metadata (Model, Execution, Reads, Writes, Tools, Condition) is
  parsed by `prompt.py` at startup. LLM-facing instructions are loaded
  as section text. Editing this file changes prompts and pipeline
  config without touching Python.
- `about.md` - canonical office description (Advocatus Diaboli /
  Defensor Causae). Mirrors `dissect/about.md`. Source of voice and
  rubric for the LLM-facing text in `advocatus.md`.
- `prompt.py` - parses step metadata from `advocatus.md`, validates
  against registered hooks, builds the ordered `StepSpec` list. Raises
  `PromptFileError` subtypes on structural mismatches.
- `pipeline.py` - async orchestration: `StepContext`, hook registry
  (`_HOOKS`), generic runner (`_run_agent`), dispatch loop, and the
  public `advocatus_paper()` entry point. Module-level
  `_task_semaphore = asyncio.Semaphore(5)` caps every parallel
  sub-agent dispatch.
- `render.py` - renders the *Relatio* (Seal, Objections, Probationes,
  Tabula Fontium, Acta, Notae Minores).
- `parse.py` - **domain-free** H2 markdown section splitter. Copied
  from dissect.
- `models.py` - Pydantic models for domain types and per-step outputs.
  `SourceLoc` imported from `paperstore` (canonical home for the loc
  type).
- `errors.py` - error hierarchy.

## Pipeline architecture

11 steps (0-10) grouped into four phases (Citatio, Inquisitio, Examen,
Relatio). Pure-Python steps: 0, 2, 3, 4, 10. LLM `default` steps: 1,
5, 6, 7, 8, 9.

Step 7 (Defensor Cross-Examination) spawns one isolated sub-agent per
candidate charge. Each sub-agent receives only: the candidate charge,
the paper quote it attacks (with `SourceLoc`), the relevant dossier
slice, the boundaries, the rhetoric, and the six-challenge rubric.
Never the prosecution's chain-of-thought. This is the structural
adversarial separation.

## Invariants

- **`advocatus.md` is the authority.** Step metadata drives model slot,
  execution mode, reads/writes, and tool registration.
- **No hardcoded prompt strings.** All LLM-facing text comes from
  `advocatus.md` at runtime.
- **No human loop.** No `AskQuestion`, no input prompts, no resumable
  runs. The pipeline runs end-to-end and emits its best judgment.
- **Provenance bound at generation time.** Every charge, objection,
  probatio, nota minor carries a `SourceLoc` (from paperstore).
- **Concurrency capped at 5.** Single module-level
  `asyncio.Semaphore(5)` covers every parallel sub-agent dispatch.
- **Paperstore is the only storage interface.** Never construct paths
  or write files directly.
- **Library code uses `logging`, never `print()`.** No
  `print(file=sys.stderr)` in any package except `cli`.
