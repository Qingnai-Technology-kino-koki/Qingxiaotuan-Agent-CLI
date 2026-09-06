"""Tool input / result display structures.

Ported from the upstream TypeScript reference implementation (attribution: see ``NOTICE``).

The TS source uses ``z.discriminatedUnion('kind', ...)``. We model each member
as a dataclass carrying its literal ``kind`` and provide discriminated
``parse_tool_input_display`` / ``parse_tool_result_display`` helpers. A shared
builder validates required fields (presence + basic type + non-empty required
strings) and ignores unknown keys, mirroring the permissive
``tool_input_display: z.unknown()`` pass-through semantics for unrecognized
shapes. Required string/int fields carry no default so empty/missing values are
rejected, matching the TS ``z.string().min(1)`` / ``z.number()`` contracts.

``kind`` is declared last in every variant so dataclass field ordering stays
valid (fields with defaults must follow fields without defaults).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FileIoOperation(str, Enum):
    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    GLOB = "glob"
    GREP = "grep"


class GoalStartMode(str, Enum):
    MANUAL = "manual"
    YOLO = "yolo"


@dataclass
class _Base:
    """Mixin providing ``to_dict`` (omits ``None``) for display variants."""

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import fields

        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            out[f.name] = value
        return out


# --- ToolInputDisplay variants -------------------------------------------------


@dataclass
class ToolInputCommand(_Base):
    command: str
    cwd: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    kind: str = "command"


@dataclass
class ToolInputFileIo(_Base):
    operation: str
    path: str
    detail: Optional[str] = None
    content: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    kind: str = "file_io"


@dataclass
class ToolInputDiff(_Base):
    path: str
    before: str
    after: str
    hunks: Optional[int] = None
    kind: str = "diff"


@dataclass
class ToolInputSearch(_Base):
    query: str
    scope: Optional[str] = None
    kind: str = "search"


@dataclass
class ToolInputUrlFetch(_Base):
    url: str
    method: Optional[str] = None
    kind: str = "url_fetch"


@dataclass
class ToolInputAgentCall(_Base):
    agent_name: str
    prompt: str
    background: Optional[bool] = None
    kind: str = "agent_call"


@dataclass
class ToolInputSkillCall(_Base):
    skill_name: str
    args: Optional[str] = None
    kind: str = "skill_call"


@dataclass
class _TodoItem(_Base):
    title: str
    status: str


@dataclass
class ToolInputTodoList(_Base):
    items: list[_TodoItem] = field(default_factory=list)
    kind: str = "todo_list"


@dataclass
class ToolInputTask(_Base):
    task_id: str
    status: str
    description: str
    task_kind: Optional[str] = None
    kind: str = "task"


@dataclass
class ToolInputTaskStop(_Base):
    task_id: str
    task_description: str
    kind: str = "task_stop"


@dataclass
class _PlanOption(_Base):
    label: str
    description: str


@dataclass
class ToolInputPlanReview(_Base):
    plan: str
    path: Optional[str] = None
    options: Optional[list[_PlanOption]] = None
    kind: str = "plan_review"


@dataclass
class ToolInputGoalStart(_Base):
    objective: str
    completionCriterion: Optional[str] = None
    mode: str = "manual"
    kind: str = "goal_start"


@dataclass
class ToolInputGeneric(_Base):
    summary: str
    detail: Optional[Any] = None
    kind: str = "generic"


TOOL_INPUT_TYPES: dict[str, type] = {
    "command": ToolInputCommand,
    "file_io": ToolInputFileIo,
    "diff": ToolInputDiff,
    "search": ToolInputSearch,
    "url_fetch": ToolInputUrlFetch,
    "agent_call": ToolInputAgentCall,
    "skill_call": ToolInputSkillCall,
    "todo_list": ToolInputTodoList,
    "task": ToolInputTask,
    "task_stop": ToolInputTaskStop,
    "plan_review": ToolInputPlanReview,
    "goal_start": ToolInputGoalStart,
    "generic": ToolInputGeneric,
}


# --- ToolResultDisplay variants ------------------------------------------------


@dataclass
class ToolResultCommandOutput(_Base):
    exit_code: int
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    kind: str = "command_output"


@dataclass
class ToolResultFileContent(_Base):
    path: str
    content: str
    range: Optional[dict[str, int]] = None
    truncated: Optional[bool] = None
    kind: str = "file_content"


@dataclass
class ToolResultDiff(_Base):
    path: str
    before: str
    after: str
    hunks: Optional[int] = None
    kind: str = "diff"


@dataclass
class _SearchMatch(_Base):
    file: str
    line: int
    text: str


@dataclass
class ToolResultSearchResults(_Base):
    query: str
    matches: list[_SearchMatch] = field(default_factory=list)
    kind: str = "search_results"


@dataclass
class ToolResultUrlContent(_Base):
    url: str
    status: int
    preview: Optional[str] = None
    content_type: Optional[str] = None
    kind: str = "url_content"


@dataclass
class ToolResultAgentSummary(_Base):
    agent_name: str
    result: Optional[str] = None
    steps: Optional[int] = None
    kind: str = "agent_summary"


@dataclass
class ToolResultTask(_Base):
    task_id: str
    status: str
    description: str
    kind: str = "task"


@dataclass
class ToolResultTodoList(_Base):
    items: list[_TodoItem] = field(default_factory=list)
    kind: str = "todo_list"


@dataclass
class ToolResultStructured(_Base):
    data: Any = None
    kind: str = "structured"


@dataclass
class ToolResultText(_Base):
    text: str
    truncated: Optional[bool] = None
    kind: str = "text"


@dataclass
class ToolResultError(_Base):
    message: str
    code: Optional[str] = None
    kind: str = "error"


@dataclass
class ToolResultGeneric(_Base):
    summary: str
    detail: Optional[Any] = None
    kind: str = "generic"


TOOL_RESULT_TYPES: dict[str, type] = {
    "command_output": ToolResultCommandOutput,
    "file_content": ToolResultFileContent,
    "diff": ToolResultDiff,
    "search_results": ToolResultSearchResults,
    "url_content": ToolResultUrlContent,
    "agent_summary": ToolResultAgentSummary,
    "task": ToolResultTask,
    "todo_list": ToolResultTodoList,
    "structured": ToolResultStructured,
    "text": ToolResultText,
    "error": ToolResultError,
    "generic": ToolResultGeneric,
}


def _build(cls: type, raw: dict[str, Any]) -> Any:
    import typing
    from dataclasses import MISSING, fields

    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        # "required" == no default value and no default_factory.
        required = (f.default is MISSING) and (f.default_factory is MISSING)
        if f.name == "kind":
            kwargs[f.name] = f.default
            continue
        if f.name in raw:
            value = raw[f.name]
        elif required:
            raise ValueError(f"{cls.__name__}.{f.name} is required")
        elif f.default_factory is not MISSING:
            kwargs[f.name] = f.default_factory()
        else:
            kwargs[f.name] = f.default
            continue
        kwargs[f.name] = _coerce(f.name, hints.get(f.name, f.type), value, required)
    return cls(**kwargs)  # type: ignore[call-arg]


def _coerce(name: str, annotation: Any, value: Any, required: bool) -> Any:
    import typing

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if annotation is str:
        if not isinstance(value, str) or (required and value == ""):
            raise ValueError(f"{name} must be a non-empty string")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value
    if origin in (list,):
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a list")
        item_type = args[0] if args else None
        if item_type is not None and hasattr(item_type, "__dataclass_fields__"):
            return [_build(item_type, item) for item in value]
        return value
    if origin in (dict,):
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be an object")
        return value
    # Any / Optional[...] / unknown: pass through.
    return value


def parse_tool_input_display(raw: dict[str, Any]) -> Any:
    """Dispatch on ``kind`` to the matching :class:`ToolInputDisplay` variant."""
    if not isinstance(raw, dict):
        raise ValueError("tool_input_display must be an object")
    kind = raw.get("kind")
    cls = TOOL_INPUT_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown tool_input_display kind: {kind!r}")
    return _build(cls, raw)


def parse_tool_result_display(raw: dict[str, Any]) -> Any:
    """Dispatch on ``kind`` to the matching :class:`ToolResultDisplay` variant."""
    if not isinstance(raw, dict):
        raise ValueError("tool_result_display must be an object")
    kind = raw.get("kind")
    cls = TOOL_RESULT_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown tool_result_display kind: {kind!r}")
    return _build(cls, raw)
