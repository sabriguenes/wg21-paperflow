#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""Mailing CLI: scrape open-std.org mailing indexes (index only, no downloads).

Workspace dir defaults to ``$WG21_DATA_DIR``; pass ``--workspace-dir`` to
override.

Usage::

    mailing                          # show usage
    mailing all                      # scrape all years >= 2011
    mailing 2026                     # scrape all mailings for 2026
    mailing 2025 2026                # scrape multiple years
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from paperstore import WORKSPACE_ENV_VAR, SqliteBackend

from mailing.scrape import discover_years, fetch_all_mailings_for_year

_EARLIEST_YEAR = 2011


def _scrape_year(year: str, store: SqliteBackend) -> int:
    """Scrape all mailings for ``year`` and upsert into the store. Returns paper count."""
    all_mailings = fetch_all_mailings_for_year(year)
    if not all_mailings:
        print(f"  {year}: no mailings found")
        return 0
    total = 0
    for mailing_id, papers in sorted(all_mailings.items()):
        merged = store.upsert_mailing_index(mailing_id, papers)
        total += len(merged)
    print(f"  {year}: {total} papers across {len(all_mailings)} mailing(s)")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mailing",
        description="Scrape WG21 mailing indexes from open-std.org (index only).",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="YEAR_OR_ALL",
        help='Year(s) to scrape (e.g. 2026), or "all" for all years >= 2011.',
    )
    parser.add_argument(
        "--workspace-dir",
        default=None,
        metavar="DIR",
        type=Path,
        help=f"Paperstore backend root (default: ${WORKSPACE_ENV_VAR}).",
    )
    args = parser.parse_args()

    if not args.targets:
        parser.print_help()
        return 0

    if args.workspace_dir is None:
        store = SqliteBackend.from_env()
    else:
        store = SqliteBackend(args.workspace_dir)

    if args.targets == ["all"]:
        print("Discovering available years from open-std.org...")
        years = [y for y in discover_years() if int(y) >= _EARLIEST_YEAR]
        if not years:
            print("No years found.", file=sys.stderr)
            return 1
        print(f"Found {len(years)} years ({years[0]}-{years[-1]})")
        total = sum(_scrape_year(y, store) for y in years)
        print(f"\nDone: {total} total papers")
        return 0

    total = 0
    for target in args.targets:
        if not target.isdigit() or len(target) != 4:
            print(f"Error: expected a 4-digit year, got {target!r}", file=sys.stderr)
            return 2
        total += _scrape_year(target, store)
    print(f"\nDone: {total} total papers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
