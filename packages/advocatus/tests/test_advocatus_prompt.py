#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for advocatus.md metadata parsing and pipeline build."""

from __future__ import annotations

import pytest

from pipeline import HookMismatchError, MissingMetadataError
from pipeline import sections
from advocatus.pipeline import _HOOKS, load_sections
from pipeline import StepHooks, build_pipeline, parse_step_meta


def test_load_sections_returns_all_step_headers():
    secs = load_sections("advocatus", "advocatus.md")
    step_keys = sorted(k for k in secs if k.startswith("Step "))
    assert len(step_keys) == 11
    assert step_keys[0] == "Step 0 - Load"
    assert step_keys[-1].startswith("Step 9")  # alphabetical sort puts 9 last
    # All 11 step names match the registered hooks exactly.
    assert set(step_keys) == set(_HOOKS)


def test_load_sections_has_system_prompt():
    secs = load_sections("advocatus", "advocatus.md")
    assert "System Prompt" in secs
    assert secs["System Prompt"].strip()


def test_build_pipeline_returns_11_specs_in_order():
    secs = load_sections("advocatus", "advocatus.md")
    specs = build_pipeline(secs, _HOOKS)
    assert len(specs) == 11
    assert [s.meta.number for s in specs] == list(range(11))


def test_parse_step_meta_extracts_fields():
    body = """
- **Model:** default
- **Execution:** parallel
- **Tools:** web_search

Some narrative.
"""
    meta = parse_step_meta("Step 5 - Examine Articuli", body)
    assert meta.number == 5
    assert meta.model_slot == "default"
    assert meta.execution == "parallel"
    assert meta.tools == ["web_search"]


def test_parse_step_meta_missing_field_raises():
    body = """
- **Execution:** main
"""
    with pytest.raises(MissingMetadataError):
        parse_step_meta("Step 1 - Foo", body)


def test_parse_step_meta_bad_header_raises():
    body = "- **Model:** default\n- **Execution:** main\n"
    with pytest.raises(MissingMetadataError):
        parse_step_meta("Not A Step", body)


def test_build_pipeline_orphan_hook_raises():
    body = """## Step 0 - Foo

- **Model:** none
- **Execution:** main
"""
    secs = sections(body)
    hooks = {
        "Step 0 - Foo": StepHooks(),
        "Step 1 - Orphan": StepHooks(),
    }
    with pytest.raises(HookMismatchError):
        build_pipeline(secs, hooks)


def test_build_pipeline_missing_hook_raises():
    body = """## Step 0 - Foo

- **Model:** none
- **Execution:** main
"""
    secs = sections(body)
    with pytest.raises(HookMismatchError):
        build_pipeline(secs, {})


def test_steps_that_spawn_subagents_declare_a_real_model_slot():
    """Steps whose pure hook calls run_task internally must declare a
    real model slot (not 'none'), otherwise the sub-agent dispatch
    fails at runtime with 'Unknown model: none'."""
    secs = load_sections("advocatus", "advocatus.md")
    specs = build_pipeline(secs, _HOOKS)
    by_name = {s.meta.name: s for s in specs}
    subagent_steps = (
        "Step 2 - Survey Public Record",
        "Step 3 - Map Stakeholders",
        "Step 5 - Examine Articuli",
        "Step 7 - Defensor Cross-Examination",
    )
    for name in subagent_steps:
        slot = by_name[name].meta.model_slot
        assert slot != "none", (
            f"{name} spawns sub-agents via run_task and must declare a "
            f"real model slot in advocatus.md, got {slot!r}."
        )
