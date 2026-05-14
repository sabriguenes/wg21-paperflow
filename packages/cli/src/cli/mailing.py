#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow mailing'."""

from __future__ import annotations

import sys


def command(args, backend):
    from mailing.scrape import discover_years, fetch_all_mailings_for_year
    from cli.progress import make_progress_handler
    from datetime import datetime, timezone

    current_year = str(datetime.now(timezone.utc).year)
    EARLIEST = 2011
    all_years = discover_years()
    years = [y for y in all_years if int(y) >= EARLIEST]

    progress_ctx, on_progress = make_progress_handler("Scraping mailings")
    succeeded = 0
    skipped = 0
    failed = 0

    from paperstore.progress import ProgressEvent

    total = len(years)
    with progress_ctx:
        for i, year in enumerate(sorted(years)):
            if on_progress:
                on_progress(ProgressEvent(
                    step=i, total=total, name=f"Mailing {year}",
                    pct=i / total if total else 1.0,
                ))
            if year < current_year and backend.has_year(year):
                skipped += 1
                continue
            try:
                mailings = fetch_all_mailings_for_year(year)
                for mid, papers in sorted(mailings.items()):
                    backend.upsert_year(year, papers)
                succeeded += 1
            except Exception as exc:
                print(f"  Failed {year}: {exc}", file=sys.stderr)
                failed += 1
        if on_progress:
            on_progress(ProgressEvent(
                step=total, total=total, name="done", pct=1.0,
            ))

    total_papers = len(backend.list_all_paper_ids())
    print(f"Mailing index: {succeeded} years scraped, {skipped} skipped, {failed} failed. {total_papers} papers in store.")
    return 1 if failed else 0
