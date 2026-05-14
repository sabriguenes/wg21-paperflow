#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Unit tests for the pymupdf-backed PDF extractor."""

from __future__ import annotations

from dissect.pdf_extract import extract_pdf_text


def _make_simple_pdf(text: str) -> bytes:
    """Build a one-page PDF containing ``text``. Returns raw bytes."""
    import fitz
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), text)
        return doc.tobytes()
    finally:
        doc.close()


def _make_multi_page_pdf(lines_per_page: int, n_pages: int) -> bytes:
    """Build an ``n_pages``-page PDF, each page filled with text."""
    import fitz
    doc = fitz.open()
    try:
        for p in range(n_pages):
            page = doc.new_page()
            body = "\n".join(
                f"page {p} line {i} of some filler content"
                for i in range(lines_per_page)
            )
            page.insert_text((72, 72), body)
        return doc.tobytes()
    finally:
        doc.close()


def test_extract_pdf_text_happy_path():
    pdf = _make_simple_pdf("Hello from a PDF.")
    text = extract_pdf_text(pdf, max_length=8000)
    assert text is not None
    assert "Hello from a PDF" in text


def test_extract_pdf_text_empty_returns_none():
    import fitz
    doc = fitz.open()
    try:
        doc.new_page()  # blank page, no text
        pdf = doc.tobytes()
    finally:
        doc.close()
    assert extract_pdf_text(pdf, max_length=8000) is None


def test_extract_pdf_text_unopenable_returns_none():
    # pymupdf is lenient about junk: on some versions it raises, on
    # others it opens an empty doc. Both routes must produce None.
    assert extract_pdf_text(b"not a pdf", max_length=8000) is None


def test_extract_pdf_text_break_at_max_length():
    # Build a PDF large enough that the first page alone exceeds the
    # max_length cap. The loop should break after page 1, returning
    # text strictly longer than max_length (so the caller's outer
    # truncation check fires reliably).
    pdf = _make_multi_page_pdf(lines_per_page=50, n_pages=5)
    text = extract_pdf_text(pdf, max_length=100)
    assert text is not None
    assert len(text) > 100  # strict >, drives caller's truncation check

    # Sanity: text from the first page is present, text from the last
    # page is not (loop broke before reaching it).
    assert "page 0" in text
    assert "page 4" not in text
