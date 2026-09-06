"""五层按钮式防误操作 + 版本锁死 + 权限回收。

这是把「代码编辑助手提示词」里的安全机制落成**可执行**的策略引擎：
- RiskLevel / classify_risk：给动作定级，决定要不要走确认闸门；
- SafetyGate：预览→确认→复核→审计→回收 五层，以及版本锁死校验、权限授予/回收；
- 所有判断都是纯函数或可注入 IO 的薄封装，便于单测，不依赖具体 UI。
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# 确认阈值：达到 HIGH 的动作必须显式确认（预览层 + 确认层）
CONFIRM_THRESHOLD = 3  # RiskLevel.HIGH.value


class RiskLevel(Enum):
    SAFE = 0      # 只读、可逆、本地
    LOW = 1       # 低影响的本地写
    MEDIUM = 2    # 局部写、可回退
    HIGH = 3      # 删除/强推/批量变更/外部写入
    CRITICAL = 4  # 删库/清环境/改系统配置/发对外消息


@dataclass
class Action:
    kind: str  # delete_files | run_command | bump_version | overwrite | edit_code |
               # read_secret | external_write | git_push_force
    description: str
    targets: List[str] = field(default_factory=list)
    command: Optional[str] = None
    version_old: Optional[str] = None
    version_new: Optional[str] = None
    uncommitted: bool = False  # 目标是否含未提交改动（复核层用）


# 每类动作的基准风险（可被具体属性上调）
_BASE_RISK = {
    "edit_code": RiskLevel.MEDIUM,
    "run_command": RiskLevel.LOW,
    "read_secret": RiskLevel.LOW,
    "external_write": RiskLevel.HIGH,
    "overwrite": RiskLevel.HIGH,
    "delete_files": RiskLevel.HIGH,
    "bump_version": RiskLevel.MEDIUM,
    "git_push_force": RiskLevel.CRITICAL,
}


def classify_risk(action: Action) -> RiskLevel:
    base = _BASE_RISK.get(action.kind, RiskLevel.LOW)
    # 上调规则：含未提交改动、批量删除、命令含危险子串 → 至少 HIGH
    if action.uncommitted and action.kind in ("delete_files", "overwrite"):
        base = RiskLevel.HIGH
    if action.command and _looks_dangerous(action.command):
        base = RiskLevel.HIGH
    if action.kind == "delete_files" and len(action.targets) > 10:
        base = RiskLevel.CRITICAL
    return base


_DANGER_SUBSTR = ("rm -rf", "rm -fr", "rmdir /s", "del /s", "git push --force",
                  "git push -f", "drop database", "truncate", "mkfs", "format ")


def _looks_dangerous(cmd: str) -> bool:
    low = cmd.lower()
    return any(s in low for s in _DANGER_SUBSTR)


@dataclass
class PermissionGrant:
    token: str
    scope: str
    issued_at: float
    ttl: float
    revoked: bool = False

    def expired(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return now - self.issued_at > self.ttl


@dataclass
class AuditEvent:
    ts: float
    layer: str  # preview | confirm | recheck | audit | revoke
    action_kind: str
    detail: str


class SafetyGate:
    """五层防误操作的可执行实现。IO 通过回调注入，便于测试与非交互环境复用。"""

    def __init__(
        self,
        confirm_fn: Optional[Callable[[str], bool]] = None,
        audit_log: Optional[List[AuditEvent]] = None,
    ):
        self._confirm_fn = confirm_fn or _default_confirm
        self._audit: List[AuditEvent] = audit_log if audit_log is not None else []
        self._perms: Dict[str, PermissionGrant] = {}

    # ---- 预览层 ----
    def preview(self, action: Action, diff: str = "") -> str:
        lines = [f"[预览] 动作: {action.kind} —— {action.description}"]
        if action.targets:
            lines.append("目标:")
            lines += [f"  - {t}" for t in action.targets]
        if action.command:
            lines.append(f"命令: {action.command}")
        if action.version_old is not None:
            lines.append(f"版本: {action.version_old} -> {action.version_new}")
        if diff:
            lines.append("差异:")
            lines += [f"  {ln}" for ln in diff.splitlines()[:200]]
        text = "\n".join(lines)
        self._audit.append(AuditEvent(time.time(), "preview", action.kind, text[:300]))
        return text

    # ---- 确认层 ----
    def requires_confirmation(self, action: Action) -> bool:
        return classify_risk(action).value >= CONFIRM_THRESHOLD

    def confirm(self, action: Action, preview_text: str = "") -> bool:
        if not preview_text:
            preview_text = self.preview(action)
        self._audit.append(AuditEvent(time.time(), "confirm", action.kind, "pending"))
        ok = bool(self._confirm_fn(preview_text))
        self._audit.append(AuditEvent(time.time(), "confirm", action.kind, "granted" if ok else "denied"))
        return ok

    # ---- 复核层 ----
    def recheck(self, action: Action) -> List[str]:
        """执行前二次校验，返回发现的风险提示（空=通过）。"""
        warnings: List[str] = []
        if action.uncommitted:
            warnings.append("目标含未提交改动，删除/覆盖前请确认非进行中工作")
        if action.kind == "delete_files" and len(action.targets) > 10:
            warnings.append("批量删除超过 10 项，建议分批并逐批确认")
        self._audit.append(AuditEvent(time.time(), "recheck", action.kind, ";".join(warnings) or "ok"))
        return warnings

    # ---- 审计层 ----
    def audit(self, action_kind: str, detail: str) -> None:
        self._audit.append(AuditEvent(time.time(), "audit", action_kind, detail))

    # ---- 回收层：权限授予 / 校验 / 回收 ----
    def grant(self, scope: str, ttl: float = 300.0) -> PermissionGrant:
        grant = PermissionGrant(token=uuid.uuid4().hex, scope=scope,
                                issued_at=time.time(), ttl=ttl)
        self._perms[grant.token] = grant
        self._audit.append(AuditEvent(time.time(), "revoke", "grant", f"scope={scope} ttl={ttl}"))
        return grant

    def is_valid(self, token: str, now: Optional[float] = None) -> bool:
        g = self._perms.get(token)
        if g is None or g.revoked or g.expired(now):
            return False
        return True

    def revoke(self, token: str) -> bool:
        g = self._perms.get(token)
        if g is None:
            return False
        g.revoked = True
        self._audit.append(AuditEvent(time.time(), "revoke", "revoke", g.token))
        return True

    @property
    def audit_trail(self) -> List[AuditEvent]:
        return list(self._audit)


def _default_confirm(preview_text: str) -> bool:
    """非交互环境的默认策略：凡需确认即拒绝（fail-safe）。真实 CLI 会注入 input()。"""
    return False


# ---------- 版本锁死 ----------
def parse_version(v: str) -> Optional[tuple]:
    """解析 MAJOR.MINOR.PATCH[-pre]，非法返回 None。"""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$", (v or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "")


def check_version_lock(
    old: Optional[str], new: Optional[str], strategy: str = "semver"
) -> tuple[bool, str]:
    """校验版本变更是否符合迭代策略。

    返回 (ok, reason)。失败情形：缺新值、无法解析、非单调、release 回退到 pre-release。
    """
    if new is None or not str(new).strip():
        return False, "未提供新版本号（版本锁死：变更前须展示旧→新并说明理由）"
    po, pn = parse_version(old or "0.0.0"), parse_version(new)
    if pn is None:
        return False, f"新版本号格式非法: {new!r}（应为 MAJOR.MINOR.PATCH）"
    if po is None:
        return False, f"旧版本号格式非法: {old!r}"
    # 单调不降
    if pn[:3] < po[:3]:
        return False, f"版本回退: {old} -> {new}（迭代策略禁止降级）"
    # release 不允许回退到同号 pre-release
    if po[3] == "" and pn[3] != "" and pn[:3] == po[:3]:
        return False, f"正式版回退到预发布: {old} -> {new}"
    # 主版本跃升视为破坏性，须显式确认（这里只提示，不阻断）
    if pn[0] > po[0]:
        return True, f"主版本跃升（破坏性）: {old} -> {new}，请确认符合发版规则"
    return True, f"版本合规: {old} -> {new}"
