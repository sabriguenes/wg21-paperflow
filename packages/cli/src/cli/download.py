#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow download'."""

from __future__ import annotations

import argparse
import asyncio
import sys

from paperstore.backend import StorageBackend

from cli.jobs import DEFAULT_DOWNLOAD_CONCURRENCY


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from cli.jobs import run_download
    from cli.progress import progress_callbacks

    progress_ctx, on_total, on_progress = progress_callbacks("Downloading")

    with progress_ctx:
        result = asyncio.run(run_download(
            args.targets,
            backend,
            force=args.force,
            verify=args.verify,
            concurrency=args.concurrency or DEFAULT_DOWNLOAD_CONCURRENCY,
            on_total=on_total,
            on_progress=on_progress,
        ))

    succeeded = result.get("succeeded", [])
    skipped = result.get("skipped", [])
    failed = result.get("failed", [])

    print(f"Download: {len(succeeded)} downloaded, {len(skipped)} skipped, {len(failed)} failed.")
    if failed:
        for item in failed:
            print(f"  ERROR {item['paper_id']}: {item['error']}", file=sys.stderr)
        return 1
    return 0
