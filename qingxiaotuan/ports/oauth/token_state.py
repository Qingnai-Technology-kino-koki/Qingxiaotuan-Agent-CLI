"""Three-state view of what storage holds for a provider.

Ported from ``token-state.ts``. A *revoked* record is a tombstone: the on-disk
file exists but the prior ``refresh_token`` was rejected (401/403). A fresh
process with no in-memory state needs to see "previously logged in, now needs
re-login" rather than "never logged in". The wire format and ``TokenInfo`` are
unchanged -- a revoked record is still persisted as an all-empty token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .types import TokenInfo


@dataclass
class TokenState:
    """Internal three-state classification of stored token data."""

    kind: Literal["valid", "revoked", "missing"]
    token: TokenInfo | None = None
    scope: str = ""
    token_type: str = ""


def classify_token(token: TokenInfo | None) -> TokenState:
    """Classify a stored token (or its absence) into a :class:`TokenState`."""
    if token is None:
        return TokenState(kind="missing")
    if not token.access_token:
        return TokenState(kind="revoked", scope=token.scope, token_type=token.token_type)
    return TokenState(kind="valid", token=token)


def revoked_tombstone(prior: TokenInfo) -> TokenInfo:
    """Build an all-empty ``TokenInfo`` carrying ``prior``'s identity fields."""
    return TokenInfo(scope=prior.scope, token_type=prior.token_type)


__all__ = ["TokenState", "classify_token", "revoked_tombstone"]
