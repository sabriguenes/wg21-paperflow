#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""PDF text extraction for the web_fetch tool.

Registered into ``WebResearcher.binary_extractors`` so dissect's
``web_fetch`` calls can resolve PDF-served citations. ``web_tools``
itself stays free of pymupdf (AGPL) by design; the extractor lives
here because dissect is already inside the paperflow AGPL surface
via tomd.
"""

from __future__ import annotations

from typing import cast


def extract_pdf_text(content: bytes, max_length: int) -> str | None:
    """Extract plain text from PDF bytes.

    Returns ``None`` for empty / image-only / unopenable PDFs. The
    caller treats ``None`` the same as a missing extraction and emits
    the standard fetch error.
    """
    import fitz  # pymupdf; lazy-import keeps the cost off other dissect paths

    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:  # broad: pymupdf's C backend raises a mix of types
        return None
    try:
        parts: list[str] = []
        total = 0
        for page in doc:
            # ``get_text("text")`` returns a str at runtime; the type stub
            # is broader (it also covers ``"dict"`` / ``"json"`` modes).
            text = cast(str, page.get_text("text") or "")
            parts.append(text)
            total += len(text)
            # Strict ``>`` so when we stop early due to size, the
            # returned text is strictly longer than max_length. The
            # caller's outer truncation check (``len(text) > max_length``)
            # then fires reliably and the ``[Content truncated]`` marker
            # reaches the LLM. ``>=`` would silently truncate on an
            # exact boundary hit with no marker.
            if total > max_length:
                break
        return "".join(parts) or None
    finally:
        doc.close()
