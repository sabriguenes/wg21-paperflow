#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Service loader: SERVICES.toml -> ModelBackend instances.

``SERVICES.toml`` at the repo root is a pure infrastructure inventory.
Each ``[services.NAME]`` section declares an endpoint with its
capabilities. API keys come from environment variables only (the
``api_key_env`` field names the env var; the key itself is never in
the file).

The ``[defaults]`` section maps slot names (``fast``, ``default``,
``tool``) to service names for interactive use. The orchestrator
overrides via ``--service`` CLI flags.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Any

from pipeline.classifier_backends import (
    CLASSIFIER_BACKEND_REGISTRY,
    ClassifierBackend,
)
from pipeline.model_backends import BACKEND_REGISTRY, ModelBackend
from pipeline.transformer_backend import TransformerProvider, default_auto_provider

logger = logging.getLogger(__name__)

_SERVICES_FILENAME = "SERVICES.toml"


def _find_services_toml() -> Path | None:
    """Walk up from cwd to find SERVICES.toml."""
    here = Path.cwd()
    for parent in [here, *here.parents]:
        candidate = parent / _SERVICES_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_services(path: Path | None = None) -> tuple[dict[str, ModelBackend], dict[str, str]]:
    """Parse SERVICES.toml, resolve API keys, build ModelBackend instances.

    Returns ``(services, defaults)`` where ``services`` maps service
    names to ``ModelBackend`` instances and ``defaults`` maps slot
    names to service names from the ``[defaults]`` section.

    Raises ``FileNotFoundError`` if the config file is not found.
    Raises ``ValueError`` for unknown backend types or missing env vars.
    """
    if path is None:
        path = _find_services_toml()
    if path is None or not path.is_file():
        raise FileNotFoundError(
            f"{_SERVICES_FILENAME} not found. Create it at the repo root "
            f"with at least one [services.NAME] section."
        )

    with open(path, "rb") as f:
        config = tomllib.load(f)

    services_config = config.get("services", {})
    defaults = config.get("defaults", {})

    services: dict[str, ModelBackend] = {}
    for name, svc in services_config.items():
        backend_key = svc.get("backend")
        if backend_key not in BACKEND_REGISTRY:
            raise ValueError(
                f"Service '{name}' declares backend '{backend_key}' "
                f"which is not in the registry. "
                f"Available: {sorted(BACKEND_REGISTRY)}"
            )

        api_key_env = svc.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not api_key:
            logger.warning(
                "Service '%s': env var '%s' is not set. "
                "Requests will fail if the endpoint requires authentication.",
                name, api_key_env,
            )

        backend_cls = BACKEND_REGISTRY[backend_key]
        init_kwargs: dict[str, Any] = {
            "base_url": svc.get("base_url", ""),
            "api_key": api_key or "dummy",
            "model": svc.get("model", ""),
            "max_tokens": svc.get("max_tokens", 16384),
        }

        services[name] = backend_cls(**init_kwargs)
        logger.info(
            "Service '%s': %s  model=%s  endpoint=%s",
            name, backend_key, init_kwargs["model"], init_kwargs["base_url"],
        )

    return services, defaults


def resolve_slots(
    services: dict[str, ModelBackend],
    defaults: dict[str, str],
    overrides: dict[str, str] | None = None,
) -> dict[str, ModelBackend]:
    """Map slot names to ModelBackend instances.

    ``overrides`` (from ``--service`` CLI flags) beat ``defaults``
    (from ``[defaults]`` in SERVICES.toml).

    When a single override has no ``=`` (e.g., ``--service b200-r1``),
    it applies to all slots in ``defaults``.

    Raises ``KeyError`` if a slot references a service name that
    doesn't exist in ``services``.
    """
    merged = dict(defaults)
    if overrides:
        merged.update(overrides)

    slots: dict[str, ModelBackend] = {}
    for slot_name, service_name in merged.items():
        if service_name not in services:
            raise KeyError(
                f"Slot '{slot_name}' references service '{service_name}' "
                f"which is not defined in SERVICES.toml. "
                f"Available services: {sorted(services)}"
            )
        slots[slot_name] = services[service_name]

    return slots


def load_classifiers(
    path: Path | None = None,
    *,
    provider: TransformerProvider | None = None,
) -> tuple[dict[str, ClassifierBackend], dict[str, str]]:
    """Parse SERVICES.toml, build ClassifierBackend instances.

    Parallel to :func:`load_services`. Returns ``(classifiers,
    defaults)`` where ``classifiers`` maps names to
    :class:`pipeline.classifier_backends.ClassifierBackend` instances
    and ``defaults`` maps slot names (e.g. ``selector``) to classifier
    names from ``[classifier_defaults]``.

    No API keys: classifier backends are local-model wrappers. Per-
    entry fields (``model`` plus any optional fields the specific
    backend reads) are forwarded as kwargs to the backend constructor.

    The runtime device / dtype / batch settings come from a
    :class:`TransformerProvider`. Resolve one with
    :func:`load_transformer_providers` + :func:`resolve_transformer_provider`
    and pass it via the ``provider`` kwarg; when omitted, the
    process-wide host-auto provider is used. The legacy per-classifier
    ``device`` field is silently dropped: configure it via the provider
    instead.

    Missing ``[classifiers.*]`` and ``[classifier_defaults]`` are not
    an error -- this lets pre-Step-1 callers ignore the new sections
    entirely. The returned dicts are empty in that case.

    Raises ``FileNotFoundError`` if the config file is not found.
    Raises ``ValueError`` for unknown backend types.
    """
    if path is None:
        path = _find_services_toml()
    if path is None or not path.is_file():
        raise FileNotFoundError(
            f"{_SERVICES_FILENAME} not found. Create it at the repo root "
            f"with at least one [services.NAME] section."
        )

    with open(path, "rb") as f:
        config = tomllib.load(f)

    classifiers_config = config.get("classifiers", {})
    defaults = config.get("classifier_defaults", {})

    if provider is None:
        provider = default_auto_provider()

    classifiers: dict[str, ClassifierBackend] = {}
    for name, cfg in classifiers_config.items():
        backend_key = cfg.get("backend")
        if backend_key not in CLASSIFIER_BACKEND_REGISTRY:
            raise ValueError(
                f"Classifier '{name}' declares backend '{backend_key}' "
                f"which is not in the registry. "
                f"Available: {sorted(CLASSIFIER_BACKEND_REGISTRY)}"
            )

        # Drop `backend` (used above) and the legacy `device` field
        # (now owned by the provider). Forward the rest as kwargs.
        init_kwargs: dict[str, Any] = {
            k: v for k, v in cfg.items() if k not in ("backend", "device")
        }
        init_kwargs["provider"] = provider

        backend_cls = CLASSIFIER_BACKEND_REGISTRY[backend_key]
        classifiers[name] = backend_cls(**init_kwargs)
        logger.info(
            "Classifier '%s': %s  model=%s  provider=%s (device=%s dtype=%s batch=%d)",
            name, backend_key, cfg.get("model", ""),
            provider.name, provider.device, provider.dtype, provider.batch_size,
        )

    return classifiers, defaults


def load_transformer_providers(
    path: Path | None = None,
) -> tuple[dict[str, TransformerProvider], dict[str, str]]:
    """Parse ``[transformer_providers.*]`` and ``[transformer_provider_defaults]``.

    Returns ``(providers, defaults)``. Always includes a ``"auto"``
    entry: if SERVICES.toml does not declare one (e.g. a fresh clone),
    a host-detected one is injected so callers can rely on the name.

    Missing sections are not an error. The returned ``defaults`` dict
    is empty in that case; the four-level resolution in
    :func:`resolve_transformer_provider` will still land on ``"auto"``.

    Raises ``FileNotFoundError`` if the config file is not found.
    Raises ``ValueError`` for malformed entries (missing required
    keys under ``mode = "explicit"``).
    """
    if path is None:
        path = _find_services_toml()
    if path is None or not path.is_file():
        raise FileNotFoundError(
            f"{_SERVICES_FILENAME} not found. Create it at the repo root "
            f"with at least one [services.NAME] section."
        )

    with open(path, "rb") as f:
        config = tomllib.load(f)

    raw_providers = config.get("transformer_providers", {})
    defaults = config.get("transformer_provider_defaults", {})

    providers: dict[str, TransformerProvider] = {}
    for name, cfg in raw_providers.items():
        providers[name] = TransformerProvider.from_toml(name, cfg)

    # Hardcoded fallback so a fresh clone with no transformer_providers
    # section still produces a usable "auto" provider.
    if "auto" not in providers:
        providers["auto"] = default_auto_provider()

    return providers, defaults


_TRANSFORMER_PROVIDER_ENV = "PAPERFLOW_TRANSFORMER_PROVIDER"


def resolve_transformer_provider(
    providers: dict[str, TransformerProvider],
    defaults: dict[str, str],
    *,
    override: str | None = None,
) -> TransformerProvider:
    """Apply the four-level provider-name precedence.

    Order, top wins:

    1. ``override`` (from the ``--provider`` CLI flag).
    2. ``PAPERFLOW_TRANSFORMER_PROVIDER`` env var.
    3. ``[transformer_provider_defaults].default`` from SERVICES.toml.
    4. The hardcoded ``"auto"`` entry injected by
       :func:`load_transformer_providers`.

    Raises ``KeyError`` if the resolved name is not in ``providers``.
    Mirrors the slot-resolution pattern used by :func:`resolve_slots`
    and :func:`resolve_classifier_slots`.
    """
    name = (
        override
        or os.environ.get(_TRANSFORMER_PROVIDER_ENV)
        or defaults.get("default")
        or "auto"
    )
    if name not in providers:
        raise KeyError(
            f"Transformer provider '{name}' is not defined in "
            f"SERVICES.toml. Available: {sorted(providers)}"
        )
    return providers[name]


def resolve_classifier_slots(
    classifiers: dict[str, ClassifierBackend],
    defaults: dict[str, str],
    overrides: dict[str, str] | None = None,
) -> dict[str, ClassifierBackend]:
    """Map classifier slot names to ClassifierBackend instances.

    Parallel to :func:`resolve_slots`. ``overrides`` (from
    ``--classifier`` CLI flags) beat ``defaults`` (from
    ``[classifier_defaults]`` in SERVICES.toml).

    When a single override has no ``=`` (e.g., ``--classifier
    zeroshot-base``), it applies to all slots in ``defaults``.

    Raises ``KeyError`` if a slot references a classifier name that
    doesn't exist.
    """
    merged = dict(defaults)
    if overrides:
        merged.update(overrides)

    slots: dict[str, ClassifierBackend] = {}
    for slot_name, classifier_name in merged.items():
        if classifier_name not in classifiers:
            raise KeyError(
                f"Slot '{slot_name}' references classifier "
                f"'{classifier_name}' which is not defined in "
                f"SERVICES.toml. "
                f"Available classifiers: {sorted(classifiers)}"
            )
        slots[slot_name] = classifiers[classifier_name]

    return slots
