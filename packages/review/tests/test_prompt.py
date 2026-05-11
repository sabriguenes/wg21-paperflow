#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for prompt metadata parsing and pipeline building."""

from __future__ import annotations

import pytest

from review.errors import HookMismatchError, MissingMetadataError
from review.prompt import StepHooks, build_pipeline, parse_step_meta


def test_parse_step_meta_basic():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        "- **Reads:** claims, evidence\n"
        "- **Writes:** support_map\n"
    )
    meta = parse_step_meta("Step 5 \u2014 Verify", body)
    assert meta.number == 5
    assert meta.model_slot == "default"
    assert meta.execution == "main"
    assert meta.reads == ["claims", "evidence"]
    assert meta.writes == ["support_map"]
    assert meta.tools == []
    assert meta.condition is None
    assert not meta.is_pure


def test_parse_step_meta_subagent():
    body = (
        "- **Model:** default\n"
        "- **Execution:** subagent\n"
        "- **Reads:** chunks\n"
        "- **Writes:** raw_claims\n"
    )
    meta = parse_step_meta("Step 1 \u2014 Extract Claims", body)
    assert meta.execution == "subagent"


def test_parse_step_meta_pure():
    body = (
        "- **Model:** none (pure Python)\n"
        "- **Execution:** main\n"
        "- **Reads:** paper_source\n"
        "- **Writes:** chunks, citations\n"
    )
    meta = parse_step_meta("Step 0 \u2014 Read", body)
    assert meta.is_pure
    assert meta.model_slot == "none"


def test_parse_step_meta_with_tools():
    body = (
        "- **Model:** default\n"
        "- **Execution:** subagent\n"
        "- **Tools:** web_search, web_fetch\n"
        "- **Reads:** claims\n"
        "- **Writes:** external_evidence\n"
    )
    meta = parse_step_meta("Step 7 \u2014 Web Search", body)
    assert meta.tools == ["web_search", "web_fetch"]


def test_parse_step_meta_with_condition():
    body = (
        "- **Model:** default\n"
        "- **Execution:** subagent\n"
        "- **Reads:** claims\n"
        "- **Writes:** external_evidence\n"
        "- **Condition:** has critical gaps\n"
    )
    meta = parse_step_meta("Step 7 \u2014 Web Search", body)
    assert meta.condition == "has critical gaps"


def test_parse_step_meta_missing_model():
    body = (
        "- **Execution:** main\n"
        "- **Reads:** claims\n"
        "- **Writes:** report\n"
    )
    with pytest.raises(MissingMetadataError, match="Model"):
        parse_step_meta("Step 9 \u2014 Report", body)


def test_parse_step_meta_missing_reads():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        "- **Writes:** report\n"
    )
    with pytest.raises(MissingMetadataError, match="Reads"):
        parse_step_meta("Step 9 \u2014 Report", body)


def test_parse_step_meta_bad_name():
    with pytest.raises(MissingMetadataError, match="does not match"):
        parse_step_meta("Not A Step", "- **Model:** default\n")


def test_build_pipeline_sorts_by_number():
    secs = {
        "Step 3 \u2014 B": "- **Model:** default\n- **Execution:** main\n- **Reads:** x\n- **Writes:** y\n",
        "Step 1 \u2014 A": "- **Model:** default\n- **Execution:** main\n- **Reads:** x\n- **Writes:** y\n",
    }
    hooks = {
        "Step 1 \u2014 A": StepHooks(),
        "Step 3 \u2014 B": StepHooks(),
    }
    specs = build_pipeline(secs, hooks)
    assert specs[0].meta.number == 1
    assert specs[1].meta.number == 3


def test_build_pipeline_orphan_hook():
    secs = {
        "Step 1 \u2014 A": "- **Model:** default\n- **Execution:** main\n- **Reads:** x\n- **Writes:** y\n",
    }
    hooks = {
        "Step 1 \u2014 A": StepHooks(),
        "Step 99 \u2014 Ghost": StepHooks(),
    }
    with pytest.raises(HookMismatchError, match="not in extractor.md"):
        build_pipeline(secs, hooks)


def test_build_pipeline_missing_hook():
    secs = {
        "Step 1 \u2014 A": "- **Model:** default\n- **Execution:** main\n- **Reads:** x\n- **Writes:** y\n",
    }
    hooks: dict[str, StepHooks] = {}
    with pytest.raises(HookMismatchError, match="no registered hooks"):
        build_pipeline(secs, hooks)


def test_build_pipeline_skips_non_step_sections():
    secs = {
        "System Prompt": "You are a bot.",
        "Global Directives": "Be good.",
        "Step 0 \u2014 Read": "- **Model:** fast\n- **Execution:** main\n- **Reads:** x\n- **Writes:** y\n",
    }
    hooks = {
        "Step 0 \u2014 Read": StepHooks(),
    }
    specs = build_pipeline(secs, hooks)
    assert len(specs) == 1
