#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""Error hierarchy for paperstore lookups.

Readers raise a subclass of :class:`MissingPaperError` when the requested
artifact is absent; ``upsert``/``put`` methods do not raise on missing
predecessors (they stage data regardless of prior state).
"""

from __future__ import annotations


class PaperstoreError(Exception):
    """Base class for paperstore-raised exceptions."""


class MissingPaperError(PaperstoreError):
    """Parent class for all per-paper missing-artifact errors."""


class MissingMetaError(MissingPaperError):
    """Raised when no metadata row is stored for the paper."""


class MissingSourceError(MissingPaperError):
    """Raised when no downloaded source file is staged for the paper."""


class MissingPaperMdError(MissingPaperError):
    """Raised when no converted markdown is stored for the paper."""


class MissingMailingIndexError(PaperstoreError):
    """Raised when the requested mailing index has never been upserted."""
