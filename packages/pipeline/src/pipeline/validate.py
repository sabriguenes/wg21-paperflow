#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pipeline-construction-time validation.

`validate_capabilities` runs after `build_pipeline` and before
`dispatch`. It performs two checks:

1. **Logical model declaration.** Every step whose
   ``StepPrompt.model`` is not ``'none'`` must appear as a key in the
   pipeline markdown's ``## Services`` map. Undeclared names fail the
   pipeline at load time so a typo in ``assay.md`` never silently
   resolves to the wrong backend.
2. **Capability compatibility.** Each agent's class capabilities must
   satisfy the step's declared requirements: ``meta.tools`` non-empty
   requires ``agent.tools_capable``; an agent with a
   ``thinking_budget`` requires ``agent.thinking_capable``.

Mismatches are collected and rendered as a compact table so a single
markdown typo surfaces every downstream failure in one readable
message.

This is the primary gate for capability errors. The call-time
``NotImplementedError`` in `AgentBackend.run` remains as defense-in-
depth for ad-hoc tools passed through `run_task` outside `meta.tools`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pipeline.agents import AgentBackend
from pipeline.errors import CapabilityMismatchError, ServiceConfigError
from pipeline.prompt import PipelinePrompt, StepSpec

_FOOTER = (
    "Capability comes from the backend class, not the SERVICES.toml keys "
    "(which are documentation-only today).\n"
    "To change a step's model, edit the pipeline markdown's `## Services` "
    "map or the step's `**Model:**` line."
)


@dataclass(frozen=True)
class _Row:
    number: int
    slot: str
    service: str
    backend_class: str
    problem: str


def validate_capabilities(
    specs: list[StepSpec],
    prompt: PipelinePrompt,
    agents: Mapping[str, AgentBackend] | None = None,
    *,
    stop_after: int | None = None,
) -> None:
    """Reject pipelines whose step wiring is incompatible with declarations.

    Two-phase check, both running on the in-scope specs:

    1. **Logical name resolution.** Any step whose ``step.model`` is
       not ``'none'`` and not in ``prompt.services`` raises
       :class:`ServiceConfigError`. The error message lists the step,
       the bad logical name, and the available logical names so the
       user knows what to edit.
    2. **Capability checks.** ``meta.tools`` non-empty implies
       ``agent.tools_capable``; ``agent.thinking_budget`` set implies
       ``agent.thinking_capable``. Failures aggregate into one
       :class:`CapabilityMismatchError` whose message is a compact
       table.

    ``stop_after`` honors the run's ``--step`` scope: when set, only
    specs whose enumerate index is <= ``stop_after`` are validated.
    Rationale: a user who explicitly scoped to Step 5 will never
    touch the tool slot, so failing them on Step 11's tool
    requirement is punitive.

    Indexing semantics mirror :func:`pipeline.runner.dispatch` exactly:
    both filter by ``enumerate`` index against the step list, not by
    ``spec.step.number``. The two functions cannot drift on which
    specs are in scope. See the scoping-parity invariant in
    ``packages/pipeline/src/pipeline/CLAUDE.md``.
    """
    in_scope: list[StepSpec] = []
    for i, spec in enumerate(specs):
        if stop_after is not None and i > stop_after:
            break
        in_scope.append(spec)

    _validate_logical_names(in_scope, prompt)

    rows: list[_Row] = []
    for spec in in_scope:
        s = spec.step
        agent = spec.hooks.agent
        if agent is None and agents is not None and s.model != "none":
            agent = agents.get(s.model)
        if agent is None:
            continue

        slot = agent.slot_name or s.model or "<unbound>"
        svc = agent.service_name or "<unknown>"
        cls = agent.backend_class_name

        if s.tools and not agent.tools_capable:
            rows.append(_Row(
                number=s.number,
                slot=slot,
                service=svc,
                backend_class=cls,
                problem="declares tools, no support",
            ))
        if agent.thinking_budget and not agent.thinking_capable:
            rows.append(_Row(
                number=s.number,
                slot=slot,
                service=svc,
                backend_class=cls,
                problem=f"thinking_budget={agent.thinking_budget} unsupported",
            ))
    if rows:
        raise CapabilityMismatchError(_render_table(rows))


def _validate_logical_names(
    specs: list[StepSpec],
    prompt: PipelinePrompt,
) -> None:
    """Hard-error when a step references a logical model not declared in `## Services`."""
    errors: list[str] = []
    for spec in specs:
        model = spec.step.model
        if model == "none":
            continue
        if model not in prompt.services:
            errors.append(
                f"  - step '{spec.step.name}' references logical model "
                f"'{model}', not declared in `## Services`"
            )
    if errors:
        available = sorted(prompt.services)
        raise ServiceConfigError(
            f"Pipeline '{prompt.filename}' has steps referencing "
            f"undeclared logical models:\n"
            + "\n".join(errors)
            + f"\nAvailable logical models: {available}\n"
            f"Add the missing entry under `## Services` in {prompt.filename}, "
            f"or change the step's `**Model:**` line."
        )


def _render_table(rows: list[_Row]) -> str:
    headers = ("Step", "Slot", "Service", "Backend", "Problem")
    cells = [
        (str(r.number), r.slot, r.service, r.backend_class, r.problem)
        for r in rows
    ]
    widths = [
        max(len(headers[i]), max(len(c[i]) for c in cells))
        for i in range(len(headers))
    ]

    def fmt(row: tuple[str, ...]) -> str:
        return "  " + "  ".join(
            row[i].ljust(widths[i]) for i in range(len(headers))
        )

    out = [
        "Capability mismatches:",
        "",
        fmt(headers),
        fmt(tuple("-" * w for w in widths)),
    ]
    out.extend(fmt(c) for c in cells)
    out.append("")
    out.append(_FOOTER)
    return "\n".join(out)
