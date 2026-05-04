#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""Paperstore CLI: inspect (and reconcile) what's stored under a workspace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from paperstore import SqliteBackend, WORKSPACE_ENV_VAR
from paperstore.errors import (
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperMdError,
    MissingSourceError,
)


def _cmd_list_years(backend: SqliteBackend) -> int:
    years = backend.list_years()
    if not years:
        print("No years in database.", file=sys.stderr)
        return 1
    for year, count in years:
        print(f"{year}\t{count} papers")
    return 0


def _cmd_show_year(backend: SqliteBackend, year: str) -> int:
    try:
        rows = backend.list_papers_for_year(year)
    except MissingMailingIndexError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    for row in rows:
        pid = row.get("paper_id", "?")
        title = row.get("title", "")
        print(f"{pid}\t{title}")
    return 0


def _has_source(backend: SqliteBackend, pid: str) -> bool:
    try:
        backend.get_source_path(pid)
        return True
    except MissingSourceError:
        return False


def _has_paper_md(backend: SqliteBackend, pid: str) -> bool:
    try:
        backend.get_paper_md(pid)
        return True
    except MissingPaperMdError:
        return False


def _cmd_ls_papers(backend: SqliteBackend, year: str | None) -> int:
    if year:
        try:
            rows = backend.list_papers_for_year(year)
        except MissingMailingIndexError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        ids = [row["paper_id"].upper() for row in rows]
    else:
        ids = backend.list_all_paper_ids()

    for pid in ids:
        marks = "".join(
            m
            for m, present in [
                ("s", _has_source(backend, pid)),
                ("m", _has_paper_md(backend, pid)),
            ]
            if present
        ) or "-"
        print(f"{pid}\t{marks}")
    return 0


def _cmd_show_paper(backend: SqliteBackend, paper_id: str) -> int:
    try:
        meta = backend.get_meta(paper_id)
    except MissingMetaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


def _cmd_reconcile(backend: SqliteBackend) -> int:
    counts = backend.reconcile()
    print(
        f"Backfilled: {counts['sources']} sources, "
        f"{counts['markdowns']} markdowns."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="paperstore",
        description=(
            "Inspect paperflow artifacts in a workspace directory. "
            "The 'reconcile' subcommand backfills DB rows from on-disk "
            "artifacts (non-destructive); all other commands are read-only."
        ),
    )
    parser.add_argument(
        "--workspace-dir",
        default=None,
        metavar="DIR",
        type=Path,
        help=f"Paperstore backend root (default: ${WORKSPACE_ENV_VAR}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-years", help="List stored years with paper counts.")

    show_y = sub.add_parser("show-year", help="List paper ids + titles for a year.")
    show_y.add_argument("year")

    ls = sub.add_parser(
        "ls-papers",
        help="List staged papers. Marks: s=source, m=paper.md.",
    )
    ls.add_argument("year", nargs="?", default=None)

    show_p = sub.add_parser("show-paper", help="Print a paper's metadata JSON.")
    show_p.add_argument("paper_id")

    sub.add_parser(
        "reconcile",
        help="Backfill DB rows from on-disk artifacts (non-destructive).",
    )

    args = parser.parse_args()
    if args.workspace_dir is None:
        backend_inst = SqliteBackend.from_env()
    else:
        backend_inst = SqliteBackend(args.workspace_dir)
    with backend_inst as backend:
        if args.command == "list-years":
            return _cmd_list_years(backend)
        if args.command == "show-year":
            return _cmd_show_year(backend, args.year)
        if args.command == "ls-papers":
            return _cmd_ls_papers(backend, args.year)
        if args.command == "show-paper":
            return _cmd_show_paper(backend, args.paper_id)
        if args.command == "reconcile":
            return _cmd_reconcile(backend)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
