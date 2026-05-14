#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Backend registry for pipeline."""

from __future__ import annotations

from pipeline.session import SearchBackend
from pipeline.backends.brave import BraveBackend


def get_default_backend() -> SearchBackend:
    """Create the default search backend.

    Returns a ``BraveBackend`` that reads its API key from
    ``BRAVE_API_KEY``. Raises ``ValueError`` if the key is missing.
    """
    return BraveBackend()
