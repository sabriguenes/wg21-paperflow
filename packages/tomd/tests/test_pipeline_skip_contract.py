#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for the unified PipelineResult skip contract (issue01)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from tomd.api import convert_paper_full
from tomd.lib.pdf.pipeline import (
    PipelineResult,
    _enforce_skip_contract,
    run_pipeline,
)
from tomd.lib.pdf.types import SkipReason

_PROSE = (
    "This paragraph exists so the page passes the readability gate, "
    "which requires a meaningful amount of real text before structural "
    "classification or glyph injection runs in the pipeline. " * 3
)


def _save(doc: pymupdf.Document, tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    doc.save(str(path))
    return path


def _tiny_png(dim_px: int = 8) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, dim_px, dim_px), False)
    pix.clear_with(255)
    return pix.tobytes("png")


def _make_empty_pdf(tmp_path: Path) -> Path:
    """Placeholder path; ``fitz.open`` is mocked for zero-page PDFs."""
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"%PDF-1.4\n% zero-page stub for skip-contract test\n")
    return path


class _ZeroPageDoc:
    page_count = 0
    metadata: dict = {}

    def close(self) -> None:
        return None


def _make_slide_deck_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page(width=720, height=540)
        page.insert_textbox(pymupdf.Rect(40, 40, 680, 300), _PROSE, fontsize=10)
        page.insert_image(pymupdf.Rect(60, 60, 72, 72), stream=_tiny_png())
    path = _save(doc, tmp_path, "slides.pdf")
    doc.close()
    return path


def _make_standards_draft_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(200):
        page = doc.new_page(width=400, height=520)
        if i == 0:
            page.insert_textbox(pymupdf.Rect(40, 40, 320, 200), _PROSE, fontsize=10)
            page.insert_image(pymupdf.Rect(322, 120, 334, 132), stream=_tiny_png())
    path = _save(doc, tmp_path, "draft.pdf")
    doc.close()
    return path


def _make_unreadable_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=520)
    page.insert_textbox(pymupdf.Rect(40, 40, 360, 480), "/" * 500, fontsize=10)
    path = _save(doc, tmp_path, "unreadable.pdf")
    doc.close()
    return path


_SKIP_FIXTURES = {
    SkipReason.EMPTY_PDF: _make_empty_pdf,
    SkipReason.SLIDE_DECK: _make_slide_deck_pdf,
    SkipReason.STANDARDS_DRAFT: _make_standards_draft_pdf,
    SkipReason.UNREADABLE: _make_unreadable_pdf,
}


@pytest.mark.parametrize("reason", list(SkipReason))
def test_run_pipeline_skip_contract(
    reason: SkipReason, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    path = _SKIP_FIXTURES[reason](tmp_path)
    if reason is SkipReason.EMPTY_PDF:
        monkeypatch.setattr(
            "tomd.lib.pdf.pipeline.fitz.open",
            lambda _path: _ZeroPageDoc(),
        )
    result = run_pipeline(path)
    assert result.skipped is True
    assert result.skip_reason == reason
    assert result.md == ""
    assert result.images == []
    if reason is SkipReason.UNREADABLE:
        assert result.readable is False
    else:
        assert result.readable is True


@pytest.mark.parametrize("reason", list(SkipReason))
def test_convert_paper_full_skip_contract(
    reason: SkipReason, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    path = _SKIP_FIXTURES[reason](tmp_path)
    if reason is SkipReason.EMPTY_PDF:
        monkeypatch.setattr(
            "tomd.lib.pdf.pipeline.fitz.open",
            lambda _path: _ZeroPageDoc(),
        )
    converted = convert_paper_full("P0000R0", path, meta={})
    assert converted.skipped is True
    assert converted.skip_reason == reason
    assert converted.markdown == ""
    assert converted.images == []


def test_enforce_skip_contract_rejects_malformed_skip():
    bad = PipelineResult(skipped=True, skip_reason=None)
    with pytest.raises(AssertionError, match="skip_reason"):
        _enforce_skip_contract(bad)


def test_enforce_skip_contract_rejects_unreadable_without_skip():
    bad = PipelineResult(readable=False)
    with pytest.raises(AssertionError, match="skipped=True"):
        _enforce_skip_contract(bad)
