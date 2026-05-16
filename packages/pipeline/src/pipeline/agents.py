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
``thinking_budget``. It validates capability requirements at call
time: if a step passes ``tools`` to a backend that declares
``tools_capable=False``, the call fails fast with a clear error.

Pipelines create named agents by intent::

    extraction_agent = AgentBackend(services["fast"], thinking_budget=2048)
    synthesis_agent = AgentBackend(services["default"], thinking_budget=4096)
    research_agent = AgentBackend(services["tool"])
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from pipeline.model_backends import ModelBackend

_T = TypeVar("_T", bound=BaseModel)


class AgentBackend:
    """Pipeline-facing agent. Wraps a ModelBackend with pipeline-level config."""

    def __init__(
        self,
        model: ModelBackend,
        *,
        thinking_budget: int | None = None,
    ) -> None:
        self._model = model
        self._thinking_budget = thinking_budget

    @property
    def thinking_capable(self) -> bool:
        return self._model.thinking_capable

    @property
    def tools_capable(self) -> bool:
        return self._model.tools_capable

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[_T],
        *,
        tools: dict[str, Callable] | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
    ) -> _T:
        """Send a prompt and return validated structured output.

        Raises ``NotImplementedError`` if ``tools`` is non-empty and
        the underlying ``ModelBackend`` declares ``tools_capable=False``.
        """
        if tools and not self._model.tools_capable:
            raise NotImplementedError(
                f"{type(self._model).__name__} does not support tools. "
                f"Assign a tools_capable service to this slot."
            )
        return await self._model.run(
            system_prompt,
            user_message,
            output_type,
            tools=tools,
            thinking_budget=self._thinking_budget,
            label=label,
            debug_log=debug_log,
        )
