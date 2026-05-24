#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Step execution engine for pipeline runners.

Provides the core loop (``dispatch``), the framework-managed agent
caller (``run_agent``), shared context (``StepContext``), and prompt
file loading (``load_sections``). Each downstream pipeline (agora)
imports these and supplies its own hooks, state, and entry function.

Model selection, structured output strategy, and provider-specific
workarounds live in ``model_backends.py``. Service configuration
lives in ``SERVICES.toml`` and is loaded by ``services.py``. This
module contains only structural orchestration.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.resources
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from paperstore.progress import ProgressCallback, ProgressEvent

from pipeline.agents import AgentBackend
from pipeline.errors import (
    HookMismatchError,
    PipelineError,
    PromptFileError,
    StepError,
)
from pipeline.model_backends import DEFAULT_REQUEST_LIMIT
from pipeline.markdown import sections
from pipeline.prompt import PipelinePrompt, StepSpec
from pipeline.tasks import render_debug_prompt
from pipeline.tools import _random_tag, guard_instruction as _guard_instruction, inject_untrusted as _inject_untrusted

logger = logging.getLogger(__name__)

_DEBUG_SEPARATOR = "\n"


def _empty_prompt() -> PipelinePrompt:
    """Default :class:`PipelinePrompt` for tests and pure-Python entry points.

    Production pipelines build their context with a real
    ``PipelinePrompt.load(...)`` result. Unit tests for the runner
    proper (which do not exercise prompt parsing) get a blank prompt
    by default so they don't have to construct one by hand.
    """
    return PipelinePrompt(
        package="",
        filename="",
        sections={},
        services={},
        config={},
        system_prompt="",
        preamble="",
        steps=(),
    )


@dataclass
class StepMetrics:
    """Timing and usage data collected for one pipeline step."""

    name: str
    duration_s: float = 0.0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)


@dataclass
class StepContext:
    """Shared resources available to every step."""

    prompt: PipelinePrompt = field(default_factory=_empty_prompt)
    agents: dict[str, AgentBackend] = field(default_factory=dict)
    # Local classifier slots. Parallel to ``agents``; populated by the
    # orchestrator from ``resolve_classifier_slots``. Read by custom
    # steps that need a deterministic, non-LLM classifier
    # (e.g. ``ctx.classifiers["selector"]``).
    classifiers: dict[str, Any] = field(default_factory=dict)
    researcher: Any = None
    backend: Any = None
    debug: bool = False
    pid: str = ""
    debug_log: list[str] | None = None
    tool_registry: dict[str, Callable[..., Any]] = field(default_factory=dict)
    step_metrics: list[StepMetrics] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)
    on_progress: ProgressCallback | None = None
    default_concurrency: int = 1
    _progress_step: int = 0
    _progress_total: int = 0
    # CLI ``--chunk N`` constraint. ``None`` means "all chunks";
    # otherwise restricts per-chunk fan-out to chunk N. Read by both
    # the parallel-LLM dispatch path (already handled in dispatch())
    # and by custom hooks that operate per-chunk (which would otherwise
    # run the classifier over every chunk's sentences regardless of
    # the flag).
    chunk_index: int | None = None
    embedder: Any = None
    _guard_tag: str = field(default_factory=_random_tag)

    def inject_untrusted(self, content: str) -> str:
        """Wrap untrusted content in guard markers. Stateless, thread-safe."""
        return _inject_untrusted(content, self._guard_tag)

    @property
    def guard_instruction(self) -> str:
        """System-prompt instruction for the guard delimiters."""
        return _guard_instruction(self._guard_tag)
    _current_spec: StepSpec | None = None

    def __post_init__(self) -> None:
        if self.debug and self.debug_log is None:
            self.debug_log = []

    async def gather_concurrent(
        self,
        coros: list[Any],
        concurrency: int = 2,
        label: str = "",
    ) -> list[Any]:
        """Run coroutines with bounded concurrency, preserving order.

        Each coroutine should return ``(index, result)``. Results are
        sorted by index so output order is deterministic regardless of
        completion order. Progress fires automatically after each
        coroutine completes.
        """
        n = len(coros)
        sem = asyncio.Semaphore(concurrency)
        completed = 0

        async def _bounded(coro):
            nonlocal completed
            async with sem:
                result = await coro
            completed += 1
            self.sub_progress(
                completed - 1, n,
                f"{label} {completed}/{n}" if label else f"{completed}/{n}",
            )
            return result

        results = await asyncio.gather(*[_bounded(c) for c in coros])
        return sorted(results, key=lambda x: x[0])

    def sub_progress(self, chunk: int, n_chunks: int, name: str) -> None:
        """Fire a fractional progress event within a multi-chunk step."""
        if self.on_progress is None or n_chunks <= 0:
            return
        frac = (chunk + 1) / n_chunks
        pct = (self._progress_step + frac) / max(self._progress_total, 1)
        self.on_progress(ProgressEvent(
            step=self._progress_step,
            total=self._progress_total,
            name=name,
            pct=pct,
        ))

    @property
    def sections(self) -> Mapping[str, str]:
        """Shim for steps that still read raw section bodies directly.

        Prefer :meth:`PipelinePrompt.step_section` (``ctx.prompt.step_section(name)``)
        which strips metadata bullets and the embedded ``### System Prompt`` block.
        """
        return self.prompt.sections

    def system_prompt_for(self, spec: StepSpec) -> str:
        """Return the composed system prompt for a step."""
        return _compose_system_prompt(spec, self)


@functools.cache
def load_sections(package: str, filename: str) -> dict[str, str]:
    """Load and parse a prompt file once per (package, filename) pair."""
    try:
        resource = importlib.resources.files(package).joinpath(filename)
        return sections(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        raise PromptFileError(
            f"Failed to read {filename}: {exc}"
        ) from exc


def _compose_system_prompt(spec: StepSpec, ctx: StepContext) -> str:
    """Compose framework, pipeline, and optional step system prompts."""
    floor = ctx.guard_instruction
    pipeline_prompt = ctx.prompt.system_prompt
    step_prompt = spec.step.system_prompt.strip()

    if spec.step.system_prompt_mode == "replace":
        parts = [floor, step_prompt]
    elif step_prompt:
        parts = [floor, pipeline_prompt, step_prompt]
    else:
        parts = [floor, pipeline_prompt]

    return "\n\n".join(part for part in parts if part)


async def run_agent(
    ctx: StepContext,
    spec: StepSpec,
    user_msg: str,
    *,
    request_limit: int = DEFAULT_REQUEST_LIMIT,
) -> Any:
    """Dispatch a single LLM call through the step's assigned agent.

    The agent (``spec.hooks.agent``) handles all model-specific
    concerns: structured output, thinking, BPE cleanup, tool calling.
    This function only composes the system prompt and gathers tools.

    Agent resolution: when ``spec.hooks.agent`` is set, use it
    directly. Otherwise look up ``ctx.agents[spec.step.model]`` so the
    pipeline markdown's ``**Model:**`` declaration drives binding.
    """
    agent: AgentBackend | None = spec.hooks.agent
    if agent is None:
        try:
            agent = ctx.agents[spec.step.model]
        except KeyError as exc:
            raise HookMismatchError(
                f"Step '{spec.step.name}' references logical model "
                f"'{spec.step.model}' but no agent is bound to that name. "
                f"Available agents: {sorted(ctx.agents)}"
            ) from exc

    system = ctx.system_prompt_for(spec)
    output_type = spec.hooks.output_type

    tools: dict[str, Callable] | None = None
    if spec.step.tools:
        tools = {}
        for tool_name in spec.step.tools:
            if tool_name not in ctx.tool_registry:
                raise HookMismatchError(
                    f"Step '{spec.step.name}' declares tool '{tool_name}' "
                    f"but no callable is registered in the tool registry. "
                    f"Available tools: {sorted(ctx.tool_registry)}"
                )
            tools[tool_name] = ctx.tool_registry[tool_name]

    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(render_debug_prompt(system, user_msg, spec.step.name))

    try:
        result = await agent.run(
            system, user_msg, output_type,
            tools=tools,
            label=spec.step.name,
            debug_log=ctx.debug_log if ctx.debug else None,
            request_limit=request_limit,
        )
    except Exception as exc:
        raise StepError(spec.step.number, spec.step.name, exc) from exc

    return result


async def dispatch(
    pipeline: list[StepSpec],
    state: Any,
    ctx: StepContext,
    *,
    tool_name: str = "",
    stop_after: int | None = None,
    chunk_index: int | None = None,
    on_progress: ProgressCallback | None = None,
    on_step_complete: Callable[[StepSpec, Any], None] | None = None,
    trace_path: Path | None = None,
    debug_path: Path | None = None,
    render_trace_fn: Callable[[Any, int], str] | None = None,
) -> None:
    """Execute the pipeline step by step.

    Three execution modes:

    - **custom**: ``hooks.custom(state, ctx, spec)`` owns execution entirely.
    - **parallel**: ``hooks.prepare(state, ctx)`` returns ``list[str]``.
      Framework dispatches N ``run_agent`` calls sequentially.
      ``hooks.extract(state, list[output])`` merges results.
      When ``chunk_index`` is set, only that chunk is sent.
    - **default**: ``hooks.prepare(state, ctx)`` returns ``str``.
      Framework calls ``run_agent``. ``hooks.extract(state, output)``
      stores the result.
    """
    total = len(pipeline)
    last_completed_step = -1
    # Expose chunk_index and progress to custom hooks.
    ctx.chunk_index = chunk_index
    ctx.on_progress = on_progress
    ctx._progress_total = total

    def _flush_trace_and_debug() -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if trace_path and render_trace_fn:
            step = last_completed_step if last_completed_step >= 0 else 0
            content = render_trace_fn(state, step)
            label = tool_name.title() if tool_name else "Trace"
            header = f"# {label} {ctx.pid} {ts}\n\n"
            trace_path.write_text(
                header + content, encoding="utf-8",
            )
        if debug_path and ctx.debug_log:
            write_debug_file(debug_path, ctx.debug_log, timestamp=ts)

    try:
        for i, spec in enumerate(pipeline):
            if stop_after is not None and i > stop_after:
                break

            ctx._progress_step = i
            if on_progress is not None:
                on_progress(ProgressEvent(
                    step=i, total=total, name=spec.step.name, pct=i / total,
                ))

            if spec.hooks.guard and not spec.hooks.guard(state):
                logger.info("Step %d: %s (skipped by guard)", i, spec.step.name)
                continue

            logger.info("Step %d: %s", i, spec.step.name)
            if ctx.debug and ctx.debug_log is not None:
                ctx.debug_log.append(f"## Step {i} ({spec.step.name})\n")
            ctx._current_spec = spec
            ctx.tool_counts = {}
            t0 = time.monotonic()
            metrics = StepMetrics(name=spec.step.name)

            effective_request_limit = (
                spec.hooks.request_limit
                if spec.hooks.request_limit is not None
                else DEFAULT_REQUEST_LIMIT
            )

            try:
                if spec.hooks.custom:
                    await spec.hooks.custom(state, ctx, spec)
                elif spec.hooks.parallel:
                    assert spec.hooks.prepare is not None
                    user_msgs = spec.hooks.prepare(state, ctx)

                    if chunk_index is not None:
                        if chunk_index < len(user_msgs):
                            user_msgs = [user_msgs[chunk_index]]
                        else:
                            user_msgs = []

                    results: list[Any] = []
                    for msg in user_msgs:
                        results.append(await run_agent(
                            ctx, spec, msg,
                            request_limit=effective_request_limit,
                        ))
                    if spec.hooks.extract:
                        spec.hooks.extract(state, results)
                else:
                    assert spec.hooks.prepare is not None
                    user_msg = spec.hooks.prepare(state, ctx)
                    result = await run_agent(
                        ctx, spec, user_msg,
                        request_limit=effective_request_limit,
                    )
                    if spec.hooks.extract:
                        spec.hooks.extract(state, result)
            except (StepError, PromptFileError, PipelineError):
                raise
            except Exception as exc:
                logger.error(
                    "Step %d (%s) failed: %s", i, spec.step.name, exc, exc_info=True,
                )
                raise StepError(i, spec.step.name, exc) from exc

            metrics.duration_s = time.monotonic() - t0
            metrics.tool_calls = dict(ctx.tool_counts)
            ctx.step_metrics.append(metrics)

            if on_step_complete is not None:
                on_step_complete(spec, state)

            last_completed_step = i
            _flush_trace_and_debug()
    finally:
        _flush_trace_and_debug()

    if on_progress is not None:
        on_progress(ProgressEvent(
            step=total, total=total, name="done", pct=1.0,
        ))


def write_debug_file(path: Path, debug_log: list[str], *, timestamp: str = "") -> None:
    """Join debug log entries and write to disk."""
    if debug_log:
        header = f"{timestamp}\n" if timestamp else ""
        path.write_text(header + _DEBUG_SEPARATOR.join(debug_log), encoding="utf-8")
