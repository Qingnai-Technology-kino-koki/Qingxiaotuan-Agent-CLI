"""Request id helpers (ULID-based).

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

The TS source depends on the ``ulid`` npm package. We re-implement ULID
generation and validation with the stdlib only (Crockford base32, 128-bit
value: 48-bit unix-ms timestamp + 80-bit randomness), so no third-party
dependency is introduced.
"""

from __future__ import annotations

import os
import re
import time

# Crockford base32 (excludes I, L, O, U).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}
ULID_REGEX = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def is_ulid(value: str) -> bool:
    """Return ``True`` if ``value`` is a well-formed ULID."""
    if not isinstance(value, str) or len(value) != 26:
        return False
    if not ULID_REGEX.match(value):
        return False
    # Confirm every symbol decodes (regex already restricts to the alphabet).
    return all(c in _DECODE for c in value)


def _encode(value: int) -> str:
    """Encode a 128-bit integer as a 26-char Crockford base32 ULID."""
    chars: list[str] = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    """Generate a new ULID."""
    timestamp_ms = int(time.time() * 1000)
    if timestamp_ms >= (1 << 48):
        # Clamp extremely-far-future clocks into the 48-bit field.
        timestamp_ms = (1 << 48) - 1
    randomness = int.from_bytes(os.urandom(10), "big")  # 80 bits
    value = (timestamp_ms << 80) | randomness
    return _encode(value)


def parse_or_generate_request_id(header_value: str | None) -> str:
    """Return ``header_value`` if it is a valid ULID, else generate a new one."""
    if isinstance(header_value, str) and is_ulid(header_value):
        return header_value
    return ulid()
