#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Step execution engine for pipeline runners.

Provides the core loop (``dispatch``), the framework-managed agent
caller (``run_agent``), shared context (``StepContext``), and prompt
file loading (``load_sections``). Each downstream pipeline (dissect,
advocatus, agora) imports these and supplies its own hooks, state,
and entry function.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.resources
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from paperstore.progress import ProgressCallback, ProgressEvent

from pipeline.errors import (
    HookMismatchError,
    PipelineError,
    PromptFileError,
    StepError,
    TransientStepError,
    ValidationStepError,
)
from pipeline.markdown import sections
from pipeline.prompt import StepHooks, StepSpec
from pipeline.tasks import render_debug_md

logger = logging.getLogger(__name__)

DEFAULT_MODEL_SLOTS = {
    "fast": "anthropic:claude-haiku-4-5-20251001",
    "default": "anthropic:claude-opus-4-6",
}

MODEL_SETTINGS_BY_SLOT = {
    "fast": ModelSettings(max_tokens=64000),
    "default": ModelSettings(max_tokens=80000),
}
_DEFAULT_MODEL_SETTINGS = ModelSettings(max_tokens=80000)

_SECTION_SYSTEM_PROMPT = "System Prompt"
_RETRIES_SINGLE = 3
_DEBUG_SEPARATOR = "\n\n---\n\n"

_TRANSIENT_EXCEPTIONS = (ModelHTTPError,)
_VALIDATION_EXCEPTIONS = (UnexpectedModelBehavior, UsageLimitExceeded)


@dataclass
class StepContext:
    """Shared resources available to every step."""

    sections: dict[str, str]
    model_slots: dict[str, str]
    researcher: Any = None
    backend: Any = None
    debug: bool = False
    pid: str = ""
    debug_log: list[str] | None = None
    tool_registry: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _current_spec: StepSpec | None = None

    def __post_init__(self) -> None:
        if self.debug and self.debug_log is None:
            self.debug_log = []


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


def _resolve_model(spec: StepSpec, ctx: StepContext) -> str:
    """Resolve the model string for a step."""
    model_slot = spec.meta.model_slot
    resolved = ctx.model_slots.get(model_slot)
    if resolved is None:
        resolved = DEFAULT_MODEL_SLOTS.get(model_slot, model_slot)
    return resolved


async def run_agent(
    ctx: StepContext,
    spec: StepSpec,
    user_msg: str,
    *,
    request_limit: int = 500,
    retries: int = _RETRIES_SINGLE,
) -> Any:
    """Create an Agent, run it, handle debug logging and errors.

    Merges the old ``_run_agent`` + ``_run_agent_with_retry`` into one
    function. When ``spec.hooks.output_validator`` is set, registers it
    on the agent - pydantic-ai handles retry-with-feedback natively via
    ``ModelRetry``.

    Prompt-driven tool registration: reads ``spec.meta.tools``, looks
    up each name in ``ctx.tool_registry``, and registers on the Agent.
    """
    system = ctx.sections.get(_SECTION_SYSTEM_PROMPT, "")
    resolved = _resolve_model(spec, ctx)
    model_slot = spec.meta.model_slot

    agent: Agent[None, Any] = Agent(
        model=resolved,
        output_type=spec.hooks.output_type or str,
        system_prompt=system,
        retries=retries,
        model_settings=MODEL_SETTINGS_BY_SLOT.get(model_slot, _DEFAULT_MODEL_SETTINGS),
    )

    if spec.hooks.output_validator:
        agent.output_validator(spec.hooks.output_validator)

    for tool_name in spec.meta.tools:
        if tool_name not in ctx.tool_registry:
            raise HookMismatchError(
                f"Step '{spec.meta.name}' declares tool '{tool_name}' "
                f"but no callable is registered in the tool registry. "
                f"Available tools: {sorted(ctx.tool_registry)}"
            )
        fn = ctx.tool_registry[tool_name]
        if ctx.debug:
            fn = _wrap_tool_debug(fn, tool_name)
        agent.tool_plain(fn)

    try:
        result = await agent.run(
            user_msg, usage_limits=UsageLimits(request_limit=request_limit),
        )
    except (*_TRANSIENT_EXCEPTIONS, *_VALIDATION_EXCEPTIONS, StepError, PromptFileError):
        raise
    except Exception as exc:
        _classify_and_raise(exc, spec)

    if ctx.debug and ctx.debug_log is not None:
        ctx.debug_log.append(render_debug_md(result, spec.meta.name))

    return result


async def dispatch(
    pipeline: list[StepSpec],
    state: Any,
    ctx: StepContext,
    *,
    stop_after: int | None = None,
    on_progress: ProgressCallback | None = None,
    on_step_complete: Callable[[StepSpec, Any], None] | None = None,
) -> None:
    """Execute the pipeline step by step.

    Three execution modes:

    - **custom**: ``hooks.custom(state, ctx)`` owns execution entirely.
    - **parallel**: ``hooks.prepare(state, ctx)`` returns ``list[str]``.
      Framework gathers N ``run_agent`` calls. ``hooks.extract(state,
      list[output])`` merges results.
    - **default**: ``hooks.prepare(state, ctx)`` returns ``str``.
      Framework calls ``run_agent``. ``hooks.extract(state, output)``
      stores the result.

    ``on_step_complete`` is called after each successful step for
    side effects like database persistence.

    When ``stop_after`` is set, processing stops after completing
    that step (inclusive).
    """
    total = len(pipeline)
    for i, spec in enumerate(pipeline):
        if stop_after is not None and i > stop_after:
            break

        if on_progress is not None:
            on_progress(ProgressEvent(
                step=i, total=total, name=spec.meta.name, pct=i / total,
            ))

        if spec.hooks.guard and not spec.hooks.guard(state):
            logger.info("Step %d: %s (skipped by guard)", i, spec.meta.name)
            continue

        logger.info("Step %d: %s", i, spec.meta.name)
        ctx._current_spec = spec

        try:
            if spec.hooks.custom:
                await spec.hooks.custom(state, ctx)
            elif spec.hooks.parallel:
                assert spec.hooks.prepare is not None
                user_msgs = spec.hooks.prepare(state, ctx)
                results = await asyncio.gather(*[
                    run_agent(
                        ctx, spec, msg,
                        request_limit=spec.hooks.request_limit or 500,
                    )
                    for msg in user_msgs
                ])
                if spec.hooks.extract:
                    spec.hooks.extract(state, [r.output for r in results])
            else:
                assert spec.hooks.prepare is not None
                user_msg = spec.hooks.prepare(state, ctx)
                result = await run_agent(
                    ctx, spec, user_msg,
                    request_limit=spec.hooks.request_limit or 500,
                )
                if spec.hooks.extract:
                    spec.hooks.extract(state, result.output)
        except (StepError, PromptFileError, PipelineError):
            raise
        except Exception as exc:
            logger.error(
                "Step %d (%s) failed: %s", i, spec.meta.name, exc, exc_info=True,
            )
            raise StepError(i, spec.meta.name, exc) from exc

        if on_step_complete is not None:
            on_step_complete(spec, state)

    if on_progress is not None:
        on_progress(ProgressEvent(
            step=total, total=total, name="done", pct=1.0,
        ))


def write_debug_file(path: Path, debug_log: list[str]) -> None:
    """Join debug log entries and write to disk."""
    if debug_log:
        path.write_text(_DEBUG_SEPARATOR.join(debug_log), encoding="utf-8")


def _wrap_tool_debug(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    """Wrap a tool function to log calls when debugging."""
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        args_str = ", ".join(
            [repr(a) for a in args] +
            [f"{k}={repr(v)}" for k, v in kwargs.items()]
        )
        logger.debug("[tool] %s(%s)", name, args_str)
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    return wrapper


def _classify_and_raise(exc: Exception, spec: StepSpec) -> None:
    """Wrap a pydantic-ai exception into the appropriate StepError subclass."""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        raise TransientStepError(spec.meta.number, spec.meta.name, exc) from exc
    if isinstance(exc, _VALIDATION_EXCEPTIONS):
        raise ValidationStepError(spec.meta.number, spec.meta.name, exc) from exc
    raise StepError(spec.meta.number, spec.meta.name, exc) from exc
