#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pipeline framework: step execution, agent runners, web tools, and shared utilities."""

from __future__ import annotations

from pipeline.agents import AgentBackend
from pipeline.classifier_backends import (
    CLASSIFIER_BACKEND_REGISTRY,
    ClassifierBackend,
    NliCrossEncoderBackend,
    ZeroShotV2Backend,
)
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
from pipeline.model_backends import ModelBackend
from pipeline.prompt import StepHooks, StepMeta, StepSpec, build_pipeline, parse_step_meta
from pipeline.runner import (
    StepContext,
    StepMetrics,
    dispatch,
    load_sections,
    run_agent,
    write_debug_file,
)
from pipeline.services import (
    load_classifiers,
    load_services,
    load_transformer_providers,
    resolve_classifier_slots,
    resolve_slots,
    resolve_transformer_provider,
)
from pipeline.transformer_backend import (
    CrossEncoderBackend,
    EmbeddingBackend,
    HFZeroShotBackend,
    TransformerBackend,
    TransformerProvider,
    default_auto_provider,
    run_mps_correctness_selftest,
)
from pipeline.session import (
    FetchResponse,
    SearchBackend,
    SearchResponse,
    SearchResult,
    WebResearcher,
)
from pipeline.postconditions import (
    ProcessResult,
    postcondition_satisfied,
    truthful_status,
)
from pipeline.process import ensure_paper_md, process_paper
from pipeline.tasks import run_task
from pipeline.tools import make_read_paper_tool, wrap_source

__all__ = [
    "AgentBackend",
    "CLASSIFIER_BACKEND_REGISTRY",
    "ClassifierBackend",
    "CrossEncoderBackend",
    "default_auto_provider",
    "EmbeddingBackend",
    "ensure_paper_md",
    "HFZeroShotBackend",
    "load_classifiers",
    "load_transformer_providers",
    "make_read_paper_tool",
    "ModelBackend",
    "NliCrossEncoderBackend",
    "resolve_classifier_slots",
    "resolve_transformer_provider",
    "run_mps_correctness_selftest",
    "TransformerBackend",
    "TransformerProvider",
    "ZeroShotV2Backend",
    "process_paper",
    "build_pipeline",
    "dispatch",
    "FetchResponse",
    "HookMismatchError",
    "load_sections",
    "load_services",
    "MissingMetadataError",
    "MissingSystemPromptError",
    "PaperNotConvertedError",
    "PaperNotDissectedError",
    "parse_step_meta",
    "PaperNotFoundError",
    "PipelineError",
    "postcondition_satisfied",
    "ProcessResult",
    "PromptFileError",
    "resolve_slots",
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
    "truthful_status",
    "ValidationStepError",
    "WebResearcher",
    "wrap_source",
    "write_debug_file",
]
