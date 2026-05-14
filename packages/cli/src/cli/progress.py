#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Shared rich progress-bar helper for paperflow CLI commands.

Provides ``make_progress_handler`` which returns a context manager and a
``ProgressCallback`` backed by a rich progress bar. When stderr is not a
terminal (CI, captured subprocess, file redirect), the context manager is
a no-op and the callback is ``None`` so callers do not need to
special-case non-TTY environments.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, ContextManager

from paperstore.progress import ProgressCallback, ProgressEvent


def make_progress_handler(
    label: str = "Working...",
) -> tuple[ContextManager[Any], ProgressCallback | None]:
    """Build ``(ctx, on_progress)`` for a paperflow command.

    Use as::

        ctx, on_progress = make_progress_handler("Extracting")
        with ctx:
            asyncio.run(dissect_paper(pid, backend, on_progress=on_progress))

    The rich spinner animates continuously between step events. The
    context manager must stay open for the entire operation.
    """
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Column

    console = Console(stderr=True)
    if not console.is_terminal:
        return nullcontext(), None

    progress = Progress(
        SpinnerColumn(style="green"),
        TextColumn("[bold]{task.description}", table_column=Column(min_width=8)),
        BarColumn(complete_style="green", finished_style="bold green"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    task_id: int | None = None

    def handler(ev: ProgressEvent) -> None:
        nonlocal task_id
        if task_id is None:
            task_id = progress.add_task(ev.name, total=ev.total)
        progress.update(task_id, completed=ev.step, total=ev.total, description=ev.name)

    return progress, handler
