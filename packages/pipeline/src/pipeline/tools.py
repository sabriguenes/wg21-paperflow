#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Scoped tools for LLM sub-agents.

``make_read_paper_tool`` creates a tool function bound to one paper's
markdown content. The agent can browse incrementally - read the table
of contents, jump to a section, find a quoted passage - without the
full document entering context at once.

Security: no path parameter, no filesystem access. The tool is bound
to one paper's content at creation time. The agent can only read what
we gave it.
"""

from __future__ import annotations

import os
from typing import Callable

from paperstore.backend import StorageBackend

_SOURCE_TAG = os.environ.get("WG21_SOURCE_TAG", "AX9K7P")
_SOURCE_START = f"<<<{_SOURCE_TAG}>>>"
_SOURCE_END = f"<<<END_{_SOURCE_TAG}>>>"


def source_tag() -> str:
    """Return the configured stable source tag."""
    return _SOURCE_TAG


def source_start() -> str:
    """Return the configured source start delimiter."""
    return _SOURCE_START


def source_end() -> str:
    """Return the configured source end delimiter."""
    return _SOURCE_END


def _escape_source_delimiters(content: str) -> str:
    """Prevent source content from forging framework delimiters."""
    return (
        content
        .replace(_SOURCE_START, f"<<\\<{_SOURCE_TAG}>>>")
        .replace(_SOURCE_END, f"<<\\<END_{_SOURCE_TAG}>>>")
    )


def wrap_source(content: str) -> str:
    """Wrap untrusted source material in configured source delimiters."""
    escaped = _escape_source_delimiters(content)
    return f"{_SOURCE_START}\n{escaped}\n{_SOURCE_END}"


def make_read_paper_tool(
    pid: str,
    backend: StorageBackend,
    *,
    max_lines: int = 500,
) -> Callable:
    """Create a read tool scoped to one paper's markdown.

    Returns a function suitable for ``agent.tool_plain(fn)``. The
    function reads lines from the paper's stored markdown, clamped
    to ``max_lines`` per call. Returns the content wrapped in
    source delimiters for prompt injection defense.
    """
    md = backend.get_paper_md(pid)
    lines = md.splitlines()
    total = len(lines)

    def read_paper(start_line: int = 1, num_lines: int = 100) -> str:
        """Read lines from the cited paper.

        Args:
            start_line: 1-indexed line number to start reading from.
            num_lines: number of lines to read (max 500).

        Returns:
            The requested lines with a position header, wrapped in
            source delimiters.
        """
        clamped = min(num_lines, max_lines)
        start_idx = max(0, start_line - 1)
        chunk = lines[start_idx : start_idx + clamped]
        end_line = start_idx + len(chunk)
        header = f"[lines {start_idx + 1}-{end_line} of {total}]"
        content = "\n".join(chunk)
        return f"{header}\n{wrap_source(content)}"

    read_paper.__name__ = f"read_paper_{pid.lower()}"
    return read_paper
