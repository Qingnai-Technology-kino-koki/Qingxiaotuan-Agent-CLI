"""用户本地黑名单减负 (blacklist override)。

背景
----
内置安全引擎有一套「致命红线模式库」 (_CRITICAL_PATTERNS / _HIGH_PATTERNS /
_MEDIUM_PATTERNS, 见 ext/safety_engine.py), 用来在 shell 护栏 / MCP 守卫 / 脚本内容
检查里拦截危险命令。这套库是「单一来源、宁可错杀」的全局口径, 但在某些用户的特定环境
里, 部分模式属于误杀, 或用户明确信任这些操作。

本模块允许用户在本地 ``~/.qingxiaotuan/blacklist-override.json`` 中声明要「抑制」
(suppress) 的模式 label 关键字; 被抑制的模式在 ``is_redline`` / ``is_hard_redline`` /
``SafetyEngine.score`` 中不再触发命中 —— 即「用户本地自主减负黑名单」。

安全边界
--------
被抑制的**只**是上述正则模式库里的条目 (label 关键字匹配)。不可逆 / OS 级的 token 化
硬红线 (``rm -rf /``、``git push --force``、``shutdown``、``dd if=... of=/dev/...`` 等)
走的是 ``has_recursive_rm`` / ``has_force_push`` / ``has_system_shutdown`` 等独立判定,
**永不**受本模块影响, 因为它们不属于「可减负」的黑名单库, 必须始终拦截。

抑制规则
--------
按 label 关键字匹配: 任一被抑制关键字若「等于」某模式 label, 或「出现在」label 中,
即视为该模式被抑制。例如声明 ``dd`` 会抑制 label 含 "dd" 的 "write raw device" 模式,
但不影响 label 里没有 "dd" 的其它模式。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Set

try:  # 延迟到运行时, 避免任何导入副作用
    from ..config.loader import home_dir
except Exception:  # noqa: BLE001
    home_dir = None  # type: ignore

_OVERRIDE_FILE = "blacklist-override.json"
_suppressed_cache: Set[str] = set()
_cache_loaded = False


def _override_path() -> Path:
    if home_dir is not None:
        try:
            return home_dir() / _OVERRIDE_FILE
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / ".qingxiaotuan" / _OVERRIDE_FILE


def _load_raw() -> dict:
    try:
        p = _override_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _load_suppressed() -> Set[str]:
    global _suppressed_cache, _cache_loaded
    if _cache_loaded:
        return _suppressed_cache
    raw = _load_raw()
    items = raw.get("suppressed") or []
    _suppressed_cache = {str(x).strip() for x in items if str(x).strip()}
    _cache_loaded = True
    return _suppressed_cache


def reset_cache() -> None:
    """测试用: 清空内存缓存, 强制下次从磁盘重读。"""
    global _suppressed_cache, _cache_loaded
    _suppressed_cache = set()
    _cache_loaded = False


def suppressed_labels() -> Set[str]:
    """返回当前被抑制的 label 关键字集合 (内存缓存)。"""
    return set(_load_suppressed())


def is_suppressed(label: str) -> bool:
    """判断某个黑名单模式 label 是否被用户抑制 (命中即跳过该模式)。"""
    if not label:
        return False
    for kw in _load_suppressed():
        if kw == label or kw in label:
            return True
    return False


def _save_raw(raw: dict) -> None:
    try:
        p = _override_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        global _cache_loaded
        _cache_loaded = False  # 写盘后让缓存失效, 下次从磁盘重读
    except Exception:  # noqa: BLE001
        pass


def suppress(keyword: str) -> bool:
    """抑制包含该关键字的黑名单模式。返回是否产生了变化。"""
    kw = (keyword or "").strip()
    if not kw:
        return False
    raw = _load_raw()
    items = list(raw.get("suppressed") or [])
    if kw in items:
        return False
    items.append(kw)
    raw["suppressed"] = items
    if "version" not in raw:
        raw["version"] = 1
    _save_raw(raw)
    return True


def release(keyword: str) -> bool:
    """释放 (恢复) 之前抑制的关键字。返回是否产生了变化。"""
    kw = (keyword or "").strip()
    if not kw:
        return False
    raw = _load_raw()
    items = list(raw.get("suppressed") or [])
    if kw not in items:
        return False
    items.remove(kw)
    raw["suppressed"] = items
    _save_raw(raw)
    return True


def list_suppressed() -> List[str]:
    """已抑制关键字列表 (排序后)。"""
    return sorted(suppressed_labels())
