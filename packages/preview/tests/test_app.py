#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Smoke tests for the preview Flask app.

We bypass the real scrivener renderer in these tests so the suite stays
fast and free of the heavy WG21 style toolchain. The end-to-end render
path is exercised manually via ``uv run preview <PID>``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paperstore import SqliteBackend
from preview.app import create_app
from preview.watcher import MarkdownWatcher


PID = "P1234R0"


@pytest.fixture
def backend(tmp_path: Path) -> SqliteBackend:
    store = SqliteBackend(tmp_path)
    store.upsert_year("2026", [{"paper_id": PID, "title": "Test Paper"}])
    store.put_source(PID, b"%PDF-1.7\nstub", suffix=".pdf")
    return store


@pytest.fixture
def watcher(backend: SqliteBackend) -> MarkdownWatcher:
    # Build the watcher but do not start the observer thread; the tests
    # don't need filesystem events and starting an observer leaves a
    # background thread alive across the suite.
    return MarkdownWatcher(backend.get_paper_md_path(PID))


def test_index_renders_iframes(backend, watcher):
    app = create_app(backend, PID, watcher)
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/source" in body
    assert "/markdown" in body
    assert PID in body
    assert "Test Paper" in body


def test_source_returns_staged_pdf(backend, watcher):
    app = create_app(backend, PID, watcher)
    client = app.test_client()
    resp = client.get("/source")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data == b"%PDF-1.7\nstub"


def test_markdown_returns_not_yet_placeholder(backend, watcher):
    app = create_app(backend, PID, watcher)
    client = app.test_client()
    resp = client.get("/markdown")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "not converted" in body.lower()
    assert PID in body


def test_markdown_renders_when_converted(backend, watcher, monkeypatch):
    backend.write_paper_md(PID, "# Hello\n\nbody\n")
    # Stub out the real scrivener renderer; the route should hand it the
    # file's content and pass through the result.
    monkeypatch.setattr(
        "preview.app.render_markdown",
        lambda md, **kw: f"<html><body>RENDERED:{md.strip()}</body></html>",
    )
    app = create_app(backend, PID, watcher)
    client = app.test_client()
    resp = client.get("/markdown")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert b"RENDERED:# Hello" in resp.data


def test_index_falls_back_to_pid_when_meta_missing(tmp_path: Path):
    # No upsert_year, so get_meta raises and the title falls back to pid.
    store = SqliteBackend(tmp_path)
    watcher = MarkdownWatcher(store.get_paper_md_path(PID))
    app = create_app(store, PID, watcher)
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    assert PID in resp.get_data(as_text=True)
