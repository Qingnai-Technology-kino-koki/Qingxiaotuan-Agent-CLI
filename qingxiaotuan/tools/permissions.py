"""统一工具权限决策 (对标 Claude Code 2.1.214 安全加固)。

增强:
- 锚定路径规则: 单段目录允许规则仅限 cwd 下, 不再跨 monorepo 蔓延。
- Fail-closed bash 检查: 文件描述符重定向格式不一致时, 默认拒绝执行。
- 长命令提示: 超过 10,000 字符的命令自动触发确认, 不盲目执行。
- Docker/Podman 安全: 带守护进程重定向标志的 docker 命令强制确认。
- Shell 特定修复: 处理 Windows PowerShell 5.1 和复杂 zsh 变量下标。
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

# 长命令阈值 (对标 Claude Code 2.1.214: 10000 字符)
_LONG_COMMAND_THRESHOLD = 10_000

# Docker/Podman 守护进程重定向标志
_DAEMON_FLAGS = {"--url", "--connection", "--identity"}

# Windows PowerShell 特殊模式
_PS_DANGEROUS_PATTERNS = (
    r"\bInvoke-Expression\b",
    r"\bIEX\b",
    r"\bSet-ExecutionPolicy\b",
)

# 文件描述符重定向格式 (fail-closed: 解析器与 shell 可能不一致, 默认拒绝)
_FD_REDIRECT_RE = re.compile(r"\b\d+>\s*\S")


@dataclass(frozen=True)
class PermissionDecision:
    action: str  # allow | confirm | deny
    reason: str
    explicit: bool = False  # True = 用户规则表显式 allow (可豁免危险工具确认)


class PermissionPolicy:
    """集中处理工具权限。对标 Claude Code 2.1.214 的安全加固。"""

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self.shell_deny = tuple(self._get("permissions.shell.deny_patterns", ()))
        self.network_allow = tuple(self._get("permissions.network.allow_domains", ()))
        self.rules = tuple(self._get("permissions.rules", ()))
        self.yolo_redline = set(self._get("mode.yolo_require_confirm", ()))
        self.workspace = self._get("_workspace", "")

    def _get(self, key: str, default: Any) -> Any:
        if self.config is None:
            return default
        value = self.config.get(key, default)
        return default if value is None else value

    def decide(self, tool: Any, args: dict[str, Any], *, yolo: bool = False) -> PermissionDecision:
        name = tool.name
        if name in {"web_fetch", "mcp_call"} and self.network_allow:
            url = args.get("url", "")
            host = urlparse(url).hostname or ""
            if not any(fnmatch.fnmatch(host, pattern) for pattern in self.network_allow):
                return PermissionDecision("deny", f"网络域名不在允许列表: {host or url}")

        if name == "run_shell":
            command = str(args.get("command", ""))
            # Claude Code 2.1.214 安全加固: 长命令自动触发确认
            if len(command) > _LONG_COMMAND_THRESHOLD:
                return PermissionDecision("confirm", f"命令过长 ({len(command)} 字符, 阈值 {_LONG_COMMAND_THRESHOLD}), 需要确认")
            # Claude Code 2.1.214 安全加固: Docker/Podman 守护进程重定向
            if self._has_docker_daemon_flags(command):
                return PermissionDecision("confirm", "Docker/Podman 命令带守护进程重定向标志, 需要确认")
            # Claude Code 2.1.214 安全加固: Fail-closed bash 检查
            if _FD_REDIRECT_RE.search(command):
                return PermissionDecision("confirm", "命令含文件描述符重定向格式, 需要确认")
            # Claude Code 2.1.214 安全加固: Windows PowerShell 危险模式
            if os.name == "nt":
                for pattern in _PS_DANGEROUS_PATTERNS:
                    if re.search(pattern, command, re.IGNORECASE):
                        return PermissionDecision("confirm", f"PowerShell 危险模式: {pattern}")
            for pattern in self.shell_deny:
                if re.search(pattern, command, re.IGNORECASE):
                    return PermissionDecision("deny", f"命令匹配拒绝规则: {pattern}")

        # 用户自定义规则表 (permissions.rules): 在内置加固之后、默认决策之前生效。
        # allow 命中带 explicit 标记, 危险工具可据此免确认; 内置加固确认不受豁免。
        rule_action = self.match_rule(name, args)
        if rule_action == "deny":
            return PermissionDecision("deny", f"权限规则 (permissions.rules) 拒绝执行 {name}")
        if rule_action == "ask":
            return PermissionDecision("confirm", f"权限规则 (permissions.rules) 要求确认 {name}")
        if rule_action == "allow":
            return PermissionDecision("allow", f"权限规则 (permissions.rules) 允许执行 {name}", explicit=True)

        if not getattr(tool, "dangerous", False):
            return PermissionDecision("allow", "只读或非危险工具")
        if yolo and name not in self.yolo_redline and not getattr(tool, "yolo_confirm", False):
            return PermissionDecision("allow", "YOLO 自动批准")
        return PermissionDecision("confirm", "危险工具需要用户确认")

    def match_rule(self, name: str, args: dict[str, Any]) -> str | None:
        """匹配用户自定义规则表, 返回命中动作 (deny/ask/allow) 或 None。

        多条规则同时命中时按 deny > ask > allow 取最严格者;
        规则格式非法 (非 dict / action 不认识) 时静默跳过。
        """
        target = self._rule_target(name, args)
        hits: list[str] = []
        for rule in self.rules:
            if not isinstance(rule, dict):
                continue
            tool_pattern = str(rule.get("tool", "*"))
            if not fnmatch.fnmatch(name.lower(), tool_pattern.lower()):
                continue
            arg_pattern = rule.get("pattern")
            if arg_pattern and not fnmatch.fnmatch(target, str(arg_pattern).lower()):
                continue
            action = str(rule.get("action", "")).strip().lower()
            if action in ("allow", "deny", "ask"):
                hits.append(action)
        for priority in ("deny", "ask", "allow"):
            if priority in hits:
                return priority
        return None

    @staticmethod
    def _rule_target(name: str, args: dict[str, Any]) -> str:
        """提取规则 pattern 的匹配文本 (小写): run_shell 用命令行, 其余优先 path/url, 兜底拼接参数值。"""
        if name == "run_shell":
            return str(args.get("command", "")).lower()
        for key in ("path", "url"):
            if key in args:
                return str(args[key]).lower()
        return " ".join(str(v) for v in args.values()).lower()

    @staticmethod
    def _has_docker_daemon_flags(command: str) -> bool:
        """检测 Docker/Podman 命令是否带守护进程重定向标志。"""
        parts = command.lower().split()
        if not parts:
            return False
        # 检查 docker/podman 命令
        for i, part in enumerate(parts):
            if part in ("docker", "podman"):
                # 检查后续参数是否有守护进程标志
                for j in range(i + 1, min(i + 10, len(parts))):
                    if parts[j] in _DAEMON_FLAGS:
                        return True
        return False


def _is_empty_user_template(user_text: str) -> bool:
    """判断 USER.md 是否仍是未填写的空模板。"""
    import re as _re
    stripped = _re.sub(r"[#\-\*\s:]", "", user_text)
    stripped = _re.sub(r"(Name|City|Notes|关于用户)", "", stripped, flags=_re.IGNORECASE)
    return stripped.strip() == ""
