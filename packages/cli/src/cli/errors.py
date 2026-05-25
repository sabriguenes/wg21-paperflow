#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Error hierarchy for the paperflow CLI.

The CLI catches these at the entry layer and renders them as
human-readable stderr messages before exiting.
"""

from __future__ import annotations


class CliError(Exception):
    """Base class for paperflow-CLI-raised exceptions."""


class InvalidTargetError(CliError):
    """Raised when a CLI target is not a paper ID, year, or year-month."""


class EmptyTargetsError(CliError):
    """Raised when a command requires at least one target but received none."""


class MixedTargetsError(CliError):
    """Raised when a single invocation mixes paper IDs and years."""
