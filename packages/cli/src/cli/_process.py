#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Shared process_paper driver for all CLI verb commands."""

from __future__ import annotations

import argparse
import asyncio
import sys

from paperstore.backend import StorageBackend
from paperstore.errors import MissingMetaError
from paperstore.stages import STAGE_NAMES

from cli.targets import MONTH_RE, resolve_pid


def _parse_service_overrides(raw: list[str] | None) -> dict[str, str] | None:
    """Parse ``--service`` flag values into a slot -> service-name dict.

    A bare ``NAME`` (no ``=``) applies to all default slots.
    ``SLOT=NAME`` overrides one slot.
    """
    if not raw:
        return None
    overrides: dict[str, str] = {}
    for item in raw:
        if "=" in item:
            slot, name = item.split("=", 1)
            overrides[slot.strip()] = name.strip()
        else:
            overrides["fast"] = item
            overrides["default"] = item
            overrides["tool"] = item
    return overrides


def _parse_classifier_overrides(raw: list[str] | None) -> dict[str, str] | None:
    """Parse ``--classifier`` flag values into a slot -> classifier-name dict.

    A bare ``NAME`` (no ``=``) applies to the default ``selector`` slot.
    ``SLOT=NAME`` overrides one slot. Mirrors :func:`_parse_service_overrides`.
    """
    if not raw:
        return None
    overrides: dict[str, str] = {}
    for item in raw:
        if "=" in item:
            slot, name = item.split("=", 1)
            overrides[slot.strip()] = name.strip()
        else:
            overrides["selector"] = item
    return overrides


def run_process_command(
    args: argparse.Namespace,
    backend: StorageBackend,
    *,
    through: int,
) -> int:
    """Run process_paper for the given target through the given stage."""
    from pipeline import process_paper, PipelineError
    from cli.progress import make_progress_handler
    import pydantic_ai.exceptions

    target = args.targets[0]
    debug = getattr(args, "debug", False)
    trace = getattr(args, "trace", False)
    step_val = getattr(args, "step", None)
    if step_val is not None:
        trace = True
    stop_after = step_val
    chunk_index = getattr(args, "chunk", None)
    force = getattr(args, "force", False)
    service_overrides = _parse_service_overrides(getattr(args, "service", None))
    classifier_overrides = _parse_classifier_overrides(getattr(args, "classifier", None))
    provider_override = getattr(args, "provider", None)

    verb = STAGE_NAMES.get(through - 1, "process")

    if MONTH_RE.match(target):
        papers = backend.list_papers_since(target)
    elif target.isdigit() and len(target) == 4:
        papers = backend.list_papers_for_year(target)
    else:
        pid = resolve_pid(target, backend)
        try:
            paper = backend.get_meta(pid)
        except MissingMetaError as exc:
            print(f"{verb} failed: {exc}", file=sys.stderr)
            return 1
        papers = [paper]

    if not force:
        papers = [p for p in papers if p.status < through]

    if not papers:
        print("No papers need processing.")
        return 0

    papers.sort(key=lambda p: (p.mailing_date or ""), reverse=True)
    papers.sort(key=lambda p: -(p.status or 0))

    progress_ctx, on_progress = make_progress_handler(verb.capitalize())

    from paperstore.progress import ProgressEvent

    failed = 0
    total = len(papers)
    last_result = None
    with progress_ctx:
        for i, paper in enumerate(papers):
            if on_progress:
                on_progress(ProgressEvent(
                    step=i, total=total,
                    name=f"{paper.paper_id} - {verb}",
                    pct=i / total if total else 1.0,
                ))
            try:
                last_result = asyncio.run(
                    process_paper(
                        paper.paper_id, backend,
                        through=through,
                        debug=debug,
                        trace=trace,
                        stop_after=stop_after,
                        chunk_index=chunk_index,
                        service_overrides=service_overrides,
                        classifier_overrides=classifier_overrides,
                        provider_override=provider_override,
                        force=force,
                        on_progress=on_progress,
                    )
                )
            except PipelineError as exc:
                print(f"{paper.paper_id}: {exc}", file=sys.stderr)
                failed += 1
            except pydantic_ai.exceptions.UsageLimitExceeded as exc:
                print(f"{paper.paper_id}: LLM usage limit ({exc})", file=sys.stderr)
                failed += 1
            except Exception as exc:
                msg = f"{paper.paper_id}: {type(exc).__name__}: {exc}"
                cause = exc.__cause__
                while cause:
                    msg += f"\n  Caused by: {type(cause).__name__}: {cause}"
                    cause = cause.__cause__
                print(msg, file=sys.stderr)
                failed += 1

    if on_progress:
        on_progress(ProgressEvent(
            step=total, total=total, name="done", pct=1.0,
        ))

    ok = total - failed
    if total > 1:
        print(f"{ok} succeeded, {failed} failed out of {total} papers")
    elif failed == 0 and total == 1:
        pid = papers[0].paper_id
        stage_label = STAGE_NAMES.get(through - 1, "done")
        # last_result may be missing only when process_paper itself
        # raised; that path goes through ``failed += 1`` above, so we
        # are guaranteed a result here. Fall back defensively anyway.
        ran = getattr(last_result, "stages_run", None)
        if ran is not None and not ran:
            print(f"{pid}: already at {stage_label} (nothing to do)")
        else:
            print(f"{pid}: {stage_label}")

    return 1 if failed else 0
