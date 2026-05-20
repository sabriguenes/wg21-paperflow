#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Cross-package integration tests for capability validation.

Drives through the public `dissect.pipeline.build_dissect_pipeline` and
verifies that:

- Misbound slots fail before any LLM call.
- `stop_after` correctly scopes which steps are checked.
- `validate_capabilities` and `dispatch` agree on what `stop_after` means.
- `dissect_paper` fails fast end-to-end when SERVICES.toml resolves to a
  toolless tool slot.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pipeline import (
    AgentBackend,
    CapabilityMismatchError,
    StepContext,
    dispatch,
    load_sections,
    validate_capabilities,
)
from pipeline.model_backends import DEFAULT_REQUEST_LIMIT, ModelBackend
from pipeline.prompt import StepHooks, StepMeta, StepSpec

from dissect.pipeline import build_dissect_pipeline


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
        tools: dict[str, Any] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> Any:
        self.calls += 1
        return output_type()


def _dissect_sections() -> dict[str, str]:
    return dict(load_sections("dissect", "dissect.md"))


def _step_numbers_in(msg: str) -> set[int]:
    nums: set[int] = set()
    for line in msg.splitlines():
        s = line.lstrip()
        if s and s.split()[0].isdigit():
            nums.add(int(s.split()[0]))
    return nums


def test_build_dissect_pipeline_rejects_toolless_tool_slot():
    capable = _ToolsCapableStub()
    toolless = _ToollessStub()
    slot_bindings = {
        "fast": ("svc-fast", capable),
        "default": ("svc-default", capable),
        "tool": ("b200-r1", toolless),
    }
    with pytest.raises(CapabilityMismatchError) as exc_info:
        build_dissect_pipeline(_dissect_sections(), slot_bindings, {})
    msg = str(exc_info.value)
    nums = _step_numbers_in(msg)
    assert 11 in nums
    assert 12 in nums
    assert "tool" in msg
    assert "b200-r1" in msg
    assert "documentation-only" in msg
    # The bad slot is the tool slot only; tools-capable agents on fast
    # and default with thinking_budget should not appear in the
    # mismatch listing.
    assert 2 not in nums
    assert 9 not in nums


def test_build_dissect_pipeline_with_stop_after_skips_out_of_scope_failures():
    capable = _ToolsCapableStub()
    toolless = _ToollessStub()
    slot_bindings = {
        "fast": ("svc-fast", capable),
        "default": ("svc-default", capable),
        "tool": ("b200-r1", toolless),
    }
    # Steps 11 and 12 are out of scope when stop_after=5; the misbound
    # tool slot is never reached by dispatch, so validate must not
    # punish the user for it.
    pipeline, _ = build_dissect_pipeline(
        _dissect_sections(), slot_bindings, {}, stop_after=5,
    )
    assert len(pipeline) >= 6  # sanity: pipeline survived construction


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

    async def _noop(state: Any, ctx: StepContext) -> None:
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


def test_dissect_paper_fails_fast_with_bad_service_binding(monkeypatch, tmp_path):
    # Drive dissect_paper far enough to reach build_dissect_pipeline
    # but no further. Patches happen at dissect.pipeline.* (the import
    # alias in the module under test) so a future import-form refactor
    # would surface here instead of silently neutering the patch.
    from dissect import pipeline as dissect_pipeline_mod
    from pipeline.services import ServiceRegistry

    capable = _ToolsCapableStub()
    toolless = _ToollessStub()

    services = {"good": capable, "bad": toolless}
    defaults = {"fast": "good", "default": "good", "tool": "bad"}
    # Empty api_key_envs opts out of resolve_slots's env-var validation;
    # the stubs do not represent real authenticated services.
    registry = ServiceRegistry(
        services=services, defaults=defaults, api_key_envs={},
    )

    monkeypatch.setattr(
        dissect_pipeline_mod,
        "load_services",
        lambda: registry,
    )

    # The transformer/classifier path runs before build_dissect_pipeline;
    # neutralize it so the test does not need real models. These names
    # are imported lazily inside dissect_paper from `pipeline`, so the
    # patch site is the `pipeline` module itself.
    import pipeline as pipeline_pkg

    class _StubProvider:
        name = "stub"
        device = "cpu"
        dtype = "fp32"
        batch_size = 1

    monkeypatch.setattr(
        pipeline_pkg,
        "load_transformer_providers",
        lambda: ({"auto": _StubProvider()}, {}),
    )
    monkeypatch.setattr(
        pipeline_pkg,
        "resolve_transformer_provider",
        lambda providers, defaults_, *, override=None: providers["auto"],
    )
    monkeypatch.setattr(
        pipeline_pkg,
        "load_classifiers",
        lambda provider=None: ({}, {}),
    )
    monkeypatch.setattr(
        pipeline_pkg,
        "resolve_classifier_slots",
        lambda classifiers, defaults_, overrides=None: {},
    )

    class _BackendShouldNotBeUsed:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(
                f"backend.{name} called -- capability validation should "
                f"fail before any backend access"
            )

    async def _go() -> Any:
        return await dissect_pipeline_mod.dissect_paper(
            "P0000R0", _BackendShouldNotBeUsed(),
        )

    with pytest.raises(CapabilityMismatchError) as exc_info:
        asyncio.run(_go())
    msg = str(exc_info.value)
    assert 11 in _step_numbers_in(msg)
    assert "tool" in msg
    assert "bad" in msg

    # Confirm no LLM call ever happened.
    assert capable.calls == 0
    assert toolless.calls == 0
