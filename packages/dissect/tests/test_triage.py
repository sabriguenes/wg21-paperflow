#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the embedding-triage helpers.

Most tests feed plain nested lists in lieu of real cosine tensors; the
helpers only need ``[i][j]`` access and optional ``shape``. The
empty-input passthrough (``cos`` matrix is ``None`` because the
underlying claim or evidence list was empty) is exercised explicitly
since the Step 8/9 hooks rely on those helpers degrading silently in
those edge cases.
"""

from __future__ import annotations


from dissect import triage
from dissect.models import Claim, SourceLoc


def _claim(uid: int, text: str, *, depends_on: list[int] | None = None) -> Claim:
    return Claim(
        uid=uid,
        loc=SourceLoc(line=uid, start_char=0, end_char=len(text)),
        text=text,
        original_quotes=[text],
        section=f"{uid}",
        question=f"Q{uid}?",
        depends_on=depends_on or [],
    )


# ---- top_k_indices -------------------------------------------------------


def test_top_k_indices_picks_highest():
    idxs = triage.top_k_indices([0.1, 0.9, 0.5], [10, 20, 30], k=2)
    assert idxs == [1, 2]


def test_top_k_indices_tiebreak_by_uid():
    # Three equal scores: stable order should be uid ascending.
    idxs = triage.top_k_indices([0.5, 0.5, 0.5], [30, 10, 20], k=3)
    assert idxs == [1, 2, 0]  # uids 10, 20, 30 in that order


def test_top_k_indices_tiebreak_partial_with_higher_score():
    # uid=20 has highest score; uid=10 and uid=30 tie on second place.
    idxs = triage.top_k_indices([0.5, 0.9, 0.5], [30, 20, 10], k=3)
    assert idxs == [1, 2, 0]  # uid 20, then uid 10, then uid 30


def test_top_k_indices_clamps_k_to_input():
    idxs = triage.top_k_indices([0.5, 0.3], [10, 20], k=5)
    assert idxs == [0, 1]


def test_top_k_indices_empty_input():
    assert triage.top_k_indices([], [], k=3) == []


def test_top_k_indices_zero_k():
    assert triage.top_k_indices([0.5, 0.3], [10, 20], k=0) == []


# ---- above_threshold_pairs ----------------------------------------------


def test_above_threshold_pairs_basic():
    cos = [
        [1.0, 0.7, 0.2],
        [0.7, 1.0, 0.8],
        [0.2, 0.8, 1.0],
    ]
    pairs = triage.above_threshold_pairs(cos, [10, 20, 30], threshold=0.5)
    assert pairs == [(10, 20), (20, 30)]


def test_above_threshold_pairs_returns_canonical_uid_order():
    cos = [
        [1.0, 0.9],
        [0.9, 1.0],
    ]
    pairs = triage.above_threshold_pairs(cos, [20, 10], threshold=0.5)
    assert pairs == [(10, 20)]


def test_above_threshold_pairs_returns_sorted():
    cos = [
        [1.0, 0.6, 0.7],
        [0.6, 1.0, 0.8],
        [0.7, 0.8, 1.0],
    ]
    pairs = triage.above_threshold_pairs(cos, [3, 1, 2], threshold=0.5)
    assert pairs == sorted(pairs)


def test_above_threshold_pairs_threshold_excludes_equal():
    # Pairs at exactly the threshold are excluded; only strictly above counts.
    cos = [[1.0, 0.5], [0.5, 1.0]]
    pairs = triage.above_threshold_pairs(cos, [1, 2], threshold=0.5)
    assert pairs == []


def test_above_threshold_pairs_fallback_returns_empty():
    assert triage.above_threshold_pairs(None, [1, 2, 3], threshold=0.5) == []


# ---- top_k_per_row -------------------------------------------------------


def test_top_k_per_row_basic():
    cos = [
        [0.1, 0.9, 0.5],   # claim 100: top-2 evidence 11, 12
        [0.6, 0.2, 0.7],   # claim 200: top-2 evidence 10, 12
    ]
    out = triage.top_k_per_row(cos, [100, 200], [10, 11, 12], k=2)
    assert out == {100: [11, 12], 200: [10, 12]}


def test_top_k_per_row_fallback_returns_full_col_set():
    out = triage.top_k_per_row(None, [100, 200], [12, 10, 11], k=2)
    assert out == {100: [10, 11, 12], 200: [10, 11, 12]}


def test_top_k_per_row_clamps_k_to_col_count():
    cos = [[0.9, 0.1]]
    out = triage.top_k_per_row(cos, [100], [10, 11], k=5)
    assert out == {100: [10, 11]}


# ---- centrality_scores ---------------------------------------------------


def test_centrality_includes_in_degree():
    a = _claim(1, "alpha")
    b = _claim(2, "beta", depends_on=[1])
    c = _claim(3, "gamma", depends_on=[1])
    scores = triage.centrality_scores(
        [a, b, c],
        claim_cos=None,
        evid_cos=None,
        evidence_threshold=0.5,
        peer_threshold=0.5,
    )
    assert scores == {1: 2.0, 2: 0.0, 3: 0.0}


def test_centrality_includes_evidence_prominence():
    a = _claim(1, "alpha")
    b = _claim(2, "beta")
    evid_cos = [
        [0.9, 0.4],     # claim 1 close to evidence 0 only
        [0.6, 0.7],     # claim 2 close to both
    ]
    scores = triage.centrality_scores(
        [a, b],
        claim_cos=None,
        evid_cos=evid_cos,
        evidence_threshold=0.5,
        peer_threshold=0.5,
    )
    assert scores == {1: 1.0, 2: 2.0}


def test_centrality_includes_peer_prominence_excludes_self():
    a = _claim(1, "alpha")
    b = _claim(2, "beta")
    c = _claim(3, "gamma")
    claim_cos = [
        [1.0, 0.9, 0.1],
        [0.9, 1.0, 0.9],
        [0.1, 0.9, 1.0],
    ]
    scores = triage.centrality_scores(
        [a, b, c],
        claim_cos=claim_cos,
        evid_cos=None,
        evidence_threshold=0.5,
        peer_threshold=0.5,
    )
    assert scores == {1: 1.0, 2: 2.0, 3: 1.0}


def test_centrality_unknown_depends_on_uid_ignored():
    a = _claim(1, "alpha", depends_on=[999])
    scores = triage.centrality_scores(
        [a], None, None,
        evidence_threshold=0.5, peer_threshold=0.5,
    )
    assert scores == {1: 0.0}


# ---- tier_split ----------------------------------------------------------


def test_tier_split_top_k_floor():
    scores = {i: float(i) for i in range(5)}  # 0,1,2,3,4
    tier1, tier2 = triage.tier_split(scores, top_k=3, top_fraction=0.0)
    assert tier1 == [2, 3, 4]
    assert tier2 == [0, 1]


def test_tier_split_top_fraction_dominates_when_larger():
    scores = {i: float(i) for i in range(20)}
    tier1, tier2 = triage.tier_split(scores, top_k=3, top_fraction=0.5)
    assert len(tier1) == 10
    assert len(tier2) == 10
    assert tier1 == sorted(tier1)
    assert tier2 == sorted(tier2)


def test_tier_split_tiebreak_by_uid():
    scores = {3: 1.0, 1: 1.0, 2: 1.0}
    tier1, _ = triage.tier_split(scores, top_k=2, top_fraction=0.0)
    assert tier1 == [1, 2]  # uids 1 and 2 win over 3 on ascending tiebreak


def test_tier_split_clamps_to_n():
    scores = {1: 1.0, 2: 2.0}
    tier1, tier2 = triage.tier_split(scores, top_k=10, top_fraction=0.0)
    assert tier1 == [1, 2]
    assert tier2 == []


def test_tier_split_empty():
    tier1, tier2 = triage.tier_split({}, top_k=5, top_fraction=0.5)
    assert tier1 == []
    assert tier2 == []


# ---- interleave_propositions --------------------------------------------


def test_interleave_propositions_breaks_claim_runs():
    pairs = [
        (1, 10), (1, 11), (1, 12),
        (2, 20), (2, 21), (2, 22),
    ]
    batches = triage.interleave_propositions(pairs, batch_claims=2, batch_evidence=3)
    assert len(batches) == 1
    # Round 0: (1,10), (2,20). Round 1: (1,11), (2,21). Round 2: (1,12), (2,22).
    assert batches[0] == [
        (1, 10), (2, 20),
        (1, 11), (2, 21),
        (1, 12), (2, 22),
    ]


def test_interleave_propositions_splits_into_batches():
    pairs = [
        (uid, e)
        for uid in (1, 2, 3, 4, 5)
        for e in (10, 11)
    ]
    batches = triage.interleave_propositions(pairs, batch_claims=2, batch_evidence=2)
    assert len(batches) == 3
    # Batches by claim chunks: [1,2], [3,4], [5].
    assert {p[0] for p in batches[0]} == {1, 2}
    assert {p[0] for p in batches[1]} == {3, 4}
    assert {p[0] for p in batches[2]} == {5}


def test_interleave_propositions_partial_evidence():
    pairs = [(1, 10), (2, 20), (2, 21)]
    batches = triage.interleave_propositions(pairs, batch_claims=2, batch_evidence=2)
    assert batches == [[(1, 10), (2, 20), (2, 21)]]


def test_interleave_propositions_empty():
    assert triage.interleave_propositions([], batch_claims=4, batch_evidence=5) == []


def test_interleave_propositions_sorts_evidence_in_each_bucket():
    pairs = [(1, 30), (1, 10), (1, 20)]
    batches = triage.interleave_propositions(pairs, batch_claims=1, batch_evidence=3)
    assert batches == [[(1, 10), (1, 20), (1, 30)]]


# ---- empty-input passthrough ---------------------------------------------


def test_embed_returns_none_on_empty():
    assert triage.embed([]) is None


def test_cosine_matrix_propagates_none():
    assert triage.cosine_matrix(None, [1, 2]) is None
    assert triage.cosine_matrix([1, 2], None) is None


def test_helpers_handle_empty_evidence_chain():
    """When evidence is empty, the chain must collapse to safe defaults.

    Mirrors what ``_custom_verify`` sees when a paper has alive claims
    but no alive evidence: ``embed_evidence`` returns ``None``, every
    downstream cosine matrix involving evidence is ``None``, and the
    helpers must degrade to "no triage" without crashing.
    """
    a = _claim(1, "alpha", depends_on=[2])
    b = _claim(2, "beta")
    evid_vecs = triage.embed([])
    assert evid_vecs is None

    fake_claim_vecs = object()  # stand-in for a real tensor
    ec = triage.cosine_matrix(fake_claim_vecs, evid_vecs)
    assert ec is None

    triaged = triage.top_k_per_row(ec, [1, 2], [], k=5)
    assert triaged == {1: [], 2: []}

    scores = triage.centrality_scores(
        [a, b], claim_cos=None, evid_cos=ec,
        evidence_threshold=0.5, peer_threshold=0.5,
    )
    assert scores == {1: 0.0, 2: 1.0}  # in-degree contribution preserved
