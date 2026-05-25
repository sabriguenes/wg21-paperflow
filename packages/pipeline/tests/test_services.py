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
- ``resolve_slots``: env-var presence per bound slot. Lazy validation
  so unbound entries in SERVICES.toml stay inert.

No network, no real LLM, no pydantic-ai. Env-var manipulation uses
``monkeypatch``; registry manipulation uses ``monkeypatch.setitem``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline.errors import ServiceConfigError
from pipeline.model_backends import BACKEND_REGISTRY, ModelBackend
from pipeline.services import ServiceRegistry, load_services, resolve_slots


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

    Env-var presence is ``resolve_slots``'s job, not
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


@pytest.mark.parametrize("attr", ["services", "defaults", "api_key_envs"])
def test_service_registry_mappings_are_read_only(attr):
    """All three mappings are wrapped, not just whichever the implementer remembered."""
    registry = ServiceRegistry(
        services={}, defaults={}, api_key_envs={},
    )
    with pytest.raises(TypeError):
        getattr(registry, attr)["x"] = "evil"


# ---------------------------------------------------------------------------
# resolve_slots: env-var validation per bound slot
# ---------------------------------------------------------------------------


def test_resolve_slots_raises_when_bound_service_env_var_missing(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("PAPERFLOW_TEST_KEY", raising=False)
    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "llama3"
base_url = "https://example.invalid/v1"
api_key_env = "PAPERFLOW_TEST_KEY"
model = "test-model"

[defaults]
fast = "s1"
""")
    registry = load_services(p)
    with pytest.raises(ServiceConfigError) as exc_info:
        resolve_slots(registry)
    msg = str(exc_info.value)
    assert "fast" in msg                  # slot name
    assert "s1" in msg                     # service name
    assert "PAPERFLOW_TEST_KEY" in msg     # env var
    assert "--service" in msg              # override remediation hint


@pytest.mark.parametrize("env_value", ["", "   ", "\n", "\t \n"])
def test_resolve_slots_raises_for_empty_or_whitespace_env_var(
    tmp_path, monkeypatch, env_value,
):
    monkeypatch.setenv("PAPERFLOW_TEST_KEY", env_value)
    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "llama3"
base_url = "https://example.invalid/v1"
api_key_env = "PAPERFLOW_TEST_KEY"
model = "test-model"

[defaults]
fast = "s1"
""")
    registry = load_services(p)
    with pytest.raises(ServiceConfigError, match="PAPERFLOW_TEST_KEY"):
        resolve_slots(registry)


def test_resolve_slots_succeeds_when_bound_service_env_var_set(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("PAPERFLOW_TEST_KEY", "sk-real-value")
    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "llama3"
base_url = "https://example.invalid/v1"
api_key_env = "PAPERFLOW_TEST_KEY"
model = "test-model"

[defaults]
fast = "s1"
""")
    registry = load_services(p)
    slots = resolve_slots(registry)
    assert slots["fast"] == ("s1", registry.services["s1"])


def test_resolve_slots_skips_validation_for_unbound_services(
    tmp_path, monkeypatch,
):
    """Regression guard for ``--service`` overrides rebinding all slots.

    KEY_A is set, KEY_B is not. ``[defaults]`` pins every slot to
    s_b. Overrides rebind every slot to s_a. ``resolve_slots`` must
    succeed and never touch KEY_B.
    """
    monkeypatch.setenv("KEY_A", "value-a")
    monkeypatch.delenv("KEY_B", raising=False)
    p = _write_services_toml(tmp_path, """
[services.s_a]
backend = "llama3"
base_url = "https://a.invalid/v1"
api_key_env = "KEY_A"
model = "model-a"

[services.s_b]
backend = "llama3"
base_url = "https://b.invalid/v1"
api_key_env = "KEY_B"
model = "model-b"

[defaults]
fast = "s_b"
default = "s_b"
tool = "s_b"
""")
    registry = load_services(p)
    slots = resolve_slots(
        registry,
        overrides={"fast": "s_a", "default": "s_a", "tool": "s_a"},
    )
    s_a = registry.services["s_a"]
    assert all(binding == ("s_a", s_a) for binding in slots.values())


def test_resolve_slots_with_empty_api_key_envs_skips_validation():
    """Hand-built registry path used by tests that construct slot maps directly.

    Empty ``api_key_envs`` explicitly opts out of validation. Do not
    delete this test without auditing all production call sites of
    ``resolve_slots`` to make sure they still go through
    ``load_services`` (which populates ``api_key_envs``).
    """
    backend = MagicMock(spec=["run"])
    registry = ServiceRegistry(
        services={"s1": backend},
        defaults={"fast": "s1"},
        api_key_envs={},
    )
    slots = resolve_slots(registry)
    assert slots["fast"] == ("s1", backend)


def test_resolve_slots_no_auth_service(tmp_path):
    p = _write_services_toml(tmp_path, """
[services.s1]
backend = "llama3"
base_url = "http://localhost:8000/v1"
model = "local-model"

[defaults]
fast = "s1"
""")
    registry = load_services(p)
    slots = resolve_slots(registry)
    assert slots["fast"] == ("s1", registry.services["s1"])
