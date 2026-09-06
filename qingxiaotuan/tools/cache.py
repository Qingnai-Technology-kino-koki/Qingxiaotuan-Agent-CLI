"""工具结果缓存 —— 对「纯查询、无副作用」的工具调用做短期记忆化。

解决的问题:
- Agent 在多轮循环里经常重复调用 read_file / codebase_map / find_symbol 等只读工具,
  每次都走真实 IO 或甚至重新触发模型摘要, 既慢又费 token。
- 只对显式标注 cacheable=True 的工具生效, 写类/危险工具不参与。

设计:
- 以 (tool_name + 规范化参数) 为键, 缓存最近 N 条结果, TTL 默认 60s。
- 危险工具 (dangerous) 永远不缓存, 即便调用方误标。
- 命中时返回 "[缓存] " 前缀的结果, 便于 UI/日志识别。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class _Entry:
    result: str
    expires: float


class ToolResultCache:
    def __init__(self, max_entries: int = 200, ttl: float = 60.0) -> None:
        self.max_entries = max_entries
        self.ttl = ttl
        self._store: Dict[str, _Entry] = {}

    @staticmethod
    def _key(name: str, arguments_json: str, workspace: str = "") -> str:
        norm = arguments_json
        try:
            # 参数顺序无关化: 排序 key 后重新序列化, 让 {a:1,b:2} 与 {b:2,a:1} 命中同一缓存
            obj = json.loads(arguments_json) if arguments_json else {}
            norm = json.dumps(obj, sort_keys=True, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            norm = arguments_json
        # workspace 纳入键: 只读工具的结果可能依赖工作区 (如相对路径/索引),
        # 否则切换工作区后会命中上一个工作区的陈旧结果 (stale)。
        raw = f"{name}\x00{workspace}\x00{norm}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def get(self, name: str, arguments_json: str, dangerous: bool = False,
            workspace: str = "") -> Optional[str]:
        if dangerous:
            return None
        key = self._key(name, arguments_json, workspace)
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires < time.time():
            self._store.pop(key, None)
            return None
        return "[缓存] " + entry.result

    def put(self, name: str, arguments_json: str, result: str, dangerous: bool = False,
            workspace: str = "") -> None:
        if dangerous:
            return
        if self.ttl <= 0:
            return
        key = self._key(name, arguments_json, workspace)
        self._store[key] = _entry(result, time.time() + self.ttl)
        # 简单 LRU 裁剪: 超出上限删最旧
        if len(self._store) > self.max_entries:
            oldest = min(self._store, key=lambda k: self._store[k].expires)
            self._store.pop(oldest, None)

    def clear(self) -> None:
        self._store.clear()

    def stats(self) -> Tuple[int, int]:
        """返回 (缓存条目数, 当前时间下未过期数)。"""
        now = time.time()
        alive = sum(1 for e in self._store.values() if e.expires >= now)
        return (len(self._store), alive)


def _entry(result: str, expires: float) -> _Entry:
    return _Entry(result=result, expires=expires)
