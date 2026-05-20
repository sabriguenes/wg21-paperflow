#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Unit tests for `validate_capabilities` and AgentBackend identity properties."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from pipeline.agents import AgentBackend
from pipeline.errors import CapabilityMismatchError, PipelineError
from pipeline.model_backends import DEFAULT_REQUEST_LIMIT, ModelBackend
from pipeline.prompt import StepHooks, StepMeta, StepSpec
from pipeline.validate import validate_capabilities


class _Empty(BaseModel):
    pass


class _CapableBackend(ModelBackend):
    """Tools-capable, thinking-capable. The happy path."""

    thinking_capable = True
    tools_capable = True

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[Any],
        *,
        tools: dict[str, Any] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> Any:
        return output_type()


class _ToollessBackend(ModelBackend):
    """Thinks but cannot call tools."""

    thinking_capable = True
    tools_capable = False

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[Any],
        *,
        tools: dict[str, Any] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> Any:
        return output_type()


class _NonThinkingBackend(ModelBackend):
    """Calls tools but does not think."""

    thinking_capable = False
    tools_capable = True

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[Any],
        *,
        tools: dict[str, Any] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> Any:
        return output_type()


def _spec(
    *,
    name: str = "11. Verify Citations",
    number: int = 11,
    tools: list[str] | None = None,
    agent: AgentBackend | None = None,
) -> StepSpec:
    return StepSpec(
        meta=StepMeta(
            name=name,
            number=number,
            model_slot="tool",
            execution="main",
            tools=tools or [],
        ),
        hooks=StepHooks(agent=agent),
    )


def _step_numbers_in(msg: str) -> set[int]:
    """Parse step numbers out of the rendered mismatch table.

    Data rows start with leading whitespace and a digit-string in the
    first column. The header ("Step") and separator ("----") rows are
    excluded by the isdigit check.
    """
    nums: set[int] = set()
    for line in msg.splitlines():
        s = line.lstrip()
        if s and s.split()[0].isdigit():
            nums.add(int(s.split()[0]))
    return nums


def test_validate_rejects_tools_on_toolless_agent():
    agent = AgentBackend(
        _ToollessBackend(),
        slot_name="tool",
        service_name="b200-r1",
    )
    spec = _spec(tools=["web_fetch"], agent=agent)
    with pytest.raises(CapabilityMismatchError) as exc_info:
        validate_capabilities([spec])
    msg = str(exc_info.value)
    assert 11 in _step_numbers_in(msg)
    assert "tool" in msg
    assert "b200-r1" in msg
    assert "_ToollessBackend" in msg
    assert "declares tools" in msg
    # The trailing footer.
    assert "documentation-only" in msg
    assert "--service SLOT=" in msg


def test_validate_accepts_tools_on_capable_agent():
    agent = AgentBackend(
        _CapableBackend(),
        slot_name="tool",
        service_name="b200-llama",
    )
    spec = _spec(tools=["web_fetch"], agent=agent)
    validate_capabilities([spec])  # should not raise


def test_validate_rejects_thinking_budget_on_non_thinking_agent():
    agent = AgentBackend(
        _NonThinkingBackend(),
        thinking_budget=4096,
        slot_name="default",
        service_name="anthropic-haiku",
    )
    spec = _spec(
        name="9. Verify",
        number=9,
        tools=[],
        agent=agent,
    )
    with pytest.raises(CapabilityMismatchError) as exc_info:
        validate_capabilities([spec])
    msg = str(exc_info.value)
    assert 9 in _step_numbers_in(msg)
    assert "default" in msg
    assert "anthropic-haiku" in msg
    assert "thinking_budget=4096" in msg
    assert "unsupported" in msg


def test_validate_accepts_thinking_budget_on_thinking_agent():
    agent = AgentBackend(
        _CapableBackend(),
        thinking_budget=4096,
        slot_name="default",
        service_name="b200-r1",
    )
    spec = _spec(name="9. Verify", number=9, tools=[], agent=agent)
    validate_capabilities([spec])  # should not raise


def test_validate_accepts_step_without_tools_or_thinking():
    agent = AgentBackend(
        _ToollessBackend(),
        slot_name="fast",
        service_name="b200-r1",
    )
    spec = _spec(tools=[], agent=agent)
    validate_capabilities([spec])  # no requirements -> no error


def test_validate_accepts_step_with_tools_and_no_agent():
    # Custom hook may declare meta.tools without binding an agent.
    spec = _spec(tools=["web_fetch"], agent=None)
    validate_capabilities([spec])  # nothing to gate -> no error


def test_validate_reports_all_mismatches():
    toolless = AgentBackend(
        _ToollessBackend(), slot_name="tool", service_name="bad-tool-svc",
    )
    non_thinking = AgentBackend(
        _NonThinkingBackend(),
        thinking_budget=4096,
        slot_name="default",
        service_name="bad-thinking-svc",
    )
    capable = AgentBackend(
        _CapableBackend(),
        thinking_budget=2048,
        slot_name="fast",
        service_name="good-svc",
    )
    specs = [
        _spec(name="11. Verify Citations", number=11, tools=["web_fetch"], agent=toolless),
        _spec(name="9. Verify", number=9, tools=[], agent=non_thinking),
        _spec(name="2. Extract Claims", number=2, tools=[], agent=capable),
    ]
    with pytest.raises(CapabilityMismatchError) as exc_info:
        validate_capabilities(specs)
    msg = str(exc_info.value)
    nums = _step_numbers_in(msg)
    assert 11 in nums
    assert 9 in nums
    assert 2 not in nums
    # Clean step's slot/service does not appear in the table.
    assert "good-svc" not in msg
    # Single trailing footer, not per line.
    assert msg.count("documentation-only") == 1


def test_capability_mismatch_error_is_pipeline_error():
    assert issubclass(CapabilityMismatchError, PipelineError)


def test_agent_backend_runtime_check_still_fires_for_undeclared_tools():
    agent = AgentBackend(_ToollessBackend())

    async def _go() -> Any:
        return await agent.run(
            "sys", "user", _Empty,
            tools={"x": lambda: None},
        )

    with pytest.raises(NotImplementedError):
        asyncio.run(_go())


def test_agent_backend_carries_slot_and_service_names():
    agent = AgentBackend(
        _CapableBackend(),
        slot_name="tool",
        service_name="b200-r1",
    )
    assert agent.slot_name == "tool"
    assert agent.service_name == "b200-r1"
    assert agent.backend_class_name == "_CapableBackend"

    bare = AgentBackend(_CapableBackend())
    assert bare.slot_name == ""
    assert bare.service_name == ""


def test_validate_stop_after_skips_out_of_scope_specs():
    # An in-scope spec is clean; an out-of-scope spec would fail.
    # validate must not raise.
    clean = AgentBackend(
        _CapableBackend(),
        slot_name="fast",
        service_name="good",
    )
    bad = AgentBackend(
        _ToollessBackend(),
        slot_name="tool",
        service_name="bad",
    )
    specs = [
        _spec(name="0. Read", number=0, tools=[], agent=clean),
        _spec(name="11. Verify Citations", number=11, tools=["web_fetch"], agent=bad),
    ]
    validate_capabilities(specs, stop_after=0)  # bad spec is out of scope
    with pytest.raises(CapabilityMismatchError):
        validate_capabilities(specs, stop_after=1)  # bad spec is in scope


def test_validate_thinking_budget_zero_is_treated_as_unset():
    # An agent without an explicit thinking_budget gets None; an
    # explicit 0 should also not trigger the thinking-capable check
    # because the agent is asking for no thinking at all.
    agent = AgentBackend(
        _NonThinkingBackend(),
        thinking_budget=0,
        slot_name="default",
        service_name="non-thinker",
    )
    spec = _spec(name="9. Verify", number=9, tools=[], agent=agent)
    validate_capabilities([spec])  # no raise
