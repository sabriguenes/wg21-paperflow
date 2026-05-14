#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Shared target-handling utilities for CLI commands."""

from __future__ import annotations

import re

from paperstore.backend import StorageBackend

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def resolve_pid(target: str, backend: StorageBackend) -> str:
    """Normalize a paper ID target to uppercase.

    Accepts full IDs (P4003R3) or short forms (p4003). For short forms
    without a revision suffix, looks up the latest revision in the store.
    """
    pid = target.strip().upper()

    if "R" in pid and pid.split("R")[-1].isdigit():
        return pid

    result = backend.resolve_year_for_paper(pid)
    if result is not None:
        return result[1].paper_id

    return pid
