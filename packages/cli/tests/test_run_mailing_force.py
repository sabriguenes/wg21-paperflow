#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the ``force`` flag on ``cli.jobs.run_mailing``."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from cli.jobs import run_mailing
from paperstore import SqliteBackend


def _seed_year(store: SqliteBackend, year: str, paper_id: str = "P1000R0") -> None:
    store.upsert_year(year, [{"paper_id": paper_id, "title": "Original Title"}])


def _fake_fetch(year: str) -> dict:
    return {
        f"{year}-01": [
            {"paper_id": "P1000R0", "title": "Refreshed Title", "url": "x"}
        ]
    }


def test_run_mailing_skips_past_indexed_year_by_default(tmp_path: Path) -> None:
    store = SqliteBackend(tmp_path)
    _seed_year(store, "2024")

    with patch("mailing.scrape.fetch_all_mailings_for_year") as fetch:
        result = asyncio.run(run_mailing(["2024"], store, current_year="2026"))

    fetch.assert_not_called()
    assert result["skipped"] == ["2024"]
    assert result["succeeded"] == []
    assert store.list_papers_for_year("2024")[0].title == "Original Title"


def test_run_mailing_force_bypasses_skip_and_updates_metadata(tmp_path: Path) -> None:
    store = SqliteBackend(tmp_path)
    _seed_year(store, "2024")

    with patch(
        "mailing.scrape.fetch_all_mailings_for_year", side_effect=_fake_fetch
    ) as fetch:
        result = asyncio.run(
            run_mailing(["2024"], store, current_year="2026", force=True)
        )

    fetch.assert_called_once_with("2024")
    assert result["skipped"] == []
    assert result["succeeded"] == [{"year": "2024", "papers": 1}]
    # Metadata refreshed; row count unchanged.
    assert store.list_papers_for_year("2024")[0].title == "Refreshed Title"


def test_run_mailing_force_preserves_source_and_markdown_paths(
    tmp_path: Path,
) -> None:
    """``upsert_year`` must not clobber download/conversion completion fields."""
    store = SqliteBackend(tmp_path)
    _seed_year(store, "2024")
    store.put_source("P1000R0", b"%PDF-fake", suffix=".pdf")
    store.write_paper_md("P1000R0", "# converted body")

    before = store.list_papers_for_year("2024")[0]
    assert before.source_file
    assert before.markdown_path

    with patch("mailing.scrape.fetch_all_mailings_for_year", side_effect=_fake_fetch):
        asyncio.run(
            run_mailing(["2024"], store, current_year="2026", force=True)
        )

    after = store.list_papers_for_year("2024")[0]
    assert after.source_file == before.source_file
    assert after.markdown_path == before.markdown_path
    assert after.title == "Refreshed Title"


def test_run_mailing_current_year_always_refetches(tmp_path: Path) -> None:
    """Force has no effect on the current year (already always re-fetched)."""
    store = SqliteBackend(tmp_path)
    _seed_year(store, "2026")

    with patch(
        "mailing.scrape.fetch_all_mailings_for_year", side_effect=_fake_fetch
    ) as fetch:
        result = asyncio.run(run_mailing(["2026"], store, current_year="2026"))

    fetch.assert_called_once_with("2026")
    assert result["succeeded"] == [{"year": "2026", "papers": 1}]
