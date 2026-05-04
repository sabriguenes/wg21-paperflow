#
# Copyright (c) 2026 Will Pak (will@cppalliance.org)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Logging wiring for the paperflow CLI.

Two handlers live here:

* ``configure_paperlint_console_logging`` attaches a stderr ``StreamHandler``
  whose level is driven by the CLI ``-v`` count (0=WARNING, 1=INFO, 2+=DEBUG).
  This is the normal path for terminal usage.
* ``configure_paperlint_file_logging_if_needed`` adds an optional file handler
  for callers that want to capture a structured log to disk, driven by the
  ``PAPERLINT_LOG_FILE`` / ``PAPERLINT_LOG_TO_WORKSPACE`` env vars.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_LOGGER_NAME = "cli"
_pwl_file_handler: logging.FileHandler | None = None
_pwl_console_handler: logging.StreamHandler | None = None


def get_cli_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def _level_for_verbosity(verbosity: int) -> int:
    if verbosity >= 2:
        return logging.DEBUG
    if verbosity == 1:
        return logging.INFO
    return logging.WARNING


def configure_paperlint_console_logging(verbosity: int = 0) -> None:
    """Attach a stderr stream handler to the cli logger. Idempotent per process."""
    global _pwl_console_handler
    if _pwl_console_handler is not None:
        return
    level = _level_for_verbosity(verbosity)
    h = logging.StreamHandler(stream=sys.stderr)
    h.setLevel(level)
    h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _pwl_console_handler = h
    log = get_cli_logger()
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
        path = str(Path(workspace) / "paperflow.log")
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(p, encoding="utf-8")
    h.setLevel(logging.DEBUG)
    h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [paperflow] %(message)s")
    )
    _pwl_file_handler = h
    log = get_cli_logger()
    log.setLevel(logging.DEBUG)
    log.addHandler(h)
