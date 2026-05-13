#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Abstract storage interface for paperflow artifacts.

All reads and writes done by the mailing, tomd, and cli packages go
through a :class:`StorageBackend` instance. :class:`SqliteBackend` is the
production implementation; an in-memory test double can drop in without
touching call sites.

Non-local backends must materialize bytes to a temp file inside
:meth:`StorageBackend.get_source_path` so callers can always treat the return
value as a local :class:`pathlib.Path`.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


def parse_authors_raw(raw: str | list) -> list[str]:
    """Deserialize an authors value from storage into a list of strings.

    Handles three shapes:

    - Already a ``list`` -- elements are coerced to ``str``.
    - A JSON array string (starts with ``[``) -- decoded via ``json.loads``,
      with a comma-split fallback on decode error. Non-list JSON results
      are wrapped in a single-element list.
    - A bare comma-separated string -- split and stripped.

    Returns an empty list for empty / falsy input.
    """
    if isinstance(raw, list):
        return [str(a) for a in raw]
    if not raw:
        return []
    if raw.startswith("["):
        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return [str(a) for a in result]
            return [str(result)]
        except (json.JSONDecodeError, ValueError):
            pass
    return [a.strip() for a in raw.split(",") if a.strip()]


@dataclass(frozen=True)
class PaperRow:
    """Immutable record for a paper row returned by storage backend read methods.

    ``authors`` is always a ``list[str]`` (deserialized from JSON or
    comma-separated storage). ``source_file`` and ``markdown_path`` are
    empty strings when the corresponding artifact has not been staged.
    """

    paper_id: str = ""
    year: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    target_group: str = ""
    intent: str = ""
    url: str = ""
    document_date: str = ""
    mailing_date: str = ""
    source_file: str = ""
    markdown_path: str = ""
    dissect_path: str = ""
    advocatus_path: str = ""
    line_count: int = 0


class StorageBackend(ABC):

    @property
    @abstractmethod
    def workspace_dir(self) -> Path:
        """Root directory of the workspace (used by ExtractStore, tools, etc.)."""

    # ---- year-based mailing index -----------------------------------------

    @abstractmethod
    def has_year(self, year: str) -> bool:
        """Return True if at least one paper row exists for ``year``."""

    @abstractmethod
    def upsert_year(self, year: str, papers: list[dict]) -> list[PaperRow]:
        """Insert or update all ``papers`` for ``year``.

        Each entry is matched by uppercased ``paper_id``. New rows are
        inserted; existing rows have their metadata fields overwritten
        (title, authors, target_group, url, document_date, mailing_date),
        while completion-state columns set by ``put_source`` /
        ``write_paper_md`` (``source_file``, ``markdown_path``) are
        preserved. Rows already present for ``year`` but absent from
        ``papers`` are retained, not deleted.

        Returns the full set of paper rows for ``year`` after merging,
        in unspecified order.
        """

    @abstractmethod
    def list_papers_for_year(self, year: str) -> list[PaperRow]:
        """Return all paper rows for ``year``.

        Raises:
            paperstore.MissingMailingIndexError: no papers for that year.
        """

    @abstractmethod
    def list_all_paper_ids(self) -> list[str]:
        """Return all known paper IDs (uppercase). Order is unspecified."""

    @abstractmethod
    def resolve_year_for_paper(self, paper_id: str) -> tuple[str, PaperRow] | None:
        """Find ``paper_id`` across all stored papers.

        Returns ``(year, paper_row)`` on success, ``None`` if not found.
        The match is case-insensitive.
        """

    # ---- writes -----------------------------------------------------------

    @abstractmethod
    def put_source(self, paper_id: str, content: bytes, *, suffix: str) -> Path:
        """Stage raw source bytes for ``paper_id``. Atomic write.

        ``suffix`` must start with a dot (``.pdf``, ``.html``). Returns the
        local path. Updates ``source_file`` in the store.
        """

    @abstractmethod
    def write_paper_md(self, paper_id: str, markdown: str) -> Path:
        """Persist the converted markdown. Atomic write. Returns path."""

    @abstractmethod
    def write_dissect_md(self, paper_id: str, markdown: str) -> Path:
        """Persist the dissect markdown. Atomic write. Returns path."""

    @abstractmethod
    def clear_dissect(self, paper_id: str) -> None:
        """Delete the dissect file and clear its path in the store.

        Called at the start of a dissect run so a crash does not leave
        a stale dissect from a previous run.
        """

    @abstractmethod
    def write_advocatus_md(self, paper_id: str, markdown: str) -> Path:
        """Persist the advocatus markdown (Relatio). Atomic write. Returns path."""

    @abstractmethod
    def clear_advocatus(self, paper_id: str) -> None:
        """Delete the advocatus file and clear its path in the store.

        Called at the start of an advocatus run so a crash does not leave
        a stale Relatio from a previous run.
        """

    @abstractmethod
    def write_intermediate(self, paper_id: str, name: str, payload: Any) -> Path:
        """Persist a labeled intermediate artifact (e.g. ``1-findings``).

        Returns the file path.
        """

    @abstractmethod
    def record_source(self, paper_id: str, path: Path | str) -> None:
        """Stamp ``path`` as the staged source file for ``paper_id`` in the index.

        For callers that already wrote the file by other means (e.g., a
        download worker that returns a Path). Atomically inserts the row
        if absent and sets ``source_file``. Does not touch the filesystem.
        """

    @abstractmethod
    def record_markdown(
        self, paper_id: str, path: Path | str, *, intent: str | None = None,
        line_count: int | None = None,
    ) -> None:
        """Stamp ``path`` as the converted markdown for ``paper_id`` in the index.

        Optionally also records ``intent`` (the YAML-front-matter signal
        from tomd) and ``line_count``. See :meth:`record_source` for the
        file-already-written use case.
        """

    @abstractmethod
    def reconcile(self) -> dict[str, int]:
        """Backfill DB rows from on-disk artifacts. Non-destructive.

        Scans the workspace for known artifact filenames (sources,
        markdowns) and fills the corresponding DB columns for any file
        that isn't currently indexed. Existing non-empty values are
        preserved.

        Returns counts of newly-indexed artifacts:
        ``{"sources": N, "markdowns": M}``.
        Useful as a recovery tool when the DB is lost or out of sync
        with the workspace, and as the basis for an admin/management
        command.
        """

    # ---- reads ------------------------------------------------------------

    @abstractmethod
    def get_meta(self, paper_id: str) -> PaperRow:
        """Return per-paper metadata as a dict.

        Raises:
            paperstore.MissingMetaError: no metadata for ``paper_id``.
        """

    @abstractmethod
    def get_source_path(self, paper_id: str) -> Path:
        """Return a local path to the staged source file.

        Raises:
            paperstore.MissingSourceError: source not staged.
        """

    @abstractmethod
    def get_paper_md(self, paper_id: str) -> str:
        """Return the converted markdown as a string.

        Raises:
            paperstore.MissingPaperMdError: markdown not written.
        """

    @abstractmethod
    def get_paper_md_path(self, paper_id: str) -> Path:
        """Return the canonical local path for the converted markdown.

        Does not check existence. Use :meth:`get_paper_md` to read content
        (which raises :class:`MissingPaperMdError` if not yet written). This
        accessor exists for callers that need a stable filesystem path
        before the file exists, such as file watchers.
        """

    @abstractmethod
    def get_dissect_path(self, paper_id: str) -> Path:
        """Return the local path to the dissect file.

        Raises:
            paperstore.MissingDissectError: no dissect for ``paper_id``.
        """

    @abstractmethod
    def get_advocatus_path(self, paper_id: str) -> Path:
        """Return the local path to the advocatus file (Relatio).

        Raises:
            paperstore.MissingAdvocatusError: no advocatus for ``paper_id``.
        """

    @abstractmethod
    def get_debug_md_path(self, paper_id: str, tool: str) -> Path:
        """Return the canonical path for a tool's per-paper debug transcript.

        File: ``paperstore/<pid>.<tool>.debug.md``. The path is returned
        whether or not the file exists; callers write to it or check
        ``.exists()`` themselves. ``tool`` is normalized to lowercase
        (e.g. ``"dissect"``, ``"advocatus"``); empty / whitespace-only
        ``tool`` raises ``ValueError``.
        """

    @abstractmethod
    def get_trace_md_path(self, paper_id: str, tool: str) -> Path:
        """Return the canonical path for a tool's per-paper pipeline trace.

        File: ``paperstore/<pid>.<tool>.trace.md``. Same semantics as
        :meth:`get_debug_md_path`.
        """

    @abstractmethod
    def list_years(self) -> list[tuple[str, int]]:
        """Return ``[(year, paper_count)]`` sorted by year."""

    @abstractmethod
    def list_papers_since(self, month: str) -> list[PaperRow]:
        """Return papers where ``mailing_date`` >= ``month``."""

    # ---- extract writes ---------------------------------------------------

    @abstractmethod
    def store_claims(self, paper_id: str, claims) -> None:
        """Replace all claims for ``paper_id``."""

    @abstractmethod
    def store_evidence(self, paper_id: str, evidence) -> None:
        """Replace all evidence for ``paper_id``."""

    @abstractmethod
    def store_paper_citations(self, paper_id: str, citations) -> None:
        """Replace paper citations for ``paper_id``."""

    @abstractmethod
    def store_external_citations(self, paper_id: str, externals) -> None:
        """Replace external citations for ``paper_id``."""

    @abstractmethod
    def store_questions(self, paper_id: str, claims, support_map) -> None:
        """Store questions for unsupported claims of ``paper_id``."""

    @abstractmethod
    def store_markers(self, paper_id: str, markers) -> None:
        """Replace rhetorical markers for ``paper_id``."""

    @abstractmethod
    def store_caput_causae(self, paper_id: str, thesis: str) -> None:
        """Store or replace the caput causae thesis for ``paper_id``."""

    @abstractmethod
    def store_citation_audit(self, paper_id: str, audits) -> None:
        """Replace citation audit entries for ``paper_id``."""

    # ---- extract reads ----------------------------------------------------

    @abstractmethod
    def get_claims(self, paper_id: str) -> list[ClaimRow]:
        """Return all claims for ``paper_id``."""

    @abstractmethod
    def get_evidence(self, paper_id: str) -> list[EvidenceRow]:
        """Return all evidence for ``paper_id``."""

    @abstractmethod
    def get_paper_citations(self, paper_id: str) -> list[PaperCitationRow]:
        """Return all paper citations for ``paper_id``."""

    @abstractmethod
    def get_external_citations(self, paper_id: str) -> list[ExternalCitationRow]:
        """Return all external citations for ``paper_id``."""

    @abstractmethod
    def get_questions(self, paper_id: str) -> list[QuestionRow]:
        """Return all questions for ``paper_id``."""

    @abstractmethod
    def get_markers(self, paper_id: str) -> list[MarkerRow]:
        """Return all rhetorical markers for ``paper_id``."""

    @abstractmethod
    def get_caput_causae(self, paper_id: str) -> CaputCausaeRow | None:
        """Return the caput causae for ``paper_id``, or None."""

    @abstractmethod
    def get_citation_audit(self, paper_id: str) -> list[CitationAuditRow]:
        """Return all citation audit entries for ``paper_id``."""
