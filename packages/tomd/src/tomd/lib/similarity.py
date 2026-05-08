"""String similarity algorithms for fuzzy matching.

Two independent algorithms with per-algorithm thresholds:
  1. SequenceMatcher - character-level (difflib, stdlib)
  2. Jaccard - word-level set overlap

A 200-character circuit breaker protects against expensive
comparisons on paragraph-length strings.

Format-agnostic: no domain-specific constants here. Callers
provide their own canonical sets and thresholds.
"""

from __future__ import annotations

from collections.abc import Iterable
from difflib import SequenceMatcher as _SM

_MAX_COMPARE_LENGTH = 200

_SEQUENCE_THRESHOLD = 0.75
_JACCARD_THRESHOLD = 0.65

_LABEL_MATCH_THRESHOLD = 0.82


def _sequence_similarity(a: str, b: str) -> float:
    """Character-level similarity using difflib.SequenceMatcher.

    Returns 0.0-1.0. Caller is responsible for the length guard.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return _SM(None, a, b).ratio()


def _jaccard_similarity(a: str, b: str) -> float:
    """Word-level similarity using set intersection/union.

    Returns 0.0-1.0. Caller is responsible for the length guard.
    Systematically scores lower than SequenceMatcher on short strings
    with one extra word.
    """
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    intersection = sa & sb
    union = sa | sb
    return len(intersection) / len(union)


def _symmetric_similarity(a: str, b: str) -> float:
    """max(SM(a,b), SM(b,a)) -- neutralizes argument-order asymmetry.

    SM's greedy leftmost-longest-block matching can produce different
    ratios depending on argument order.
    """
    return max(_sequence_similarity(a, b), _sequence_similarity(b, a))


def fuzzy_match_label(candidate: str, known: Iterable[str],
                      threshold: float = _LABEL_MATCH_THRESHOLD,
                      ) -> str | None:
    """Return the best-matching known label, or ``None`` if below *threshold*.

    Compares *candidate* against every string in *known* using symmetric
    SequenceMatcher similarity. Returns the highest-scoring known string
    whose score meets *threshold*, or ``None``.

    Callers provide the canonical set; this function carries no
    domain-specific knowledge.
    """
    candidate = candidate.lower().strip()
    if not candidate:
        return None
    if len(candidate) > _MAX_COMPARE_LENGTH:
        return None
    best_score = 0.0
    best_label: str | None = None
    for label in known:
        score = _symmetric_similarity(candidate, label)
        if score >= threshold and score > best_score:
            best_score = score
            best_label = label
    return best_label


def similar(a: str, b: str) -> bool:
    """True if EITHER algorithm scores above its calibrated threshold.

    The per-string check is lenient because the caller (TOC detection)
    provides a second guard via the 3+ consecutive run requirement.
    Identical strings short-circuit to True regardless of length; the
    200-char gate only protects against expensive fuzzy-compare work.
    """
    if a == b:
        return True
    if len(a) > _MAX_COMPARE_LENGTH or len(b) > _MAX_COMPARE_LENGTH:
        return False
    if _sequence_similarity(a, b) >= _SEQUENCE_THRESHOLD:
        return True
    if _jaccard_similarity(a, b) >= _JACCARD_THRESHOLD:
        return True
    return False
