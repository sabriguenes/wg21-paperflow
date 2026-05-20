#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Line-number formatting utilities."""

from __future__ import annotations


def format_numbered_lines(paper_lines: list[str], start_line: int, end_line: int) -> str:
    """Format paper lines with line-number prefix for LLM input.

    Collapses runs of 2+ blank lines to a single sentinel.
    """
    result = []
    blank_run = 0
    for i in range(start_line - 1, min(end_line, len(paper_lines))):
        line = paper_lines[i]
        line_num = i + 1
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                result.append(f"{line_num:>6}|")
        else:
            blank_run = 0
            result.append(f"{line_num:>6}| {line}")
    return "\n".join(result)
