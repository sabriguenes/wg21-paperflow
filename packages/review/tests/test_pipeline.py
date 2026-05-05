#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the review pipeline entry point.

Tests cover error paths, prompt loading, and reads filtering without
hitting the LLM.
"""

from __future__ import annotations

import pytest

from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.testing import store  # noqa: F401

from review.errors import ReviewError
from review.models import PipelineState, Claim
from review.pipeline import (
    _build_state_context,
    _extract_model_slot,
    _extract_reads,
    _extract_tools,
    _load_paper,
    load_sections,
)


def test_paper_not_found_raises_review_error(store):  # noqa: F811
    """get_meta raises MissingMetaError -> ReviewError with guidance."""
    with pytest.raises(ReviewError, match="not found in paperstore") as exc_info:
        _load_paper("P9999R0", store)

    assert isinstance(exc_info.value.__cause__, MissingMetaError)


def test_paper_no_markdown_raises_review_error(store):  # noqa: F811
    """Paper indexed but no markdown -> ReviewError with guidance."""
    store.upsert_year("2026", [{"paper_id": "P9999R0", "title": "Test"}])

    with pytest.raises(ReviewError, match="no converted markdown") as exc_info:
        _load_paper("P9999R0", store)

    assert isinstance(exc_info.value.__cause__, MissingPaperMdError)


def test_load_paper_success(store):  # noqa: F811
    """Paper with metadata and markdown loads successfully."""
    store.upsert_year("2026", [{"paper_id": "P9999R0", "title": "Test"}])
    store.write_paper_md("P9999R0", "# Test Paper\n\nContent.")

    meta, paper_md = _load_paper("P9999R0", store)
    assert meta["paper_id"] == "P9999R0"
    assert "Content." in paper_md


def test_load_sections_returns_expected_keys():
    """Verify review.md is parseable and contains all step sections."""
    secs = load_sections()

    assert "System Prompt" in secs
    assert "_preamble" in secs

    for i in range(9):
        matching = [k for k in secs if k.startswith(f"Step {i}")]
        assert len(matching) == 1, f"Expected exactly one section for Step {i}, got {matching}"


def test_load_sections_no_global_directives():
    """Verify Global Directives was removed."""
    secs = load_sections()
    assert "Global Directives" not in secs


def test_load_sections_no_posture():
    """Verify posture was removed from all steps."""
    secs = load_sections()
    for key, body in secs.items():
        if key.startswith("Step"):
            assert "posture" not in body.lower(), f"posture found in section '{key}'"


def test_load_sections_no_askquestion():
    """Verify AskQuestion was removed from all sections."""
    secs = load_sections()
    for key, body in secs.items():
        assert "AskQuestion" not in body, f"AskQuestion found in section '{key}'"


def test_load_sections_no_prior_review():
    """Verify prior review step was removed."""
    secs = load_sections()
    assert not any("Import Prior" in k for k in secs)


def test_load_sections_no_cache():
    """Verify cache references were removed."""
    secs = load_sections()
    for key, body in secs.items():
        if key.startswith("Step"):
            assert "cache" not in body.lower(), f"cache reference found in section '{key}'"


def test_load_sections_step2_has_web_search():
    """Verify Step 2 declares web_search tools."""
    secs = load_sections()
    step2 = secs["Step 2 - Gather Evidence"]
    assert "web_search" in step2


def test_review_error_message_includes_pid(store):  # noqa: F811
    """Error messages include the paper ID for actionability."""
    with pytest.raises(ReviewError, match="P0001R0"):
        _load_paper("P0001R0", store)


def test_extract_model_slot():
    body = "- **Model:** fast\n- **Execution:** main"
    assert _extract_model_slot(body) == "fast"


def test_extract_model_slot_default():
    body = "No model line here."
    assert _extract_model_slot(body) == "default"


def test_extract_reads():
    body = "- **Reads:** claims, evidence, argument_structures\n- **Writes:** foo"
    assert _extract_reads(body) == ["claims", "evidence", "argument_structures"]


def test_extract_reads_empty():
    body = "No reads line."
    assert _extract_reads(body) == []


def test_extract_tools_web_search():
    body = "- **Tools:** web_search\n- **Reads:** paper"
    assert _extract_tools(body) == ["web_search"]


def test_extract_tools_none():
    body = "- **Tools:** none\n- **Reads:** paper"
    assert _extract_tools(body) == []


def test_extract_tools_missing():
    body = "- **Reads:** paper"
    assert _extract_tools(body) == []


def test_build_state_context_filters_to_reads():
    state = PipelineState()
    state.title = "Test"
    state.document_number = "P1234R0"
    state.thesis = "The thesis"
    state.claims = [Claim(text="c", section="1", tag="factual")]

    ctx = _build_state_context(state, ["title", "document_number"])
    import json
    parsed = json.loads(ctx)
    assert "title" in parsed
    assert "document_number" in parsed
    assert "thesis" not in parsed
    assert "claims" not in parsed


def test_build_state_context_empty_reads():
    state = PipelineState()
    state.title = "Test"
    assert _build_state_context(state, []) == "{}"


def test_build_state_context_paper_not_in_state():
    """'paper' in reads should not crash - it's not a state field."""
    state = PipelineState()
    state.title = "Test"
    ctx = _build_state_context(state, ["paper", "title"])
    import json
    parsed = json.loads(ctx)
    assert "title" in parsed
    assert "paper" not in parsed
