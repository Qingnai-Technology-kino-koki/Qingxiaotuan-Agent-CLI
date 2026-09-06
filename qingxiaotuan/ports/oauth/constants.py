"""OAuth flow constants, ported from ``constants.ts``.

Node-side env overrides are resolved through ``os.environ`` so the module stays
loadable without a ``process`` global.
"""

from __future__ import annotations

import os

DEFAULT_KIMI_CODE_OAUTH_HOST = "https://auth.kimi.com"


def _env_override(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


KIMI_CODE_FLOW_CONFIG = {
    "name": "kimi-code",
    "oauth_host": _env_override("KIMI_CODE_OAUTH_HOST", "KIMI_OAUTH_HOST")
    or DEFAULT_KIMI_CODE_OAUTH_HOST,
    "client_id": "17e5f671-d194-4dfb-9706-5516cb48c098",
}

__all__ = ["DEFAULT_KIMI_CODE_OAUTH_HOST", "KIMI_CODE_FLOW_CONFIG"]
