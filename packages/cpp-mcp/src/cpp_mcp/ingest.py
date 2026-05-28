#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Ingestion pipeline: clone cplusplus/draft, parse LaTeX, load into backend."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from cpp_mcp.backend import SectionRow, StandardBackend
from cpp_mcp.parser import Section, parse_directory

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


def ingest_from_directory(
    backend: StandardBackend,
    source_dir: Path,
    draft_tag: str,
    git_sha: str | None = None,
) -> int:
    """Parse a local source directory and load sections into the backend.

    Returns the number of sections ingested.
    """
    log.info("Parsing LaTeX from %s", source_dir)
    sections = parse_directory(source_dir)
    log.info("Parsed %d sections", len(sections))

    rows = [_section_to_row(s, draft_tag) for s in sections]
    backend.upsert_draft(draft_tag, rows, git_sha=git_sha)
    log.info("Ingested %d sections for draft '%s'", len(rows), draft_tag)
    return len(rows)


def ingest_from_git(
    backend: StandardBackend,
    tag: str,
) -> int:
    """Clone cplusplus/draft at *tag*, parse, and load into backend.

    The clone is done into a temporary directory that is cleaned up
    after ingestion completes.
    """
    tmp_dir = tempfile.mkdtemp(prefix="cpp-mcp-")
    try:
        clone_dir = Path(tmp_dir) / "draft"
        git_sha = _clone_draft(tag, clone_dir)
        source_dir = clone_dir / "source"
        if not source_dir.is_dir():
            raise FileNotFoundError(
                f"No source/ directory found in cloned repo at {clone_dir}"
            )
        return ingest_from_directory(backend, source_dir, tag, git_sha=git_sha)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
