"""Merge a refreshed managed model alias over an existing record in place-free style.

Ported from ``model-alias-merge.ts``. Generic record (dict) logic: remote-owned
fields win, user-added extras survive, and the user's ``overrides`` blob is
deep-cloned so a later refresh cannot mutate the caller's copy.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .utils import is_record

MANAGED_KIMI_MODEL_FIELDS: frozenset[str] = frozenset(
    {
        "provider",
        "model",
        "maxContextSize",
        "capabilities",
        "displayName",
        "protocol",
        "betaApi",
        "adaptiveThinking",
        "supportEfforts",
        "defaultEffort",
    }
)

CUSTOM_REGISTRY_MODEL_FIELDS: frozenset[str] = frozenset(
    {
        "provider",
        "model",
        "maxContextSize",
        "capabilities",
        "displayName",
        "supportEfforts",
        "defaultEffort",
    }
)


@dataclass
class ManagedKimiModelAliasOverrides:
    """User-controlled overrides for a managed model alias."""

    max_context_size: int | None = None
    max_output_size: int | None = None
    capabilities: list[str] | None = None
    display_name: str | None = None
    reasoning_key: str | None = None
    adaptive_thinking: bool | None = None
    support_efforts: list[str] | None = None
    default_effort: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ManagedKimiModelAlias:
    """A model alias within a managed Kimi config."""

    provider: str = ""
    model: str = ""
    max_context_size: int = 0
    capabilities: list[str] | None = None
    support_efforts: list[str] | None = None
    default_effort: str | None = None
    display_name: str | None = None
    protocol: str | None = None
    beta_api: bool = False
    adaptive_thinking: bool | None = None
    overrides: ManagedKimiModelAliasOverrides | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _clone_overrides(overrides: Any) -> Any:
    if not is_record(overrides):
        return None
    return copy.deepcopy(overrides)


def _user_extras(existing: dict[str, Any], remote_owned_fields: frozenset[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in existing.items():
        if key == "overrides":
            continue
        if key not in remote_owned_fields:
            out[key] = value
    return out


def merge_refreshed_model_alias(
    existing: Any,
    remote: dict[str, Any],
    remote_owned_fields: frozenset[str],
) -> dict[str, Any]:
    """Merge *remote* alias fields over *existing*, preserving user extras/overrides."""
    current = existing if is_record(existing) else {}
    overrides = _clone_overrides(current.get("overrides"))
    merged: dict[str, Any] = {
        **_user_extras(current, remote_owned_fields),
        **remote,
    }
    if overrides is not None:
        merged["overrides"] = overrides
    return merged


__all__ = [
    "MANAGED_KIMI_MODEL_FIELDS",
    "CUSTOM_REGISTRY_MODEL_FIELDS",
    "ManagedKimiModelAliasOverrides",
    "ManagedKimiModelAlias",
    "merge_refreshed_model_alias",
]
