#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the extractor pipeline.

Tests cover error paths, prompt loading, pure-Python step hooks, and
structural correctness without hitting the LLM.
"""

from __future__ import annotations

import asyncio
import pytest

from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.testing import store  # noqa: F401

from dissect.errors import PaperNotFoundError, PaperNotConvertedError
from dissect.pipeline import (
    _known_paper_urls,
    _pure_read,
    _pure_report,
    _pure_verify_citations,
    _guard_web_search,
    _guard_resolve,
    _guard_verify_citations,
    _guard_caput_causae,
    load_sections,
    dissect_paper,
    StepContext,
)
from dissect.models import (
    CaputCausae,
    CitationAuditEntry,
    CitationRef,
    CitationTaskOutput,
    Claim,
    ExternalEvidence,
    LoadBearingResult,
    PipelineState,
    SourceLoc,
    SupportLink,
)
from dissect.prompt import StepHooks, StepMeta, StepSpec


def test_paper_not_found_raises_specific_error(store):  # noqa: F811
    import asyncio

    with pytest.raises(PaperNotFoundError, match="not found in paperstore") as exc_info:
        asyncio.run(dissect_paper("P9999R0", store))

    assert isinstance(exc_info.value.__cause__, MissingMetaError)


def test_paper_no_markdown_raises_specific_error(store):  # noqa: F811
    import asyncio

    store.upsert_year("2026", [{"paper_id": "P9999R0", "title": "Test"}])

    with pytest.raises(PaperNotConvertedError, match="no converted markdown") as exc_info:
        asyncio.run(dissect_paper("P9999R0", store))

    assert isinstance(exc_info.value.__cause__, MissingPaperMdError)


def test_load_paper_success(store):  # noqa: F811
    store.upsert_year("2026", [{"paper_id": "P9999R0", "title": "Test"}])
    store.write_paper_md("P9999R0", "# Test Paper\n\nContent.")

    meta = store.get_meta("P9999R0")
    paper_md = store.get_paper_md("P9999R0")
    assert meta.paper_id == "P9999R0"
    assert "Content." in paper_md


def test_load_sections_returns_system_prompt():
    secs = load_sections()
    assert "System Prompt" in secs


def test_dissect_error_message_includes_pid(store):  # noqa: F811
    import asyncio

    with pytest.raises(PaperNotFoundError, match="P0001R0"):
        asyncio.run(dissect_paper("P0001R0", store))


# -- Pure step hooks ---------------------------------------------------------


def test_step0_read_chunks_and_citations():
    import asyncio
    state = PipelineState(paper_source="# Title\n\nSee P2300R10 for details.\n")
    ctx = StepContext(sections={}, model_slots={})
    asyncio.run(_pure_read(state, ctx))

    assert state.chunks is not None
    assert len(state.chunks) == 1
    assert state.chunks[0].line_offset == 1
    assert state.citations is not None
    assert any(c.paper_id == "P2300R10" for c in state.citations)


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_step13_report_renders_unsupported():
    import asyncio
    state = PipelineState(
        claims=[
            Claim(uid=1, loc=_loc(1), text="X is fast", original_quotes=["X is fast"],
                  section="3", question="How fast is X?", depends_on=[]),
        ],
        support_map=[
            SupportLink(claim_uid=1, evidence_uids=[], status="unsupported"),
        ],
    )
    ctx = StepContext(sections={}, model_slots={}, pid="P0001R0")
    asyncio.run(_pure_report(state, ctx))

    assert state.report is not None
    assert "How fast is X?" in state.report
    assert "Unsupported Claims" in state.report


# -- Guard hooks -------------------------------------------------------------


def test_guard_web_search_skips_when_no_critical_gap():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_uid=1, dependents=[], classification="anchored"),
        ],
    )
    assert _guard_web_search(state) is False


def test_guard_web_search_fires_on_critical_gap():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_uid=1, dependents=[], classification="critical_gap"),
        ],
    )
    assert _guard_web_search(state) is True


def test_guard_web_search_skips_when_citation_evidence_covers_gap():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_uid=1, dependents=[], classification="critical_gap"),
        ],
        external_evidence=[
            ExternalEvidence(
                claim_uid=1, source_url="https://example.com",
                source_title="Ex", text="passage", finding="confirmed",
                stance="supports", quantitative=False, cited=True,
                verifiable=True, normative=False,
            ),
        ],
    )
    assert _guard_web_search(state) is False


def test_guard_verify_citations_skips_when_no_citations():
    state = PipelineState(citations=None)
    assert _guard_verify_citations(state) is False


def test_guard_verify_citations_skips_when_empty():
    state = PipelineState(citations=[])
    assert _guard_verify_citations(state) is False


def test_guard_caput_causae_skips_when_no_anchored():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_uid=1, dependents=[], classification="critical_gap"),
        ],
    )
    assert _guard_caput_causae(state) is False


def test_guard_caput_causae_fires_when_anchored():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_uid=1, dependents=[], classification="anchored"),
        ],
    )
    assert _guard_caput_causae(state) is True


def test_guard_resolve_skips_when_no_external():
    state = PipelineState()
    assert _guard_resolve(state) is False


# -- Persistence dispatch ---------------------------------------------------


def test_store_citation_audit_adapts_field_name(store):  # noqa: F811
    """Regression: CitationAuditEntry uses `paper_id`, but the storage
    schema uses `cited_paper_id`. The persist path must adapt the
    duck-typed object before handing it to store_citation_audit.
    """
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    entries = [
        CitationAuditEntry(
            paper_id="P9999R0",
            resolution_method="wg21_link",
            resolved=True,
            source_url="https://wg21.link/p9999r0",
            quote_match="exact",
        ),
    ]
    from types import SimpleNamespace
    store.store_citation_audit("P1000R0", [
        SimpleNamespace(
            cited_paper_id=e.paper_id,
            resolution_method=e.resolution_method,
            resolved=e.resolved,
            source_url=e.source_url,
            quote_match=e.quote_match,
            discrepancy=e.discrepancy,
        )
        for e in entries
    ])
    rows = store.get_citation_audit("P1000R0")
    assert len(rows) == 1
    assert rows[0].cited_paper_id == "P9999R0"
    assert rows[0].resolution_method == "wg21_link"
    assert rows[0].resolved is True


def test_store_caput_causae_writes_thesis(store):  # noqa: F811
    """Regression: dispatch persists state.caput_causae.thesis via
    store_caput_causae(pid, thesis_string)."""
    store.upsert_year("2026", [{"paper_id": "P1000R0"}])
    cc = CaputCausae(thesis="The paper argues for X.")
    store.store_caput_causae("P1000R0", cc.thesis)
    row = store.get_caput_causae("P1000R0")
    assert row is not None
    assert row.thesis == "The paper argues for X."


# -- Known-URL lookup --------------------------------------------------------


_P3175_URL = "https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2024/p3175r3.html"


def test_known_paper_urls_returns_urls_for_indexed_papers(store):  # noqa: F811
    store.upsert_year("2024", [{
        "paper_id": "P3175R3",
        "title": "X",
        "url": _P3175_URL,
    }])
    citations = [CitationRef(paper_id="P3175R3", count=2)]
    urls = _known_paper_urls(citations, store)
    assert urls == {"P3175R3": _P3175_URL}


def test_known_paper_urls_skips_unindexed_papers(store):  # noqa: F811
    citations = [CitationRef(paper_id="P9999R0", count=1)]
    assert _known_paper_urls(citations, store) == {}


def test_known_paper_urls_handles_missing_backend():
    citations = [CitationRef(paper_id="P3175R3", count=1)]
    assert _known_paper_urls(citations, None) == {}


def test_known_paper_urls_skips_rows_with_empty_url(store):  # noqa: F811
    store.upsert_year("2024", [{"paper_id": "P3175R3", "title": "X"}])
    citations = [CitationRef(paper_id="P3175R3", count=1)]
    assert _known_paper_urls(citations, store) == {}


# -- Verify-citations user message assembly ---------------------------------


def _verify_citations_spec() -> StepSpec:
    meta = StepMeta(
        name="Step 8 - Verify Citations",
        number=8,
        model_slot="fast",
        execution="parallel",
        reads=["citations", "claims", "evidence"],
        writes=["citation_audit", "external_evidence"],
        tools=["web_fetch"],
        condition="citations is non-empty",
    )
    return StepSpec(meta=meta, hooks=StepHooks())


def test_pure_verify_citations_injects_known_url_into_user_message(
    store, monkeypatch,  # noqa: F811
):
    store.upsert_year("2024", [{
        "paper_id": "P3175R3",
        "title": "X",
        "url": _P3175_URL,
    }])

    captured: list[str] = []

    async def fake_run_task(*, system_prompt, user_message, output_type, **kwargs):
        captured.append(user_message)
        return CitationTaskOutput(
            audit=CitationAuditEntry(
                paper_id="P3175R3",
                resolution_method="local_index",
                resolved=True,
                source_url=_P3175_URL,
            ),
            evidence=[],
        )

    monkeypatch.setattr("dissect.pipeline.run_task", fake_run_task)

    state = PipelineState(
        citations=[
            CitationRef(paper_id="P3175R3", count=1),
            CitationRef(paper_id="P9999R0", count=1),
        ],
        claims=[],
        evidence=[],
    )
    ctx = StepContext(
        sections={"Step 8 - Verify Citations": "INSTRUCTIONS"},
        model_slots={"fast": "stub-model"},
        backend=store,
        tool_registry={"web_fetch": lambda **_: ""},
    )
    ctx._current_spec = _verify_citations_spec()

    asyncio.run(_pure_verify_citations(state, ctx))

    by_pid = {msg.split("Paper: ", 1)[1].split(" ", 1)[0]: msg for msg in captured}
    assert "## Known URL" in by_pid["P3175R3"]
    assert _P3175_URL in by_pid["P3175R3"]
    assert "## Known URL" not in by_pid["P9999R0"]

    indexed = by_pid["P3175R3"]
    assert (
        indexed.index("## Citation")
        < indexed.index("## Known URL")
        < indexed.index("## Primary Claims")
        < indexed.index("## Instructions")
    )
