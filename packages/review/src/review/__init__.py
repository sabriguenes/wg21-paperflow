#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""WG21 paper review pipeline."""

from __future__ import annotations

from review.errors import ReviewError
from review.pipeline import review_paper

__all__ = ["review_paper", "ReviewError"]
