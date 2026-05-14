#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Standalone sub-agent runner and debug rendering.

``run_task`` creates an isolated pydantic-ai Agent for a focused
mission (web search, citation verification, per-item analysis). It
handles concurrency limiting, debug transcript capture, and optional
output validation via pydantic-ai's native ``ModelRetry`` mechanism.

``render_debug_md`` serializes an agent run result into a markdown
debug section for diagnostic transcripts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

T = TypeVar("T", bound=BaseModel)

_TASK_CONCURRENCY = 1
"""Serial sub-task dispatch.

See ``MODELS.md`` at repo root. One in-flight ``run_task`` request at a
time eliminates cross-request batch interference at the inference layer.
Cost: longer wall clock on dissect Steps 10 (verify citations) and 11
(web search). Benefit: zero variance from concurrent KV-cache or expert
routing contention. Raising this requires a documented variance budget
per determinism rule D3."""

_task_semaphore = asyncio.Semaphore(_TASK_CONCURRENCY)


async def run_task(
    system_prompt: str,
    user_message: str,
    output_type: type[T],
    *,
    label: str = "run_task",
    debug_log: list[str] | None = None,
    tools: dict[str, Callable] | None = None,
    model: Any = None,
    request_limit: int = 10,
    output_validator: Callable | None = None,
    output_retries: int = 1,
) -> T:
    """Run an isolated sub-agent and return structured output.

    Focused mission, tight budget, one-way data flow. Raw content
    stays inside the task. Serial by design: ``_task_semaphore`` is
    ``asyncio.Semaphore(1)`` so callers fan out via ``asyncio.gather``
    without inducing concurrent in-flight LLM requests. See
    ``MODELS.md`` at repo root for the variance rationale.

    When ``output_validator`` is provided, it is registered on the
    agent via ``@agent.output_validator``. The validator should raise
    ``ModelRetry("reason")`` for incomplete output - pydantic-ai
    retries within the same conversation so the LLM sees its previous
    attempt and the rejection reason.

    When ``debug_log`` is provided, the rendered debug transcript is
    appended under the given ``label``. Serial dispatch makes
    transcript ordering deterministic.
    """
    from pipeline.runner import (
        DEFAULT_MODEL_SLOTS,
        MODEL_SETTINGS_BY_SLOT,
        _DEFAULT_MODEL_SETTINGS,
    )

    async with _task_semaphore:
        agent: Agent[None, T] = Agent(
            model or DEFAULT_MODEL_SLOTS["default"],
            output_type=output_type,
            system_prompt=system_prompt,
            output_retries=output_retries,
            model_settings=MODEL_SETTINGS_BY_SLOT.get(
                "default", _DEFAULT_MODEL_SETTINGS,
            ),
        )
        if tools:
            for name, fn in tools.items():
                agent.tool_plain(fn)
        if output_validator:
            agent.output_validator(output_validator)
        result = await agent.run(
            user_message,
            usage_limits=UsageLimits(request_limit=request_limit),
        )
        if debug_log is not None:
            debug_log.append(render_debug_md(result, label))
        return result.output


def _comment(text: str) -> str:
    return f"<!-- {text} -->"


def render_debug_prompt(system_prompt: str, user_msg: str, step_name: str) -> str:
    """Render the prompt that is about to be sent to the model."""
    return (
        f"{_comment(step_name)}\n"
        f"{_comment('system')}\n"
        f"{system_prompt.rstrip()}\n"
        f"{_comment('user')}\n"
        f"{user_msg.rstrip()}\n"
    )


def render_debug_response(result: Any) -> str:
    """Render the model's response after it returns."""
    parts: list[str] = []
    for msg in result.all_messages():
        if msg.kind == "response":
            for part in msg.parts:
                if part.part_kind == "text":
                    parts.append(f"{_comment('response')}\n{part.content}\n")
                elif part.part_kind == "tool-call":
                    tool_name = getattr(part, "tool_name", "")
                    args = getattr(part, "args", "")
                    args_str = json.dumps(args) if not isinstance(args, str) else args
                    parts.append(f"{_comment('tool-call: ' + tool_name)}\n{args_str}\n")
        elif msg.kind == "request":
            for part in msg.parts:
                if part.part_kind == "tool-return":
                    tool_name = getattr(part, "tool_name", "")
                    content = getattr(part, "content", "")
                    parts.append(f"{_comment('tool-return: ' + tool_name)}\n{content}\n")
    if hasattr(result, "output"):
        output = result.output
        if hasattr(output, "model_dump"):
            output_str = json.dumps(
                output.model_dump(), indent=2, ensure_ascii=False,
            )
        else:
            output_str = str(output)
        parts.append(f"{_comment('output')}\n{output_str}\n")
    return "\n".join(parts)


def render_debug_md(result: Any, step_name: str) -> str:
    """Render a complete debug transcript (backward compat)."""
    parts: list[str] = [f"{_comment(step_name)}\n"]
    for msg in result.all_messages():
        if msg.kind == "request":
            for part in msg.parts:
                if hasattr(part, "content") and part.part_kind == "system-prompt":
                    parts.append(f"{_comment('system')}\n{part.content}\n")
                elif hasattr(part, "content") and part.part_kind == "user-prompt":
                    parts.append(f"{_comment('user')}\n{part.content}\n")
                elif part.part_kind == "tool-return":
                    tool_name = getattr(part, "tool_name", "")
                    content = getattr(part, "content", "")
                    parts.append(f"{_comment('tool-return: ' + tool_name)}\n{content}\n")
        elif msg.kind == "response":
            for part in msg.parts:
                if part.part_kind == "text":
                    parts.append(f"{_comment('response')}\n{part.content}\n")
                elif part.part_kind == "tool-call":
                    tool_name = getattr(part, "tool_name", "")
                    args = getattr(part, "args", "")
                    args_str = json.dumps(args) if not isinstance(args, str) else args
                    parts.append(f"{_comment('tool-call: ' + tool_name)}\n{args_str}\n")
    if hasattr(result, "output"):
        output = result.output
        if hasattr(output, "model_dump"):
            output_str = json.dumps(
                output.model_dump(), indent=2, ensure_ascii=False,
            )
        else:
            output_str = str(output)
        parts.append(f"{_comment('output')}\n{output_str}\n")
    return "\n".join(parts)
