"""沙箱 4 层滤网共享决策模型。

一层滤网的实质: 输入一次「执行请求」, 输出一个 Verdict ——
放行(allow) / 确认(confirm) / 隔离(isolate) / 拒绝(deny)。
四层按 L0→L3 顺序叠加, 每一层都是独立滤网, 只可能把请求"收窄"得更安全, 不会放宽
(逐层 fail-closed)。最终 Verdict 取全部滤网里最严格者的并集。

对比 TraeWork/CodeX "丢进容器跑"的单层方案:
  本子系统把「安全决定」从"运行时碰运气"上移到"确定性滤网栈" —— 即使没有容器,
  L0/L1 已把致命命令与越权写入挡在门外; 有容器时 L3 再叠加真正的内核/容器隔离。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Action(str, Enum):
    """滤网输出动作, 按严格程度排序 (DENY 最严)。"""

    ALLOW = "allow"      # 放行, 无需沙箱
    CONFIRM = "confirm"  # 需人工确认后方可继续
    ISOLATE = "isolate"  # 放行, 但必须在隔离环境执行
    DENY = "deny"        # 拒绝 (fail-closed)

    @staticmethod
    def _rank(a: "Action") -> int:
        return {
            Action.DENY: 3,
            Action.ISOLATE: 2,
            Action.CONFIRM: 1,
            Action.ALLOW: 0,
        }.get(a, 0)

    def stricter_than(self, other: "Action") -> bool:
        return self._rank(self) > self._rank(other)


# 常见严重度
SEV = {
    "none": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}


@dataclass
class Verdict:
    """一次滤网评估的结论 (可被后续滤网继续收紧)。"""

    action: Action = Action.ALLOW
    layer: str = ""                               # 命中的滤网层 (base/intent/trust/resource/hard)
    reason: str = ""
    severity: str = SEV["none"]
    suggestions: List[str] = field(default_factory=list)

    # ---------- 由 L2/L3 填充的执行元数据 ----------
    isolate: bool = False                         # 需要隔离执行
    network: bool = True                          # 是否允许网络
    backend: str = ""                             # 后端名 (docker/bwrap/jobobject/...)
    enforced: bool = False                        # 是否强制定级: 后端不可用则视为拒绝
    backend_available: bool = False
    memory_mb: int = 0                            # 0 = 不限
    timeout: float = 0.0                          # 0 = 用调用方默认
    workspace_mode: str = "direct"                # direct | copy_diff_apply
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocks(self) -> bool:
        """该结论是否已构成拒绝 (短路用)。"""
        return self.action == Action.DENY

    @property
    def allows_without_check(self) -> bool:
        """无需任何确认/隔离即可放行 (仅 ALLOW)。"""
        return self.action == Action.ALLOW and not self.isolate

    def merged(self, other: "Verdict") -> "Verdict":
        """把另一层滤网的结果合并进来: 取更严 action, 覆写/拼接原因与元数据。

        元数据字段: 若 other 里显式非默认, 则覆写当前值 (逐层只可能更严)。
        """
        merged = copy.copy(self)
        if other.action.stricter_than(merged.action):
            merged.action = other.action
        if other.layer:
            merged.layer = other.layer
        if other.severity not in (SEV["none"], merged.severity):
            if Action._rank(other.action) >= Action._rank(merged.action):
                merged.severity = other.severity
        if other.reason:
            merged.reason = other.reason
        merged.suggestions = list(dict.fromkeys(self.suggestions + other.suggestions))
        # 布尔元数据: 任一滤网要求隔离/禁网 → 保持真
        merged.isolate = self.isolate or other.isolate
        merged.network = self.network and other.network
        # 标量元数据: 非默认值覆写
        for attr in ("backend", "workspace_mode", "enforced", "backend_available"):
            val = getattr(other, attr)
            default = _DEFAULT_ATTRS.get(attr)
            if val != default:
                setattr(merged, attr, val)
        if other.memory_mb:
            merged.memory_mb = other.memory_mb
        if other.timeout:
            merged.timeout = other.timeout
        return merged

    def describe(self) -> str:
        parts = [f"[{self.action.value}]"]
        if self.layer:
            parts.append(f"L{self.layer}")
        if self.reason:
            parts.append(self.reason)
        if self.suggestions:
            parts.append("建议: " + "; ".join(self.suggestions))
        return " ".join(parts)


_DEFAULT_ATTRS = {
    "backend": "",
    "workspace_mode": "direct",
    "enforced": False,
    "backend_available": False,
}


@dataclass
class Payload:
    """一次执行请求的归一化输入, 供每一层滤网读取。

    text: 从工具参数里提取的"可执行文本" (命令/code/sql/script/url 等)。
          空列表表示该请求不涉及可执行内容 (纯读类), 上游不得误拦。
    """

    tool_name: str = ""
    text: List[str] = field(default_factory=list)   # 可执行文本字段
    command: str = ""                                # 主命令 (run_shell/run_tests 等)
    method: str = "shell"                            # shell|write|edit|read|code|mcp|web
    workspace: str = ""
    trust_level: Optional[str] = None                # trusted/limited/untrusted/unknown
    plan_mode: bool = False
    yolo: bool = False
    require_confirm: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def any_text(self) -> bool:
        return bool(self.command.strip()) or any(t.strip() for t in self.text)


def deny(layer: str, reason: str, severity: str = SEV["high"],
         suggestions: Optional[List[str]] = None) -> Verdict:
    return Verdict(Action.DENY, layer=layer, reason=reason,
                   severity=severity, suggestions=suggestions or [])


def confirm(layer: str, reason: str, severity: str = SEV["medium"],
            suggestions: Optional[List[str]] = None) -> Verdict:
    return Verdict(Action.CONFIRM, layer=layer, reason=reason,
                   severity=severity, suggestions=suggestions or [])


def isolate(layer: str, reason: str, severity: str = SEV["high"],
            suggestions: Optional[List[str]] = None, **meta) -> Verdict:
    return Verdict(Action.ISOLATE, layer=layer, reason=reason,
                   severity=severity, suggestions=suggestions or [],
                   isolate=True, **meta)