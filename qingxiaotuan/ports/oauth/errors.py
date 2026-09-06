"""OAuth error hierarchy.

All errors derive from :class:`OAuthError`. The distinguishing subclasses let
callers react appropriately:

- ``OAuthUnauthorizedError``: 401/403 from token endpoint -> refresh_token or
  credentials are bad; drive the user through /login again.
- ``OAuthAccessDeniedError``: user denied the authorization request on the
  consent page (``access_denied``); surface as a user-initiated cancel.
- ``OAuthConnectionError``: transport-level OAuth request failure; callers may
  retry the operation.
- ``DeviceCodeExpiredError``: device_code TTL ran out before the user approved;
  restart the device flow.
- ``DeviceCodeTimeoutError``: local wall-clock budget exhausted before the user
  completed approval.
- ``RetryableRefreshError``: 429 / 5xx from token endpoint; the refresh helper
  retries with exponential backoff before surfacing this.
"""

from __future__ import annotations


class OAuthError(Exception):
    """Base class for all OAuth-related errors."""


class OAuthUnauthorizedError(OAuthError):
    """401/403 from the token endpoint (bad or revoked credentials)."""


class OAuthAccessDeniedError(OAuthError):
    """User denied the authorization request on the consent page."""

    def __init__(self, message: str = "Authorization denied.") -> None:
        super().__init__(message)


class OAuthConnectionError(OAuthError):
    """Transport-level OAuth request failure (DNS, refused, timeout, TLS)."""


class DeviceCodeExpiredError(OAuthError):
    """device_code TTL ran out before the user approved."""

    def __init__(self, message: str = "Device code expired.") -> None:
        super().__init__(message)


class DeviceCodeTimeoutError(OAuthError):
    """Local wall-clock budget exhausted before approval completed."""

    def __init__(self, message: str = "Device authorization timed out locally.") -> None:
        super().__init__(message)


class RetryableRefreshError(OAuthError):
    """429 / 5xx from the token endpoint after retries were exhausted."""


__all__ = [
    "OAuthError",
    "OAuthUnauthorizedError",
    "OAuthAccessDeniedError",
    "OAuthConnectionError",
    "DeviceCodeExpiredError",
    "DeviceCodeTimeoutError",
    "RetryableRefreshError",
]
