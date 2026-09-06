"""统一、fail-closed 安全闸门 —— 最强安全级别的核心。

所有「会产生副作用的动作」入口 (shell / mcp 工具 / 文件写 / 远程 prompt /
自我改进 / 定时任务 / 技能) 都应经此闸门裁决。设计原则:

- **fail-closed**: 任何异常、未知动作类型或未知信任级别, 一律 DENY;
- **红线优先**: ``is_hard_redline`` 命中永远 DENY, 即便 YOLO / 远程 / 白名单也不例外;
- **信任级别**: untrusted 工作区禁止 shell / 写文件; unknown 必须 confirm;
- **远程来源**: 远程发来的 prompt/命令默认升级 (非良性需 confirm, 红线 DENY)。

本模块只依赖标准库与同级安全组件 (safety_engine / security_policy), 不引入新网络或
重型依赖; 对 core 内模块的依赖通过方法内惰性 import 完成, 避免循环导入。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from .safety_engine import (
    is_benign_dev_command,
    is_hard_redline,
    is_redline,
)
from ..core.workspace_trust import TrustLevel


@dataclass(frozen=True)
class GateVerdict:
    """闸门裁决结果 (不可变, 便于在多线程/异步上下文安全传递)。"""

    action: str          # allow | confirm | deny
    severity: str        # none | low | medium | high | critical
    reasons: tuple       # 触发原因 (双语, 便于展示/日志)

    def blocks(self) -> bool:
        return self.action == "deny"

    def needs_confirm(self) -> bool:
        return self.action == "confirm"

    def allows(self) -> bool:
        return self.action == "allow"


_DENY_CRITICAL = "critical"
_DENY = "deny"
_CONFIRM = "confirm"
_ALLOW = "allow"


def _iter_text_values(obj: Any):
    """递归收集对象中的字符串值 (命令/脚本等可执行文本字段)。

    也处理 JSON 字符串: 参数以字符串形式传入时 (如 '{"command": "rm -rf /"}'),
    先解析再递归, 否则会被当作整体文本而漏判。
    """
    if isinstance(obj, str):
        stripped = obj.lstrip()
        if stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(obj)
            except Exception:  # noqa: BLE001 - 非 JSON, 按普通文本处理
                parsed = None
            if parsed is not None:
                yield from _iter_text_values(parsed)
                return
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_text_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_text_values(v)


class SecurityGate:
    """fail-closed 统一安全闸门。

    用法::

        gate = SecurityGate(yolo=False, trust_level=TrustLevel.TRUSTED, remote=False)
        v = gate.decide_shell(command)
        if v.blocks():
            ...
        elif v.needs_confirm():
            ...
    """

    def __init__(
        self,
        *,
        yolo: bool = False,
        trust_level: Optional[str] = None,
        remote: bool = False,
        rules_engine: Any = None,
        auditor: Any = None,  # 可选: SecurityAuditor 实例, 记录每条决策的 provenance
    ) -> None:
        # yolo 仅影响「可确认」层级的放行; 红线/信任级别始终 fail-closed。
        self.yolo = bool(yolo)
        self.trust_level = trust_level
        self.remote = bool(remote)
        self._rules = rules_engine  # 可选: 文件写规则校验 (RuleEngine 实例)
        self._auditor = auditor  # 可选: SecurityAuditor, 记录每条决策的 provenance

    # ---------------------------------------------------------- shell
    def decide_shell(self, command: str) -> GateVerdict:
        try:
            # 1. 硬红线: 永不自动执行 (YOLO/白名单/远程 均不豁免)
            if is_hard_redline(command):
                return GateVerdict(
                    _DENY, _DENY_CRITICAL,
                    ("命中致命红线 (文件系统/OS 级毁灭操作), 永不自动执行",),
                )

            # 2. 软红线 (critical/high 级): 默认 deny, YOLO 下升级为 confirm
            if is_redline(command):
                if self.yolo:
                    return GateVerdict(
                        _CONFIRM, _DENY_CRITICAL,
                        ("命令命中安全红线 (YOLO 模式下升级为确认)",),
                    )
                return GateVerdict(
                    _DENY, _DENY_CRITICAL,
                    ("命令命中安全红线, 已拒绝执行",),
                )

            # 3. 信任级别检查
            lvl = self.trust_level
            if lvl is not None:
                if lvl == TrustLevel.UNTRUSTED:
                    return GateVerdict(
                        _DENY, "high",
                        ("工作区信任级别为 untrusted, 禁止执行 shell 命令",),
                    )
                if lvl == TrustLevel.UNKNOWN:
                    return GateVerdict(
                        _CONFIRM, "medium",
                        ("工作区信任级别未知, 需用户确认后方可执行 shell",),
                    )
                if lvl == TrustLevel.LIMITED and not is_benign_dev_command(command):
                    return GateVerdict(
                        _CONFIRM, "medium",
                        ("工作区为 limited 信任, 非良性命令需确认",),
                    )

            # 4. 远程来源: 非良性操作需确认
            if self.remote and not is_benign_dev_command(command):
                return GateVerdict(
                    _CONFIRM, "high",
                    ("命令来自远程控制会话, 非良性操作需用户确认",),
                )

            return GateVerdict(_ALLOW, "none", ())
        except Exception:  # noqa: BLE001 - fail-closed
            return GateVerdict(_DENY, _DENY_CRITICAL, ("安全闸门异常, 保守拒绝执行",))

    # ---------------------------------------------------------- mcp 工具
    def decide_mcp_tool(self, tool_name: str, args: Any = None) -> GateVerdict:
        try:
            # 单一来源: 把参数中的所有文本字段递归送 safety_engine 做红线检测。
            # 不依赖工具名是否带 mcp__ 前缀或含危险关键词 —— 沙箱场景下 server 不可信,
            # 即便工具名叫 echo_text, 其参数里的 rm -rf 也必须 fail-closed 拒绝。
            from ..ext.safety_engine import is_redline as _engine_is_redline
            for text in _iter_text_values(args):
                if text.strip() and _engine_is_redline(text):
                    return GateVerdict(
                        _DENY, _DENY_CRITICAL,
                        (f"MCP 工具 {tool_name} 的参数包含危险操作, 已阻止",),
                    )
            return GateVerdict(_ALLOW, "none", ())
        except Exception:  # noqa: BLE001 - fail-closed
            return GateVerdict(_DENY, _DENY_CRITICAL, ("MCP 安全闸门异常, 保守拒绝调用",))

    # ---------------------------------------------------------- 远程 prompt
    def classify_remote_prompt(self, prompt: str) -> GateVerdict:
        """对远程控制会话发来的 prompt 做安全分类。

        - 硬红线 -> deny (远程设备绝不能驱动致命命令)
        - 软红线 -> confirm (极端确认)
        - 非良性 -> confirm (普通确认)
        - 良性开发命令 -> allow
        """
        try:
            if is_hard_redline(prompt):
                return GateVerdict(_DENY, _DENY_CRITICAL,
                                   ("远程 prompt 命中致命红线, 拒绝执行",))
            if is_redline(prompt):
                return GateVerdict(_CONFIRM, _DENY_CRITICAL,
                                   ("远程 prompt 含危险操作, 需极端确认",))
            if not is_benign_dev_command(prompt):
                return GateVerdict(_CONFIRM, "high",
                                   ("远程 prompt 非良性开发命令, 需用户确认",))
            return GateVerdict(_ALLOW, "none", ())
        except Exception:  # noqa: BLE001 - fail-closed
            return GateVerdict(_DENY, _DENY_CRITICAL, ("远程 prompt 安全分类异常, 保守拒绝",))

    # 系统敏感路径: 写入这些路径默认需确认 (非 hard_redline, 但 defense-in-depth)
    _SYSTEM_PATH_RE = None  # Lazy init to avoid import at module level

    @classmethod
    def _get_system_path_re(cls):
        if cls._SYSTEM_PATH_RE is None:
            import re as _re
            cls._SYSTEM_PATH_RE = _re.compile(
                r'^(?:/etc/|/boot/|/usr/lib/|/usr/bin/|/usr/sbin/|/sys/|/proc/|'
                r'~?/\.ssh/|~?/\.aws/|~?/\.gnupg/|'
                r'/var/log/|/var/run/|/var/spool/)',
                _re.IGNORECASE,
            )
        return cls._SYSTEM_PATH_RE

    # ---------------------------------------------------------- 文件写
    def decide_file_write(self, path: str, content: str) -> GateVerdict:
        try:
            # 1. 内容红线检测: 写入内容本身含危险操作 (如写入 rm -rf / 到脚本)
            if is_redline(content):
                return GateVerdict(_DENY, _DENY_CRITICAL,
                                   ("写入内容含致命操作, 拒绝写入",))
            # 2. 信任级别检查
            lvl = self.trust_level
            if lvl == TrustLevel.UNTRUSTED:
                return GateVerdict(_DENY, "high",
                                   ("工作区为 untrusted, 禁止写文件",))
            # 3. 系统敏感路径保护: 写入 /etc/ ~/.ssh/ 等路径需确认 (defense-in-depth)
            path_re = self._get_system_path_re()
            if path and path_re and path_re.search(path):
                if self.yolo:
                    return GateVerdict(
                        _CONFIRM, "high",
                        (f"写入系统敏感路径: {path} (YOLO 模式下升级为确认)",),
                    )
                return GateVerdict(
                    _CONFIRM, "high",
                    (f"写入系统敏感路径: {path}, 需用户确认",),
                )
            # 4. 用户自定义规则校验
            eng = self._rules
            if eng is not None:
                violations = eng.check(path, content, kind="file")
                blocking = [v for v in violations if v.get("severity") == "error"]
                if blocking:
                    msgs = "; ".join(v.get("message", v.get("id", "")) for v in blocking)
                    return GateVerdict(_DENY, "high", (f"命中策略错误规则: {msgs}",))
            return GateVerdict(_ALLOW, "none", ())
        except Exception:  # noqa: BLE001 - fail-closed
            return GateVerdict(_DENY, _DENY_CRITICAL, ("文件写安全闸门异常, 保守拒绝写入",))

    # ---------------------------------------------------------- 统一分发
    def decide(self, kind: str, payload: Any = None, **extra: Any) -> GateVerdict:
        """统一入口: kind ∈ {shell, mcp_tool, remote_prompt, file_write}。"""
        verdict = None
        try:
            if kind == "shell":
                verdict = self.decide_shell(str(payload))
            elif kind == "mcp_tool":
                name = extra.get("tool_name", payload)
                verdict = self.decide_mcp_tool(name, extra.get("args"))
            elif kind == "remote_prompt":
                verdict = self.classify_remote_prompt(str(payload))
            elif kind == "file_write":
                verdict = self.decide_file_write(
                    str(extra.get("path", "")), str(payload))
            else:
                # 未知动作类型: fail-closed
                verdict = GateVerdict(_DENY, _DENY_CRITICAL, (f"未知动作类型: {kind}",))
        except Exception:  # noqa: BLE001 - fail-closed
            verdict = GateVerdict(_DENY, _DENY_CRITICAL, ("安全闸门分发异常, 保守拒绝",))

        # 安全溯源: 记录每条决策的完整 provenance
        if self._auditor is not None and verdict is not None:
            try:
                self._auditor.record(
                    module="security_gate",
                    action=verdict.action,
                    severity=verdict.severity,
                    input_summary=str(payload)[:500] if payload else kind,
                    reasons=list(verdict.reasons),
                    context={
                        "kind": kind,
                        "yolo": self.yolo,
                        "trust_level": self.trust_level,
                        "remote": self.remote,
                    },
                )
            except Exception:  # noqa: BLE001
                pass  # 审计失败不应影响安全裁决

        return verdict
