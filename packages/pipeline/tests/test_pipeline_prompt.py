#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

from __future__ import annotations

import pytest

from pipeline import MissingMetadataError, MissingSystemPromptError
from pipeline.errors import ServiceConfigError
from pipeline.prompt import (
    PipelinePrompt,
    StepHooks,
    _parse_prompt,
    build_pipeline,
    parse_step_prompt,
)


def _llm_body(extra: str = "") -> str:
    return (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        f"{extra}"
    )


# ---------------------------------------------------------------------------
# parse_step_prompt
# ---------------------------------------------------------------------------


def test_parse_step_system_prompt_append():
    s = parse_step_prompt(
        "1. A",
        _llm_body("\n### System Prompt\n\nStep role."),
    )

    assert s.system_prompt == "Step role."
    assert s.system_prompt_mode == "append"


def test_parse_step_system_prompt_replace():
    s = parse_step_prompt(
        "1. A",
        _llm_body("- **System prompt:** replace\n\n### System Prompt\n\nOnly step."),
    )

    assert s.system_prompt == "Only step."
    assert s.system_prompt_mode == "replace"


def test_replace_without_body_raises():
    with pytest.raises(MissingMetadataError, match="replace"):
        parse_step_prompt("1. A", _llm_body("- **System prompt:** replace\n"))


def test_invalid_system_prompt_mode_raises():
    with pytest.raises(MissingMetadataError, match="invalid"):
        parse_step_prompt("1. A", _llm_body("- **System prompt:** merge\n"))


def test_model_defaults_to_default_when_absent():
    """A step that omits `**Model:**` is treated as model=default, not
    none. This makes 'default' the implicit binding the user reasons
    about; pure-Python steps must say so explicitly via
    `**Model:** none`."""
    s = parse_step_prompt("1. A", "- **Execution:** main\n")
    assert s.model == "default"
    assert not s.is_custom


def test_model_none_is_pure_python():
    s = parse_step_prompt("1. A", "- **Model:** none\n")
    assert s.model == "none"
    assert s.is_custom


def test_unknown_bullet_keys_land_in_extra():
    """Bullets whose keys are not consumed by a typed field flow into
    StepPrompt.extra so pipelines can declare per-step variables."""
    s = parse_step_prompt(
        "1. A",
        _llm_body(
            "- **chunk-overlap:** 100\n"
            "- **lens:** Performance\n"
        ),
    )
    assert s.extra == {"chunk-overlap": "100", "lens": "Performance"}
    assert s.model == "default"


def test_bullets_inside_fence_are_not_parsed_as_meta():
    """Metadata parsing is fence-aware: triple-backtick blocks do not
    contribute bullets to fields or extra, and a `### System Prompt`
    header inside a fence does not capture the next lines."""
    body = (
        "- **Model:** default\n"
        "\n"
        "```\n"
        "- **Model:** WRONG\n"
        "- **chunk-overlap:** WRONG\n"
        "### System Prompt\n"
        "Don't capture me.\n"
        "```\n"
    )
    s = parse_step_prompt("1. A", body)
    assert s.model == "default"
    assert s.extra == {}
    assert s.system_prompt == ""


# ---------------------------------------------------------------------------
# PipelinePrompt parsing
# ---------------------------------------------------------------------------


def _pipeline_text(services: str, steps: str) -> str:
    return (
        "# Test pipeline\n\n"
        "## Services\n\n"
        f"{services}\n"
        "## System Prompt\n\n"
        "Be helpful.\n\n"
        f"{steps}"
    )


def test_pipeline_prompt_parses_services_and_steps():
    text = _pipeline_text(
        "- **default:** anthropic-opus\n- **tool:** anthropic-opus\n",
        "## 0. Receive\n\n- **Model:** none\n\n"
        "## 1. Extract\n\n- **Model:** default\n",
    )
    p = _parse_prompt("test", "test.md", text)
    assert dict(p.services) == {"default": "anthropic-opus", "tool": "anthropic-opus"}
    assert p.system_prompt == "Be helpful."
    assert [s.name for s in p.steps] == ["0. Receive", "1. Extract"]
    assert p.steps[1].model == "default"


def test_pipeline_prompt_post_horizontal_rule_step_body_is_absent():
    """`markdown.sections` already truncates each step body at the
    first non-fenced `---`. Anything below that line never reaches the
    section map, and therefore never reaches `parse_step_prompt`.
    """
    text = _pipeline_text(
        "- **default:** s1\n",
        "## 1. A\n\n- **Model:** default\n\nInstructions.\n\n---\n\nMETA_BELOW\n",
    )
    p = _parse_prompt("test", "test.md", text)
    assert "META_BELOW" not in p.sections["1. A"]


# ---------------------------------------------------------------------------
# build_pipeline
# ---------------------------------------------------------------------------


def test_build_pipeline_requires_system_prompt_for_llm_step():
    text = (
        "# Test\n\n"
        "## Services\n\n- **default:** s1\n\n"
        "## 1. A\n\n- **Model:** default\n"
    )
    p = _parse_prompt("test", "test.md", text)
    with pytest.raises(MissingSystemPromptError):
        build_pipeline(p, {"1. A": StepHooks()})


def test_build_pipeline_allows_missing_system_prompt_for_pure_steps():
    text = (
        "# Test\n\n"
        "## 0. Read\n\n- **Model:** none\n"
    )
    p = _parse_prompt("test", "test.md", text)
    specs = build_pipeline(p, {"0. Read": StepHooks()})
    assert len(specs) == 1


# ---------------------------------------------------------------------------
# validate_capabilities: undeclared logical name hard-errors
# ---------------------------------------------------------------------------


def test_validate_capabilities_rejects_undeclared_logical_model():
    """A step that references a logical name not in `## Services`
    raises ServiceConfigError at validate time."""
    from pipeline.validate import validate_capabilities

    text = (
        "# Test\n\n"
        "## Services\n\n- **default:** s1\n\n"
        "## System Prompt\n\nBe helpful.\n\n"
        "## 1. A\n\n- **Model:** typo\n"
    )
    p = _parse_prompt("test", "test.md", text)
    specs = build_pipeline(p, {"1. A": StepHooks()})

    with pytest.raises(ServiceConfigError) as exc_info:
        validate_capabilities(specs, p)
    msg = str(exc_info.value)
    assert "typo" in msg
    assert "default" in msg  # available names listed
    assert "1. A" in msg
