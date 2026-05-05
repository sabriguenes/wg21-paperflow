#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Paperflow pipeline orchestrator.

Provides per-paper conversion via ``convert_one_paper``.
"""

from pathlib import Path

from cli.models import ConvertResult, Paper
from tomd.api import convert_paper as tomd_convert_paper

__all__ = [
    "convert_one_paper",
]


def convert_one_paper(paper: "Paper") -> "ConvertResult":
    """Convert a staged paper source to markdown. No LLM, no I/O beyond
    reading the source file.

    Takes a :class:`Paper` object with ``source_file`` populated. Returns
    a :class:`ConvertResult` carrying the markdown and any tomd prompts;
    the caller (``jobs.run_convert``) is responsible for persisting the
    result through the storage backend.

    Raises:
        RuntimeError: source_file is empty - run ``paperflow download`` first.
        RuntimeError: tomd produced no usable markdown.
    """
    paper_id = paper.document_id.strip().upper()

    if not paper.source_file:
        raise RuntimeError(
            f"{paper_id} source not staged. Run 'paperflow download {paper_id}' first."
        )

    source_path = Path(paper.source_file)
    meta = {
        "paper_id": paper_id,
        "title": paper.title,
        "authors": paper.authors,
        "target_group": paper.audience,
        "subgroup": paper.audience,
        "document_date": paper.document_date,
        "intent": paper.intent,
        "url": paper.url,
    }

    markdown, prompts, extracted_intent = tomd_convert_paper(
        paper_id, source_path, meta
    )

    # tomd front-matter intent wins over scraper-derived intent
    intent = extracted_intent if extracted_intent else paper.intent

    return ConvertResult(
        paper_id=paper_id,
        markdown=markdown,
        prompts=prompts,
        intent=intent,
        title=paper.title,
        status="ok",
    )
