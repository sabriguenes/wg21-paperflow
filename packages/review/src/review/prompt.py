#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Parse step metadata from ``extractor.md`` and build the pipeline.

``extractor.md`` is the upstream authority for pipeline structure. Each
step section declares metadata (model slot, execution mode, reads,
writes, tools, conditions). This module parses that metadata, validates
it, and combines it with registered Python hooks to produce an ordered
list of ``StepSpec`` instances.

Raises ``PromptFileError`` subtypes on any structural mismatch so the
user knows to go fix ``extractor.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from review.errors import HookMismatchError, MissingMetadataError

_STEP_RE = re.compile(r"^Step\s+(\d+)")
_META_RE = re.compile(r"^-\s+\*\*(\w+):\*\*\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class StepMeta:
    """Parsed from a step section in ``extractor.md``.

    This is the authority for the step's configuration. Python hooks
    provide HOW to prepare and extract; this provides WHAT.
    """

    name: str
    """Full section header, e.g. ``'Step 5 -- Verify + Deps + Map + Contradict'``."""

    number: int
    """Numeric index parsed from the name. Controls execution order."""

    model_slot: str
    """Key into ``StepContext.model_slots``. ``'none'`` for pure-Python steps."""

    execution: str
    """``'main'`` (sequential) or ``'subagent'`` (parallel per chunk)."""

    reads: list[str]
    """``PipelineState`` field names this step reads."""

    writes: list[str]
    """``PipelineState`` field names this step writes."""

    tools: list[str] = field(default_factory=list)
    """Tool names to register on the Agent. Empty for most steps."""

    condition: str | None = None
    """Guard condition text from ``**Condition:**``, or ``None``."""

    @property
    def is_pure(self) -> bool:
        """True when the step bypasses the LLM entirely."""
        return self.model_slot.startswith("none")


@dataclass(frozen=True)
class StepHooks:
    """Python-side hooks registered for a single step.

    The hooks control HOW to format the reads into a user message
    (``prepare``) and HOW to store the LLM output into state
    (``extract``). The metadata controls WHAT.
    """

    output_type: type[BaseModel] | None = None
    prepare: Any = None
    extract: Any = None
    guard: Any = None
    pure: Any = None
    retry_empty: Any = None
    parallel: bool = False


@dataclass(frozen=True)
class StepSpec:
    """Declarative step descriptor.

    ``extractor.md`` is the upstream authority for pipeline structure.
    Each step section declares its metadata: model slot, execution
    mode, which state fields it reads and writes, tools, and guard
    conditions. Python provides the bespoke hooks: how to format
    the reads into a user message (prepare), and how to store the
    LLM output into the writes (extract).

    The prompt file controls WHAT each step does. The hooks
    control HOW. The runner handles everything common.
    """

    meta: StepMeta
    hooks: StepHooks


def parse_step_meta(name: str, body: str) -> StepMeta:
    """Parse step metadata from a section body.

    Raises ``MissingMetadataError`` if Model, Execution, Reads, or
    Writes is absent.
    """
    m = _STEP_RE.match(name)
    if not m:
        raise MissingMetadataError(
            f"Section '{name}' does not match expected 'Step N' format."
        )
    number = int(m.group(1))

    fields: dict[str, str] = {}
    for match in _META_RE.finditer(body):
        fields[match.group(1).lower()] = match.group(2).strip()

    required = ["model", "execution", "reads", "writes"]
    for req in required:
        if req not in fields:
            raise MissingMetadataError(
                f"Step '{name}' is missing required metadata field "
                f"'**{req.title()}:**'. Expected format: "
                f"'- **{req.title()}:** value'"
            )

    def _split_list(raw: str) -> list[str]:
        return [s.strip() for s in raw.split(",") if s.strip()]

    return StepMeta(
        name=name,
        number=number,
        model_slot=fields["model"].split("(")[0].strip().lower(),
        execution=fields["execution"].strip().lower(),
        reads=_split_list(fields["reads"]),
        writes=_split_list(fields["writes"]),
        tools=_split_list(fields.get("tools", "")),
        condition=fields.get("condition"),
    )


def build_pipeline(
    sections: dict[str, str],
    hooks: dict[str, StepHooks],
) -> list[StepSpec]:
    """Parse ``extractor.md`` metadata, attach hooks, return ordered specs.

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

    metas.sort(key=lambda m: m.number)

    step_names = {m.name for m in metas}
    hook_names = set(hooks)

    orphan_hooks = hook_names - step_names
    if orphan_hooks:
        raise HookMismatchError(
            f"Hooks registered for steps not in extractor.md: "
            f"{sorted(orphan_hooks)}"
        )

    missing_hooks = step_names - hook_names
    if missing_hooks:
        raise HookMismatchError(
            f"Steps in extractor.md have no registered hooks: "
            f"{sorted(missing_hooks)}"
        )

    return [
        StepSpec(meta=m, hooks=hooks[m.name])
        for m in metas
    ]
