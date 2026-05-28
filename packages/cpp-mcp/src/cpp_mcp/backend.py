#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Abstract backend for the C++ standard section store.

Concrete implementations live in separate modules (sqlite_backend, and
eventually a Postgres backend). All search and MCP tool code depends
only on this ABC.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class SectionRow:
    """A section as returned from the store."""

    draft_tag: str
    stable_label: str
    section_number: str | None
    title: str
    depth: int
    parent_label: str | None
    chapter_file: str
    raw_latex: str
    cleaned_text: str
    paragraph_count: int


@dataclass(frozen=True)
class DraftInfo:
    """Metadata about an ingested draft version."""

    draft_tag: str
    ingested_at: str
    section_count: int
    git_sha: str | None


class StandardBackend(abc.ABC):
    """Storage abstraction for C++ standard sections."""

    @abc.abstractmethod
    def create_schema(self) -> None:
        """Create tables and indexes if they do not exist."""

    @abc.abstractmethod
    def upsert_draft(
        self,
        draft_tag: str,
        sections: list[SectionRow],
        git_sha: str | None = None,
    ) -> None:
        """Insert or replace all sections for *draft_tag*.

        Removes any existing rows for the tag, inserts the new ones,
        and updates the ``drafts`` metadata table. Must be atomic.
        """

    @abc.abstractmethod
    def lookup_section(
        self, stable_label: str, draft_tag: str | None = None
    ) -> SectionRow | None:
        """Return a single section by stable label, or ``None``."""

    @abc.abstractmethod
    def get_section_with_children(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[SectionRow]:
        """Return a section and all its descendants."""

    @abc.abstractmethod
    def list_chapters(self, draft_tag: str | None = None) -> list[SectionRow]:
        """Return all depth-0 sections (chapter headings)."""

    @abc.abstractmethod
    def list_sections(
        self,
        chapter: str | None = None,
        depth: int | None = None,
        draft_tag: str | None = None,
    ) -> list[SectionRow]:
        """Browse sections, optionally filtered by chapter file and depth."""

    @abc.abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 10,
        chapter: str | None = None,
        draft_tag: str | None = None,
    ) -> list[SectionRow]:
        """Full-text search over cleaned_text."""

    @abc.abstractmethod
    def list_drafts(self) -> list[DraftInfo]:
        """Return metadata for every ingested draft version."""

    @abc.abstractmethod
    def diff_section(
        self,
        stable_label: str,
        from_draft: str,
        to_draft: str,
    ) -> tuple[SectionRow | None, SectionRow | None]:
        """Return both versions of a section for comparison."""

    @abc.abstractmethod
    def default_draft_tag(self) -> str | None:
        """Return the most recently published draft tag, or ``None``."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release resources."""
