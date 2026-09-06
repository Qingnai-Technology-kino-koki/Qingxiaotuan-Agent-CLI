"""规则外置：具体项目规则放项目级配置文件，记忆系统只存用户偏好。

- load_project_rules：会话开始时自动加载项目规则（.qingxiaotuan/rules.md 或 AGENTS.md）。
- UserPrefsMemory：长期记忆只接受「用户偏好」类条目，拒绝存项目事实，天然满足外置约定。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

_PROJECT_RULE_FILES = [
    ".qingxiaotuan/rules.md",
    ".qxt/rules.md",
    "AGENTS.md",
    "docs/AGENTS.md",
]


def load_project_rules(root: str | Path) -> str:
    """按优先级加载项目级规则文件，拼接返回；无则空串。"""
    root = Path(root)
    chunks: list[str] = []
    for rel in _PROJECT_RULE_FILES:
        p = root / rel
        if p.is_file():
            try:
                chunks.append(f"# 规则文件: {rel}\n\n{p.read_text(encoding='utf-8', errors='ignore')}")
            except OSError:
                continue
    return "\n\n---\n\n".join(chunks)


def has_project_rules(root: str | Path) -> bool:
    root = Path(root)
    return any((root / rel).is_file() for rel in _PROJECT_RULE_FILES)


class UserPrefsMemory:
    """只存用户个人偏好（表达风格、常用命令、个人禁忌）。

    设计上强制「外置约定」：store 仅接受 category='pref'，任何试图写入
    项目事实（代码、文件路径、临时细节）的调用都会抛 ValueError，防止记忆膨胀。
    """

    def __init__(self, store: Optional[Dict[str, str]] = None):
        self._prefs: Dict[str, str] = dict(store or {})

    def set_pref(self, key: str, value: str) -> None:
        self._prefs[key] = value

    def get_pref(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self._prefs.get(key, default)

    def items(self):
        return list(self._prefs.items())

    def store(self, category: str, key: str, value: str) -> None:
        """唯一对外写入口：只允许 'pref' 类别，拒绝项目事实。"""
        if category != "pref":
            raise ValueError(
                f"记忆系统只接受用户偏好(category='pref')，拒绝写入 {category!r}。"
                f"项目事实应放入项目级规则文件 (.qingxiaotuan/rules.md)。"
            )
        self.set_pref(key, value)
