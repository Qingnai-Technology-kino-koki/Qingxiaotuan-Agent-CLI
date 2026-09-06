"""Custom-registry (api.json) parsing + config application.

Ported from ``custom-registry.ts``. The parsing and in-memory config
application helpers are pure and live here. The network-bound
``fetchCustomRegistry`` (it performs an HTTP fetch against a registry URL) is
SKIPPED -- see ``SKIPPED.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .model_alias_merge import CUSTOM_REGISTRY_MODEL_FIELDS, merge_refreshed_model_alias
from .utils import is_record

CUSTOM_REGISTRY_DEFAULT_MAX_CONTEXT = 131072
CUSTOM_REGISTRY_DEFAULT_CAPABILITIES: tuple[str, ...] = ("tool_use",)

_ALLOWED_PROVIDER_TYPES = frozenset({"anthropic", "openai", "openai_responses", "kimi"})


class CustomRegistryProviderType(str, Enum):
    """Provider type union mirrored from the api.json schema."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENAI_RESPONSES = "openai_responses"
    KIMI = "kimi"


class CustomRegistryApiError(Exception):
    """Raised when a custom-registry fetch returns a non-OK HTTP status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class CustomRegistrySource:
    """Where a custom-registry-managed provider came from."""

    kind: str = "apiJson"
    url: str = ""
    api_key: str = ""


@dataclass
class CustomRegistryModelEntry:
    """One model entry inside a custom-registry provider."""

    id: str
    name: str | None = None
    limit: dict[str, int] | None = None
    tool_call: bool | None = None
    reasoning: bool | None = None
    modalities: dict[str, list[str]] | None = None
    support_efforts: list[str] | None = None
    default_effort: str | None = None


@dataclass
class CustomRegistryProviderEntry:
    """One top-level provider entry inside an api.json document."""

    id: str
    name: str
    api: str
    type: str
    models: dict[str, CustomRegistryModelEntry] = field(default_factory=dict)
    env: list[str] | None = None


@dataclass
class ManagedKimiConfigShape:
    """Managed Kimi config document (mutable, dict-backed provider/model maps)."""

    providers: dict[str, Any] = field(default_factory=dict)
    models: dict[str, Any] | None = None
    default_model: str | None = None
    default_provider: str | None = None
    thinking: dict[str, Any] | None = None
    services: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _is_allowed_provider_type(value: Any) -> bool:
    return isinstance(value, str) and value in _ALLOWED_PROVIDER_TYPES


def _to_string_array(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        out.append(item)
    return out


def to_model_entry(value: Any) -> CustomRegistryModelEntry | None:
    if not is_record(value):
        return None
    model_id = value.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None

    entry = CustomRegistryModelEntry(id=model_id)

    name = value.get("name")
    if isinstance(name, str) and name:
        entry.name = name

    limit = value.get("limit")
    if is_record(limit):
        parsed: dict[str, int] = {}
        for key in ("context", "output"):
            raw = limit.get(key)
            if isinstance(raw, (int, float)) and raw > 0:
                if isinstance(raw, float) and not raw.is_integer():
                    continue
                parsed[key] = int(raw)
        if parsed:
            entry.limit = parsed

    if isinstance(value.get("tool_call"), bool):
        entry.tool_call = value["tool_call"]
    if isinstance(value.get("reasoning"), bool):
        entry.reasoning = value["reasoning"]

    support_efforts = _to_string_array(value.get("support_efforts"))
    if support_efforts is not None:
        entry.support_efforts = support_efforts
    default_effort = value.get("default_effort")
    if isinstance(default_effort, str) and default_effort:
        entry.default_effort = default_effort

    modalities = value.get("modalities")
    if is_record(modalities):
        parsed_modalities: dict[str, list[str]] = {}
        for key in ("input", "output"):
            arr = _to_string_array(modalities.get(key))
            if arr is not None:
                parsed_modalities[key] = arr
        if parsed_modalities:
            entry.modalities = parsed_modalities

    return entry


def to_provider_entry(value: Any) -> CustomRegistryProviderEntry | None:
    if not is_record(value):
        return None
    provider_id = value.get("id")
    name = value.get("name")
    api = value.get("api")
    provider_type = value.get("type")
    models = value.get("models")

    if not isinstance(provider_id, str) or not provider_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(api, str) or not api:
        return None
    if not _is_allowed_provider_type(provider_type):
        return None
    if not is_record(models):
        return None

    parsed_models: dict[str, CustomRegistryModelEntry] = {}
    for key, raw in models.items():
        model_entry = to_model_entry(raw)
        if model_entry is not None:
            parsed_models[key] = model_entry

    env = _to_string_array(value.get("env"))
    return CustomRegistryProviderEntry(
        id=provider_id,
        name=name,
        api=api,
        type=str(provider_type),
        models=parsed_models,
        env=env,
    )


def _has_rich_capability_hints(model: CustomRegistryModelEntry) -> bool:
    return (
        isinstance(model.tool_call, bool)
        or isinstance(model.reasoning, bool)
        or model.modalities is not None
        or model.support_efforts is not None
    )


def capabilities_from_custom_entry(model: CustomRegistryModelEntry) -> list[str]:
    """Derive kernel capability strings from a model entry's rich fields."""
    caps: set[str] = set()
    if model.tool_call is True:
        caps.add("tool_use")
    if model.reasoning is True or bool(model.support_efforts):
        caps.add("thinking")
    if model.modalities and "image" in model.modalities.get("input", []):
        caps.add("image_in")
    if model.modalities and "video" in model.modalities.get("input", []):
        caps.add("video_in")
    if model.modalities and "image" in model.modalities.get("output", []):
        caps.add("image_out")
    if model.modalities and "audio" in model.modalities.get("output", []):
        caps.add("audio_out")
    return sorted(caps)


def _resolve_max_context_size(model: CustomRegistryModelEntry) -> int:
    context = model.limit.get("context") if model.limit else None
    output = model.limit.get("output") if model.limit else None
    if isinstance(context, int) and context > 0:
        return context
    if isinstance(output, int) and output > 0:
        return output
    return CUSTOM_REGISTRY_DEFAULT_MAX_CONTEXT


def _resolve_capabilities(model: CustomRegistryModelEntry) -> list[str]:
    if _has_rich_capability_hints(model):
        return capabilities_from_custom_entry(model)
    return list(CUSTOM_REGISTRY_DEFAULT_CAPABILITIES)


def apply_custom_registry_provider(
    config: ManagedKimiConfigShape,
    entry: CustomRegistryProviderEntry,
    source: CustomRegistrySource,
) -> None:
    """Write one provider entry into *config* in place (provider + model aliases)."""
    provider_key = entry.id

    config.providers[provider_key] = {
        "type": entry.type,
        "baseUrl": entry.api,
        "apiKey": source.api_key,
        "source": {"kind": source.kind, "url": source.url, "apiKey": source.api_key},
    }

    existing_models = config.models or {}
    upstream_keys = {f"{provider_key}/{model_key}" for model_key in entry.models}
    for key, alias in list(existing_models.items()):
        if is_record(alias) and alias.get("provider") == provider_key and key not in upstream_keys:
            del existing_models[key]

    for model_key, model in entry.models.items():
        alias_key = f"{provider_key}/{model_key}"
        max_context_size = _resolve_max_context_size(model)
        capabilities = _resolve_capabilities(model)
        display_name = model.name if (isinstance(model.name, str) and model.name) else model.id
        existing = existing_models.get(alias_key) if is_record(existing_models.get(alias_key)) else {}

        remote_alias: dict[str, Any] = {
            "provider": provider_key,
            "model": model.id,
            "maxContextSize": max_context_size,
            "capabilities": capabilities,
            "displayName": display_name,
        }
        if model.support_efforts is not None:
            remote_alias["supportEfforts"] = model.support_efforts
        if model.default_effort is not None:
            remote_alias["defaultEffort"] = model.default_effort

        existing_models[alias_key] = merge_refreshed_model_alias(
            existing, remote_alias, CUSTOM_REGISTRY_MODEL_FIELDS
        )

    config.models = existing_models


def remove_custom_registry_provider(config: ManagedKimiConfigShape, provider_id: str) -> None:
    """Remove a provider and every model alias that referenced it."""
    config.providers.pop(provider_id, None)

    removed_default = False
    existing_models = config.models or {}
    for key, alias in list(existing_models.items()):
        if not is_record(alias) or alias.get("provider") != provider_id:
            continue
        del existing_models[key]
        if config.default_model == key:
            removed_default = True
    config.models = existing_models

    if removed_default:
        config.default_model = None
    if config.default_provider == provider_id:
        config.default_provider = None


def apply_custom_registry_entries(
    config: ManagedKimiConfigShape,
    entries: dict[str, CustomRegistryProviderEntry],
    source: CustomRegistrySource,
) -> None:
    """Apply every entry from one api.json import, removing stale same-URL providers."""
    surviving = {entry.id for entry in entries.values()}
    for provider_id, provider in list(config.providers.items()):
        if provider_id in surviving or not is_record(provider):
            continue
        existing_source = provider.get("source")
        if (
            is_record(existing_source)
            and existing_source.get("kind") == "apiJson"
            and existing_source.get("url") == source.url
        ):
            remove_custom_registry_provider(config, provider_id)

    for entry in entries.values():
        if entry.id in config.providers:
            remove_custom_registry_provider(config, entry.id)
        apply_custom_registry_provider(config, entry, source)


__all__ = [
    "CUSTOM_REGISTRY_DEFAULT_MAX_CONTEXT",
    "CUSTOM_REGISTRY_DEFAULT_CAPABILITIES",
    "CustomRegistryProviderType",
    "CustomRegistryApiError",
    "CustomRegistrySource",
    "CustomRegistryModelEntry",
    "CustomRegistryProviderEntry",
    "ManagedKimiConfigShape",
    "to_model_entry",
    "to_provider_entry",
    "capabilities_from_custom_entry",
    "apply_custom_registry_provider",
    "remove_custom_registry_provider",
    "apply_custom_registry_entries",
]
