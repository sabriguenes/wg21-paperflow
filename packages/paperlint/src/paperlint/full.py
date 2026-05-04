#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow full' (all three stages)."""

from __future__ import annotations

import argparse
import asyncio
import sys

from paperstore.backend import StorageBackend


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "full",
        help="Run all three stages: mailing + download + convert",
        description=(
            "Full pipeline: scrape mailing index, download sources, and convert to "
            "markdown. Each stage is idempotent; already-done work is skipped."
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
        help="Redo every stage even if already complete.",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="HEAD-check staged source files against Content-Length.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=10,
        metavar="N",
        help="Concurrency limit for network stages (default: 10).",
    )
    return p


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from paperlint.jobs import run_full
    results = asyncio.run(run_full(
        args.targets,
        backend,
        force=args.force,
        verify=args.verify,
        concurrency=args.concurrency,
    ))

    any_failed = False
    for stage, result in results.items():
        succeeded = len(result.get("succeeded", []))
        skipped = len(result.get("skipped", []))
        failed_list = result.get("failed", [])
        print(f"  {stage}: {succeeded} ok, {skipped} skipped, {len(failed_list)} failed")
        if failed_list:
            any_failed = True
            for item in failed_list:
                key = item.get("paper_id") or item.get("year", "?")
                print(f"    ERROR {key}: {item.get('error', '?')}", file=sys.stderr)

    return 1 if any_failed else 0
