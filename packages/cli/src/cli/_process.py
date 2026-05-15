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
    trace_val = getattr(args, "trace", None)
    trace = trace_val is not None
    force = getattr(args, "force", False)

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
    with progress_ctx:
        for i, paper in enumerate(papers):
            if on_progress:
                stage_name = STAGE_NAMES.get(paper.status, "processing")
                on_progress(ProgressEvent(
                    step=i, total=total,
                    name=f"{paper.paper_id} - {stage_name}",
                    pct=i / total if total else 1.0,
                ))
            try:
                asyncio.run(
                    process_paper(
                        paper.paper_id, backend,
                        through=through,
                        debug=debug,
                        trace=trace,
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
        print(f"{papers[0].paper_id}: {STAGE_NAMES.get(through - 1, 'done')}")

    return 1 if failed else 0
