"""白名单管理模块 (Trae 模式): 手动维护, 命中自动执行。

设计原则:
- 白名单优先级永远低于黑名单: 即使命令在白名单中, 只要命中黑名单/红线, 仍然拦截
- 白名单通过 CLI 手动维护 (qxt whitelist list / add / remove / clear),
  不会在单次确认放行后自动加入 —— 确认通道的放行仅本次生效
- 白名单存本地 (~/.qingxiaotuan/whitelist.json)

安全约束:
- 黑名单/红线命令永远无法加入白名单 (is_redline 命中则拒绝)
- 白名单命令如果被修改/包含危险模式, 执行前仍会重新检测
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)

# 确认码字母表: 去除易混淆字符 0/O/1/I, 仅保留清晰可辨字符
_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_confirm_token(length: int = 4) -> str:
    """生成一次性确认码 (防肌肉记忆/盲点自动确认)。"""
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(length))


def interactive_confirm(
    prompt: str,
    *,
    expected: Optional[str] = None,
    position: Optional[int] = None,
) -> bool:
    """终端交互式确认 (供 MultiStageConfirm 在无 confirm_fn 且为 TTY 时兜底)。

    - position: 0..4, 控制横幅前的空行数, 使每次确认出现在屏幕不同垂直位置
      (防自动点击脚本固定坐标命中)。
    - expected: 若提供, 用户必须键入该确认码才授权 (否则视为拒绝)。
    - 返回 True 表示用户确认通过。
    """
    # 真实变化屏幕垂直位置: 不同位置打印不同数量的引导空行
    offsets = (0, 2, 1, 3, 1)
    if position is not None:
        blank = offsets[position % len(offsets)]
        for _ in range(blank):
            print()
    print(prompt)
    if expected is not None:
        try:
            typed = input("请键入上方确认码以授权 (直接回车拒绝): ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            return False
        return typed == expected.upper()
    try:
        typed = input("确认执行? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return typed in ("y", "yes")

# ------------------------------------------------------------ 白名单存储

# 默认白名单 (只读命令, 永远安全)
_DEFAULT_WHITELIST: List[Dict[str, str]] = [
    {"command": "ls", "description": "列出目录内容"},
    {"command": "cat", "description": "查看文件内容"},
    {"command": "head", "description": "查看文件头部"},
    {"command": "tail", "description": "查看文件尾部"},
    {"command": "grep", "description": "文本搜索"},
    {"command": "find", "description": "查找文件"},
    {"command": "pwd", "description": "显示当前目录"},
    {"command": "whoami", "description": "显示当前用户"},
    {"command": "date", "description": "显示日期时间"},
    {"command": "git status", "description": "查看Git状态"},
    {"command": "git diff", "description": "查看Git差异"},
    {"command": "git log", "description": "查看Git日志"},
    {"command": "git branch", "description": "查看Git分支"},
    {"command": "python -c", "description": "执行Python片段(需谨慎)"},
]


class WhitelistManager:
    """白名单管理器.

    实现 Trae 模式: 白名单命令自动执行, 无需再次确认。
    白名单只由 CLI 手动维护 (add/remove/clear), 确认通道的放行不写入白名单。
    """

    def __init__(self, home: Optional[Path] = None) -> None:
        self.home = home or Path(os.environ.get("QXT_HOME", Path.home() / ".qingxiaotuan"))
        self._lock = threading.RLock()
        self._path = self.home / "whitelist.json"
        self._entries: List[Dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        """从磁盘加载白名单."""
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._entries = [
                            e for e in data
                            if isinstance(e, dict) and "command" in e and "description" in e
                        ]
                    elif isinstance(data, dict) and "entries" in data:
                        self._entries = data["entries"]
            # 合并默认白名单 (不覆盖用户已有)
            existing_commands = {e["command"] for e in self._entries}
            for entry in _DEFAULT_WHITELIST:
                if entry["command"] not in existing_commands:
                    self._entries.append(entry)
        except Exception as exc:  # noqa: BLE001
            log.debug("白名单加载失败 (使用默认): %s", exc)
            self._entries = list(_DEFAULT_WHITELIST)

    def _save(self) -> None:
        """保存白名单到磁盘."""
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "entries": self._entries}, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            log.debug("白名单保存失败: %s", exc)

    def is_whitelisted(self, command: str) -> bool:
        """判断命令是否在白名单中.

        匹配规则:
        - 完全匹配 (如 "ls -la" 与 "ls -la")
        - 前缀匹配 (如 "ls" 匹配 "ls -la")
        注意: 只有命令的第一个token匹配白名单条目才放行。
        """
        cmd_norm = command.strip().lower()
        if not cmd_norm:
            return False
        with self._lock:
            for entry in self._entries:
                pattern = entry["command"].strip().lower()
                # 完全匹配
                if cmd_norm == pattern:
                    return True
                # 前缀匹配 (如 "git status" 匹配 "git status --short")
                if cmd_norm.startswith(pattern + " ") or cmd_norm.startswith(pattern + "\t"):
                    return True
        return False

    def add(self, command: str, description: str = "") -> bool:
        """添加命令到白名单.

        安全约束: 黑名单/红线命令永远无法加入白名单。
        需要传入 is_redline 函数进行验证 (由调用方注入)。
        """
        from ..ext.safety_engine import is_redline

        if is_redline(command):
            log.warning("拒绝将红线命令加入白名单: %s", command)
            return False

        cmd_norm = command.strip().lower()
        if not cmd_norm:
            return False
        with self._lock:
            # 检查是否已存在
            for entry in self._entries:
                if entry["command"].strip().lower() == cmd_norm:
                    return False
            self._entries.append({
                "command": command.strip(),
                "description": description or "用户手动添加",
            })
            self._save()
            return True

    def remove(self, command: str) -> bool:
        """从白名单移除命令."""
        cmd_norm = command.strip().lower()
        with self._lock:
            for i, entry in enumerate(self._entries):
                if entry["command"].strip().lower() == cmd_norm:
                    self._entries.pop(i)
                    self._save()
                    return True
        return False

    def list(self) -> List[Dict[str, str]]:
        """列出所有白名单条目."""
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        """清空白名单 (保留默认条目)."""
        with self._lock:
            self._entries = list(_DEFAULT_WHITELIST)
            self._save()


# ------------------------------------------------------------ 警告级别管理

# 极高风险命令: 至少弹5次警告
_EXTREME_RISK_PATTERNS = [
    r"rm\s+-[a-zA-Z]*[rR][a-zA-Z]*[fF]\s+/",          # rm -rf /
    r"rm\s+-[a-zA-Z]*[fF][a-zA-Z]*[rR]\s+/",          # rm -fr /
    r"dd\s+if=.*\s+of=/dev/sd",                        # dd 写磁盘
    r"mkfs\s+",                                        # mkfs 创建文件系统
    r"format\s+[a-zA-Z]:",                             # format 磁盘
    r"shutdown\s+",                                    # shutdown 关机
    r"halt\s+",                                        # halt 关机
    r"poweroff\s+",                                    # poweroff 关机
    r"reboot\s+",                                      # reboot 重启
    r"init\s+[06]",                                    # init 0/6
    r"systemctl\s+(poweroff|reboot|halt)",             # systemctl 关机/重启
    r"chmod\s+-[Rr]\s+0{3,}\s+/",                     # chmod -R 000 /
    r"chown\s+-[Rr]\s+root\s+/",                      # chown -R root /
    r"drop\s+table",                                   # DROP TABLE
    r"drop\s+database",                                # DROP DATABASE
    r"delete\s+from\s+.*;?",                           # DELETE FROM
    r"Remove-Item\s+.*-Recurse\s+.*-Force",           # PowerShell 递归强删
    r"Stop-Computer",                                  # PowerShell 关机
    r"Restart-Computer",                               # PowerShell 重启
    r"git\s+push\s+.*--force",                         # git push --force
    r"git\s+push\s+.*-f",                              # git push -f
]

# 高风险命令: 至少弹3次警告
_HIGH_RISK_PATTERNS = [
    r"chmod\s+777",                                    # chmod 777
    r"git\s+reset\s+--hard",                           # git reset --hard
    r"git\s+clean\s+-[a-zA-Z]*f",                      # git clean -f
    r"git\s+checkout\s+--\s+\.",                      # git checkout -- .
    r"docker\s+rm\s+-f",                               # docker rm -f
    r"docker\s+rmi\s+-f",                              # docker rmi -f
    r"kubectl\s+delete",                               # kubectl delete
    r"iptables\s+-F",                                  # iptables -F
    r"alter\s+table\s+.*\s+drop",                      # ALTER TABLE DROP
    r"kill\s+-9",                                      # kill -9
    r"pkill\s+",                                       # pkill
    r"killall\s+",                                     # killall
]


def get_warning_level(command: str) -> int:
    """获取命令的警告级别.

    Returns:
    - 5: 极高风险 (必须弹5次警告)
    - 3: 高风险 (必须弹3次警告)
    - 0: 普通命令 (白名单直接放行)
    """
    # 降误杀: 良性开发命令直接返回 0 (不弹确认), 但仍由 is_hard_redline / score 兜底判定
    # 不可逆破坏 (rm -rf /、force push 等); 写系统关键路径会被 _SYSTEM_PATH_RE 排除出良性集合。
    from ..ext.safety_engine import is_benign_dev_command
    if is_benign_dev_command(command):
        return 0

    import re as _re
    cmd_lower = command.lower()

    # 极高风险检测
    for pattern in _EXTREME_RISK_PATTERNS:
        if _re.search(pattern, cmd_lower, _re.IGNORECASE):
            return 5

    # 高风险检测
    for pattern in _HIGH_RISK_PATTERNS:
        if _re.search(pattern, cmd_lower, _re.IGNORECASE):
            return 3

    return 0


# ------------------------------------------------------------ 多阶段确认

class MultiStageConfirm:
    """多阶段确认: 极高风险命令弹5次, 高风险弹3次, 每次出现在屏幕不同位置且需键入独立确认码。

    防呆机制 (真实生效, 非仅文本标签):
    1. 每次确认打印在屏幕不同垂直位置 (position 控制引导空行数), 自动点击脚本无法固定坐标命中;
    2. 每次确认生成一个一次性确认码 (expected), 用户必须键入才能授权, 杜绝肌肉记忆/盲点回车;
    3. 两次警告之间至少间隔 min_interval 秒, 防止程序自动连续点击绕过。

    确认回调约定: confirm_fn(text, *, expected=None, position=None) -> bool。
    若回调不支持 expected/position 关键字 (如非交互式 lambda), 自动退化为纯 yes/no。
    """

    # 不同屏幕位置标签 (与交互式渲染的引导空行数一一对应, 真正改变垂直位置)
    BUTTON_POSITIONS = [
        "屏幕顶部",   # 位置 0
        "屏幕中上",   # 位置 1
        "屏幕中部",   # 位置 2
        "屏幕中下",   # 位置 3
        "屏幕底部",   # 位置 4
    ]

    def __init__(self, confirm_fn: Optional[Callable[[str], bool]] = None,
                 min_interval: float = 2.0) -> None:
        """
        Args:
            confirm_fn: 回调 (text, *, expected=None, position=None) -> bool
            min_interval: 两次警告之间的最小间隔秒数 (默认 2.0)
        """
        self._confirm_fn = confirm_fn
        self._min_interval = min_interval

    def set_confirm_fn(self, confirm_fn) -> None:
        """设置确认回调."""
        self._confirm_fn = confirm_fn

    def confirm(self, command: str, warning_level: int = 0, reason: str = "") -> bool:
        """执行多阶段确认.

        Args:
            command: 要执行的命令
            warning_level: 警告级别 (5=极危, 3=高危, 0=普通)
            reason: 拦截原因描述

        Returns:
            True = 用户确认通过, False = 用户拒绝 (或无确认通道且非交互终端)
        """
        if warning_level <= 0:
            return True

        fn = self._confirm_fn
        # 无回调且非交互终端 -> fail-closed (无人值守场景默认拒绝高危命令)
        if fn is None and not sys.stdin.isatty():
            log.warning("无确认回调且非交互终端, 拒绝执行高危命令: %s", command)
            return False

        stages = warning_level
        for i in range(stages):
            # 真实变化: 每次随机屏幕位置 + 一次性确认码
            position = secrets.randbelow(len(self.BUTTON_POSITIONS))
            token = make_confirm_token()
            msg = self._build_msg(command, reason, warning_level, i, stages, position, token)
            if not self._invoke(msg, expected=token, position=position):
                log.info("用户在第 %d 次警告拒绝执行: %s", i + 1, command)
                return False
            if self._min_interval > 0 and i < stages - 1:
                time.sleep(self._min_interval)
        return True

    def _invoke(self, msg: str, *, expected: str, position: int) -> bool:
        """调用确认回调; 不支持 expected/position 关键字的回调退化为纯 yes/no。"""
        fn = self._confirm_fn
        if fn is None:
            return interactive_confirm(msg, expected=expected, position=position)
        try:
            return bool(fn(msg, expected=expected, position=position))
        except TypeError:
            return bool(fn(msg))

    @staticmethod
    def _build_msg(command: str, reason: str, level: int, i: int,
                   stages: int, position: int, token: str) -> str:
        pos_label = MultiStageConfirm.BUTTON_POSITIONS[position % len(MultiStageConfirm.BUTTON_POSITIONS)]
        risk_word = "极高" if level >= 5 else "高"
        return (
            f"⚠️ 第 {i+1}/{stages} 次警告 ({pos_label})\n"
            f"命令: {command}\n"
            f"风险: {reason}\n"
            f"此操作具有{risk_word}风险, 请确认。\n"
            f"为防误触/肌肉记忆, 请键入本步骤的确认码以授权:\n"
            f"  >>> 确认码: {token} <<<\n"
            f"(直接回车或输入其他内容将拒绝执行)"
        )
