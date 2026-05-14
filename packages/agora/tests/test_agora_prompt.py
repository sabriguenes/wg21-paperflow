#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for agora.md metadata parsing and pipeline build."""

from __future__ import annotations

import pytest

from agora.errors import HookMismatchError, MissingMetadataError
from agora.parse import sections
from agora.pipeline import _HOOKS, load_sections
from agora.prompt import StepHooks, build_pipeline, parse_step_meta


def test_load_sections_returns_all_step_headers():
    secs = load_sections()
    step_keys = sorted(k for k in secs if k.startswith("Step "))
    assert len(step_keys) == 8
    assert step_keys[0] == "Step 0 - Load"
    assert set(step_keys) == set(_HOOKS)


def test_load_sections_has_system_prompt():
    secs = load_sections()
    assert "System Prompt" in secs
    assert secs["System Prompt"].strip()


def test_build_pipeline_returns_8_specs_in_numeric_order():
    secs = load_sections()
    specs = build_pipeline(secs, _HOOKS)
    assert len(specs) == 8
    assert [s.meta.number for s in specs] == list(range(8))


def test_step_2_research_declares_real_model_slot_for_subagents():
    """Step 2 spawns sub-agents via run_task. If its model slot is
    'none' the dispatch would crash with 'Unknown model: none'."""
    secs = load_sections()
    specs = build_pipeline(secs, _HOOKS)
    by_name = {s.meta.name: s for s in specs}
    assert by_name["Step 2 - Research"].meta.model_slot != "none"


def test_step_6_encounters_declares_condition():
    secs = load_sections()
    specs = build_pipeline(secs, _HOOKS)
    by_name = {s.meta.name: s for s in specs}
    enc = by_name["Step 6 - Encounters"]
    assert enc.meta.condition is not None
    assert "encounter_count" in enc.meta.condition


def test_parse_step_meta_extracts_fields():
    body = """
- **Model:** default
- **Execution:** main
- **Reads:** paper_source, dissect_claims
- **Writes:** technical_anchors

Some narrative.
"""
    meta = parse_step_meta("Step 1 - Smell Test", body)
    assert meta.number == 1
    assert meta.model_slot == "default"
    assert meta.execution == "main"
    assert meta.reads == ["paper_source", "dissect_claims"]
    assert meta.writes == ["technical_anchors"]


def test_parse_step_meta_missing_field_raises():
    body = """
- **Model:** default
- **Execution:** main

(no Reads/Writes)
"""
    with pytest.raises(MissingMetadataError):
        parse_step_meta("Step 1 - Smell Test", body)


def test_parse_step_meta_bad_header_raises():
    body = "- **Model:** default\n- **Execution:** main\n- **Reads:** x\n- **Writes:** y\n"
    with pytest.raises(MissingMetadataError):
        parse_step_meta("Not A Step", body)


def test_build_pipeline_orphan_hook_raises():
    body = """## Step 0 - Foo

- **Model:** none
- **Execution:** main
- **Reads:** x
- **Writes:** y
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
- **Reads:** x
- **Writes:** y
"""
    secs = sections(body)
    with pytest.raises(HookMismatchError):
        build_pipeline(secs, {})
