#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pipeline-facing agent abstraction.

``AgentBackend`` is the interface that pipeline steps call. It wraps a
``ModelBackend`` (which knows how to talk to a specific model on
specific infrastructure) and adds pipeline-level configuration like
``thinking_budget``.

Capability validation has two layers:

1. Primary gate: ``pipeline.validate.validate_capabilities`` walks the
   built ``StepSpec`` list at pipeline-construction time and raises
   ``CapabilityMismatchError`` if any step's declared ``meta.tools``
   or assigned ``thinking_budget`` would land on a backend whose class
   attributes do not support it. Each ``AgentBackend`` carries
   ``slot_name`` and ``service_name`` so the error message can echo
   back exactly what the user typed.

2. Secondary defense at call time: ``run`` raises
   ``NotImplementedError`` when ``tools`` is non-empty but the
   underlying ``ModelBackend`` is ``tools_capable=False``. This covers
   custom hooks that build ad-hoc tool dicts inside ``run_task``
   without declaring them in ``meta.tools`` (the build-time walk does
   not see those). Do not delete this check.

Pipelines create named agents by intent::

    extraction_agent = AgentBackend(
        slots["fast"][1], thinking_budget=2048,
        slot_name="fast", service_name=slots["fast"][0],
    )
"""

from __future__ import annotations

from typing import Callable, TypeVar

from pydantic import BaseModel

from pipeline.model_backends import DEFAULT_REQUEST_LIMIT, ModelBackend

_T = TypeVar("_T", bound=BaseModel)


class AgentBackend:
    """Pipeline-facing agent. Wraps a ModelBackend with pipeline-level config."""

    def __init__(
        self,
        model: ModelBackend,
        *,
        max_tokens: int = 16384,
        thinking_budget: int | None = None,
        slot_name: str = "",
        service_name: str = "",
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._thinking_budget = thinking_budget
        self._slot_name = slot_name
        self._service_name = service_name

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def max_context_window(self) -> int:
        return self._model.max_context_window

    @property
    def chars_per_token(self) -> float:
        return self._model.chars_per_token

    @property
    def token_multiplier(self) -> float:
        """Deprecated. Use chars_per_token instead."""
        return self._model.token_multiplier

    @property
    def thinking_capable(self) -> bool:
        return self._model.thinking_capable

    @property
    def tools_capable(self) -> bool:
        return self._model.tools_capable

    @property
    def thinking_budget(self) -> int | None:
        return self._thinking_budget

    @property
    def slot_name(self) -> str:
        return self._slot_name

    @property
    def service_name(self) -> str:
        return self._service_name

    @property
    def backend_class_name(self) -> str:
        return type(self._model).__name__

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        tools: dict[str, Callable] | None = None,
        max_tokens: int | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> _T:
        """Send a prompt and return validated structured output.

        ``max_tokens`` overrides the agent's default output budget for
        this call. ``None`` (the default) uses the value set at
        construction time. Per-step overrides come from
        ``StepMeta.max_output_tokens`` via the runner.

        ``thinking_budget`` overrides the agent's default thinking
        token budget for this call. ``None`` (the default) uses the
        value set at construction time. Per-step overrides come from
        ``StepMeta.thinking_budget``.

        ``request_limit`` caps total model requests inside this call;
        forwarded to the underlying ``ModelBackend``.

        Raises ``NotImplementedError`` if ``tools`` is non-empty and
        the underlying ``ModelBackend`` declares ``tools_capable=False``.
        This is the secondary defense for ad-hoc tools that bypass
        ``meta.tools``; the primary gate is
        ``pipeline.validate.validate_capabilities`` at construction time.
        """
        if tools and not self._model.tools_capable:
            raise NotImplementedError(
                f"{type(self._model).__name__} does not support tools. "
                f"Assign a tools_capable service to this slot."
            )
        resolved_max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        resolved_thinking = thinking_budget if thinking_budget is not None else self._thinking_budget
        if debug_log is not None:
            model_name = getattr(self._model, "_model_name", "") or getattr(self._model, "_model", "")
            debug_log.append(
                f"<!-- call: {label or 'unlabeled'} | "
                f"service={self._service_name or 'unknown'} | "
                f"model={model_name} | "
                f"max_tokens={resolved_max_tokens} -->\n"
            )
        return await self._model.run(
            system_prompt,
            user_message,
            output_type,
            max_tokens=resolved_max_tokens,
            tools=tools,
            thinking_budget=resolved_thinking,
            label=label,
            debug_log=debug_log,
            request_limit=request_limit,
        )
