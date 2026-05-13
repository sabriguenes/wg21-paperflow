#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Batch job library for the paperflow pipeline.

Each ``run_*`` function is ``async def`` and returns a result dict with
``succeeded``, ``failed``, and ``skipped`` lists. Workers are coroutines
that return plain result dicts - they never touch the storage backend.
CPU-bound work (tomd conversion) uses ``asyncio.to_thread``. The main coroutine
receives each result via ``asyncio.as_completed`` and writes to the
backend serially, avoiding any SQLite concurrency issues.

Stages: mailing, download, convert. Command modules call
``asyncio.run(jobs.run_*(...))``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from paperstore import parse_authors_raw
from paperstore.backend import PaperRow, StorageBackend
from paperstore.errors import (
    MissingMailingIndexError,
    MissingPaperMdError,
    MissingSourceError,
)
from paperstore.progress import ProgressCallback, ProgressEvent

logger = logging.getLogger(__name__)

MAILING_EARLIEST_YEAR = 2011
DEFAULT_DOWNLOAD_CONCURRENCY = 8


# ---------------------------------------------------------------------------
# Target resolution helpers
# ---------------------------------------------------------------------------

def _validate_targets(targets: list[str]) -> str:
    """Return the target type: 'all', 'years', or 'papers'.

    Raises ValueError if targets are empty, mix years and paper IDs, or
    contain an unrecognized format.
    """
    if not targets:
        raise ValueError("At least one target is required.")
    if targets == ["all"]:
        return "all"
    # Check all are years (4 digits) or all are paper IDs.
    are_years = [t.isdigit() and len(t) == 4 for t in targets]
    if all(are_years):
        return "years"
    if not any(are_years):
        return "papers"
    raise ValueError(
        "Cannot mix years and paper IDs in one command. "
        f"Got: {targets!r}"
    )


def _papers_from_scope(
    targets: list[str], target_type: str, backend: StorageBackend
) -> list[PaperRow]:
    """Return paper rows matching the scope, without idempotency filtering."""
    if target_type == "all":
        ids = backend.list_all_paper_ids()
        rows = []
        for pid in ids:
            result = backend.resolve_year_for_paper(pid)
            if result:
                _, row = result
                rows.append(row)
        return rows
    if target_type == "years":
        rows = []
        for year in targets:
            try:
                rows.extend(backend.list_papers_for_year(year))
            except MissingMailingIndexError:
                logger.warning("No papers found for year %s; run 'paperflow mailing %s' first.", year, year)
        return rows
    # paper IDs
    rows = []
    for pid in targets:
        result = backend.resolve_year_for_paper(pid.upper())
        if result is None:
            logger.warning("Paper %s not found in database.", pid)
        else:
            _, row = result
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# run_mailing
# ---------------------------------------------------------------------------

async def run_mailing(
    targets: list[str],
    backend: StorageBackend,
    *,
    current_year: str | None = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Scrape mailing indexes from open-std.org and store in the backend.

    ``targets`` is a list of year strings, or ``["all"]``. Past years
    where ``backend.has_year(year)`` is True are skipped; the current year
    is always re-fetched. Pass ``force=True`` to bypass the skip and
    re-fetch every requested year. ``upsert_year`` preserves
    ``source_file`` and ``markdown_path``, so a forced re-fetch only
    updates mailing metadata (title, authors, url, dates) without touching
    downloaded sources or converted markdown.
    """
    from mailing.scrape import discover_years, fetch_all_mailings_for_year

    if current_year is None:
        current_year = str(datetime.now(timezone.utc).year)

    target_type = _validate_targets(targets)

    if target_type == "all":
        all_years = discover_years()
        years = [y for y in all_years if int(y) >= MAILING_EARLIEST_YEAR]
    else:
        years = targets

    succeeded = []
    skipped = []
    failed = []
    total_years = len(years)

    for i, year in enumerate(years):
        if on_progress is not None:
            try:
                on_progress(ProgressEvent(
                    step=i, total=total_years,
                    name=f"Mailing {year}", pct=i / total_years if total_years else 1.0,
                ))
            except Exception:
                logger.warning("on_progress hook raised; disabling", exc_info=True)
                on_progress = None

        if not force and year < current_year and backend.has_year(year):
            skipped.append(year)
            continue
        try:
            all_mailings = fetch_all_mailings_for_year(year)
            for mailing_id, papers in sorted(all_mailings.items()):
                backend.upsert_year(year, papers)
            total = len(backend.list_papers_for_year(year))
            succeeded.append({"year": year, "papers": total})
        except Exception as exc:
            logger.exception("Failed to fetch year %s", year)
            failed.append({"year": year, "error": str(exc)})

    if on_progress is not None:
        try:
            on_progress(ProgressEvent(
                step=total_years, total=total_years,
                name="done", pct=1.0,
            ))
        except Exception:
            pass

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# run_download
# ---------------------------------------------------------------------------

async def run_download(
    targets: list[str],
    backend: StorageBackend,
    *,
    force: bool = False,
    verify: bool = False,
    concurrency: int = DEFAULT_DOWNLOAD_CONCURRENCY,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Download source files for papers. Workers are async httpx calls.

    ``on_progress`` is invoked after each task completion with a
    :class:`~paperstore.progress.ProgressEvent`.
    """
    from mailing.download import content_length, default_client, download_paper

    concurrency = max(1, concurrency)
    target_type = _validate_targets(targets)
    all_papers = _papers_from_scope(targets, target_type, backend)

    # Apply idempotency filter via SQL-equivalent: exclude already-downloaded.
    if not force:
        to_process = [p for p in all_papers if not p.source_file]
    else:
        to_process = [p for p in all_papers if p.url]

    total = len(to_process)
    semaphore = asyncio.Semaphore(concurrency)

    async with default_client() as http:

        async def _one(paper: PaperRow) -> dict:
            pid = paper.paper_id
            url = paper.url
            if not url:
                return {"paper_id": pid, "status": "skipped", "reason": "no_url"}
            async with semaphore:
                if verify and paper.source_file:
                    cl = await content_length(url, client=http)
                    if cl is not None:
                        try:
                            source_path = backend.get_source_path(pid)
                            existing_size = source_path.stat().st_size
                        except (MissingSourceError, FileNotFoundError):
                            existing_size = None
                        if existing_size == cl:
                            return {"paper_id": pid, "status": "skipped", "reason": "verified_match"}
                try:
                    fetched = await download_paper(pid, source_url=url, client=http)
                    if fetched is None:
                        return {"paper_id": pid, "status": "skipped", "reason": "no_url"}
                    content, suffix = fetched
                    return {
                        "paper_id": pid,
                        "content": content,
                        "suffix": suffix,
                        "status": "ok",
                    }
                # Batch robustness: one bad paper must not crash the run
                except Exception as exc:
                    logger.exception("Download failed for %s", pid)
                    return {"paper_id": pid, "status": "error", "error": str(exc)}

        tasks = [asyncio.create_task(_one(p)) for p in to_process]
        succeeded = []
        failed = []
        to_process_ids = {p.paper_id for p in to_process}
        skipped_papers = [
            {"paper_id": p.paper_id,
             "reason": "no_url" if not p.url else "already_staged"}
            for p in all_papers if p.paper_id not in to_process_ids
        ]

        completed = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result["status"] == "ok":
                backend.put_source(
                    result["paper_id"], result["content"], suffix=result["suffix"]
                )
                succeeded.append(result["paper_id"])
            elif result["status"] == "skipped":
                skipped_papers.append(result)
            else:
                failed.append(result)
            completed += 1
            if on_progress is not None:
                try:
                    on_progress(ProgressEvent(
                        step=completed, total=total,
                        name=result["paper_id"], pct=completed / total if total else 1.0,
                    ))
                except Exception:
                    logger.warning("on_progress hook raised; disabling", exc_info=True)
                    on_progress = None

    return {"succeeded": succeeded, "skipped": skipped_papers, "failed": failed}


# ---------------------------------------------------------------------------
# run_convert
# ---------------------------------------------------------------------------

async def run_convert(
    targets: list[str],
    backend: StorageBackend,
    *,
    force: bool = False,
    concurrency: int = 4,
    write_prompts: bool = True,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Convert staged source files to markdown. Workers run in threads.

    ``write_prompts`` controls whether the ``<pid>.prompts.json``
    intermediate is persisted (default True). Set False from CLI flows
    that explicitly opt out via ``--no-prompts``.

    ``on_progress`` is invoked after each task completion with a
    :class:`~paperstore.progress.ProgressEvent`.
    """
    from cli.orchestrator import convert_one_paper
    from cli.models import Paper

    concurrency = max(1, concurrency)
    target_type = _validate_targets(targets)
    all_papers = _papers_from_scope(targets, target_type, backend)

    if not force:
        to_process = [p for p in all_papers
                      if p.source_file and not p.markdown_path]
    else:
        to_process = [p for p in all_papers if p.source_file]

    total = len(to_process)
    semaphore = asyncio.Semaphore(concurrency)

    def _make_paper(row: PaperRow) -> Paper:
        authors = parse_authors_raw(row.authors or [])
        return Paper(
            document_id=row.paper_id,
            year=row.year,
            title=row.title,
            authors=authors,
            mailing_date=row.mailing_date,
            document_date=row.document_date,
            audience=row.target_group,
            intent=row.intent,
            url=row.url,
            source_file=row.source_file,
            markdown_path=row.markdown_path,
        )

    in_flight: set[str] = set()

    async def _one(paper_row: PaperRow) -> dict:
        pid = paper_row.paper_id
        async with semaphore:
            in_flight.add(pid)
            try:
                paper = _make_paper(paper_row)
                # Worker reads the source but does no backend writes;
                # the main coroutine persists through the backend below.
                result = await asyncio.wait_for(
                    asyncio.to_thread(convert_one_paper, paper),
                    timeout=120,
                )
                return {
                    "paper_id": pid,
                    "markdown": result.markdown,
                    "prompts": result.prompts,
                    "intent": result.intent,
                    "title": result.title,
                    "status": "ok",
                }
            except RuntimeError as exc:
                msg = str(exc)
                if "empty markdown" in msg:
                    logger.warning("Skipping %s: %s", pid, msg)
                    return {"paper_id": pid, "status": "skipped", "reason": "unreadable_source"}
                logger.exception("Convert failed for %s", pid)
                return {"paper_id": pid, "status": "error", "error": msg}
            except TimeoutError:
                logger.warning("Skipping %s: conversion timed out (120s)", pid)
                return {"paper_id": pid, "status": "skipped", "reason": "timeout"}
            except Exception as exc:
                logger.exception("Convert failed for %s", pid)
                return {"paper_id": pid, "status": "error", "error": str(exc)}
            finally:
                in_flight.discard(pid)

    tasks = [asyncio.create_task(_one(p)) for p in to_process]
    succeeded = []
    failed = []
    to_process_ids = {p.paper_id for p in to_process}
    skipped = [{"paper_id": p.paper_id, "reason": "already_converted"}
               for p in all_papers if p.paper_id not in to_process_ids]

    completed = 0
    for coro in asyncio.as_completed(tasks):
        result = await coro
        if result["status"] == "ok":
            pid = result["paper_id"]
            md_path = backend.write_paper_md(pid, result["markdown"])
            if write_prompts and result["prompts"]:
                backend.write_intermediate(pid, "prompts", result["prompts"])
            backend.record_markdown(pid, md_path, intent=result["intent"])
            succeeded.append(pid)
        elif result["status"] == "skipped":
            skipped.append(result)
        else:
            failed.append(result)
        completed += 1
        if on_progress is not None:
            try:
                on_progress(ProgressEvent(
                    step=completed, total=total,
                    name=next(iter(in_flight)) if in_flight else result["paper_id"],
                    pct=completed / total if total else 1.0,
                ))
            except Exception:
                logger.warning("on_progress hook raised; disabling", exc_info=True)
                on_progress = None

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# run_qa
# ---------------------------------------------------------------------------

def run_qa(
    targets: list[str],
    backend: StorageBackend,
    *,
    json_path: Path | None = None,
    workers: int = 1,
    timeout: int = 120,
) -> dict:
    """Score quality of converted markdown for the given targets.

    Synchronous. ``run_qa_report`` does its own ProcessPoolExecutor
    parallelism, so there is no per-row async work to overlap. Returns
    ``{"succeeded": [paper_ids], "skipped": [{paper_id, reason}], "failed": []}``.
    """
    from tomd.lib.pdf.qa import run_qa_report

    workers = max(1, workers)
    target_type = _validate_targets(targets)
    rows = _papers_from_scope(targets, target_type, backend)

    items: list[tuple[str, str]] = []
    skipped: list[dict] = []
    for row in rows:
        pid = row.paper_id
        try:
            md = backend.get_paper_md(pid)
        except MissingPaperMdError:
            skipped.append({"paper_id": pid, "reason": "no_markdown"})
            continue
        items.append((pid, md))

    if not items:
        return {"succeeded": [], "skipped": skipped, "failed": []}

    run_qa_report(items, json_path=json_path, workers=workers, timeout=timeout)
    return {
        "succeeded": [pid for pid, _ in items],
        "skipped": skipped,
        "failed": [],
    }


# ---------------------------------------------------------------------------
# run_full
# ---------------------------------------------------------------------------

async def run_full(
    targets: list[str],
    backend: StorageBackend,
    *,
    force: bool = False,
    verify: bool = False,
    concurrency: int = DEFAULT_DOWNLOAD_CONCURRENCY,
    current_year: str | None = None,
) -> dict:
    """Chain mailing -> download -> convert for the given targets."""
    target_type = _validate_targets(targets)

    # Determine years for mailing stage.
    if target_type == "years":
        mailing_targets = targets
    elif target_type == "all":
        mailing_targets = ["all"]
    else:
        # Paper IDs: derive years from what's in the DB (or skip mailing stage).
        mailing_targets = None

    results = {}

    if mailing_targets is not None:
        results["mailing"] = await run_mailing(
            mailing_targets, backend, current_year=current_year, force=force
        )

    results["download"] = await run_download(
        targets, backend, force=force, verify=verify, concurrency=concurrency
    )
    results["convert"] = await run_convert(
        targets, backend, force=force, concurrency=(concurrency // 2) or 1
    )

    return results
