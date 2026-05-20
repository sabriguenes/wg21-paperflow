#
# Copyright (c) 2026 D Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest
from pydantic import BaseModel

from pipeline import StepContext
from pipeline.agents import AgentBackend
from pipeline.model_backends import (
    DEFAULT_REQUEST_LIMIT,
    AnthropicBackend,
    VllmThinkingBackend,
    Llama3Backend,
    ModelBackend,
    Qwen3Backend,
)
from pipeline.prompt import StepHooks, StepMeta, StepSpec
from pipeline.runner import dispatch


class _Empty(BaseModel):
    pass


class _RecordingBackend(ModelBackend):
    """Captures request_limit at the ModelBackend boundary."""

    thinking_capable = False
    tools_capable = True

    def __init__(self) -> None:
        self.calls: list[int] = []

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
        self.calls.append(request_limit)
        return output_type()


def _spec(
    *,
    agent: AgentBackend,
    prepare: Any,
    extract: Any = None,
    request_limit: int | None = None,
    parallel: bool = False,
) -> StepSpec:
    return StepSpec(
        meta=StepMeta(
            name="1. Test",
            number=1,
            model_slot="default",
            execution="main",
        ),
        hooks=StepHooks(
            output_type=_Empty,
            agent=agent,
            prepare=prepare,
            extract=extract,
            parallel=parallel,
            request_limit=request_limit,
        ),
    )


def _ctx() -> StepContext:
    return StepContext(sections={})


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_step_hooks_request_limit_reaches_backend():
    backend = _RecordingBackend()
    spec = _spec(
        agent=AgentBackend(backend),
        prepare=lambda s, c: "msg",
        extract=lambda s, o: None,
        request_limit=15,
    )
    _run(dispatch([spec], object(), _ctx()))
    assert backend.calls == [15]


def test_default_applied_when_hook_unset():
    backend = _RecordingBackend()
    spec = _spec(
        agent=AgentBackend(backend),
        prepare=lambda s, c: "msg",
        extract=lambda s, o: None,
        request_limit=None,
    )
    _run(dispatch([spec], object(), _ctx()))
    assert backend.calls == [DEFAULT_REQUEST_LIMIT]


def test_parallel_step_propagates_request_limit_to_each_call():
    backend = _RecordingBackend()
    spec = _spec(
        agent=AgentBackend(backend),
        prepare=lambda s, c: ["a", "b", "c"],
        extract=lambda s, results: None,
        request_limit=4,
        parallel=True,
    )
    _run(dispatch([spec], object(), _ctx()))
    assert backend.calls == [4, 4, 4]


def test_parallel_step_with_chunk_index_propagates_request_limit():
    backend = _RecordingBackend()
    spec = _spec(
        agent=AgentBackend(backend),
        prepare=lambda s, c: ["a", "b", "c"],
        extract=lambda s, results: None,
        request_limit=4,
        parallel=True,
    )
    _run(dispatch([spec], object(), _ctx(), chunk_index=1))
    assert backend.calls == [4]


@pytest.mark.parametrize(
    "backend_cls",
    [
        Llama3Backend,
        Qwen3Backend,
        AnthropicBackend,
        VllmThinkingBackend,
    ],
)
def test_concrete_backend_signatures_use_named_default(backend_cls):
    # Value equality, not identity: 500 sits outside CPython's small-int
    # intern range, so `is` would be implementation-dependent.
    sig = inspect.signature(backend_cls.run)
    assert sig.parameters["request_limit"].default == DEFAULT_REQUEST_LIMIT


@pytest.mark.parametrize("hook_value", [0, -1])
def test_zero_and_negative_request_limit_pass_through_dispatch(hook_value):
    # Dispatch resolves None → DEFAULT_REQUEST_LIMIT only. Any other int,
    # including degenerate values, propagates verbatim so the backend's
    # entry guard is the single source of truth.
    backend = _RecordingBackend()
    spec = _spec(
        agent=AgentBackend(backend),
        prepare=lambda s, c: "msg",
        extract=lambda s, o: None,
        request_limit=hook_value,
    )
    _run(dispatch([spec], object(), _ctx()))
    assert backend.calls == [hook_value]


@pytest.mark.parametrize(
    "request_limit,responses,expected_calls,expects_raise",
    [
        (2, ["not json", "{}"], 2, None),
        (2, ["not json", "not json"], 2, ValueError),
        (1, ["not json"], 1, ValueError),
        (DEFAULT_REQUEST_LIMIT, ["{}"], 1, None),
        (0, [], 0, ValueError),
    ],
    ids=[
        "retry-recovers",
        "retry-exhausted",
        "single-attempt-fails",
        "clean-default",
        "rejects-zero",
    ],
)
def test_deepseek_backend_honors_request_limit(
    request_limit, responses, expected_calls, expects_raise, monkeypatch,
):
    call_kwargs: list[dict] = []
    response_iter = iter(responses)

    class _Msg:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    async def _create(**kwargs):
        call_kwargs.append(kwargs)
        return _Resp(next(response_iter))

    class _Completions:
        create = staticmethod(_create)

    class _Chat:
        completions = _Completions()

    class _FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.chat = _Chat()

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeOpenAI)

    backend = VllmThinkingBackend(
        base_url="http://x", api_key="y", model="z",
    )

    async def _go() -> Any:
        return await backend.run(
            "sys", "user", _Empty, request_limit=request_limit,
        )

    if expects_raise is not None:
        with pytest.raises(expects_raise):
            _run(_go())
    else:
        result = _run(_go())
        assert isinstance(result, _Empty)

    assert len(call_kwargs) == expected_calls
    for kw in call_kwargs:
        assert "usage_limits" not in kw
