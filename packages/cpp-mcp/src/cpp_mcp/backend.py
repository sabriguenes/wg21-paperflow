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


# ---------------------------------------------------------------------------
# Data rows
# ---------------------------------------------------------------------------


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
    is_deprecated: bool
    is_synopsis: bool


@dataclass(frozen=True)
class DraftInfo:
    """Metadata about an ingested draft version."""

    draft_tag: str
    ingested_at: str
    section_count: int
    git_sha: str | None
    standard_version: str | None
    version_note: str | None


@dataclass(frozen=True)
class IndexTermRow:
    """An index entry (general, library, grammar, header, etc.)."""

    draft_tag: str
    stable_label: str
    category: str
    term: str


@dataclass(frozen=True)
class MechanismRow:
    """A named language or library mechanism (e.g. overload resolution, ADL)."""

    draft_tag: str
    name: str
    category: str
    stable_label: str


@dataclass(frozen=True)
class GrammarRuleRow:
    """A grammar production rule."""

    draft_tag: str
    nonterminal: str
    stable_label: str
    raw_rule: str


@dataclass(frozen=True)
class DefinedTermRow:
    """A term with a normative definition."""

    draft_tag: str
    term: str
    stable_label: str
    definition_text: str


@dataclass(frozen=True)
class LibraryDeclRow:
    """A library declaration with its specification elements."""

    draft_tag: str
    stable_label: str
    declaration: str
    description: str
    preconditions: str | None = None
    effects: str | None = None
    postconditions: str | None = None
    returns: str | None = None
    throws: str | None = None
    mandates: str | None = None
    constraints: str | None = None
    complexity: str | None = None
    remarks: str | None = None


@dataclass(frozen=True)
class ParagraphRow:
    """A single normative paragraph within a section."""

    draft_tag: str
    stable_label: str
    paragraph_number: int
    raw_latex: str
    cleaned_text: str
    normative_force: str


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------


class StandardBackend(abc.ABC):
    """Storage abstraction for the C++ standard."""

    # -- schema / lifecycle -------------------------------------------------

    @abc.abstractmethod
    def create_schema(self) -> None:
        """Create tables and indexes if they do not exist."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release resources."""

    # -- draft ingestion ----------------------------------------------------

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
    def upsert_xrefs(
        self, draft_tag: str, xrefs: list[tuple[str, str]]
    ) -> None:
        """Store cross-reference pairs *(from_label, to_label)* for a draft."""

    @abc.abstractmethod
    def upsert_index_terms(
        self, draft_tag: str, terms: list[IndexTermRow]
    ) -> None:
        """Store index entries for a draft."""

    @abc.abstractmethod
    def upsert_mechanisms(
        self, draft_tag: str, mechanisms: list[MechanismRow]
    ) -> None:
        """Store named mechanism entries for a draft."""

    @abc.abstractmethod
    def upsert_grammar_rules(
        self, draft_tag: str, rules: list[GrammarRuleRow]
    ) -> None:
        """Store grammar production rules for a draft."""

    @abc.abstractmethod
    def upsert_defined_terms(
        self, draft_tag: str, terms: list[DefinedTermRow]
    ) -> None:
        """Store defined terms for a draft."""

    @abc.abstractmethod
    def upsert_library_declarations(
        self, draft_tag: str, decls: list[LibraryDeclRow]
    ) -> None:
        """Store library declarations for a draft."""

    @abc.abstractmethod
    def upsert_paragraphs(
        self, draft_tag: str, paragraphs: list[ParagraphRow]
    ) -> None:
        """Store individual paragraphs for a draft."""

    @abc.abstractmethod
    def atomic_replace_draft(
        self, staging_tag: str, real_tag: str
    ) -> None:
        """Rename *staging_tag* to *real_tag* atomically across all tables.

        Used to ingest into a temporary tag, then swap it into place so
        readers never see a half-written draft.
        """

    # -- section queries ----------------------------------------------------

    @abc.abstractmethod
    def lookup_section(
        self, stable_label: str, draft_tag: str | None = None
    ) -> SectionRow | None:
        """Return a single section by stable label, or ``None``."""

    @abc.abstractmethod
    def lookup_sections(
        self, labels: list[str], draft_tag: str | None = None
    ) -> list[SectionRow]:
        """Return sections for multiple stable labels (preserving order)."""

    @abc.abstractmethod
    def get_section_with_children(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[SectionRow]:
        """Return a section and all its descendants."""

    @abc.abstractmethod
    def get_ancestors(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[SectionRow]:
        """Return the ancestor chain from root to the section's parent."""

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

    # -- full-text search ---------------------------------------------------

    @abc.abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 10,
        chapter: str | None = None,
        draft_tag: str | None = None,
    ) -> list[SectionRow]:
        """Full-text search over cleaned_text."""

    # -- cross-references ---------------------------------------------------

    @abc.abstractmethod
    def get_references_from(
        self, label: str, draft_tag: str | None = None
    ) -> list[str]:
        """Return labels that *label* references (outgoing edges)."""

    @abc.abstractmethod
    def get_references_to(
        self, label: str, draft_tag: str | None = None
    ) -> list[str]:
        """Return labels that reference *label* (incoming edges)."""

    # -- index, mechanisms, grammar, definitions ----------------------------

    @abc.abstractmethod
    def search_index(
        self,
        term: str,
        category: str | None = None,
        draft_tag: str | None = None,
    ) -> list[IndexTermRow]:
        """Search the index for entries matching *term*."""

    @abc.abstractmethod
    def verify_mechanism(
        self, name: str, draft_tag: str | None = None
    ) -> list[MechanismRow]:
        """Look up a named mechanism and return matching entries."""

    @abc.abstractmethod
    def search_grammar(
        self, nonterminal: str, draft_tag: str | None = None
    ) -> GrammarRuleRow | None:
        """Return the grammar rule for *nonterminal*, or ``None``."""

    @abc.abstractmethod
    def lookup_definition(
        self, term: str, draft_tag: str | None = None
    ) -> list[DefinedTermRow]:
        """Return all definition sites for *term*, ordered by document position."""

    # -- library declarations -----------------------------------------------

    @abc.abstractmethod
    def lookup_declarations(
        self, pattern: str, draft_tag: str | None = None
    ) -> list[LibraryDeclRow]:
        """Return library declarations matching *pattern*."""

    # -- paragraphs ---------------------------------------------------------

    @abc.abstractmethod
    def lookup_paragraph(
        self,
        stable_label: str,
        paragraph_number: int,
        draft_tag: str | None = None,
    ) -> ParagraphRow | None:
        """Return a specific paragraph by label and number, or ``None``."""

    @abc.abstractmethod
    def get_paragraphs(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[ParagraphRow]:
        """Return all paragraphs for a section, ordered by number."""

    # -- draft metadata -----------------------------------------------------

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
        """Return the most recently published draft tag, or ``None``.

        Determined by sorting draft tags in descending order.
        """
