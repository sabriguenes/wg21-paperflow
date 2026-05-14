#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Paper processing stage constants.

The status integer on each paper row tracks pipeline progress.
Positive values indicate the next action needed. Negative values
indicate failure at a specific stage: failed_status = -(stage + 1),
recovery: retry_stage = abs(status) - 1. The paper row's ``error``
column stores the cause.
"""

STAGES = {
    "download": 0,
    "convert": 1,
    "dissect": 2,
    "advocatus": 3,
    "agora": 4,
    "herald": 5,
    "ready": 6,
}

STAGE_NAMES = {v: k for k, v in STAGES.items()}


def failed_status(stage: int) -> int:
    """Status value for a paper that failed at the given stage."""
    return -(stage + 1)


def failed_stage(status: int) -> int:
    """Recover the stage from a negative (failed) status."""
    return abs(status) - 1
