"""ISO-8601 datetime helpers.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

The TS ``isoDateTimeSchema`` refines a string against an ISO-8601 regex and
normalizes it to canonical ``toISOString()`` form (``Z`` suffix, millisecond
precision). We keep the same contract: :func:`normalize_iso_date_time` validates
and normalizes; :func:`is_iso_date_time` is the pure predicate.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

ISO_8601_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?"
    r"(?:Z|[+-]\d{2}(?::?\d{2})?)$"
)


def is_iso_date_time(value: str) -> bool:
    """Return ``True`` if ``value`` is a syntactically valid ISO-8601 datetime."""
    if not isinstance(value, str) or not ISO_8601_REGEX.match(value):
        return False
    try:
        datetime.fromisoformat(_py_normalize(value))
    except ValueError:
        return False
    return True


def normalize_iso_date_time(value: str) -> str:
    """Validate ``value`` and normalize to canonical UTC ``toISOString`` form.

    Raises ``ValueError`` if the input is not a valid ISO-8601 datetime.
    """
    if not isinstance(value, str) or not ISO_8601_REGEX.match(value):
        raise ValueError("must be an ISO 8601 datetime string")
    try:
        dt = datetime.fromisoformat(_py_normalize(value))
    except ValueError as exc:  # pragma: no cover - regex already constrains this
        raise ValueError("invalid ISO 8601 datetime") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        "%03dZ" % (dt.microsecond // 1000)
    )


def _py_normalize(value: str) -> str:
    """Make an ISO string parseable by ``datetime.fromisoformat`` (Py3.11+)."""
    v = value
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    # Collapse +08:00 / +0800 to +08:00 form accepted by fromisoformat.
    return v


def now_iso_date_time() -> str:
    """Current UTC time in canonical ``toISOString`` form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        "%03dZ" % (datetime.now(timezone.utc).microsecond // 1000)
    )


# Type alias mirroring ``IsoDateTime = string`` in the TS source.
IsoDateTime = str
