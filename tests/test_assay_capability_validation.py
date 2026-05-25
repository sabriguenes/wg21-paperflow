#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Cross-package integration tests for capability validation.

Drives through the public ``assay.pipeline.assay_paper`` entry point
and through ``pipeline.build_pipeline`` + ``pipeline.validate_capabilities``
directly to verify that:

- Misbound slots fail before any LLM call.
- ``stop_after`` correctly scopes which steps are checked.
- ``validate_capabilities`` and ``dispatch`` agree on what ``stop_after``
  means.
- ``assay_paper`` fails fast end-to-end when SERVICES.toml resolves to a
  toolless tool slot.

Ports the dissect-era ``test_dissect_capability_validation.py`` to the
assay pipeline. The dissect→assay rename had silently dropped the
``validate_capabilities`` call from ``assay_paper``; this test exists in
tandem with restoring that call so the documented invariant
(``packages/pipeline/src/pipeline/CLAUDE.md``: "called by each pipeline's
entry function right after ``build_pipeline``") stays enforced.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from assay import pipeline as assay_pipeline_mod
from pipeline import (
    AgentBackend,
    CapabilityMismatchError,
    StepContext,
    build_pipeline,
    dispatch,
    load_sections,
    validate_capabilities,
)
from pipeline.model_backends import DEFAULT_REQUEST_LIMIT, ModelBackend
from pipeline.prompt import StepHooks, StepMeta, StepSpec
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


def _assay_sections() -> dict[str, str]:
    return dict(load_sections("assay", "assay.md"))


def _step_numbers_in(msg: str) -> set[int]:
    nums: set[int] = set()
    for line in msg.splitlines():
        s = line.lstrip()
        if s and s.split()[0].isdigit():
            nums.add(int(s.split()[0]))
    return nums


def _assay_hooks_with_bad_tool_slot(
    tool_service: str, tool_backend: ModelBackend,
) -> dict[str, StepHooks]:
    """Build the assay hook table with a deliberately misbound tool slot.

    Mirrors ``assay.pipeline._build_hooks`` step-for-step but with the
    research agent forced onto ``tool_backend`` (which is toolless in
    the negative cases). Fast and default agents stay tools-capable so
    only the tool slot's mismatch surfaces in the rendered table.
    """
    capable = _ToolsCapableStub()
    extraction_agent = AgentBackend(
        capable, slot_name="fast", service_name="svc-fast",
    )
    synthesis_agent = AgentBackend(
        capable, slot_name="default", service_name="svc-default",
    )
    research_agent = AgentBackend(
        tool_backend, slot_name="tool", service_name=tool_service,
    )
    return assay_pipeline_mod._build_hooks(
        extraction_agent, synthesis_agent, research_agent,
    )


def test_assay_pipeline_rejects_toolless_tool_slot():
    toolless = _ToollessStub()
    hooks = _assay_hooks_with_bad_tool_slot("b200-r1", toolless)
    pipeline = build_pipeline(_assay_sections(), hooks)

    with pytest.raises(CapabilityMismatchError) as exc_info:
        validate_capabilities(pipeline)
    msg = str(exc_info.value)
    nums = _step_numbers_in(msg)
    # Step 8 (Research) is the only step that declares Tools in assay.md;
    # it is the only one that should appear in the mismatch table.
    assert 8 in nums
    assert "tool" in msg
    assert "b200-r1" in msg
    assert "documentation-only" in msg
    # Fast / default slots are tools-capable and must not appear.
    assert 4 not in nums
    assert 5 not in nums
    assert 7 not in nums


def test_assay_pipeline_with_stop_after_skips_out_of_scope_failures():
    toolless = _ToollessStub()
    hooks = _assay_hooks_with_bad_tool_slot("b200-r1", toolless)
    pipeline = build_pipeline(_assay_sections(), hooks)

    # Step 8 is out of scope when stop_after=5; dispatch will never
    # reach the misbound tool slot, so validate must not punish the
    # user for it.
    validate_capabilities(pipeline, stop_after=5)  # must not raise


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
        meta=StepMeta(
            name=name or f"{number}. Step {number}",
            number=number,
            model_slot="default",
            execution="main",
            tools=["web_fetch"] if bad else [],
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

    state: dict[str, set[int]] = {}
    ctx = StepContext(sections={})

    asyncio.run(dispatch(specs, state, ctx, stop_after=stop_after))
    visited = state.get("visited", set())

    bad_in_scope = 3 in visited
    if bad_in_scope:
        with pytest.raises(CapabilityMismatchError):
            validate_capabilities(specs, stop_after=stop_after)
    else:
        validate_capabilities(specs, stop_after=stop_after)


def test_assay_paper_fails_fast_with_bad_service_binding(monkeypatch):
    # Drive assay_paper far enough to reach validate_capabilities but
    # no further. Patches happen at assay.pipeline.* (the import alias
    # in the module under test) so a future import-form refactor would
    # surface here instead of silently neutering the patch.
    capable = _ToolsCapableStub()
    toolless = _ToollessStub()

    services = {"good": capable, "bad": toolless}
    # assay.md's ## Services section declares fast/default/tool/frontier;
    # all four slots must resolve or resolve_slots raises KeyError.
    defaults = {
        "fast": "good", "default": "good", "tool": "bad", "frontier": "good",
    }
    # Empty api_key_envs opts out of resolve_slots's env-var validation;
    # the stubs do not represent real authenticated services.
    registry = ServiceRegistry(
        services=services, defaults=defaults, api_key_envs={},
    )

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

    # assay.md's ## Services section pins slots to real backend names
    # via parse_service_overrides; service_overrides wins (it is
    # spread last into the merged dict in assay_paper), so we route
    # the slots onto our stub registry without patching the parser.
    async def _go() -> Any:
        return await assay_pipeline_mod.assay_paper(
            "P0000R0",
            _BackendShouldNotBeUsed(),
            service_overrides={
                "fast": "good", "default": "good",
                "tool": "bad", "frontier": "good",
            },
        )

    with pytest.raises(CapabilityMismatchError) as exc_info:
        asyncio.run(_go())
    msg = str(exc_info.value)
    assert 8 in _step_numbers_in(msg)
    assert "tool" in msg
    assert "bad" in msg

    # Confirm no LLM call ever happened.
    assert capable.calls == 0
    assert toolless.calls == 0
