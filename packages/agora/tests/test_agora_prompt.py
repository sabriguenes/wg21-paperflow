#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for agora.md metadata parsing and pipeline build."""

from __future__ import annotations

import pytest

from pipeline import HookMismatchError, MissingMetadataError
from pipeline import sections
from agora.pipeline import _build_hooks, load_sections
from pipeline import StepHooks, build_pipeline, parse_step_meta
from pipeline.agents import AgentBackend
from pipeline.model_backends import Llama3Backend


def _make_test_hooks():
    stub = AgentBackend(Llama3Backend(base_url="", api_key="", model=""))
    return _build_hooks(stub, stub)


def test_load_sections_returns_all_step_headers():
    secs = dict(load_sections("agora", "agora.md"))
    step_keys = sorted(k for k in secs if k.startswith("Step "))
    assert len(step_keys) == 8
    assert step_keys[0] == "Step 0 - Load"
    assert set(step_keys) == set(_make_test_hooks())


def test_load_sections_has_system_prompt():
    secs = dict(load_sections("agora", "agora.md"))
    assert "System Prompt" in secs
    assert secs["System Prompt"].strip()


def test_build_pipeline_returns_8_specs_in_numeric_order():
    secs = dict(load_sections("agora", "agora.md"))
    specs = build_pipeline(secs, _make_test_hooks())
    assert len(specs) == 8
    assert [s.meta.number for s in specs] == list(range(8))


def test_step_2_research_declares_real_model_slot_for_subagents():
    """Step 2 spawns sub-agents via run_task. If its model slot is
    'none' the dispatch would crash with 'Unknown model: none'."""
    secs = dict(load_sections("agora", "agora.md"))
    specs = build_pipeline(secs, _make_test_hooks())
    by_name = {s.meta.name: s for s in specs}
    assert by_name["Step 2 - Research"].meta.model_slot != "none"


def test_step_6_encounters_declares_condition():
    secs = dict(load_sections("agora", "agora.md"))
    specs = build_pipeline(secs, _make_test_hooks())
    by_name = {s.meta.name: s for s in specs}
    enc = by_name["Step 6 - Encounters"]
    assert enc.meta.condition is not None
    assert "encounter_count" in enc.meta.condition


def test_parse_step_meta_extracts_fields():
    body = """
- **Model:** default
- **Execution:** main

Some narrative.
"""
    meta = parse_step_meta("Step 1 - Smell Test", body)
    assert meta.number == 1
    assert meta.model_slot == "default"
    assert meta.execution == "main"


def test_parse_step_meta_defaults_missing_model_to_none():
    body = """
- **Execution:** main
"""
    meta = parse_step_meta("Step 1 - Smell Test", body)
    assert meta.model_slot == "none"
    assert meta.execution == "main"


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
