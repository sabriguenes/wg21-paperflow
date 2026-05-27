#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Core data models for the paperflow CLI.

``Paper`` is the canonical in-memory representation of a row in the ``papers``
table. ``ConvertResult`` is the output of a single tomd conversion pass.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tomd.lib.pdf import ExtractedImage

__all__ = [
    "Paper",
    "ConvertResult",
]


@dataclass
class Paper:
    """Canonical in-memory representation of a WG21 paper.

    Maps to a row in the ``papers`` SQLite table. Used as the input type
    passed to conversion workers; workers never access the storage backend
    directly.

    Field semantics:

    * ``year`` -- 4-digit year string (``"2026"``). Mailings are bucketed
      by year only; the monthly mailing granularity is not exposed.
    * ``audience`` -- target subgroup name (``"LEWG"``, ``"SG16"``).
    * ``intent`` -- ``"ask"`` | ``"info"`` | ``""`` (unknown). Derived from
      the mailing title prefix (``"Ask:"`` / ``"Info:"``), confirmed or
      overridden by the paper's own YAML front matter after tomd conversion.
    * ``source_file`` -- local filesystem path to the staged PDF or HTML.
      Empty string means not yet downloaded.
    * ``markdown_path`` -- local filesystem path to the converted ``.md``.
      Empty string means not yet converted.
    """
    document_id: str
    year: str
    title: str
    authors: list[str]
    mailing_date: str
    document_date: str
    audience: str
    intent: str
    url: str
    source_file: str
    markdown_path: str


@dataclass
class ConvertResult:
    """Output of a single tomd conversion pass for one paper.

    Returned by :func:`cli.orchestrator.convert_one_paper`. The
    worker performs no I/O beyond reading the source file; the main
    coroutine persists ``markdown``, ``prompts``, and image bytes
    through the storage backend.
    """
    paper_id: str
    markdown: str
    prompts: list[str] | None
    intent: str
    title: str
    status: str         # "ok" | "error"
    error: str = ""
    images: list["ExtractedImage"] = field(default_factory=list)
