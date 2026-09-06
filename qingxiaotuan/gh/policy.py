"""GitHub 安全分级策略引擎 —— 永不绕过的破坏性操作拦截。

分级 (Action):
  SAFE      可读/本地克隆: 直接放行 (原生体验, 无闸门)。
  GUARDED   远端状态变更 (非破坏): 放行前打印影响提示, 保持原生体验。
  FORBIDDEN 破坏性 (删除/注销/任意 -X DELETE): **硬性拒绝**, 永不执行。
            即便带 --yes / --force / -f / -D / -y 也一律拒绝。

判定不依赖具体命令是否真实存在, 只做 **argv 词法扫描**, 保证任何拼装出来的
破坏性调用 (包括 `gh api -X DELETE /repos/...`) 都能被拦截。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# 破坏性动词: 在破坏性命令语境下出现即 FEORBIDDEN。
_FORBIDDEN_VERBS = {"delete", "remove", "rm", "del", "drop", "prune"}

# ``gh api`` 相关的 HTTP 方法 (必须大写, 来自 -X/--method)。
_DESTRUCTIVE_METHODS = {"DELETE"}
_MUTATIVE_METHODS = {"POST", "PATCH", "PUT"}

# 破坏性子命令的"主词" (删除这些资源 = 不可逆)。
_DESTRUCTIVE_SCOPES = {
    "repo", "gist", "release", "secret", "ssh-key", "alias", "ext", "extension",
    "environment", "org", "codespace", "token", "label", "milestone", "branch",
    "ruleset", "workflow",
}

# 受保护 (非破坏远端写): 打印影响提示即可放行。
_GUARDED_VERBS = {
    "create", "open", "edit", "close", "reopen", "merge", "transfer", "fork",
    "enable", "disable", "set", "unset", "add", "remove-member", "approve",
    "comment", "label", "assign", "unassign", "recycle", "rollback", "rerun",
    "import", "prerun", "run", "cancel", "watch", "unwatch", "star", "unstar",
    "convert", "lock", "unlock", "promote", "demote", "grant", "revoke", "invite",
}


class Action(str, Enum):
    SAFE = "safe"
    GUARDED = "guarded"
    FORBIDDEN = "forbidden"


FORBIDDEN_HINT = (
    "青小团奉行最小破坏原则: 该操作被视为破坏性 (删除/注销/不可逆), 已被策略引擎拦截, "
    "永不执行。即便附加 --yes/--force/-D 也无法绕过。请在仓库/账户中手动谨慎操作, "
    "或联系管理员。"
)


@dataclass
class SafetyPolicy:
    """可配置但不可放宽破坏性阈值的安全策略。"""

    # 用户自定义 (仅能在 SAFE 内加白, 不能把 GUARDED/FORBIDDEN 降级为 SAFE)。
    allow_extra: List[str] = field(default_factory=list)   # 额外放行的"主词"
    guard_extra: List[str] = field(default_factory=list)   # 额外加为受保护
    # 是否在受保护命令前打印影响提示 (默认开)。
    verbose_guarded: bool = True


def _command_words(argv: List[str]) -> List[str]:
    """提取命令的"位置词"序列, 排除一切 flag 及其取值。

    这是分类的核心: 破坏性/变更判定只作用在这些**命令词**上,
    从而避免把 ``-f key=delete`` ``-H 头 value`` 里的字段值/头值误判为动词
    (修复误伤: ``api -H ... search remove`` 中 remove 是查询数据, 不是删除命令)。
    """
    words: List[str] = []
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok.startswith("-"):
            # flag: 单 token 自带值 (--key=val / -fkey) 或布尔, 整体跳过
            if "=" in tok:
                i += 1
                continue
            # 取值型 flag : 若下一 token 存在且不是 flag, 一并视为其值跳过
            if i + 1 < n and not argv[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        words.append(tok.lower().replace("/", " ").strip())
        i += 1
    return words


def _extract_method(argv: List[str]) -> Optional[str]:
    """从 argv 中解析 http 方法覆盖 (-X/-x/--method)。"""
    for i, tok in enumerate(argv):
        t = tok.lower()
        if t in ("-x", "--method"):
            if i + 1 < len(argv):
                m = argv[i + 1].upper()
                if m in ("GET", "HEAD", "DELETE", "POST", "PATCH", "PUT"):
                    return m
        if t.startswith("--method="):
            m = t.split("=", 1)[1].upper()
            if m in ("GET", "HEAD", "DELETE", "POST", "PATCH", "PUT"):
                return m
        if t.startswith("-x") and len(t) > 2 and not t.startswith("--"):
            m = t[2:].upper()
            if m in ("GET", "HEAD", "DELETE", "POST", "PATCH", "PUT"):
                return m
    return None


def classify(argv: List[str], policy: Optional[SafetyPolicy] = None) -> Action:
    """对一条即将交给 gh 的命令 argv 做安全分级。纯函数, 便于测试。

    判定基于「命令位置词」而非全部 token, 破坏性判定不可被 --yes/--force 绕过。
    """
    pol = policy or SafetyPolicy()
    words = _command_words(argv)
    method = _extract_method(argv)

    # 1) 破坏性方法: 任意 DELETE → 禁止 (覆盖注销账户/删仓库的 API 拼装)。
    if method == "DELETE":
        return Action.FORBIDDEN

    # 2) 作用域主词 + 紧跟的破坏性动词 → 禁止 (repo delete / gist remove …)。
    for i, w in enumerate(words):
        if i > 0 and words[i - 1] in _DESTRUCTIVE_SCOPES and w in _FORBIDDEN_VERBS:
            return Action.FORBIDDEN

    # 3) 顶层裸破坏性动词 (前面无合法读词) → 禁止。
    if words and words[0] in _FORBIDDEN_VERBS:
        return Action.FORBIDDEN

    # 4) 任何 -X POST/PATCH/PUT 或变更动词 → 受保护。
    if method in _MUTATIVE_METHODS:
        return Action.GUARDED
    if any(w in _GUARDED_VERBS for w in words):
        return Action.GUARDED
    if any(g in words for g in pol.guard_extra):
        return Action.GUARDED

    # 5) 其余 (view/list/search/status/api GET/clone/auth status/...) → 安全。
    return Action.SAFE


def _policy_path(home: Optional[Path] = None) -> Path:
    if home is None:
        from ..config.loader import home_dir
        home = home_dir()
    return Path(home) / "gh.policy.json"


def load_policy(home: Optional[Path] = None) -> SafetyPolicy:
    """读取策略配置 (~/.qingxiaotuan/gh.policy.json)。缺失/损坏则用安全默认值。

    注意: 配置只能**加**受保护, 不能把破坏性降到放行 —— 破坏性判定在
    ``classify`` 中是硬编码的, 与配置文件解耦。
    """
    p = _policy_path(home)
    pol = SafetyPolicy()
    if not p.exists():
        return pol
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return pol
    pod = isinstance(data, dict) and data
    pol.allow_extra = [str(s) for s in pod.get("allow_extra", [])]
    pol.guard_extra = [str(s) for s in pod.get("guard_extra", [])]
    pol.verbose_guarded = bool(pod.get("verbose_guarded", True))
    return pol


def write_policy(policy: SafetyPolicy, home: Optional[Path] = None) -> Path:
    """把策略写盘 (供 `qxt gh policy set` 使用)。"""
    p = _policy_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "allow_extra": policy.allow_extra,
        "guard_extra": policy.guard_extra,
        "verbose_guarded": policy.verbose_guarded,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p