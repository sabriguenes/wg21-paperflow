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
from typing import Any

import pytest

from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.testing import store  # noqa: F401

from pipeline import StepContext, load_sections
from pipeline.errors import PaperNotFoundError, PaperNotConvertedError, StepError
from pipeline.model_backends import DEFAULT_REQUEST_LIMIT, ModelBackend


class _FullyCapableStub(ModelBackend):
    """Tools + thinking capable. For tests that exercise dissect_paper's
    early-exit error paths without needing real services."""

    thinking_capable = True
    tools_capable = True

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        output_type: type[Any],
        *,
        tools: dict[str, Any] | None = None,
        thinking_budget: int | None = None,
        label: str = "",
        debug_log: list[str] | None = None,
        request_limit: int = DEFAULT_REQUEST_LIMIT,
    ) -> Any:
        return output_type()


@pytest.fixture
def capable_services(monkeypatch):
    """Replace load_services with a tools+thinking-capable stub.

    `dissect_paper` constructs the pipeline and calls
    `validate_capabilities` before reaching the get_meta call that the
    paper-not-found tests rely on. The production SERVICES.toml binds
    all default slots to AnthropicBackend (thinking_capable=False),
    which the validator correctly rejects. These tests do not care
    about real backends; they just need pipeline construction to
    succeed so the get_meta error path is reachable.
    """
    from dissect import pipeline as dissect_pipeline_mod

    stub = _FullyCapableStub()
    services = {"stub": stub}
    defaults = {"fast": "stub", "default": "stub", "tool": "stub"}
    monkeypatch.setattr(
        dissect_pipeline_mod,
        "load_services",
        lambda: (services, defaults),
    )
from dissect.pipeline import (
    _citation_info,
    _custom_read,
    _custom_report,
    _custom_verify_citations,
    _custom_web_search,
    _guard_web_search,
    _guard_resolve,
    _guard_verify_citations,
    _guard_caput_causae,
    dissect_paper,
)
from dissect.models import (
    CaputCausae,
    CitationAuditEntry,
    CitationRef,
    CitationTaskOutput,
    Claim,
    ClaimVerdict,
    ExternalEvidence,
    LoadBearingResult,
    PipelineState,
    SourceLoc,
    WebSearchOutput,
)
from pipeline import StepHooks, StepMeta, StepSpec


def test_paper_not_found_raises_specific_error(store, capable_services):  # noqa: F811
    import asyncio

    with pytest.raises(PaperNotFoundError, match="not found in paperstore") as exc_info:
        asyncio.run(dissect_paper("P9999R0", store))

    assert isinstance(exc_info.value.__cause__, MissingMetaError)


def test_paper_no_markdown_raises_specific_error(store, capable_services):  # noqa: F811
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
    secs = load_sections("dissect", "dissect.md")
    assert "System Prompt" in secs


def test_dissect_error_message_includes_pid(store, capable_services):  # noqa: F811
    import asyncio

    with pytest.raises(PaperNotFoundError, match="P0001R0"):
        asyncio.run(dissect_paper("P0001R0", store))


# -- Pure step hooks ---------------------------------------------------------


def test_step0_read_chunks_and_citations():
    import asyncio
    state = PipelineState(paper_source="# Title\n\nSee P2300R10 for details.\n")
    ctx = StepContext(sections={}, agents={})
    asyncio.run(_custom_read(state, ctx))

    assert state.chunks is not None
    assert len(state.chunks) == 1
    assert state.chunks[0].line_offset == 1
    assert state.citations is not None
    assert any(c.paper_id == "P2300R10" for c in state.citations)


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_step15_report_renders_unsupported():
    import asyncio
    state = PipelineState(
        normative_claims=[
            Claim(uid=1, loc=_loc(1), text="X is fast", original_quotes=["X is fast"],
                  section="3", question="How fast is X?", depends_on=[]),
        ],
        verdicts=[
            ClaimVerdict(claim_uid=1, status="unproven"),
        ],
    )
    ctx = StepContext(sections={}, agents={}, pid="P0001R0")
    asyncio.run(_custom_report(state, ctx))

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
            resolution_method="local_index",
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
    assert rows[0].resolution_method == "local_index"
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


def test_citation_info_returns_info_for_indexed_papers(store):  # noqa: F811
    store.upsert_year("2024", [{
        "paper_id": "P3175R3",
        "title": "X",
        "url": _P3175_URL,
    }])
    citations = [CitationRef(paper_id="P3175R3", count=2)]
    info = _citation_info(citations, store)
    assert "P3175R3" in info
    assert info["P3175R3"]["url"] == _P3175_URL


def test_citation_info_skips_unindexed_papers(store):  # noqa: F811
    citations = [CitationRef(paper_id="P9999R0", count=1)]
    assert _citation_info(citations, store) == {}


def test_citation_info_handles_missing_backend():
    citations = [CitationRef(paper_id="P3175R3", count=1)]
    assert _citation_info(citations, None) == {}


def test_citation_info_includes_rows_with_empty_url(store):  # noqa: F811
    store.upsert_year("2024", [{"paper_id": "P3175R3", "title": "X"}])
    citations = [CitationRef(paper_id="P3175R3", count=1)]
    info = _citation_info(citations, store)
    assert "P3175R3" in info
    assert info["P3175R3"]["url"] == ""


# -- Verify-citations user message assembly ---------------------------------


def _verify_citations_spec() -> StepSpec:
    meta = StepMeta(
        name="10. Verify Citations",
        number=10,
        model_slot="fast",
        execution="parallel",
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

    async def fake_run_task(agent, system_prompt, user_message, output_type, **kwargs):
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

    from pipeline.agents import AgentBackend
    from pipeline.model_backends import Llama3Backend
    _stub = AgentBackend(Llama3Backend(base_url="", api_key="", model=""))

    state = PipelineState(
        citations=[
            CitationRef(paper_id="P3175R3", count=1),
            CitationRef(paper_id="P9999R0", count=1),
        ],
        normative_claims=[],
        deduped_evidence=[],
    )
    ctx = StepContext(
        sections={
            "System Prompt": "You dissect papers.",
            "10. Verify Citations": "INSTRUCTIONS",
        },
        agents={"tool": _stub, "default": _stub, "fast": _stub},
        backend=store,
        tool_registry={"web_fetch": lambda **_: ""},
    )
    ctx._current_spec = _verify_citations_spec()

    asyncio.run(_custom_verify_citations(state, ctx))

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


def test_verify_citations_fails_above_threshold(store, monkeypatch):  # noqa: F811
    calls = 0

    async def fake_run_task(agent, system_prompt, user_message, output_type, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("fetch failed")
        return CitationTaskOutput(
            audit=CitationAuditEntry(
                paper_id="P0003R0",
                resolution_method="not_found",
                resolved=False,
            ),
            evidence=[],
        )

    monkeypatch.setattr("dissect.pipeline.run_task", fake_run_task)
    from pipeline.agents import AgentBackend
    from pipeline.model_backends import Llama3Backend
    _stub = AgentBackend(Llama3Backend(base_url="", api_key="", model=""))

    state = PipelineState(
        citations=[
            CitationRef(paper_id="P0001R0", count=1),
            CitationRef(paper_id="P0002R0", count=1),
            CitationRef(paper_id="P0003R0", count=1),
        ],
        normative_claims=[],
        deduped_evidence=[],
    )
    ctx = StepContext(
        sections={
            "System Prompt": "You dissect papers.",
            "10. Verify Citations": "INSTRUCTIONS",
        },
        agents={"tool": _stub, "default": _stub, "fast": _stub},
        backend=store,
        tool_registry={"web_fetch": lambda **_: ""},
    )
    ctx._current_spec = _verify_citations_spec()

    with pytest.raises(StepError, match="Citation verification failed"):
        asyncio.run(_custom_verify_citations(state, ctx))


def test_web_search_fails_above_threshold(monkeypatch):
    calls = 0

    async def fake_run_task(agent, system_prompt, user_message, output_type, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("search failed")
        return WebSearchOutput(external_evidence=[])

    monkeypatch.setattr("dissect.pipeline.run_task", fake_run_task)
    from pipeline.agents import AgentBackend
    from pipeline.model_backends import Llama3Backend
    _stub = AgentBackend(Llama3Backend(base_url="", api_key="", model=""))

    claims = [
        Claim(
            uid=i, loc=_loc(i), text=f"Claim {i}", original_quotes=[f"Claim {i}"],
            section="1", question=f"Question {i}?", depends_on=[],
        )
        for i in range(1, 4)
    ]
    state = PipelineState(
        normative_claims=claims,
        load_bearing_claims=[
            LoadBearingResult(claim_uid=i, dependents=[], classification="critical_gap")
            for i in range(1, 4)
        ],
    )
    ctx = StepContext(
        sections={
            "System Prompt": "You dissect papers.",
            "11. Web Search": "INSTRUCTIONS",
        },
        agents={"tool": _stub, "default": _stub, "fast": _stub},
        tool_registry={"deep_search": lambda **_: "", "web_fetch": lambda **_: ""},
    )
    ctx._current_spec = StepSpec(
        meta=StepMeta(
            name="11. Web Search",
            number=11,
            model_slot="fast",
            execution="parallel",
        ),
        hooks=StepHooks(),
    )

    with pytest.raises(StepError, match="Web search failed"):
        asyncio.run(_custom_web_search(state, ctx))


# ---------------------------------------------------------------------------
# Step 1: _custom_tag_sentences chunk_index honoring
# ---------------------------------------------------------------------------


class _StubClassifier:
    """Records which texts get classified so the test can verify
    chunk_index restriction."""

    model_id = "stub"
    device = "cpu"

    def __init__(self):
        self.seen_texts: list[str] = []

    def classify(self, texts, candidate_labels, *, multi_label=True):
        self.seen_texts.extend(texts)
        return [
            {candidate_labels[0]: 0.5, candidate_labels[1]: 0.1}
            for _ in texts
        ]


def test_custom_tag_sentences_honors_chunk_index():
    """``--chunk N`` must restrict Step 1 work to chunk N. Without this,
    interactive iteration on ``--step 1 --chunk 0`` pays the full
    classifier cost across every chunk in the paper."""
    import asyncio
    from dissect.models import Chunk, PipelineState
    from dissect.pipeline import _custom_tag_sentences
    from pipeline import StepContext

    state = PipelineState(
        chunks=[
            Chunk(text="Chunk zero sentence.", line_offset=1),
            Chunk(text="Chunk one sentence.", line_offset=10),
        ],
    )
    clf = _StubClassifier()
    ctx = StepContext(
        sections={},
        classifiers={"selector": clf},
        chunk_index=0,
    )

    asyncio.run(_custom_tag_sentences(state, ctx))

    # Only chunk 0's text should reach the classifier.
    assert clf.seen_texts == ["Chunk zero sentence."]
    assert state.tagged_sentences is not None
    assert len(state.tagged_sentences) == 1
    assert state.tagged_sentences[0].span.line == 1


def test_custom_tag_sentences_no_chunk_index_processes_all():
    """When chunk_index is None, Step 1 processes every chunk."""
    import asyncio
    from dissect.models import Chunk, PipelineState
    from dissect.pipeline import _custom_tag_sentences
    from pipeline import StepContext

    state = PipelineState(
        chunks=[
            Chunk(text="Chunk zero sentence.", line_offset=1),
            Chunk(text="Chunk one sentence.", line_offset=10),
        ],
    )
    clf = _StubClassifier()
    ctx = StepContext(
        sections={},
        classifiers={"selector": clf},
        chunk_index=None,
    )

    asyncio.run(_custom_tag_sentences(state, ctx))
    assert clf.seen_texts == ["Chunk zero sentence.", "Chunk one sentence."]


def test_custom_tag_sentences_out_of_range_chunk_index():
    """An out-of-range chunk index produces zero tagged sentences."""
    import asyncio
    from dissect.models import Chunk, PipelineState
    from dissect.pipeline import _custom_tag_sentences
    from pipeline import StepContext

    state = PipelineState(
        chunks=[Chunk(text="Only chunk.", line_offset=1)],
    )
    clf = _StubClassifier()
    ctx = StepContext(
        sections={},
        classifiers={"selector": clf},
        chunk_index=5,
    )

    asyncio.run(_custom_tag_sentences(state, ctx))
    assert clf.seen_texts == []
    assert state.tagged_sentences == []
