#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Model-family-specific LLM backends.

Each ``ModelBackend`` subclass knows how to talk to one specific model
family on one specific infrastructure. It encapsulates all mechanical
concerns: HTTP calls, tokenizer quirks, think-block format, BPE
cleanup, tool-call parsing, structured output strategy, and retry.

The backend is named after what it is (``VllmThinkingBackend``,
``Llama3Backend``) so the code is honest about which workarounds are
active. When a bug is fixed upstream, a new backend class replaces the
old one; the old stays in the codebase for anyone still on the
affected version.

See ``MODELS.md`` at repo root for the workaround inventory with
upstream issue links and retire-when conditions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar, TypeVar

import openai

from pydantic import BaseModel, ValidationError

from pipeline.errors import MalformedModelOutputError, ModelBackendConfigError

logger = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Helpers (module-private, used by backend implementations)
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)

_BPE_ARTIFACTS = str.maketrans({"\u0120": " ", "\u010a": "\n"})

DEFAULT_REQUEST_LIMIT = 500
"""Default cap on pydantic-ai's UsageLimits.request_limit.

This counts every model request, tool call, and output-validator retry
issued inside one Agent.run(). 500 is high enough for tool-free synthesis
under retries=3 (effective ceiling ~4 model calls), and low enough that a
runaway tool loop terminates in single-digit minutes at typical hosted-LLM
latency. Lower it per-step via StepHooks.request_limit when the tool-call
shape is known.
"""


def _clean_bpe(text: str) -> str:
    """Replace leaked BPE token markers with the characters they represent.

    Root cause: HF Transformers v5 regression (#45920, #45488).
    ``tokenizer_class: LlamaTokenizerFast`` installs a Metaspace
    pre-tokenizer over ByteLevel BPE, causing U+0120 (space) and
    U+010A (newline) markers to leak through to decoded text.

    Handles both raw Unicode and JSON-escaped forms.
    """
    text = text.translate(_BPE_ARTIFACTS)
    text = text.replace("\\u0120", " ").replace("\\u010a", "\n")
    text = text.replace("\\u010A", "\n")
    return text


def _strip_think_block(text: str) -> tuple[str, str]:
    """Return ``(reasoning, content)``. Reasoning is empty if no think block."""
    text = _clean_bpe(text)
    match = _THINK_RE.search(text)
    if not match:
        return "", text.strip()
    reasoning = match.group(1).strip()
    content = text[match.end():].strip()
    return reasoning, content


def _extract_json(text: str) -> str:
    """Find and return the JSON object from model output text."""
    text = _clean_bpe(text)
    text = _FENCE_RE.sub("", text)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    start = text.find("{")
    if start < 0:
        raise MalformedModelOutputError(
            f"No JSON object found in model response: {text[:200]!r}"
        )
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _schema_instruction(output_type: type[BaseModel]) -> str:
    """Render a JSON-schema block to append to the system prompt."""
    schema = json.dumps(output_type.model_json_schema(), indent=2)
    return (
        "Return your answer as a single JSON object matching this schema exactly.\n"
        "Do not wrap it in markdown code fences. Do not add commentary outside the JSON.\n\n"
        f"Schema:\n{schema}"
    )


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class ModelBackend(ABC):
    """Knows how to talk to one specific model on one specific infrastructure.

    Subclasses are named after the model family they handle. Each
    encapsulates all mechanical concerns so pipeline code never
    touches tokenizer quirks, think-block parsing, or provider-specific
    API extensions.
    """

    thinking_capable: ClassVar[bool] = False
    tools_capable: ClassVar[bool] = False

    required_api_key_env: ClassVar[str | None] = None
    """Env var the loader must see declared on the service entry.

    Set on subclasses whose SDK reads its credential directly from the
    environment (and therefore ignores the ``api_key`` kwarg). When
    non-None, ``load_services`` rejects ``[services.NAME]`` entries
    whose ``api_key_env`` does not match this value, so the loader and
    the SDK cannot drift apart on which variable the user must export.
    Leave as ``None`` for backends that accept the ``api_key`` kwarg.
    """

    _max_context_window: int = 0
    _chars_per_token: float = 0
    _token_multiplier: float = 0

    @property
    def max_context_window(self) -> int:
        """Total context window capacity (input + output tokens)."""
        return self._max_context_window

    @property
    def chars_per_token(self) -> float:
        """Characters per token for this model's tokenizer.

        Used by pipeline.tokens.est_tokens() and tokens_to_chars() when
        an agent is available. Falls back to pipeline.tokens.CHARS_PER_TOKEN
        if not set on this backend.
        """
        if self._chars_per_token > 0:
            return self._chars_per_token
        from pipeline.tokens import CHARS_PER_TOKEN
        return CHARS_PER_TOKEN

    @property
    def token_multiplier(self) -> float:
        """Words-to-tokens multiplier used by batching."""
        return self._token_multiplier if self._token_multiplier > 0 else 1.3

    @abstractmethod
    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        max_tokens: int = 16384,
        tools: dict[str, Callable] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> _T:
        """Send a prompt and return validated structured output.

        ``max_tokens`` caps the output token count for this call.
        Pipelines set this per-agent via ``AgentBackend``; backends
        forward it to the API layer.

        ``request_limit`` caps the total model requests (model calls,
        tool calls, output-validator retries) issued in this call.
        Backends without an agentic loop bound their internal retry
        budget by this value.
        """
        ...


# ---------------------------------------------------------------------------
# Thinking models on vLLM (with workarounds)
# ---------------------------------------------------------------------------


class VllmThinkingBackend(ModelBackend):
    """Thinking-capable models served by vLLM (DeepSeek-R1-Distill-Llama,
    Qwen3 with reasoning enabled, etc.).

    Workarounds (see MODELS.md for upstream issue links):
    - BPE U+0120/U+010A cleanup (HF Transformers v5 regression #45920)
    - <think> block stripping (vLLM reasoning parser doesn't support
      tool calling; unified parser RFC #32713 in progress)
    - Schema-in-prompt structured output (tool calling broken #28219)
    - Raw JSON extraction with retry

    Retire when: vLLM unified parser ships AND HF Transformers fixes
    the AutoTokenizer dispatch.
    """

    thinking_capable = True

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 max_context_window: int = 131072,
                 chars_per_token: float = 0,
                 token_multiplier: float = 0,
                 stream: bool = True,
                 tools_capable: bool = False, **kwargs: Any) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._max_context_window = max_context_window
        self._chars_per_token = chars_per_token
        self._token_multiplier = token_multiplier
        self._stream = stream
        self.tools_capable = tools_capable

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        max_tokens: int = 16384,
        tools: dict[str, Callable] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> _T:
        from openai import AsyncOpenAI

        if tools:
            return await self._run_with_tools(
                system_prompt, user_message, output_type,
                tools=tools, max_tokens=max_tokens,
                thinking_budget=thinking_budget,
                label=label, debug_log=debug_log,
                request_limit=request_limit,
            )
        if request_limit < 1:
            raise ModelBackendConfigError(
                f"request_limit must be >= 1, got {request_limit}"
            )

        client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)

        schema_block = _schema_instruction(output_type)
        full_system = f"{system_prompt}\n\n{schema_block}"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user_message},
        ]

        extra: dict[str, Any] = {}
        if thinking_budget is not None:
            if thinking_budget == 0:
                extra["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
            else:
                extra["extra_body"] = {"thinking_token_budget": thinking_budget}

        max_attempts = min(2, request_limit)
        for attempt in range(max_attempts):
            effective_max = max_tokens

            try:
                if self._stream:
                    stream = await client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        temperature=0.0,
                        top_p=1.0,
                        seed=0,
                        max_tokens=effective_max,
                        stream=True,
                        **extra,
                    )
                    parts: list[str] = []
                    async for chunk in stream:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            parts.append(delta)
                    raw_content = "".join(parts)
                else:
                    response = await client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        temperature=0.0,
                        top_p=1.0,
                        seed=0,
                        max_tokens=effective_max,
                        **extra,
                    )
                    raw_content = response.choices[0].message.content or ""
            except (openai.NotFoundError, openai.APIConnectionError,
                    openai.InternalServerError, openai.APITimeoutError) as exc:
                if attempt < max_attempts - 1:
                    delay = 2 ** (attempt + 1)
                    logger.warning(
                        "Transient API error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, max_attempts, delay, exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            reasoning, content = _strip_think_block(raw_content)

            if debug_log is not None:
                parts = [f"<!-- {label or 'raw-json'} -->"]
                if reasoning:
                    parts.append(f"<!-- reasoning -->\n{reasoning}")
                parts.append(f"<!-- raw-json -->\n{content}")
                debug_log.append("\n".join(parts) + "\n")

            try:
                json_str = _extract_json(content)
                parsed = json.loads(json_str)
                result = output_type.model_validate(parsed)
                if debug_log is not None:
                    debug_log.append(
                        f"<!-- output -->\n"
                        f"{json.dumps(result.model_dump(), indent=2, ensure_ascii=False)}\n"
                    )
                return result
            except (
                MalformedModelOutputError,
                json.JSONDecodeError,
                ValidationError,
            ) as exc:
                if attempt < max_attempts - 1:
                    logger.warning(
                        "Raw JSON parse failed (attempt %d), retrying: %s",
                        attempt + 1, exc,
                    )
                    messages.append({"role": "assistant", "content": raw_content})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous output was not valid JSON matching the schema. "
                            f"Error: {exc}\nPlease output only the JSON object, no commentary."
                        ),
                    })
                    continue
                raise MalformedModelOutputError(
                    f"Raw JSON completion failed after {attempt + 1} attempt(s): {exc}\n"
                    f"Content: {content[:500]!r}"
                ) from exc

        raise AssertionError(
            f"unreachable: loop must return or raise (max_attempts={max_attempts})"
        )

    async def _run_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        tools: dict[str, Callable],
        max_tokens: int = 16384,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> _T:
        """Tool-calling path via pydantic-ai Agent.

        Uses the OpenAI-compatible tool calling API exposed by vLLM
        with ``--tool-call-parser gemma4``. The non-tool path stays
        on raw completions with schema-in-prompt.
        """
        from pydantic_ai import Agent
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.settings import ModelSettings
        from pydantic_ai.usage import UsageLimits
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
        provider = OpenAIProvider(openai_client=client)
        model = OpenAIChatModel(self._model, provider=provider)

        settings = ModelSettings(
            temperature=0.0,
            top_p=1.0,
            seed=0,
            parallel_tool_calls=False,
            max_tokens=max_tokens,
        )

        agent: Agent[None, _T] = Agent(
            model=model,
            output_type=output_type,
            system_prompt=system_prompt,
            retries=3,
            model_settings=settings,
        )

        for name, fn in tools.items():
            agent.tool_plain(fn)

        try:
            result = await agent.run(
                user_message,
                usage_limits=UsageLimits(request_limit=request_limit),
            )
        except UsageLimitExceeded as exc:
            exc.add_note(f"request_limit={request_limit}")
            raise

        if debug_log is not None:
            from pipeline.tasks import render_debug_md
            debug_log.append(render_debug_md(result, label))

        return result.output


# ---------------------------------------------------------------------------
# Llama 3.x family (3.1, 3.3) on vLLM or Fireworks
# ---------------------------------------------------------------------------


class Llama3Backend(ModelBackend):
    """Llama 3.x family on vLLM or Fireworks.

    Uses pydantic-ai Agent with ModelProfile for JSON-schema structured
    output. Tool calling via llama3_json parser when tools are provided.
    Clean tokenizer (no BPE artifacts).

    Known issues:
    - pydantic-ai + vLLM tool calling can produce infinite loops
      (pydantic-ai #1414) and $defs schema failures (vLLM #15035).
      Prefer JSON-schema output for tool-free steps.
    """

    thinking_capable = False
    tools_capable = True

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 max_context_window: int = 131072,
                 chars_per_token: float = 0,
                 token_multiplier: float = 0, **kwargs: Any) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model_name = model
        self._max_context_window = max_context_window
        self._chars_per_token = chars_per_token
        self._token_multiplier = token_multiplier

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        max_tokens: int = 16384,
        tools: dict[str, Callable] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> _T:
        from pydantic_ai import Agent
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.settings import ModelSettings
        from pydantic_ai.usage import UsageLimits
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
        provider = OpenAIProvider(openai_client=client)
        model = OpenAIChatModel(self._model_name, provider=provider)

        settings = ModelSettings(
            temperature=0.0,
            top_p=1.0,
            seed=0,
            parallel_tool_calls=False,
            max_tokens=max_tokens,
        )

        agent: Agent[None, _T] = Agent(
            model=model,
            output_type=output_type,
            system_prompt=system_prompt,
            retries=3,
            model_settings=settings,
        )

        if tools:
            for name, fn in tools.items():
                agent.tool_plain(fn)

        try:
            result = await agent.run(
                user_message,
                usage_limits=UsageLimits(request_limit=request_limit),
            )
        except UsageLimitExceeded as exc:
            exc.add_note(f"request_limit={request_limit}")
            raise

        if debug_log is not None:
            from pipeline.tasks import render_debug_md
            debug_log.append(render_debug_md(result, label))

        return result.output


# ---------------------------------------------------------------------------
# Qwen3 family on vLLM or Fireworks
# ---------------------------------------------------------------------------


class Qwen3Backend(ModelBackend):
    """Qwen3 family (32B, 235B-A22B) on vLLM or Fireworks.

    Workarounds:
    - Constrained-decoding instability (vLLM #39677): Qwen3 emits
      newlines after </think> and structured constraints activate
      too early. Schemas with fewer than 5 fields may need dummy
      fields (unused1, unused2) to anchor generation.
    - Hybrid thinking toggle via enable_thinking chat template kwarg.
    - hermes tool-call parser expected on vLLM server side.
    """

    thinking_capable = True
    tools_capable = True

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 max_context_window: int = 131072,
                 chars_per_token: float = 0,
                 token_multiplier: float = 0, **kwargs: Any) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model_name = model
        self._max_context_window = max_context_window
        self._chars_per_token = chars_per_token
        self._token_multiplier = token_multiplier

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        max_tokens: int = 16384,
        tools: dict[str, Callable] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> _T:
        from pydantic_ai import Agent
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.settings import ModelSettings
        from pydantic_ai.usage import UsageLimits
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
        provider = OpenAIProvider(openai_client=client)
        model = OpenAIChatModel(self._model_name, provider=provider)

        extra: dict[str, Any] = {"top_k": 1}

        settings = ModelSettings(
            temperature=0.0,
            top_p=1.0,
            seed=0,
            parallel_tool_calls=False,
            max_tokens=max_tokens,
            extra_body=extra,
        )

        agent: Agent[None, _T] = Agent(
            model=model,
            output_type=output_type,
            system_prompt=system_prompt,
            retries=3,
            model_settings=settings,
        )

        if tools:
            for name, fn in tools.items():
                agent.tool_plain(fn)

        try:
            result = await agent.run(
                user_message,
                usage_limits=UsageLimits(request_limit=request_limit),
            )
        except UsageLimitExceeded as exc:
            exc.add_note(f"request_limit={request_limit}")
            raise

        if debug_log is not None:
            from pipeline.tasks import render_debug_md
            debug_log.append(render_debug_md(result, label))

        return result.output


# ---------------------------------------------------------------------------
# Anthropic Claude family
# ---------------------------------------------------------------------------


class AnthropicBackend(ModelBackend):
    """Anthropic Claude family via native API.

    Uses pydantic-ai Agent with native Anthropic provider. No
    workarounds needed; tool calling and structured output work
    natively. parallel_tool_calls=False maps to
    disable_parallel_tool_use=True on Anthropic.
    """

    thinking_capable = False
    tools_capable = True
    required_api_key_env = "ANTHROPIC_API_KEY"

    def __init__(self, *, model: str, max_context_window: int = 200000,
                 chars_per_token: float = 0,
                 token_multiplier: float = 0, **kwargs: Any) -> None:
        self._model_name = model
        self._max_context_window = max_context_window
        self._chars_per_token = chars_per_token
        self._token_multiplier = token_multiplier

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        max_tokens: int = 16384,
        tools: dict[str, Callable] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> _T:
        from pydantic_ai import Agent
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.settings import ModelSettings
        from pydantic_ai.usage import UsageLimits

        settings = ModelSettings(
            temperature=0.0,
            parallel_tool_calls=False,
            max_tokens=max_tokens,
        )

        agent: Agent[None, _T] = Agent(
            model=self._model_name,
            output_type=output_type,
            system_prompt=system_prompt,
            retries=3,
            model_settings=settings,
        )

        if tools:
            for name, fn in tools.items():
                agent.tool_plain(fn)

        try:
            result = await agent.run(
                user_message,
                usage_limits=UsageLimits(request_limit=request_limit),
            )
        except UsageLimitExceeded as exc:
            exc.add_note(f"request_limit={request_limit}")
            raise

        if debug_log is not None:
            from pipeline.tasks import render_debug_md
            debug_log.append(render_debug_md(result, label))

        return result.output


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BACKEND_REGISTRY: dict[str, type[ModelBackend]] = {
    "vllm_thinking": VllmThinkingBackend,
    "llama3": Llama3Backend,
    "qwen3": Qwen3Backend,
    "anthropic": AnthropicBackend,
}
