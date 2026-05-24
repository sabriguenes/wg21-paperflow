#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for assay.harness helpers added by the pipeline-fixes patch.

Covers:

- `upgrade_gaps` with embedder present, embedder missing (lexical fallback),
  and embedder raising.
- `dedupe_findings` against existing corpus, paraphrase vs distinct cases.
- `_ensure_int_list` coercion on the GapOutput model.
"""

from __future__ import annotations

import numpy as np
import pytest

from assay.harness import dedupe_findings, upgrade_gaps
from assay.models import FindingOutput, GapOutput, _ensure_int_list


class _StubEmbedder:
    """Embedder stub that returns a torch-tensor-like object."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def embed(self, texts):  # noqa: D401
        import torch  # type: ignore[import-untyped]

        rows = []
        for t in texts:
            vec = self._vectors.get(t)
            if vec is None:
                # Default: orthogonal unit vector keyed off text length.
                vec = [1.0, 0.0, 0.0] if len(t) % 2 == 0 else [0.0, 1.0, 0.0]
            rows.append(vec)
        return torch.tensor(rows, dtype=torch.float32)


class _BrokenEmbedder:
    def embed(self, texts):
        raise RuntimeError("simulated embedder failure")


def _gap(gap_text: str, severity: str = "significant") -> GapOutput:
    return GapOutput(
        chunk_index=0,
        item_quote="x",
        line=1,
        gap=gap_text,
        why_important="y",
        primary_lens="Performance",
        severity=severity,
    )


def _finding(title: str, explanation: str = "") -> FindingOutput:
    return FindingOutput(
        title=title,
        lens="Design",
        severity="significant",
        quote="",
        line=0,
        explanation=explanation or title,
    )


# -- _ensure_int_list -------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, []),
        (0, []),
        ("", []),
        (5, [5]),
        ([1, 2, 3], [1, 2, 3]),
        ("1,2", [1, 2]),
    ],
)
def test_ensure_int_list_coercion(raw, expected):
    assert _ensure_int_list(raw) == expected


def test_gap_default_closed_by_is_list():
    g = _gap("anything")
    assert g.closed_by == []


def test_gap_legacy_int_closed_by_coerced_to_list():
    g = GapOutput.model_validate({
        "chunk_index": 0, "item_quote": "x", "line": 1,
        "gap": "?", "why_important": "y", "primary_lens": "Design",
        "closed_by": 7,
    })
    assert g.closed_by == [7]


# -- upgrade_gaps -----------------------------------------------------------


def test_upgrade_gaps_lexical_no_match_keeps_severity():
    g = _gap("an unrelated minor concern about formatting")
    result = upgrade_gaps(
        {"Performance": [g]},
        central_claim="senders deliver three times the throughput",
        problem_statement="legacy executors are slow",
    )
    assert result["Performance"][0].severity == "significant"


def test_upgrade_gaps_lexical_match_upgrades_to_critical():
    g = _gap("benchmark methodology problem invalidates throughput results")
    result = upgrade_gaps(
        {"Performance": [g]},
        central_claim="benchmark methodology problem invalidates throughput claim",
        problem_statement="",
    )
    assert result["Performance"][0].severity == "critical"


def test_upgrade_gaps_embedder_match_upgrades_to_critical():
    g = _gap("paraphrase of thesis")
    parallel = [1.0, 0.0, 0.0]
    embedder = _StubEmbedder({
        "the thesis text here": parallel,
        "paraphrase of thesis": parallel,
    })
    result = upgrade_gaps(
        {"Performance": [g]},
        central_claim="the thesis text here",
        problem_statement="",
        embedder=embedder,
    )
    assert result["Performance"][0].severity == "critical"


def test_upgrade_gaps_embedder_orthogonal_does_not_upgrade():
    g = _gap("xyz")  # 3 chars (odd) -> stub default [0,1,0]
    embedder = _StubEmbedder({
        "abcdef": [1.0, 0.0, 0.0],  # 6 chars (even) -> overridden, but key matches
    })
    result = upgrade_gaps(
        {"Performance": [g]},
        central_claim="abcdef",
        problem_statement="",
        embedder=embedder,
    )
    assert result["Performance"][0].severity == "significant"


def test_upgrade_gaps_falls_back_when_embedder_raises():
    g = _gap("benchmark methodology problem invalidates throughput results")
    result = upgrade_gaps(
        {"Performance": [g]},
        central_claim="benchmark methodology problem invalidates throughput claim",
        problem_statement="",
        embedder=_BrokenEmbedder(),
    )
    assert result["Performance"][0].severity == "critical"


def test_upgrade_gaps_does_not_touch_already_critical():
    g = _gap("paraphrase of thesis", severity="critical")
    result = upgrade_gaps(
        {"Performance": [g]},
        central_claim="cake recipes",
        problem_statement="",
    )
    assert result["Performance"][0].severity == "critical"


# -- dedupe_findings --------------------------------------------------------


def test_dedupe_findings_drops_paraphrase_by_embedding():
    existing = [_finding("X requires language change",
                         "X needs a core language change at compile time")]
    candidate = _finding("X requires core language change",
                         "X needs language change at the compile site")
    other = _finding("Y mishandles allocator", "Allocator propagation drops state")

    parallel = [1.0, 0.0, 0.0]
    perpendicular = [0.0, 1.0, 0.0]
    embedder = _StubEmbedder({
        f"{existing[0].title} {existing[0].explanation}": parallel,
        f"{candidate.title} {candidate.explanation}": parallel,
        f"{other.title} {other.explanation}": perpendicular,
    })

    kept = dedupe_findings(existing, [candidate, other], embedder=embedder)
    assert [f.title for f in kept] == [other.title]


def test_dedupe_findings_keeps_distinct_findings_via_lexical_fallback():
    existing = [_finding("X requires language change")]
    candidate = _finding("Y mishandles allocator")
    kept = dedupe_findings(existing, [candidate])
    assert kept == [candidate]


def test_dedupe_findings_substring_match_via_lexical_fallback():
    existing = [_finding("X requires language change")]
    candidate = _finding("X requires language change too", "x")
    kept = dedupe_findings(existing, [candidate])
    assert kept == []


def test_dedupe_findings_empty_inputs():
    f = _finding("only candidate")
    assert dedupe_findings([], [f]) == [f]
    assert dedupe_findings([f], []) == []


def test_dedupe_findings_broken_embedder_falls_back_to_lexical():
    existing = [_finding("X requires language change")]
    candidate = _finding("Y mishandles allocator")
    kept = dedupe_findings(existing, [candidate], embedder=_BrokenEmbedder())
    assert kept == [candidate]


# -- _cosine sanity ---------------------------------------------------------


def test_cosine_handles_zero_norm():
    from assay.harness import _cosine

    assert _cosine(np.zeros(3), np.array([1.0, 0.0, 0.0])) == 0.0
    assert _cosine(np.array([1.0, 0.0]), np.array([1.0, 0.0])) == pytest.approx(1.0)
