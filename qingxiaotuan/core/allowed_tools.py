"""作用域工具白名单 (对标 Claude Code 2.1.246 的 --allowedTools / scoped Bash)。

- 解析 Claude Code 的 allowedTools 语法:
    `Bash(npm test),Read,Edit,Write,Bash(pytest *.py)` 等。
- 支持作用域 Bash: 只放行匹配指定前缀/通配符子集的命令, 其余仍需确认。
  当命令以 * 结尾时, 视作前缀通配 (Claude Code 语义: `Bash(npm *)`);
  `Bash(*)` 放行全部 Bash。
- 语义:
    - 普通工具名 (Read/Edit/Write/...): 放行同名工具, 无需提示。
    - Bash(scope): 若命令作用域匹配, 放行该 Bash 调用。
- 安全约束: 本白名单只是"会话内免确认"的加分项, 优先级仍低于黑名单与硬红线;
  命中致命红线 (YOLO_REDLINE) 的命令无论是否在 allowedTools 中都不放行。
- 作用域只对本会话有效 (不写入磁盘白名单), 由 CLI 一次性注入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AllowedTools:
    """解析后的 allowedTools 规格。"""
    tools: set[str] = field(default_factory=set)          # 普通工具名
    bash_scopes: List[str] = field(default_factory=list)  # Bash 作用域 (glob/prefix)
    _bash_all: bool = False

    @classmethod
    def parse(cls, raw: Optional[str]) -> "AllowedTools":
        obj = cls()
        if not raw:
            return obj
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            # Bash(...) 作用域
            m = re.fullmatch(r"Bash\((.*)\)", piece)
            if m:
                scope = m.group(1).strip()
                obj.bash_scopes.append(scope)
                if not scope or scope == "*":
                    obj._bash_all = True
                continue
            # 普通工具名
            obj.tools.add(piece)
        return obj

    def allows_tool(self, tool_name: str) -> bool:
        return tool_name in self.tools

    def allows_bash(self, command: str) -> bool:
        """判断命令是否命中任一 Bash 作用域。"""
        if self._bash_all:
            return True
        cmd = command.strip()
        if not cmd:
            return False
        for scope in self.bash_scopes:
            if _scope_match(cmd, scope):
                return True
        return False

    def scopes_text(self) -> str:
        parts = []
        for t in sorted(self.tools):
            parts.append(t)
        for s in self.bash_scopes:
            parts.append(f"Bash({s})")
        return ", ".join(parts) or "(空)"


def _scope_match(command: str, scope: str) -> bool:
    """匹配命令与作用域: 前缀匹配 + * 通配。"""
    scope = scope.strip()
    if not scope:
        return False
    if "*" in scope:
        # 转 glob 到正则
        rx = _glob_to_regex(scope)
        return bool(re.search(rx, command, re.IGNORECASE))
    # 前缀匹配: `npm test` 放行 `npm test -- --watch`
    return command.lower().startswith(scope.lower()[:])


def _glob_to_regex(pattern: str) -> str:
    import re as _re

    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == " ":
            out.append(r"\s+")
        elif ch in ".^$+?()[]{}|\\-":
            out.append("\\" + ch)
        else:
            out.append(_re.escape(ch))
    return "".join(out)