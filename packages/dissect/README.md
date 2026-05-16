# dissect

LLM-driven paper extraction pipeline for WG21 (ISO C++) papers. Takes a
paper ID, pulls markdown from paperstore, runs a multi-step extractor
workflow via Pydantic AI, and returns structured output identifying
unsupported claims.

## The prompt-first model

`dissect.md` is the upstream authority for pipeline structure. Each
step section declares its metadata (model slot, execution mode, tools,
guard conditions) and its
LLM-facing instructions. Python conforms to it.

At startup, `prompt.py` parses the metadata, validates it against the
registered Python hooks, and builds an ordered list of step
descriptors. If anything doesn't match, the pipeline fails with an
actionable error before the first LLM call.

## Pipeline overview

```
Step 0  Read              chunk paper, extract citations          (pure Python)
Step 1  Tag Sentences     decompose + classify each sentence      (pure Python + local classifier)
Step 2  Extract Claims    normative claims per chunk              (parallel LLM)
Step 3  Dedup Claims      deterministic tiers 0-1-2 + shadow      (pure Python)
Step 4  Extract Evidence  supporting evidence per chunk           (parallel LLM)
Step 5  Dedup Evidence    deterministic tiers 0-1 + shadow        (pure Python)
Step 6  Extract Factual   factual claims per chunk                (parallel LLM)
Step 7  Dedup Factual     deterministic tiers 0-1-2               (pure Python)
Step 8  Extract Rhetoric  rhetoric markers per chunk              (parallel LLM)
Step 9  Verify            triage + propositions + disclaim        (custom LLM)
Step 10 Load-Bearing      classify + per-claim binary             (custom LLM)
Step 11 Verify Citations  fetch and verify each cited paper       (parallel LLM + web_fetch)
Step 12 Web Search        search for evidence on critical gaps    (parallel LLM + web_search/fetch)
Step 13 Resolve External  integrate external evidence             (single LLM)
Step 14 Caput Causae      identify the load-bearing root cause    (single LLM)
Step 15 Detect Patterns   cross-rhetoric pattern analysis         (single LLM)
Step 16 Report            render final dissect markdown           (pure Python)
```

## Architecture

```
dissect.md      upstream authority: step metadata + LLM instructions (17 steps, 0-16)
prompt.py       parse metadata, validate hooks, build StepSpec list
pipeline.py     hook registry (_HOOKS), generic runner, dispatch loop,
                  dissect_paper() and dissect_since() entry points
render.py       output rendering (report, trace, debug transcript)
models.py       Pydantic domain models (sole schema authority)
harness.py      pure Python: chunking, SourceLoc, dedup tiers 0-1, citations,
                  pysbd sentence decomposition for Step 1
parse.py        domain-free H2 markdown splitter
errors.py       error hierarchy (PromptFileError, StepError, paper errors)
```

## Classifier

Step 1 (Tag Sentences) uses a local zero-shot text classifier to tag
each sentence in each chunk as `target`, `context`, or `skip`. The
default is `MoritzLaurer/deberta-v3-large-zeroshot-v2.0` (~1.7GB,
~3-5s CPU per paper). First run auto-downloads weights into the local
HF cache; subsequent runs are fully offline.

Switch models via `SERVICES.toml`:

```toml
[classifier_defaults]
selector = "zeroshot-base"  # ~700MB, sub-second per paper
```

Or from the CLI:

```bash
paperflow dissect P4003R3 --classifier selector=zeroshot-base
```

Registered alternatives: `zeroshot-base` (faster), `nli-small`
(smallest, lowest accuracy). Add a new backend by registering a class
in `pipeline.classifier_backends.CLASSIFIER_BACKEND_REGISTRY`.

The `StepSpec` combines parsed metadata (from `dissect.md`) with
bespoke Python hooks. Metadata provides WHAT (which model, which fields,
which tools). Hooks provide HOW (format the user message, store the
output). The generic runner handles everything common: Agent
construction, retries, debug logging, tool registration.

## Adding a step

1. Add a `## Step N` section to `dissect.md` with the metadata block:

```markdown
## Step 17 -- My New Step

- **Model:** default
- **Execution:** main
```

2. Define a Pydantic output model in `models.py`.
3. Write `_prepare_*` and `_extract_*` hooks in `pipeline.py`.
4. Register in `_HOOKS` under the exact section header.
5. Add the new field to `PipelineState` in `models.py`.

## Error categories

| Error | Meaning | Action |
|---|---|---|
| `PromptFileError` | `dissect.md` has a structural problem | Edit `dissect.md` |
| `PaperNotFoundError` | Paper not in paperstore | Run `paperflow mailing` + `download` |
| `PaperNotConvertedError` | Paper has no markdown | Run `paperflow convert` |
| `TransientStepError` | API timeout, rate limit | Retry |
| `ValidationStepError` | LLM output didn't match schema | Pipeline bug |

## Usage

```python
from dissect import dissect_paper, dissect_since

# Single paper
report = await dissect_paper("P2300R10", backend)

# All papers from a mailing month onward
results = await dissect_since("2025-04", backend)
```

## Development

```bash
uv run pytest packages/dissect/tests/
```
