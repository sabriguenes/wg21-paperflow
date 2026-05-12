#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Paperstore: storage abstraction for paperflow artifacts."""

from __future__ import annotations

from paperstore.backend import PaperRow, StorageBackend, parse_authors_raw
from paperstore.errors import (
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperError,
    MissingPaperMdError,
    MissingReviewError,
    MissingSourceError,
    PaperstoreError,
)
from paperstore.extract_rows import (
    CaputCausaeRow,
    CitationAuditRow,
    ClaimRow,
    EvidenceRow,
    ExternalCitationRow,
    MarkerRow,
    PaperCitationRow,
    QuestionRow,
)
from paperstore.factory import WORKSPACE_ENV_VAR, default_workspace_dir, from_uri
from paperstore.progress import ProgressCallback as ProgressCallback
from paperstore.progress import ProgressEvent as ProgressEvent
from paperstore.sqlite_backend import SqliteBackend
from paperstore.tools import PaperstoreTools

__all__ = [
    "PaperRow",
    "StorageBackend",
    "parse_authors_raw",
    "SqliteBackend",
    "PaperstoreTools",
    "PaperstoreError",
    "MissingPaperError",
    "MissingMetaError",
    "MissingSourceError",
    "MissingPaperMdError",
    "MissingReviewError",
    "MissingMailingIndexError",
    "from_uri",
    "default_workspace_dir",
    "WORKSPACE_ENV_VAR",
    "ProgressCallback",
    "ProgressEvent",
    "CaputCausaeRow",
    "CitationAuditRow",
    "ClaimRow",
    "EvidenceRow",
    "ExternalCitationRow",
    "MarkerRow",
    "PaperCitationRow",
    "QuestionRow",
]
