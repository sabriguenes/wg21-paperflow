#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Shared runner for LLM pipeline CLI commands.

``run_llm_command`` replaces the three near-identical ``command()``
functions in dissect.py, advocatus.py, and agora.py. Each command
module becomes a thin wrapper that passes its package-specific
callables and labels.

Auto-prerequisites: when a pipeline raises ``PaperNotDissectedError``
or ``PaperNotConvertedError``, the runner automatically downloads,
converts, and/or dissects the paper, then retries. Each prerequisite
runs at most once. ``PaperNotFoundError`` (needs mailing) is a hard
stop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Callable

import pydantic_ai.exceptions

from paperstore.backend import StorageBackend

from cli.targets import MONTH_RE, resolve_pid


def _ensure_converted(pid: str, backend: StorageBackend) -> None:
    """Download and convert a paper if not already done."""
    from cli.jobs import run_convert, run_download

    print(f"  Running download + convert for {pid}...")
    asyncio.run(run_download([pid], backend))
    asyncio.run(run_convert([pid], backend))


def _ensure_dissected(pid: str, backend: StorageBackend) -> None:
    """Run dissect for a paper if not already done."""
    from dissect import dissect_paper

    print(f"  Running dissect for {pid}...")
    asyncio.run(dissect_paper(pid, backend))


def run_llm_command(
    args: argparse.Namespace,
    backend: StorageBackend,
    *,
    paper_fn: Callable,
    batch_fn: Callable,
    write_fn: Callable | None,
    output_check_fn: Callable[[str], Path],
    verb: str,
    progress_label: str,
    trace_tool: str,
    success_msg: str = "Output written to {path}",
) -> int:
    """Run an LLM pipeline command with auto-prerequisites.

    Handles batch (YYYY-MM) and single-paper modes, progress display,
    exception firewall, trace file writing, and output verification.
    """
    from pipeline import PaperNotConvertedError, PaperNotDissectedError, PipelineError
    from cli.progress import make_progress_handler

    target = args.targets[0]
    trace = args.trace is not None
    stop_after = args.trace if args.trace is not None and args.trace >= 0 else None

    if MONTH_RE.match(target):
        return _run_batch(
            target, backend, batch_fn=batch_fn, verb=verb,
            debug=args.debug, trace=trace,
        )

    pid = resolve_pid(target, backend)
    progress_ctx, on_progress = make_progress_handler(progress_label)

    with progress_ctx:
        try:
            result = asyncio.run(
                paper_fn(
                    pid, backend,
                    on_progress=on_progress,
                    stop_after=stop_after,
                    debug=args.debug,
                    trace=trace,
                )
            )
        except PaperNotDissectedError:
            print(f"No dissect output for {pid}, running prerequisites...")
            _ensure_converted(pid, backend)
            _ensure_dissected(pid, backend)
            result = asyncio.run(
                paper_fn(
                    pid, backend,
                    on_progress=on_progress,
                    stop_after=stop_after,
                    debug=args.debug,
                    trace=trace,
                )
            )
        except PaperNotConvertedError:
            print(f"No markdown for {pid}, running prerequisites...")
            _ensure_converted(pid, backend)
            result = asyncio.run(
                paper_fn(
                    pid, backend,
                    on_progress=on_progress,
                    stop_after=stop_after,
                    debug=args.debug,
                    trace=trace,
                )
            )
        except PipelineError as exc:
            print(f"{verb} failed: {exc}", file=sys.stderr)
            return 1
        except pydantic_ai.exceptions.UsageLimitExceeded as exc:
            print(f"{verb} aborted: LLM usage limit reached ({exc})", file=sys.stderr)
            return 1
        except Exception as exc:
            msg = f"{verb} failed unexpectedly: {type(exc).__name__}: {exc}"
            cause = exc.__cause__
            while cause:
                msg += f"\n  Caused by: {type(cause).__name__}: {cause}"
                cause = cause.__cause__
            print(msg, file=sys.stderr)
            return 1

    if stop_after is not None:
        trace_path = backend.get_trace_md_path(pid, trace_tool)
        trace_path.write_text(
            result if isinstance(result, str) else str(result),
            encoding="utf-8",
        )
        print(f"Trace written to {trace_path}")
        return 0

    if write_fn is not None:
        out_path = write_fn(pid, result)
    else:
        out_path = output_check_fn(pid)

    if not out_path.exists() or out_path.stat().st_size == 0:
        print(f"{verb} write failed: {out_path} is empty or missing", file=sys.stderr)
        return 1

    print(success_msg.format(path=out_path))
    return 0


def _run_batch(
    month: str,
    backend: StorageBackend,
    *,
    batch_fn: Callable,
    verb: str,
    debug: bool = False,
    trace: bool = False,
) -> int:
    """Run a batch LLM pipeline for all papers in a month."""
    from cli.jobs import run_full
    from cli.progress import make_progress_handler

    year = month[:4]
    print(f"Ensuring prerequisites for {year}...")
    asyncio.run(run_full([year], backend))

    progress_ctx, on_progress = make_progress_handler(f"Batch {verb.lower()}")
    with progress_ctx:
        results = asyncio.run(
            batch_fn(
                month, backend,
                on_progress=on_progress,
                debug=debug,
                trace=trace,
            )
        )

    ok = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "error")
    print(f"Batch {verb.lower()}: {ok} succeeded, {failed} failed out of {len(results)} papers")
    for r in results:
        if r["status"] == "error":
            print(f"  FAILED: {r['paper_id']}: {r['error']}", file=sys.stderr)
    return 1 if failed else 0
