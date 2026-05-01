#
# Copyright (c) 2026 Will Pak (will@cppalliance.org)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Logging wiring for paperlint.

Three handlers live here:

* ``configure_paperlint_console_logging`` attaches a stderr ``StreamHandler``
  whose level is driven by the CLI ``-v`` count (0=WARNING, 1=INFO, 2+=DEBUG).
  This is the normal path for terminal usage.
* ``configure_paperlint_file_logging_if_needed`` adds an optional file handler
  for callers that want to capture a structured log to disk, driven by the
  ``PAPERLINT_LOG_FILE`` / ``PAPERLINT_LOG_TO_WORKSPACE`` env vars.
* ``rich_console_handler`` is a context manager that temporarily swaps the
  console handler for a ``RichHandler`` sharing a given ``Console``, so log
  output coordinates with an active Rich Progress bar.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import Console

_LOGGER_NAME = "paperlint"
_pwl_file_handler: logging.FileHandler | None = None
_pwl_console_handler: logging.StreamHandler | None = None


def get_paperlint_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def _level_for_verbosity(verbosity: int) -> int:
    if verbosity >= 2:
        return logging.DEBUG
    if verbosity == 1:
        return logging.INFO
    return logging.WARNING


def configure_paperlint_console_logging(verbosity: int = 0) -> None:
    """Attach a stderr stream handler to the paperlint logger. Idempotent per process."""
    global _pwl_console_handler
    if _pwl_console_handler is not None:
        return
    level = _level_for_verbosity(verbosity)
    h = logging.StreamHandler(stream=sys.stderr)
    h.setLevel(level)
    h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _pwl_console_handler = h
    log = get_paperlint_logger()
    if log.level == logging.NOTSET or log.level > level:
        log.setLevel(level)
    log.addHandler(h)


def configure_paperlint_file_logging_if_needed(workspace: Path | None) -> None:
    """Add a file handler when env says so. First successful configuration wins for the process.

    * ``PAPERLINT_LOG_FILE`` — if set, log to that path.
    * Else, if ``PAPERLINT_LOG_TO_WORKSPACE`` is truthy and *workspace* is set, use
      ``<workspace>/paperlint.log``.
    """
    global _pwl_file_handler
    if _pwl_file_handler is not None:
        return
    path: str | None = None
    raw = os.environ.get("PAPERLINT_LOG_FILE", "").strip()
    if raw:
        path = raw
    elif workspace and os.environ.get("PAPERLINT_LOG_TO_WORKSPACE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        path = str(Path(workspace) / "paperlint.log")
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(p, encoding="utf-8")
    h.setLevel(logging.DEBUG)
    h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [paperlint] %(message)s")
    )
    _pwl_file_handler = h
    log = get_paperlint_logger()
    log.setLevel(logging.DEBUG)
    log.addHandler(h)


@contextmanager
def rich_console_handler(console: Console) -> Iterator[None]:
    """Temporarily route paperlint log output through a shared Rich Console.

    While a Rich ``Progress`` bar is active, the plain stderr handler would
    write directly to the original ``sys.stderr``, bypassing Rich's display
    coordination.  This context manager swaps it for a ``RichHandler`` that
    shares the same ``Console`` as the progress bar, then restores the
    original handler on exit.  No-op when no console handler is configured.
    """
    global _pwl_console_handler
    if _pwl_console_handler is None:
        yield
        return

    from rich.logging import RichHandler

    log = get_paperlint_logger()
    old = _pwl_console_handler

    rh = RichHandler(
        console=console,
        show_time=False,
        show_level=False,
        show_path=False,
        markup=False,
    )
    rh.setLevel(old.level)
    rh.setFormatter(old.formatter)

    log.removeHandler(old)
    log.addHandler(rh)
    _pwl_console_handler = rh
    try:
        yield
    finally:
        log.removeHandler(rh)
        log.addHandler(old)
        _pwl_console_handler = old
