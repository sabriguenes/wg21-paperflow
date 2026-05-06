#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""File-system watcher for the converted markdown file.

Wraps :class:`watchdog.observers.Observer` and exposes a
publish/subscribe interface. Each Server-Sent-Events handler subscribes
to get its own queue; when the markdown file is written (created,
moved into place, or modified) the watcher pushes a ``"reload"``
notification onto every subscriber queue.

The watchdog observer runs in its own thread, so all subscriber state
is guarded by a lock. Atomic-rename writes typically fire several
events in quick succession; a short debounce timer collapses them into
a single notification.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

# Atomic-rename writes fire created+moved+modified events back-to-back.
# 200 ms collapses them into a single SSE push without making refresh
# feel laggy.
_DEBOUNCE_SECONDS = 0.2

_RELEVANT_EVENT_TYPES = frozenset({"created", "modified", "moved"})


class MarkdownWatcher:
    """Observe a single markdown file and fan out change notifications.

    Watches the parent directory (so events still fire when the file
    is created via atomic rename) and filters down to the target name.
    """

    def __init__(
        self,
        md_path: Path,
        *,
        debounce_seconds: float = _DEBOUNCE_SECONDS,
    ) -> None:
        self._md_path = md_path
        self._debounce_seconds = debounce_seconds

        self._observer = Observer()
        handler = _Handler(self._on_event, target_name=md_path.name)
        self._observer.schedule(handler, str(md_path.parent), recursive=False)

        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[str]] = []
        self._timer: threading.Timer | None = None
        self._stopped = False

    @property
    def target_path(self) -> Path:
        return self._md_path

    def start(self) -> None:
        self._observer.start()
        logger.info("watching %s", self._md_path)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._observer.stop()
        self._observer.join()

    def __enter__(self) -> "MarkdownWatcher":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def notify(self) -> None:
        """Fan out a reload event to every current subscriber."""
        with self._lock:
            subs = list(self._subscribers)
        logger.info("notify: fan-out to %d subscriber(s)", len(subs))
        for q in subs:
            q.put_nowait("reload")

    def _on_event(self) -> None:
        logger.info("fs event for %s", self._md_path.name)
        with self._lock:
            if self._stopped:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self.notify)
            self._timer.daemon = True
            self._timer.start()


class _Handler(FileSystemEventHandler):
    """Filter watchdog events to a single filename in the watched dir."""

    def __init__(self, callback, *, target_name: str) -> None:
        super().__init__()
        self._callback = callback
        self._target = target_name

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if event.event_type not in _RELEVANT_EVENT_TYPES:
            return
        candidates = (event.src_path, getattr(event, "dest_path", ""))
        if any(p and self._matches(p) for p in candidates):
            self._callback()

    def _matches(self, path: str | bytes) -> bool:
        if isinstance(path, bytes):
            path = path.decode("utf-8", errors="replace")
        return Path(path).name == self._target
