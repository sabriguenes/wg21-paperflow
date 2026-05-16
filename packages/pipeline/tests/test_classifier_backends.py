#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for ``pipeline.classifier_backends``.

Uses a fake backend implementing the ``ClassifierBackend`` ABC plus
monkey-patched ``transformers`` / ``sentence_transformers`` stubs to
exercise ``ZeroShotV2Backend`` and ``NliCrossEncoderBackend`` without
touching the network or HF cache.
"""

from __future__ import annotations

import sys
import types

import pytest

from pipeline.classifier_backends import (
    CLASSIFIER_BACKEND_REGISTRY,
    ClassifierBackend,
    NliCrossEncoderBackend,
    ZeroShotV2Backend,
)


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------


def test_abc_cannot_instantiate():
    with pytest.raises(TypeError):
        ClassifierBackend()  # type: ignore[abstract]


class _FakeBackend(ClassifierBackend):
    def __init__(self, scores: dict[str, float]) -> None:
        self.model_id = "fake"
        self.device = "cpu"
        self._scores = scores
        self.last_call: dict | None = None

    def classify(self, texts, candidate_labels, *, multi_label=True):
        self.last_call = {
            "texts": list(texts),
            "labels": list(candidate_labels),
            "multi_label": multi_label,
        }
        return [
            {label: float(self._scores.get(label, 0.0)) for label in candidate_labels}
            for _ in texts
        ]


def test_fake_backend_implements_contract():
    fb = _FakeBackend({"a": 0.7, "b": 0.2})
    result = fb.classify(["x", "y"], ["a", "b"])
    assert result == [{"a": 0.7, "b": 0.2}, {"a": 0.7, "b": 0.2}]
    assert fb.last_call == {"texts": ["x", "y"], "labels": ["a", "b"], "multi_label": True}


def test_fake_backend_records_multi_label_flag():
    fb = _FakeBackend({"a": 1.0, "b": 0.0})
    fb.classify(["x"], ["a", "b"], multi_label=False)
    assert fb.last_call is not None and fb.last_call["multi_label"] is False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_contains_known_backends():
    assert "zeroshot_v2" in CLASSIFIER_BACKEND_REGISTRY
    assert "nli_cross_encoder" in CLASSIFIER_BACKEND_REGISTRY
    assert CLASSIFIER_BACKEND_REGISTRY["zeroshot_v2"] is ZeroShotV2Backend
    assert CLASSIFIER_BACKEND_REGISTRY["nli_cross_encoder"] is NliCrossEncoderBackend


# ---------------------------------------------------------------------------
# ZeroShotV2Backend
# ---------------------------------------------------------------------------


class _StubHFPipeline:
    """Mimics the callable returned by ``transformers.pipeline``."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, texts, *, candidate_labels, multi_label):
        self.calls.append({
            "texts": list(texts),
            "labels": list(candidate_labels),
            "multi_label": multi_label,
        })
        out = []
        for _ in texts:
            # Return labels in reversed order to verify reconstruction.
            sorted_labels = list(reversed(candidate_labels))
            scores = [0.9 - 0.3 * i for i in range(len(sorted_labels))]
            out.append({
                "sequence": "...",
                "labels": sorted_labels,
                "scores": scores,
            })
        return out if len(texts) > 1 else out[0]


def _install_stub_transformers(monkeypatch, stub: _StubHFPipeline) -> None:
    fake_mod = types.ModuleType("transformers")

    def fake_pipeline(task, *, model, device, **_kw):
        assert task == "zero-shot-classification"
        return stub

    fake_mod.pipeline = fake_pipeline
    monkeypatch.setitem(sys.modules, "transformers", fake_mod)


def test_zeroshot_v2_passes_labels_through_and_dicts_results(monkeypatch):
    stub = _StubHFPipeline()
    _install_stub_transformers(monkeypatch, stub)

    backend = ZeroShotV2Backend(model="fake/model", device="cpu")
    result = backend.classify(["a", "b"], ["target", "skip"])

    assert stub.calls == [{
        "texts": ["a", "b"],
        "labels": ["target", "skip"],
        "multi_label": True,
    }]
    assert len(result) == 2
    for r in result:
        assert set(r.keys()) == {"target", "skip"}
        assert all(isinstance(v, float) for v in r.values())


def test_zeroshot_v2_empty_input_short_circuits(monkeypatch):
    stub = _StubHFPipeline()
    _install_stub_transformers(monkeypatch, stub)
    backend = ZeroShotV2Backend(model="fake/model")
    assert backend.classify([], ["target", "skip"]) == []
    assert stub.calls == []


def test_zeroshot_v2_single_input_handled(monkeypatch):
    stub = _StubHFPipeline()
    _install_stub_transformers(monkeypatch, stub)
    backend = ZeroShotV2Backend(model="fake/model")
    result = backend.classify(["only one"], ["target", "skip"])
    assert len(result) == 1
    assert set(result[0].keys()) == {"target", "skip"}


def test_zeroshot_v2_caches_pipeline(monkeypatch):
    """Verify the pipeline is loaded once per instance, not per call."""
    load_count = {"n": 0}
    stub = _StubHFPipeline()

    fake_mod = types.ModuleType("transformers")

    def fake_pipeline(task, *, model, device, **_kw):
        load_count["n"] += 1
        return stub

    fake_mod.pipeline = fake_pipeline
    monkeypatch.setitem(sys.modules, "transformers", fake_mod)

    backend = ZeroShotV2Backend(model="fake/model")
    backend.classify(["a"], ["target", "skip"])
    backend.classify(["b"], ["target", "skip"])
    backend.classify(["c"], ["target", "skip"])
    assert load_count["n"] == 1


def test_zeroshot_v2_propagates_multi_label_flag(monkeypatch):
    stub = _StubHFPipeline()
    _install_stub_transformers(monkeypatch, stub)
    backend = ZeroShotV2Backend(model="fake/model")
    backend.classify(["x"], ["target", "skip"], multi_label=False)
    assert stub.calls[-1]["multi_label"] is False


# ---------------------------------------------------------------------------
# NliCrossEncoderBackend
# ---------------------------------------------------------------------------


class _StubCrossEncoder:
    def __init__(self, model_id, device=None, local_files_only=False):
        self.model_id = model_id
        self.device = device
        self.calls: list[list[tuple[str, str]]] = []
        # Map hypothesis suffix -> (entail_logit, contra_logit) so tests
        # can construct deterministic per-label outcomes.
        self.scores_by_hypothesis: dict[str, tuple[float, float]] = {}

    def predict(self, pairs, *, apply_softmax=False, show_progress_bar=False):
        self.calls.append(list(pairs))
        out = []
        for _premise, hypothesis in pairs:
            entail, contra = self.scores_by_hypothesis.get(hypothesis, (0.0, 0.0))
            # Index 0 = contradiction, 1 = entailment, 2 = neutral.
            out.append([contra, entail, 0.0])
        return out


def _install_stub_st(monkeypatch, stub: _StubCrossEncoder) -> None:
    fake_mod = types.ModuleType("sentence_transformers")

    def factory(model, device=None, local_files_only=False):
        # The backend's _load tries local_files_only=True first; in our
        # stub that succeeds, so the fallback branch never runs.
        stub.model_id = model
        stub.device = device
        return stub

    fake_mod.CrossEncoder = factory
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)


def test_nli_cross_encoder_multi_label(monkeypatch):
    stub = _StubCrossEncoder("fake")
    stub.scores_by_hypothesis = {
        # Hypothesis 'This text is target' strongly entailed for both texts.
        "This text is target": (3.0, -1.0),
        # Hypothesis 'This text is skip' strongly contradicted.
        "This text is skip": (-2.0, 2.0),
    }
    _install_stub_st(monkeypatch, stub)
    backend = NliCrossEncoderBackend(model="fake/nli")
    result = backend.classify(["foo", "bar"], ["target", "skip"], multi_label=True)

    assert len(result) == 2
    for r in result:
        assert r["target"] > 0.9  # entailed
        assert r["skip"] < 0.1    # contradicted


def test_nli_cross_encoder_pair_construction(monkeypatch):
    stub = _StubCrossEncoder("fake")
    _install_stub_st(monkeypatch, stub)
    backend = NliCrossEncoderBackend(model="fake/nli")
    backend.classify(["alpha", "beta"], ["target", "skip"])

    # One predict() call with N_texts * N_labels pairs.
    assert len(stub.calls) == 1
    pairs = stub.calls[0]
    assert pairs == [
        ("alpha", "This text is target"),
        ("alpha", "This text is skip"),
        ("beta", "This text is target"),
        ("beta", "This text is skip"),
    ]


def test_nli_cross_encoder_single_label_softmax(monkeypatch):
    stub = _StubCrossEncoder("fake")
    stub.scores_by_hypothesis = {
        "This text is target": (3.0, -1.0),
        "This text is skip": (-2.0, 2.0),
    }
    _install_stub_st(monkeypatch, stub)
    backend = NliCrossEncoderBackend(model="fake/nli")
    result = backend.classify(["foo"], ["target", "skip"], multi_label=False)
    # Softmaxed across labels -> sum to 1.0.
    total = sum(result[0].values())
    assert total == pytest.approx(1.0, abs=1e-6)
    # Target dominates.
    assert result[0]["target"] > result[0]["skip"]


def test_nli_cross_encoder_empty_input(monkeypatch):
    stub = _StubCrossEncoder("fake")
    _install_stub_st(monkeypatch, stub)
    backend = NliCrossEncoderBackend(model="fake/nli")
    assert backend.classify([], ["target", "skip"]) == []
    assert stub.calls == []


# ---------------------------------------------------------------------------
# load_classifiers / resolve_classifier_slots
# ---------------------------------------------------------------------------


def _write_services_toml(tmp_path, body: str):
    p = tmp_path / "SERVICES.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_classifiers_parses_sections(tmp_path):
    from pipeline.services import load_classifiers

    p = _write_services_toml(tmp_path, """
[classifiers.zeroshot-base]
backend = "zeroshot_v2"
model = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
device = "cpu"

[classifiers.nli-small]
backend = "nli_cross_encoder"
model = "cross-encoder/nli-deberta-v3-small"
device = "cpu"

[classifier_defaults]
selector = "zeroshot-base"
""")
    classifiers, defaults = load_classifiers(p)
    assert set(classifiers) == {"zeroshot-base", "nli-small"}
    assert isinstance(classifiers["zeroshot-base"], ZeroShotV2Backend)
    assert classifiers["zeroshot-base"].model_id == "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
    assert isinstance(classifiers["nli-small"], NliCrossEncoderBackend)
    assert defaults == {"selector": "zeroshot-base"}


def test_load_classifiers_unknown_backend_errors(tmp_path):
    from pipeline.services import load_classifiers

    p = _write_services_toml(tmp_path, """
[classifiers.weird]
backend = "no_such_backend"
model = "x"

[classifier_defaults]
selector = "weird"
""")
    with pytest.raises(ValueError, match="no_such_backend"):
        load_classifiers(p)


def test_load_classifiers_missing_sections_returns_empty(tmp_path):
    from pipeline.services import load_classifiers

    p = _write_services_toml(tmp_path, """
[services.foo]
backend = "anthropic"
base_url = "https://example.com"
api_key_env = "FOO"
model = "x"
""")
    classifiers, defaults = load_classifiers(p)
    assert classifiers == {}
    assert defaults == {}


def test_resolve_classifier_slots_defaults_only():
    from pipeline.services import resolve_classifier_slots

    fb = _FakeBackend({})
    slots = resolve_classifier_slots(
        {"zsb": fb}, {"selector": "zsb"}, overrides=None,
    )
    assert slots == {"selector": fb}


def test_resolve_classifier_slots_override_wins():
    from pipeline.services import resolve_classifier_slots

    fb_a = _FakeBackend({})
    fb_b = _FakeBackend({})
    slots = resolve_classifier_slots(
        {"a": fb_a, "b": fb_b},
        {"selector": "a"},
        overrides={"selector": "b"},
    )
    assert slots == {"selector": fb_b}


def test_resolve_classifier_slots_unknown_classifier_raises():
    from pipeline.services import resolve_classifier_slots

    fb = _FakeBackend({})
    with pytest.raises(KeyError, match="nope"):
        resolve_classifier_slots(
            {"zsb": fb},
            {"selector": "nope"},
        )


def test_load_classifiers_file_not_found(tmp_path):
    from pipeline.services import load_classifiers

    with pytest.raises(FileNotFoundError):
        load_classifiers(tmp_path / "missing.toml")

