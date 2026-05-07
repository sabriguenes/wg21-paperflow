#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the extractor pipeline entry point.

Tests cover error paths, prompt loading, and structural correctness
without hitting the LLM.
"""

from __future__ import annotations

import pytest

from paperstore.errors import MissingMetaError, MissingPaperMdError
from paperstore.testing import store  # noqa: F401

from review.errors import ReviewError
from review.pipeline import (
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


def test_load_sections_returns_system_prompt():
    """Verify extractor.md has a System Prompt section."""
    secs = load_sections()
    assert "System Prompt" in secs


def test_review_error_message_includes_pid(store):  # noqa: F811
    """Error messages include the paper ID for actionability."""
    with pytest.raises(ReviewError, match="P0001R0"):
        _load_paper("P0001R0", store)
