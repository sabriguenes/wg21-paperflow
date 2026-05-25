#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Error hierarchy for the mailing scraper / downloader."""

from __future__ import annotations


class MailingError(Exception):
    """Base class for mailing-raised exceptions."""


class InvalidSourceUrlError(MailingError):
    """Raised when a paper source URL does not end with a supported suffix."""
