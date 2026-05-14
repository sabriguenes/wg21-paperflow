#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Embedding model singleton and dedup-shadow merge proposals.

This module owns the ``sentence-transformers`` model used by both the
dedup shadow (observational merge proposals) and the Step 8/9 triage
helpers in :mod:`dissect.triage`. The shadow is observational only --
it stores proposed merges on ``PipelineState`` and renders them into
the trace, but never applies them. Triage is load-bearing -- Step 8
Verify and Step 9 Load-Bearing depend on it to bound their per-LLM
inputs.

Single-process only: ``SentenceTransformer.encode`` is not thread-safe,
so the module-level singleton is intentionally not shared across
threads. The first call downloads the model (~120MB) into the local
Hugging Face cache. Every subsequent call is fully offline.

Set ``HF_HUB_OFFLINE=1`` before importing ``sentence_transformers`` as a
belt-and-suspenders guarantee that no network call is ever attempted.

The clustering primitive is ``util.community_detection`` (centroid
radius), not connected components over pairwise cosine. Centroid radius
avoids the "single linkage" chaining failure where unrelated items get
glued together through intermediate friends-of-friends pairs.
"""

from __future__ import annotations

import logging

from sentence_transformers import SentenceTransformer, util

logger = logging.getLogger(__name__)

_SHADOW_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_SHADOW_COSINE_THRESHOLD = 0.90
_SHADOW_MIN_COMMUNITY_SIZE = 2

_MODEL: SentenceTransformer | None = None  # cached singleton (per process)


def _load_model() -> SentenceTransformer:
    """Load and cache the model; offline-first, falls back to download."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        _MODEL = SentenceTransformer(_SHADOW_MODEL_NAME, local_files_only=True)
    except (OSError, ValueError):
        logger.info(
            "Downloading %s to local HF cache (first run only).",
            _SHADOW_MODEL_NAME,
        )
        _MODEL = SentenceTransformer(_SHADOW_MODEL_NAME)
    return _MODEL


def shadow_groups(texts: list[str]) -> list[list[int]]:
    """Embedding-based merge proposals via centroid-radius clustering.

    Returns a list of index groups proposed as semantic duplicates. Each
    inner list contains indices into ``texts`` that the model considers
    near-duplicates. Items absent from every returned group are not
    proposed for merging.

    Returns ``[]`` for empty input or when no clusters exceed the
    threshold.
    """
    if not texts:
        return []
    model = _load_model()
    embeddings = model.encode(
        texts,
        convert_to_tensor=True,
        show_progress_bar=False,
    )
    raw = util.community_detection(
        embeddings,
        threshold=_SHADOW_COSINE_THRESHOLD,
        min_community_size=_SHADOW_MIN_COMMUNITY_SIZE,
    )
    return [[int(i) for i in group] for group in raw]


def prefetch_cli() -> int:
    """CLI entry point: warm the Hugging Face cache by loading the model.

    Run once on a fresh machine or in CI to stage the ~120MB download
    deterministically. Future dissect runs then never touch the network.
    Returns 0 on success.
    """
    print(
        f"Loading {_SHADOW_MODEL_NAME} "
        "(downloads to HF cache if not already present)..."
    )
    _load_model()
    print("Model cached. Future dissect runs will use it offline.")
    return 0
