#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow convert'."""

from __future__ import annotations

import argparse
import sys

from paperstore.backend import StorageBackend


_CONTENT_CHECK_WORKERS = 1
_CONTENT_CHECK_TIMEOUT = 120


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    if getattr(args, "check_content", False) or getattr(args, "check_content_json", None):
        return _check_content_command(args, backend)
    from cli._process import run_process_command
    return run_process_command(args, backend, through=2)


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
