#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Advocatus: WG21 paper examination pipeline (Advocatus Diaboli / Defensor Causae)."""

from __future__ import annotations

from pipeline.errors import (
    HookMismatchError,
    MissingMetadataError,
    PaperNotConvertedError,
    PaperNotFoundError,
    PipelineError,
    PromptFileError,
    StepError,
    TransientStepError,
    ValidationStepError,
)
from advocatus.pipeline import advocatus_paper, advocatus_since

__all__ = [
    "advocatus_paper",
    "advocatus_since",
    "PipelineError",
    "PaperNotFoundError",
    "PaperNotConvertedError",
    "PromptFileError",
    "MissingMetadataError",
    "HookMismatchError",
    "StepError",
    "TransientStepError",
    "ValidationStepError",
]
