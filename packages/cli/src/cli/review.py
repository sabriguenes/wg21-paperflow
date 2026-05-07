#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow review'."""

from __future__ import annotations

import argparse
import asyncio
import sys

import pydantic_ai.exceptions

from paperstore.backend import StorageBackend


def _resolve_pid(target: str, backend: StorageBackend) -> str:
    """Normalize a paper ID target to uppercase.

    Accepts full IDs (P4003R3) or short forms (p4003). For short forms
    without a revision suffix, looks up the latest revision in the store.
    """
    pid = target.strip().upper()

    if "R" in pid and pid.split("R")[-1].isdigit():
        return pid

    result = backend.resolve_year_for_paper(pid)
    if result is not None:
        return result[1]["paper_id"]

    return pid


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from review import ReviewError, review_paper
    from cli.progress import make_progress_handler

    pid = _resolve_pid(args.targets[0], backend)
    stop_after = args.stop_after

    progress_ctx, on_progress = make_progress_handler("Extracting")

    with progress_ctx:
        try:
            report = asyncio.run(
                review_paper(
                    pid, backend,
                    on_progress=on_progress,
                    stop_after=stop_after,
                )
            )
        except ReviewError as exc:
            print(f"Review failed: {exc}", file=sys.stderr)
            return 1
        except pydantic_ai.exceptions.UsageLimitExceeded as exc:
            print(f"Review aborted: LLM usage limit reached ({exc})", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Review failed unexpectedly: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    if stop_after is not None:
        return 0

    out_path = backend.write_review_md(pid, report)
    if not out_path.exists() or out_path.stat().st_size == 0:
        print(f"Review write failed: {out_path} is empty or missing", file=sys.stderr)
        return 1

    print(f"Review written to {out_path}")
    return 0
