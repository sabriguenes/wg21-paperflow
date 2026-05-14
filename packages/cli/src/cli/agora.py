#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for ``paperflow agora``.

Plans an r/wg21 thread for a dissected WG21 paper (analysis phase
only). Reads the paper and dissect output from paperstore and writes
the planned ``Thread`` as ``{pid}.agora.json`` via the backend.
Generation-phase fields stay ``None``.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

import pydantic_ai.exceptions

from paperstore.backend import StorageBackend

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _resolve_pid(target: str, backend: StorageBackend) -> str:
    """Normalize a paper ID target to uppercase.

    Accepts full IDs (``P4003R3``) or short forms (``p4003``). For
    short forms without a revision suffix, looks up the latest
    revision in the store.
    """
    pid = target.strip().upper()

    if "R" in pid and pid.split("R")[-1].isdigit():
        return pid

    result = backend.resolve_year_for_paper(pid)
    if result is not None:
        return result[1].paper_id

    return pid


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from agora import AgoraError, agora_paper, agora_since
    from cli.progress import make_progress_handler

    target = args.targets[0]

    trace = args.trace is not None
    stop_after = args.trace if args.trace is not None and args.trace >= 0 else None

    if _MONTH_RE.match(target):
        progress_ctx, on_progress = make_progress_handler("Batch agora")
        with progress_ctx:
            results = asyncio.run(
                agora_since(
                    target, backend,
                    on_progress=on_progress,
                    debug=args.debug,
                    trace=trace,
                )
            )

        ok = sum(1 for r in results if r["status"] == "ok")
        failed = sum(1 for r in results if r["status"] == "error")
        print(
            f"Batch agora: {ok} succeeded, {failed} failed "
            f"out of {len(results)} papers"
        )
        for r in results:
            if r["status"] == "error":
                print(
                    f"  FAILED: {r['paper_id']}: {r['error']}",
                    file=sys.stderr,
                )
        return 1 if failed else 0

    pid = _resolve_pid(target, backend)

    progress_ctx, on_progress = make_progress_handler("Planning thread")

    with progress_ctx:
        try:
            result = asyncio.run(
                agora_paper(
                    pid, backend,
                    on_progress=on_progress,
                    stop_after=stop_after,
                    debug=args.debug,
                    trace=trace,
                )
            )
        except AgoraError as exc:
            print(f"Agora failed: {exc}", file=sys.stderr)
            return 1
        except pydantic_ai.exceptions.UsageLimitExceeded as exc:
            print(
                f"Agora aborted: LLM usage limit reached ({exc})",
                file=sys.stderr,
            )
            return 1
        except Exception as exc:  # last-resort firewall: log full chain
            msg = f"Agora failed unexpectedly: {type(exc).__name__}: {exc}"
            cause = exc.__cause__
            while cause:
                msg += f"\n  Caused by: {type(cause).__name__}: {cause}"
                cause = cause.__cause__
            print(msg, file=sys.stderr)
            return 1

    if stop_after is not None:
        # agora_paper returned the partial trace string; CLI writes it.
        trace_path = backend.get_trace_md_path(pid, "agora")
        trace_path.write_text(result, encoding="utf-8")  # type: ignore[arg-type]
        print(f"Trace written to {trace_path}")
        return 0

    out_path = backend.get_agora_path(pid)
    if not out_path.exists() or out_path.stat().st_size == 0:
        print(
            f"Agora write failed: {out_path} is empty or missing",
            file=sys.stderr,
        )
        return 1

    print(f"Thread blueprint written to {out_path}")
    return 0
