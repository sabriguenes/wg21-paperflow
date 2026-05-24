# Architecture

## Packages

- **paperstore** - Storage layer. SQLite backend for paper metadata, source files, markdown, and pipeline outputs. No network dependencies. Defines the `papers` table with status tracking.
- **mailing** - Scrapes WG21 mailing indexes from open-std.org. Populates paperstore with paper metadata and URLs. Also handles source file downloads. Admin-only operation.
- **tomd** - Converts paper source files (HTML, PDF) to markdown.
- **pipeline** - Framework for LLM-driven pipelines. Step execution engine (dispatch, run_agent, run_task), error hierarchy, prompt parsing, markdown utilities, web search/fetch, process_paper orchestration, read_paper tool.
- **dissect** - Extracts claims, evidence, and rhetoric from a paper's markdown. First analytical stage.
- **agora** - Plans a discussion thread for a dissected paper.
- **cli** - Command-line interface. Maps verbs to pipeline stages.

## Dependency graph

```
paperstore (no deps)
  <- mailing (scraping, downloading)
  <- tomd (conversion)
  <- pipeline (framework, web tools)
      <- dissect
      <- agora
  <- cli (user interface)
```

## Paper status model

Each paper has an integer `status` on the `papers` table:

```
 0 = download     -1 = failed at download
 1 = convert      -2 = failed at convert
 2 = dissect      -3 = failed at dissect
 4 = agora        -5 = failed at agora
 5 = herald       -6 = failed at herald
 6 = ready
```

Status means "the next action needed." Pipeline is linear: download -> convert -> dissect -> agora -> herald -> ready.

Failed status encodes which stage failed: `failed_status = -(stage + 1)`, recovery: `retry_stage = abs(status) - 1`. The `error` column stores the diagnostic message.

Constants live in `paperstore.stages.STAGES` and `STAGE_NAMES`.

## Processing model

`process_paper(pid, backend, through)` in the pipeline package walks one paper through stages up to `through`. Each stage does its work, then advances status with a CAS:

```sql
UPDATE papers SET status = :new WHERE paper_id = :pid AND status = :expected
```

If rowcount == 1, the caller won. If 0, someone else advanced it. Works on both SQLite (CLI) and Postgres (Django).

Duplicate work is harmless: file writes use atomic rename, content is deterministic from the same source.

## CLI

Each verb maps to a through-stage value:

| Command | through |
|---------|---------|
| paperflow mailing | n/a (scrapes everything) |
| paperflow download TARGET | 1 |
| paperflow convert TARGET | 2 |
| paperflow dissect TARGET | 3 |
| paperflow agora TARGET | 5 |
| paperflow status [TARGET] | n/a |

TARGET is a paper ID (P4003R2), year (2026), or month (2026-05). No "all" keyword.

Each command runs `process_paper` for matching papers, which auto-runs all prerequisite stages.

## Django integration

Django dispatches work using modulus sharding: each Celery worker gets `worker_id` and `num_workers`, processes papers where `paper_number % num_workers == worker_id`. Zero contention between workers.

Processing order within a shard: `ORDER BY status DESC, mailing_date DESC` (finish what's closest to done, newest mailing first).

Django calls the same `process_paper(pid, backend, through)` function. The `papers` table is the shared queue.

## Scraping policy

Scraping is an admin action, never a pipeline side effect:
- CLI: `paperflow mailing` scrapes all years 2011-current (idempotent)
- Django admin: calls mailing.scrape functions directly

Pipelines never scrape. If a citation references a paper not in the index, the citation is reported as not_found.

## Citation resolution

During dissect Step 8 (Verify Citations), the pipeline resolves cited papers:

1. Paper in DB with markdown available: sub-agent gets a `read_paper` tool scoped to that paper
2. Paper in DB without markdown: `ensure_paper_md` downloads and converts it, then provides the tool
3. Paper not in DB: reported as not_found
4. Paper in unreadable format (TIFF, PostScript): reported as unreadable

No wg21.link dependency. No URL guessing or cascade.

## Fidelity invariant

The analytical pipeline (dissect, agora) cannot tolerate partial results. These tools inform real decisions about real proposals. A false objection or missing evidence is worse than a failed run.

Rule: if full fidelity cannot be achieved, stop. Set paper status to failed. Preserve the debug transcript. Never produce a partial result that could be mistaken for a complete one.

## Prompt injection defense

Paper markdown and web-fetched content are untrusted data:
- Structured output via pydantic-ai enforces the output schema
- Tool returns wrapped in configured source delimiters via `pipeline.tools.wrap_source`
- System prompts instruct agents to treat delimited content as data
- read_paper tool is scoped to one paper, capped at 500 lines per call

## Global cutoff

`process_since` setting (YYYY-MM) in the settings table controls which papers get processed. Processing loops skip papers with mailing_date before the cutoff. The full mailing index exists regardless. Single-paper CLI commands ignore the cutoff.

## Concurrency

CAS on the status column works on SQLite (write serialization) and Postgres (row-level locking). Same Python code, same semantics:

```python
cur = conn.execute(
    "UPDATE papers SET status = ? WHERE paper_id = ? AND status = ?",
    (new_status, pid, expected_status),
)
won = cur.rowcount == 1
```

## Services and agents

Two layers separate infrastructure from pipeline intent:

```
SERVICES.toml -> load_services() -> dict[str, ModelBackend]
                                         |
                 resolve_slots() -> dict[str, ModelBackend]
                                         |
                 AgentBackend(slot, max_tokens=..., thinking_budget=...) -> per-pipeline agents
```

**ModelBackend** (one class per model family) encapsulates all mechanical concerns: structured output strategy, BPE cleanup, thinking-block stripping, tool-calling workarounds. Four backends: `VllmThinkingBackend`, `Llama3Backend`, `Qwen3Backend`, `AnthropicBackend`.

**AgentBackend** wraps a `ModelBackend` with pipeline-level config (`max_tokens`, `thinking_budget`). Validates `tools_capable` at call time.

**SERVICES.toml** is pure infrastructure inventory describing service capacity. Each `[services.NAME]` section declares an endpoint with its capabilities and `max_context_window`. API keys come from environment variables only. The `[defaults]` section maps slot names (`fast`, `default`, `tool`) to service names. Output token limits (`max_tokens`) are pipeline-level constants, not service config.

Pipelines create agents by intent, each defining its own `MAX_OUTPUT_TOKENS`:

```python
extraction_agent = AgentBackend(slots["fast"], max_tokens=MAX_OUTPUT_TOKENS, thinking_budget=2048)
synthesis_agent = AgentBackend(slots["default"], max_tokens=MAX_OUTPUT_TOKENS, thinking_budget=4096)
research_agent = AgentBackend(slots["tool"], max_tokens=MAX_OUTPUT_TOKENS)
```

CLI `--service` overrides beat `[defaults]`. Resolution order: `--service` > `[defaults]` > error.
