#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""End-to-end: mailing.download -> tomd.api.convert -> paperstore artifacts.

Stubs ``httpx.AsyncClient`` so the test runs hermetically against a tomd fixture PDF.
This is the only cross-package test in the workspace; per-package suites cover
their own surfaces.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mailing import download as mailing_download
from paperstore.testing import store  # noqa: F401  (re-exports pytest fixture)
from tomd.api import convert_paper


FIXTURE_PDF = (
    Path(__file__).resolve().parent.parent
    / "packages" / "tomd" / "tests" / "fixtures" / "golden" / "p1112r4.pdf"
)


class _FakeResp:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


def _make_mock_client(content: bytes) -> AsyncMock:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_FakeResp(content))
    return mock_client


def test_end_to_end_convert(store):
    paper_id = "P1112R4"
    year = "2026"
    pdf_bytes = FIXTURE_PDF.read_bytes()

    store.upsert_year(
        year,
        [
            {
                "paper_id": paper_id,
                "title": "Test fixture paper",
                "authors": ["A. Author"],
                "subgroup": "EWG",
            }
        ],
    )

    with patch("mailing.download.httpx.AsyncClient", return_value=_make_mock_client(pdf_bytes)):
        fetched = asyncio.run(mailing_download.download_paper(
            paper_id,
            source_url="https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2026/p1112r4.pdf",
        ))
    assert fetched is not None
    content, suffix = fetched
    source_path = store.put_source(paper_id, content, suffix=suffix)

    meta = store.get_meta(paper_id)
    markdown, _prompts, _intent = convert_paper(paper_id, source_path, meta)
    md_path = store.write_paper_md(paper_id, markdown)

    papers_dir = store.workspace_dir / "paperstore"
    stem = paper_id.lower()
    assert source_path == papers_dir / f"{stem}.pdf"
    assert source_path.is_file()
    assert source_path.read_bytes() == pdf_bytes

    assert md_path == papers_dir / f"{stem}.md"
    assert md_path.is_file()
    assert md_path.read_text(encoding="utf-8").strip(), "convert_paper produced empty markdown"

    # Verify both paths recorded in DB.
    db_meta = store.get_meta(paper_id)
    assert db_meta["source_file"] == str(source_path)
    assert db_meta["markdown_path"] == str(md_path)
