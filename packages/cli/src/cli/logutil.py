#
# Copyright (c) 2026 Will Pak (will@cppalliance.org)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Logging wiring for the paperflow CLI.

``configure_console_logging`` attaches a stderr ``StreamHandler``
whose level is driven by the CLI ``-v`` count (0=WARNING, 1=INFO, 2+=DEBUG).
This is the normal path for terminal usage.
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "cli"
_console_handler: logging.StreamHandler | None = None


def get_cli_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def _level_for_verbosity(verbosity: int) -> int:
    if verbosity >= 2:
        return logging.DEBUG
    if verbosity == 1:
        return logging.INFO
    return logging.WARNING


def configure_console_logging(verbosity: int = 0) -> None:
    """Attach a stderr stream handler to the cli logger. Idempotent per process."""
    global _console_handler
    if _console_handler is not None:
        return
    level = _level_for_verbosity(verbosity)
    h = logging.StreamHandler(stream=sys.stderr)
    h.setLevel(level)
    h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _console_handler = h
    log = get_cli_logger()
    if log.level == logging.NOTSET or log.level > level:
        log.setLevel(level)
    log.addHandler(h)
