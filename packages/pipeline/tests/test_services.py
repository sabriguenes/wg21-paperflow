#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for ``pipeline.services`` API-key fail-fast contract.

Covers both layers:

- ``load_services``: config-shape checks (unknown backend, generic
  ``required_api_key_env`` mismatch). Env-agnostic at this layer; a
  missing env var does NOT raise here.
- ``resolve_pipeline_models``: env-var presence per pipeline-referenced
  service. Lazy validation so SERVICES.toml entries not referenced by
  any loaded pipeline stay inert.

No network, no real LLM, no pydantic-ai. Env-var manipulation uses
``monkeypatch``; registry manipulation uses ``monkeypatch.setitem``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline.errors import ServiceConfigError
from pipeline.model_backends import BACKEND_REGISTRY, ModelBackend
from pipeline.services import (
    ServiceRegistry,
    load_services,
    resolve_pipeline_models,
)


def _write_services_toml(tmp_path, body: str):
    p = tmp_path / "SERVICES.toml"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_services: returns ServiceRegistry; env-agnostic at load time
# ---------------------------------------------------------------------------


def test_load_services_returns_registry_with_api_key_envs(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERFLOW_TEST_KEY", raising=False)
    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "llama3"
base_url = "https://example.invalid/v1"
api_key_env = "PAPERFLOW_TEST_KEY"
model = "test-model"
""")
    registry = load_services(p)
    assert isinstance(registry, ServiceRegistry)
    assert set(registry.services) == {"s1"}
    assert registry.api_key_envs["s1"] == "PAPERFLOW_TEST_KEY"


@pytest.mark.parametrize(
    "body",
    [
        # api_key_env field omitted entirely
        (
            'backend = "llama3"\n'
            'base_url = "http://localhost:8000/v1"\n'
            'model = "local-model"'
        ),
        # api_key_env field explicitly empty
        (
            'backend = "llama3"\n'
            'base_url = "http://localhost:8000/v1"\n'
            'api_key_env = ""\n'
            'model = "local-model"'
        ),
    ],
    ids=["field-omitted", "field-empty-string"],
)
def test_load_services_no_auth_styles_pass_empty_string(tmp_path, body):
    p = _write_services_toml(tmp_path, f"[services.s1]\n{body}\n")
    registry = load_services(p)
    assert registry.api_key_envs["s1"] == ""


def test_load_services_required_api_key_env_mismatch_raises(tmp_path):
    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "anthropic"
api_key_env = "MY_OWN_KEY"
model = "claude-test"
""")
    with pytest.raises(ServiceConfigError) as exc_info:
        load_services(p)
    msg = str(exc_info.value)
    assert "anthropic" in msg
    assert "ANTHROPIC_API_KEY" in msg
    assert "MY_OWN_KEY" in msg


def test_required_api_key_env_class_attribute_enforced(tmp_path, monkeypatch):
    """Loader reads ``backend_cls.required_api_key_env`` generically.

    Regression guard against a future revert to a hardcoded
    ``svc.get("backend") == "anthropic"`` check that would silently
    skip new SDK-reads-env-directly backends.
    """

    class _SdkReadsEnvBackend(ModelBackend):
        thinking_capable = False
        tools_capable = False
        required_api_key_env = "SYNTH_KEY"

        def __init__(self, **kwargs) -> None:  # accept loader kwargs, ignore
            pass

        async def run(self, *args, **kwargs):
            raise NotImplementedError

    monkeypatch.setitem(BACKEND_REGISTRY, "synthetic", _SdkReadsEnvBackend)

    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "synthetic"
api_key_env = "WRONG_KEY"
model = "test"
""")
    with pytest.raises(ServiceConfigError) as exc_info:
        load_services(p)
    msg = str(exc_info.value)
    assert "SYNTH_KEY" in msg
    assert "WRONG_KEY" in msg


def test_load_services_required_api_key_env_match_loads(tmp_path, monkeypatch):
    """Matching env-var name passes the shape check even if unset.

    Env-var presence is ``resolve_pipeline_models``'s job, not
    ``load_services``'s.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"
model = "claude-test"
""")
    registry = load_services(p)
    assert "s1" in registry.services


# ---------------------------------------------------------------------------
# ServiceRegistry: structural immutability (MappingProxyType wrap)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attr", ["services", "api_key_envs"])
def test_service_registry_mappings_are_read_only(attr):
    """Both mappings are wrapped, not just whichever the implementer remembered."""
    registry = ServiceRegistry(services={}, api_key_envs={})
    with pytest.raises(TypeError):
        getattr(registry, attr)["x"] = "evil"


# ---------------------------------------------------------------------------
# resolve_pipeline_models: env-var validation per pipeline-referenced service
# ---------------------------------------------------------------------------


def _registry_with(services, api_key_envs=None):
    """Build a tiny ServiceRegistry for tests."""
    if api_key_envs is None:
        api_key_envs = {name: "" for name in services}
    return ServiceRegistry(services=services, api_key_envs=api_key_envs)


def test_resolve_pipeline_models_success_no_auth():
    """No-auth service references resolve without env-var checks."""
    backend = MagicMock(spec=["run"])
    registry = _registry_with({"s1": backend})
    models = resolve_pipeline_models({"default": "s1"}, registry)
    assert models == {"default": backend}


def test_resolve_pipeline_models_requires_default_logical_name():
    """A pipeline that omits `default` is rejected at load time."""
    backend = MagicMock(spec=["run"])
    registry = _registry_with({"s1": backend})
    with pytest.raises(ServiceConfigError, match="default"):
        resolve_pipeline_models({"tool": "s1"}, registry)


def test_resolve_pipeline_models_rejects_unknown_service():
    """A logical name pointing at a service not in SERVICES.toml is rejected."""
    backend = MagicMock(spec=["run"])
    registry = _registry_with({"s1": backend})
    with pytest.raises(ServiceConfigError) as exc_info:
        resolve_pipeline_models({"default": "missing"}, registry)
    msg = str(exc_info.value)
    assert "default" in msg
    assert "missing" in msg


def test_resolve_pipeline_models_requires_env_var_set(monkeypatch):
    """Bound service with declared api_key_env must have it exported."""
    monkeypatch.delenv("PAPERFLOW_TEST_KEY", raising=False)
    backend = MagicMock(spec=["run"])
    registry = _registry_with(
        {"s1": backend},
        api_key_envs={"s1": "PAPERFLOW_TEST_KEY"},
    )
    with pytest.raises(ServiceConfigError) as exc_info:
        resolve_pipeline_models({"default": "s1"}, registry)
    msg = str(exc_info.value)
    assert "PAPERFLOW_TEST_KEY" in msg
    assert "default" in msg


@pytest.mark.parametrize("env_value", ["", "   ", "\n", "\t \n"])
def test_resolve_pipeline_models_rejects_whitespace_env_var(monkeypatch, env_value):
    monkeypatch.setenv("PAPERFLOW_TEST_KEY", env_value)
    backend = MagicMock(spec=["run"])
    registry = _registry_with(
        {"s1": backend},
        api_key_envs={"s1": "PAPERFLOW_TEST_KEY"},
    )
    with pytest.raises(ServiceConfigError, match="PAPERFLOW_TEST_KEY"):
        resolve_pipeline_models({"default": "s1"}, registry)


def test_resolve_pipeline_models_skips_unreferenced_services(monkeypatch):
    """Services declared in SERVICES.toml but unreferenced by the pipeline stay inert.

    KEY_A is set, KEY_B is not. Only s_a is referenced by the
    pipeline. Resolution must succeed without ever touching KEY_B.
    """
    monkeypatch.setenv("KEY_A", "value-a")
    monkeypatch.delenv("KEY_B", raising=False)
    a, b = MagicMock(spec=["run"]), MagicMock(spec=["run"])
    registry = _registry_with(
        {"s_a": a, "s_b": b},
        api_key_envs={"s_a": "KEY_A", "s_b": "KEY_B"},
    )
    models = resolve_pipeline_models({"default": "s_a"}, registry)
    assert models == {"default": a}
