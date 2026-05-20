#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Local zero-shot text classifier backends.

These classes are thin adapters over :mod:`pipeline.transformer_backend`.
They preserve the original ``ClassifierBackend.classify(...)`` API so
existing classifier consumers keep working without edits.

Two adapters are registered:

- :class:`ZeroShotV2Backend` -> :class:`HFZeroShotBackend` (the HF
  ``zero-shot-classification`` pipeline). Targets
  ``MoritzLaurer/deberta-v3-*-zeroshot-v2.0`` and similar.
- :class:`NliCrossEncoderBackend` -> :class:`CrossEncoderBackend` (the
  ``sentence_transformers`` CrossEncoder NLI head). Targets
  ``cross-encoder/nli-deberta-v3-*`` and similar.

Configuration moved from the per-classifier ``device`` field to the
``[transformer_providers.*]`` namespace in SERVICES.toml. A bare
``device=`` kwarg is accepted for backward compatibility but ignored
in favor of the active provider; see :mod:`pipeline.services` for the
resolver and override precedence.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

from pipeline.transformer_backend import (
    CrossEncoderBackend,
    HFZeroShotBackend,
    TransformerProvider,
)

logger = logging.getLogger(__name__)

# Belt-and-suspenders determinism guarantee: even if a backend is
# constructed before any explicit load, the next HF call will refuse
# to hit the network unless the file is missing locally.
os.environ.setdefault("HF_HUB_OFFLINE", "0")


class ClassifierBackend(ABC):
    """Local zero-shot text classifier.

    Implementations wrap a transformer framework behind a common
    ``classify(texts, candidate_labels, multi_label) -> per-text
    {label: score}`` API.

    Subclasses accept ``model: str`` and ``provider: TransformerProvider
    | None = None`` (defaulting to host-auto detection) plus framework
    extras via ``**kwargs``.
    """

    #: Hugging Face model id or local path. Set by ``__init__``.
    model_id: str

    #: Resolved provider. Set by ``__init__``.
    provider: TransformerProvider

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
        in [0, 1] suitable for an absolute threshold. The only correct
        mode for non-mutually-exclusive labels.

        ``multi_label=False``: softmax across all candidate labels
        (scores sum to 1.0). Use only when exactly one label must be
        true.
        """
        ...


class ZeroShotV2Backend(ClassifierBackend):
    """Adapter over :class:`HFZeroShotBackend`.

    Delegates ``classify(...)`` to ``HFZeroShotBackend.zero_shot_classify(...)``.
    The legacy ``device`` kwarg is accepted for backward compatibility
    but ignored; runtime configuration comes from the active
    :class:`TransformerProvider`.
    """

    def __init__(
        self,
        *,
        model: str,
        provider: TransformerProvider | None = None,
        device: str | None = None,  # legacy; ignored
        **_unused: Any,
    ) -> None:
        if device is not None:
            logger.debug(
                "ZeroShotV2Backend: legacy device=%r kwarg ignored; "
                "configure via [transformer_providers.*] in SERVICES.toml.",
                device,
            )
        self.model_id = model
        self.provider = provider or TransformerProvider.auto()
        self._backend = HFZeroShotBackend(model, self.provider)

    @property
    def device(self) -> str:
        """Backwards-compatible read-only view of the resolved device."""
        return self.provider.device

    def classify(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        return self._backend.zero_shot_classify(
            texts, candidate_labels, multi_label=multi_label,
        )


class NliCrossEncoderBackend(ClassifierBackend):
    """Adapter over :class:`CrossEncoderBackend`.

    Delegates ``classify(...)`` to the template-style zero-shot path
    of ``CrossEncoderBackend``. The cross-encoder's ``nli_pairs(...)``
    method is also available via the underlying backend for future
    Step 9 disclaim / verify gates -- expose it explicitly if needed.
    """

    def __init__(
        self,
        *,
        model: str,
        provider: TransformerProvider | None = None,
        device: str | None = None,  # legacy; ignored
        **_unused: Any,
    ) -> None:
        if device is not None:
            logger.debug(
                "NliCrossEncoderBackend: legacy device=%r kwarg ignored; "
                "configure via [transformer_providers.*] in SERVICES.toml.",
                device,
            )
        self.model_id = model
        self.provider = provider or TransformerProvider.auto()
        self._backend = CrossEncoderBackend(model, self.provider)

    @property
    def device(self) -> str:
        return self.provider.device

    def classify(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        return self._backend.zero_shot_classify(
            texts, candidate_labels, multi_label=multi_label,
        )


CLASSIFIER_BACKEND_REGISTRY: dict[str, type[ClassifierBackend]] = {
    "zeroshot_v2": ZeroShotV2Backend,
    "nli_cross_encoder": NliCrossEncoderBackend,
}
