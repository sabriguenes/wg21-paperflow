#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

from __future__ import annotations

import pytest

from pipeline import MissingMetadataError, MissingSystemPromptError
from pipeline.prompt import StepHooks, build_pipeline, parse_step_meta


def _llm_body(extra: str = "") -> str:
    return (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        f"{extra}"
    )


def test_parse_step_system_prompt_append():
    meta = parse_step_meta(
        "1. A",
        _llm_body("\n### System Prompt\n\nStep role."),
    )

    assert meta.system_prompt == "Step role."
    assert meta.system_prompt_mode == "append"


def test_parse_step_system_prompt_replace():
    meta = parse_step_meta(
        "1. A",
        _llm_body("- **System prompt:** replace\n\n### System Prompt\n\nOnly step."),
    )

    assert meta.system_prompt == "Only step."
    assert meta.system_prompt_mode == "replace"


def test_replace_without_body_raises():
    with pytest.raises(MissingMetadataError, match="replace"):
        parse_step_meta("1. A", _llm_body("- **System prompt:** replace\n"))


def test_invalid_system_prompt_mode_raises():
    with pytest.raises(MissingMetadataError, match="invalid"):
        parse_step_meta("1. A", _llm_body("- **System prompt:** merge\n"))


def test_build_pipeline_requires_system_prompt_for_llm_step():
    sections = {"1. A": _llm_body()}

    with pytest.raises(MissingSystemPromptError):
        build_pipeline(sections, {"1. A": StepHooks()})


def test_build_pipeline_allows_missing_system_prompt_for_pure_steps():
    sections = {"0. Read": "- **Model:** none\n"}

    specs = build_pipeline(sections, {"0. Read": StepHooks()})

    assert len(specs) == 1
