#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Source location type for items extracted from a paper.

Stdlib only, no Pydantic. ``SourceLoc`` is the canonical in-memory shape
of a (line, start_char, end_char) span and lives at the storage layer
because that is where the underlying columns live (``ClaimRow``,
``EvidenceRow``, and ``RhetoricRow`` carry ``loc_line`` / ``loc_start``
/ ``loc_end`` columns).

Consumers (``dissect``, ``advocatus``, future tools) import this type
rather than redefining it. ``loc_from_row`` centralizes the
column-triple to ``SourceLoc`` reconstruction every consumer would
otherwise duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceLoc:
    """Text span of an extracted item in a paper.

    ``line`` is the 1-based line number in the paper markdown.
    ``start_char`` is the 0-based character offset where the extracted
    text begins on that line. ``end_char`` is ``start_char + len(text)``
    (exclusive end of the span).
    """

    line: int
    start_char: int
    end_char: int


def loc_from_row(row: Any) -> SourceLoc:
    """Build a ``SourceLoc`` from any object exposing ``loc_line`` /
    ``loc_start`` / ``loc_end`` attributes (``ClaimRow``, ``EvidenceRow``,
    ``RhetoricRow``).
    """
    return SourceLoc(
        line=row.loc_line,
        start_char=row.loc_start,
        end_char=row.loc_end,
    )
