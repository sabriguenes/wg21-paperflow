#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for prompt metadata parsing and pipeline building."""

from __future__ import annotations

import pytest

from pipeline import HookMismatchError, MissingMetadataError, MissingSystemPromptError
from pipeline import StepHooks, build_pipeline, parse_step_meta


def test_parse_step_meta_basic():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
    )
    meta = parse_step_meta("4. Verify", body)
    assert meta.number == 4
    assert meta.model_slot == "default"
    assert meta.execution == "main"
    assert meta.tools == []
    assert meta.condition is None
    assert not meta.is_custom


def test_parse_step_meta_subagent():
    body = (
        "- **Model:** default\n"
        "- **Execution:** subagent\n"
    )
    meta = parse_step_meta("1. Extract", body)
    assert meta.execution == "subagent"


def test_parse_step_meta_pure():
    body = (
        "- **Model:** none (pure Python)\n"
        "- **Execution:** main\n"
    )
    meta = parse_step_meta("0. Read", body)
    assert meta.is_custom
    assert meta.model_slot == "none"


def test_parse_step_meta_with_tools():
    body = (
        "- **Model:** default\n"
        "- **Execution:** subagent\n"
        "- **Tools:** web_search, web_fetch\n"
    )
    meta = parse_step_meta("6. Web Search", body)
    assert meta.tools == ["web_search", "web_fetch"]


def test_parse_step_meta_with_condition():
    body = (
        "- **Model:** default\n"
        "- **Execution:** subagent\n"
        "- **Condition:** has critical gaps\n"
    )
    meta = parse_step_meta("6. Web Search", body)
    assert meta.condition == "has critical gaps"


def test_parse_step_meta_missing_model_defaults_to_none():
    body = (
        "- **Execution:** main\n"
    )
    meta = parse_step_meta("9. Report", body)
    assert meta.model_slot == "none"
    assert meta.is_custom


def test_parse_step_meta_bad_name():
    with pytest.raises(MissingMetadataError, match="does not match"):
        parse_step_meta("Not A Step", "- **Model:** default\n")


def test_build_pipeline_sorts_by_number():
    secs = {
        "System Prompt": "You are a bot.",
        "3. B": "- **Model:** default\n- **Execution:** main\n",
        "1. A": "- **Model:** default\n- **Execution:** main\n",
    }
    hooks = {
        "1. A": StepHooks(),
        "3. B": StepHooks(),
    }
    specs = build_pipeline(secs, hooks)
    assert specs[0].meta.number == 1
    assert specs[1].meta.number == 3


def test_build_pipeline_orphan_hook():
    secs = {
        "System Prompt": "You are a bot.",
        "1. A": "- **Model:** default\n- **Execution:** main\n",
    }
    hooks = {
        "1. A": StepHooks(),
        "99. Ghost": StepHooks(),
    }
    with pytest.raises(HookMismatchError):
        build_pipeline(secs, hooks)


def test_build_pipeline_missing_hook():
    secs = {
        "System Prompt": "You are a bot.",
        "1. A": "- **Model:** default\n- **Execution:** main\n",
    }
    hooks: dict[str, StepHooks] = {}
    with pytest.raises(HookMismatchError, match="no registered hooks"):
        build_pipeline(secs, hooks)


def test_build_pipeline_skips_non_step_sections():
    secs = {
        "System Prompt": "You are a bot.",
        "Global Directives": "Be good.",
        "0. Read": "- **Model:** fast\n- **Execution:** main\n",
    }
    hooks = {
        "0. Read": StepHooks(),
    }
    specs = build_pipeline(secs, hooks)
    assert len(specs) == 1


def test_parse_step_meta_extract_factual():
    body = (
        "- **Model:** default\n"
        "- **Execution:** subagent\n"
    )
    meta = parse_step_meta("5. Extract Factual", body)
    assert meta.number == 5
    assert meta.execution == "subagent"


def test_parse_step_meta_dedup_factual():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
    )
    meta = parse_step_meta("6. Dedup Factual Claims", body)
    assert meta.number == 6


def test_parse_step_meta_verify_citations():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        "- **Tools:** web_fetch\n"
        "- **Condition:** has citations\n"
    )
    meta = parse_step_meta("10. Verify Citations", body)
    assert meta.number == 10
    assert meta.tools == ["web_fetch"]
    assert meta.condition == "has citations"


def test_parse_step_meta_caput_causae():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        "- **Condition:** has anchored claims\n"
    )
    meta = parse_step_meta("13. Caput Causae", body)
    assert meta.number == 13
    assert meta.condition == "has anchored claims"


def _make_test_hooks():
    from pipeline.agents import AgentBackend
    from pipeline.model_backends import Llama3Backend
    stub = AgentBackend(Llama3Backend(base_url="", api_key="", model=""))
    from dissect.pipeline import _build_hooks
    return _build_hooks(stub, stub, stub)


def test_pipeline_has_17_steps():
    """Verify the full pipeline has steps 0-16."""
    from dissect.pipeline import load_sections
    hooks = _make_test_hooks()
    secs = dict(load_sections("dissect", "dissect.md"))
    specs = build_pipeline(secs, hooks)
    assert len(specs) == 17
    assert specs[0].meta.number == 0
    assert specs[-1].meta.number == 16


def test_dissect_prompt_has_required_system_prompts():
    from dissect.pipeline import load_sections
    hooks = _make_test_hooks()
    secs = dict(load_sections("dissect", "dissect.md"))
    specs = build_pipeline(secs, hooks)
    by_number = {spec.meta.number: spec for spec in specs}

    assert secs["System Prompt"].strip()
    for number in [8, 9, 10, 11, 12]:
        assert by_number[number].meta.system_prompt


def test_parse_step_meta_system_prompt_append():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n\n"
        "### System Prompt\n\n"
        "Step role."
    )
    meta = parse_step_meta("1. A", body)
    assert meta.system_prompt == "Step role."
    assert meta.system_prompt_mode == "append"


def test_parse_step_meta_system_prompt_replace():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        "- **System prompt:** replace\n\n"
        "### System Prompt\n\n"
        "Replacement."
    )
    meta = parse_step_meta("1. A", body)
    assert meta.system_prompt == "Replacement."
    assert meta.system_prompt_mode == "replace"


def test_parse_step_meta_invalid_system_prompt_mode():
    body = (
        "- **Model:** default\n"
        "- **Execution:** main\n"
        "- **System prompt:** merge\n"
    )
    with pytest.raises(MissingMetadataError, match="System prompt"):
        parse_step_meta("1. A", body)


def test_build_pipeline_requires_system_prompt_for_llm_steps():
    secs = {
        "1. A": "- **Model:** default\n- **Execution:** main\n",
    }
    with pytest.raises(MissingSystemPromptError):
        build_pipeline(secs, {"1. A": StepHooks()})
