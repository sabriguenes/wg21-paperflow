#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for `_custom_classify` (Step 6, per-chunk gap classification).

Uses a stubbed agent and the real `StepContext` so the actual
`gather_concurrent` ordering path runs. Verifies that gaps are merged
in chunk order regardless of completion order, that each gap's
`chunk_index` is pinned to the call's authoritative chunk (not the
model's value), and that a paper with no unsupported claims issues no
LLM calls.
"""

from __future__ import annotations

import asyncio

from pipeline import StepContext

from assay.models import (
    BatchClassifyOutput,
    ChunkDecideOutput,
    ChunkEntry,
    ChunkExtractOutput,
    ClaimDecision,
    GapOutput,
    ItemOutput,
    PipelineState,
)
from assay.pipeline import _custom_classify


class _StubAgent:
    """Returns one gap per call, tagged with the wrong chunk_index.

    The gap's ``chunk_index`` is deliberately set to a bogus sentinel so
    a test can prove `_custom_classify` overwrites it with the call's
    authoritative chunk. Lower-numbered chunks sleep longer, forcing
    out-of-order completion so the ordering guarantee is actually exercised.
    """

    _BOGUS_CHUNK = 99

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.max_tokens = 8192

    async def run(
        self,
        *,
        system_prompt: str,
        user_message: str,
        output_type,
        max_tokens: int,
        thinking_budget,
        label: str,
        debug_log=None,
        tools=None,
    ):
        self.calls.append(label)
        ci = int(label.rsplit("-", 1)[1])
        # Lower ci -> longer sleep -> completes last.
        await asyncio.sleep((10 - ci) / 1000)
        return BatchClassifyOutput(gaps=[
            GapOutput(
                chunk_index=self._BOGUS_CHUNK,
                item_quote=f"claim in chunk {ci}",
                line=ci,
                gap="why?",
                why_important="matters",
                primary_lens="Design",
            ),
        ])


class _Step:
    model = "fast"
    max_output_tokens = 16384
    thinking_budget = 4096
    concurrency = 8


class _Spec:
    step = _Step()


def _ctx(agent: _StubAgent) -> StepContext:
    return StepContext(agents={"fast": agent}, default_concurrency=8)


def _state() -> PipelineState:
    """One unsupported claim each in chunk 2 and chunk 0 (insertion order)."""
    state = PipelineState()
    state.paper_id = "P9999R0"
    state.paper_md = "\n".join(f"line {i}" for i in range(40))
    state.chunk_map = [
        ChunkEntry(index=0, heading="Intro", start_line=1, end_line=10, char_count=50),
        ChunkEntry(index=2, heading="Body", start_line=20, end_line=30, char_count=50),
    ]
    state.raw_extractions = [
        ChunkExtractOutput(chunk_index=2, items=[
            ItemOutput(type="claim", quote="body claim", line=22),
        ]),
        ChunkExtractOutput(chunk_index=0, items=[
            ItemOutput(type="claim", quote="intro claim", line=3),
        ]),
    ]
    state.raw_decisions = [
        ChunkDecideOutput(chunk_index=2, decisions=[
            ClaimDecision(claim_id=0, supported=False, reason="no support"),
        ]),
        ChunkDecideOutput(chunk_index=0, decisions=[
            ClaimDecision(claim_id=0, supported=False, reason="no support"),
        ]),
    ]
    return state


def test_classify_pins_chunk_index_and_orders_by_chunk():
    state = _state()
    agent = _StubAgent()
    asyncio.run(_custom_classify(state, _ctx(agent), _Spec()))

    # One call per chunk with unsupported claims.
    assert sorted(agent.calls) == ["classify-chunk-0", "classify-chunk-2"]

    # raw_scans sorted by chunk index, each gap pinned to its scan's chunk.
    assert [s.chunk_index for s in state.raw_scans] == [0, 2]
    for scan in state.raw_scans:
        assert all(g.chunk_index == scan.chunk_index for g in scan.gaps)

    # Merged gaps are ordered by chunk (0 before 2) despite chunk 2
    # completing first, and the model's bogus chunk_index is overwritten.
    gaps = state.raw_classifications.gaps
    assert [g.chunk_index for g in gaps] == [0, 2]
    assert [g.item_quote for g in gaps] == ["claim in chunk 0", "claim in chunk 2"]


def test_classify_no_unsupported_skips_calls():
    state = _state()
    state.raw_decisions = [
        ChunkDecideOutput(chunk_index=2, decisions=[
            ClaimDecision(claim_id=0, supported=True, reason="ok"),
        ]),
        ChunkDecideOutput(chunk_index=0, decisions=[
            ClaimDecision(claim_id=0, supported=True, reason="ok"),
        ]),
    ]
    agent = _StubAgent()
    asyncio.run(_custom_classify(state, _ctx(agent), _Spec()))

    assert agent.calls == []
    assert state.raw_scans == []
    assert state.raw_classifications.gaps == []
