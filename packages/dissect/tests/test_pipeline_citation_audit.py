#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Regression test for citation_audit after a successful PDF fetch.

Closes the gap that the WebResearcher PDF-fetch bug fix exposed: when
``web_fetch`` returns extracted PDF text (rather than an error string),
the resulting citation_audit row must report ``Resolved: Yes`` with an
empty ``Discrepancy``. This test exercises ``_pure_verify_citations``
end-to-end with the LLM call stubbed and verifies both the in-memory
``state.citation_audit`` entries and the rendered Citation Audit row.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dissect import pipeline
from dissect.models import (
    CitationAuditEntry,
    CitationRef,
    CitationTaskOutput,
    PipelineState,
)
from dissect.pipeline import StepContext, _pure_verify_citations
from dissect.render import render_report


def _fake_run_task_returning(audit_entry: CitationAuditEntry):
    """Build a stub for ``pipeline.run_task`` returning a fixed audit row."""
    async def _stub(*args, **kwargs):
        return CitationTaskOutput(audit=audit_entry, evidence=[])
    return _stub


@pytest.mark.anyio
async def test_successful_pdf_fetch_yields_resolved_audit_row(monkeypatch):
    state = PipelineState(
        paper_source="dummy",
        citations=[CitationRef(paper_id="N5032", count=2)],
        claims=[],
        evidence=[],
    )

    # The post-fix happy path: web_fetch returns extracted PDF text. The
    # LLM then produces an audit row reporting successful resolution.
    successful_entry = CitationAuditEntry(
        paper_id="N5032",
        resolution_method="open_std",
        resolved=True,
        source_url="https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2025/n5032.pdf",
        quote_match="exact",
        discrepancy="",
    )

    # web_fetch result content doesn't matter to this test — the stub
    # run_task returns a fixed audit regardless. We just need it present
    # in tool_registry so _pure_verify_citations can hand it through.
    async def fake_web_fetch(url: str) -> str:
        return "Extracted text from N5032 PDF."

    spec = MagicMock()
    spec.meta.model_slot = "default"

    ctx = StepContext(
        sections={"Step 8 - Verify Citations": "stub instructions"},
        model_slots={"default": "test:stub"},
        researcher=None,
        backend=None,
        debug=False,
        pid="P4234R0",
        tool_registry={"web_fetch": fake_web_fetch},
    )
    ctx._current_spec = spec

    monkeypatch.setattr(
        pipeline, "run_task", _fake_run_task_returning(successful_entry),
    )

    await _pure_verify_citations(state, ctx)

    assert state.citation_audit is not None
    assert len(state.citation_audit) == 1
    row = state.citation_audit[0]
    assert row.paper_id == "N5032"
    assert row.resolved is True
    assert row.discrepancy == ""
    assert row.quote_match == "exact"


def test_citation_audit_renders_resolved_row_as_yes_with_empty_discrepancy():
    """Render-level regression: a Resolved audit row produces ``Yes`` / ``-``."""
    state = PipelineState(
        paper_source="dummy",
        citation_audit=[
            CitationAuditEntry(
                paper_id="N5032",
                resolution_method="open_std",
                resolved=True,
                source_url="https://example.org/n5032.pdf",
                quote_match="exact",
                discrepancy="",
            ),
        ],
    )

    rendered = render_report(state, pid="P4234R0", title="Test Paper")

    assert "## Citation Audit" in rendered
    assert "| N5032 | Yes | exact | - |" in rendered


def test_citation_audit_renders_unresolved_row_with_discrepancy():
    """Inverse of the above: the unresolved-row format is the current bug surface."""
    state = PipelineState(
        paper_source="dummy",
        citation_audit=[
            CitationAuditEntry(
                paper_id="N5032",
                resolution_method="not_found",
                resolved=False,
                source_url="",
                quote_match="not_checked",
                discrepancy="error fetching PDF",
            ),
        ],
    )

    rendered = render_report(state, pid="P4234R0", title="Test Paper")

    assert "| N5032 | No | not_checked | error fetching PDF |" in rendered
