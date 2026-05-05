#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async review pipeline for WG21 papers.

All LLM-facing text comes from ``review.md`` at runtime. This module
contains only structural orchestration - no prompt strings.
"""

from __future__ import annotations

import functools
import importlib.resources
import json
import logging
import re
from typing import Callable

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from paperstore.backend import PaperRow, StorageBackend
from paperstore.errors import MissingMetaError, MissingPaperMdError

from review.errors import ReviewError
from review.models import (
    ChallengeFindingsOutput,
    ClassifyOutput,
    GatherEvidenceOutput,
    InterpretResultsOutput,
    PipelineState,
    ReadPaperOutput,
    ResolveAssumptionsOutput,
    TestAndDraftOutput,
    VerifyCitationsOutput,
    WriteOutputOutput,
)
from review.parse import sections

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_SLOTS = {
    "fast": "anthropic:claude-haiku-4-5-20251001",
    "default": "anthropic:claude-sonnet-4-6",
}

_MODEL_LINE_RE = re.compile(r"^\-\s*\*\*Model:\*\*\s*(\S+)", re.MULTILINE)
_READS_LINE_RE = re.compile(r"^\-\s*\*\*Reads:\*\*\s*(.+)$", re.MULTILINE)
_TOOLS_LINE_RE = re.compile(r"^\-\s*\*\*Tools:\*\*\s*(.+)$", re.MULTILINE)

_STEP_KEYS: list[tuple[str, type[BaseModel]]] = [
    ("Step 0 - Classify", ClassifyOutput),
    ("Step 1 - Read Paper", ReadPaperOutput),
    ("Step 2 - Gather Evidence", GatherEvidenceOutput),
    ("Step 3 - Resolve Assumptions", ResolveAssumptionsOutput),
    ("Step 4 - Test and Draft", TestAndDraftOutput),
    ("Step 5 - Challenge Findings", ChallengeFindingsOutput),
    ("Step 6 - Interpret Results", InterpretResultsOutput),
    ("Step 7 - Verify Citations", VerifyCitationsOutput),
    ("Step 8 - Write Output", WriteOutputOutput),
]

_VERIFY_CITATIONS_KEY = "Step 7 - Verify Citations"
_WRITE_OUTPUT_KEY = "Step 8 - Write Output"


@functools.cache
def load_sections() -> dict[str, str]:
    """Load and parse review.md once per process."""
    resource = importlib.resources.files("review").joinpath("review.md")
    return sections(resource.read_text(encoding="utf-8"))


def _extract_model_slot(section_body: str) -> str:
    """Extract the Model: value from a step's metadata bullets."""
    m = _MODEL_LINE_RE.search(section_body)
    return m.group(1) if m else "default"


def _extract_reads(section_body: str) -> list[str]:
    """Extract the Reads: field names from a step's metadata bullets."""
    m = _READS_LINE_RE.search(section_body)
    if not m:
        return []
    return [f.strip() for f in m.group(1).split(",")]


def _extract_tools(section_body: str) -> list[str]:
    """Extract the Tools: values from a step's metadata bullets."""
    m = _TOOLS_LINE_RE.search(section_body)
    if not m:
        return []
    tools = [t.strip().lower() for t in m.group(1).split(",")]
    return [t for t in tools if t != "none"]


def _build_state_context(state: PipelineState, reads: list[str]) -> str:
    """Serialize only the Reads fields from the pipeline state."""
    full = state.model_dump(exclude_none=True)
    filtered = {k: v for k, v in full.items() if k in reads}
    if not filtered:
        return "{}"
    return json.dumps(filtered, indent=2, ensure_ascii=False, default=str)


def _load_paper(pid: str, backend: StorageBackend) -> tuple[PaperRow, str]:
    """Load paper metadata and markdown, raising ReviewError on failure."""
    try:
        meta = backend.get_meta(pid)
    except MissingMetaError as exc:
        raise ReviewError(
            f"Paper '{pid}' not found in paperstore. "
            f"Run 'paperflow mailing <year>' to index it, "
            f"then 'paperflow download {pid}' to stage its source."
        ) from exc

    try:
        paper_md = backend.get_paper_md(pid)
    except MissingPaperMdError as exc:
        raise ReviewError(
            f"Paper '{pid}' has no converted markdown. "
            f"Run 'paperflow convert {pid}' first."
        ) from exc

    return meta, paper_md


def _apply_output(state: PipelineState, output: BaseModel) -> None:
    """Copy fields from a step output onto the pipeline state."""
    for field_name in output.model_fields:
        if hasattr(state, field_name):
            setattr(state, field_name, getattr(output, field_name))


async def review_paper(
    pid: str,
    backend: StorageBackend,
    *,
    model_slots: dict[str, str] | None = None,
    on_step: Callable[[int, str], None] | None = None,
    on_step_complete: Callable[[int, str, BaseModel], None] | None = None,
    on_step_skip: Callable[[int, str], None] | None = None,
    stop_after: int | None = None,
) -> str:
    """Review a WG21 paper and return the rendered markdown report.

    Loads the paper from paperstore via ``pid``, runs the multi-step
    review pipeline, and returns the final markdown report string.

    ``on_step_complete`` is called with (index, key, output) after each
    step finishes. ``on_step_skip`` is called with (index, key) when a
    step is skipped. ``stop_after`` halts after step N and returns an
    empty string (for debugging).

    Raises :class:`ReviewError` if the paper is not found or has no
    converted markdown.
    """
    from web_tools import WebResearcher

    slots = {**_DEFAULT_MODEL_SLOTS, **(model_slots or {})}
    secs = load_sections()
    system_msg = secs["System Prompt"]

    _meta, paper_md = _load_paper(pid, backend)
    backend.clear_review(pid)
    state = PipelineState()
    report = ""

    async with WebResearcher() as researcher:
        for step_index, (key, response_model) in enumerate(_STEP_KEYS):
            step_body = secs.get(key)
            if step_body is None:
                raise ReviewError(
                    f"Step '{key}' not found in review.md. "
                    f"Available sections: {sorted(secs)}"
                )
            model_slot = _extract_model_slot(step_body)
            model = slots.get(model_slot)
            if model is None:
                raise ReviewError(
                    f"Step '{key}' requests model slot '{model_slot}' "
                    f"but available slots are: {sorted(slots)}"
                )
            reads = _extract_reads(step_body)
            tools = _extract_tools(step_body)

            if key == _VERIFY_CITATIONS_KEY:
                if state.surviving_findings is not None and len(state.surviving_findings) == 0:
                    if on_step_skip is not None:
                        on_step_skip(step_index, key)
                    continue

            if on_step is not None:
                on_step(step_index, key)

            logger.info(
                "Step %d (%s) model_slot='%s' -> %s, tools=%s",
                step_index, key, model_slot, model, tools,
            )

            state_context = _build_state_context(state, reads)
            include_paper = "paper" in reads

            user_content = ""
            if include_paper:
                user_content += f"## Paper Content\n\n{paper_md}\n\n"
            if state_context != "{}":
                user_content += f"## Pipeline State\n\n```json\n{state_context}\n```\n\n"
            user_content += f"## Step Instructions\n\n{step_body}"

            agent: Agent = Agent(
                model=model,
                output_type=response_model,
                system_prompt=system_msg,
                retries=3,
            )

            if "web_search" in tools:
                agent.tool_plain(researcher.web_search)
                agent.tool_plain(researcher.web_fetch)

            result = await agent.run(
                user_content,
                usage_limits=UsageLimits(request_limit=200),
            )
            output = result.output
            _apply_output(state, output)

            if on_step_complete is not None:
                on_step_complete(step_index, key, output)

            if stop_after is not None and step_index >= stop_after:
                return ""

            if key == _WRITE_OUTPUT_KEY:
                assert isinstance(output, WriteOutputOutput)
                report = output.report

    return report
