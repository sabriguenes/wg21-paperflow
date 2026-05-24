#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Service loader: SERVICES.toml -> :class:`ServiceRegistry`.

``SERVICES.toml`` at the repo root is a pure infrastructure inventory.
Each ``[services.NAME]`` section declares an endpoint with its
capabilities. API keys come from environment variables only (the
``api_key_env`` field names the env var; the key itself is never in
the file). There are no slot defaults; each pipeline's markdown file
declares its own logical-name -> service-name map under
``## Services``.

Validation happens in two layers:

- :func:`load_services` does eager config-shape checks: unknown
  ``backend`` keys and ``required_api_key_env`` mismatches both raise
  at load. Env var presence is *not* checked here.
- :func:`resolve_pipeline_models` does lazy env var validation: only
  services referenced by the pipeline's ``## Services`` block are
  required to have their env var set. SERVICES.toml entries that no
  loaded pipeline references stay inert.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pipeline.classifier_backends import (
    CLASSIFIER_BACKEND_REGISTRY,
    ClassifierBackend,
)
from pipeline.errors import ServiceConfigError
from pipeline.model_backends import BACKEND_REGISTRY, ModelBackend
from pipeline.transformer_backend import TransformerProvider, default_auto_provider

if TYPE_CHECKING:
    from pipeline.transformer_backend import EmbeddingBackend

logger = logging.getLogger(__name__)

_SERVICES_FILENAME = "SERVICES.toml"


@dataclass(frozen=True)
class ServiceRegistry:
    """Loader output: services + env-var metadata.

    Read-only by construction: both mappings are wrapped in
    :class:`types.MappingProxyType` at init time, so attempts to
    assign into them raise ``TypeError``. The type annotations use
    :class:`collections.abc.Mapping` (not ``dict``) to reflect that
    callers must treat the contents as immutable.

    ``api_key_envs[name]`` is the env var name declared on
    ``[services.NAME]``, or ``""`` for entries with no auth.
    """

    services: Mapping[str, ModelBackend]
    api_key_envs: Mapping[str, str]

    def __post_init__(self) -> None:
        # Copy then proxy: freezes the snapshot we were handed and
        # prevents external mutation of the original dict from
        # bleeding through the proxy. ``frozen=True`` blocks direct
        # attribute assignment in __post_init__, so go through
        # ``object.__setattr__``.
        object.__setattr__(
            self, "services", MappingProxyType(dict(self.services))
        )
        object.__setattr__(
            self, "api_key_envs", MappingProxyType(dict(self.api_key_envs))
        )


def _find_services_toml() -> Path | None:
    """Walk up from cwd to find SERVICES.toml."""
    here = Path.cwd()
    for parent in [here, *here.parents]:
        candidate = parent / _SERVICES_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_services(path: Path | None = None) -> ServiceRegistry:
    """Parse SERVICES.toml, build ModelBackend instances, return registry.

    Returns a :class:`ServiceRegistry` carrying:

    - ``services``: service name -> :class:`ModelBackend` instance.
    - ``api_key_envs``: service name -> env var name declared on the
      entry, or ``""`` for entries with no auth.
      :func:`resolve_pipeline_models` consumes this to validate the
      env vars of the pipeline-referenced subset at resolution time
      (lazy, so unused inventory entries stay inert).

    For each ``[services.NAME]`` entry the order of operations is
    fixed:

    1. Look up ``backend`` in :data:`BACKEND_REGISTRY` (raises on
       unknown).
    2. Read ``backend_cls.required_api_key_env``. If non-None, the
       entry's ``api_key_env`` must match (raises otherwise). This
       shape check fires *before* the backend constructor runs, so
       the framework's :class:`ServiceConfigError` wins over any
       backend ``__init__`` strictness.
    3. Read the env var (may be unset; no error here).
    4. Construct the backend.
    5. Record the env var name in ``api_key_envs``.

    Raises ``FileNotFoundError`` if the config file is not found.
    Raises :class:`ServiceConfigError` for unknown backend types and for
    ``required_api_key_env`` mismatches.
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

    services: dict[str, ModelBackend] = {}
    api_key_envs: dict[str, str] = {}
    for name, svc in services_config.items():
        # (1) Resolve the backend class.
        backend_key = svc.get("backend")
        if backend_key not in BACKEND_REGISTRY:
            raise ServiceConfigError(
                f"Service '{name}' declares backend '{backend_key}' "
                f"which is not in the registry. "
                f"Available: {sorted(BACKEND_REGISTRY)}"
            )
        backend_cls = BACKEND_REGISTRY[backend_key]

        # (2) Resolve the API key. Three modes:
        #
        #   api_key = "sk-literal"       -> literal value
        #   api_key = "$ENV_VAR_NAME"    -> expand env var
        #   api_key_env = "ENV_VAR_NAME" -> legacy, same as "$ENV_VAR_NAME"
        #
        # ``api_key`` takes precedence over ``api_key_env``.
        raw_api_key = svc.get("api_key", "")
        api_key_env = svc.get("api_key_env", "")

        if raw_api_key and raw_api_key.startswith("$"):
            api_key_env = raw_api_key[1:]
            api_key = os.environ.get(api_key_env, "")
        elif raw_api_key:
            api_key = raw_api_key
        elif api_key_env:
            api_key = os.environ.get(api_key_env, "")
        else:
            api_key = ""

        # Shape check: backends that read their credential directly
        # from the environment declare the only acceptable env var
        # name. Reject mismatches before constructing the backend.
        required = backend_cls.required_api_key_env
        if required is not None and api_key_env != required:
            raise ServiceConfigError(
                f"Service '{name}': backend='{backend_key}' requires "
                f"api_key_env='{required}' (got {api_key_env!r}). "
                f"This backend reads {required} from the environment "
                f"directly; any other name will not be honored."
            )

        # (4) Construct. Known keys are extracted explicitly; the
        # rest flow through **kwargs so backends can declare their
        # own config (e.g. stream = true for vLLM behind Cloudflare).
        _KNOWN_KEYS = {"backend", "api_key", "api_key_env"}
        init_kwargs: dict[str, Any] = {
            "base_url": svc.get("base_url", ""),
            "api_key": api_key,
            "model": svc.get("model", ""),
            "max_context_window": svc.get("max_context_window", 131072),
            "chars_per_token": svc.get("chars_per_token", 0),
            "token_multiplier": svc.get("token_multiplier", 0),
        }
        for k, v in svc.items():
            if k not in init_kwargs and k not in _KNOWN_KEYS:
                init_kwargs[k] = v
        services[name] = backend_cls(**init_kwargs)

        # (5) Record.
        api_key_envs[name] = api_key_env

        logger.info(
            "Service '%s': %s  model=%s  endpoint=%s",
            name, backend_key, init_kwargs["model"], init_kwargs["base_url"],
        )

    return ServiceRegistry(
        services=services,
        api_key_envs=api_key_envs,
    )


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
    Raises :class:`ServiceConfigError` for unknown backend types.
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
            raise ServiceConfigError(
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


def load_embedders(
    path: Path | None = None,
    *,
    provider: TransformerProvider | None = None,
) -> tuple[dict[str, EmbeddingBackend], dict[str, str]]:
    """Parse SERVICES.toml ``[embedders.*]``, build EmbeddingBackend instances.

    Parallel to :func:`load_classifiers`. Returns ``(embedders, defaults)``
    where ``embedders`` maps names to
    :class:`pipeline.transformer_backend.EmbeddingBackend` instances and
    ``defaults`` maps slot names to embedder names from
    ``[embedder_defaults]``.

    Missing ``[embedders.*]`` and ``[embedder_defaults]`` sections are not
    an error - the returned dicts are empty in that case.
    """
    from pipeline.transformer_backend import EmbeddingBackend

    if path is None:
        path = _find_services_toml()
    if path is None or not path.is_file():
        raise FileNotFoundError(
            f"{_SERVICES_FILENAME} not found. Create it at the repo root "
            f"with at least one [services.NAME] section."
        )

    with open(path, "rb") as f:
        config = tomllib.load(f)

    embedders_config = config.get("embedders", {})
    defaults = config.get("embedder_defaults", {})

    if provider is None:
        provider = default_auto_provider()

    embedders: dict[str, EmbeddingBackend] = {}
    for name, cfg in embedders_config.items():
        model_id = cfg.get("model", "")
        if not model_id:
            raise ServiceConfigError(
                f"Embedder '{name}' is missing required 'model' field."
            )
        embedders[name] = EmbeddingBackend(model_id, provider)
        logger.info(
            "Embedder '%s': model=%s  provider=%s (device=%s dtype=%s batch=%d)",
            name, model_id,
            provider.name, provider.device, provider.dtype, provider.batch_size,
        )

    return embedders, defaults


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
    Raises :class:`pipeline.errors.TransformerConfigError` for malformed
    entries (missing required keys under ``mode = "explicit"``).
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


def resolve_pipeline_models(
    services_map: Mapping[str, str],
    registry: ServiceRegistry,
) -> dict[str, ModelBackend]:
    """Resolve a pipeline's logical-name -> ModelBackend map.

    ``services_map`` is the pipeline's parsed ``## Services`` block
    (from :func:`pipeline.prompt.parse_pipeline_services`). Logical
    names are pipeline-defined; the only required entry is
    ``"default"``.

    Validates:

    - ``"default"`` is present.
    - Every service name in the map exists in
      ``registry.services``.
    - Every referenced service's ``api_key_env`` env var is set to a
      non-whitespace value (services that declared no env var are
      skipped, mirroring :func:`resolve_slots`).

    Raises :class:`ServiceConfigError` on any failure. The error
    message identifies the failing logical name and either the missing
    service or the missing env var so the user can edit the pipeline's
    markdown file or export the env var.
    """
    if "default" not in services_map:
        raise ServiceConfigError(
            "Pipeline markdown is missing the required `**default:**` "
            "entry under `## Services`. Every pipeline must declare which "
            "service backs the `default` logical model."
        )

    out: dict[str, ModelBackend] = {}
    for logical_name, service_name in services_map.items():
        if service_name not in registry.services:
            raise ServiceConfigError(
                f"Pipeline logical model '{logical_name}' maps to service "
                f"'{service_name}', which is not defined in SERVICES.toml. "
                f"Available services: {sorted(registry.services)}"
            )

        env_var = registry.api_key_envs.get(service_name, "")
        if env_var:
            value = os.environ.get(env_var, "").strip()
            if not value:
                raise ServiceConfigError(
                    f"Pipeline logical model '{logical_name}' maps to service "
                    f"'{service_name}', which requires env var "
                    f"'{env_var}'.\n"
                    f"Export it before running paperflow:\n"
                    f"  export {env_var}=<your-key>\n"
                    f"Or change the binding in the pipeline's markdown "
                    f"`## Services` section."
                )

        out[logical_name] = registry.services[service_name]

    return out


