#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Flask application factory for the side-by-side preview server."""

from __future__ import annotations

import logging
import mimetypes
import queue
import threading
from pathlib import Path
from typing import Iterator

from flask import Flask, Response, render_template, send_file
from paperstore import (
    MissingMetaError,
    MissingPaperMdError,
    StorageBackend,
)

from preview.render import render_markdown
from preview.watcher import MarkdownWatcher

logger = logging.getLogger(__name__)

# Heartbeat keeps proxies and idle browsers from dropping the SSE
# connection. 15 s is below the default Werkzeug/nginx idle timeout.
_SSE_HEARTBEAT_SECONDS = 15.0

# Single source of truth for the SSE event name; the browser side
# pulls the same value via the rendered template.
_RELOAD_EVENT = "reload"


def create_app(
    backend: StorageBackend,
    paper_id: str,
    watcher: MarkdownWatcher,
) -> Flask:
    """Build a Flask app pinned to a single paper id."""
    pid = paper_id.strip().upper()
    title = _resolve_title(backend, pid)

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    md_cache = _MarkdownCache()

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            paper_id=pid,
            title=title,
            reload_event=_RELOAD_EVENT,
        )

    @app.get("/source")
    def source():
        path = backend.get_source_path(pid)
        return send_file(path, mimetype=_mime_for(path))

    @app.get("/markdown")
    def markdown():
        md_path = backend.get_paper_md_path(pid)
        try:
            stat = md_path.stat()
        except FileNotFoundError:
            return render_template("not_yet.html", paper_id=pid), 200

        try:
            html = md_cache.get_or_render(md_path, stat.st_mtime_ns, stat.st_size)
        except MissingPaperMdError:
            return render_template("not_yet.html", paper_id=pid), 200
        return Response(html, mimetype="text/html")

    @app.get("/events")
    def events() -> Response:
        resp = Response(_sse_stream(watcher), mimetype="text/event-stream")
        # Defensive headers for SSE: keep proxies (and overzealous
        # browsers) from buffering or caching the stream.
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Connection"] = "keep-alive"
        return resp

    return app


def _resolve_title(backend: StorageBackend, pid: str) -> str:
    try:
        meta = backend.get_meta(pid)
    except MissingMetaError:
        return pid
    return meta.get("title") or pid


def _mime_for(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


class _MarkdownCache:
    """Single-slot cache keyed on (mtime_ns, size) so refreshes don't
    re-run scrivener when the file hasn't changed.

    Concurrent /markdown requests serialize on the lock, which is fine
    for the local preview server (one or two tabs).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[int, int] | None = None
        self._html: str = ""

    def get_or_render(self, path: Path, mtime_ns: int, size: int) -> str:
        key = (mtime_ns, size)
        with self._lock:
            if self._key == key:
                return self._html
            content = path.read_text(encoding="utf-8")
            self._html = render_markdown(content)
            self._key = key
            return self._html


def _sse_stream(watcher: MarkdownWatcher) -> Iterator[str]:
    """Yield Server-Sent Events for one subscriber.

    Sends an initial comment so the browser sees the connection open,
    then a reload event for every notification, with a heartbeat
    comment in between to keep the connection alive.
    """
    q = watcher.subscribe()
    try:
        yield ": connected\n\n"
        while True:
            try:
                msg = q.get(timeout=_SSE_HEARTBEAT_SECONDS)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            yield f"event: {_RELOAD_EVENT}\ndata: {msg}\n\n"
    finally:
        watcher.unsubscribe(q)
