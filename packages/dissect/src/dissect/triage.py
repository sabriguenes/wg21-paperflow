#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Embedding-based triage helpers for Verify and Load-Bearing.

The Step 8 Verify and Step 9 Load-Bearing custom hooks use these helpers
to bound each LLM call's input regardless of paper size:

* Embed every alive claim and evidence item once with the shared
  ``BAAI/bge-small-en-v1.5`` model from :mod:`dissect.shadow`.
* Compute cosine similarity matrices (claim x evidence, claim x claim).
* For each claim, keep only the top-K evidence items by cosine. The LLM
  for that claim never sees the rest.
* For disclaim detection, only consider claim pairs whose cosine exceeds
  a threshold (disclaim pairs share topic and polarity).
* For Load-Bearing centrality, score each claim by graph in-degree plus
  evidence and peer prominence; the top tier is sent to the LLM, the
  rest are auto-classified as ``peripheral``.

All helpers are pure Python: deterministic vectors out of the model
(same string in, same numbers out), deterministic tie-breaks
(score desc, then uid asc). No LLM calls. No network.

The embedding model is a hard requirement of :mod:`dissect` (see the
package ``pyproject.toml``). Consistent quality of Step 8/9 results
depends on having real embeddings, not a fallback. The few ``None``
checks below cover empty-input edge cases (no claims, no evidence) so
callers do not need to special-case them; they never fire because
sentence-transformers is missing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Iterable

from sentence_transformers import util

from dissect.shadow import _load_model

if TYPE_CHECKING:
    from dissect.models import Claim, Evidence

logger = logging.getLogger(__name__)


def _claim_text(c: "Claim") -> str:
    """Return the embedding input for a claim."""
    q = (c.question or "").strip()
    return f"{c.text} | {q}" if q else c.text


def _evidence_text(e: "Evidence") -> str:
    """Return the embedding input for an evidence item."""
    sup = " ".join(s.strip() for s in (e.supports or []) if s.strip())
    return f"{e.text} | {sup}" if sup else e.text


def embed(texts: list[str]) -> Any | None:
    """Embed ``texts`` with the shared embedding model singleton.

    Returns a 2D tensor of shape ``(len(texts), 384)`` for non-empty
    input. Returns ``None`` for empty input so callers can cheap-skip
    downstream cosine computations.
    """
    if not texts:
        return None
    return _load_model().encode(
        texts,
        convert_to_tensor=True,
        show_progress_bar=False,
    )


def embed_claims(claims: list["Claim"]) -> Any | None:
    """Embed a list of alive Claims by their ``text | question`` form."""
    return embed([_claim_text(c) for c in claims])


def embed_evidence(ev: list["Evidence"]) -> Any | None:
    """Embed a list of alive Evidence by their ``text | supports`` form."""
    return embed([_evidence_text(e) for e in ev])


def cosine_matrix(a: Any, b: Any) -> Any | None:
    """Return the cosine similarity matrix ``a.dot(b.T)`` row-normalized.

    Inputs are tensors from :func:`embed`. Output shape is
    ``(len(a), len(b))``. Returns ``None`` if either input is ``None``
    (empty-input passthrough).
    """
    if a is None or b is None:
        return None
    return util.cos_sim(a, b)


def top_k_indices(scores: list[float], uids: list[int], k: int) -> list[int]:
    """Return indices of the ``k`` highest-scoring items.

    Tie-breaks deterministically: score descending, then uid ascending.
    ``uids`` provides the deterministic tiebreak key per index.

    Returns at most ``k`` indices. The result is sorted by the sort key
    (highest score first); call sites that need a stable per-claim
    iteration order should re-sort by uid afterward.
    """
    if k <= 0 or not scores:
        return []
    keyed = sorted(
        range(len(scores)),
        key=lambda i: (-float(scores[i]), uids[i]),
    )
    return keyed[: min(k, len(keyed))]


def above_threshold_pairs(
    cos: Any,
    uids: list[int],
    threshold: float,
) -> list[tuple[int, int]]:
    """Return ``(uid_i, uid_j)`` pairs where ``i < j`` and cosine > threshold.

    Iterates the upper triangle in a deterministic order, returns pairs
    sorted by ``(uid_i, uid_j)``. Returns ``[]`` if ``cos`` is ``None``
    (the empty-input passthrough).
    """
    if cos is None:
        return []
    n = len(uids)
    pairs: list[tuple[int, int]] = []
    for i in range(n):
        row = cos[i]
        for j in range(i + 1, n):
            if float(row[j]) > threshold:
                a, b = uids[i], uids[j]
                pairs.append((a, b) if a < b else (b, a))
    pairs.sort()
    return pairs


def top_k_per_row(
    cos: Any,
    row_uids: list[int],
    col_uids: list[int],
    k: int,
) -> dict[int, list[int]]:
    """Return per-row top-K column uids by cosine.

    Output is a dict ``{row_uid: [col_uid, ...]}`` mapping each row uid
    to up to K column uids, sorted by their column uid (deterministic
    iteration order at call sites). Returns ``{row_uid: sorted(col_uids)}``
    when ``cos`` is ``None`` (the empty-input passthrough); with
    empty ``col_uids`` this collapses to an empty list per row.
    """
    if cos is None:
        return {ru: sorted(col_uids) for ru in row_uids}
    out: dict[int, list[int]] = {}
    for i, ru in enumerate(row_uids):
        scores = [float(cos[i][j]) for j in range(len(col_uids))]
        idxs = top_k_indices(scores, col_uids, k)
        out[ru] = sorted(col_uids[j] for j in idxs)
    return out


def centrality_scores(
    claims: list["Claim"],
    claim_cos: Any,
    evid_cos: Any,
    *,
    evidence_threshold: float,
    peer_threshold: float,
) -> dict[int, float]:
    """Score each claim by graph in-degree plus prominence.

    ``score(claim) = depends_on_in_degree(claim)
                     + count(evidence with cosine > evidence_threshold)
                     + count(peer claims with cosine > peer_threshold)``

    All three components contribute equally; the score is a non-negative
    integer expressed as float for downstream sorting. Returns a dict
    keyed by ``claim.uid``. When ``claim_cos`` or ``evid_cos`` is
    ``None`` (empty-input passthrough), the corresponding component
    contributes 0; the graph in-degree always contributes.
    """
    n = len(claims)
    uids = [c.uid for c in claims]
    in_degree: dict[int, int] = {u: 0 for u in uids}
    for c in claims:
        for dep_uid in c.depends_on or []:
            if dep_uid in in_degree:
                in_degree[dep_uid] += 1

    evid_prom: dict[int, int] = {u: 0 for u in uids}
    if evid_cos is not None:
        m = evid_cos.shape[1] if hasattr(evid_cos, "shape") else len(evid_cos[0])
        for i in range(n):
            row = evid_cos[i]
            evid_prom[uids[i]] = sum(
                1 for j in range(m) if float(row[j]) > evidence_threshold
            )

    peer_prom: dict[int, int] = {u: 0 for u in uids}
    if claim_cos is not None:
        for i in range(n):
            row = claim_cos[i]
            peer_prom[uids[i]] = sum(
                1 for j in range(n)
                if i != j and float(row[j]) > peer_threshold
            )

    return {
        u: float(in_degree[u] + evid_prom[u] + peer_prom[u])
        for u in uids
    }


def tier_split(
    scores: dict[int, float],
    *,
    top_k: int,
    top_fraction: float,
) -> tuple[list[int], list[int]]:
    """Split claim uids into a central tier and a peripheral tier.

    Tier 1 size is ``max(top_k, int(top_fraction * N))`` to bias
    generously: small papers always include at least ``top_k`` claims;
    large papers include at least ``top_fraction`` of them. Returns
    ``(tier1_uids, tier2_uids)`` both sorted ascending by uid for
    deterministic iteration at call sites.
    """
    if not scores:
        return [], []
    n = len(scores)
    cut = max(top_k, int(top_fraction * n))
    cut = min(cut, n)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    tier1 = sorted(uid for uid, _ in ranked[:cut])
    tier2 = sorted(uid for uid, _ in ranked[cut:])
    return tier1, tier2


def interleave_propositions(
    pairs: Iterable[tuple[int, int]],
    batch_claims: int,
    batch_evidence: int,
) -> list[list[tuple[int, int]]]:
    """Group ``(claim_uid, evidence_uid)`` pairs into batches.

    Pairs are bucketed by claim then interleaved: within each batch,
    proposition 1 is claim_A/ev_1, proposition 2 is claim_B/ev_1, ...
    proposition K+1 is claim_A/ev_2, and so on. This breaks runs of
    same-claim propositions inside a batch, which we hypothesise
    reduces local pattern bias when the LLM judges 20 propositions in
    one prompt.

    Batches contain up to ``batch_claims * batch_evidence`` propositions.
    The last batch may be smaller. Returns ``[]`` when ``pairs`` is
    empty.
    """
    buckets: dict[int, list[int]] = {}
    for cuid, euid in pairs:
        buckets.setdefault(cuid, []).append(euid)
    if not buckets:
        return []
    for ev_list in buckets.values():
        ev_list.sort()

    claim_order = sorted(buckets.keys())
    batches: list[list[tuple[int, int]]] = []
    cursor = 0
    while cursor < len(claim_order):
        chunk = claim_order[cursor:cursor + batch_claims]
        cursor += batch_claims
        batch: list[tuple[int, int]] = []
        for ev_slot in range(batch_evidence):
            for cuid in chunk:
                ev_list = buckets[cuid]
                if ev_slot < len(ev_list):
                    batch.append((cuid, ev_list[ev_slot]))
        if batch:
            batches.append(batch)
    return batches
