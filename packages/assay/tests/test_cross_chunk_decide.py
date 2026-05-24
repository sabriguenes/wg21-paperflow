#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for `_cross_chunk_decide` (Step 5 follow-up pass).

Uses a stubbed agent and bypasses the real LLM. Verifies global claim
ID round-trip from `(chunk_index, local_id)` and that reconciliation
logs missing / hallucinated IDs without crashing.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from assay.models import (
    ChunkDecideOutput,
    ChunkEntry,
    ChunkExtractOutput,
    ClaimDecision,
    CrossChunkClaimDecision,
    CrossChunkDecideOutput,
    ItemOutput,
    PipelineState,
)
from assay.pipeline import _cross_chunk_decide


class _StubAgent:
    """Agent stub: returns a pre-canned CrossChunkDecideOutput on run()."""

    def __init__(self, decisions: list[CrossChunkClaimDecision]) -> None:
        self._decisions = decisions
        self.calls: list[dict] = []
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
        self.calls.append({
            "label": label,
            "user_message": user_message,
        })
        return CrossChunkDecideOutput(decisions=self._decisions)


class _StubCtx:
    """Minimum StepContext-shaped surface used by _cross_chunk_decide."""

    def __init__(self, sections: dict[str, str]):
        self.debug = False
        self.debug_log = None
        self._sections = sections

        class _Prompt:
            @staticmethod
            def step_section(_name: str) -> str:
                return ""

        self.prompt = _Prompt()


def _state_with_two_chunks() -> PipelineState:
    """Build a state with two claims in chunk 0 and one evidence in chunk 2."""
    state = PipelineState()
    state.paper_id = "P9999R0"
    state.chunk_map = [
        ChunkEntry(index=0, heading="Intro", start_line=1, end_line=10, char_count=50),
        ChunkEntry(index=2, heading="Body", start_line=20, end_line=30, char_count=50),
    ]
    state.raw_extractions = [
        ChunkExtractOutput(chunk_index=0, items=[
            ItemOutput(type="claim", quote="exactly three things", line=3),
            ItemOutput(type="claim", quote="separate concern", line=5),
        ]),
        ChunkExtractOutput(chunk_index=2, items=[
            ItemOutput(type="evidence",
                       quote="The io_env struct contains exactly three fields", line=24),
        ]),
    ]
    state.raw_decisions = [
        ChunkDecideOutput(chunk_index=0, decisions=[
            ClaimDecision(claim_id=0, supported=False, reason="no struct shown"),
            ClaimDecision(claim_id=1, supported=False, reason="no support"),
        ]),
        ChunkDecideOutput(chunk_index=2, decisions=[]),
    ]
    state.claim_global_id_map = {
        (0, 0): 100,
        (0, 1): 101,
    }
    state._next_id = 102
    return state


def test_cross_chunk_decide_flips_supported_using_global_id():
    state = _state_with_two_chunks()
    agent = _StubAgent([
        CrossChunkClaimDecision(
            claim_id=100, supported=True,
            supporting_evidence_lines=[24],
            reason="evidence in chunk 2 lists the three fields",
        ),
        CrossChunkClaimDecision(
            claim_id=101, supported=False,
            supporting_evidence_lines=[],
            reason="still unsupported",
        ),
    ])
    ctx = _StubCtx({})
    asyncio.run(_cross_chunk_decide(state, ctx, agent, max_output=8192, thinking=None))

    decisions = {d.claim_id: d for d in state.raw_decisions[0].decisions}
    assert decisions[0].supported is True
    assert "cross-chunk" in decisions[0].reason
    assert decisions[1].supported is False
    assert state.raw_decisions[1].decisions == []


def test_cross_chunk_decide_logs_reconciliation_for_hallucinated_ids(caplog):
    state = _state_with_two_chunks()
    agent = _StubAgent([
        CrossChunkClaimDecision(claim_id=100, supported=True, reason="ok"),
        CrossChunkClaimDecision(claim_id=999, supported=True, reason="invented"),
    ])
    ctx = _StubCtx({})
    with caplog.at_level(logging.WARNING, logger="assay.pipeline"):
        asyncio.run(_cross_chunk_decide(state, ctx, agent, max_output=8192, thinking=None))
    text = " ".join(r.message for r in caplog.records)
    assert "missing" in text and "hallucinated" in text


def test_cross_chunk_decide_no_unsupported_skips_call():
    state = _state_with_two_chunks()
    state.raw_decisions = [
        ChunkDecideOutput(chunk_index=0, decisions=[
            ClaimDecision(claim_id=0, supported=True, reason="ok"),
            ClaimDecision(claim_id=1, supported=True, reason="ok"),
        ]),
    ]
    agent = _StubAgent([])
    ctx = _StubCtx({})
    asyncio.run(_cross_chunk_decide(state, ctx, agent, max_output=8192, thinking=None))
    assert agent.calls == []


def test_cross_chunk_decide_no_evidence_skips_call():
    state = _state_with_two_chunks()
    # Strip evidence from chunk 2
    state.raw_extractions[1] = ChunkExtractOutput(chunk_index=2, items=[])
    agent = _StubAgent([])
    ctx = _StubCtx({})
    asyncio.run(_cross_chunk_decide(state, ctx, agent, max_output=8192, thinking=None))
    assert agent.calls == []
