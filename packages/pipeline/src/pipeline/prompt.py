#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Parse a pipeline's prompt file into a :class:`PipelinePrompt`.

The prompt file is the upstream authority for pipeline structure. It
declares the pipeline's logical-model map (``## Services``), the
pipeline-wide system prompt (``## System Prompt``), top-level config
(``## Config``), and every step section (``## N. StepName``). Each
step section carries its own per-step metadata: which logical model
runs it, execution mode, tools, conditions, and per-step budgets.

This module parses that file once via :meth:`PipelinePrompt.load` and
combines the parsed steps with the pipeline's registered Python hooks
to produce an ordered list of :class:`StepSpec` instances.

Raises :class:`PromptFileError` subtypes on any structural mismatch so
the user knows to go edit the prompt file.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from pipeline.errors import (
    HookMismatchError,
    MissingMetadataError,
    MissingSystemPromptError,
)
from pipeline.markdown import sections as _split_sections

_STEP_RE = re.compile(r"^(?:Step\s+)?(\d+)")
_META_RE = re.compile(r"^-\s+\*\*([\w \-]+):\*\*\s*(.+)$")
_STEP_SYSTEM_RE = re.compile(
    r"^### System Prompt\s*\n(?P<body>.*?)(?=^### |\Z)",
    re.MULTILINE | re.DOTALL,
)
_MD_BOLD_ITEM_RE = re.compile(r"^\s*-\s+\*\*(\w+):\*\*\s*(.+)", re.MULTILINE)

_PREAMBLE_KEY = "_preamble"
_SECTION_SERVICES = "Services"
_SECTION_CONFIG = "Config"
_SECTION_SYSTEM_PROMPT = "System Prompt"

# Reserved step-meta bullet keys consumed by typed fields on StepPrompt.
# Anything else found in a step body's `- **Key:** value` lines lands
# in StepPrompt.extra so pipelines can introduce per-step variables
# without changing this module.
_RESERVED_META_KEYS = frozenset({
    "model",
    "execution",
    "tools",
    "condition",
    "system prompt",
    "max-output",
    "thinking-budget",
    "chunk-tokens",
    "concurrency",
})


@dataclass(frozen=True)
class StepPrompt:
    """Parsed from a step section in the prompt file.

    This is the authority for the step's configuration. Python hooks
    provide HOW to prepare and extract; this provides WHAT.
    """

    name: str
    """Full section header, e.g. ``'4. Extract'``."""

    number: int
    """Numeric index parsed from the name. Controls execution order."""

    model: str
    """Logical model name. Resolves through ``## Services`` to a
    concrete :class:`ModelBackend`. Defaults to ``'default'`` when
    ``**Model:**`` is absent. ``'none'`` marks a pure-Python step
    with no framework-managed LLM call."""

    execution: str
    """``'main'`` (sequential) or ``'subagent'`` (parallel)."""

    tools: tuple[str, ...] = ()
    """Tool names to register on the Agent. Empty for most steps."""

    condition: str | None = None
    """Guard condition text from ``**Condition:**``, or ``None``."""

    system_prompt: str = ""
    """Step-specific system prompt override from ``### System Prompt``."""

    system_prompt_mode: str = "append"
    """How the step prompt combines with the pipeline prompt: append or replace."""

    max_output_tokens: int | None = None
    """Per-step override for the agent's ``max_tokens``. ``None`` means
    "use the agent's default". Parsed from ``**max-output:**``."""

    thinking_budget: int | None = None
    """Per-step thinking token budget for reasoning models. ``None`` means
    "use the agent's default"; ``0`` explicitly disables thinking.
    Parsed from ``**thinking-budget:**``."""

    chunk_tokens: int | None = None
    """Max tokens per chunk for steps that chunk the paper. ``None`` means
    "use the pipeline default". Parsed from ``**chunk-tokens:**``."""

    concurrency: int | None = None
    """Max concurrent LLM calls for fan-out steps. ``None`` means
    "use the pipeline default". Parsed from ``**concurrency:**``."""

    extra: Mapping[str, str] = field(default_factory=dict)
    """Any ``- **Key:** value`` bullet whose key is not consumed by a
    typed field. Lets pipelines declare per-step variables (e.g.
    ``**chunk-overlap:** 100``) without changing this module. Keys are
    lowercased; values are stripped strings."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    @property
    def is_custom(self) -> bool:
        """True when the step owns its own execution (no framework-managed LLM call)."""
        return self.model == "none"


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
    Each step section declares its metadata: logical model, execution
    mode, tools, and guard conditions. Python provides the bespoke
    hooks: how to format state into a user message (prepare), and how
    to store the LLM output into state (extract).

    The prompt file controls WHAT each step does. The hooks control
    HOW. The runner handles everything common.
    """

    step: StepPrompt
    hooks: StepHooks


@dataclass(frozen=True)
class PipelinePrompt:
    """Whole-file parse of a pipeline's markdown prompt.

    Produced once per ``(package, filename)`` pair by
    :meth:`PipelinePrompt.load` and reused for every run. Carries the
    raw section map plus typed views of the three structural sections
    (``## Services``, ``## Config``, ``## System Prompt``) and the
    parsed steps in file order.
    """

    package: str
    """Python package the file was loaded from (for :func:`importlib.resources`)."""

    filename: str
    """Filename inside the package, e.g. ``'assay.md'``."""

    sections: Mapping[str, str]
    """Whole-file section map (key = H2 header text, value = body).
    Same shape as :func:`pipeline.markdown.sections` returns. Kept
    around so step hooks can read step bodies directly (the pipeline
    prompt is the upstream authority for instructions)."""

    services: Mapping[str, str]
    """Logical-name -> SERVICES.toml-service-name map parsed from
    ``## Services``. ``default`` must be present; other names are
    pipeline-defined (``fast``, ``tool``, etc.)."""

    config: Mapping[str, str]
    """Flat key-value map parsed from ``## Config``. All values are
    strings; callers cast as needed (e.g. ``int(config["concurrency"])``)."""

    system_prompt: str
    """Pipeline-wide system prompt body from ``## System Prompt``.
    Empty string when the pipeline is pure-Python and declares no
    LLM-facing steps."""

    preamble: str
    """Text before the first ``## `` header (H1 title, intro mermaid)."""

    steps: tuple[StepPrompt, ...]
    """Parsed step prompts in file order. ``build_pipeline`` re-sorts
    by ``StepPrompt.number`` before pairing with hooks."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", MappingProxyType(dict(self.sections)))
        object.__setattr__(self, "services", MappingProxyType(dict(self.services)))
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))
        object.__setattr__(self, "steps", tuple(self.steps))

    def step_section(self, name: str) -> str:
        """Return the raw body of ``## name`` with metadata bullets and
        ``### System Prompt`` stripped, ready to use as instructions
        for an LLM call."""
        body = self.sections.get(name, "")
        return _strip_step_system_prompt(body)

    @classmethod
    def load(cls, package: str, filename: str) -> PipelinePrompt:
        """Read and parse the file once per process.

        Cached on ``(package, filename)`` via
        :func:`_load_cached`; calling :meth:`load` repeatedly is free.
        """
        return _load_cached(package, filename)


@functools.cache
def _load_cached(package: str, filename: str) -> PipelinePrompt:
    """Cache layer for :meth:`PipelinePrompt.load`. Keyed only on
    ``(package, filename)`` because the file is shipped read-only inside
    the package; mutating it in development requires a Python restart
    just like any other ``importlib.resources`` load."""
    import importlib.resources

    from pipeline.errors import PromptFileError as _PromptFileError

    try:
        resource = importlib.resources.files(package).joinpath(filename)
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise _PromptFileError(
            f"Failed to read {filename}: {exc}"
        ) from exc

    return _parse_prompt(package, filename, text)


def _parse_prompt(package: str, filename: str, text: str) -> PipelinePrompt:
    """Pure parse path; exposed without the cache so tests can drive
    it from in-memory strings."""
    section_map = _split_sections(text)

    services = parse_pipeline_services(section_map.get(_SECTION_SERVICES, ""))
    config = parse_pipeline_config(section_map.get(_SECTION_CONFIG, ""))
    system_prompt = section_map.get(_SECTION_SYSTEM_PROMPT, "").strip()
    preamble = section_map.get(_PREAMBLE_KEY, "")

    steps: list[StepPrompt] = []
    for key, body in section_map.items():
        if _STEP_RE.match(key):
            steps.append(parse_step_prompt(key, body))

    return PipelinePrompt(
        package=package,
        filename=filename,
        sections=section_map,
        services=services,
        config=config,
        system_prompt=system_prompt,
        preamble=preamble,
        steps=tuple(sorted(steps, key=lambda s: s.number)),
    )


def parse_pipeline_services(body: str) -> dict[str, str]:
    """Parse a ``## Services`` markdown section into a logical-name map.

    Accepts lines like ``- **default:** anthropic-opus`` and returns
    ``{"default": "anthropic-opus"}``. Keys are lowercased; empty
    values are skipped.
    """
    out: dict[str, str] = {}
    for m in _MD_BOLD_ITEM_RE.finditer(body):
        name = m.group(1).strip().lower()
        service = m.group(2).strip()
        if service:
            out[name] = service
    return out


def parse_pipeline_config(body: str) -> dict[str, str]:
    """Parse a ``## Config`` markdown section into a flat config dict.

    Same ``- **key:** value`` format as Services. Returns
    ``{"concurrency": "2", ...}``. Keys are lowercased.
    """
    out: dict[str, str] = {}
    for m in _MD_BOLD_ITEM_RE.finditer(body):
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        if value:
            out[key] = value
    return out


def parse_step_prompt(name: str, body: str) -> StepPrompt:
    """Parse step metadata from a section body.

    Body parsing is fence-aware: triple-backtick lines toggle an
    ``in_fence`` flag, and ``- **Key:** value`` bullets and
    ``### System Prompt`` markers inside a fenced code block are
    ignored. This keeps documentation examples and embedded templates
    from being interpreted as live metadata.

    ``**Model:**`` defaults to ``"default"`` when absent; the special
    value ``"none"`` marks a pure-Python step. ``**Execution:**``
    defaults to ``"main"``.

    Unknown ``- **Key:** value`` bullets land in :attr:`StepPrompt.extra`
    so pipelines can declare per-step variables without editing this
    module.
    """
    m = _STEP_RE.match(name)
    if not m:
        raise MissingMetadataError(
            f"Section '{name}' does not match expected 'N. Name' or 'Step N' format."
        )
    number = int(m.group(1))

    fields, system_prompt = _scan_step_body(body)

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

    model = fields.get("model", "default").split("(")[0].strip().lower()
    execution = fields.get("execution", "main").strip().lower()

    def _split_list(raw: str) -> tuple[str, ...]:
        return tuple(s.strip() for s in raw.split(",") if s.strip())

    def _opt_int(raw: str | None) -> int | None:
        return int(raw) if raw is not None else None

    extra = {
        key: value
        for key, value in fields.items()
        if key not in _RESERVED_META_KEYS
    }

    return StepPrompt(
        name=name,
        number=number,
        model=model,
        execution=execution,
        tools=_split_list(fields.get("tools", "")),
        condition=fields.get("condition"),
        system_prompt=system_prompt,
        system_prompt_mode=system_prompt_mode,
        max_output_tokens=_opt_int(fields.get("max-output")),
        thinking_budget=_opt_int(fields.get("thinking-budget")),
        chunk_tokens=_opt_int(fields.get("chunk-tokens")),
        concurrency=_opt_int(fields.get("concurrency")),
        extra=extra,
    )


def _scan_step_body(body: str) -> tuple[dict[str, str], str]:
    """Walk a step body once, fence-aware, collecting bullet metadata
    and the ``### System Prompt`` block.

    Returns ``(fields, system_prompt)``. ``fields`` is keyed by the
    lowercased bullet name. ``system_prompt`` is the body text under
    the first non-fenced ``### System Prompt`` header, stripped.
    """
    fields: dict[str, str] = {}
    in_fence = False
    in_system_prompt = False
    system_prompt_lines: list[str] = []

    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            if in_system_prompt:
                system_prompt_lines.append(line)
            continue
        if in_fence:
            if in_system_prompt:
                system_prompt_lines.append(line)
            continue
        if line.startswith("### "):
            header = line[4:].strip()
            in_system_prompt = header.lower() == "system prompt"
            continue
        if in_system_prompt:
            system_prompt_lines.append(line)
            continue
        m = _META_RE.match(line)
        if m:
            fields[m.group(1).strip().lower()] = m.group(2).strip()

    return fields, "\n".join(system_prompt_lines).strip()


def _strip_step_system_prompt(body: str) -> str:
    """Remove per-step system prompt and metadata from user-facing
    step instructions. Fence-aware via line walk so triple-backtick
    blocks survive intact."""
    out: list[str] = []
    in_fence = False
    skipping_system_prompt = False

    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            skipping_system_prompt = False
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        if line.startswith("### "):
            header = line[4:].strip()
            if header.lower() == "system prompt":
                skipping_system_prompt = True
                continue
            skipping_system_prompt = False
            out.append(line)
            continue
        if skipping_system_prompt:
            continue
        if _META_RE.match(line):
            continue
        out.append(line)

    return "\n".join(out).strip()


def build_pipeline(
    prompt: PipelinePrompt,
    hooks: dict[str, StepHooks],
) -> list[StepSpec]:
    """Pair parsed step prompts with hooks, return ordered specs.

    Steps are sorted by ``StepPrompt.number`` (parsed from ``N.
    StepName`` or ``Step N``), not by section position in the file.

    Raises :class:`MissingSystemPromptError` if any step is LLM-backed
    (either by carrying a ``StepHooks.agent`` directly or by having
    ``StepPrompt.model != "none"``) but the prompt file's
    ``## System Prompt`` section is empty.

    Raises :class:`HookMismatchError` if the hook dict and the parsed
    steps disagree (orphan hook or unregistered step).
    """
    steps = list(prompt.steps)

    has_llm_steps = any(
        not s.is_custom or (
            s.name in hooks and hooks[s.name].agent is not None
        )
        for s in steps
    )
    if has_llm_steps and not prompt.system_prompt:
        raise MissingSystemPromptError(
            f"Prompt file '{prompt.filename}' is missing required "
            f"non-empty '## System Prompt' section."
        )

    step_names = {s.name for s in steps}
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
        StepSpec(step=s, hooks=hooks[s.name])
        for s in steps
    ]
