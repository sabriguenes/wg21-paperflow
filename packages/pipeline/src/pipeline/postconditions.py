#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Stage postcondition checks for the per-paper pipeline.

The DB's ``status`` column records the highest stage a paper completed,
but that column can drift from the on-disk truth: a markdown file can
be deleted, a workspace can be moved, a partial write can be rolled
back. ``postcondition_satisfied`` is the single source of truth for
"did stage N produce its artifact?", and ``truthful_status`` floors a
claimed status to whatever the artifacts actually support.

Both are read-only against the backend and the filesystem. Callers
(``process_paper`` and every ``_stage_*`` short-circuit) use them to
keep ``status`` and reality in sync without introducing duplicate
inline checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from paperstore.backend import StorageBackend
from paperstore.stages import STAGES

__all__ = [
    "ProcessResult",
    "postcondition_satisfied",
    "truthful_status",
]


@dataclass(frozen=True)
class ConvertReport:
    """Per-paper convert-stage telemetry, surfaced to the CLI for
    end-of-batch summaries.

    Populated by ``_stage_convert`` when it ran. ``downstream_cleared``
    is the names list from :class:`paperstore.ClearedSet` for papers
    whose markdown content changed and where ``--keep-downstream`` was
    not set.
    """

    images_kept: int = 0
    source_image_count: int = 0
    images_truncated: bool = False
    downstream_cleared: tuple[str, ...] = ()


@dataclass
class ProcessResult:
    """Result of one ``process_paper`` invocation.

    ``stages_run`` is the ordered list of stage indices whose
    ``_stage_*`` body actually executed. An empty list means the verb
    short-circuited (paper already at or past ``through`` with all
    artifacts intact).

    ``convert_report`` is set when the convert stage ran in this
    invocation (so the CLI can roll up truncation and invalidation
    summaries across the batch).
    """

    final_status: int
    stages_run: list[int] = field(default_factory=list)
    convert_report: ConvertReport | None = None


def postcondition_satisfied(
    backend: StorageBackend, pid: str, stage: int
) -> bool:
    """Return True iff the on-disk artifact for ``stage`` exists.

    Each numbered stage produces one canonical artifact recorded in a
    ``papers`` table column. The postcondition is "that column is set
    and the file at that path exists". Herald and ready have no
    filesystem artifact and trivially pass.
    """
    paper = backend.get_meta(pid)
    if stage == STAGES["download"]:
        return bool(paper.source_file) and Path(paper.source_file).exists()
    if stage == STAGES["convert"]:
        return bool(paper.markdown_path) and Path(paper.markdown_path).exists()
    if stage == STAGES["dissect"]:
        return bool(paper.dissect_path) and Path(paper.dissect_path).exists()
    if stage == STAGES["advocatus"]:
        return bool(paper.advocatus_path) and Path(paper.advocatus_path).exists()
    if stage == STAGES["agora"]:
        return bool(paper.agora_path) and Path(paper.agora_path).exists()
    return True


def truthful_status(
    backend: StorageBackend, pid: str, claimed: int
) -> int:
    """Floor ``claimed`` to the first stage whose postcondition fails.

    Walks stages ``0..claimed-1`` and returns the smallest ``s`` such
    that ``postcondition_satisfied(backend, pid, s)`` is False. If
    every postcondition holds, returns ``claimed`` unchanged.

    Capped at ``STAGES["ready"]`` so callers passing arbitrary
    ``claimed`` values never walk off the end of the stage table.
    """
    upper = min(claimed, STAGES["ready"])
    for s in range(upper):
        if not postcondition_satisfied(backend, pid, s):
            return s
    return claimed
