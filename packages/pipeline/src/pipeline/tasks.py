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

_TASK_CONCURRENCY = 5
_task_semaphore = asyncio.Semaphore(_TASK_CONCURRENCY)


async def run_task(
    system_prompt: str,
    user_message: str,
    output_type: type[T],
    *,
    label: str = "run_task",
    debug_log: list[str] | None = None,
    tools: dict[str, Callable] | None = None,
    model: str | None = None,
    request_limit: int = 10,
    output_validator: Callable | None = None,
    output_retries: int = 1,
) -> T:
    """Run an isolated sub-agent and return structured output.

    Focused mission, tight budget, one-way data flow. Raw content
    stays inside the task. Concurrency is capped at
    ``_TASK_CONCURRENCY`` (5) to avoid hitting API rate limits when
    many tasks run in parallel.

    When ``output_validator`` is provided, it is registered on the
    agent via ``@agent.output_validator``. The validator should raise
    ``ModelRetry("reason")`` for incomplete output - pydantic-ai
    retries within the same conversation so the LLM sees its previous
    attempt and the rejection reason.

    When ``debug_log`` is provided, the rendered debug transcript is
    appended under the given ``label``. Concurrent appends are
    GIL-atomic; final ordering is non-deterministic but no entries
    are lost.
    """
    from pipeline.runner import DEFAULT_MODEL_SLOTS

    async with _task_semaphore:
        agent: Agent[None, T] = Agent(
            model or DEFAULT_MODEL_SLOTS["default"],
            output_type=output_type,
            system_prompt=system_prompt,
            output_retries=output_retries,
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


def render_debug_md(result: Any, step_name: str) -> str:
    """Render an agent run result as a markdown debug section."""
    parts: list[str] = [f"# {step_name}\n"]
    for msg in result.all_messages():
        kind = msg.kind
        if kind == "request":
            for part in msg.parts:
                if hasattr(part, "content") and part.part_kind == "system-prompt":
                    parts.append(f"## System Prompt\n\n{part.content}\n")
                elif hasattr(part, "content") and part.part_kind == "user-prompt":
                    parts.append(f"## User Message\n\n{part.content}\n")
                elif part.part_kind == "tool-return":
                    tool_name = getattr(part, "tool_name", "")
                    content = getattr(part, "content", "")
                    parts.append(
                        f"### Tool Return: {tool_name}\n\n{content}\n"
                    )
        elif kind == "response":
            for part in msg.parts:
                if part.part_kind == "text":
                    parts.append(f"## Model Response\n\n{part.content}\n")
                elif part.part_kind == "tool-call":
                    tool_name = getattr(part, "tool_name", "")
                    args = getattr(part, "args", "")
                    args_str = json.dumps(args) if not isinstance(args, str) else args
                    parts.append(
                        f"### Tool Call: {tool_name}\n\n```json\n{args_str}\n```\n"
                    )
    if hasattr(result, "output"):
        output = result.output
        if hasattr(output, "model_dump"):
            output_str = json.dumps(
                output.model_dump(), indent=2, ensure_ascii=False,
            )
        else:
            output_str = str(output)
        parts.append(f"## Final Output\n\n```json\n{output_str}\n```\n")
    return "\n".join(parts)
