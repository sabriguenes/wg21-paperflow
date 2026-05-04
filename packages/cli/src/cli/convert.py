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
from pathlib import Path

from paperstore.backend import StorageBackend


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "convert",
        help="Convert staged source files to markdown (no LLM)",
        description=(
            "Convert staged PDF/HTML sources to markdown using tomd. "
            "Hard-fails if the source is not yet staged - run 'paperflow download' first."
        ),
    )
    p.add_argument(
        "targets",
        nargs="+",
        metavar="TARGET",
        help='Year (2026), paper id(s) (P3642R4 ...), or "all".',
    )
    p.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Re-convert even if markdown already exists.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=4,
        metavar="N",
        help="Number of concurrent conversion threads (default: 4).",
    )
    p.add_argument(
        "--no-prompts",
        action="store_true",
        help="Skip writing the .prompts.json intermediate.",
    )
    p.add_argument(
        "--qa",
        action="store_true",
        help="Score existing markdown quality instead of converting (dev/debug; reads paper.md only, never reconverts).",
    )
    p.add_argument(
        "--qa-json",
        type=Path,
        metavar="PATH",
        help="Write per-paper QA metrics as JSON to PATH (implies --qa).",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="QA parallelism (default: 1).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="QA straggler timeout in seconds (default: 120).",
    )
    return p


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    if args.qa or args.qa_json:
        return _qa_command(args, backend)
    return _convert_command(args, backend)


def _convert_command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from cli.jobs import run_convert
    from cli.progress import progress_callbacks

    progress_ctx, on_total, on_progress = progress_callbacks("Converting")

    with progress_ctx:
        result = asyncio.run(run_convert(
            args.targets,
            backend,
            force=args.force,
            concurrency=args.concurrency,
            write_prompts=not args.no_prompts,
            on_total=on_total,
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
        workers=args.workers,
        timeout=args.timeout,
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
