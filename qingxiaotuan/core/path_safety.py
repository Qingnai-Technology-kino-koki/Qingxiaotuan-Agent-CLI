"""路径安全校验 —— 防止目录遍历攻击与路径注入。

核心原则:
- **fail-closed**: 任何无法确认安全的路径一律拒绝
- **规范化优先**: 所有路径在判定前先 resolve + normalize, 消除 `../` / `..\\` / symlink 混淆
- **工作区边界**: 受限工具 (read_file / write_file / edit_file) 的路径必须落在工作区内
- **敏感路径保护**: 某些系统路径 (.env / .ssh / .gnupg / /etc/shadow) 始终拒绝访问

与现有安全层的关系:
- safety_engine: 检查命令内容 (shell 级)
- path_safety: 检查文件路径 (文件操作级) —— 补齐文件工具的路径安全短板
- security_gate: 统一裁决入口, 调用 path_safety 做路径校验

用法::

    from qingxiaotuan.core.path_safety import PathSafety, PathViolation

    safety = PathSafety(workspace="/home/user/project")
    result = safety.validate_path("src/main.py", action="read")
    if result.denied:
        print(f"拒绝: {result.reason}")
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)


# ============================================================ 配置常量

# 需要向上查找的工作区根标记文件 (任一存在即视为项目根)
_PROJECT_ROOT_MARKERS = frozenset({
    ".git", "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "Cargo.toml", "go.mod", "Makefile",
    ".qingxiaotuan", "AGENTS.md",
})

# 敏感路径正则 (无论是否在工作区内, 始终拒绝)
_SENSITIVE_PATH_PATTERNS = [
    re.compile(r"(^|[/\\])\.env($|[/.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.env\.\w+($|[/.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.ssh($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.gnupg($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.aws($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.kube($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.docker($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])id_rsa($|[/.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])id_ed25519($|[/.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.npmrc($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.pypirc($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.gitconfig($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.netrc($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.pgpass($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])\.my\.cnf($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])shadow($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])passwd($|[/\\.])", re.IGNORECASE),
    re.compile(r"(^|[/\\])sudoers($|[/\\.])", re.IGNORECASE),
]

# 禁止写入的系统目录 (无论信任级别)
_FORBIDDEN_WRITE_DIRS = {
    "/etc", "/usr", "/boot", "/sys", "/proc",
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
}

# 路径遍历攻击特征
_TRAVERSAL_PATTERNS = [
    re.compile(r"(^|[/\\])\.\.([/\\]|$)"),        # ../ 或 ..\\
    re.compile(r"%2e%2e", re.IGNORECASE),           # URL 编码
    re.compile(r"%252e%252e", re.IGNORECASE),       # 双重 URL 编码
    re.compile(r"\.\.%af", re.IGNORECASE),          # Unicode 混淆
    re.compile(r"\.\.%c0%af", re.IGNORECASE),       # Overlong UTF-8
    re.compile(r"\.\.%c1%9c", re.IGNORECASE),       # Overlong UTF-8
]


# ============================================================ 数据类

@dataclass
class PathViolation:
    """路径安全校验结果。"""
    safe: bool = True
    denied: bool = False
    reason: str = ""
    risk_level: str = "none"          # none / low / medium / high / critical
    original_path: str = ""
    resolved_path: str = ""
    violations: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.safe


# ============================================================ 路径安全校验器

class PathSafety:
    """路径安全校验器 —— 防目录遍历 / 敏感路径泄露 / 工作区越界。

    用法::

        safety = PathSafety(workspace="/home/user/project")
        result = safety.validate_path("src/main.py", action="read")
        assert result.safe

        result = safety.validate_path("../../etc/passwd", action="read")
        assert result.denied
    """

    def __init__(
        self,
        workspace: str = "",
        home: Optional[str] = None,
        extra_sensitive_patterns: Optional[List[str]] = None,
    ) -> None:
        self._workspace = self._normalize(workspace) if workspace else ""
        self._home = self._normalize(home) if home else str(Path.home() / ".qingxiaotuan")
        self._extra_sensitive = []
        if extra_sensitive_patterns:
            for p in extra_sensitive_patterns:
                try:
                    self._extra_sensitive.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    pass

    # ------------------------------------------------------------ 公共 API

    def validate_path(
        self,
        path: str,
        action: str = "read",
        workspace: Optional[str] = None,
    ) -> PathViolation:
        """校验路径安全性。

        Args:
            path: 待校验路径 (可以是相对路径或绝对路径)
            action: 操作类型 (read / write / execute / list)
            workspace: 可选的工作区覆盖 (默认使用构造时的工作区)

        Returns:
            PathViolation: safe=True 表示安全; denied=True 表示拒绝
        """
        ws = self._normalize(workspace) if workspace else self._workspace
        original = path
        violations: List[str] = []

        # 1) 路径遍历攻击检测
        if self._has_traversal_attack(path):
            return PathViolation(
                safe=False, denied=True,
                reason="路径包含遍历攻击特征 (../ / URL编码 / Unicode混淆)",
                risk_level="critical",
                original_path=original,
                violations=["traversal_attack"],
            )

        # 2) 规范化路径
        resolved = self._resolve_path(path, ws)

        # 3) 敏感路径保护
        sensitive_result = self._check_sensitive(resolved)
        if sensitive_result.denied:
            return sensitive_result

        # 4) 工作区边界检查 (写/执行操作)
        if action in ("write", "execute") and ws:
            boundary_result = self._check_boundary(resolved, ws, action)
            if boundary_result.denied:
                return boundary_result

        # 5) 禁止写入系统目录
        if action == "write":
            sys_result = self._check_system_dirs(resolved)
            if sys_result.denied:
                return sys_result

        return PathViolation(
            safe=True, denied=False,
            original_path=original,
            resolved_path=resolved,
        )

    def is_sensitive(self, path: str) -> bool:
        """快速检查路径是否为敏感文件。"""
        resolved = self._resolve_path(path, self._workspace)
        return self._is_sensitive_path(resolved)

    def safe_join(self, base: str, *parts: str) -> Optional[str]:
        """安全路径拼接: 防止拼接结果逃逸出 base 目录。

        成功返回拼接后的规范化路径; 失败返回 None。
        """
        base_resolved = self._resolve_path(base, "")
        try:
            result = base_resolved
            for part in parts:
                if not part:
                    continue
                # 检查每一段是否包含遍历
                if self._has_traversal_attack(part):
                    return None
                result = os.path.join(result, part)
            result = self._normalize(result)
            # 验证结果仍在 base 下
            if not result.startswith(base_resolved):
                return None
            return result
        except (ValueError, OSError):
            return None

    def find_project_root(self, start_path: str) -> Optional[str]:
        """从 start_path 向上查找项目根目录。"""
        current = self._normalize(start_path)
        for _ in range(20):  # 防止无限循环
            for marker in _PROJECT_ROOT_MARKERS:
                candidate = os.path.join(current, marker)
                if os.path.exists(candidate):
                    return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        return None

    # ------------------------------------------------------------ 内部方法

    @staticmethod
    def _normalize(path: str) -> str:
        """规范化路径: resolve() + 归一化分隔符。"""
        if not path:
            return ""
        try:
            p = Path(path).expanduser().resolve()
            return str(p)
        except (ValueError, OSError):
            return os.path.normpath(path)

    def _resolve_path(self, path: str, workspace: str) -> str:
        """解析路径: 相对路径基于 workspace 解析, 然后规范化。"""
        if not path:
            return workspace or ""
        # 如果是相对路径, 基于 workspace 解析
        if not os.path.isabs(path) and workspace:
            resolved = os.path.join(workspace, path)
        else:
            resolved = path
        return self._normalize(resolved)

    def _has_traversal_attack(self, path: str) -> bool:
        """检测路径是否包含遍历攻击特征。"""
        for pattern in _TRAVERSAL_PATTERNS:
            if pattern.search(path):
                return True
        return False

    def _is_sensitive_path(self, resolved: str) -> bool:
        """检查规范化后的路径是否匹配敏感路径模式。"""
        for pattern in _SENSITIVE_PATH_PATTERNS:
            if pattern.search(resolved):
                return True
        for pattern in self._extra_sensitive:
            if pattern.search(resolved):
                return True
        return False

    def _check_sensitive(self, resolved: str) -> PathViolation:
        """敏感路径检查。"""
        if self._is_sensitive_path(resolved):
            return PathViolation(
                safe=False, denied=True,
                reason=f"路径为敏感文件 (凭据/密钥/配置): {os.path.basename(resolved)}",
                risk_level="critical",
                resolved_path=resolved,
                violations=["sensitive_path"],
            )
        return PathViolation(safe=True, resolved_path=resolved)

    def _check_boundary(self, resolved: str, workspace: str, action: str) -> PathViolation:
        """工作区边界检查: 写/执行操作必须落在工作区内。"""
        if not workspace:
            return PathViolation(safe=True, resolved_path=resolved)
        ws = self._normalize(workspace)
        if not resolved.startswith(ws):
            return PathViolation(
                safe=False, denied=True,
                reason=f"路径逃逸工作区边界 ({action} 操作): {resolved} 不在 {ws} 内",
                risk_level="high",
                original_path=resolved,
                resolved_path=resolved,
                violations=["boundary_escape"],
            )
        return PathViolation(safe=True, resolved_path=resolved)

    def _check_system_dirs(self, resolved: str) -> PathViolation:
        """禁止写入系统关键目录。"""
        resolved_lower = resolved.lower().replace("\\", "/")
        for forbidden in _FORBIDDEN_WRITE_DIRS:
            forbidden_norm = forbidden.lower().replace("\\", "/")
            if resolved_lower == forbidden_norm or resolved_lower.startswith(forbidden_norm + "/"):
                return PathViolation(
                    safe=False, denied=True,
                    reason=f"禁止写入系统目录: {forbidden}",
                    risk_level="critical",
                    resolved_path=resolved,
                    violations=["system_dir_write"],
                )
        return PathViolation(safe=True, resolved_path=resolved)


# ============================================================ 模块级便捷函数

_global_safety: Optional[PathSafety] = None


def get_path_safety(workspace: str = "", home: Optional[str] = None) -> PathSafety:
    """获取全局 PathSafety 单例。"""
    global _global_safety
    if _global_safety is None or (workspace and _global_safety._workspace != workspace):
        _global_safety = PathSafety(workspace=workspace, home=home)
    return _global_safety


def validate_path(path: str, action: str = "read", workspace: str = "") -> PathViolation:
    """模块级便捷校验。"""
    safety = get_path_safety(workspace)
    return safety.validate_path(path, action, workspace)
