#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""End-to-end pipeline test against a live LLM using a minimal synthetic paper.

Requires an LLM backend configured via ``SERVICES.toml`` (see
``packages/pipeline/src/pipeline/services.py``). Marked ``network`` so
the default test run skips it; opt in with::

    uv run pytest packages/dissect/tests/test_synthetic_pipeline.py -v -m network

The synthetic paper at ``tests/fixtures/synthetic_paper.md`` is the
minimum input that exercises every pipeline category: 2+ normative
claims, 2+ factual claims, 2+ evidence items, 2+ rhetoric markers, and
the four normative claims are engineered to produce anchored,
critical_gap, and conflicted load-bearing classifications plus
unproven, disproven, and disclaimed verdicts.

Assertions are permissive (lower bounds and presence of categories)
because LLM output varies across providers and runs. We do not assert
exact counts, uids, or claim texts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import pytest

from paperstore import SqliteBackend
from paperstore.tools import PaperstoreTools

from pipeline import (
    AgentBackend,
    StepContext,
    WebResearcher,
    build_pipeline,
    dispatch,
    load_sections,
    load_services,
    resolve_slots,
)

from dissect.models import PipelineState
from dissect.pdf_extract import extract_pdf_text
from dissect.pipeline import _build_hooks


pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not os.environ.get("BRAVE_API_KEY"),
        reason="BRAVE_API_KEY not set",
    ),
]

PAPER_ID = "P9999R0"
FIXTURES = Path(__file__).parent / "fixtures"


def _seed_backend(tmp_path: Path) -> SqliteBackend:
    """Spin up a tmp SqliteBackend with the synthetic paper preloaded."""
    backend = SqliteBackend(tmp_path)
    backend.upsert_year("2026", [{
        "paper_id": PAPER_ID,
        "title": "std::channel<T> for Inter-Thread Communication",
        "authors": ["Test Fixture"],
        "subgroup": "LEWG",
        "url": "",
        "document_date": "2026-05-15",
        "mailing_date": "2026-05",
    }])
    paper_md = (FIXTURES / "synthetic_paper.md").read_text(encoding="utf-8")
    backend.write_paper_md(PAPER_ID, paper_md)
    return backend


async def _run_full_pipeline(
    backend: SqliteBackend,
    *,
    on_step: Callable[[str], None] | None = None,
) -> PipelineState:
    """Run the dissect pipeline and return the final state.

    Mirrors ``dissect.pipeline.dissect_paper`` but keeps the mutable
    ``PipelineState`` reachable for assertions; ``dissect_paper``
    returns only the rendered report string.
    """
    services, defaults = load_services()
    slots = resolve_slots(services, defaults)
    fast_svc, fast_backend = slots.get("fast", slots["default"])
    default_svc, default_backend = slots["default"]
    tool_svc, tool_backend = slots.get("tool", slots["default"])
    extraction_agent = AgentBackend(
        fast_backend, thinking_budget=2048,
        slot_name="fast", service_name=fast_svc,
    )
    synthesis_agent = AgentBackend(
        default_backend, thinking_budget=4096,
        slot_name="default", service_name=default_svc,
    )
    research_agent = AgentBackend(
        tool_backend,
        slot_name="tool", service_name=tool_svc,
    )
    agents = {
        "fast": extraction_agent,
        "default": synthesis_agent,
        "tool": research_agent,
    }

    secs = dict(load_sections("dissect", "dissect.md"))
    hooks = _build_hooks(extraction_agent, synthesis_agent, research_agent)
    pipeline = build_pipeline(secs, hooks)

    paper_md = backend.get_paper_md(PAPER_ID)
    backend.clear_dissect(PAPER_ID)

    state = PipelineState(paper_source=paper_md)

    async with WebResearcher(
        binary_extractors={"application/pdf": extract_pdf_text},
    ) as researcher:
        ps_tools = PaperstoreTools(backend)
        tool_reg = {
            "paper_meta": ps_tools.paper_meta,
            "paper_meta_latest": ps_tools.paper_meta_latest,
            "read_file": ps_tools.read_file,
            "deep_search": researcher.deep_search,
            "web_search": researcher.web_search,
            "web_fetch": researcher.web_fetch,
        }

        ctx = StepContext(
            sections=secs,
            agents=agents,
            researcher=researcher,
            backend=backend,
            debug=False,
            pid=PAPER_ID,
            tool_registry=tool_reg,
        )

        def _on_step_complete(spec, _state) -> None:
            if on_step is not None:
                on_step(spec.meta.name)

        await dispatch(pipeline, state, ctx, on_step_complete=_on_step_complete)

    return state


def _normative_alive(state: PipelineState) -> list:
    assert state.normative_claims is not None
    return [c for c in state.normative_claims if c.kind == "normative" and c.merged_into is None]


def _factual_alive(state: PipelineState) -> list:
    assert state.normative_claims is not None
    return [c for c in state.normative_claims if c.kind == "factual" and c.merged_into is None]


@pytest.mark.anyio
async def test_synthetic_paper_full_pipeline(tmp_path: Path) -> None:
    """Drive the synthetic paper through every step and check planted ground truth.

    The synthetic paper is engineered to exhibit, at minimum:

    * Chunking runs (at least one chunk).
    * 2+ normative claims and 2+ factual claims, the latter textually
      distinct from any normative claim.
    * 2+ evidence items and 2+ rhetoric markers.
    * Four normative claims that cover anchored, critical_gap, and
      conflicted load-bearing classifications.
    * Verdicts covering unproven, disproven, and disclaimed.
    """
    visited: list[str] = []
    backend = _seed_backend(tmp_path)
    state = await _run_full_pipeline(backend, on_step=visited.append)

    # ----- Step 0: Read --------------------------------------------------
    assert state.chunks, "Step 0 must populate state.chunks"

    # ----- Step 2: Extract Claims ---------------------------------------
    assert state.raw_claims is not None, "Step 2 must populate state.raw_claims"
    assert len(state.raw_claims) >= 3, (
        f"Expected at least 3 raw normative claims from the synthetic paper; "
        f"got {len(state.raw_claims)}. Visited steps: {visited}"
    )

    # ----- Step 3: Dedup Claims -----------------------------------------
    normative_survivors = _normative_alive(state)
    assert len(normative_survivors) >= 2, (
        f"Expected at least 2 normative survivors after dedup; "
        f"got {len(normative_survivors)}."
    )

    # ----- Step 6: Extract Factual --------------------------------------
    assert state.raw_factual is not None, "Step 6 must populate state.raw_factual"
    assert len(state.raw_factual) >= 2, (
        f"Expected at least 2 raw factual claims; got {len(state.raw_factual)}."
    )

    # ----- Step 7: Dedup Factual ----------------------------------------
    factual_survivors = _factual_alive(state)
    assert len(factual_survivors) >= 2, (
        f"Expected at least 2 factual survivors after dedup; "
        f"got {len(factual_survivors)}."
    )

    # ----- Step 8: Extract Rhetoric -------------------------------------
    assert state.rhetoric is not None, "Step 8 must populate state.rhetoric"
    assert len(state.rhetoric) >= 2, (
        f"Expected at least 2 rhetorical markers from the synthetic paper; "
        f"got {len(state.rhetoric)}."
    )

    # ----- Step 9: Verify -----------------------------------------------
    assert state.verdicts is not None, "Step 9 must populate state.verdicts"
    statuses = {v.status for v in state.verdicts}
    for required in ("unproven", "disproven", "disclaimed"):
        assert required in statuses, (
            f"Verify did not produce a {required!r} verdict on a paper "
            f"engineered to have one. Statuses seen: {sorted(statuses)}."
        )

    # ----- Step 10: Load-Bearing ----------------------------------------
    assert state.load_bearing_claims is not None, "Step 10 must populate load_bearing_claims"
    classifications = {lb.classification for lb in state.load_bearing_claims}
    for required in ("anchored", "conflicted"):
        assert required in classifications, (
            f"Load-Bearing did not produce a {required!r} classification "
            f"on a paper engineered to have one. "
            f"Classifications seen: {sorted(classifications)}."
        )
    # Step 12/13 may upgrade ``critical_gap`` to ``externally_anchored`` or
    # ``externally_contested`` when web search finds backing or counter
    # evidence, and a claim that depends on a contested claim becomes
    # ``depends_on_contested``. Any of these four indicate the load-bearing
    # system correctly identified an originally-unsupported claim.
    unsupported_flavours = {
        "critical_gap", "externally_anchored",
        "externally_contested", "depends_on_contested",
    }
    assert classifications & unsupported_flavours, (
        f"Load-Bearing did not flag any unsupported claim on a paper "
        f"engineered to have one. Classifications seen: "
        f"{sorted(classifications)}."
    )

    # ----- Step 11: Verify Citations ------------------------------------
    # The synthetic paper cites P9001R0 (fictional; should be not_found)
    # and N4860 (real; may resolve via the local paperstore index or
    # report not_found depending on what is staged).
    if state.citation_audit is not None:
        audited_pids = {row.paper_id for row in state.citation_audit}
        assert "P9001R0" in audited_pids, (
            f"Expected fictional P9001R0 in citation audit; "
            f"got {sorted(audited_pids)}."
        )

    # ----- Step 16: Report ----------------------------------------------
    assert state.report, "Step 16 must populate a non-empty report"
