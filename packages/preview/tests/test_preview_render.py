#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for ``preview.render``: paper image inlining + cache invalidation.

The full ``render_markdown`` path is integration-tested manually via
``paperflow preview <PID>`` (scrivener is heavy and out of scope here).
These tests exercise the pre-scrivener rewrite and the data-URL cache
key fix in isolation.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from paperstore import SqliteBackend
from preview.render import (
    _image_data_url,
    _image_data_url_cached,
    _rewrite_paper_image_refs,
)


# 1x1 transparent PNG.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


@pytest.fixture
def backend(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty data-url cache so mtime keying is
    actually exercised."""
    _image_data_url_cached.cache_clear()
    yield
    _image_data_url_cached.cache_clear()


# ---- _rewrite_paper_image_refs ----------------------------------------------


def test_rewrite_inlines_paper_image_as_html_img(backend: SqliteBackend):
    """The whole point of the rewrite: scrivener would strip the markdown
    img's src; raw HTML img passes through."""
    backend.write_paper_image("P3556R0", 3, 1, "png", _PNG_BYTES)
    md = (
        "Body before.\n\n"
        "![Figure 1: Hello World!](p3556r0-fig3-1.png)\n\n"
        "Body after.\n"
    )
    out = _rewrite_paper_image_refs(md, backend, "P3556R0")
    assert "![Figure 1: Hello World!]" not in out
    assert "<img src=\"data:image/png;base64," in out
    assert 'alt="Figure 1: Hello World!"' in out


def test_rewrite_empty_alt(backend: SqliteBackend):
    backend.write_paper_image("P1", 12, 1, "png", _PNG_BYTES)
    md = "![](p1-fig12-1.png)"
    out = _rewrite_paper_image_refs(md, backend, "P1")
    assert "<img src=\"data:image/png;base64," in out
    assert 'alt=""' in out


def test_rewrite_escapes_html_special_chars_in_alt(backend: SqliteBackend):
    """Quote, ampersand, and angle brackets must not break out of the
    attribute or inject HTML."""
    backend.write_paper_image("P1", 1, 1, "png", _PNG_BYTES)
    md = '![<b>"quoted" & x</b>](p1-fig1-1.png)'
    out = _rewrite_paper_image_refs(md, backend, "P1")
    # Quote, ampersand, and angle brackets must be escaped in the attr.
    assert "&lt;b&gt;" in out
    assert "&quot;quoted&quot;" in out
    assert "&amp; x" in out
    # The raw < should not appear inside the alt attr value.
    assert 'alt="<b>"' not in out


def test_rewrite_leaves_other_paper_refs_alone(backend: SqliteBackend):
    """Filenames whose pid does not match the current paper survive the
    rewrite (and get stripped by scrivener downstream, same as today -
    no regression on cross-paper refs)."""
    backend.write_paper_image("P1", 1, 1, "png", _PNG_BYTES)
    md = "![ours](p1-fig1-1.png)\n\n![not ours](p9999r0-fig5-1.png)"
    out = _rewrite_paper_image_refs(md, backend, "P1")
    # Our paper inlined as HTML
    assert "<img src=\"data:image/png" in out
    assert 'alt="ours"' in out
    # The other paper's ref is untouched markdown
    assert "![not ours](p9999r0-fig5-1.png)" in out


def test_rewrite_leaves_non_paper_refs_alone(backend: SqliteBackend):
    """A ref to ``foo.png`` (not the paperstore convention) is left as
    markdown so scrivener handles it (and strips it - same as today)."""
    md = "![logo](some-other-logo.png)"
    out = _rewrite_paper_image_refs(md, backend, "P1")
    assert out == md


def test_rewrite_when_image_file_missing(backend: SqliteBackend):
    """A ref to a paperstore-shaped filename that doesn't exist on disk
    leaves the markdown alone (no spurious empty data URL)."""
    md = "![ghost](p1-fig1-1.png)"
    out = _rewrite_paper_image_refs(md, backend, "P1")
    assert out == md


def test_rewrite_handles_multiple_refs(backend: SqliteBackend):
    backend.write_paper_image("P1", 0, 1, "png", _PNG_BYTES)
    backend.write_paper_image("P1", 0, 2, "jpeg", _PNG_BYTES)
    md = (
        "![one](p1-fig0-1.png)\n\nmid\n\n![two](p1-fig0-2.jpeg)\n"
    )
    out = _rewrite_paper_image_refs(md, backend, "P1")
    assert out.count("<img src=\"data:image/png;base64,") == 1
    assert out.count("<img src=\"data:image/jpeg;base64,") == 1
    assert 'alt="one"' in out
    assert 'alt="two"' in out


def test_rewrite_pid_case_insensitive(backend: SqliteBackend):
    """Caller can pass the pid in any case; the filename match is on
    the lowercased form."""
    backend.write_paper_image("P3556R0", 3, 1, "png", _PNG_BYTES)
    md = "![cap](p3556r0-fig3-1.png)"
    out = _rewrite_paper_image_refs(md, backend, "P3556R0")
    assert "<img src=\"data:image/png;base64," in out


# ---- cache invalidation -----------------------------------------------------


def test_image_data_url_invalidates_when_mtime_changes(
    backend: SqliteBackend, tmp_path: Path,
):
    """A re-convert that overwrites the image file with new bytes must
    cause the next preview render to return the new base64, not the
    cached old one.
    """
    backend.write_paper_image("P1", 1, 1, "png", _PNG_BYTES)
    path = backend.get_paper_image_path("P1", 1, 1, "png")
    first = _image_data_url(path)
    assert first is not None
    assert base64.b64encode(_PNG_BYTES).decode() in first

    # Overwrite with different bytes, bumping mtime past the cache key.
    new_bytes = _PNG_BYTES + b"\x00" * 16
    path.write_bytes(new_bytes)
    import os
    new_mtime = path.stat().st_mtime + 1.0
    os.utime(path, (new_mtime, new_mtime))

    second = _image_data_url(path)
    assert second is not None
    assert second != first
    assert base64.b64encode(new_bytes).decode() in second


def test_image_data_url_returns_none_for_missing_file(tmp_path: Path):
    missing = tmp_path / "no-such-file.png"
    assert _image_data_url(missing) is None


# ---- caption-duplication gate (plan section 6) ------------------------------


def test_rewrite_produces_bare_img_not_figure(backend: SqliteBackend):
    """Caption-duplication gate. The rewrite emits a bare ``<img>``
    tag, NOT a ``<figure><figcaption>`` wrap.

    The "keep both" decision in plan section 1.2 (caption appears as
    image alt text AND as a body paragraph) relies on ``<img alt>``
    being invisible to the browser - the user sees the caption
    exactly once via the body paragraph. A ``<figure><figcaption>``
    wrap would render the alt text visibly below the image,
    duplicating the body paragraph in the rendered output and
    forcing dedupe-at-emit instead.

    Verified once visually for P3556R0 ("Figure 1: Hello World!"
    rendered once in the preview iframe). This test pins the
    invariant against accidental regressions.
    """
    backend.write_paper_image("P1", 3, 1, "png", _PNG_BYTES)
    md = "![Figure 1: Hello World!](p1-fig3-1.png)"
    out = _rewrite_paper_image_refs(md, backend, "P1")
    assert "<figure" not in out
    assert "<figcaption" not in out
    assert out.startswith('<img src="data:image/png;base64,')
    assert 'alt="Figure 1: Hello World!"' in out
