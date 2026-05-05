#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow mailing'."""

from __future__ import annotations

import argparse
import asyncio
import sys

from paperstore.backend import StorageBackend


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    if not args.targets:
        args._parser.print_help()
        return 0

    from cli.jobs import run_mailing
    result = asyncio.run(run_mailing(args.targets, backend, force=args.force))

    succeeded = result.get("succeeded", [])
    skipped = result.get("skipped", [])
    failed = result.get("failed", [])

    for item in succeeded:
        print(f"  {item['year']}: {item['papers']} papers")
    if skipped:
        print(f"Skipped {len(skipped)} already-indexed year(s).")
    if failed:
        for item in failed:
            print(f"  ERROR {item['year']}: {item['error']}", file=sys.stderr)
        return 1

    print(f"\nMailing sync complete: {len(succeeded)} year(s) fetched.")
    return 0
