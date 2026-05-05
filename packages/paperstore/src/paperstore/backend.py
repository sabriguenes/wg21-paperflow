#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""Abstract storage interface for paperflow artifacts.

All reads and writes done by the mailing, tomd, and paperlint packages go
through a :class:`StorageBackend` instance. :class:`SqliteBackend` is the
production implementation; an in-memory test double can drop in without
touching call sites.

Non-local backends must materialize bytes to a temp file inside
:meth:`StorageBackend.get_source_path` so callers can always treat the return
value as a local :class:`pathlib.Path`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StorageBackend(ABC):

    # ---- year-based mailing index -----------------------------------------

    @abstractmethod
    def has_year(self, year: str) -> bool:
        """Return True if at least one paper row exists for ``year``."""

    @abstractmethod
    def upsert_year(self, year: str, papers: list[dict]) -> list[dict]:
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
    def list_papers_for_year(self, year: str) -> list[dict]:
        """Return all paper rows for ``year``.

        Raises:
            paperstore.MissingMailingIndexError: no papers for that year.
        """

    @abstractmethod
    def list_all_paper_ids(self) -> list[str]:
        """Return all known paper IDs (uppercase). Order is unspecified."""

    @abstractmethod
    def resolve_year_for_paper(self, paper_id: str) -> tuple[str, dict] | None:
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
    def write_review_md(self, paper_id: str, markdown: str) -> Path:
        """Persist the review markdown. Atomic write. Returns path."""

    @abstractmethod
    def clear_review(self, paper_id: str) -> None:
        """Delete the review file and clear its path in the store.

        Called at the start of a review run so a crash does not leave
        a stale review from a previous run.
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
        self, paper_id: str, path: Path | str, *, intent: str | None = None
    ) -> None:
        """Stamp ``path`` as the converted markdown for ``paper_id`` in the index.

        Optionally also records ``intent`` (the YAML-front-matter signal
        from tomd). See :meth:`record_source` for the file-already-written
        use case.
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
    def get_meta(self, paper_id: str) -> dict:
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
    def get_review_path(self, paper_id: str) -> Path:
        """Return the local path to the review file.

        Raises:
            paperstore.MissingReviewError: no review for ``paper_id``.
        """

    # ---- legacy aliases ----

    def upsert_mailing_index(
        self, mailing_id: str, papers: list[dict]
    ) -> list[dict]:
        """Deprecated: use upsert_year."""
        year = mailing_id.split("-")[0] if "-" in mailing_id else mailing_id
        return self.upsert_year(year, papers)

    def list_mailing(self, mailing_id: str) -> list[dict]:
        """Deprecated: use list_papers_for_year."""
        year = mailing_id.split("-")[0] if "-" in mailing_id else mailing_id
        return self.list_papers_for_year(year)

    def resolve_mailing_for_paper(self, paper_id: str) -> tuple[str, dict] | None:
        """Deprecated: use resolve_year_for_paper."""
        return self.resolve_year_for_paper(paper_id)

    def list_paper_ids(self) -> list[str]:
        """Deprecated: use list_all_paper_ids."""
        return self.list_all_paper_ids()

    def patch_meta(self, paper_id: str, fields: dict) -> None:
        """Deprecated: internal implementation detail."""
        raise NotImplementedError(
            "patch_meta is not part of the public API. "
            "Use upsert_year or specific update methods."
        )
