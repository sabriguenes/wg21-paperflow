#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the Step 8 Verify self-pair filter in ``_custom_verify``.

Covers the deterministic harness layer that drops ``(claim_uid,
evidence_uid)`` pairs whose normalized text is identical. Upstream
Steps 3 (Extract Evidence) and 5 (Extract Factual) can capture the
same source sentence twice, and without this filter Step 8 would
record the claim as ``proven`` by an evidence item identical to it.

The model-side backstop lives in ``dissect.md`` under
``Sub-prompt: Batched Verify`` and is not exercised here (no LLM in
the test).
"""

from __future__ import annotations


import pytest
from pipeline import StepContext
from pipeline.agents import AgentBackend
from pipeline.model_backends import Llama3Backend

from dissect import pipeline
from dissect.models import (
    BatchVerifyOutput,
    Claim,
    DisclaimPairOutput,
    Evidence,
    PipelineState,
    SourceLoc,
    VerifyProposition,
)
from dissect.pipeline import _custom_verify, _normalize


def _stub_agents() -> dict[str, AgentBackend]:
    stub = AgentBackend(Llama3Backend(base_url="", api_key="", model=""))
    return {"fast": stub, "default": stub, "tool": stub}


# ---- _normalize ----------------------------------------------------------


def test_normalize_strips_outer_whitespace():
    assert _normalize("  hello  ") == "hello"


def test_normalize_casefolds():
    assert _normalize("Hello") == _normalize("hello")
    assert _normalize("HELLO") == _normalize("hello")


def test_normalize_collapses_internal_whitespace():
    assert _normalize("a   b\tc\nd") == "a b c d"


def test_normalize_strips_trailing_terminal_punctuation():
    for suffix in (".", ",", ";", ":", "!", "?", "...", " . ", "?!"):
        assert _normalize(f"hello{suffix}") == "hello"


def test_normalize_keeps_internal_punctuation():
    assert _normalize("a.b.c") == "a.b.c"
    assert _normalize("U.S.A.") == "u.s.a"


def test_normalize_identical_after_only_formatting_differences():
    a = "Boost.Lockfree first published its SPSC queue in version 1.49."
    b = "  boost.lockfree first PUBLISHED  its SPSC queue in version 1.49  "
    assert _normalize(a) == _normalize(b)


def test_normalize_distinguishes_substantive_differences():
    a = "X shipped in 2012."
    b = "The X library shipped in 2012."
    assert _normalize(a) != _normalize(b)


def test_normalize_empty_input():
    assert _normalize("") == ""
    assert _normalize("   ") == ""
    assert _normalize("...") == ""


# ---- _custom_verify self-pair filter ------------------------------------


def _claim(uid: int, text: str) -> Claim:
    return Claim(
        uid=uid,
        loc=SourceLoc(line=uid, start_char=0, end_char=len(text)),
        text=text,
        original_quotes=[text],
        section=f"{uid}",
        question=f"Q{uid}?",
        depends_on=[],
    )


def _evidence(uid: int, text: str) -> Evidence:
    return Evidence(
        uid=uid,
        loc=SourceLoc(line=uid, start_char=0, end_char=len(text)),
        text=text,
        original_quotes=[text],
        section=f"{uid}",
        supports=[],
        quantitative=False,
        cited=False,
        verifiable=True,
        normative=False,
    )


class _StubVecs:
    """Sentinel sequence with the length the helpers care about."""

    def __init__(self, n: int):
        self._n = n

    def __len__(self) -> int:
        return self._n


def _ones(n: int, m: int) -> list[list[float]]:
    """Return an n by m cosine matrix populated with 1.0 entries.

    Forces every (claim, evidence) cosine to 1.0, so triage's top-K
    keeps every evidence item in scope and the harness filter is the
    only thing standing between a self-pair and the LLM.
    """
    return [[1.0 for _ in range(m)] for _ in range(n)]


@pytest.mark.anyio
async def test_custom_verify_drops_identical_text_pair(monkeypatch):
    """Identical-text (claim, evidence) pair is filtered before the LLM call.

    Two claims, two evidence items, with claim 1's text identical to
    evidence 10's text. After triage, four candidate pairs exist:
    (1, 10), (1, 11), (2, 10), (2, 11). The filter must drop (1, 10);
    the surviving three must reach ``run_task``.
    """
    state = PipelineState(
        paper_source="dummy",
        normative_claims=[
            _claim(1, "Boost.Lockfree first published its SPSC queue in 2012."),
            _claim(2, "Channels are essential for inter-thread message passing."),
        ],
        deduped_evidence=[
            _evidence(
                10,
                # Same text as claim 1 modulo trailing space and casing.
                "  Boost.Lockfree first published its SPSC queue in 2012  ",
            ),
            _evidence(11, "Independent measurements report a 2x regression."),
        ],
    )

    # Stub the embedding pipeline so the test runs offline. The cosine
    # matrices are uniform 1.0 so every (claim, evidence) pair survives
    # top-K triage and reaches the self-pair filter. ``triage`` is
    # imported as ``from dissect import triage`` in pipeline.py, so we
    # patch through that module attribute.
    monkeypatch.setattr(
        pipeline.triage, "embed_claims", lambda claims: _StubVecs(len(claims)),
    )
    monkeypatch.setattr(
        pipeline.triage, "embed_evidence", lambda ev: _StubVecs(len(ev)),
    )
    monkeypatch.setattr(
        pipeline.triage,
        "cosine_matrix",
        lambda a, b: _ones(len(a), len(b)) if a is not None and b is not None else None,
    )

    captured_payloads: list[list[dict]] = []

    async def fake_run_task(agent, system_prompt, user_message, output_type, **kwargs):
        # The Verify step calls run_task in two phases: Batched Verify
        # (output_type=BatchVerifyOutput) and Detect Disclaim
        # (output_type=DisclaimPairOutput). We only care about the
        # first; return a benign empty result for the second so the
        # call returns through to Phase 5 aggregation.
        if output_type is DisclaimPairOutput:
            # The harness overrides claim_a_uid/claim_b_uid via
            # model_copy after the call, so the stub values don't
            # propagate; "none" keeps Phase 5 from emitting verdicts.
            return DisclaimPairOutput(
                claim_a_uid=0, claim_b_uid=0, relation="none",
            )
        captured_payloads.append([
            {"claim_uid": cuid, "evidence_uid": euid}
            for cuid, euid in _extract_pairs_from_msg(user_message)
        ])
        return BatchVerifyOutput(judgements=[
            VerifyProposition(claim_uid=cuid, evidence_uid=euid, verdict="unrelated")
            for cuid, euid in _extract_pairs_from_msg(user_message)
        ])

    monkeypatch.setattr(pipeline, "run_task", fake_run_task)

    ctx = StepContext(
        sections={"8. Verify": "### Sub-prompt: Batched Verify\n\nstub instructions\n"},
        agents=_stub_agents(),
        researcher=None,
        backend=None,
        debug=False,
        pid="P9999R0",
        tool_registry={},
    )
    ctx._current_spec = None  # run_task is stubbed; agent lookup uses ctx.agents.

    await _custom_verify(state, ctx)

    assert state.self_pair_dropped == 1, (
        f"Expected exactly one self-pair drop, got {state.self_pair_dropped}."
    )

    seen_pairs: set[tuple[int, int]] = {
        (p["claim_uid"], p["evidence_uid"])
        for batch in captured_payloads
        for p in batch
    }
    assert (1, 10) not in seen_pairs, (
        f"Identical-text pair (1, 10) reached the LLM: {seen_pairs}"
    )
    assert seen_pairs == {(1, 11), (2, 10), (2, 11)}, (
        f"Surviving pairs do not match expectation: {sorted(seen_pairs)}"
    )


@pytest.mark.anyio
async def test_custom_verify_keeps_pairs_when_no_self_collision(monkeypatch):
    """No drops happen when every (claim, evidence) text differs."""
    state = PipelineState(
        paper_source="dummy",
        normative_claims=[
            _claim(1, "Alpha must hold."),
            _claim(2, "Beta should always pass."),
        ],
        deduped_evidence=[
            _evidence(10, "Measurement X reports value Y."),
            _evidence(11, "Library Z shipped in 2015."),
        ],
    )

    monkeypatch.setattr(
        pipeline.triage, "embed_claims", lambda claims: _StubVecs(len(claims)),
    )
    monkeypatch.setattr(
        pipeline.triage, "embed_evidence", lambda ev: _StubVecs(len(ev)),
    )
    monkeypatch.setattr(
        pipeline.triage,
        "cosine_matrix",
        lambda a, b: _ones(len(a), len(b)) if a is not None and b is not None else None,
    )
    async def fake_run_task(agent, system_prompt, user_message, output_type, **kwargs):
        if output_type is DisclaimPairOutput:
            return DisclaimPairOutput(
                claim_a_uid=0, claim_b_uid=0, relation="none",
            )
        return BatchVerifyOutput(judgements=[])

    monkeypatch.setattr(pipeline, "run_task", fake_run_task)

    ctx = StepContext(
        sections={"8. Verify": "### Sub-prompt: Batched Verify\n\nstub instructions\n"},
        agents=_stub_agents(),
        researcher=None,
        backend=None,
        debug=False,
        pid="P9999R0",
        tool_registry={},
    )
    ctx._current_spec = None

    await _custom_verify(state, ctx)

    assert state.self_pair_dropped == 0


# ---- helpers -------------------------------------------------------------


def _extract_pairs_from_msg(user_msg: str) -> list[tuple[int, int]]:
    """Pull ``(claim_uid, evidence_uid)`` pairs from a wrapped prompt body.

    The real harness builds ``user_msg`` as a Markdown block containing
    a JSON list of propositions; rather than parse JSON across the
    wrap_source delimiters, we extract the uids with a tolerant regex.
    """
    import re

    return [
        (int(m.group(1)), int(m.group(2)))
        for m in re.finditer(
            r'"claim_uid"\s*:\s*(\d+)[^{}]*?"evidence_uid"\s*:\s*(\d+)',
            user_msg,
            re.DOTALL,
        )
    ]
