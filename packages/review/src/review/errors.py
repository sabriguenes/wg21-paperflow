#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Error hierarchy for the review pipeline."""

from __future__ import annotations


class ReviewError(Exception):
    """Raised when the review pipeline cannot proceed.

    Always includes an actionable message explaining what went wrong
    and how to fix it. Chains the original cause via ``from``.
    """
