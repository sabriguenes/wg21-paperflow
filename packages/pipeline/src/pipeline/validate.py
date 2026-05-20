#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pipeline-construction-time capability validation.

`validate_capabilities` runs after `build_pipeline` and before
`dispatch`. It walks the assembled `StepSpec` list and rejects any
step whose declared `meta.tools` or assigned `thinking_budget` would
land on a backend whose class attributes do not support it. Mismatches
are collected and rendered as a compact table so a single SERVICES.toml
typo surfaces the full damage in one readable message.

This is the primary gate for capability errors. The call-time
`NotImplementedError` in `AgentBackend.run` remains as defense-in-
depth for ad-hoc tools passed through `run_task` outside `meta.tools`.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.errors import CapabilityMismatchError
from pipeline.prompt import StepSpec

_FOOTER = (
    "Rebind misbound slots: --service SLOT=<service-with-matching-capabilities>\n"
    "Capability comes from the backend class, not the SERVICES.toml keys "
    "(which are documentation-only today)."
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
    *,
    stop_after: int | None = None,
) -> None:
    """Reject pipelines whose step-to-agent wiring is incompatible.

    Checks:
      * `meta.tools` non-empty implies `agent.tools_capable`.
      * `agent.thinking_budget` set implies `agent.thinking_capable`.

    `stop_after` honors the run's `--step` scope: when set, only
    specs whose enumerate index is <= `stop_after` are validated.
    Rationale: a user who explicitly scoped to Step 5 will never
    touch the tool slot, so failing them on Step 11's tool
    requirement is punitive.

    Indexing semantics mirror `pipeline.runner.dispatch` exactly:
    both filter by `enumerate` index against the step list, not by
    `spec.meta.number`. The two functions cannot drift on which
    specs are in scope. See the scoping-parity invariant in
    `packages/pipeline/src/pipeline/CLAUDE.md` and the parity test
    in the dissect integration suite.

    On mismatch, raises one `CapabilityMismatchError` whose message
    is a compact table: one row per mismatch with columns for step
    number, slot, service, backend class, and the specific problem.
    A single footer points at the fix.
    """
    rows: list[_Row] = []
    for i, spec in enumerate(specs):
        if stop_after is not None and i > stop_after:
            break
        m = spec.meta
        agent = spec.hooks.agent
        if agent is None:
            continue

        slot = agent.slot_name or "<unbound>"
        svc = agent.service_name or "<unknown>"
        cls = agent.backend_class_name

        if m.tools and not agent.tools_capable:
            rows.append(_Row(
                number=m.number,
                slot=slot,
                service=svc,
                backend_class=cls,
                problem="declares tools, no support",
            ))
        if agent.thinking_budget and not agent.thinking_capable:
            rows.append(_Row(
                number=m.number,
                slot=slot,
                service=svc,
                backend_class=cls,
                problem=f"thinking_budget={agent.thinking_budget} unsupported",
            ))
    if rows:
        raise CapabilityMismatchError(_render_table(rows))


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
