#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

from __future__ import annotations

import pytest

from pipeline import PipelinePrompt, StepContext
from pipeline.errors import StepError
from pipeline.prompt import StepHooks, StepPrompt, StepSpec
from pipeline.runner import _compose_system_prompt, dispatch


def _prompt(system_prompt: str = "") -> PipelinePrompt:
    return PipelinePrompt(
        package="test",
        filename="test.md",
        sections={},
        services={},
        config={},
        system_prompt=system_prompt,
        preamble="",
        steps=(),
    )


def _spec(
    *,
    system_prompt: str = "",
    system_prompt_mode: str = "append",
    custom=None,
) -> StepSpec:
    return StepSpec(
        step=StepPrompt(
            name="1. Test",
            number=1,
            model="default",
            execution="main",
            system_prompt=system_prompt,
            system_prompt_mode=system_prompt_mode,
        ),
        hooks=StepHooks(custom=custom),
    )


def test_step_context_classifiers_defaults_to_empty():
    """StepContext gains a classifiers slot parallel to agents."""
    ctx = StepContext()
    assert ctx.classifiers == {}


def test_step_context_classifiers_populated_from_orchestrator():
    """Smoke: pass a resolved classifier dict; it lands keyed by slot name."""
    sentinel = object()
    ctx = StepContext(classifiers={"selector": sentinel})
    assert ctx.classifiers["selector"] is sentinel


def test_compose_system_prompt_append():
    ctx = StepContext(prompt=_prompt(system_prompt="Pipeline role."), agents={})
    prompt = _compose_system_prompt(_spec(system_prompt="Step role."), ctx)

    assert "Pipeline role." in prompt
    assert "Step role." in prompt


def test_compose_system_prompt_replace():
    ctx = StepContext(prompt=_prompt(system_prompt="Pipeline role."), agents={})
    prompt = _compose_system_prompt(
        _spec(system_prompt="Step role.", system_prompt_mode="replace"),
        ctx,
    )

    assert "Pipeline role." not in prompt
    assert "Step role." in prompt


def test_step_failure_propagates_as_step_error():
    async def boom(state, ctx, spec):
        raise RuntimeError("boom")

    ctx = StepContext(agents={})

    with pytest.raises(StepError, match="Step 0"):
        import asyncio
        asyncio.run(dispatch([_spec(custom=boom)], object(), ctx))


def test_failed_step_does_not_call_on_step_complete():
    async def boom(state, ctx, spec):
        raise RuntimeError("boom")

    completed = []
    ctx = StepContext(agents={})

    with pytest.raises(StepError):
        import asyncio
        asyncio.run(
            dispatch(
                [_spec(custom=boom)],
                object(),
                ctx,
                on_step_complete=lambda spec, state: completed.append(spec),
            )
        )

    assert completed == []


def test_step_failure_flushes_trace(tmp_path):
    async def boom(state, ctx, spec):
        state["seen"] = True
        raise RuntimeError("boom")

    ctx = StepContext(agents={})
    trace_path = tmp_path / "trace.md"

    with pytest.raises(StepError):
        import asyncio
        asyncio.run(
            dispatch(
                [_spec(custom=boom)],
                {},
                ctx,
                trace_path=trace_path,
                render_trace_fn=lambda state, step: f"step={step} seen={state['seen']}",
            )
        )

    assert "seen=True" in trace_path.read_text(encoding="utf-8")
