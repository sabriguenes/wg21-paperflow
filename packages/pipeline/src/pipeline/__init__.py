#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pipeline framework: step execution, agent runners, web tools, and shared utilities."""

from __future__ import annotations

from pipeline.errors import (
    HookMismatchError,
    MissingMetadataError,
    MissingSystemPromptError,
    PaperNotConvertedError,
    PaperNotDissectedError,
    PaperNotFoundError,
    PipelineError,
    PromptFileError,
    StepError,
    TransientStepError,
    ValidationStepError,
)
from pipeline.markdown import sanitize_md, sections
from pipeline.prompt import StepHooks, StepMeta, StepSpec, build_pipeline, parse_step_meta
from pipeline.runner import (
    DEFAULT_MODEL_SLOTS,
    StepContext,
    StepMetrics,
    dispatch,
    load_sections,
    run_agent,
    write_debug_file,
)
from pipeline.session import (
    FetchResponse,
    SearchBackend,
    SearchResponse,
    SearchResult,
    WebResearcher,
)
from pipeline.process import ensure_paper_md, process_paper
from pipeline.tasks import run_task
from pipeline.tools import make_read_paper_tool, wrap_source

__all__ = [
    "ensure_paper_md",
    "make_read_paper_tool",
    "process_paper",
    "build_pipeline",
    "DEFAULT_MODEL_SLOTS",
    "dispatch",
    "FetchResponse",
    "HookMismatchError",
    "load_sections",
    "MissingMetadataError",
    "MissingSystemPromptError",
    "PaperNotConvertedError",
    "PaperNotDissectedError",
    "parse_step_meta",
    "PaperNotFoundError",
    "PipelineError",
    "PromptFileError",
    "run_agent",
    "run_task",
    "sanitize_md",
    "SearchBackend",
    "SearchResponse",
    "SearchResult",
    "sections",
    "StepContext",
    "StepError",
    "StepMetrics",
    "StepHooks",
    "StepMeta",
    "StepSpec",
    "TransientStepError",
    "ValidationStepError",
    "WebResearcher",
    "wrap_source",
    "write_debug_file",
]
