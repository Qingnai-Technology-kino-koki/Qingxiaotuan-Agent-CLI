"""Approval request / response types.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

``tool_input_display`` is ``z.unknown()`` in the TS source, so it is carried
through as an opaque value (typically a :class:`display.ToolInputDisplay`
dict). We keep it as ``Any`` for faithful pass-through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional

from .time import normalize_iso_date_time


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ApprovalScope(str, Enum):
    SESSION = "session"


@dataclass
class ApprovalRequest:
    approval_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    action: str
    tool_input_display: Any
    created_at: str
    expires_at: str
    turn_id: Optional[int] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ApprovalRequest":
        _require_str(raw, "approval_id")
        _require_str(raw, "session_id")
        _require_str(raw, "tool_call_id")
        _require_str(raw, "tool_name")
        _require_str(raw, "action")
        if "tool_input_display" not in raw:
            raise ValueError("approval.tool_input_display is required")
        turn_id = raw.get("turn_id")
        if turn_id is not None and not isinstance(turn_id, int):
            raise ValueError("approval.turn_id must be an integer")
        return cls(
            approval_id=raw["approval_id"],
            session_id=raw["session_id"],
            tool_call_id=raw["tool_call_id"],
            tool_name=raw["tool_name"],
            action=raw["action"],
            tool_input_display=raw["tool_input_display"],
            created_at=normalize_iso_date_time(raw["created_at"]),
            expires_at=normalize_iso_date_time(raw["expires_at"]),
            turn_id=turn_id,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "approval_id": self.approval_id,
            "session_id": self.session_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "action": self.action,
            "tool_input_display": self.tool_input_display,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }
        if self.turn_id is not None:
            out["turn_id"] = self.turn_id
        return out


@dataclass
class ApprovalResponse:
    decision: ApprovalDecision
    scope: Optional[ApprovalScope] = None
    feedback: Optional[str] = None
    selected_label: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ApprovalResponse":
        if "decision" not in raw:
            raise ValueError("approval response.decision is required")
        try:
            decision = ApprovalDecision(raw["decision"])
        except ValueError as exc:
            raise ValueError(f"invalid approval decision: {raw['decision']!r}") from exc
        scope = raw.get("scope")
        if scope is not None:
            try:
                scope = ApprovalScope(scope)
            except ValueError as exc:
                raise ValueError(f"invalid approval scope: {scope!r}") from exc
        feedback = raw.get("feedback")
        selected_label = raw.get("selected_label")
        if feedback is not None and not isinstance(feedback, str):
            raise ValueError("approval response.feedback must be a string")
        if selected_label is not None and not isinstance(selected_label, str):
            raise ValueError("approval response.selected_label must be a string")
        return cls(
            decision=decision,
            scope=scope,
            feedback=feedback,
            selected_label=selected_label,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"decision": self.decision.value}
        if self.scope is not None:
            out["scope"] = self.scope.value
        if self.feedback is not None:
            out["feedback"] = self.feedback
        if self.selected_label is not None:
            out["selected_label"] = self.selected_label
        return out


def _require_str(raw: dict[str, Any], key: str) -> None:
    if not isinstance(raw.get(key), str) or raw[key] == "":
        raise ValueError(f"approval.{key} is required")
