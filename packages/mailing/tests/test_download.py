#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for ``mailing.download.download_paper``.

``httpx.AsyncClient`` is monkeypatched so tests run hermetically.
download_paper returns ``(content, suffix)`` and never writes to disk; the
caller persists via ``StorageBackend.put_source``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mailing import download as md


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def _make_mock_client(content: bytes) -> MagicMock:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_FakeResponse(content))
    return mock_client


def test_download_paper_returns_bytes_and_pdf_suffix():
    pdf_bytes = b"%PDF-1.7\nhello"

    with patch("mailing.download.httpx.AsyncClient", return_value=_make_mock_client(pdf_bytes)):
        result = asyncio.run(md.download_paper(
            "p1234r0",
            source_url="https://www.open-std.org/.../p1234r0.pdf",
        ))
    assert result == (pdf_bytes, ".pdf")


def test_download_paper_normalizes_htm_to_html():
    html_bytes = b"<html>ok</html>"

    with patch("mailing.download.httpx.AsyncClient", return_value=_make_mock_client(html_bytes)):
        result = asyncio.run(md.download_paper(
            "n5000",
            source_url="https://www.open-std.org/.../n5000.htm",
        ))
    assert result == (html_bytes, ".html")


def test_download_paper_returns_none_for_empty_url():
    assert asyncio.run(md.download_paper("p1", source_url="")) is None


def test_download_paper_rejects_unknown_suffix():
    with pytest.raises(ValueError, match="must end with"):
        asyncio.run(md.download_paper("p1", source_url="https://x/paper.docx"))
