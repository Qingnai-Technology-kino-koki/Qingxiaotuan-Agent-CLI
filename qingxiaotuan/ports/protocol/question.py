"""Question (interactive prompt) request / answer types.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union

from .time import normalize_iso_date_time


class QuestionAnswerMethod(str, Enum):
    ENTER = "enter"
    SPACE = "space"
    NUMBER_KEY = "number_key"
    CLICK = "click"


@dataclass
class QuestionOption:
    id: str
    label: str
    description: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuestionOption":
        if not isinstance(raw.get("id"), str) or raw["id"] == "":
            raise ValueError("question option.id is required")
        if not isinstance(raw.get("label"), str) or raw["label"] == "":
            raise ValueError("question option.label is required")
        return cls(id=raw["id"], label=raw["label"], description=raw.get("description"))


@dataclass
class QuestionItem:
    id: str
    question: str
    options: list[QuestionOption]
    header: Optional[str] = None
    body: Optional[str] = None
    multi_select: Optional[bool] = None
    allow_other: Optional[bool] = None
    other_label: Optional[str] = None
    other_description: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuestionItem":
        if not isinstance(raw.get("id"), str) or raw["id"] == "":
            raise ValueError("question.id is required")
        if not isinstance(raw.get("question"), str) or raw["question"] == "":
            raise ValueError("question.question is required")
        options = raw.get("options")
        if not isinstance(options, list) or not (2 <= len(options) <= 4):
            raise ValueError("question.options must be 2..4 items")
        return cls(
            id=raw["id"],
            question=raw["question"],
            options=[QuestionOption.from_dict(o) for o in options],
            header=raw.get("header"),
            body=raw.get("body"),
            multi_select=raw.get("multi_select"),
            allow_other=raw.get("allow_other"),
            other_label=raw.get("other_label"),
            other_description=raw.get("other_description"),
        )


@dataclass
class QuestionRequest:
    question_id: str
    session_id: str
    questions: list[QuestionItem]
    created_at: str
    turn_id: Optional[int] = None
    tool_call_id: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuestionRequest":
        for key in ("question_id", "session_id"):
            if not isinstance(raw.get(key), str) or raw[key] == "":
                raise ValueError(f"question.{key} is required")
        questions = raw.get("questions")
        if not isinstance(questions, list) or not (1 <= len(questions) <= 4):
            raise ValueError("question.questions must be 1..4 items")
        turn_id = raw.get("turn_id")
        if turn_id is not None and not isinstance(turn_id, int):
            raise ValueError("question.turn_id must be an integer")
        tool_call_id = raw.get("tool_call_id")
        if tool_call_id is not None and not isinstance(tool_call_id, str):
            raise ValueError("question.tool_call_id must be a string")
        return cls(
            question_id=raw["question_id"],
            session_id=raw["session_id"],
            questions=[QuestionItem.from_dict(q) for q in questions],
            created_at=normalize_iso_date_time(raw["created_at"]),
            turn_id=turn_id,
            tool_call_id=tool_call_id,
        )


@dataclass
class QuestionAnswerSingle:
    kind: Literal["single"] = "single"
    option_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "single", "option_id": self.option_id}


@dataclass
class QuestionAnswerMulti:
    kind: Literal["multi"] = "multi"
    option_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "multi", "option_ids": self.option_ids}


@dataclass
class QuestionAnswerOther:
    kind: Literal["other"] = "other"
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "other", "text": self.text}


@dataclass
class QuestionAnswerMultiWithOther:
    kind: Literal["multi_with_other"] = "multi_with_other"
    option_ids: list[str] = field(default_factory=list)
    other_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "multi_with_other", "option_ids": self.option_ids, "other_text": self.other_text}


@dataclass
class QuestionAnswerSkipped:
    kind: Literal["skipped"] = "skipped"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "skipped"}


QuestionAnswer = Union[
    QuestionAnswerSingle,
    QuestionAnswerMulti,
    QuestionAnswerOther,
    QuestionAnswerMultiWithOther,
    QuestionAnswerSkipped,
]


_ANSWER_TYPES: dict[str, type] = {
    "single": QuestionAnswerSingle,
    "multi": QuestionAnswerMulti,
    "other": QuestionAnswerOther,
    "multi_with_other": QuestionAnswerMultiWithOther,
    "skipped": QuestionAnswerSkipped,
}


def parse_question_answer(raw: dict[str, Any]) -> QuestionAnswer:
    if not isinstance(raw, dict) or "kind" not in raw:
        raise ValueError("question answer requires a 'kind'")
    cls = _ANSWER_TYPES.get(raw["kind"])
    if cls is None:
        raise ValueError(f"unknown question answer kind: {raw['kind']!r}")
    if cls is QuestionAnswerSingle:
        return QuestionAnswerSingle(option_id=raw.get("option_id", ""))
    if cls is QuestionAnswerMulti:
        return QuestionAnswerMulti(option_ids=list(raw.get("option_ids", [])))
    if cls is QuestionAnswerOther:
        return QuestionAnswerOther(text=raw.get("text", ""))
    if cls is QuestionAnswerMultiWithOther:
        return QuestionAnswerMultiWithOther(
            option_ids=list(raw.get("option_ids", [])), other_text=raw.get("other_text", "")
        )
    return QuestionAnswerSkipped()


@dataclass
class QuestionResponse:
    answers: dict[str, QuestionAnswer]
    method: Optional[QuestionAnswerMethod] = None
    note: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuestionResponse":
        answers = raw.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("question response.answers is required")
        parsed: dict[str, QuestionAnswer] = {}
        for qid, ans in answers.items():
            parsed[qid] = parse_question_answer(ans)
        method = raw.get("method")
        if method is not None:
            try:
                method = QuestionAnswerMethod(method)
            except ValueError as exc:
                raise ValueError(f"invalid question answer method: {method!r}") from exc
        return cls(answers=parsed, method=method, note=raw.get("note"))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "answers": {qid: ans.to_dict() for qid, ans in self.answers.items()}
        }
        if self.method is not None:
            out["method"] = self.method.value
        if self.note is not None:
            out["note"] = self.note
        return out
