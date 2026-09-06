"""OAuth token / identity data models and snake_case <-> wire conversion.

Ported from ``types.ts``. The TypeScript source keeps in-process types in
camelCase and a snake_case wire format. Idiomatic Python uses snake_case
everywhere, so the in-process models and the wire format share field names;
``token_to_wire`` / ``token_from_wire`` still perform strict, defaulted
validation so persisted JSON stays compatible with the server contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .utils import is_record


@dataclass
class TokenInfo:
    """A persisted OAuth token bundle."""

    access_token: str = ""
    refresh_token: str = ""
    expires_at: int = 0
    scope: str = ""
    token_type: str = ""
    expires_in: int = 0


@dataclass
class DeviceAuthorization:
    """RFC 8628 §3.2 device authorization response."""

    user_code: str = ""
    device_code: str = ""
    verification_uri_complete: str = ""
    verification_uri: str = ""
    expires_in: int | None = None
    interval: int = 5


@dataclass
class OAuthFlowConfig:
    """OAuth flow endpoint + client configuration."""

    name: str = ""
    oauth_host: str = ""
    client_id: str = ""


@dataclass
class DeviceHeaders:
    """Device identification for the ``X-Msh-*`` headers."""

    x_msh_platform: str = ""
    x_msh_version: str = ""
    x_msh_device_name: str = ""
    x_msh_device_model: str = ""
    x_msh_os_version: str = ""
    x_msh_device_id: str = ""


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def token_to_wire(token: TokenInfo) -> dict[str, Any]:
    """Serialize a :class:`TokenInfo` to the snake_case wire dict."""
    return {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at,
        "scope": token.scope,
        "token_type": token.token_type,
        "expires_in": token.expires_in,
    }


def token_from_wire(wire: dict[str, Any]) -> TokenInfo:
    """Parse a (possibly partial) snake_case wire dict into a :class:`TokenInfo`.

    Missing fields default the same way the TypeScript ``tokenFromWire`` does:
    strings -> ``""``, numeric fields -> ``0`` when not a finite number.
    """
    if not is_record(wire):
        return TokenInfo()

    raw_expires_at = wire.get("expires_at")
    raw_expires_in = wire.get("expires_in")
    return TokenInfo(
        access_token=wire.get("access_token") if isinstance(wire.get("access_token"), str) else "",
        refresh_token=wire.get("refresh_token") if isinstance(wire.get("refresh_token"), str) else "",
        expires_at=_as_int(raw_expires_at) if isinstance(raw_expires_at, (int, float)) else 0,
        scope=wire.get("scope") if isinstance(wire.get("scope"), str) else "",
        token_type=wire.get("token_type") if isinstance(wire.get("token_type"), str) else "",
        expires_in=_as_int(raw_expires_in) if isinstance(raw_expires_in, (int, float)) else 0,
    )


__all__ = [
    "TokenInfo",
    "DeviceAuthorization",
    "OAuthFlowConfig",
    "DeviceHeaders",
    "token_to_wire",
    "token_from_wire",
]
