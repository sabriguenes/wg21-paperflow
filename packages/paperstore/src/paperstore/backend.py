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
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paperstore.extract_rows import (
    CaputCausaeRow,
    CitationAuditRow,
    ClaimRow,
    EvidenceRow,
    ExternalCitationRow,
    RhetoricRow,
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
    disposition: str = ""
    previous_version: str = ""
    source_file: str = ""
    markdown_path: str = ""
    dissect_path: str = ""
    agora_path: str = ""
    assay_path: str = ""
    line_count: int = 0
    status: int = 0
    error: str = ""


@dataclass(frozen=True)
class ClearedSet:
    """Record of which downstream pipelines a ``clear_downstream_outputs`` call wiped.

    The CLI consumes this to build a per-paper summary line such as
    ``P3556R0 (agora)`` after a re-convert. ``bool(set)`` is True iff
    anything was cleared (so the CLI can skip empty summaries).
    """

    agora: bool = False
    assay: bool = False

    def __bool__(self) -> bool:
        return self.agora or self.assay

    def names(self) -> list[str]:
        """Return the pipeline names that were cleared, in stable order."""
        out: list[str] = []
        if self.agora:
            out.append("agora")
        if self.assay:
            out.append("assay")
        return out


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

    # ---- mailing metadata -------------------------------------------------

    @abstractmethod
    def upsert_mailing_label(self, mailing_id: str, label: str) -> None:
        """Insert or update the descriptive label for a mailing.

        ``label`` is the human-readable suffix from the open-std heading
        (e.g. ``"post-Croydon"``). Empty string clears the label.
        """

    @abstractmethod
    def get_mailing_label(self, mailing_id: str) -> str:
        """Return the descriptive label for ``mailing_id``, or ``""``."""

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
    def write_agora_json(self, paper_id: str, payload: Any) -> Path:
        """Persist the agora thread blueprint as JSON. Atomic write. Returns path.

        ``payload`` is serialised with ``json.dumps(..., indent=2,
        ensure_ascii=False)``. Pass a ``Thread.model_dump(mode='json')``
        dict.
        """

    @abstractmethod
    def read_agora_json(self, paper_id: str) -> Any:
        """Return the agora JSON as a parsed Python object.

        Raises:
            paperstore.MissingAgoraError: no agora JSON stored.
        """

    @abstractmethod
    def clear_agora(self, paper_id: str) -> None:
        """Delete the agora file and clear its path in the store.

        Called at the start of an agora run so a crash does not leave
        a stale thread blueprint from a previous run.
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

    # ---- image artifacts and downstream invalidation ---------------------

    @abstractmethod
    def get_paper_image_path(
        self, paper_id: str, page: int, index: int, ext: str
    ) -> Path:
        """Return the canonical path for an extracted paper image.

        File name: ``<pid>-fig{page}-{index}.{ext}`` under the
        ``paperstore/`` subdirectory, with ``pid`` lowercased. Does not
        check existence. ``page=0`` is the "HTML, no page concept"
        sentinel used by the mailing-side HTML image fetcher.
        """

    @abstractmethod
    def write_paper_image(
        self,
        paper_id: str,
        page: int,
        index: int,
        ext: str,
        data: bytes,
    ) -> Path:
        """Persist extracted image bytes atomically. Returns the final path.

        Caller is the CLI orchestration in :mod:`cli.convert` (for PDF
        sources) or the mailing fetcher (for HTML sources). Library
        extractors return bytes; persistence is a CLI concern.
        """

    @abstractmethod
    def iter_paper_image_paths(self, paper_id: str) -> Iterator[Path]:
        """Yield existing image paths for ``paper_id`` in deterministic order.

        Order is alphanumeric on filename, which equals ``(page, index)``
        ascending by construction. Yields nothing when the paper has no
        extracted images.
        """

    @abstractmethod
    def delete_paper_images(self, paper_id: str) -> int:
        """Delete all extracted images for ``paper_id``. Returns count removed.

        Called by ``paperflow convert`` before writing the new image set
        so a re-convert leaves no stale figures from a previous run.
        """

    @abstractmethod
    def get_html_images_manifest_path(self, paper_id: str) -> Path:
        """Return the canonical path for the mailing -> tomd HTML manifest.

        File name: ``<pid>.html-images.json``. Does not check existence.
        The schema is :class:`paperstore.html_manifest.HtmlImagesManifest`.
        """

    @abstractmethod
    def clear_downstream_outputs(self, paper_id: str) -> ClearedSet:
        """Invalidate agora artifacts for ``paper_id``.

        Called by ``paperflow convert`` after a re-convert that changed
        the markdown content. This method:

        - Deletes the ``.agora.json`` file (if present) and clears its
          path column.

        Does not touch ``paper.md`` or extracted images. Returns a
        :class:`ClearedSet` describing which pipelines had artifacts
        to clear.
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
    def try_read_paper_md(self, paper_id: str) -> str | None:
        """Return the converted markdown, or None if not yet written.

        Non-raising alternative to :meth:`get_paper_md`, used by the
        convert orchestration to perform the byte-equality check that
        gates downstream invalidation. A first conversion returns None;
        a re-convert producing the same bytes leaves agora artifacts
        intact.
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
    def get_agora_path(self, paper_id: str) -> Path:
        """Return the local path to the agora JSON (thread blueprint).

        Raises:
            paperstore.MissingAgoraError: no agora JSON for ``paper_id``.
        """

    @abstractmethod
    def get_debug_md_path(self, paper_id: str, tool: str = "") -> Path:
        """Return the canonical path for a paper's unified debug transcript.

        File: ``paperstore/<pid>.debug.md``. When ``tool`` is non-empty,
        the path is ``paperstore/<pid>.debug.<tool>.md``. The path is
        returned whether or not the file exists; callers write to it or
        check ``.exists()`` themselves.
        """

    @abstractmethod
    def get_trace_md_path(self, paper_id: str, tool: str = "") -> Path:
        """Return the canonical path for a paper's unified pipeline trace.

        File: ``paperstore/<pid>.trace.md``. When ``tool`` is non-empty,
        the path is ``paperstore/<pid>.trace.<tool>.md``. Same semantics
        as :meth:`get_debug_md_path`.
        """

    @abstractmethod
    def list_years(self) -> list[tuple[str, int]]:
        """Return ``[(year, paper_count)]`` sorted by year."""

    @abstractmethod
    def list_papers_since(self, month: str) -> list[PaperRow]:
        """Return papers where ``mailing_date`` >= ``month``."""

    # ---- status / settings ------------------------------------------------

    @abstractmethod
    def advance_status(self, paper_id: str, from_status: int, to_status: int) -> bool:
        """CAS: advance only if current status matches from_status. Clears error."""

    @abstractmethod
    def fail_paper(self, paper_id: str, stage: int, error: str) -> None:
        """Mark paper as failed at the given stage and store the error."""

    @abstractmethod
    def get_setting(self, key: str) -> str | None:
        """Return the value for ``key`` from the settings table, or None."""

    @abstractmethod
    def set_setting(self, key: str, value: str) -> None:
        """Insert or replace a setting value."""

    # ---- assay writes ---------------------------------------------------------

    @abstractmethod
    def store_assay_claims(self, paper_id: str, claims) -> None:
        """Replace assay claim entries for ``paper_id``."""

    @abstractmethod
    def store_assay_evidence(self, paper_id: str, evidence) -> None:
        """Replace assay evidence entries for ``paper_id``."""

    @abstractmethod
    def store_assay_concessions(self, paper_id: str, concessions) -> None:
        """Replace assay concession entries for ``paper_id``."""

    @abstractmethod
    def store_assay_gaps(self, paper_id: str, gaps) -> None:
        """Replace assay gap entries for ``paper_id``."""

    @abstractmethod
    def store_assay_thesis(self, paper_id: str, thesis) -> None:
        """Store or replace assay thesis for ``paper_id``."""

    @abstractmethod
    def store_assay_findings(self, paper_id: str, findings) -> None:
        """Replace assay finding entries for ``paper_id``."""

    # ---- assay reads ----------------------------------------------------------

    @abstractmethod
    def get_assay_claims(self, paper_id: str) -> list:
        """Return all assay claims for ``paper_id``."""

    @abstractmethod
    def get_assay_evidence(self, paper_id: str) -> list:
        """Return all assay evidence for ``paper_id``."""

    @abstractmethod
    def get_assay_concessions(self, paper_id: str) -> list:
        """Return all assay concessions for ``paper_id``."""

    @abstractmethod
    def get_assay_gaps(self, paper_id: str) -> list:
        """Return all assay gaps for ``paper_id``."""

    @abstractmethod
    def get_assay_thesis(self, paper_id: str):
        """Return the assay thesis for ``paper_id``, or None."""

    @abstractmethod
    def get_assay_findings(self, paper_id: str) -> list:
        """Return all assay findings for ``paper_id``."""

    @abstractmethod
    def store_assay_asks(self, paper_id: str, asks) -> None:
        """Replace assay ask entries for ``paper_id``."""

    @abstractmethod
    def get_assay_asks(self, paper_id: str) -> list:
        """Return all assay asks for ``paper_id``."""

    @abstractmethod
    def store_assay_pids(self, paper_id: str, pids) -> None:
        """Replace assay paper-number references for ``paper_id``."""

    @abstractmethod
    def get_assay_pids(self, paper_id: str) -> list:
        """Return all assay paper-number references for ``paper_id``."""

    @abstractmethod
    def store_assay_urls(self, paper_id: str, urls) -> None:
        """Replace assay standalone URLs for ``paper_id``."""

    @abstractmethod
    def get_assay_urls(self, paper_id: str) -> list:
        """Return all assay standalone URLs for ``paper_id``."""

    @abstractmethod
    def store_assay_strengths(self, paper_id: str, strengths) -> None:
        """Replace assay strength entries for ``paper_id``."""

    @abstractmethod
    def get_assay_strengths(self, paper_id: str) -> list:
        """Return all assay strengths for ``paper_id``."""

    @abstractmethod
    def store_assay_checklist(self, paper_id: str, items) -> None:
        """Replace assay SD-4 checklist entries for ``paper_id``."""

    @abstractmethod
    def get_assay_checklist(self, paper_id: str) -> list:
        """Return all assay checklist items for ``paper_id``."""

    @abstractmethod
    def store_assay_compounds(self, paper_id: str, compounds) -> None:
        """Replace assay compound dynamic entries for ``paper_id``."""

    @abstractmethod
    def get_assay_compounds(self, paper_id: str) -> list:
        """Return all assay compounds for ``paper_id``."""

    @abstractmethod
    def store_assay_synthesis(self, paper_id: str, synthesis) -> None:
        """Store or replace assay synthesis (verdict, counts) for ``paper_id``."""

    @abstractmethod
    def get_assay_synthesis(self, paper_id: str):
        """Return assay synthesis for ``paper_id``, or None."""

    @abstractmethod
    def write_assay_md(self, paper_id: str, markdown: str):
        """Write assay report markdown and update DB path."""

    @abstractmethod
    def clear_assay(self, paper_id: str) -> None:
        """Clear all assay artifacts for ``paper_id``."""

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
    def store_questions(self, paper_id: str, claims, verdicts) -> None:
        """Store questions for unproven claims of ``paper_id``."""

    @abstractmethod
    def store_rhetoric(self, paper_id: str, rhetoric) -> None:
        """Replace rhetoric items for ``paper_id``."""

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
    def get_rhetoric(self, paper_id: str) -> list[RhetoricRow]:
        """Return all rhetoric items for ``paper_id``."""

    @abstractmethod
    def get_caput_causae(self, paper_id: str) -> CaputCausaeRow | None:
        """Return the caput causae for ``paper_id``, or None."""

    @abstractmethod
    def get_citation_audit(self, paper_id: str) -> list[CitationAuditRow]:
        """Return all citation audit entries for ``paper_id``."""
