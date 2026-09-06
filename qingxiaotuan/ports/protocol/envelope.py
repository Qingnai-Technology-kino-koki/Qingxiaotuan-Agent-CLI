"""Protocol envelope (success / error) types and helpers.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

The TS source models the envelope with a generic ``Envelope<T>`` interface and
two constructors ``okEnvelope`` / ``errEnvelope``. In Python (no compile-time
generics needed for wire shapes) we use a single dataclass whose ``data`` field
is ``Any``. The wire shape is byte-identical to the TS ``JSON.stringify``
output: ``details`` and ``stack`` are omitted from the wire when ``None``
(``JSON.stringify`` drops ``undefined``), while ``data`` is always present
(``null`` for error envelopes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Envelope:
    """A daemon response envelope.

    Fields mirror the TS interface exactly:
      code        integer status (0 == success)
      msg         human / machine message string
      data        payload (``null`` on error envelopes)
      request_id  opaque per-request correlation id
      details     optional structured error details
      stack       optional captured stack trace (omitted on the wire when None)
    """

    code: int
    msg: str
    data: Any
    request_id: str
    details: Optional[Any] = None
    stack: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the canonical wire dict.

        ``details`` / ``stack`` are dropped when ``None`` so the output matches
        the TS ``JSON.stringify`` shape byte-for-byte.
        """
        out: dict[str, Any] = {
            "code": self.code,
            "msg": self.msg,
            "data": self.data,
            "request_id": self.request_id,
        }
        if self.details is not None:
            out["details"] = self.details
        if self.stack is not None:
            out["stack"] = self.stack
        return out

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)


def ok_envelope(data: Any, request_id: str) -> Envelope:
    """Build a success envelope (code 0, msg ``"success"``)."""
    return Envelope(code=0, msg="success", data=data, request_id=request_id)


def err_envelope(
    code: int,
    msg: str,
    request_id: str,
    stack: Optional[str] = None,
) -> Envelope:
    """Build an error envelope.

    When ``stack`` is provided it is surfaced verbatim on the wire; when omitted
    the field is absent so the wire shape stays byte-identical to
    ``{code, msg, data: null, request_id}``.
    """
    return Envelope(
        code=code,
        msg=msg,
        data=None,
        request_id=request_id,
        stack=stack,
    )


def parse_envelope(raw: dict[str, Any]) -> Envelope:
    """Parse a wire dict into an :class:`Envelope`, validating the shape.

    Raises ``ValueError`` on malformed input (non-integer code, missing fields).
    """
    if not isinstance(raw, dict):
        raise ValueError("envelope must be an object")
    code = raw.get("code")
    msg = raw.get("msg")
    data = raw.get("data")
    request_id = raw.get("request_id")
    if not isinstance(code, int) or isinstance(code, bool):
        raise ValueError("envelope.code must be an integer")
    if not isinstance(msg, str):
        raise ValueError("envelope.msg must be a string")
    if not isinstance(request_id, str):
        raise ValueError("envelope.request_id must be a string")
    if "data" not in raw:
        raise ValueError("envelope.data is required")
    details = raw.get("details")
    stack = raw.get("stack")
    return Envelope(
        code=code,
        msg=msg,
        data=data,
        request_id=request_id,
        details=details,
        stack=stack,
    )
