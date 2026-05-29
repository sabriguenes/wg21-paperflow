#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow assay'."""

from __future__ import annotations

import asyncio
import sys

from paperstore.errors import MissingMetaError, MissingPaperMdError


def command(args, backend):
    from cli.progress import make_progress_handler

    pid = args.targets[0].upper()
    rerender = getattr(args, "rerender", False)

    if rerender:
        return _rerender(pid, backend)

    debug = getattr(args, "debug", False)
    trace = getattr(args, "trace", False)
    step_val = getattr(args, "step", None)
    if step_val is not None:
        trace = True
    stop_after = step_val

    try:
        backend.get_meta(pid)
    except MissingMetaError:
        print(f"Error: paper '{pid}' not found in paperstore.", file=sys.stderr)
        print("Run 'paperflow mailing <year>' to index it.", file=sys.stderr)
        return 1

    try:
        backend.get_paper_md(pid)
    except MissingPaperMdError:
        print(f"Error: paper '{pid}' has no converted markdown.", file=sys.stderr)
        print(f"Run 'paperflow convert {pid}' first.", file=sys.stderr)
        return 1

    progress_ctx, on_progress = make_progress_handler("Assay")

    try:
        with progress_ctx:
            from assay import assay_paper
            no_cpp_mcp = getattr(args, "no_cpp_mcp", False)
            cpp_mcp_url = getattr(args, "cpp_mcp_url", None)
            report = asyncio.run(assay_paper(
                pid, backend,
                debug=debug, trace=trace,
                stop_after=stop_after,
                on_progress=on_progress,
                no_cpp_mcp=no_cpp_mcp,
                cpp_mcp_url=cpp_mcp_url,
            ))

        if stop_after is None:
            out_path = backend.write_assay_md(pid, report)
            print(f"{pid}: assay complete -> {out_path}")

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


def _rerender(pid: str, backend) -> int:
    """Regenerate report from DB without re-running the pipeline."""
    try:
        backend.get_meta(pid)
    except MissingMetaError:
        print(f"Error: paper '{pid}' not found in paperstore.", file=sys.stderr)
        return 1

    try:
        from assay.render import rerender_report
        report = rerender_report(pid, backend)
        out_path = backend.write_assay_md(pid, report)
        print(f"{pid}: report regenerated -> {out_path}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0
