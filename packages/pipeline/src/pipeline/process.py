#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Per-paper pipeline orchestration.

``process_paper`` walks one paper through the linear processing
pipeline (download -> convert -> agora -> herald -> ready). Each
verb in the CLI maps to a ``through`` value that says how far to
advance.

``ensure_paper_md`` is the citation shortcut: download + convert as
one unit with a CAS guard, used by citation verification to prepare
cited papers without committing them to the full pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from paperstore.backend import StorageBackend
from paperstore.stages import STAGE_NAMES, STAGES, failed_stage

from pipeline.errors import UnknownStageError
from pipeline.postconditions import (
    ConvertReport,
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
    classifier_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    force: bool = False,
    keep_downstream: bool = False,
    extract_vector: bool = False,
    whiteout_text: bool = False,
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
    without re-downloading or re-converting.

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
    convert_report: ConvertReport | None = None
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
            stage_result = await _run_stage(
                pid, status, backend, debug=debug, trace=trace,
                stop_after=stop_after, chunk_index=chunk_index,
                classifier_overrides=classifier_overrides,
                provider_override=provider_override,
                keep_downstream=keep_downstream,
                extract_vector=extract_vector,
                whiteout_text=whiteout_text,
                on_progress=on_progress,
            )
        except Exception as exc:
            logger.error("%s failed at %s: %s", pid, stage_name, exc)
            backend.fail_paper(pid, status, str(exc))
            raise

        if isinstance(stage_result, ConvertReport):
            convert_report = stage_result
        stages_run.append(status)

        if not backend.advance_status(pid, status, status + 1):
            break

        status += 1

    return ProcessResult(
        final_status=status,
        stages_run=stages_run,
        convert_report=convert_report,
    )


async def ensure_paper_md(pid: str, backend: StorageBackend) -> str | None:
    """Ensure a paper's markdown is available. Returns content or None.

    Downloads and converts as one unit if needed. Uses CAS to advance
    status past convert if currently below that. Safe for concurrent
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
        paper.status > STAGES["convert"]
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
    backend.advance_status(pid, STAGES["convert"], STAGES["agora"])

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
    classifier_overrides: dict[str, str] | None = None,
    provider_override: str | None = None,
    keep_downstream: bool = False,
    extract_vector: bool = False,
    whiteout_text: bool = False,
    on_progress: object = None,
) -> Any:
    """Execute a single pipeline stage for one paper."""
    if stage == STAGES["download"]:
        await _stage_download(pid, backend, on_progress=on_progress)
    elif stage == STAGES["convert"]:
        return await _stage_convert(
            pid, backend,
            keep_downstream=keep_downstream,
            extract_vector=extract_vector,
            whiteout_text=whiteout_text,
        )
    elif stage == STAGES["agora"]:
        await _stage_agora(pid, backend, debug=debug, trace=trace,
                           provider_override=provider_override,
                           on_progress=on_progress)
    elif stage == STAGES["herald"]:
        pass
    else:
        raise UnknownStageError(f"Unknown stage {stage}")


async def _stage_download(pid: str, backend: StorageBackend, *, on_progress: object = None) -> None:
    """Download the paper's source file. For HTML sources, also fetch
    referenced images and write the tomd-side handoff manifest.
    """
    import httpx
    from mailing import fetch_html_images
    from mailing.download import default_client, download_paper
    from paperstore.html_manifest import HtmlImageEntry, HtmlImagesManifest
    from paperstore.stages import STAGES as _STAGES

    if postcondition_satisfied(backend, pid, STAGES["download"]):
        return
    paper = backend.get_meta(pid)
    if not paper.url:
        raise RuntimeError(f"No URL for {pid}")
    try:
        async with default_client() as client:
            result = await download_paper(pid, source_url=paper.url, client=client,
                                          on_progress=on_progress)
    except httpx.HTTPStatusError as exc:
        error_msg = f"{exc.response.status_code} {exc.response.reason_phrase}: {paper.url}"
        logger.warning("%s failed at download: %s", pid, error_msg)
        backend.fail_paper(pid, stage=_STAGES["download"], error=error_msg)
        return
    except httpx.RequestError as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.warning("%s failed at download: %s", pid, error_msg)
        backend.fail_paper(pid, stage=_STAGES["download"], error=error_msg)
        return

    if result is None:
        raise RuntimeError(f"Download returned nothing for {pid}")
        content, suffix = result
        backend.put_source(pid, content, suffix=suffix)

        # HTML papers: walk <img> refs, fetch each, persist alongside the
        # source. The manifest is the convert-time handoff so tomd never
        # has to re-parse the HTML or hit the network.
        if suffix in (".html", ".htm"):
            try:
                fetched = await fetch_html_images(
                    content, source_url=paper.url, client=client,
                )
            except Exception as exc:  # Per-paper firewall: image flow
                # failures must not fail the whole download stage. The
                # paper's source is already on disk; the manifest just
                # ends up missing or partial.
                logger.warning(
                    "html image walk failed for %s: %s", pid, exc,
                )
                fetched = []
            if fetched:
                # Re-fetching is destructive: wipe any prior set first so
                # a re-run with different image refs doesn't leave
                # orphans on disk.
                backend.delete_paper_images(pid)
                entries: list[HtmlImageEntry] = []
                for img in fetched:
                    backend.write_paper_image(
                        pid, page=0, index=img.document_order,
                        ext=img.ext, data=img.bytes,
                    )
                    entries.append(HtmlImageEntry(
                        original_src=img.original_src,
                        stored_filename=(
                            f"{pid.lower()}-fig0-{img.document_order}.{img.ext}"
                        ),
                        document_order=img.document_order,
                        caption_text=img.caption_text,
                        alt_attr=img.alt_attr,
                    ))
                manifest = HtmlImagesManifest(pid=pid, entries=entries)
                manifest_path = backend.get_html_images_manifest_path(pid)
                manifest_path.write_text(
                    manifest.to_json(), encoding="utf-8",
                )
                logger.info(
                    "%s: persisted %d HTML image(s) + manifest",
                    pid, len(entries),
                )


def _warn_if_html_image_files_missing(
    backend: StorageBackend,
    pid: str,
    manifest,
    source_path: str,
) -> int:
    """Surface a warning when manifest entries reference files that are
    no longer on disk. Returns the count of missing files.

    Edge case the convert stage cannot recover on its own: HTML image
    files are persisted at mailing time, not convert time. If they
    later go missing (manual deletion, filesystem failure, ...), the
    rendered preview will show broken ``<img>`` tags silently. The
    warning's hint points at the recovery flow.
    """
    missing = [
        e.stored_filename for e in manifest.entries
        if not backend.get_paper_image_path(
            pid,
            page=0,
            index=e.document_order,
            ext=Path(e.stored_filename).suffix.lstrip(".").lower(),
        ).exists()
    ]
    if missing:
        logger.warning(
            "%s: html-images manifest references %d missing file(s) "
            "(first few: %s). Preview will show broken images. "
            "Recover with: rm %s && paperflow download %s",
            pid, len(missing), ", ".join(missing[:3]),
            Path(source_path).name, pid,
        )
    return len(missing)


async def _stage_convert(
    pid: str,
    backend: StorageBackend,
    *,
    keep_downstream: bool = False,
    extract_vector: bool = False,
    whiteout_text: bool = False,
) -> ConvertReport:
    """Convert source file to markdown, persist extracted images, and
    conditionally invalidate downstream pipelines.

    No artifact-exists short-circuit: ``truthful_status`` upstream
    already decided we need to run. (The agora stage follows the same
    pattern; only ``--force`` reruns reach this body with a still-good
    prior markdown_path.)

    For skipped papers (slide-deck, standards-draft, unreadable):
    nothing is written to disk and no DB state is touched. The caller
    treats the stage as failed (raise) so that the paper's status is
    not advanced past ``download``.

    Otherwise: byte-equality check against any prior markdown decides
    whether downstream pipelines (agora) should be invalidated. A
    re-convert that produces identical markdown leaves extract-table
    rows intact, since their stored ``loc.line`` offsets are still
    valid. When the markdown does change AND ``keep_downstream`` is
    False, the .agora.json files and the extract rows are wiped.
    """
    import asyncio
    from pathlib import Path

    from paperstore.html_manifest import HtmlImagesManifest, HtmlManifestError

    from tomd.api import convert_paper_full

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

    # HTML papers carry a sidecar manifest written by mailing's HTML
    # image fetcher. Convert reads it from disk so it never hits the
    # network and never re-parses the source HTML for image refs.
    html_images_manifest = None
    if Path(source_path).suffix.lower() in (".html", ".htm"):
        manifest_path = backend.get_html_images_manifest_path(pid)
        if manifest_path.exists():
            try:
                html_images_manifest = HtmlImagesManifest.from_json(
                    manifest_path.read_text(encoding="utf-8"),
                )
            except HtmlManifestError as exc:
                logger.warning(
                    "%s: html images manifest unreadable (%s); continuing "
                    "without image refs", pid, exc,
                )

        # Manifest entries claim per-image filenames on disk. Check
        # them up front: a missing image file makes the rendered
        # preview show broken images silently. Surface the hint here
        # rather than letting the failure leak downstream.
        if html_images_manifest is not None:
            _warn_if_html_image_files_missing(
                backend, pid, html_images_manifest, source_path,
            )

    result = await asyncio.to_thread(
        convert_paper_full, pid, Path(source_path), meta,
        html_images_manifest=html_images_manifest,
        extract_vector=extract_vector,
        whiteout_text=whiteout_text,
    )

    if result.skipped:
        # The convert stage cannot make this paper into markdown.
        # Raise so process_paper records the failure and does not
        # advance status past download. ``skip_reason`` distinguishes
        # this from a genuine conversion error.
        raise RuntimeError(f"convert skipped ({result.skip_reason})")

    if result.images_truncated:
        logger.warning(
            "%s: kept %d of %d images (capped). The truncation marker is "
            "in the markdown body.",
            pid, len(result.images), result.source_image_count,
        )

    existing = backend.try_read_paper_md(pid)
    content_changed = existing is None or existing != result.markdown

    # HTML images carry empty bytes - mailing already persisted them
    # at download time, alongside the manifest. The PDF case has bytes
    # in memory and needs the delete-then-write rebuild to stay in sync
    # with the canonical (page, y0, x0) order.
    pdf_images = [img for img in result.images if img.bytes]
    if pdf_images:
        backend.delete_paper_images(pid)
        for img in pdf_images:
            backend.write_paper_image(
                pid, img.page, img.index_on_page, img.ext, img.bytes,
            )
        logger.info("%s: persisted %d image(s)", pid, len(pdf_images))
    backend.write_paper_md(pid, result.markdown)

    downstream_cleared: tuple[str, ...] = ()
    if content_changed and not keep_downstream:
        cleared = backend.clear_downstream_outputs(pid)
        if cleared:
            downstream_cleared = tuple(cleared.names())
            logger.warning(
                "%s: cleared downstream artifacts (%s) - markdown content "
                "changed and stored loc.line offsets would be stale. "
                "Re-run those pipelines to refresh.",
                pid, ", ".join(downstream_cleared),
            )
    elif content_changed and keep_downstream:
        logger.warning(
            "%s: markdown changed; downstream preserved per "
            "--keep-downstream (loc.line offsets may be stale).", pid,
        )

    return ConvertReport(
        images_kept=len(result.images),
        source_image_count=result.source_image_count,
        images_truncated=result.images_truncated,
        downstream_cleared=downstream_cleared,
        source_raster_count=result.source_raster_count,
        source_vector_count=result.source_vector_count,
    )


async def _stage_agora(
    pid: str, backend: StorageBackend, *, debug: bool = False, trace: bool = False,
    provider_override: str | None = None,
    on_progress: object = None,
) -> None:
    """Run agora pipeline on the paper.

    No artifact-exists short-circuit: this stage is only reached from
    ``process_paper``, which already used ``truthful_status`` to decide
    we needed to run.
    """
    from agora import agora_paper

    await agora_paper(
        pid, backend, debug=debug, trace=trace,
        on_progress=on_progress,
    )
    _ = provider_override
