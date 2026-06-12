#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""VllmThinkingBackend raw-JSON retry: truncation is handled distinctly from
malformation. A ``finish_reason == 'length'`` grows the output budget and
re-issues the original request; a parse failure with a complete response
nudges the model with a corrective turn."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from pipeline.errors import MalformedModelOutputError
from pipeline.model_backends import (
    _RETRY_MAX_TOKENS_GROWTH,
    VllmThinkingBackend,
)


class _Empty(BaseModel):
    pass


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str, finish_reason: str | None) -> None:
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content: str, finish_reason: str | None) -> None:
        self.choices = [_Choice(content, finish_reason)]


def _fake_openai(monkeypatch, responses: list[tuple[str, str | None]]) -> list[dict]:
    """Patch openai.AsyncOpenAI to replay (content, finish_reason) pairs.

    Returns the list that accumulates each create() call's kwargs.
    """
    call_kwargs: list[dict] = []
    response_iter = iter(responses)

    async def _create(**kwargs: Any) -> _Resp:
        call_kwargs.append(kwargs)
        content, reason = next(response_iter)
        return _Resp(content, reason)

    class _Completions:
        create = staticmethod(_create)

    class _Chat:
        completions = _Completions()

    class _FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.chat = _Chat()

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeOpenAI)
    return call_kwargs


def _backend(context_window: int = 131072) -> VllmThinkingBackend:
    return VllmThinkingBackend(
        base_url="http://x", api_key="y", model="z", stream=False,
        max_context_window=context_window,
    )


def test_truncation_grows_max_tokens_and_skips_nudge(monkeypatch):
    # First response is a truncated object (finish_reason=length); second is
    # clean. The retry must (a) grow max_tokens and (b) NOT append a corrective
    # user turn, since the cause was length, not comprehension.
    truncated = '{"a": "unterminated'
    call_kwargs = _fake_openai(monkeypatch, [(truncated, "length"), ("{}", "stop")])
    backend = _backend()

    result = asyncio.run(backend.run("sys", "user", _Empty, max_tokens=8192))
    assert isinstance(result, _Empty)
    assert len(call_kwargs) == 2

    # Budget grew on the retry.
    assert call_kwargs[0]["max_tokens"] == 8192
    assert call_kwargs[1]["max_tokens"] == int(8192 * _RETRY_MAX_TOKENS_GROWTH)

    # No corrective turn: message list is unchanged (system + user only), so
    # the retry's context did not balloon with the truncated output.
    assert len(call_kwargs[1]["messages"]) == len(call_kwargs[0]["messages"]) == 2


def test_malformation_nudges_and_holds_budget(monkeypatch):
    # A complete-but-invalid response (finish_reason=stop) keeps the old
    # behavior: hold max_tokens steady and append the corrective turn.
    call_kwargs = _fake_openai(monkeypatch, [("not json", "stop"), ("{}", "stop")])
    backend = _backend()

    result = asyncio.run(backend.run("sys", "user", _Empty, max_tokens=8192))
    assert isinstance(result, _Empty)
    assert len(call_kwargs) == 2

    # Budget unchanged.
    assert call_kwargs[0]["max_tokens"] == call_kwargs[1]["max_tokens"] == 8192

    # Corrective turn appended: assistant echo + user nudge.
    assert len(call_kwargs[1]["messages"]) == 4
    assert call_kwargs[1]["messages"][-1]["role"] == "user"
    assert "valid JSON" in call_kwargs[1]["messages"][-1]["content"]


def test_truncation_growth_capped_at_context_window(monkeypatch):
    # Growth must not exceed the model's context window.
    call_kwargs = _fake_openai(monkeypatch, [("{", "length"), ("{}", "stop")])
    backend = _backend(context_window=9000)

    asyncio.run(backend.run("sys", "user", _Empty, max_tokens=8192))
    # 8192 * 1.5 = 12288, capped to 9000.
    assert call_kwargs[1]["max_tokens"] == 9000


def test_persistent_truncation_raises(monkeypatch):
    # Both attempts truncate: the call ultimately fails (no infinite growth).
    call_kwargs = _fake_openai(monkeypatch, [("{", "length"), ("{", "length")])
    backend = _backend()

    with pytest.raises(MalformedModelOutputError):
        asyncio.run(backend.run("sys", "user", _Empty, max_tokens=8192))
    assert len(call_kwargs) == 2
    assert call_kwargs[1]["max_tokens"] == int(8192 * _RETRY_MAX_TOKENS_GROWTH)
