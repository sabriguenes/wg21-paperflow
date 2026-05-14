#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""End-to-end pipeline test against a live LLM using a minimal synthetic paper.

Requires an LLM backend configured via environment variables (see
``packages/pipeline/src/pipeline/runner.py::_build_default_slots``).
Marked ``network`` so the default test run skips it; opt in with::

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

from pathlib import Path
from typing import Callable

import pytest

from paperstore import SqliteBackend
from paperstore.tools import PaperstoreTools

from pipeline import (
    DEFAULT_MODEL_SLOTS,
    StepContext,
    WebResearcher,
    build_pipeline,
    dispatch,
    load_sections,
)

from dissect.models import PipelineState
from dissect.pdf_extract import extract_pdf_text
from dissect.pipeline import _HOOKS


pytestmark = pytest.mark.network

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
    slots = dict(DEFAULT_MODEL_SLOTS)
    secs = dict(load_sections("dissect", "dissect.md"))
    pipeline = build_pipeline(secs, _HOOKS)

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
            model_slots=slots,
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

    # ----- Step 1: Extract Claims ---------------------------------------
    assert state.raw_claims is not None, "Step 1 must populate state.raw_claims"
    assert len(state.raw_claims) >= 3, (
        f"Expected at least 3 raw normative claims from the synthetic paper; "
        f"got {len(state.raw_claims)}. Visited steps: {visited}"
    )

    # ----- Step 2: Dedup Claims -----------------------------------------
    normative_survivors = _normative_alive(state)
    assert len(normative_survivors) >= 2, (
        f"Expected at least 2 normative survivors after dedup; "
        f"got {len(normative_survivors)}."
    )

    # ----- Step 5: Extract Factual --------------------------------------
    assert state.raw_factual is not None, "Step 5 must populate state.raw_factual"
    assert len(state.raw_factual) >= 2, (
        f"Expected at least 2 raw factual claims; got {len(state.raw_factual)}."
    )

    # ----- Step 6: Dedup Factual ----------------------------------------
    factual_survivors = _factual_alive(state)
    assert len(factual_survivors) >= 2, (
        f"Expected at least 2 factual survivors after dedup; "
        f"got {len(factual_survivors)}."
    )

    # ----- Step 7: Extract Rhetoric -------------------------------------
    assert state.rhetoric is not None, "Step 7 must populate state.rhetoric"
    assert len(state.rhetoric) >= 2, (
        f"Expected at least 2 rhetorical markers from the synthetic paper; "
        f"got {len(state.rhetoric)}."
    )

    # ----- Step 8: Verify -----------------------------------------------
    assert state.verdicts is not None, "Step 8 must populate state.verdicts"
    statuses = {v.status for v in state.verdicts}
    for required in ("unproven", "disproven", "disclaimed"):
        assert required in statuses, (
            f"Verify did not produce a {required!r} verdict on a paper "
            f"engineered to have one. Statuses seen: {sorted(statuses)}."
        )

    # ----- Step 9: Load-Bearing -----------------------------------------
    assert state.load_bearing_claims is not None, "Step 9 must populate load_bearing_claims"
    classifications = {lb.classification for lb in state.load_bearing_claims}
    for required in ("anchored", "conflicted"):
        assert required in classifications, (
            f"Load-Bearing did not produce a {required!r} classification "
            f"on a paper engineered to have one. "
            f"Classifications seen: {sorted(classifications)}."
        )
    # Step 11/12 may upgrade ``critical_gap`` to ``externally_anchored`` or
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

    # ----- Step 10: Verify Citations ------------------------------------
    # The synthetic paper cites P9001R0 (fictional; should be not_found)
    # and N4860 (real; may resolve via the local paperstore index or
    # report not_found depending on what is staged).
    if state.citation_audit is not None:
        audited_pids = {row.paper_id for row in state.citation_audit}
        assert "P9001R0" in audited_pids, (
            f"Expected fictional P9001R0 in citation audit; "
            f"got {sorted(audited_pids)}."
        )

    # ----- Step 15: Report ----------------------------------------------
    assert state.report, "Step 15 must populate a non-empty report"
