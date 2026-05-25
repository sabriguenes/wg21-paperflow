#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Error hierarchy for pipeline execution.

Three categories, each with a different response:

- **User-fixable**: edit the prompt file or run a paperflow command.
  ``PromptFileError`` and its subclasses carry the step name and
  expected format. ``PaperNotFoundError`` and ``PaperNotConvertedError``
  carry the paperflow command to run.
- **Transient**: retry. ``TransientStepError`` wraps API timeouts,
  rate limits, and network errors.
- **Hard runtime**: pipeline bug. ``ValidationStepError`` wraps LLM
  output that did not match the expected Pydantic schema.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base for all pipeline errors."""


class PaperNotFoundError(PipelineError):
    """Paper not in paperstore.

    Message includes the paperflow command to run.
    """


class PaperNotConvertedError(PipelineError):
    """Paper has no converted markdown.

    Message includes the paperflow convert command.
    """


class PromptFileError(PipelineError):
    """The prompt file has a structural problem the user must fix.

    Every subclass carries the step name (if applicable) and a
    description of the expected format.
    """


class MissingMetadataError(PromptFileError):
    """A step section is missing a required metadata field.

    Required fields: ``**Model:**``, ``**Execution:**``.
    """


class MissingSystemPromptError(PromptFileError):
    """A prompt file lacks a non-empty ``## System Prompt`` section."""


class HookMismatchError(PromptFileError):
    """A step in the prompt file has no registered Python hook,
    a hook is registered for a step that does not exist,
    or a declared tool has no matching callable in the registry.
    """


class ServiceConfigError(PipelineError):
    """Raised when SERVICES.toml service or slot resolution fails.

    Covers unknown backend keys, required-api-key-env mismatches, and
    bound slots whose declared env var is missing, empty, or
    whitespace-only.
    """


class ModelBackendConfigError(PipelineError):
    """Raised when a :class:`ModelBackend` receives an invalid runtime value
    (e.g. ``request_limit < 1``).
    """


class MalformedModelOutputError(PipelineError):
    """Raised when a model response could not be parsed into the expected
    structured form even after the backend's internal retries.
    """


class TransformerConfigError(PipelineError):
    """Raised when a :class:`TransformerProvider` TOML entry is malformed."""


class BackendConfigError(PipelineError):
    """Raised when a search backend (e.g. Brave) is misconfigured at construction."""


class UnknownStageError(PipelineError):
    """Raised when :func:`pipeline.process.run_pipeline` is asked to run an
    unknown stage.
    """


class CapabilityMismatchError(PipelineError):
    """A step's declared requirements exceed the assigned agent's capabilities.

    Raised at pipeline-construction time (before any LLM call) when a
    step declares ``**Tools:**`` but its assigned agent wraps a
    ``tools_capable=False`` backend, or when an agent carries a
    ``thinking_budget`` but its backend is ``thinking_capable=False``.
    The message names the slot, the resolved service, and the
    backend class so the user can fix the SERVICES.toml binding.
    """


class StepError(PipelineError):
    """A step failed during execution.

    Wraps the cause with step index and name for diagnostics.
    """

    def __init__(self, step: int, name: str, cause: Exception) -> None:
        self.step = step
        self.name = name
        self.cause = cause
        super().__init__(f"Step {step} ({name}) failed: {cause}")
        self.__cause__ = cause


class TransientStepError(StepError):
    """Retryable failure: API timeout, rate limit, network error."""


class ValidationStepError(StepError):
    """LLM output did not match the expected Pydantic schema."""
