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
from datetime import datetime, timezone
from typing import Any

from paperstore.backend import StorageBackend
from paperstore.stages import STAGE_NAMES, STAGES, failed_stage

from pipeline.postconditions import (
    ProcessResult,
    postcondition_satisfied,
    truthful_status,
)

logger = logging.getLogger(__name__)


async def process_paper(
    pid: str,
    backend: StorageBackend,
    *,
    through: int = STAGES["ready"],
    debug: bool = False,
    trace: bool = False,
    stop_after: int | None = None,
    chunk_index: int | None = None,
    service_overrides: dict[str, str] | None = None,
    classifier_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    force: bool = False,
    on_progress: object = None,
) -> ProcessResult:
    """Advance a paper through pipeline stages up to ``through``.

    Reads the paper's current status and walks forward one stage at
    a time. Each stage does its work, then calls ``advance_status``
    (CAS) to record completion. On failure, calls ``fail_paper`` and
    re-raises.

    When ``force`` is True, resets status to ``through - 1`` so the
    target stage re-runs without redoing earlier stages. For example,
    ``process_paper(pid, through=3, force=True)`` resets to status 2
    (needs dissect) without re-downloading or re-converting.

    Before entering the work loop, the in-memory status is floored to
    :func:`truthful_status`, so a paper whose DB column claims a stage
    its on-disk artifact no longer supports is rewound to redo the
    missing work. This makes the CLI verbs self-healing against state
    drift (deleted markdown files, moved workspaces, partial writes).

    Returns a :class:`ProcessResult` carrying the final status and the
    ordered list of stage indices whose body actually executed. An
    empty ``stages_run`` means the verb short-circuited.
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

    # Rewind to whatever the on-disk artifacts actually support. This
    # fixes the "convert was a lie" class of bugs: if the DB column
    # claims a paper is past convert but the markdown file is gone,
    # ``truthful_status`` returns the missing-artifact stage and we
    # rewind status to redo it. Idempotent when artifacts are intact.
    truthful = truthful_status(backend, pid, status)
    if truthful < status:
        logger.info(
            "Rewinding %s from status %d to %d (missing artifact)",
            pid, status, truthful,
        )
        backend.advance_status(pid, status, truthful)
        status = truthful

    if debug or trace:
        trace_path = backend.get_trace_md_path(pid)
        debug_path = backend.get_debug_md_path(pid)
        if paper.status >= 0:
            for p in (trace_path, debug_path):
                if p.exists():
                    bak = p.with_suffix(".bak.md")
                    bak.unlink(missing_ok=True)
                    p.rename(bak)

    stages_run: list[int] = []
    while 0 <= status < through:
        stage_name = STAGE_NAMES.get(status, f"stage-{status}")
        logger.info("Processing %s: %s", pid, stage_name)

        if trace and status >= 2:
            tp = backend.get_trace_md_path(pid)
            if tp.exists():
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                with open(tp, "a", encoding="utf-8") as f:
                    f.write(f"\n---\n\n## {stage_name} (started {now})\n\n")

        try:
            await _run_stage(pid, status, backend, debug=debug, trace=trace,
                             stop_after=stop_after, chunk_index=chunk_index,
                             service_overrides=service_overrides,
                             classifier_overrides=classifier_overrides,
                             provider_override=provider_override,
                             on_progress=on_progress)
        except Exception as exc:
            logger.error("%s failed at %s: %s", pid, stage_name, exc)
            backend.fail_paper(pid, status, str(exc))
            raise

        stages_run.append(status)

        if not backend.advance_status(pid, status, status + 1):
            break

        status += 1

    return ProcessResult(final_status=status, stages_run=stages_run)


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

    # Trust-but-verify: status alone is not enough, the markdown file
    # may have been deleted under us.
    if (
        paper.status >= STAGES["dissect"]
        and postcondition_satisfied(backend, pid, STAGES["convert"])
    ):
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
    stop_after: int | None = None,
    chunk_index: int | None = None,
    service_overrides: dict[str, str] | None = None,
    classifier_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    on_progress: object = None,
) -> Any:
    """Execute a single pipeline stage for one paper."""
    if stage == STAGES["download"]:
        await _stage_download(pid, backend, on_progress=on_progress)
    elif stage == STAGES["convert"]:
        await _stage_convert(pid, backend)
    elif stage == STAGES["dissect"]:
        await _stage_dissect(pid, backend, debug=debug, trace=trace,
                             stop_after=stop_after, chunk_index=chunk_index,
                             service_overrides=service_overrides,
                             classifier_overrides=classifier_overrides,
                             provider_override=provider_override,
                             on_progress=on_progress)
    elif stage == STAGES["advocatus"]:
        await _stage_advocatus(pid, backend, debug=debug, trace=trace,
                               service_overrides=service_overrides,
                               provider_override=provider_override,
                               on_progress=on_progress)
    elif stage == STAGES["agora"]:
        await _stage_agora(pid, backend, debug=debug, trace=trace,
                           service_overrides=service_overrides,
                           provider_override=provider_override,
                           on_progress=on_progress)
    elif stage == STAGES["herald"]:
        pass
    else:
        raise ValueError(f"Unknown stage {stage}")


async def _stage_download(pid: str, backend: StorageBackend, *, on_progress: object = None) -> None:
    """Download the paper's source file."""
    from mailing.download import default_client, download_paper

    if postcondition_satisfied(backend, pid, STAGES["download"]):
        return
    paper = backend.get_meta(pid)
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

    if postcondition_satisfied(backend, pid, STAGES["convert"]):
        return
    paper = backend.get_meta(pid)
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
    stop_after: int | None = None, chunk_index: int | None = None,
    service_overrides: dict[str, str] | None = None,
    classifier_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    on_progress: object = None,
) -> None:
    """Run dissect pipeline on the paper.

    No artifact-exists short-circuit: this stage is only reached from
    ``process_paper``, which already used ``truthful_status`` to decide
    we needed to run. Adding a postcondition check here would silently
    no-op ``--force`` reruns when the prior ``dissect.md`` still exists.
    """
    from dissect import dissect_paper

    report = await dissect_paper(
        pid, backend, debug=debug, trace=trace,
        stop_after=stop_after, chunk_index=chunk_index,
        service_overrides=service_overrides,
        classifier_overrides=classifier_overrides,
        provider_override=provider_override,
        on_progress=on_progress,
    )
    backend.write_dissect_md(pid, report)


async def _stage_advocatus(
    pid: str, backend: StorageBackend, *, debug: bool = False, trace: bool = False,
    service_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    on_progress: object = None,
) -> None:
    """Run advocatus pipeline on the paper.

    See ``_stage_dissect`` for why no postcondition short-circuit lives
    here.
    """
    from advocatus import advocatus_paper

    relatio = await advocatus_paper(
        pid, backend, debug=debug, trace=trace,
        service_overrides=service_overrides,
        on_progress=on_progress,
    )
    backend.write_advocatus_md(pid, relatio)
    # provider_override is accepted for API parity but advocatus does
    # not currently load classifiers; passing it through is a no-op
    # today and a future-friendly hook.
    _ = provider_override


async def _stage_agora(
    pid: str, backend: StorageBackend, *, debug: bool = False, trace: bool = False,
    service_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    on_progress: object = None,
) -> None:
    """Run agora pipeline on the paper.

    See ``_stage_dissect`` for why no postcondition short-circuit lives
    here.
    """
    from agora import agora_paper

    await agora_paper(
        pid, backend, debug=debug, trace=trace,
        service_overrides=service_overrides,
        on_progress=on_progress,
    )
    _ = provider_override
