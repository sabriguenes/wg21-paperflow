#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Cross-package integration tests for capability validation.

Drives through ``assay.pipeline.assay_paper`` and through
``pipeline.build_pipeline`` + ``pipeline.validate_capabilities``
directly to verify that:

- Misbound logical models in ``assay.md``'s ``## Services`` fail before
  any LLM call.
- ``stop_after`` correctly scopes which steps are checked.
- ``validate_capabilities`` and ``dispatch`` agree on what ``stop_after``
  means.
- ``assay_paper`` fails fast end to end when the resolved ``tool``
  service is a toolless backend.

Model selection now lives entirely in ``assay.md``; there is no
``--service`` flag or ``service_overrides`` parameter. Negative cases
patch ``load_services`` to return a registry whose ``tool``-bound
service is toolless.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from assay import pipeline as assay_pipeline_mod
from pipeline import (
    AgentBackend,
    CapabilityMismatchError,
    PipelinePrompt,
    StepContext,
    build_pipeline,
    dispatch,
    validate_capabilities,
)
from pipeline.model_backends import DEFAULT_REQUEST_LIMIT, ModelBackend
from pipeline.prompt import StepHooks, StepPrompt, StepSpec
from pipeline.services import ServiceRegistry


class _ToolsCapableStub(ModelBackend):
    thinking_capable = True
    tools_capable = True

    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[Any],
        *,
        max_tokens: int = 16384,
        tools: dict[str, Any] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> Any:
        self.calls += 1
        return output_type()


class _ToollessStub(ModelBackend):
    thinking_capable = True
    tools_capable = False

    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[Any],
        *,
        max_tokens: int = 16384,
        tools: dict[str, Any] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> Any:
        self.calls += 1
        return output_type()


def _assay_prompt() -> PipelinePrompt:
    return PipelinePrompt.load("assay", "assay.md")


def _step_numbers_in(msg: str) -> set[int]:
    nums: set[int] = set()
    for line in msg.splitlines():
        s = line.lstrip()
        if s and s.split()[0].isdigit():
            nums.add(int(s.split()[0]))
    return nums


def _assay_agents_with_bad_default(
    default_service: str, default_backend: ModelBackend,
) -> dict[str, AgentBackend]:
    """Build the assay agent map with a deliberately misbound default slot.

    Step 10 (Research) declares tools and uses the ``default`` model.
    Making ``default`` toolless causes only that step to fail.
    """
    capable = _ToolsCapableStub()
    return {
        "gemma": AgentBackend(capable, slot_name="gemma", service_name="svc-gemma"),
        "deepseek": AgentBackend(capable, slot_name="deepseek", service_name="svc-deepseek"),
        "opus": AgentBackend(capable, slot_name="opus", service_name="svc-opus"),
        "default": AgentBackend(default_backend, slot_name="default", service_name=default_service),
    }


def test_assay_pipeline_rejects_toolless_default_slot():
    toolless = _ToollessStub()
    prompt = _assay_prompt()
    agents = _assay_agents_with_bad_default("bad-svc", toolless)
    hooks = assay_pipeline_mod._build_hooks()
    pipeline = build_pipeline(prompt, hooks)

    with pytest.raises(CapabilityMismatchError) as exc_info:
        validate_capabilities(pipeline, prompt, agents)
    msg = str(exc_info.value)
    nums = _step_numbers_in(msg)
    # Step 10 (Research) is the only step that declares Tools in
    # assay.md; it is the only one that should appear in the mismatch
    # table.
    assert 10 in nums
    assert "default" in msg
    assert "bad-svc" in msg
    assert "documentation-only" in msg
    # Steps without tool requirements must not appear.
    assert 4 not in nums
    assert 5 not in nums
    assert 7 not in nums


def test_assay_pipeline_with_stop_after_skips_out_of_scope_failures():
    toolless = _ToollessStub()
    prompt = _assay_prompt()
    agents = _assay_agents_with_bad_default("bad-svc", toolless)
    hooks = assay_pipeline_mod._build_hooks()
    pipeline = build_pipeline(prompt, hooks)

    # Step 10 (Research) is out of scope when stop_after=5; dispatch
    # will never reach the misbound default slot's tool requirement,
    # so validate must not punish the user for it.
    validate_capabilities(pipeline, prompt, agents, stop_after=5)  # must not raise


def _empty_prompt(*models: str) -> PipelinePrompt:
    return PipelinePrompt(
        package="test",
        filename="test.md",
        sections={},
        services={m: f"svc-{m}" for m in models},
        config={},
        system_prompt="",
        preamble="",
        steps=(),
    )


def _parity_spec(
    number: int,
    *,
    bad: bool,
    name: str | None = None,
) -> StepSpec:
    """Spec with a no-op custom hook. ``bad=True`` declares an
    incompatible tools requirement on a toolless agent."""
    agent = AgentBackend(
        _ToollessStub() if bad else _ToolsCapableStub(),
        slot_name="tool" if bad else "default",
        service_name="bad-svc" if bad else "good-svc",
    )

    async def _noop(state: Any, ctx: StepContext, spec: StepSpec) -> None:
        visited: set[int] = state.setdefault("visited", set())
        visited.add(number)

    return StepSpec(
        step=StepPrompt(
            name=name or f"{number}. Step {number}",
            number=number,
            model="tool" if bad else "default",
            execution="main",
            tools=("web_fetch",) if bad else (),
        ),
        hooks=StepHooks(agent=agent, custom=_noop),
    )


@pytest.mark.parametrize("stop_after", [None, 0, 2, 5])
def test_validate_capabilities_filtering_matches_dispatch_filtering(stop_after):
    # Indices 0-5: bad spec at index 3 only. validate must raise iff
    # dispatch with the same stop_after would actually visit index 3.
    specs = [
        _parity_spec(0, bad=False),
        _parity_spec(1, bad=False),
        _parity_spec(2, bad=False),
        _parity_spec(3, bad=True),
        _parity_spec(4, bad=False),
        _parity_spec(5, bad=False),
    ]
    prompt = _empty_prompt("default", "tool")

    state: dict[str, set[int]] = {}
    ctx = StepContext()

    asyncio.run(dispatch(specs, state, ctx, stop_after=stop_after))
    visited = state.get("visited", set())

    bad_in_scope = 3 in visited
    if bad_in_scope:
        with pytest.raises(CapabilityMismatchError):
            validate_capabilities(specs, prompt, stop_after=stop_after)
    else:
        validate_capabilities(specs, prompt, stop_after=stop_after)


def test_assay_paper_fails_fast_with_bad_service_binding(monkeypatch):
    """End-to-end: patch ``load_services`` so the service named under
    ``tool`` in assay.md resolves to a toolless backend. Validation
    must fire before any LLM call.

    Done without poking ``assay.md`` on disk: we patch
    ``load_services`` to return a registry whose service names match
    what assay.md's ``## Services`` block currently declares.
    """
    capable = _ToolsCapableStub()
    toolless = _ToollessStub()

    real_prompt = PipelinePrompt.load("assay", "assay.md")

    # Build a registry where every service name declared in assay.md
    # exists; the one bound to the `default` logical name is toolless.
    # Step 10 (Research) uses `default` and declares tools, so the
    # capability check must fire.
    services: dict[str, ModelBackend] = {}
    for logical, service_name in real_prompt.services.items():
        services[service_name] = toolless if logical == "default" else capable

    registry = ServiceRegistry(services=services, api_key_envs={})
    monkeypatch.setattr(
        assay_pipeline_mod,
        "load_services",
        lambda: registry,
    )

    class _BackendShouldNotBeUsed:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(
                f"backend.{name} called -- capability validation should "
                f"fail before any backend access"
            )

    async def _go() -> Any:
        return await assay_pipeline_mod.assay_paper(
            "P0000R0",
            _BackendShouldNotBeUsed(),
        )

    with pytest.raises(CapabilityMismatchError) as exc_info:
        asyncio.run(_go())
    msg = str(exc_info.value)
    assert 10 in _step_numbers_in(msg)
    assert "default" in msg

    # Confirm no LLM call ever happened.
    assert capable.calls == 0
    assert toolless.calls == 0
