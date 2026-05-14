#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Per-paper pipeline orchestration.

``process_paper`` walks one paper through the linear processing
pipeline (download -> convert -> dissect -> advocatus -> agora ->
herald -> ready). Each verb in the CLI maps to a ``through`` value
that says how far to advance.

``ensure_paper_md`` is the citation shortcut: download + convert as
one unit with a CAS guard, used by dissect's citation verification
to prepare cited papers without committing them to the full pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from paperstore.backend import StorageBackend
from paperstore.stages import STAGE_NAMES, STAGES, failed_stage

logger = logging.getLogger(__name__)


async def process_paper(
    pid: str,
    backend: StorageBackend,
    *,
    through: int = STAGES["ready"],
    debug: bool = False,
    trace: bool = False,
    force: bool = False,
    on_progress: object = None,
) -> int:
    """Advance a paper through pipeline stages up to ``through``.

    Reads the paper's current status and walks forward one stage at
    a time. Each stage does its work, then calls ``advance_status``
    (CAS) to record completion. On failure, calls ``fail_paper`` and
    re-raises.

    When ``force`` is True, resets status to ``through - 1`` so the
    target stage re-runs without redoing earlier stages. For example,
    ``process_paper(pid, through=3, force=True)`` resets to status 2
    (needs dissect) without re-downloading or re-converting.

    Returns the final status value.
    """
    pid = pid.strip().upper()
    paper = backend.get_meta(pid)
    status = paper.status

    if force:
        reset_to = through - 1
        backend.advance_status(pid, status, reset_to)
        status = reset_to

    if status < 0:
        status = failed_stage(status)
        backend.advance_status(pid, paper.status, status)

    while 0 <= status < through:
        stage_name = STAGE_NAMES.get(status, f"stage-{status}")
        logger.info("Processing %s: %s", pid, stage_name)

        try:
            await _run_stage(pid, status, backend, debug=debug, trace=trace,
                             on_progress=on_progress)
        except Exception as exc:
            logger.error("%s failed at %s: %s", pid, stage_name, exc)
            backend.fail_paper(pid, status, str(exc))
            raise

        if not backend.advance_status(pid, status, status + 1):
            break

        status += 1

    return status


async def ensure_paper_md(pid: str, backend: StorageBackend) -> str | None:
    """Ensure a paper's markdown is available. Returns content or None.

    Downloads and converts as one unit if needed. Uses CAS to advance
    status to 2 (dissect) if currently below that. Safe for concurrent
    callers - duplicate work is harmless, the CAS ensures status
    advances exactly once.

    Returns None if the paper is not in the database.
    """
    pid = pid.strip().upper()
    result = backend.resolve_year_for_paper(pid)
    if result is None:
        return None

    _, paper = result

    if paper.status >= STAGES["dissect"]:
        return backend.get_paper_md(pid)

    try:
        await _run_stage(pid, STAGES["download"], backend)
        await _run_stage(pid, STAGES["convert"], backend)
    except Exception:
        logger.warning("ensure_paper_md failed for %s", pid, exc_info=True)
        return None

    backend.advance_status(pid, STAGES["download"], STAGES["convert"])
    backend.advance_status(pid, STAGES["convert"], STAGES["dissect"])

    try:
        return backend.get_paper_md(pid)
    except Exception:
        return None


async def _run_stage(
    pid: str,
    stage: int,
    backend: StorageBackend,
    *,
    debug: bool = False,
    trace: bool = False,
    on_progress: object = None,
) -> Any:
    """Execute a single pipeline stage for one paper."""
    if stage == STAGES["download"]:
        await _stage_download(pid, backend, on_progress=on_progress)
    elif stage == STAGES["convert"]:
        await _stage_convert(pid, backend)
    elif stage == STAGES["dissect"]:
        await _stage_dissect(pid, backend, debug=debug, trace=trace, on_progress=on_progress)
    elif stage == STAGES["advocatus"]:
        await _stage_advocatus(pid, backend, debug=debug, trace=trace, on_progress=on_progress)
    elif stage == STAGES["agora"]:
        await _stage_agora(pid, backend, debug=debug, trace=trace, on_progress=on_progress)
    elif stage == STAGES["herald"]:
        pass
    else:
        raise ValueError(f"Unknown stage {stage}")


async def _stage_download(pid: str, backend: StorageBackend, *, on_progress: object = None) -> None:
    """Download the paper's source file."""
    from mailing.download import default_client, download_paper

    from pathlib import Path as _Path

    paper = backend.get_meta(pid)
    if paper.source_file and _Path(paper.source_file).exists():
        return
    if not paper.url:
        raise RuntimeError(f"No URL for {pid}")
    async with default_client() as client:
        result = await download_paper(pid, source_url=paper.url, client=client,
                                      on_progress=on_progress)
    if result is None:
        raise RuntimeError(f"Download returned nothing for {pid}")
    content, suffix = result
    backend.put_source(pid, content, suffix=suffix)


async def _stage_convert(pid: str, backend: StorageBackend) -> None:
    """Convert source file to markdown."""
    import asyncio
    from pathlib import Path

    from tomd.api import convert_paper

    from pathlib import Path as _Path

    paper = backend.get_meta(pid)
    if paper.markdown_path and _Path(paper.markdown_path).exists():
        return
    source_path = paper.source_file
    if not source_path:
        raise RuntimeError(f"No source file for {pid}")

    meta = {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "authors": paper.authors,
        "url": paper.url,
        "document_date": paper.document_date,
    }
    markdown, _prompts, _intent = await asyncio.to_thread(
        convert_paper, pid, Path(source_path), meta,
    )
    backend.write_paper_md(pid, markdown)


async def _stage_dissect(
    pid: str, backend: StorageBackend, *, debug: bool = False, trace: bool = False,
    on_progress: object = None,
) -> None:
    """Run dissect pipeline on the paper."""
    from dissect import dissect_paper

    report = await dissect_paper(pid, backend, debug=debug, trace=trace,
                                   on_progress=on_progress)
    backend.write_dissect_md(pid, report)


async def _stage_advocatus(
    pid: str, backend: StorageBackend, *, debug: bool = False, trace: bool = False,
    on_progress: object = None,
) -> None:
    """Run advocatus pipeline on the paper."""
    from advocatus import advocatus_paper

    relatio = await advocatus_paper(pid, backend, debug=debug, trace=trace,
                                      on_progress=on_progress)
    backend.write_advocatus_md(pid, relatio)


async def _stage_agora(
    pid: str, backend: StorageBackend, *, debug: bool = False, trace: bool = False,
    on_progress: object = None,
) -> None:
    """Run agora pipeline on the paper."""
    from agora import agora_paper

    await agora_paper(pid, backend, debug=debug, trace=trace,
                       on_progress=on_progress)
