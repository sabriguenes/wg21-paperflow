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
    BackendConfigError,
    CapabilityMismatchError,
    HookMismatchError,
    MalformedModelOutputError,
    MissingMetadataError,
    MissingSystemPromptError,
    ModelBackendConfigError,
    PaperNotConvertedError,
    PaperNotFoundError,
    PipelineError,
    PromptFileError,
    ServiceConfigError,
    StepError,
    TransformerConfigError,
    TransientStepError,
    UnknownStageError,
    ValidationStepError,
)
from pipeline.markdown import extract_code_blocks, sanitize_md, sections
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
    ServiceRegistry,
    load_classifiers,
    load_services,
    load_transformer_providers,
    parse_pipeline_config,
    parse_service_overrides,
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
    ConvertReport,
    ProcessResult,
    postcondition_satisfied,
    truthful_status,
)
from pipeline.process import ensure_paper_md, process_paper
from pipeline.tasks import run_task
from pipeline.tokens import CHARS_PER_TOKEN, est_tokens, tokens_to_chars
from pipeline.tools import make_read_paper_tool, wrap_source
from pipeline.validate import validate_capabilities

__all__ = [
    "AgentBackend",
    "BackendConfigError",
    "CapabilityMismatchError",
    "MalformedModelOutputError",
    "ModelBackendConfigError",
    "ServiceConfigError",
    "TransformerConfigError",
    "UnknownStageError",
    "CLASSIFIER_BACKEND_REGISTRY",
    "ClassifierBackend",
    "CrossEncoderBackend",
    "default_auto_provider",
    "EmbeddingBackend",
    "ensure_paper_md",
    "extract_code_blocks",
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
    "parse_step_meta",
    "PaperNotFoundError",
    "parse_pipeline_config",
    "parse_service_overrides",
    "PipelineError",
    "postcondition_satisfied",
    "ProcessResult",
    "ConvertReport",
    "PromptFileError",
    "resolve_slots",
    "ServiceRegistry",
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
    "validate_capabilities",
    "ValidationStepError",
    "WebResearcher",
    "wrap_source",
    "write_debug_file",
    "CHARS_PER_TOKEN",
    "est_tokens",
    "tokens_to_chars",
]
