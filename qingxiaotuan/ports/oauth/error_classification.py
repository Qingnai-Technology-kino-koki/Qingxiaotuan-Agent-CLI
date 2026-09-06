"""Pure OAuth flow parsing + HTTP-status error classification.

Ported from the validation/classification logic in ``oauth.ts``. Network I/O
(``postForm``/``fetch``) is omitted; these helpers operate on already-parsed
payloads so they are fully unit-testable without a transport.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .api_error import extract_api_error_message
from .errors import (
    OAuthError,
    OAuthUnauthorizedError,
    RetryableRefreshError,
)
from .types import DeviceAuthorization, TokenInfo
from .utils import is_record

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass
class DevicePollResult:
    """Outcome of one device-token poll, mirroring the TS union."""

    kind: str  # 'success' | 'pending' | 'expired' | 'denied'
    token: TokenInfo | None = None
    error_code: str = ""
    description: str = ""


def parse_token_response(payload: dict[str, Any], now: float | None = None) -> TokenInfo:
    """Validate a token endpoint payload and build a :class:`TokenInfo`.

    Raises :class:`OAuthError` if any of ``access_token`` / ``refresh_token`` /
    ``expires_in`` is missing or invalid.
    """
    if not is_record(payload):
        raise OAuthError("OAuth response was not a JSON object")

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthError("OAuth response missing access_token")

    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise OAuthError("OAuth response missing refresh_token")

    expires_in = payload.get("expires_in")
    try:
        expires_in_num = float(expires_in)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise OAuthError("OAuth response missing or invalid expires_in")
    if not expires_in_num.is_integer() or expires_in_num <= 0:
        raise OAuthError("OAuth response missing or invalid expires_in")

    base = time.time() if now is None else now
    return TokenInfo(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=int(base) + int(expires_in_num),
        scope=payload.get("scope") if isinstance(payload.get("scope"), str) else "",
        token_type=payload.get("token_type") if isinstance(payload.get("token_type"), str) else "Bearer",
        expires_in=int(expires_in_num),
    )


def parse_device_authorization(data: dict[str, Any]) -> DeviceAuthorization:
    """Validate a device-authorization response and build a :class:`DeviceAuthorization`."""
    if not is_record(data):
        raise OAuthError("Device authorization response was not a JSON object")

    user_code = data.get("user_code")
    if not isinstance(user_code, str) or not user_code:
        raise OAuthError("Device authorization response missing user_code")
    device_code = data.get("device_code")
    if not isinstance(device_code, str) or not device_code:
        raise OAuthError("Device authorization response missing device_code")
    verification_uri_complete = data.get("verification_uri_complete")
    if not isinstance(verification_uri_complete, str) or not verification_uri_complete:
        raise OAuthError("Device authorization response missing verification_uri_complete")

    raw_expires = data.get("expires_in")
    expires_in = int(raw_expires) if isinstance(raw_expires, (int, float)) else None
    raw_interval = data.get("interval")
    interval = int(raw_interval) if isinstance(raw_interval, (int, float)) else 5

    return DeviceAuthorization(
        user_code=user_code,
        device_code=device_code,
        verification_uri_complete=verification_uri_complete,
        verification_uri=data.get("verification_uri") if isinstance(data.get("verification_uri"), str) else "",
        expires_in=expires_in,
        interval=interval,
    )


def classify_refresh_error(
    status: int,
    error_code: str = "",
    detail: str | None = None,
) -> OAuthError:
    """Classify a token-refresh HTTP result into the right error subclass.

    401/403 or ``invalid_grant`` -> :class:`OAuthUnauthorizedError`;
    429 / 5xx -> :class:`RetryableRefreshError`; otherwise :class:`OAuthError`.
    """
    if status in (401, 403) or error_code == "invalid_grant":
        return OAuthUnauthorizedError(detail or "Token refresh unauthorized.")
    if status in RETRYABLE_STATUSES:
        return RetryableRefreshError(detail or f"Token refresh failed (HTTP {status}).")
    return OAuthError(detail or f"Token refresh failed (HTTP {status}).")


def classify_poll_result(status: int, data: dict[str, Any]) -> DevicePollResult:
    """Map a device-token poll HTTP result onto a :class:`DevicePollResult`.

    Raises :class:`OAuthError` for unexpected server errors / unknown codes;
    returns a typed result for the expected pending/expired/denied states.
    """
    if status == 200 and isinstance(data.get("access_token"), str):
        return DevicePollResult(kind="success", token=parse_token_response(data))

    if status >= 500:
        raise OAuthError(
            f"Device token polling server error (HTTP {status}): "
            f"{extract_api_error_message(data) or 'unknown'}"
        )

    error_code = data.get("error") if isinstance(data.get("error"), str) else "unknown_error"
    detail = extract_api_error_message(data)
    description = (
        data.get("error_description")
        if isinstance(data.get("error_description"), str)
        else (detail or "")
    )
    if error_code in ("authorization_pending", "slow_down"):
        return DevicePollResult(kind="pending", error_code=error_code, description=description)
    if error_code == "expired_token":
        return DevicePollResult(kind="expired")
    if error_code == "access_denied":
        return DevicePollResult(kind="denied", description=description)
    raise OAuthError(
        f"Device token polling failed (HTTP {status}): "
        f"{detail or f'{error_code} {description}'}"
    )


__all__ = [
    "RETRYABLE_STATUSES",
    "DevicePollResult",
    "parse_token_response",
    "parse_device_authorization",
    "classify_refresh_error",
    "classify_poll_result",
]
