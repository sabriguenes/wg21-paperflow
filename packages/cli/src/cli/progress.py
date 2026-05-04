#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Shared rich progress-bar helper for paperflow CLI commands.

Returns a context manager and two callbacks suitable for passing to
``jobs.run_*`` job functions as ``on_total`` and ``on_progress``. When
stdout is not a terminal (CI, captured subprocess, file redirect), the
context manager is a no-op and the callbacks are ``None`` so callers do
not need to special-case non-TTY environments.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable, ContextManager


def progress_callbacks(
    label: str,
) -> tuple[ContextManager[Any], Callable[[int], None] | None, Callable[[dict], None] | None]:
    """Build ``(ctx, on_total, on_progress)`` for a paperflow command.

    Use as::

        ctx, on_total, on_progress = progress_callbacks("Converting")
        with ctx:
            asyncio.run(run_convert(..., on_total=on_total, on_progress=on_progress))
    """
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    console = Console()
    if not console.is_terminal:
        return nullcontext(), None, None

    progress = Progress(
        SpinnerColumn(style="green"),
        TextColumn("[bold]{task.description}"),
        BarColumn(complete_style="green", finished_style="bold green"),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    task_id = progress.add_task(label, total=None, start=False)

    def on_total(n: int) -> None:
        progress.update(task_id, total=n)
        progress.start_task(task_id)

    def on_progress(_result: dict) -> None:
        progress.advance(task_id)

    return progress, on_total, on_progress
