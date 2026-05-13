#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for advocatus pipeline guards, dispatch, and Step 0 load."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from paperstore import SqliteBackend, SourceLoc

from advocatus import advocatus_paper
from advocatus.errors import (
    PaperNotConvertedError,
    PaperNotDissectedError,
    PaperNotFoundError,
)
from advocatus.models import PipelineState
from advocatus.pipeline import _guard_not_sine_causa


def test_guard_not_sine_causa_passes_when_no_seal():
    state = PipelineState()
    assert _guard_not_sine_causa(state) is True


def test_guard_not_sine_causa_passes_under_other_seals():
    state = PipelineState(seal="nihil_obstat")
    assert _guard_not_sine_causa(state) is True
    state = PipelineState(seal="cum_objectionibus")
    assert _guard_not_sine_causa(state) is True


def test_guard_not_sine_causa_skips_under_sine_causa():
    state = PipelineState(seal="sine_causa")
    assert _guard_not_sine_causa(state) is False


def test_advocatus_paper_unknown_pid_raises(tmp_path: Path):
    backend = SqliteBackend(tmp_path)
    with pytest.raises(PaperNotFoundError):
        asyncio.run(advocatus_paper("PXXXXR0", backend))


def test_advocatus_paper_not_converted_raises(tmp_path: Path):
    """Paper exists in the index but has no converted markdown."""
    backend = SqliteBackend(tmp_path)
    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])
    with pytest.raises(PaperNotConvertedError):
        asyncio.run(advocatus_paper("P1234R0", backend))


def test_advocatus_paper_not_dissected_raises(tmp_path: Path):
    """Paper has converted markdown but has not been dissected."""
    backend = SqliteBackend(tmp_path)
    backend.upsert_year("2026", [{"paper_id": "P1234R0"}])
    backend.write_paper_md("P1234R0", "# A paper\n\nBody.")
    # No dissect_path set; advocatus must fail-fast, not emit Sine causa.
    with pytest.raises(PaperNotDissectedError):
        asyncio.run(advocatus_paper("P1234R0", backend))


def test_step_0_sine_causa_state_shape():
    """Sine causa state has a seal but no articuli; downstream renders."""
    state = PipelineState(
        paper_id="P0000R0",
        paper_title="Schedule",
        seal="sine_causa",
        confidence=1.0,
        one_sentence_assessment="No claims.",
    )
    # Guards skip steps 1-9; only Step 10 runs.
    assert _guard_not_sine_causa(state) is False


def test_load_sections_caches_result():
    from advocatus.pipeline import load_sections
    s1 = load_sections()
    s2 = load_sections()
    assert s1 is s2  # @functools.cache


def test_step_names_match_advocatus_md_to_hooks():
    from advocatus.pipeline import _HOOKS, load_sections
    secs = load_sections()
    step_keys = {k for k in secs if k.startswith("Step ")}
    assert step_keys == set(_HOOKS), (
        f"Mismatch between advocatus.md and _HOOKS:\n"
        f"  in advocatus.md, not in _HOOKS: {step_keys - set(_HOOKS)}\n"
        f"  in _HOOKS, not in advocatus.md: {set(_HOOKS) - step_keys}"
    )


def test_loc_from_row_round_trip_with_paperstore():
    """Smoke test: paperstore's loc_from_row builds the same SourceLoc
    advocatus models expect."""
    from paperstore import ClaimRow, loc_from_row

    row = ClaimRow(
        paper_id="P1000R0",
        loc_line=42,
        loc_start=0,
        loc_end=80,
        text="x",
        section="s",
        question="q",
    )
    loc = loc_from_row(row)
    assert isinstance(loc, SourceLoc)
    assert (loc.line, loc.start_char, loc.end_char) == (42, 0, 80)


def test_pure_load_reads_all_paperrow_fields_we_care_about(tmp_path: Path):
    """Regression: catch any drift between PaperRow's field names and
    what _pure_load reads. The previous bug was ``meta.audience`` (the
    field is actually ``target_group``).
    """
    from advocatus.models import PipelineState
    from advocatus.pipeline import StepContext, _pure_load

    backend = SqliteBackend(tmp_path)
    backend.upsert_year("2026", [{
        "paper_id": "P1234R0",
        "title": "Test Paper",
        "authors": ["Alice", "Bob"],
        "target_group": "LEWG",
    }])
    backend.write_paper_md("P1234R0", "# Test\n\nBody.")
    # Make dissect_path non-empty so the fail-fast check passes.
    backend.write_dissect_md("P1234R0", "# Dissect\n\nContent.")

    state = PipelineState()
    ctx = StepContext(sections={}, model_slots={}, backend=backend, pid="P1234R0")

    asyncio.run(_pure_load(state, ctx))

    assert state.paper_id == "P1234R0"
    assert state.paper_title == "Test Paper"
    assert state.paper_audience == "LEWG"
    assert state.paper_authors == ["Alice", "Bob"]
    assert state.paper_source == "# Test\n\nBody."
    # No dissect rows yet → empty articuli seed → seal=sine_causa
    assert state.seal == "sine_causa"


def test_step_context_initializes_debug_log_when_debug_true():
    from advocatus.pipeline import StepContext
    ctx = StepContext(sections={}, model_slots={}, debug=True)
    assert ctx.debug_log == []


def test_step_context_debug_log_stays_none_when_debug_false():
    from advocatus.pipeline import StepContext
    ctx = StepContext(sections={}, model_slots={}, debug=False)
    assert ctx.debug_log is None


def test_dispatch_stop_after_halts_after_step_n():
    """_dispatch must stop after pipeline step N (inclusive)."""
    from advocatus.pipeline import StepContext, _dispatch
    from advocatus.prompt import StepHooks, StepMeta, StepSpec

    visited: list[int] = []

    def _spec(n: int) -> StepSpec:
        meta = StepMeta(
            name=f"Step {n} - X", number=n, model_slot="none",
            execution="main", reads=[], writes=[],
        )

        async def _pure(state, ctx):
            visited.append(n)

        return StepSpec(meta=meta, hooks=StepHooks(pure=_pure))

    pipeline = [_spec(i) for i in range(5)]
    state = PipelineState()
    ctx = StepContext(sections={}, model_slots={})

    asyncio.run(_dispatch(pipeline, state, ctx, stop_after=2))
    assert visited == [0, 1, 2]


def test_run_task_appends_to_debug_log_when_provided():
    """run_task must call render_debug_md and append to debug_log."""
    import advocatus.pipeline as pmod

    log: list[str] = []
    captured: list = []

    class _StubResult:
        output = "stub-output"

        def all_messages(self):
            return []

    class _StubAgent:
        def __init__(self, *a, **kw):
            pass

        def tool_plain(self, fn):
            pass

        async def run(self, *a, **kw):
            return _StubResult()

    monkey_patch_targets = {"Agent": _StubAgent}
    orig_agent = pmod.Agent
    pmod.Agent = _StubAgent  # type: ignore[assignment]
    try:
        result = asyncio.run(pmod.run_task(
            system_prompt="sp",
            user_message="um",
            output_type=str,
            label="test-label",
            debug_log=log,
        ))
    finally:
        pmod.Agent = orig_agent  # type: ignore[assignment]
    captured.append(monkey_patch_targets)  # silence unused-var

    assert result == "stub-output"
    assert len(log) == 1
    assert log[0].startswith("# test-label")
