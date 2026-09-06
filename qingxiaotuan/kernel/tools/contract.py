"""kernel.tools.contract —— 工具的声明与可执行契约。

定义「可执行工具」抽象（ExecutableTool）、单次运行描述（RunnableToolExecution）、
归一化结果（ExecutableToolResult），以及用于「读并行 / 写串行 / 路径重叠串行」细粒度
调度的 ``ToolAccesses``。

类型上尽量复用上层 ``kernel.contract`` 的 ``Tool`` / ``ToolCall`` / ``ContentPart``，
避免重复定义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

from ..contract import ContentPart, Tool, ToolCall

# ----------------------------------------------------------------- 结果类型

# 输出可以是纯文本，也可以是多模态内容块列表（简化 port：本层仅关心文本拼接）
ExecutableToolOutput = Union[str, List[ContentPart]]

DEFAULT_TOOL_RESULT_MAX_CHARS = 50_000
DEFAULT_TOOL_RESULT_MAX_RETAINED_CHARS = 10_000_000


@dataclass
class ToolResultSpill:
    """结果过大时的溢出描述（简化 port：仅保留字段，由上层截断逻辑使用）。"""

    output_path: Optional[str] = None
    total_chars: Optional[int] = None
    suffix: Optional[str] = None


@dataclass
class ToolUpdate:
    """工具执行期间的进度/状态更新（对应 TS ToolUpdate）。"""

    kind: str  # 'stdout' | 'stderr' | 'progress' | 'status' | 'custom'
    text: Optional[str] = None
    percent: Optional[float] = None
    custom_kind: Optional[str] = None
    custom_data: Any = None
    replace: bool = False


@dataclass
class ExecutableToolResult:
    """一次工具执行的最终结果（成功/失败共用，用 is_error 区分）。"""

    output: ExecutableToolOutput
    is_error: bool = False
    stop_turn: bool = False
    truncated: bool = False
    note: Optional[str] = None
    spill: Optional[ToolResultSpill] = None
    spill_exempt: bool = False
    # 以下字段为兼容历史结构保留，当前不被强制使用
    delivery: Any = None
    display: Any = None
    description: Optional[str] = None
    approval_rule: Optional[str] = None
    stop_batch_after_this: bool = False


# TS 中 ToolExecution = RunnableToolExecution | ExecutableToolResult
ToolExecution = Union["RunnableToolExecution", ExecutableToolResult]


@dataclass
class ExecutableToolContext:
    """传给 ``execute`` 的运行时上下文。"""

    turn_id: int
    tool_call_id: str
    signal: Any = None  # 兼容 asyncio.CancelledError / 自定义取消令牌，仅读 .aborted
    trace: Any = None
    metadata: Any = None
    on_update: Optional[Callable[[ToolUpdate], None]] = None
    on_foreground_task_start: Optional[Callable[[str], None]] = None


@dataclass
class RunnableToolExecution:
    """resolve_execution 返回的「可运行执行」描述。

    - approval_rule: 会话级审批规则键（用于 ask 后写 sessionApprovalRule）
    - accesses:      资源访问声明，供调度器做细粒度并发控制
    - matches_rule:  给定 ruleArgs 时判断该执行是否命中规则（可选）
    - execute:       真正执行（异步），返回 ExecutableToolResult
    """

    approval_rule: str
    execute: Callable[[ExecutableToolContext], Awaitable[ExecutableToolResult]]
    accesses: "ToolAccesses" = field(default_factory=lambda: ToolAccesses.all())
    matches_rule: Optional[Callable[[str], bool]] = None
    description: Optional[str] = None
    display: Any = None
    stop_batch_after_this: bool = False


# ----------------------------------------------------------------- 工具类型


@runtime_checkable
class ExecutableTool(Protocol):
    """可执行工具协议：在 ``Tool`` 基础上增加 resolve_execution。"""

    name: str
    description: str
    parameters: Dict[str, Any]

    def resolve_execution(self, input: Any) -> "ToolExecution | Awaitable[ToolExecution]":
        ...


ToolSource = str  # 'builtin' | 'user' | 'mcp'


@dataclass
class ToolDefinition:
    """工具的静态声明（对应 TS ToolDefinition）。"""

    name: str
    description: str
    parameters: Optional[Dict[str, Any]] = None
    source: ToolSource = "builtin"
    disclosure: str = "inline"  # 'inline' | 'deferred'
    info: Optional[Dict[str, Any]] = None


@dataclass
class ToolInfo:
    """注册表里一条记录的可查询信息。"""

    name: str
    source: ToolSource
    description: str = ""
    parameters: Optional[Dict[str, Any]] = None


# ----------------------------------------------------------------- 资源访问


@dataclass(frozen=True)
class ToolResourceAccess:
    """一条资源访问声明。

    kind='all' 表示需要全部资源（兜底：与任何其它访问都冲突，即完全串行）。
    kind='file' 时配合 operation（read/write/readwrite/search）与 path 使用。
    """

    kind: str  # 'file' | 'all'
    operation: str = "read"  # 'read' | 'write' | 'readwrite' | 'search'
    path: str = ""
    recursive: bool = False


def _normalize_path(path: str) -> str:
    """路径归一：反斜杠转正斜杠、折叠连续斜杠、小写、去尾部斜杠。"""
    normalized = re.sub(r"\\+", "/", path)
    normalized = re.sub(r"/+", "/", normalized)
    folded = normalized.lower()
    if len(folded) > 1 and folded.endswith("/"):
        folded = folded[:-1]
    return folded


def _file_op_writes(operation: str) -> bool:
    return operation in ("write", "readwrite")


def _file_operations_conflict(left_op: str, right_op: str) -> bool:
    # 任一方为写操作即认为可能冲突（写串行，写对读也串行）
    return _file_op_writes(left_op) or _file_op_writes(right_op)


def _file_overlap(left: ToolResourceAccess, right: ToolResourceAccess) -> bool:
    lp = _normalize_path(left.path)
    rp = _normalize_path(right.path)
    if lp == rp:
        return True
    l_prefix = lp + "/" if not lp.endswith("/") else lp
    r_prefix = rp + "/" if not rp.endswith("/") else rp
    return (left.recursive and rp.startswith(l_prefix)) or (
        right.recursive and lp.startswith(r_prefix)
    )


def _resource_conflict(left: ToolResourceAccess, right: ToolResourceAccess) -> bool:
    if left.kind == "all" or right.kind == "all":
        return True
    if not _file_operations_conflict(left.operation, right.operation):
        return False
    return _file_overlap(left, right)


class ToolAccesses(list):
    """资源访问集合（list 子类），并附带 ``conflict`` 静态方法做两两冲突判定。"""

    @classmethod
    def none(cls) -> "ToolAccesses":
        return cls()

    @classmethod
    def all(cls) -> "ToolAccesses":
        return cls([ToolResourceAccess(kind="all")])

    @classmethod
    def file(
        cls,
        operation: str,
        path: str,
        recursive: bool = False,
    ) -> "ToolAccesses":
        return cls([ToolResourceAccess(kind="file", operation=operation, path=path, recursive=recursive)])

    @classmethod
    def read_file(cls, path: str) -> "ToolAccesses":
        return cls.file("read", path)

    @classmethod
    def read_tree(cls, path: str) -> "ToolAccesses":
        return cls.file("read", path, recursive=True)

    @classmethod
    def write_file(cls, path: str) -> "ToolAccesses":
        return cls.file("write", path)

    @classmethod
    def write_tree(cls, path: str) -> "ToolAccesses":
        return cls.file("write", path, recursive=True)

    @classmethod
    def read_write_file(cls, path: str) -> "ToolAccesses":
        return cls.file("readwrite", path)

    @classmethod
    def read_write_tree(cls, path: str) -> "ToolAccesses":
        return cls.file("readwrite", path, recursive=True)

    @classmethod
    def search_tree(cls, path: str) -> "ToolAccesses":
        return cls.file("search", path, recursive=True)

    @staticmethod
    def conflict(left: "ToolAccesses", right: "ToolAccesses") -> bool:
        """任一写操作与另一访问重叠即冲突。"""
        return any(_resource_conflict(l, r) for l in left for r in right)


__all__ = [
    "ExecutableToolOutput",
    "DEFAULT_TOOL_RESULT_MAX_CHARS",
    "DEFAULT_TOOL_RESULT_MAX_RETAINED_CHARS",
    "ToolResultSpill",
    "ToolUpdate",
    "ExecutableToolResult",
    "ExecutableToolContext",
    "RunnableToolExecution",
    "ExecutableTool",
    "ToolSource",
    "ToolDefinition",
    "ToolInfo",
    "ToolResourceAccess",
    "ToolAccesses",
]
