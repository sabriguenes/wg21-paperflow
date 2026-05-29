#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Ingestion pipeline: clone cplusplus/draft, parse LaTeX, load into backend.

Supports atomic ingestion (stage-then-rename) so the MCP server never
sees partially ingested data. Skips re-ingestion when the git SHA has
not changed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from cpp_mcp.backend import (
    DefinedTermRow,
    GrammarRuleRow,
    IndexTermRow,
    LibraryDeclRow,
    MechanismRow,
    ParagraphRow,
    SectionRow,
    StandardBackend,
)
from cpp_mcp.parser import Section, parse_directory
from cpp_mcp.versions import resolve_version

log = logging.getLogger(__name__)

DRAFT_REPO_URL = "https://github.com/cplusplus/draft.git"


def _section_to_row(section: Section, draft_tag: str) -> SectionRow:
    return SectionRow(
        draft_tag=draft_tag,
        stable_label=section.stable_label,
        section_number=section.section_number,
        title=section.title,
        depth=section.depth,
        parent_label=section.parent_label,
        chapter_file=section.chapter_file,
        raw_latex=section.raw_latex,
        cleaned_text=section.cleaned_text,
        paragraph_count=section.paragraph_count,
        is_deprecated=section.is_deprecated,
        is_synopsis=section.is_synopsis,
    )


def _clone_draft(tag: str, dest: Path) -> str | None:
    """Clone cplusplus/draft at a specific tag/branch and return the git SHA."""
    log.info("Cloning cplusplus/draft at '%s' into %s", tag, dest)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", tag, DRAFT_REPO_URL, str(dest)],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(dest),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _collect_extracted_data(
    sections: list[Section],
    draft_tag: str,
) -> tuple[
    list[tuple[str, str]],
    list[IndexTermRow],
    list[MechanismRow],
    list[GrammarRuleRow],
    list[DefinedTermRow],
    list[LibraryDeclRow],
    list[ParagraphRow],
]:
    """Collect all extracted metadata from parsed sections."""
    all_xrefs: list[tuple[str, str]] = []
    all_index_terms: list[IndexTermRow] = []
    all_mechanisms: list[MechanismRow] = []
    all_grammar_rules: list[GrammarRuleRow] = []
    all_defined_terms: list[DefinedTermRow] = []
    all_library_decls: list[LibraryDeclRow] = []
    all_paragraphs: list[ParagraphRow] = []

    seen_grammar: set[str] = set()
    seen_defined: set[tuple[str, str]] = set()

    for section in sections:
        for target in section.xrefs:
            all_xrefs.append((section.stable_label, target))

        for category, term in section.index_terms:
            all_index_terms.append(IndexTermRow(
                draft_tag=draft_tag,
                stable_label=section.stable_label,
                category=category,
                term=term,
            ))

        for category, name in section.mechanisms:
            all_mechanisms.append(MechanismRow(
                draft_tag=draft_tag,
                name=name,
                category=category,
                stable_label=section.stable_label,
            ))

        for nt, rule in section.grammar_rules:
            if nt not in seen_grammar:
                seen_grammar.add(nt)
                all_grammar_rules.append(GrammarRuleRow(
                    draft_tag=draft_tag,
                    nonterminal=nt,
                    stable_label=section.stable_label,
                    raw_rule=rule,
                ))

        for term_name in section.defined_terms:
            key = (term_name, section.stable_label)
            if key not in seen_defined:
                seen_defined.add(key)
                all_defined_terms.append(DefinedTermRow(
                    draft_tag=draft_tag,
                    term=term_name,
                    stable_label=section.stable_label,
                    definition_text=section.cleaned_text[:500],
                ))

        for decl in section.library_declarations:
            all_library_decls.append(LibraryDeclRow(
                draft_tag=draft_tag,
                stable_label=section.stable_label,
                declaration=decl.declaration,
                description=decl.description,
                preconditions=decl.preconditions,
                effects=decl.effects,
                postconditions=decl.postconditions,
                returns=decl.returns,
                throws=decl.throws,
                mandates=decl.mandates,
                constraints=decl.constraints,
                complexity=decl.complexity,
                remarks=decl.remarks,
            ))

        for para in section.paragraphs:
            all_paragraphs.append(ParagraphRow(
                draft_tag=draft_tag,
                stable_label=section.stable_label,
                paragraph_number=para.number,
                raw_latex=para.raw_latex,
                cleaned_text=para.cleaned_text,
                normative_force=para.normative_force,
            ))

    return (
        all_xrefs,
        all_index_terms,
        all_mechanisms,
        all_grammar_rules,
        all_defined_terms,
        all_library_decls,
        all_paragraphs,
    )


def ingest_from_directory(
    backend: StandardBackend,
    source_dir: Path,
    draft_tag: str,
    git_sha: str | None = None,
    *,
    atomic: bool = True,
) -> int:
    """Parse a local source directory and load sections into the backend.

    When *atomic* is True (default), ingests into a staging tag then
    atomically renames to the real tag. Returns the number of sections.
    """
    log.info("Parsing LaTeX from %s", source_dir)
    t0 = time.monotonic()
    sections = parse_directory(source_dir)
    parse_time = time.monotonic() - t0
    log.info("Parsed %d sections in %.1fs", len(sections), parse_time)

    from collections import Counter
    label_counts = Counter(s.stable_label for s in sections)
    duplicates = {label: count for label, count in label_counts.items() if count > 1}
    if duplicates:
        log.warning(
            "Draft '%s' has %d duplicate stable labels: %s",
            draft_tag, len(duplicates),
            ", ".join(f"[{label}] x{cnt}" for label, cnt in sorted(duplicates.items())),
        )

    version, note = resolve_version(draft_tag)

    if atomic:
        staging_tag = f"_staging_{draft_tag}_{int(time.time())}"
    else:
        staging_tag = draft_tag

    rows = [_section_to_row(s, staging_tag) for s in sections]
    backend.upsert_draft(
        staging_tag, rows,
        git_sha=git_sha,
        standard_version=version,
        version_note=note,
    )

    (xrefs, index_terms, mechanisms, grammar_rules,
     defined_terms, library_decls, paragraphs) = _collect_extracted_data(
        sections, staging_tag,
    )

    log.info(
        "Extracted: %d xrefs, %d index terms, %d mechanisms, %d grammar rules, "
        "%d defined terms, %d library decls, %d paragraphs",
        len(xrefs), len(index_terms), len(mechanisms), len(grammar_rules),
        len(defined_terms), len(library_decls), len(paragraphs),
    )

    backend.upsert_xrefs(staging_tag, xrefs)
    backend.upsert_index_terms(staging_tag, index_terms)
    backend.upsert_mechanisms(staging_tag, mechanisms)
    backend.upsert_grammar_rules(staging_tag, grammar_rules)
    backend.upsert_defined_terms(staging_tag, defined_terms)
    backend.upsert_library_declarations(staging_tag, library_decls)
    backend.upsert_paragraphs(staging_tag, paragraphs)

    if atomic and staging_tag != draft_tag:
        backend.atomic_replace_draft(staging_tag, draft_tag)
        log.info("Atomic swap: %s -> %s", staging_tag, draft_tag)

    log.info("Ingested %d sections for draft '%s'", len(rows), draft_tag)
    return len(rows)


def ingest_from_git(
    backend: StandardBackend,
    tag: str,
    *,
    atomic: bool = True,
) -> int:
    """Clone cplusplus/draft at *tag*, parse, and load into backend.

    Skips ingestion if the git SHA has not changed since the last ingest.
    """
    tmp_dir = tempfile.mkdtemp(prefix="cpp-mcp-")
    try:
        clone_dir = Path(tmp_dir) / "draft"
        git_sha = _clone_draft(tag, clone_dir)

        existing_drafts = backend.list_drafts()
        for d in existing_drafts:
            if d.draft_tag == tag and d.git_sha and d.git_sha == git_sha:
                log.info(
                    "Draft '%s' already at SHA %s, skipping re-ingestion",
                    tag, git_sha[:12],
                )
                return 0

        source_dir = clone_dir / "source"
        if not source_dir.is_dir():
            raise FileNotFoundError(
                f"No source/ directory found in cloned repo at {clone_dir}"
            )
        return ingest_from_directory(
            backend, source_dir, tag, git_sha=git_sha, atomic=atomic,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
