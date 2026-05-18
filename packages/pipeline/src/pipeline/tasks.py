#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Standalone sub-agent runner and debug rendering.

``run_task`` dispatches a focused LLM call through an ``AgentBackend``
with serial concurrency control. Used by custom hooks (Steps 8, 9,
10, 11) that fan out many small calls per step.

``render_debug_md`` serializes an agent run result into a markdown
debug section for diagnostic transcripts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

from pydantic import BaseModel

from pipeline.agents import AgentBackend

T = TypeVar("T", bound=BaseModel)

_TASK_CONCURRENCY = 1
"""Serial sub-task dispatch.

One in-flight ``run_task`` request at a time eliminates cross-request
batch interference at the inference layer. Raising this requires a
documented variance budget per determinism rule D3.
"""

_task_semaphore = asyncio.Semaphore(_TASK_CONCURRENCY)


async def run_task(
    agent: AgentBackend,
    system_prompt: str,
    user_message: str,
    output_type: type[T],
    *,
    tools: dict[str, Any] | None = None,
    label: str = "run_task",
    debug_log: list[str] | None = None,
) -> T:
    """Run an isolated sub-agent call and return structured output.

    Serial by design: ``_task_semaphore`` is ``asyncio.Semaphore(1)``
    so callers fan out via ``asyncio.gather`` without inducing
    concurrent in-flight LLM requests.
    """
    async with _task_semaphore:
        return await agent.run(
            system_prompt,
            user_message,
            output_type,
            tools=tools,
            label=label,
            debug_log=debug_log,
        )


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
