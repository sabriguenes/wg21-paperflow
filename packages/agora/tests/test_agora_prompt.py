#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for agora.md metadata parsing and pipeline build."""

from __future__ import annotations

import pytest

from pipeline import (
    HookMismatchError,
    MissingMetadataError,
    PipelinePrompt,
    StepHooks,
    build_pipeline,
    parse_step_prompt,
)
from pipeline.prompt import _parse_prompt
from agora.pipeline import _build_hooks


def _agora_prompt() -> PipelinePrompt:
    return PipelinePrompt.load("agora", "agora.md")


def test_load_returns_all_step_headers():
    prompt = _agora_prompt()
    step_names = [s.name for s in prompt.steps]
    assert len(step_names) == 8
    assert step_names[0] == "Step 0 - Load"
    assert set(step_names) == set(_build_hooks())


def test_load_has_system_prompt():
    prompt = _agora_prompt()
    assert prompt.system_prompt.strip()


def test_load_has_services():
    prompt = _agora_prompt()
    assert "default" in prompt.services
    assert "tool" in prompt.services


def test_build_pipeline_returns_8_specs_in_numeric_order():
    prompt = _agora_prompt()
    specs = build_pipeline(prompt, _build_hooks())
    assert len(specs) == 8
    assert [s.step.number for s in specs] == list(range(8))


def test_step_2_research_uses_a_real_model():
    """Step 2 spawns sub-agents via run_task. If its model is 'none'
    the dispatch would crash with 'Unknown model: none'."""
    prompt = _agora_prompt()
    specs = build_pipeline(prompt, _build_hooks())
    by_name = {s.step.name: s for s in specs}
    assert by_name["Step 2 - Research"].step.model != "none"


def test_step_6_encounters_declares_condition():
    prompt = _agora_prompt()
    specs = build_pipeline(prompt, _build_hooks())
    by_name = {s.step.name: s for s in specs}
    enc = by_name["Step 6 - Encounters"]
    assert enc.step.condition is not None
    assert "encounter_count" in enc.step.condition


def test_parse_step_prompt_extracts_fields():
    body = """
- **Model:** default
- **Execution:** main

Some narrative.
"""
    s = parse_step_prompt("Step 1 - Smell Test", body)
    assert s.number == 1
    assert s.model == "default"
    assert s.execution == "main"


def test_parse_step_prompt_defaults_missing_model_to_default():
    """No `**Model:**` line means the implicit 'default' logical name,
    not 'none'. Pure-Python steps must say `**Model:** none`
    explicitly."""
    body = """
- **Execution:** main
"""
    s = parse_step_prompt("Step 1 - Smell Test", body)
    assert s.model == "default"
    assert s.execution == "main"


def test_parse_step_prompt_bad_header_raises():
    body = "- **Model:** default\n- **Execution:** main\n"
    with pytest.raises(MissingMetadataError):
        parse_step_prompt("Not A Step", body)


def _prompt_with(step_md: str) -> PipelinePrompt:
    return _parse_prompt(
        "test",
        "test.md",
        "# T\n\n## Services\n\n- **default:** s1\n\n"
        "## System Prompt\n\nBe helpful.\n\n"
        + step_md,
    )


def test_build_pipeline_orphan_hook_raises():
    prompt = _prompt_with(
        "## Step 0 - Foo\n\n- **Model:** none\n- **Execution:** main\n"
    )
    hooks = {
        "Step 0 - Foo": StepHooks(),
        "Step 1 - Orphan": StepHooks(),
    }
    with pytest.raises(HookMismatchError):
        build_pipeline(prompt, hooks)


def test_build_pipeline_missing_hook_raises():
    prompt = _prompt_with(
        "## Step 0 - Foo\n\n- **Model:** none\n- **Execution:** main\n"
    )
    with pytest.raises(HookMismatchError):
        build_pipeline(prompt, {})
