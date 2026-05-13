#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Source location type for items extracted from a paper.

Stdlib only, no Pydantic. ``SourceLoc`` is the canonical in-memory shape
of a (line, start_char, end_char) triple and lives at the storage layer
because that is where the underlying columns live (``ClaimRow``,
``EvidenceRow``, and ``MarkerRow`` carry ``loc_line`` / ``loc_start`` /
``loc_end`` columns plus optional ``merged_into_*`` columns).

Consumers (``dissect``, ``advocatus``, future tools) import this type
rather than redefining it. ``loc_from_row`` and ``merged_into_loc``
centralize the column-triple to ``SourceLoc`` reconstruction every
consumer would otherwise duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceLoc:
    """Position of an extracted item in a paper.

    ``line`` is the 1-based line number in the paper markdown.
    ``start_char`` is an ordinal disambiguator when multiple items share
    a line (the Nth item on the same line gets ``start_char = N - 1``);
    it is **not** a character offset. ``end_char`` is the character count
    of the source line.
    """

    line: int
    start_char: int
    end_char: int


def loc_from_row(row: Any) -> SourceLoc:
    """Build a ``SourceLoc`` from any object exposing ``loc_line`` /
    ``loc_start`` / ``loc_end`` attributes (``ClaimRow``, ``EvidenceRow``,
    ``MarkerRow``).
    """
    return SourceLoc(
        line=row.loc_line,
        start_char=row.loc_start,
        end_char=row.loc_end,
    )


def merged_into_loc(row: Any) -> SourceLoc | None:
    """Build the merged-into ``SourceLoc`` from a row with
    ``merged_into_line`` / ``merged_into_start`` / ``merged_into_end``
    columns, or ``None`` when the row is alive (no merge tombstone).
    """
    line = row.merged_into_line
    if line is None:
        return None
    return SourceLoc(
        line=line,
        start_char=row.merged_into_start,
        end_char=row.merged_into_end,
    )
