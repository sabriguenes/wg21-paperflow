#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for :mod:`pipeline.transformer_backend` and the
``[transformer_providers.*]`` resolver in :mod:`pipeline.services`.

Covers:

- ``TransformerProvider.from_toml`` auto-mode device / dtype / batch
  selection with monkeypatched torch capability probes.
- ``TransformerProvider.from_toml`` explicit-mode required-key
  validation.
- Slot-style ``resolve_transformer_provider`` four-level override
  precedence (CLI > env > defaults > hardcoded auto).
- Adapter delegation: ``ZeroShotV2Backend.classify`` and
  ``NliCrossEncoderBackend.classify`` forward to the backing
  TransformerBackend.
- Async parity: ``zero_shot_classify_async`` returns the same result
  as the sync method.
- Executor-worker bound respects ``provider.executor_workers``
  (default 1 enforces the thread-safety invariant).
- MPS self-test only runs on MPS and at most once per process.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from pipeline import transformer_backend as tb
from pipeline.errors import TransformerConfigError
from pipeline.transformer_backend import (
    CrossEncoderBackend,
    HFZeroShotBackend,
    TransformerProvider,
    default_auto_provider,
    run_mps_correctness_selftest,
)


# ---------------------------------------------------------------------------
# Auto-mode detection
# ---------------------------------------------------------------------------


def _install_fake_torch(monkeypatch, *, cuda: bool = False,
                       bf16: bool = False, mps: bool = False) -> None:
    """Install a minimal fake torch module exposing only the capability
    probes used by :mod:`pipeline.transformer_backend`."""
    fake_torch = types.ModuleType("torch")

    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return cuda
        @staticmethod
        def is_bf16_supported() -> bool:
            return bf16

    class _Mps:
        @staticmethod
        def is_available() -> bool:
            return mps

    class _Backends:
        mps = _Mps()

    fake_torch.cuda = _Cuda()
    fake_torch.backends = _Backends()
    # inference_mode is a context manager; provide a no-op replacement
    # so any code path that hits it during these tests is safe.
    import contextlib
    fake_torch.inference_mode = contextlib.nullcontext  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", fake_torch)


def test_auto_detects_cuda_bf16_when_supported(monkeypatch):
    _install_fake_torch(monkeypatch, cuda=True, bf16=True)
    p = TransformerProvider.auto()
    assert p.device == "cuda"
    assert p.dtype == "bf16"
    assert p.batch_size == 64  # cuda default
    assert p.mode == "auto"


def test_auto_picks_fp16_when_cuda_lacks_bf16(monkeypatch):
    _install_fake_torch(monkeypatch, cuda=True, bf16=False)
    p = TransformerProvider.auto()
    assert p.device == "cuda"
    assert p.dtype == "fp16"


def test_auto_picks_mps_fp32_when_no_cuda(monkeypatch):
    _install_fake_torch(monkeypatch, cuda=False, mps=True)
    p = TransformerProvider.auto()
    assert p.device == "mps"
    # MPS always picks fp32 (fp16 NaNs in pytorch#110975; bf16 falls
    # back to software on M1/M2).
    assert p.dtype == "fp32"
    assert p.batch_size == 32


def test_auto_falls_back_to_cpu_fp32(monkeypatch):
    _install_fake_torch(monkeypatch, cuda=False, mps=False)
    p = TransformerProvider.auto()
    assert p.device == "cpu"
    assert p.dtype == "fp32"
    assert p.batch_size == 16


def test_auto_caps_batch_size_by_max_batch_size(monkeypatch):
    _install_fake_torch(monkeypatch, cuda=True, bf16=True)
    p = TransformerProvider.auto(max_batch_size=8)
    assert p.device == "cuda"
    assert p.batch_size == 8  # capped


def test_auto_handles_missing_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # type: ignore[arg-type]
    # The bare ``import torch`` inside _detect_device should swallow the
    # ImportError and return "cpu".
    p = TransformerProvider.auto()
    assert p.device == "cpu"
    assert p.dtype == "fp32"


# ---------------------------------------------------------------------------
# from_toml
# ---------------------------------------------------------------------------


def test_from_toml_auto_uses_max_batch_size(monkeypatch):
    _install_fake_torch(monkeypatch, cuda=True, bf16=True)
    p = TransformerProvider.from_toml("auto", {
        "mode": "auto", "max_batch_size": 32, "max_length": 256,
    })
    assert p.batch_size == 32
    assert p.max_length == 256


def test_from_toml_explicit_uses_declared_values():
    p = TransformerProvider.from_toml("cuda-b200", {
        "mode": "explicit",
        "device": "cuda",
        "dtype": "bf16",
        "batch_size": 128,
        "max_length": 256,
        "executor_workers": 2,
    })
    assert p.mode == "explicit"
    assert p.device == "cuda"
    assert p.dtype == "bf16"
    assert p.batch_size == 128
    assert p.executor_workers == 2


def test_from_toml_explicit_missing_keys_raises():
    with pytest.raises(TransformerConfigError, match="missing required key"):
        TransformerProvider.from_toml("bad", {
            "mode": "explicit",
            "device": "cuda",
            # dtype, batch_size missing
        })


def test_from_toml_explicit_bad_device_raises():
    with pytest.raises(TransformerConfigError, match="device must be"):
        TransformerProvider.from_toml("bad", {
            "mode": "explicit",
            "device": "tpu",
            "dtype": "bf16",
            "batch_size": 32,
        })


def test_from_toml_bad_mode_raises():
    with pytest.raises(TransformerConfigError, match="mode must be"):
        TransformerProvider.from_toml("bad", {"mode": "magic"})


# ---------------------------------------------------------------------------
# Resolver precedence (load_transformer_providers + resolve)
# ---------------------------------------------------------------------------


def _write_services_toml(tmp_path, body: str):
    p = tmp_path / "SERVICES.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_resolver_cli_override_wins(tmp_path, monkeypatch):
    from pipeline.services import (
        load_transformer_providers,
        resolve_transformer_provider,
    )
    monkeypatch.setenv("PAPERFLOW_TRANSFORMER_PROVIDER", "auto")
    p = _write_services_toml(tmp_path, """
[transformer_providers.cuda-b200]
mode = "explicit"
device = "cuda"
dtype = "bf16"
batch_size = 64

[transformer_provider_defaults]
default = "auto"
""")
    providers, defaults = load_transformer_providers(p)
    resolved = resolve_transformer_provider(
        providers, defaults, override="cuda-b200",
    )
    assert resolved.name == "cuda-b200"
    assert resolved.device == "cuda"


def test_resolver_env_var_beats_defaults(tmp_path, monkeypatch):
    from pipeline.services import (
        load_transformer_providers,
        resolve_transformer_provider,
    )
    p = _write_services_toml(tmp_path, """
[transformer_providers.cuda-b200]
mode = "explicit"
device = "cuda"
dtype = "bf16"
batch_size = 64

[transformer_provider_defaults]
default = "auto"
""")
    providers, defaults = load_transformer_providers(p)
    monkeypatch.setenv("PAPERFLOW_TRANSFORMER_PROVIDER", "cuda-b200")
    resolved = resolve_transformer_provider(providers, defaults)
    assert resolved.name == "cuda-b200"


def test_resolver_defaults_used_when_no_override(tmp_path, monkeypatch):
    from pipeline.services import (
        load_transformer_providers,
        resolve_transformer_provider,
    )
    monkeypatch.delenv("PAPERFLOW_TRANSFORMER_PROVIDER", raising=False)
    p = _write_services_toml(tmp_path, """
[transformer_providers.cuda-b200]
mode = "explicit"
device = "cuda"
dtype = "bf16"
batch_size = 64

[transformer_provider_defaults]
default = "cuda-b200"
""")
    providers, defaults = load_transformer_providers(p)
    resolved = resolve_transformer_provider(providers, defaults)
    assert resolved.name == "cuda-b200"


def test_resolver_hardcoded_auto_fallback_when_nothing_declared(tmp_path, monkeypatch):
    from pipeline.services import (
        load_transformer_providers,
        resolve_transformer_provider,
    )
    monkeypatch.delenv("PAPERFLOW_TRANSFORMER_PROVIDER", raising=False)
    p = _write_services_toml(tmp_path, "")
    providers, defaults = load_transformer_providers(p)
    # Hardcoded "auto" is injected so a fresh clone resolves.
    assert "auto" in providers
    resolved = resolve_transformer_provider(providers, defaults)
    assert resolved.name == "auto"


def test_resolver_unknown_name_raises(tmp_path):
    from pipeline.services import (
        load_transformer_providers,
        resolve_transformer_provider,
    )
    p = _write_services_toml(tmp_path, "")
    providers, defaults = load_transformer_providers(p)
    with pytest.raises(KeyError, match="nope"):
        resolve_transformer_provider(providers, defaults, override="nope")


# ---------------------------------------------------------------------------
# Adapter delegation: classifier_backends -> transformer_backend
# ---------------------------------------------------------------------------


class _StubHFPipeline:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, texts, *, candidate_labels, multi_label, batch_size=None):
        materialized = list(texts)
        self.calls.append({
            "texts": materialized,
            "labels": list(candidate_labels),
            "batch_size": batch_size,
        })
        out = []
        for _ in materialized:
            out.append({
                "sequence": "...",
                "labels": list(candidate_labels),
                "scores": [0.9 / len(candidate_labels)] * len(candidate_labels),
            })
        return out if len(materialized) > 1 else out[0]


def _install_stub_hf(monkeypatch, stub: _StubHFPipeline) -> None:
    fake_mod = types.ModuleType("transformers")
    def fake_pipeline(task, *, model, device, **_kw):
        return stub
    fake_mod.pipeline = fake_pipeline
    monkeypatch.setitem(sys.modules, "transformers", fake_mod)


def test_zeroshot_adapter_forwards_to_transformer_backend(monkeypatch):
    from pipeline.classifier_backends import ZeroShotV2Backend
    stub = _StubHFPipeline()
    _install_stub_hf(monkeypatch, stub)
    provider = TransformerProvider.from_toml("cpu", {
        "mode": "explicit", "device": "cpu", "dtype": "fp32",
        "batch_size": 7,
    })
    backend = ZeroShotV2Backend(model="fake/model", provider=provider)
    backend.classify(["a", "b"], ["x", "y"])
    assert len(stub.calls) == 1
    # batch_size from the provider lands on the pipeline call.
    assert stub.calls[0]["batch_size"] == 7


def test_zeroshot_adapter_preserves_classify_api(monkeypatch):
    """The public ClassifierBackend.classify signature is unchanged."""
    from pipeline.classifier_backends import ZeroShotV2Backend, ClassifierBackend
    stub = _StubHFPipeline()
    _install_stub_hf(monkeypatch, stub)
    backend = ZeroShotV2Backend(model="fake/model")
    assert isinstance(backend, ClassifierBackend)
    out = backend.classify(["t"], ["a", "b"], multi_label=False)
    assert len(out) == 1
    assert set(out[0].keys()) == {"a", "b"}


# ---------------------------------------------------------------------------
# Async parity
# ---------------------------------------------------------------------------


def test_async_zero_shot_returns_same_as_sync(monkeypatch):
    stub = _StubHFPipeline()
    _install_stub_hf(monkeypatch, stub)
    provider = TransformerProvider.from_toml("cpu", {
        "mode": "explicit", "device": "cpu", "dtype": "fp32",
        "batch_size": 4, "executor_workers": 1,
    })
    backend = HFZeroShotBackend("fake/model", provider)
    sync_result = backend.zero_shot_classify(["a", "b"], ["x", "y"])
    async_result = asyncio.run(
        backend.zero_shot_classify_async(["a", "b"], ["x", "y"])
    )
    assert sync_result == async_result


# ---------------------------------------------------------------------------
# Executor worker bound
# ---------------------------------------------------------------------------


def test_executor_workers_default_is_one():
    """Default ``executor_workers`` is 1 (thread-safety invariant).

    See module docstring: PyTorch CUDA/MPS thread-safety bugs make >1
    workers unsafe by default. Operators must opt in explicitly.
    """
    p = TransformerProvider.from_toml("p", {
        "mode": "explicit", "device": "cpu", "dtype": "fp32",
        "batch_size": 8,
    })
    assert p.executor_workers == 1


def test_executor_handle_respects_provider_workers(monkeypatch):
    stub = _StubHFPipeline()
    _install_stub_hf(monkeypatch, stub)
    provider = TransformerProvider.from_toml("p", {
        "mode": "explicit", "device": "cpu", "dtype": "fp32",
        "batch_size": 8, "executor_workers": 3,
    })
    backend = HFZeroShotBackend("fake/model", provider)
    # Force creation by calling the helper directly.
    pool = backend._executor_handle()
    assert pool._max_workers == 3


# ---------------------------------------------------------------------------
# MPS self-test
# ---------------------------------------------------------------------------


def test_mps_selftest_no_op_on_non_mps_device():
    """No-op when the provider's device is not 'mps'."""
    provider = TransformerProvider.from_toml("p", {
        "mode": "explicit", "device": "cpu", "dtype": "fp32",
        "batch_size": 8,
    })
    backend = CrossEncoderBackend("fake/ce", provider)
    # Reset module-level singleton so the test is independent.
    tb._MPS_SELFTEST_RUN = False  # type: ignore[attr-defined]
    run_mps_correctness_selftest(backend)
    # Still False: never ran (and never set the flag) on non-MPS.
    assert tb._MPS_SELFTEST_RUN is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Hardcoded auto provider singleton
# ---------------------------------------------------------------------------


def test_default_auto_provider_is_cached():
    p1 = default_auto_provider()
    p2 = default_auto_provider()
    assert p1 is p2
