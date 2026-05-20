#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Parse step metadata from a prompt file and build the pipeline.

The prompt file is the upstream authority for pipeline structure. Each
step section declares metadata (model slot, execution mode, tools,
conditions). This module parses that metadata, validates it, and
combines it with registered Python hooks to produce an ordered list
of ``StepSpec`` instances.

Raises ``PromptFileError`` subtypes on any structural mismatch so the
user knows to go fix the prompt file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from pipeline.errors import (
    HookMismatchError,
    MissingMetadataError,
    MissingSystemPromptError,
)

_STEP_RE = re.compile(r"^(?:Step\s+)?(\d+)")
_META_RE = re.compile(r"^-\s+\*\*([\w ]+):\*\*\s*(.+)$", re.MULTILINE)
_STEP_SYSTEM_RE = re.compile(
    r"^### System Prompt\s*\n(?P<body>.*?)(?=^### |\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class StepMeta:
    """Parsed from a step section in the prompt file.

    This is the authority for the step's configuration. Python hooks
    provide HOW to prepare and extract; this provides WHAT.
    """

    name: str
    """Full section header, e.g. ``'Step 5 - Verify'``."""

    number: int
    """Numeric index parsed from the name. Controls execution order."""

    model_slot: str
    """Key into ``StepContext.agents``. ``'none'`` for custom steps."""

    execution: str
    """``'main'`` (sequential) or ``'subagent'`` (parallel)."""

    tools: list[str] = field(default_factory=list)
    """Tool names to register on the Agent. Empty for most steps."""

    condition: str | None = None
    """Guard condition text from ``**Condition:**``, or ``None``."""

    system_prompt: str = ""
    """Step-specific system prompt override from ``### System Prompt``."""

    system_prompt_mode: str = "append"
    """How the step prompt combines with the pipeline prompt: append or replace."""

    @property
    def is_custom(self) -> bool:
        """True when the step owns its own execution (no framework-managed LLM call)."""
        return self.model_slot.startswith("none")


@dataclass(frozen=True)
class StepHooks:
    """Python-side hooks registered for a single step.

    The hooks control HOW to format state into a user message
    (``prepare``) and HOW to store the LLM output into state
    (``extract``). ``agent`` is the ``AgentBackend`` instance
    assigned to this step; set at pipeline setup time, not at
    import time.
    """

    output_type: type[BaseModel] | None = None
    agent: Any = None
    prepare: Any = None
    extract: Any = None
    guard: Any = None
    custom: Any = None
    output_validator: Any = None
    parallel: bool = False
    request_limit: int | None = None


@dataclass(frozen=True)
class StepSpec:
    """Declarative step descriptor.

    The prompt file is the upstream authority for pipeline structure.
    Each step section declares its metadata: model slot, execution
    mode, tools, and guard conditions. Python provides the bespoke
    hooks: how to format state into a user message (prepare), and
    how to store the LLM output into state (extract).

    The prompt file controls WHAT each step does. The hooks
    control HOW. The runner handles everything common.
    """

    meta: StepMeta
    hooks: StepHooks


def parse_step_meta(name: str, body: str) -> StepMeta:
    """Parse step metadata from a section body.

    ``**Model:**`` and ``**Execution:**`` are optional. When absent,
    they default to ``"none"`` and ``"main"`` respectively. Agent
    assignment is handled by ``StepHooks.agent`` in Python, not by
    the prompt file.

    ``**Tools:**``, ``**Condition:**``, and ``**System prompt:**``
    remain active and are parsed when present.
    """
    m = _STEP_RE.match(name)
    if not m:
        raise MissingMetadataError(
            f"Section '{name}' does not match expected 'N. Name' or 'Step N' format."
        )
    number = int(m.group(1))

    fields: dict[str, str] = {}
    for match in _META_RE.finditer(body):
        fields[match.group(1).lower()] = match.group(2).strip()

    system_prompt = _parse_step_system_prompt(body)
    system_prompt_mode = fields.get("system prompt", "append").split()[0].strip().lower()
    if system_prompt_mode not in {"append", "replace"}:
        raise MissingMetadataError(
            f"Step '{name}' has invalid '**System prompt:**' value "
            f"'{fields.get('system prompt')}'. Expected 'append' or 'replace'."
        )
    if system_prompt_mode == "replace" and not system_prompt:
        raise MissingMetadataError(
            f"Step '{name}' sets '**System prompt:** replace' but has no "
            f"'### System Prompt' body."
        )

    model_slot = fields.get("model", "none").split("(")[0].strip().lower()
    execution = fields.get("execution", "main").strip().lower()

    def _split_list(raw: str) -> list[str]:
        return [s.strip() for s in raw.split(",") if s.strip()]

    return StepMeta(
        name=name,
        number=number,
        model_slot=model_slot,
        execution=execution,
        tools=_split_list(fields.get("tools", "")),
        condition=fields.get("condition"),
        system_prompt=system_prompt,
        system_prompt_mode=system_prompt_mode,
    )


def _parse_step_system_prompt(body: str) -> str:
    """Extract the optional per-step ``### System Prompt`` body."""
    match = _STEP_SYSTEM_RE.search(body)
    return match.group("body").strip() if match else ""


def _strip_step_system_prompt(body: str) -> str:
    """Remove per-step system prompt and metadata from user-facing step instructions."""
    body = _STEP_SYSTEM_RE.sub("", body)
    body = _META_RE.sub("", body)
    return body.strip()


def build_pipeline(
    sections: dict[str, str],
    hooks: dict[str, StepHooks],
) -> list[StepSpec]:
    """Parse prompt file metadata, attach hooks, return ordered specs.

    Steps are sorted by their numeric index (parsed from ``Step N``),
    not by section position in the file.

    Raises ``MissingMetadataError`` if any step section lacks required
    fields. Raises ``HookMismatchError`` if the hook dict and the
    parsed steps disagree (orphan hook or unregistered step).
    """
    metas: list[StepMeta] = []
    for key, body in sections.items():
        if _STEP_RE.match(key):
            metas.append(parse_step_meta(key, body))
            sections[key] = _strip_step_system_prompt(body)

    metas.sort(key=lambda m: m.number)

    has_llm_steps = any(not m.is_custom for m in metas) or any(
        hooks.get(m.name) and hooks[m.name].agent is not None
        for m in metas if m.name in hooks
    )
    if has_llm_steps:
        if not sections.get("System Prompt", "").strip():
            raise MissingSystemPromptError(
                "Prompt file is missing required non-empty '## System Prompt' section."
            )

    step_names = {m.name for m in metas}
    hook_names = set(hooks)

    orphan_hooks = hook_names - step_names
    if orphan_hooks:
        raise HookMismatchError(
            f"Hooks registered for steps not in prompt file: "
            f"{sorted(orphan_hooks)}"
        )

    missing_hooks = step_names - hook_names
    if missing_hooks:
        raise HookMismatchError(
            f"Steps in prompt file have no registered hooks: "
            f"{sorted(missing_hooks)}"
        )

    return [
        StepSpec(meta=m, hooks=hooks[m.name])
        for m in metas
    ]
