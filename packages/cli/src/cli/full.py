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

from cli.jobs import DEFAULT_DOWNLOAD_CONCURRENCY


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from cli.jobs import run_full
    results = asyncio.run(run_full(
        args.targets,
        backend,
        force=args.force,
        verify=args.verify,
        concurrency=args.concurrency or DEFAULT_DOWNLOAD_CONCURRENCY,
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
