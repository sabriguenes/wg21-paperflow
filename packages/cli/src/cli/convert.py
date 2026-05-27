#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow convert'."""

from __future__ import annotations

import argparse
import asyncio
import sys

from paperstore.backend import StorageBackend


_CONTENT_CHECK_WORKERS = 1
_CONTENT_CHECK_TIMEOUT = 120


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    if getattr(args, "check_content", False) or getattr(args, "check_content_json", None):
        return _check_content_command(args, backend)

    from cli.jobs import run_convert
    from cli.progress import make_progress_handler
    from paperstore.stages import STAGES

    targets = args.targets
    force = getattr(args, "force", False)
    concurrency = getattr(args, "concurrency", None) or 4
    extract_vector = getattr(args, "extract_vector_images", False)
    whiteout_text = getattr(args, "vector_whiteout_text", False)

    progress_ctx, on_progress = make_progress_handler("Convert")

    with progress_ctx:
        result = asyncio.run(
            run_convert(
                targets, backend,
                force=force,
                concurrency=concurrency,
                extract_vector=extract_vector,
                whiteout_text=whiteout_text,
                on_progress=on_progress,
            )
        )

    succeeded = result["succeeded"]
    failed = result["failed"]
    skipped = result["skipped"]

    for entry in failed:
        pid = entry["paper_id"]
        error = entry.get("error", "unknown error")
        backend.fail_paper(pid, stage=STAGES["convert"], error=error)
        print(f"{pid}: {error}", file=sys.stderr)

    total = len(succeeded) + len(failed)
    if total > 1:
        print(f"{len(succeeded)} succeeded, {len(failed)} failed, {len(skipped)} skipped")
    elif total == 1 and not failed:
        print(f"{succeeded[0]}: converted")
    elif total == 0:
        print("No papers need converting.")

    return 1 if failed else 0


def _check_content_command(
    args: argparse.Namespace, backend: StorageBackend,
) -> int:
    """Run the content-coverage check instead of converting.

    Short-circuits the convert pipeline. Reads each paper's staged
    source and converted markdown, scores coverage, and emits a ranked
    report.
    """
    from cli.jobs import run_content_check

    result = run_content_check(
        args.targets,
        backend,
        json_path=args.check_content_json,
        workers=_CONTENT_CHECK_WORKERS,
        timeout=_CONTENT_CHECK_TIMEOUT,
    )

    for entry in result["skipped"]:
        print(
            f"Skipping {entry['paper_id']}: {entry['reason']}.",
            file=sys.stderr,
        )

    if not result["succeeded"]:
        print(
            "No papers available for content check. Run "
            "'paperflow convert' to produce markdown first.",
            file=sys.stderr,
        )
        return 1
    return 0
