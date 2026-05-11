# review

LLM-driven paper extraction pipeline for WG21 (ISO C++) papers. Takes a
paper ID, pulls markdown from paperstore, runs a multi-step extractor
workflow via Pydantic AI, and returns structured output identifying
unsupported claims.

## The prompt-first model

`extractor.md` is the upstream authority for pipeline structure. Each
step section declares its metadata (model slot, execution mode, which
state fields it reads and writes, tools, guard conditions) and its
LLM-facing instructions. Python conforms to it.

At startup, `prompt.py` parses the metadata, validates it against the
registered Python hooks, and builds an ordered list of step
descriptors. If anything doesn't match, the pipeline fails with an
actionable error before the first LLM call.

## Pipeline overview

```
Step 0  Read           chunk paper, extract citations          (pure Python)
Step 1  Extract Claims extract normative assertions per chunk  (parallel LLM)
Step 2  Dedup Claims   deterministic tiers 0-1 + LLM tier 2   (hybrid)
Step 3  Extract Evid.  extract supporting facts per chunk      (parallel LLM)
Step 4  Dedup Evidence deterministic tiers 0-1 + LLM tier 2   (hybrid)
Step 5  Verify         cross-ref claims/evidence, map support  (single LLM)
Step 6  Load-Bearing   graph analysis: which claims matter     (single LLM)
Step 7  Web Search     search for evidence on critical gaps    (single LLM + tools)
Step 8  Resolve        integrate external evidence             (single LLM)
Step 9  Report         render final review markdown            (pure Python)
```

## Architecture

```
extractor.md    upstream authority: step metadata + LLM instructions
prompt.py       parse metadata, validate hooks, build StepSpec list
pipeline.py     hook registry (_HOOKS), generic runner, dispatch loop
render.py       output rendering (report, trace, debug transcript)
models.py       Pydantic domain models (sole schema authority)
harness.py      pure Python: chunking, SourceLoc, dedup tiers 0-1
parse.py        domain-free H2 markdown splitter
errors.py       error hierarchy (PromptFileError, StepError, paper errors)
```

The `StepSpec` combines parsed metadata (from `extractor.md`) with
bespoke Python hooks. Metadata provides WHAT (which model, which fields,
which tools). Hooks provide HOW (format the user message, store the
output). The generic runner handles everything common: Agent
construction, retries, debug logging, tool registration.

## Adding a step

1. Add a `## Step N` section to `extractor.md` with the metadata block:

```markdown
## Step 10 -- My New Step

- **Model:** default
- **Execution:** main
- **Reads:** claims, evidence
- **Writes:** my_new_field
```

2. Define a Pydantic output model in `models.py`.
3. Write `_prepare_*` and `_extract_*` hooks in `pipeline.py`.
4. Register in `_HOOKS` under the exact section header.
5. Add the new field to `PipelineState` in `models.py`.

## Error categories

| Error | Meaning | Action |
|---|---|---|
| `PromptFileError` | `extractor.md` has a structural problem | Edit `extractor.md` |
| `PaperNotFoundError` | Paper not in paperstore | Run `paperflow mailing` + `download` |
| `PaperNotConvertedError` | Paper has no markdown | Run `paperflow convert` |
| `TransientStepError` | API timeout, rate limit | Retry |
| `ValidationStepError` | LLM output didn't match schema | Pipeline bug |

## Usage

```python
from review import review_paper

report = await review_paper("P2300R10", backend)
```

## Development

```bash
uv run pytest packages/review/tests/
```
