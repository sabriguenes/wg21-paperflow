#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""WG21 mailing index scraper + paper-source downloader."""

from __future__ import annotations

from mailing.download import content_length, default_client, download_paper
from mailing.scrape import (
    discover_years,
    fetch_all_mailings_for_year,
    fetch_paper_ids_for_year,
    fetch_papers_for_year,
    parse_all_mailings,
    parse_papers_for_mailing,
)

__all__ = [
    "content_length",
    "default_client",
    "discover_years",
    "download_paper",
    "fetch_all_mailings_for_year",
    "fetch_paper_ids_for_year",
    "fetch_papers_for_year",
    "parse_all_mailings",
    "parse_papers_for_mailing",
]
