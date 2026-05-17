#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Pipeline-owned transformer inference layer.

Callers (dissect, advocatus, future Step 9 NLI gates) pass collections
of work to a :class:`TransformerBackend` subclass; the backend slices
the collection into batches sized by the active
:class:`TransformerProvider` and runs inference on the device, dtype,
and batch size the provider declared.

Two provider modes:

- ``mode = "auto"`` scans the host once at construction. Picks
  ``cuda > mps > cpu`` for the device; ``bf16`` on Ampere+, ``fp16``
  on older CUDA, ``fp32`` on MPS (fp16 routes to the Apple Neural
  Engine and returns NaNs on macOS 14+ -- pytorch#110975) and CPU;
  batch sizes ``cuda=64``, ``mps=32``, ``cpu=16`` capped by
  ``max_batch_size``. Suited to dev machines.
- ``mode = "explicit"`` uses declared TOML values verbatim. Missing
  required keys are a load-time error so cloud misconfiguration fails
  fast. Suited to provisioned cloud / production.

Three documented PyTorch hazards justify the ``executor_workers = 1``
default for async paths:

- MPS Metal-cache races (pytorch#167541).
- cuDNN v8 plan-cache races (pytorch#103793).
- ``torch.set_default_device`` is not thread-local (pytorch#115917);
  executor functions must call ``torch.cuda.set_device(idx)`` instead.

A separate HF zero-shot pipeline batching gotcha applies to the
``HFZeroShotBackend`` path: the pipeline's ``is_last`` flag flushes
its accumulator at every sequence boundary, so passing ``list[str]``
to ``pipeline(...)`` does not batch across texts
(huggingface/transformers#24005). Feed an iterable instead. The
``CrossEncoderBackend`` path is unaffected.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


DeviceStr = Literal["cuda", "mps", "cpu"]
DtypeStr = Literal["bf16", "fp16", "fp32"]
ProviderMode = Literal["auto", "explicit"]


_AUTO_BATCH_SIZE: dict[str, int] = {"cuda": 64, "mps": 32, "cpu": 16}
_DEFAULT_MAX_BATCH_SIZE = 64
_DEFAULT_MAX_LENGTH = 192
_DEFAULT_EXECUTOR_WORKERS = 1


@dataclass(frozen=True)
class TransformerProvider:
    """Runtime configuration for a transformer model.

    Resolved (post-auto) values live on the dataclass; callers never
    re-detect. Construct via :meth:`from_toml` from a SERVICES.toml
    ``[transformer_providers.NAME]`` entry, or via :meth:`auto` for a
    one-shot host-detected provider used by the hardcoded ``auto``
    fallback.
    """

    name: str
    mode: ProviderMode
    device: DeviceStr
    dtype: DtypeStr
    batch_size: int
    max_length: int = _DEFAULT_MAX_LENGTH
    executor_workers: int = _DEFAULT_EXECUTOR_WORKERS

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def auto(cls, name: str = "auto", *, max_batch_size: int = _DEFAULT_MAX_BATCH_SIZE,
             max_length: int = _DEFAULT_MAX_LENGTH,
             executor_workers: int = _DEFAULT_EXECUTOR_WORKERS) -> "TransformerProvider":
        """Detect the host once and return a resolved auto-mode provider.

        Used by the hardcoded ``auto`` fallback in
        :func:`pipeline.services.load_transformer_providers` so a fresh
        clone works without any TOML edits. If torch is unavailable,
        falls back safely to cpu / fp32 / batch=cpu_cap so tests and
        environments without torch still construct a usable provider.
        """
        device = _detect_device()
        dtype = _detect_dtype_for(device)
        cap = max(1, max_batch_size)
        batch_size = min(_AUTO_BATCH_SIZE[device], cap)
        return cls(
            name=name,
            mode="auto",
            device=device,
            dtype=dtype,
            batch_size=batch_size,
            max_length=max_length,
            executor_workers=max(1, executor_workers),
        )

    @classmethod
    def from_toml(cls, name: str, raw: dict[str, Any]) -> "TransformerProvider":
        """Build a provider from one ``[transformer_providers.NAME]`` entry.

        ``raw["mode"]`` selects the branch:

        - ``"auto"`` runs detection now; ``max_batch_size`` /
          ``max_length`` / ``executor_workers`` cap the detected
          values.
        - ``"explicit"`` uses the declared ``device`` / ``dtype`` /
          ``batch_size`` verbatim. Missing keys raise ``ValueError``
          at load time, not at first inference.
        """
        mode = raw.get("mode", "auto")
        if mode not in ("auto", "explicit"):
            raise ValueError(
                f"Transformer provider '{name}': mode must be 'auto' "
                f"or 'explicit', got {mode!r}."
            )

        if mode == "auto":
            return cls.auto(
                name=name,
                max_batch_size=int(raw.get("max_batch_size", _DEFAULT_MAX_BATCH_SIZE)),
                max_length=int(raw.get("max_length", _DEFAULT_MAX_LENGTH)),
                executor_workers=int(raw.get("executor_workers", _DEFAULT_EXECUTOR_WORKERS)),
            )

        # Explicit mode: validate required keys before constructing.
        missing = [k for k in ("device", "dtype", "batch_size") if k not in raw]
        if missing:
            raise ValueError(
                f"Transformer provider '{name}' (mode=explicit): "
                f"missing required key(s) {missing}. Explicit providers "
                f"must declare device, dtype, and batch_size so cloud "
                f"misconfiguration fails at load, not at first inference."
            )

        device = str(raw["device"])
        if device not in ("cuda", "mps", "cpu"):
            raise ValueError(
                f"Transformer provider '{name}': device must be 'cuda', "
                f"'mps', or 'cpu', got {device!r}."
            )

        dtype = str(raw["dtype"])
        if dtype not in ("bf16", "fp16", "fp32"):
            raise ValueError(
                f"Transformer provider '{name}': dtype must be 'bf16', "
                f"'fp16', or 'fp32', got {dtype!r}."
            )

        return cls(
            name=name,
            mode="explicit",
            device=device,  # type: ignore[arg-type]
            dtype=dtype,    # type: ignore[arg-type]
            batch_size=int(raw["batch_size"]),
            max_length=int(raw.get("max_length", _DEFAULT_MAX_LENGTH)),
            executor_workers=max(1, int(raw.get("executor_workers", _DEFAULT_EXECUTOR_WORKERS))),
        )


def _detect_device() -> DeviceStr:
    """Return the best available device, falling back to cpu safely.

    Order: cuda > mps > cpu. If torch is not importable (rare in tests
    that stub HF modules), returns ``"cpu"``.
    """
    try:
        import torch  # type: ignore[import-untyped]
    except Exception:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    try:
        if torch.backends.mps.is_available():  # type: ignore[attr-defined]
            return "mps"
    except Exception:
        pass
    return "cpu"


def _detect_dtype_for(device: DeviceStr) -> DtypeStr:
    """Map a device to its safest auto dtype.

    - ``cuda``: ``bf16`` on Ampere+ (``torch.cuda.is_bf16_supported``);
      else ``fp16``. bf16 avoids the CrossEncoder fp16 NaN-logits bug
      (UKPLab/sentence-transformers#3233) and fp16 softmax overflow.
    - ``mps``: ``fp32``. fp16 routes large matmuls to the Apple Neural
      Engine on macOS 14+ and returns NaNs (pytorch#110975); bf16 on
      M1/M2 falls back to software.
    - ``cpu``: ``fp32``.
    """
    if device == "cpu" or device == "mps":
        return "fp32"
    # cuda
    try:
        import torch  # type: ignore[import-untyped]
        if torch.cuda.is_bf16_supported():
            return "bf16"
    except Exception:
        pass
    return "fp16"


def torch_dtype_for(dtype: DtypeStr) -> Any:
    """Map a provider dtype string to ``torch.dtype``.

    Imported lazily so callers that never touch a real backend (e.g.
    pure config tests) do not need torch installed.
    """
    import torch  # type: ignore[import-untyped]
    return {
        "bf16": torch.bfloat16,
        "fp16": torch.float16,
        "fp32": torch.float32,
    }[dtype]


# ---------------------------------------------------------------------------
# Backend base
# ---------------------------------------------------------------------------


@dataclass
class _ThreadState:
    """Per-thread bookkeeping for the executor.

    ``device_set`` tracks whether ``torch.cuda.set_device`` has been
    called on the current thread for the current CUDA device. Without
    this, ``run_in_executor`` workers inherit the OS default and may
    or may not see the CUDA context we want (pytorch CUDA semantics
    are per-thread).
    """

    device_set: bool = False


class TransformerBackend:
    """Base for framework-specific transformer wrappers.

    Owns the :class:`TransformerProvider`, model id, lazy framework
    object, and the async executor. Subclasses implement framework-
    specific operations (HF zero-shot pipeline, CrossEncoder NLI,
    SentenceTransformer embeddings).

    The model is loaded lazily on first use and cached per instance.
    Per-process model deduplication is not done in v1; each backend
    instance owns its own framework object.
    """

    def __init__(
        self,
        model_id: str,
        provider: TransformerProvider | None = None,
    ) -> None:
        self.model_id = model_id
        self.provider = provider or TransformerProvider.auto()
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._thread_state = threading.local()

        if self.provider.device == "mps":
            # Unimplemented MPS ops should fall back to CPU rather than
            # raising NotImplementedError mid-inference. Setdefault
            # respects an operator-set override.
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    # ------------------------------------------------------------------
    # Thread / device setup
    # ------------------------------------------------------------------

    def _ensure_thread_setup(self) -> None:
        """Call ``torch.cuda.set_device`` once per executor thread.

        Mitigates pytorch#115917 (mode stack not thread-local) and the
        general CUDA-per-thread context requirement. No-op on non-CUDA
        devices.
        """
        if self.provider.device != "cuda":
            return
        state = getattr(self._thread_state, "state", None)
        if state is None:
            state = _ThreadState()
            self._thread_state.state = state
        if state.device_set:
            return
        try:
            import torch  # type: ignore[import-untyped]
            # Index 0 is the default; multi-GPU is out of scope for v1.
            torch.cuda.set_device(0)
            state.device_set = True
        except Exception:
            logger.warning(
                "TransformerBackend(%s): torch.cuda.set_device(0) failed; "
                "CUDA inference may run on the wrong device.",
                self.model_id,
                exc_info=True,
            )

    def _executor_handle(self) -> ThreadPoolExecutor:
        """Return the shared per-instance ThreadPoolExecutor.

        Sized at ``provider.executor_workers`` (default 1). See the
        module docstring for why 1 is the v1 default.
        """
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=max(1, self.provider.executor_workers),
                        thread_name_prefix=f"xfm-{self.model_id}",
                    )
        return self._executor

    async def _run_in_executor(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()

        def _runner() -> Any:
            self._ensure_thread_setup()
            return fn(*args, **kwargs)

        return await loop.run_in_executor(self._executor_handle(), _runner)

    # ------------------------------------------------------------------
    # Batching helper
    # ------------------------------------------------------------------

    def _batched(self, items: list[Any]) -> Iterable[list[Any]]:
        """Slice ``items`` into batches sized at ``provider.batch_size``."""
        n = max(1, self.provider.batch_size)
        for i in range(0, len(items), n):
            yield items[i : i + n]


# ---------------------------------------------------------------------------
# HF zero-shot pipeline backend
# ---------------------------------------------------------------------------


class HFZeroShotBackend(TransformerBackend):
    """Wraps ``transformers.pipeline('zero-shot-classification')``.

    Avoids the huggingface/transformers#24005 batching trap by feeding
    an iterable to the pipeline (instead of ``list[str]``), so the
    pipeline's ``is_last`` flag does not flush the accumulator at every
    sequence boundary.
    """

    def __init__(self, model_id: str, provider: TransformerProvider | None = None) -> None:
        super().__init__(model_id, provider)
        self._pipeline: Any = None

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        from transformers import pipeline as _hf_pipeline  # type: ignore[import-untyped]

        hf_device = _hf_device_string(self.provider.device)
        model_kwargs: dict[str, Any] = {}
        try:
            model_kwargs["torch_dtype"] = torch_dtype_for(self.provider.dtype)
        except Exception:
            # If torch is unavailable but the pipeline factory is stubbed
            # (tests), proceed without forcing a dtype.
            pass

        try:
            self._pipeline = _hf_pipeline(
                "zero-shot-classification",
                model=self.model_id,
                device=hf_device,
                model_kwargs=model_kwargs or None,
            )
        except TypeError:
            # Stubs in tests may not accept model_kwargs.
            self._pipeline = _hf_pipeline(
                "zero-shot-classification",
                model=self.model_id,
                device=hf_device,
            )
        return self._pipeline

    def zero_shot_classify(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        if not texts:
            return []
        pipe = self._load()

        # Feed via a generator iterable so the pipeline batches across
        # sequence boundaries instead of flushing per-sequence
        # (huggingface/transformers#24005). The generator yields one
        # text at a time; the pipeline returns one result per text in
        # the same order.
        def _stream() -> Iterable[str]:
            for t in texts:
                yield t

        results = []
        out = _inference_mode_call(
            pipe,
            _stream(),
            candidate_labels=candidate_labels,
            multi_label=multi_label,
            batch_size=self.provider.batch_size,
        )
        # The pipeline returns an iterator when given an iterable; a
        # single dict when given a single text. Tests stub the pipeline
        # with a list-returning callable, so handle all three shapes.
        if isinstance(out, dict):
            results = [out]
        else:
            results = list(out)

        normalized: list[dict[str, float]] = []
        for r in results:
            labels = r["labels"]
            scores = r["scores"]
            normalized.append({label: float(score) for label, score in zip(labels, scores)})
        return normalized

    async def zero_shot_classify_async(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        return await self._run_in_executor(
            self.zero_shot_classify, texts, candidate_labels,
            multi_label=multi_label,
        )


def _hf_device_string(device: DeviceStr) -> str | int:
    """Map our DeviceStr to what ``transformers.pipeline`` expects."""
    if device == "cuda":
        return 0  # cuda:0
    return device  # "mps" or "cpu"


# ---------------------------------------------------------------------------
# CrossEncoder NLI backend
# ---------------------------------------------------------------------------


# NLI label index convention used by ``cross-encoder/nli-*`` checkpoints:
# 0 = contradiction, 1 = entailment, 2 = neutral.
_CE_ENTAIL_IDX = 1
_CE_CONTRA_IDX = 0
_CE_NEUTRAL_IDX = 2


class CrossEncoderBackend(TransformerBackend):
    """Wraps ``sentence_transformers.CrossEncoder`` for NLI.

    Supports two operations on the same loaded model:

    - :meth:`zero_shot_classify`: per-label "This text is {label}"
      template, per-label binary softmax over (entail, contradict) for
      ``multi_label=True``; cross-label softmax for ``multi_label=False``.
      Mirrors the legacy ``NliCrossEncoderBackend.classify`` semantics
      verbatim so adapter delegation preserves Step 1 behavior.
    - :meth:`nli_pairs`: raw (premise, hypothesis) pairs in, 3-way
      softmax probabilities ``{entailment, neutral, contradiction}``
      out. Intended for future Step 9 disclaim / verify gates.
    """

    def __init__(self, model_id: str, provider: TransformerProvider | None = None) -> None:
        super().__init__(model_id, provider)
        self._cross_encoder: Any = None

    def _load(self) -> Any:
        if self._cross_encoder is not None:
            return self._cross_encoder
        from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

        ce_device = self.provider.device
        ce_kwargs: dict[str, Any] = {"device": ce_device}
        try:
            ce_kwargs["model_kwargs"] = {"torch_dtype": torch_dtype_for(self.provider.dtype)}
        except Exception:
            pass

        try:
            self._cross_encoder = CrossEncoder(
                self.model_id, local_files_only=True, **ce_kwargs,
            )
        except (OSError, ValueError, TypeError):
            logger.info(
                "Downloading NLI cross-encoder '%s' to local HF cache "
                "(first run only).",
                self.model_id,
            )
            try:
                self._cross_encoder = CrossEncoder(self.model_id, **ce_kwargs)
            except TypeError:
                # Test stubs may not accept model_kwargs.
                self._cross_encoder = CrossEncoder(self.model_id, device=ce_device)
        return self._cross_encoder

    # ------------------------------------------------------------------
    # zero_shot_classify (template-style)
    # ------------------------------------------------------------------

    def zero_shot_classify(
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
        pairs: list[tuple[str, str]] = [
            (text, f"This text is {label}")
            for text in texts for label in candidate_labels
        ]

        logits = _inference_mode_call(
            model.predict, pairs,
            apply_softmax=False, show_progress_bar=False,
            batch_size=self.provider.batch_size,
        )

        n_labels = len(candidate_labels)
        out: list[dict[str, float]] = []
        for i in range(len(texts)):
            row_logits = logits[i * n_labels : (i + 1) * n_labels]
            per_label_entail: list[float] = []
            for triple in row_logits:
                e = float(triple[_CE_ENTAIL_IDX])
                c = float(triple[_CE_CONTRA_IDX])
                m = max(e, c)
                exp_e = math.exp(e - m)
                exp_c = math.exp(c - m)
                per_label_entail.append(exp_e / (exp_e + exp_c))

            if multi_label:
                scores = per_label_entail
            else:
                m = max(per_label_entail)
                exps = [math.exp(p - m) for p in per_label_entail]
                z = sum(exps)
                scores = [e / z for e in exps]

            out.append({label: scores[j] for j, label in enumerate(candidate_labels)})
        return out

    async def zero_shot_classify_async(
        self,
        texts: list[str],
        candidate_labels: list[str],
        *,
        multi_label: bool = True,
    ) -> list[dict[str, float]]:
        return await self._run_in_executor(
            self.zero_shot_classify, texts, candidate_labels,
            multi_label=multi_label,
        )

    # ------------------------------------------------------------------
    # nli_pairs (raw premise/hypothesis pairs)
    # ------------------------------------------------------------------

    def nli_pairs(self, pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
        """Score raw (premise, hypothesis) pairs.

        Returns one dict per input pair with keys ``entailment``,
        ``neutral``, ``contradiction`` summing to 1.0 after a 3-way
        softmax. Intended for future Step 9 disclaim / verify gates.
        """
        if not pairs:
            return []
        import math

        model = self._load()
        logits = _inference_mode_call(
            model.predict, list(pairs),
            apply_softmax=False, show_progress_bar=False,
            batch_size=self.provider.batch_size,
        )

        out: list[dict[str, float]] = []
        for triple in logits:
            c = float(triple[_CE_CONTRA_IDX])
            e = float(triple[_CE_ENTAIL_IDX])
            n = float(triple[_CE_NEUTRAL_IDX])
            m = max(c, e, n)
            exps = [math.exp(x - m) for x in (e, n, c)]
            z = sum(exps)
            out.append({
                "entailment": exps[0] / z,
                "neutral": exps[1] / z,
                "contradiction": exps[2] / z,
            })
        return out

    async def nli_pairs_async(self, pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
        return await self._run_in_executor(self.nli_pairs, pairs)


# ---------------------------------------------------------------------------
# Embedding backend (skeleton; full migration is a follow-up)
# ---------------------------------------------------------------------------


class EmbeddingBackend(TransformerBackend):
    """Wraps ``sentence_transformers.SentenceTransformer`` for embeddings.

    Provided as the embedding op of the TransformerBackend family. The
    existing dissect ``shadow.py`` embedding loader is not migrated
    yet (see plan Out-of-scope); this class is the target for that
    migration.
    """

    def __init__(self, model_id: str, provider: TransformerProvider | None = None) -> None:
        super().__init__(model_id, provider)
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
        self._model = SentenceTransformer(
            self.model_id, device=self.provider.device,
        )
        return self._model

    def embed(self, texts: list[str]) -> Any:
        if not texts:
            return None
        model = self._load()
        return _inference_mode_call(
            model.encode, texts,
            batch_size=self.provider.batch_size,
            convert_to_tensor=True,
            show_progress_bar=False,
        )

    async def embed_async(self, texts: list[str]) -> Any:
        return await self._run_in_executor(self.embed, texts)


# ---------------------------------------------------------------------------
# Inference-mode helper
# ---------------------------------------------------------------------------


def _inference_mode_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn(*args, **kwargs)`` inside ``torch.inference_mode``.

    Falls back to a plain call when torch is unavailable (tests with
    stubbed HF / sentence_transformers modules). The fallback is safe
    because tests never reach a real torch forward pass.
    """
    try:
        import torch  # type: ignore[import-untyped]
        with torch.inference_mode():
            return fn(*args, **kwargs)
    except ImportError:
        return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# MPS correctness self-test
# ---------------------------------------------------------------------------


_MPS_SELFTEST_RUN = False
_MPS_SELFTEST_LOCK = threading.Lock()


def run_mps_correctness_selftest(backend: CrossEncoderBackend) -> None:
    """Verify MPS batched logits match single-sample on a fixed prompt.

    No-op unless the backend's device is ``mps``. Runs at most once
    per process. Emits ``logger.warning`` on divergence citing
    pytorch#170837 and recommending ``--provider cpu-fp32``.

    Cost: one extra forward pass per MPS process startup. Catches a
    real correctness regression silently shipping on Apple Silicon.
    """
    if backend.provider.device != "mps":
        return
    global _MPS_SELFTEST_RUN
    with _MPS_SELFTEST_LOCK:
        if _MPS_SELFTEST_RUN:
            return
        _MPS_SELFTEST_RUN = True

    try:
        single = backend.nli_pairs([
            ("The cat sat on the mat.", "An animal rested on a rug."),
        ])
        batched = backend.nli_pairs([
            ("The cat sat on the mat.", "An animal rested on a rug."),
            ("The sky is blue.", "The grass is green."),
        ])

        rtol, atol = 1e-3, 1e-3
        # Compare the first pair across the two runs.
        for key in ("entailment", "neutral", "contradiction"):
            s = single[0][key]
            b = batched[0][key]
            if abs(s - b) > atol + rtol * abs(b):
                logger.warning(
                    "MPS correctness self-test: batched vs single logits "
                    "for '%s' diverge by %.4f > tol (pytorch#170837). "
                    "Consider running with --provider cpu-fp32 until "
                    "upstream fix lands.",
                    key, abs(s - b),
                )
                return
    except Exception:
        logger.warning(
            "MPS correctness self-test raised; skipping (model %s).",
            backend.model_id, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Default ("auto") singleton helpers used by services.py
# ---------------------------------------------------------------------------


_DEFAULT_PROVIDER: TransformerProvider | None = None


def default_auto_provider() -> TransformerProvider:
    """Return the hardcoded ``auto`` provider used by the resolver.

    A fresh clone of paperflow that does not declare
    ``[transformer_providers.auto]`` in SERVICES.toml still gets a
    working auto provider via this hook. Re-detection happens once per
    process.
    """
    global _DEFAULT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        _DEFAULT_PROVIDER = TransformerProvider.auto()
    return _DEFAULT_PROVIDER


# Silence "unused import" warnings while keeping the public surface
# discoverable from ``from pipeline.transformer_backend import *``.
__all__ = [
    "CrossEncoderBackend",
    "DeviceStr",
    "DtypeStr",
    "EmbeddingBackend",
    "HFZeroShotBackend",
    "ProviderMode",
    "TransformerBackend",
    "TransformerProvider",
    "default_auto_provider",
    "run_mps_correctness_selftest",
    "torch_dtype_for",
]

# Suppress unused-import F401 for replace/field that may help future
# subclasses without imports cluttering the module top.
_ = (replace, field)
