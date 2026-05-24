#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Paperstore: storage abstraction for paperflow artifacts."""

from __future__ import annotations

from paperstore.backend import ClearedSet, PaperRow, StorageBackend, parse_authors_raw
from paperstore.errors import (
    InvalidPaperstoreUriError,
    InvalidSuffixError,
    MissingAgoraError,
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperError,
    MissingPaperMdError,
    MissingSourceError,
    PaperstoreError,
)
from paperstore.extract_rows import (
    AssayGapRow,
    AssayClaimRow,
    AssayConcessionRow,
    AssayEvidenceRow,
    AssayFindingRow,
    AssayThesisRow,
    CandidateRow,
    CaputCausaeRow,
    CitationAuditRow,
    ClaimRow,
    EvidenceRow,
    ExternalCitationRow,
    FindingRow,
    PaperCitationRow,
    QuestionRow,
    RhetoricRow,
)
from paperstore.factory import WORKSPACE_ENV_VAR, default_workspace_dir, from_uri
from paperstore.html_manifest import (
    HtmlImageEntry,
    HtmlImagesManifest,
    HtmlManifestError,
)
from paperstore.locs import SourceLoc, loc_from_row
from paperstore.progress import ProgressCallback as ProgressCallback
from paperstore.progress import ProgressEvent as ProgressEvent
from paperstore.sqlite_backend import SqliteBackend
from paperstore.stages import STAGES, STAGE_NAMES, failed_status, failed_stage
from paperstore.tools import PaperstoreTools

__all__ = [
    "ClearedSet",
    "HtmlImageEntry",
    "HtmlImagesManifest",
    "HtmlManifestError",
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
    "MissingAgoraError",
    "MissingMailingIndexError",
    "InvalidPaperstoreUriError",
    "InvalidSuffixError",
    "from_uri",
    "default_workspace_dir",
    "WORKSPACE_ENV_VAR",
    "ProgressCallback",
    "ProgressEvent",
    "SourceLoc",
    "loc_from_row",
    "STAGES",
    "STAGE_NAMES",
    "failed_status",
    "failed_stage",
    "AssayGapRow",
    "AssayClaimRow",
    "AssayConcessionRow",
    "AssayEvidenceRow",
    "AssayFindingRow",
    "AssayThesisRow",
    "CandidateRow",
    "CaputCausaeRow",
    "CitationAuditRow",
    "ClaimRow",
    "EvidenceRow",
    "ExternalCitationRow",
    "FindingRow",
    "PaperCitationRow",
    "QuestionRow",
    "RhetoricRow",
]
