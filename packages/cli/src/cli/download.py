#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow download'."""

from __future__ import annotations

import asyncio
import sys


def command(args, backend):
    from cli.jobs import run_download
    from cli.progress import make_progress_handler
    from paperstore.stages import STAGES

    targets = args.targets
    force = getattr(args, "force", False)
    concurrency = getattr(args, "concurrency", None) or 4

    progress_ctx, on_progress = make_progress_handler("Download")

    with progress_ctx:
        result = asyncio.run(
            run_download(
                targets, backend,
                force=force,
                concurrency=concurrency,
                on_progress=on_progress,
            )
        )

    succeeded = result["succeeded"]
    failed = result["failed"]
    skipped = result["skipped"]

    for entry in failed:
        pid = entry["paper_id"]
        error = entry.get("error", "unknown error")
        backend.fail_paper(pid, stage=STAGES["download"], error=error)
        print(f"{pid}: {error}", file=sys.stderr)

    total = len(succeeded) + len(failed)
    if total > 1:
        print(f"{len(succeeded)} succeeded, {len(failed)} failed, {len(skipped)} skipped")
    elif total == 1 and not failed:
        print(f"{succeeded[0]}: downloaded")
    elif total == 0:
        print("No papers need downloading.")

    return 1 if failed else 0
