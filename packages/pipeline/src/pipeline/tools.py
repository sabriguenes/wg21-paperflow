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

from typing import Callable

from paperstore.backend import StorageBackend

_SOURCE_START = "<<<SOURCE>>>"
_SOURCE_END = "<<<END_SOURCE>>>"


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
        return f"{header}\n{_SOURCE_START}\n{content}\n{_SOURCE_END}"

    read_paper.__name__ = f"read_paper_{pid.lower()}"
    return read_paper
