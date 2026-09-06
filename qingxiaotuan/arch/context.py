"""上下文层 — 1M 上下文缓存友好 + 事件溯源 + 分叉重放 + 自动技能蒸馏。

四块能力:
1. TieredContext  —— 三层式上下文 (hot 工作窗口 / warm 摘要 / cold 事件溯源日志),
                      支持 prompt-cache 锚点 (cache_control) 与 token 预算, 为 1M 级
                      上下文做"稳定前缀 + 滚动窗口"的缓存友好布局。
2. ContextEventLog —— 在 core.event_sourcer.EventSourcer 之上记录所有上下文变更
                      (append-only), 单一真相源。
3. fork_and_replay —— 从任意序号分叉出新上下文, 并把历史事件重放到子上下文。
4. SkillDistiller  —— 观察上下文/轨迹, 把高频成功模式自动蒸馏成技能 (桥接
                      self_improve.auto_distiller, 不可用时有内置轻量蒸馏)。
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.event_sourcer import EventSourcer, SourcedEvent


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算: 中文约 1.5 字/token, 英文约 4 字符/token; 这里取字符/4 近似。"""
    return max(1, len(text) // 4)


# ============================================================ 分层上下文
@dataclass
class ContextEntry:
    role: str            # system / user / assistant / tool
    content: str
    cache_anchor: bool = False   # 是否作为 prompt-cache 锚点 (稳定前缀)
    tokens: int = 0
    seq: int = 0

    def __post_init__(self):
        if self.tokens == 0:
            self.tokens = _estimate_tokens(self.content)


class TieredContext:
    """1M 上下文缓存友好的分层上下文。

    - hot:    最近 N 条消息 (滚动窗口), 每次都发给模型
    - warm:   被压缩的摘要块, 作为稳定前缀锚点 (命中 prompt cache)
    - cold:   完整的事件溯源日志 (不直接进上下文, 按需检索)
    渲染时把 warm(锚点) 放前面, hot 放后面 -> 稳定前缀命中缓存, 长上下文不重复计费。
    """

    def __init__(self, budget_tokens: int = 200_000, hot_capacity: int = 40) -> None:
        self.budget = budget_tokens
        self.hot_capacity = hot_capacity
        self.hot: List[ContextEntry] = []
        self.warm: List[ContextEntry] = []   # 摘要块, 带 cache_anchor
        self._seq = 0
        self.compactions = 0

    # ---- 写入 ----
    def add(self, role: str, content: str, cache_anchor: bool = False) -> ContextEntry:
        self._seq += 1
        entry = ContextEntry(role=role, content=content, cache_anchor=cache_anchor, seq=self._seq)
        if cache_anchor:
            self.warm.append(entry)   # 锚点进 warm (稳定前缀)
        else:
            self.hot.append(entry)
            self._maybe_compact()
        return entry

    def anchor_system(self, content: str) -> ContextEntry:
        """注册系统提示为永久 cache 锚点 (最稳定前缀)。"""
        return self.add("system", content, cache_anchor=True)

    # ---- 压缩 ----
    def _maybe_compact(self) -> None:
        total = sum(e.tokens for e in self.hot)
        if total <= self.budget and len(self.hot) <= self.hot_capacity:
            return
        # 把一半最旧的 hot 压缩成 warm 摘要
        half = max(1, len(self.hot) // 2)
        to_compact = self.hot[:half]
        self.hot = self.hot[half:]
        summary = self._summarize(to_compact)
        self.warm.append(ContextEntry("system", summary, cache_anchor=True, seq=self._seq))
        self.compactions += 1

    @staticmethod
    def _summarize(entries: List[ContextEntry]) -> str:
        """从一组上下文条目中提取关键信息, 生成结构化摘要。

        策略 (extractive, 无 LLM 依赖):
        - 保留 system 提示的前 200 字符 (通常包含关键指令)
        - 提取 user/assistant 消息的第一句和最后一句 (首尾锚定)
        - 提取所有 tool 结果的「状态+名称」(仅保留工具链路, 丢弃返回体)
        - 拼接成紧凑摘要, 控制在 800 字符以内
        """
        import re
        parts: List[str] = []
        # 1) system 消息: 取前 200 字符
        for e in entries:
            if e.role == "system":
                snippet = e.content[:200].strip()
                if snippet:
                    parts.append(f"[SYS] {snippet}")
        # 2) user / assistant: 取首句 + 末句 (若不同)
        #    改进: 处理代码块 (```...```) 和 markdown 标题, 不截断代码
        for e in entries:
            if e.role in ("user", "assistant"):
                text = e.content.strip()
                if not text:
                    continue
                label = "USR" if e.role == "user" else "ASR"
                # 检测代码块: 以 ``` 开头, 或包含 ``` 的混合内容
                if "```" in text:
                    # 取第一个 ``` 之前的内容作为说明
                    pre = text.split("```")[0].strip()
                    if pre:
                        parts.append(f"[{label}] (code) {pre[:100]}")
                    else:
                        parts.append(f"[{label}] (code block)")
                    continue
                # 按句子分割: 支持中英文句号/感叹号/问号/换行
                sentences = re.split(r"(?<=[.!?。！？\n])\s*", text)
                sentences = [s.strip() for s in sentences if s.strip()]
                if not sentences:
                    continue
                first = sentences[0][:120]
                last = sentences[-1][:120] if len(sentences) > 1 else ""
                if last and last != first:
                    parts.append(f"[{label}] {first} … {last}")
                else:
                    parts.append(f"[{label}] {first}")
        # 3) tool: 只保留工具名+状态 (丢弃返回体)
        #    状态推断: 覆盖 [错误]/[超时]/[拒绝]/[已限流]/[已禁用]/[安全拦截]/[cached]
        _TOOL_STATUS = [
            ("[错误]", "err"), ("[超时]", "timeout"), ("[拒绝", "denied"),
            ("[已限流]", "rate-limited"), ("[已禁用]", "disabled"),
            ("[安全拦截]", "blocked"), ("[cached]", "cached"),
        ]
        tool_names: List[str] = []
        for e in entries:
            if e.role == "tool":
                content = e.content.strip()
                status = "ok"
                for prefix, code in _TOOL_STATUS:
                    if content.startswith(prefix):
                        status = code
                        break
                tool_names.append(f"{content[:30]}({status})")
        if tool_names:
            # 去重保序: 按工具名去重 (同一工具只保留首次出现的条目)
            # 工具名提取: 跳过状态前缀 + 中文填充词, 取第一个实际工具名
            _STATUS_PREFIXES = ("[错误]", "[超时]", "[拒绝", "[已限流]",
                                "[已禁用]", "[安全拦截]", "[cached]")
            _FILLER_WORDS = {"工具", "的", "被", "了", "在", "和", "与", "或",
                              "用", "将", "从", "到", "对", "为", "MCP", "mcp"}
            seen_tools: set = set()
            unique: List[str] = []
            for t in tool_names:
                content_part = t.split("(")[0]  # 去掉状态后缀 (ok)/(err) 等
                # 跳过状态前缀
                tool_name = content_part
                for pfx in _STATUS_PREFIXES:
                    if tool_name.startswith(pfx):
                        tool_name = tool_name[len(pfx):].strip()
                        break
                # 跳过中文填充词, 取第一个实际工具名
                words = tool_name.split()
                first_word = ""
                for w in words:
                    if w not in _FILLER_WORDS and not all(ord(c) > 127 for c in w):
                        first_word = w
                        break
                if not first_word and words:
                    first_word = words[0]
                if first_word and first_word not in seen_tools:
                    seen_tools.add(first_word)
                    unique.append(t)
            parts.append("[TOOLS] " + " → ".join(unique[:15]))
        # 4) 拼接, 加前缀, 再截断 (前缀约占 40 字符)
        body = "\n".join(parts)
        prefix = f"[compressed #{len(entries)} msgs @ {int(time.time())}]\n"
        if len(prefix) + len(body) > 800:
            body = body[: 800 - len(prefix) - 3] + "..."
        return f"{prefix}{body}"

    # ---- 渲染 (给模型) ----
    def render(self) -> List[Dict[str, Any]]:
        """渲染成 messages, 带 cache_control 锚点 (Anthropic/兼容格式)。"""
        messages: List[Dict[str, Any]] = []
        for e in list(self.warm) + list(self.hot):
            msg: Dict[str, Any] = {"role": e.role, "content": e.content}
            if e.cache_anchor:
                msg["cache_control"] = {"type": "ephemeral"}
            messages.append(msg)
        return messages

    def total_tokens(self) -> int:
        return sum(e.tokens for e in self.warm) + sum(e.tokens for e in self.hot)

    def stats(self) -> Dict[str, Any]:
        return {
            "hot": len(self.hot),
            "warm": len(self.warm),
            "compactions": self.compactions,
            "total_tokens": self.total_tokens(),
            "budget": self.budget,
            "cache_friendly": self.total_tokens() <= self.budget,
        }


# ============================================================ 事件溯源日志
class ContextEventLog:
    """在 core.event_sourcer 之上的上下文事件日志 (append-only 单一真相源)。"""

    def __init__(self, session_id: Optional[str] = None) -> None:
        self.sourcer = EventSourcer()  # 内存态, 不依赖 SessionStore
        self.session_id = session_id or f"ctx-{uuid.uuid4().hex[:8]}"

    def record(self, kind: str, payload: Dict[str, Any], tags: Optional[List[str]] = None) -> SourcedEvent:
        return self.sourcer.emit(kind, payload, session_id=self.session_id, tags=tags)

    def query(self, **kwargs) -> List[SourcedEvent]:
        return self.sourcer.query(**kwargs)

    def events(self) -> List[SourcedEvent]:
        return list(self.sourcer._events)

    def count(self) -> int:
        return self.sourcer.count()

    # EventSourcer.replay 需要一个 "session store" (有 .append / .session_id);
    # 这里让 ContextEventLog 自身充当该角色, 把重放事件并入自己的 sourcer。
    def append(self, d: Dict[str, Any]) -> None:
        """供 EventSourcer.replay 回调: 把重放事件并入本日志。"""
        self.sourcer._events.append(SourcedEvent.from_dict(d))

    # ---- 分叉 / 重放 ----
    def fork(self, at_seq: int, reason: str = "") -> str:
        return self.sourcer.fork(at_seq, reason)

    def replay(self, fork_id: str, to_log: Optional["ContextEventLog"] = None,
               to_seq: Optional[int] = None) -> List[SourcedEvent]:
        target = to_log if to_log else None
        return self.sourcer.replay(fork_id, to_session_store=target, to_seq=to_seq)


# ============================================================ 分叉重放 (便捷函数)
def fork_and_replay(
    source: ContextEventLog,
    at_seq: int,
    reason: str = "branch",
    child: Optional[ContextEventLog] = None,
) -> "tuple[str, ContextEventLog]":
    """从 source 的 at_seq 处分叉, 重放历史到 child (若无则新建), 返回 (fork_id, child)。"""
    fork_id = source.fork(at_seq, reason)
    child = child or ContextEventLog(session_id=f"{source.session_id}-fork")
    source.replay(fork_id, to_log=child)
    return fork_id, child


# ============================================================ 自动技能蒸馏
@dataclass
class DistilledCandidate:
    name: str
    description: str
    trigger: str
    steps: List[str]
    source_tools: List[str]
    frequency: int
    confidence: float


class SkillDistiller:
    """观察上下文事件流, 把高频成功工具序列自动蒸馏为技能候选。

    优先桥接 self_improve.auto_distiller.AutoDistiller (若该环境已装配
    skill_manager / memory_store); 否则用内置轻量聚类。
    """

    def __init__(self, enable_native: bool = True) -> None:
        self.enable_native = enable_native
        self._native = None
        if enable_native:
            try:
                from ..self_improve.auto_distiller import AutoDistiller  # noqa: F401
                self._native_available = True
            except Exception:
                self._native_available = False
        else:
            self._native_available = False

    def distill(self, log: ContextEventLog, min_frequency: int = 2,
                max_ngram: int = 3) -> List[DistilledCandidate]:
        """从事件日志蒸馏技能候选。

        策略: 统计相邻 tool.executed 序列的 n-gram 出现频率 (支持 2-gram 和 3-gram),
        高频 (>= min_frequency) 且成功率高的工具组合 -> 候选技能。
        3-gram 比 2-gram 有更高置信度 (捕获更完整的工作流模式)。
        """
        tool_names = [
            ev.payload.get("name", "")
            for ev in log.events()
            if ev.kind == "tool.executed" and ev.payload.get("status") == "ok"
        ]
        tool_names = [n for n in tool_names if n]  # 过滤空名
        if len(tool_names) < 2:
            return []

        # 统计 n-gram 频率 (2-gram 和 3-gram)
        patterns: Counter = Counter()
        for n in range(2, min(max_ngram + 1, len(tool_names) + 1)):
            for i in range(len(tool_names) - n + 1):
                key = tuple(tool_names[i:i + n])
                patterns[key] += 1

        candidates: List[DistilledCandidate] = []
        seen_names: set = set()
        for seq, freq in patterns.most_common():
            if freq < min_frequency:
                continue
            name = "-then-".join(seq)
            # 去重: 长序列如果与已有的短序列完全重叠, 跳过
            if name in seen_names:
                continue
            seen_names.add(name)

            ngram_size = len(seq)
            # 置信度: 3-gram 比 2-gram 基线更高 (捕获更完整模式)
            base_confidence = 0.5 if ngram_size == 2 else 0.65
            confidence = min(1.0, base_confidence + freq * 0.08)

            candidates.append(DistilledCandidate(
                name=f"learned-{name}",
                description=f"工作流模式: {' → '.join(seq)} (出现 {freq} 次)",
                trigger=f"完成 {seq[0]} 且结果为 ok",
                steps=[f"运行 {t}" for t in seq],
                source_tools=list(seq),
                frequency=freq,
                confidence=confidence,
            ))
        candidates.sort(key=lambda c: c.frequency, reverse=True)
        return candidates

    def activate(self, candidate: DistilledCandidate, skill_manager: Any = None) -> Optional[str]:
        """把候选技能落库 (若提供了 skill_manager 或原生 AutoDistiller 可用)。"""
        if skill_manager is not None:
            skill_manager.register(candidate.name, candidate.description, candidate.steps)
            return candidate.name
        return None
