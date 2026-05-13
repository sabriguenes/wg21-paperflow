#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""WG21 mailing index scraper + paper-source downloader."""

from __future__ import annotations

# DEFAULT_USER_AGENT must be bound before the submodule imports below, because
# mailing.download and mailing.scrape do `from mailing import DEFAULT_USER_AGENT`
# at module load time. Hence the E402 waivers on the submodule imports.
DEFAULT_USER_AGENT = "paperflow/0.1 (+https://github.com/cppalliance/wg21-paperflow)"

from mailing.download import content_length, default_client, download_paper  # noqa: E402
from mailing.scrape import (  # noqa: E402
    discover_years,
    fetch_all_mailings_for_year,
    fetch_paper_ids_for_year,
    fetch_papers_for_year,
    parse_all_mailings,
    parse_papers_for_mailing,
)

__all__ = [
    "DEFAULT_USER_AGENT",
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
