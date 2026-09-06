"""MCP Tool Search —— MCP 工具上下文降耗 (对标 Claude Code 的 MCP Tool Search)。

背景
----
当一个 MCP server 暴露几十乃至上百个工具、且每个工具带大段 JSON Schema 描述时，
把全部工具 schema 原样塞进系统提示词会侵占上下文窗口的很大比例 (常见于 MCP 工具数量多、
描述肥的情形)。Claude Code 的做法是: 当 MCP 工具信息占用上下文超过阈值 (默认约 10%)
时, 不再全量暴露 MCP 工具 schema, 而是:
  1. 把一个专门的 ``mcp__tool_search`` 搜索工具注入模型 (只占极小上下文);
  2. 完整 schema 移入本地索引, 模型按需用搜索工具检索, 命中后把相关工具的完整
     schema 作为搜索结果返回, 再对命中工具发起正常调用。

本实现提供:
- ``ContextBudget``: 估算工具 schema 的 token 成本, 判断是否触发"搜索模式"。
- ``MCPToolSearchEngine``: 维护 MCP 工具目录 + 关键词倒排索引, 提供相关性打分检索,
  并把完整工具列表"降耗"成一个 (搜索 stub + 其余非 MCP 工具) 的列表。
- 生成与 Claude Code 兼容的 ``mcp__tool_search`` 工具声明, 供模型按需检索。

设计约束
--------
- 只读工具 (read_only=True): 搜索 stub 在 Plan 模式下仍然可用。
- 完整 MCP 工具仍保留在注册表中 (已注册), 搜索返回 schema 后模型发起的普通
  ``mcp__xxx`` 调用照常 dispatch, 无需动态注册。
- 零副作用: 本模块不触碰工具执行/安全闸门, 只改"模型可见的工具清单"。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import Tool

log = logging.getLogger(__name__)


# ================================================================ 常量

# 默认上下文窗口 (token)。实际以 config mcp.tool_search.context_window 优先。
DEFAULT_CONTEXT_WINDOW: int = 200_000
# 默认触发阈值: MCP 工具 schema token 成本占上下文窗口超过该比例即启用搜索模式。
DEFAULT_THRESHOLD: float = 0.10
# 粗粒度 token 估算: 每 4 个字符约 1 token (英文场景的常用近似)。
CHARS_PER_TOKEN: float = 4.0
# 默认返回命中的工具数量上限。
DEFAULT_TOP_K: int = 5
# 搜索 stub 的最小注入成本, 用于即使索引被禁用也能稳定触发搜索。
_SEARCH_STUB_COST_TOKENS: int = 120

_STOPWORDS: Set[str] = {
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with",
    "tool", "mcp", "mcp__", "server", "this", "that", "from", "function",
    "工具", "用于", "提供", "当前", "以及", "一个", "来自", "的", "并", "个",
}


def estimate_tokens(schema_or_text: Any) -> int:
    """粗粒度估算一段文本/JSON Schema 的 token 数。

    以字符数 / CHARS_PER_TOKEN 近似的实现, 不依赖真实 tokenizer —— 目的是做
    "相对成本对比" (阈值判断), 而非精确计量, 避免引入重量级依赖。
    """
    try:
        if not isinstance(schema_or_text, str):
            schema_or_text = json.dumps(schema_or_text, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        schema_or_text = str(schema_or_text)
    return max(1, int(len(schema_or_text) / CHARS_PER_TOKEN))


def _tokenize(text: str) -> Set[str]:
    """提取文本的 token 小写集合 (过滤停用词/纯符号)。"""
    tokens = {w for w in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", (text or "").lower()) if w}
    return {t for t in tokens if t not in _STOPWORDS}


class ContextBudget:
    """把一组工具按"占上下文比例"归一化, 判断是否应启用搜索模式。

    - ``measure(tools) -> (total_tokens, mcp_tokens)``: 统计全部工具与其中 MCP 工具的
      schema token 成本。
    - ``should_search(mcp_tokens) -> bool``: mcp 占比是否超过阈值。
    """

    def __init__(self, context_window: int = DEFAULT_CONTEXT_WINDOW,
                 threshold: float = DEFAULT_THRESHOLD) -> None:
        self.context_window = max(1000, int(context_window))
        self.threshold = max(0.0, min(1.0, float(threshold)))

    def mcp_only(self, tools: List[Tool]) -> List[Tool]:
        return [t for t in tools if (t.name or "").startswith("mcp__")]

    def measure(self, tools: List[Tool]) -> Tuple[int, int]:
        """返回 (全部工具 schema token 数, 其中 MCP 工具的 schema token 数)。"""
        total = mcp_tokens = 0
        for t in tools:
            try:
                cost = estimate_tokens(t.schema())
            except Exception:  # noqa: BLE001
                cost = 1
            total += cost
            if (t.name or "").startswith("mcp__"):
                mcp_tokens += cost
        return total, mcp_tokens

    def should_search(self, mcp_tokens: int) -> bool:
        if mcp_tokens <= 0:
            return False
        return (mcp_tokens / self.context_window) >= self.threshold

    def utilization(self, mcp_tokens: int) -> float:
        return mcp_tokens / self.context_window


class MCPToolSearchEngine:
    """MCP 工具目录 + 搜索索引 + 上下文降耗。

    用法::

        engine = MCPToolSearchEngine(budget=ContextBudget())
        engine.add(tool)              # 每个已注册 MCP 工具加入目录
        reduced = engine.reduce(tools)  # 传入完整工具列表, 返回(可能降耗后的)列表
    """

    def __init__(self,
                 budget: Optional[ContextBudget] = None,
                 top_k: int = DEFAULT_TOP_K,
                 enabled: Optional[bool] = None,
                 max_sticky: int = 3) -> None:
        self.budget = budget or ContextBudget()
        self.top_k = max(1, int(top_k))
        # enabled: None=按阈值自动; True/False=强制启用/禁用
        self.enabled = enabled
        # 已被模型通过检索"发现"并保持驻留的工具名 (上限 max_sticky 个)。
        # 驻留后其在降耗模式下仍以完整 schema 保留在上下文中, 无需重复检索。
        self.max_sticky = max(0, int(max_sticky))
        self._sticky: List[str] = []
        self._tools: List[Tool] = []
        self._index: Dict[str, Set[str]] = {}  # 关键词 -> {工具名, ...}
        self._lock = threading.Lock()
        self._search_calls: List[str] = []
        self._stub: Optional[Tool] = None

    # ------------------------------------------------------------ 目录维护

    def add(self, tool: Tool) -> None:
        """把一个 MCP 工具纳入目录 + 倒排索引 (幂等)。"""
        if tool is None or not (tool.name or "").startswith("mcp__"):
            return
        with self._lock:
            if any(t.name == tool.name for t in self._tools):
                return
            self._tools.append(tool)
            text = f"{tool.name} {tool.description}"
            for kw in _tokenize(text):
                self._index.setdefault(kw, set()).add(tool.name)

    def remove(self, name: str) -> None:
        with self._lock:
            self._tools = [t for t in self._tools if t.name != name]
            for kw in list(self._index):
                self._index[kw].discard(name)
                if not self._index[kw]:
                    del self._index[kw]

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def mcp_tokens(self) -> int:
        return self.budget.measure(self._tools)[1]

    # ------------------------------------------------------------ 检索

    def search(self, query: str, top_k: Optional[int] = None,
               sticky: bool = False) -> List[Dict[str, Any]]:
        """按相关性返回命中的 MCP 工具完整 schema (OpenAI function 声明)。

        打分: 名称/描述 token 与查询 token 重合数为主序, 名称精确命中 +3 (名称是
        最强信号), 描述包含原始查询 +2; 重名工具靠前。结果按分数降序, 返回 top_k 个。

        ``sticky=True`` 时把命中的前几个工具名加入驻留集 (搜索处理后保持完整 schema
        在上下文中), 供后续轮次直接引用, 无需重复检索。
        """
        self._search_calls.append(query)
        k = self.top_k if top_k is None else max(1, int(top_k))
        if not self._tools or not query:
            return []
        q_tokens = _tokenize(query)

        scored: List[Tuple[float, Tool]] = []
        for tool in self._tools:
            name_l = _tokenize(tool.name)
            text = f"{tool.name} {tool.description}"
            desc_l = _tokenize(text)
            name_hits = len(name_l & q_tokens)
            desc_hits = len(desc_l - name_l & q_tokens)
            if not name_hits and not desc_hits:
                continue
            score = float(desc_hits) + name_hits * 3.0
            if query.strip().lower() in text.lower():
                score += 2.0
            scored.append((score, tool))

        scored.sort(key=lambda p: p[0], reverse=True)
        hits = scored[:k]
        if sticky and hits:
            self.pin([t.name for _, t in hits])
        return [t.schema() for _, t in hits]

    def pin(self, names: List[str]) -> None:
        """把命名的 MCP 工具加入驻留集 (FIFO, 上限 max_sticky)。"""
        if self.max_sticky <= 0:
            return
        known = {t.name for t in self._tools}
        to_add = [n for n in names if n in known and n not in self._sticky]
        self._sticky.extend(to_add)
        if len(self._sticky) > self.max_sticky:
            self._sticky = self._sticky[-self.max_sticky:]

    def unpin(self, name: str) -> None:
        if name in self._sticky:
            self._sticky.remove(name)

    @property
    def search_call_count(self) -> int:
        return len(self._search_calls)

    # ------------------------------------------------------------ 上下文降耗

    def _make_stub(self) -> Tool:
        """构建 ``mcp__tool_search`` 搜索工具 (只读)。

        参数与 Claude Code 对齐: ``search``(检索词) + ``enabled``(强制开关)。
        """
        desc = (
            "Search over MCP tools from connected servers. When multiple MCP tools "
            "are available, use this to find the tools whose schemas you need. "
            "Provide a concise search query (tool name or capability keywords). "
            "Returns full schemas for the most relevant tools."
        )
        params = {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Search query describing the MCP tool you need "
                                   "(e.g. 'read file', 'notion page tool').",
                },
            },
            "required": ["search"],
        }
        return Tool(
            name="mcp__tool_search",
            description=desc,
            parameters=params,
            handler=self._handle_search,
            read_only=True,
            group="mcp",
        )

    def _handle_search(self, _ctx, **kwargs) -> str:
        query = str(kwargs.get("search", "") or "")
        hits = self.search(query, sticky=True)
        if not hits:
            return (
                "没有匹配到 MCP 工具。已收录 %d 个工具, 请换更精确的关键词重试。"
                % self.tool_count
            )
        body = json.dumps(hits, ensure_ascii=False, indent=2)
        return f"找到 {len(hits)} 个匹配的 MCP 工具 (已驻留供后续直接调用):\n{body}"

    @property
    def stub_schema(self) -> Dict[str, Any]:
        return self._stub.schema()

    def install(self, registry: Any) -> "MCPToolSearchEngine":
        """把搜索 stub 注册进 tool_registry 使其可 dispatch, 并返回自身。

        完整 MCP 工具 schema 移入索引后, 模型通过调用 ``mcp__tool_search`` 按需检索;
        该 stub 必须在 registry 中才可被工具执行器分发。
        """
        if self._stub is None:
            self._stub = self._make_stub()
        reg_get = getattr(registry, "register", None)
        if reg_get is not None:
            reg_get(self._stub)
        return self

    def is_search_mode(self, tools: List[Tool]) -> bool:
        """是否应对当前工具列表启用搜索模式 (降耗)。"""
        if self.enabled is False:
            return False
        _total, mcp_tokens = self.budget.measure(tools)
        if self.budget.should_search(mcp_tokens):
            return True
        # enabled=True 强制启用, 即使未超阈也降耗
        return self.enabled is True and mcp_tokens > 0

    def reduce(self, tools: List[Tool]) -> List[Tool]:
        """返回模型可见的工具列表。

        - 若 MCP 成本未超阈值 (且未强制): 原样返回, 并剔除可能已注册的搜索 stub
          (未降耗时不应向模型暴露额外搜索工具)。
        - 否则: 移除所有 MCP 工具的完整 schema (包括 stub 自身), 注入一个
          ``mcp__tool_search`` 搜索 stub。其余 (内置/非 MCP) 工具不受影响。
        """
        stub = self._stub or self._make_stub()
        stub_name = stub.name
        real_mcp = [t for t in tools
                    if (t.name or "").startswith("mcp__") and t.name != stub_name]
        non_mcp = [t for t in tools if not (t.name or "").startswith("mcp__")]

        if not real_mcp:
            # 无真实 MCP 工具: 至少把可能遗留的 stub 摘掉
            return [t for t in tools if t.name != stub_name]

        if self.enabled is False:
            return [t for t in tools if t.name != stub_name]

        _total, mcp_tokens = self.budget.measure(real_mcp)
        if self.enabled is None and not self.budget.should_search(mcp_tokens):
            # 未触发阈值: 保持全量, 不注入搜索 stub
            return [t for t in tools if t.name != stub_name]

        log.info(
            "MCP Tool Search 已启用: MCP 工具 %d 个 schema 约占 %.1f%% 上下文 "
            "(%d tokens), 已降耗为搜索项",
            len(real_mcp), self.budget.utilization(mcp_tokens) * 100, mcp_tokens,
        )
        if self._stub is None:
            self._stub = stub
        # 驻留工具以完整 schema 保留在上下文中 (按当前注册表重建), 其余降耗为搜索项。
        sticky_full = [t for t in real_mcp if t.name in self._sticky]
        return non_mcp + [stub] + sticky_full

    # ------------------------------------------------------------ 观测

    def context_report(self) -> Dict[str, Any]:
        """上下文占用报告 (供 CLI/诊断/TUI 展示)。"""
        total, mcp_tokens = self.budget.measure(self._tools)
        return {
            "mode": "search" if self.is_search_mode(self._tools) else "inline",
            "mcp_tools": len(self._tools),
            "mcp_tokens": mcp_tokens,
            "total_tokens": total,
            "utilization": round(self.budget.utilization(mcp_tokens), 4),
            "threshold": self.budget.threshold,
            "context_window": self.budget.context_window,
            "search_calls": len(self._search_calls),
        }