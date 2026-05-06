#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Unit tests for MarkdownWatcher pub/sub mechanics.

The actual watchdog observer is exercised end-to-end via manual smoke
testing. Here we only verify the in-process subscription and fan-out
contract by calling ``notify()`` directly.
"""

from __future__ import annotations

from pathlib import Path

from preview.watcher import MarkdownWatcher


def test_subscribe_receives_notifications(tmp_path: Path):
    watcher = MarkdownWatcher(tmp_path / "p1234r0.md")
    q = watcher.subscribe()
    watcher.notify()
    watcher.notify()
    assert q.get_nowait() == "reload"
    assert q.get_nowait() == "reload"


def test_unsubscribe_stops_notifications(tmp_path: Path):
    watcher = MarkdownWatcher(tmp_path / "p1234r0.md")
    q = watcher.subscribe()
    watcher.unsubscribe(q)
    watcher.notify()
    assert q.qsize() == 0


def test_multiple_subscribers_all_receive(tmp_path: Path):
    watcher = MarkdownWatcher(tmp_path / "p1234r0.md")
    q1 = watcher.subscribe()
    q2 = watcher.subscribe()
    watcher.notify()
    assert q1.get_nowait() == "reload"
    assert q2.get_nowait() == "reload"
