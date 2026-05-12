#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Error hierarchy for the review pipeline.

Three categories, each with a different response:

- **User-fixable**: edit ``review.md`` or run a paperflow command.
  ``PromptFileError`` and its subclasses carry the step name and
  expected format. ``PaperNotFoundError`` and ``PaperNotConvertedError``
  carry the paperflow command to run.
- **Transient**: retry. ``TransientStepError`` wraps API timeouts,
  rate limits, and network errors.
- **Hard runtime**: pipeline bug. ``ValidationStepError`` wraps LLM
  output that did not match the expected Pydantic schema.
"""

from __future__ import annotations


class ReviewError(Exception):
    """Base for all review pipeline errors."""


# -- User-fixable: edit review.md or run a paperflow command ------------------


class PaperNotFoundError(ReviewError):
    """Paper not in paperstore.

    Message includes the paperflow command to run.
    """


class PaperNotConvertedError(ReviewError):
    """Paper has no converted markdown.

    Message includes the paperflow convert command.
    """


class PromptFileError(ReviewError):
    """``review.md`` has a structural problem the user must fix.

    Every subclass carries the step name (if applicable) and a
    description of the expected format.
    """


class MissingMetadataError(PromptFileError):
    """A step section is missing a required metadata field.

    Required fields: ``**Model:**``, ``**Execution:**``,
    ``**Reads:**``, ``**Writes:**``.
    """


class HookMismatchError(PromptFileError):
    """A step in ``review.md`` has no registered Python hook,
    a hook is registered for a step that does not exist,
    or a declared tool has no matching callable in the registry.
    """


# -- Runtime: pipeline bug or external failure --------------------------------


class StepError(ReviewError):
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
