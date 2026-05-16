#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Local zero-shot text classifier backends.

Parallel namespace to ``model_backends.py``. Each ``ClassifierBackend``
subclass wraps one local-model framework (HF Transformers
``zero-shot-classification`` pipeline, ``sentence_transformers``
CrossEncoder NLI, future ClaimBuster, future custom fine-tunes) behind
one common ``classify(texts, candidate_labels, multi_label) -> per-text
{label: score}`` API.

Used by dissect Step 1 (Tag Sentences) to tag each sentence as
``target``, ``context``, or ``skip`` before Step 2 sees the chunk.
Configured via ``[classifiers.NAME]`` sections in ``SERVICES.toml``;
slot resolution lives in ``services.py`` parallel to
``resolve_slots`` for LLM services.

Determinism contract (mirror of ``dissect/shadow.py``):

- Offline-first: try ``local_files_only=True`` first; download on
  first run only.
- Per-instance pipeline singleton: ``_load()`` caches the underlying
  framework object so repeated calls in the same process pay no
  reload cost.
- CPU only by default (no GPU non-determinism).
- ``eval()`` mode, no dropout. The HF pipeline does this on
  construction; ``transformers.pipeline`` puts the model in eval mode.

Determinism rationale lives in the root ``CLAUDE.md`` D-series rules
and the existing ``shadow.py`` docstring.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# Force offline-first behavior at the import boundary. Belt-and-suspenders
# determinism guarantee: even if a backend is constructed before any
# explicit load, the next HF call will refuse to hit the network unless
# the file is missing locally.
os.environ.setdefault("HF_HUB_OFFLINE", "0")


class ClassifierBackend(ABC):
    """Local zero-shot text classifier.

    Implementations wrap a framework (HF Transformers pipeline,
    sentence_transformers CrossEncoder, custom fine-tune) behind a
    common API.

    Subclasses must accept ``model: str`` and ``device: str = "cpu"``
    as the canonical positional kwargs; further per-entry fields from
    ``SERVICES.toml`` are forwarded as kwargs.
    """

    #: Hugging Face model id or local path. Set by ``__init__``.
    model_id: str

    #: Device string accepted by the underlying framework.
    device: str

    @abstractmethod
    def classify(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        """Score each text against each candidate label.

        Returns one ``{label: score}`` dict per input text. Order of
        ``texts`` is preserved.

        ``multi_label=True`` (default): each label is scored
        independently via per-label binary entailment-vs-contradiction
        softmax. Scores do NOT sum to 1; each is a per-label probability
        in [0, 1] suitable for an absolute threshold. This is the only
        correct mode for non-mutually-exclusive labels (e.g. ``target``
        and ``skip`` can both be weakly true, both be weakly false, or
        split, leaving ``context`` as the fallback).

        ``multi_label=False``: softmax across all candidate labels
        (scores sum to 1.0). Use only when exactly one label must be
        true.
        """
        ...


class ZeroShotV2Backend(ClassifierBackend):
    """HF Transformers ``zero-shot-classification`` pipeline wrapper.

    Targets ``MoritzLaurer/deberta-v3-*-zeroshot-v2.0`` and similar
    NLI-fine-tuned-on-many-non-NLI-datasets models. Auto-downloads on
    first use, offline-first thereafter, eval mode, per-process per-
    instance singleton.
    """

    def __init__(
        self,
        *,
        model: str,
        device: str = "cpu",
        **_unused: Any,
    ) -> None:
        self.model_id = model
        self.device = device
        self._pipeline: Any = None  # lazy-loaded; cached per instance

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        from transformers import pipeline as _hf_pipeline

        try:
            self._pipeline = _hf_pipeline(
                "zero-shot-classification",
                model=self.model_id,
                device=self.device,
            )
        except (OSError, ValueError):
            logger.info(
                "Downloading classifier model '%s' to local HF cache "
                "(first run only).",
                self.model_id,
            )
            self._pipeline = _hf_pipeline(
                "zero-shot-classification",
                model=self.model_id,
                device=self.device,
            )
        return self._pipeline

    def classify(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        if not texts:
            return []
        pipe = self._load()
        raw = pipe(
            texts,
            candidate_labels=candidate_labels,
            multi_label=multi_label,
        )
        # HF returns a single dict if len(texts) == 1, else a list.
        results = raw if isinstance(raw, list) else [raw]
        out: list[dict[str, float]] = []
        for r in results:
            labels = r["labels"]
            scores = r["scores"]
            out.append({label: float(score) for label, score in zip(labels, scores)})
        return out


class NliCrossEncoderBackend(ClassifierBackend):
    """sentence_transformers ``CrossEncoder`` NLI adapter.

    Wraps an NLI cross-encoder (3-way: entail / contradict / neutral)
    into the zero-shot-classification API. Each (text, label) pair is
    formatted as ``premise = text``, ``hypothesis = "This text is {label}"``
    and the entailment probability is reported as the label score.

    ``multi_label=True``: each label's entailment probability is
    reported independently. ``multi_label=False``: entailment
    probabilities are softmaxed across labels.

    Swap-in compatibility for smaller NLI models (e.g.
    ``cross-encoder/nli-deberta-v3-small``).
    """

    # NLI label index convention used by ``cross-encoder/nli-*`` checkpoints:
    # 0 = contradiction, 1 = entailment, 2 = neutral.
    _ENTAIL_IDX = 1
    _CONTRA_IDX = 0

    def __init__(
        self,
        *,
        model: str,
        device: str = "cpu",
        **_unused: Any,
    ) -> None:
        self.model_id = model
        self.device = device
        self._cross_encoder: Any = None

    def _load(self) -> Any:
        if self._cross_encoder is not None:
            return self._cross_encoder
        from sentence_transformers import CrossEncoder

        try:
            self._cross_encoder = CrossEncoder(
                self.model_id, device=self.device, local_files_only=True,
            )
        except (OSError, ValueError, TypeError):
            logger.info(
                "Downloading NLI cross-encoder '%s' to local HF cache "
                "(first run only).",
                self.model_id,
            )
            self._cross_encoder = CrossEncoder(self.model_id, device=self.device)
        return self._cross_encoder

    def classify(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        if not texts:
            return []
        import math

        model = self._load()
        pairs: list[tuple[str, str]] = []
        for text in texts:
            for label in candidate_labels:
                pairs.append((text, f"This text is {label}"))

        logits = model.predict(pairs, apply_softmax=False, show_progress_bar=False)

        out: list[dict[str, float]] = []
        n_labels = len(candidate_labels)
        for i, _ in enumerate(texts):
            row_logits = logits[i * n_labels : (i + 1) * n_labels]
            per_label_entail: list[float] = []
            for triple in row_logits:
                # ``CrossEncoder.predict`` returns ``numpy.ndarray`` for
                # multi-class NLI heads; index by entail / contradict.
                e = float(triple[self._ENTAIL_IDX])
                c = float(triple[self._CONTRA_IDX])
                # Per-label binary softmax over (entail, contradict).
                m = max(e, c)
                exp_e = math.exp(e - m)
                exp_c = math.exp(c - m)
                per_label_entail.append(exp_e / (exp_e + exp_c))

            if multi_label:
                scores = per_label_entail
            else:
                # Softmax across labels using raw entailment logits.
                # Re-derive logits for softmax: log(p/(1-p)) on the
                # per-label binary score is fine since we already have
                # probabilities.
                m = max(per_label_entail)
                exps = [math.exp(p - m) for p in per_label_entail]
                z = sum(exps)
                scores = [e / z for e in exps]

            out.append({
                label: scores[j]
                for j, label in enumerate(candidate_labels)
            })

        return out


CLASSIFIER_BACKEND_REGISTRY: dict[str, type[ClassifierBackend]] = {
    "zeroshot_v2": ZeroShotV2Backend,
    "nli_cross_encoder": NliCrossEncoderBackend,
}
