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

import pytest

from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.testing import store  # noqa: F401

from dissect.errors import PaperNotFoundError, PaperNotConvertedError
from dissect.pipeline import (
    _pure_read,
    _pure_report,
    _guard_web_search,
    _guard_resolve,
    _guard_verify_citations,
    _guard_caput_causae,
    load_sections,
    dissect_paper,
    StepContext,
)
from dissect.models import (
    Claim,
    ExternalEvidence,
    LoadBearingResult,
    PipelineState,
    SourceLoc,
    SupportLink,
)


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
            Claim(loc=_loc(1), text="X is fast", original_quotes=["X is fast"],
                  section="3", question="How fast is X?", depends_on=[]),
        ],
        support_map=[
            SupportLink(claim_loc=_loc(1), evidence_locs=[], status="unsupported"),
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
            LoadBearingResult(claim_loc=_loc(1), dependents=[], classification="anchored"),
        ],
    )
    assert _guard_web_search(state) is False


def test_guard_web_search_fires_on_critical_gap():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_loc=_loc(1), dependents=[], classification="critical_gap"),
        ],
    )
    assert _guard_web_search(state) is True


def test_guard_web_search_skips_when_citation_evidence_covers_gap():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_loc=_loc(1), dependents=[], classification="critical_gap"),
        ],
        external_evidence=[
            ExternalEvidence(
                claim_loc=_loc(1), source_url="https://example.com",
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
            LoadBearingResult(claim_loc=_loc(1), dependents=[], classification="critical_gap"),
        ],
    )
    assert _guard_caput_causae(state) is False


def test_guard_caput_causae_fires_when_anchored():
    state = PipelineState(
        load_bearing_claims=[
            LoadBearingResult(claim_loc=_loc(1), dependents=[], classification="anchored"),
        ],
    )
    assert _guard_caput_causae(state) is True


def test_guard_resolve_skips_when_no_external():
    state = PipelineState()
    assert _guard_resolve(state) is False
