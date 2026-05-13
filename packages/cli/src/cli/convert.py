#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
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


_DEFAULT_CONVERT_CONCURRENCY = 4
_DEFAULT_QA_WORKERS = 1
_DEFAULT_QA_TIMEOUT = 120


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    if args.qa or args.qa_json:
        return _qa_command(args, backend)
    return _convert_command(args, backend)


def _convert_command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from cli.jobs import run_convert
    from cli.progress import make_progress_handler

    progress_ctx, on_progress = make_progress_handler("Converting")

    with progress_ctx:
        result = asyncio.run(run_convert(
            args.targets,
            backend,
            force=args.force,
            concurrency=args.concurrency or _DEFAULT_CONVERT_CONCURRENCY,
            write_prompts=not args.no_prompts,
            on_progress=on_progress,
        ))

    succeeded = result.get("succeeded", [])
    skipped = result.get("skipped", [])
    failed = result.get("failed", [])

    print(f"Convert: {len(succeeded)} converted, {len(skipped)} skipped, {len(failed)} failed.")
    if failed:
        for item in failed:
            print(f"  ERROR {item['paper_id']}: {item['error']}", file=sys.stderr)
        return 1
    return 0


def _qa_command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from cli.jobs import run_qa

    result = run_qa(
        args.targets,
        backend,
        json_path=args.qa_json,
        workers=args.workers or _DEFAULT_QA_WORKERS,
        timeout=args.timeout or _DEFAULT_QA_TIMEOUT,
    )

    for entry in result["skipped"]:
        print(
            f"Skipping {entry['paper_id']}: no paper markdown. Run 'paperflow convert' first.",
            file=sys.stderr,
        )

    if not result["succeeded"]:
        print("No markdown available for QA.", file=sys.stderr)
        return 1
    return 0
