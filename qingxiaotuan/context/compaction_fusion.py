"""compaction_fusion.py —— 深度融合层: 把 kernel 的上下文压缩「head / tail / elision」策略
graft 进原生 ContextManager (context/manager.py)。

与原生「保留最近 keep_recent + 折叠中间」策略不同, kernel 的压缩形状是:
  [system] + [head: 最旧若干] + [elision 标记] + [tail: 最新若干] + [summary 摘要]
即**同时保留最旧与最新上下文, 只省略中间**, 更符合长会话「既要记得最早的需求, 又要看得见
刚改的内容」的诉求 (融合百家之长)。

融合接缝: 原生 ContextManager 默认仍走原生策略; 仅当 fusion.context_compaction=True 时,
_compact_once 改用本模块的 compact_head_tail_elision 构造压缩形状。
- 原生 system 前缀永远不动 (缓存友好)。
- 以「消息组」为单位选择 head/tail, 绝不把 assistant(tool_calls) 与其 tool 结果拆开, 保证
  协议合法。
- 默认关闭, 零行为改变; 本模块为纯函数, 不依赖任何外部服务, 可独立测试。

token 估算沿用原生启发式 (英文 ~4 字符/token, 中文 ~1.6 字符/token), 与 manager.py 保持一致。

增强 (v0.4, 对标 CC 上下文质量):
- 语义重要性评分: 决策/错误/路径等信号词加权, 短指令高密度加权 —— 让"哪条该留"有依据。
- 动态预算分配: 为摘要/省略标记预留开销, 头尾预算按重要性而非固定值分配。
- 高价值救援: 中间被省略区域里, 若预算仍有富余, 把重要性最高的消息组按原顺序捞回
  (长会话里"决定/结论"往往散落在中段, 纯头尾保留会丢信号)。
- 压缩质量指标: compaction_metrics 输出压缩率、逐字保留率与摘要恢复率估计。
"""

from __future__ import annotations

import functools
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# 与原生 manager.estimate_tokens 完全一致, 此处自带副本以避免循环 import。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@functools.lru_cache(maxsize=8192)
def estimate_tokens(text: str) -> int:
    """粗略估算 token 数 (lru 缓存: 长会话热路径反复估算同一批消息, 命中率极高)。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk / 1.6 + other / 4)


def _msg_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content") or ""
    if isinstance(content, list):
        return " ".join(str(p) for p in content)
    return str(content)


def estimate_message(msg: Dict[str, Any]) -> int:
    total = estimate_tokens(_msg_text(msg))
    for tc in msg.get("tool_calls", []) or []:
        total += estimate_tokens(str(tc))
    return total


def estimate_messages(messages: List[Dict[str, Any]]) -> int:
    return sum(estimate_message(m) for m in messages)


# ---------------------------------------------------------------- 语义重要性


# 决策/结论信号: 中英双语, 出现即视为高价值内容
_DECISION_MARKERS = re.compile(
    r"(决定|结论|最终|方案|采用|修复|完成|建议|原因|decided|decision|conclusion|"
    r"final|approved|chosen|fixed|reason)",
    re.IGNORECASE,
)
# 错误/失败信号
_ERROR_MARKERS = re.compile(
    r"(失败|错误|异常|崩溃|回滚|error|failed|exception|crash|rollback)",
    re.IGNORECASE,
)
# 文件/符号路径信号
_PATH_RE = re.compile(r"(?:[\w\-.]+/){1,}[\w\-.]+|\.\w+[\w.\-/]*")

_ROLE_BASE = {"user": 2.0, "assistant": 1.6, "tool": 0.8, "system": 0.0}


def message_importance(msg: Dict[str, Any]) -> float:
    """启发式重要性评分 (0 ~ ~4): 用于压缩时决定保留优先级与质量评估。

    信号来源: 角色基础分 + 短指令高密度分 + 决策/错误/路径关键词加分,
    再按长度亚线性衰减 (长消息边际信息密度低)。
    """
    role = msg.get("role", "")
    content = _msg_text(msg)
    if role == "system" or not content:
        return _ROLE_BASE.get(role, 0.5)
    score = _ROLE_BASE.get(role, 1.0)
    if len(content) <= 400:
        score += 0.6  # 决策/指令通常很短, 高密度
    if _DECISION_MARKERS.search(content):
        score += 1.2
    if _ERROR_MARKERS.search(content):
        score += 0.8
    if _PATH_RE.search(content):
        score += 0.8
    return score / (1.0 + len(content) ** 0.25)


# ---------------------------------------------------------------- 消息分组


def _group(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """把连续消息切成「协议合法」的组: 普通消息自成一组; assistant(tool_calls) 与其后
    连续的 tool 结果合并为一组。head/tail 选择以组为单位, 保证不拆分工具调用链。"""
    groups: List[List[Dict[str, Any]]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            grp = [m]
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                grp.append(messages[j])
                j += 1
            groups.append(grp)
            i = j
        else:
            groups.append([m])
            i += 1
    return groups


def _flatten(groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for g in groups:
        out.extend(g)
    return out


def _group_tokens(g: List[Dict[str, Any]]) -> int:
    return sum(estimate_message(m) for m in g)


def _group_importance(g: List[Dict[str, Any]],
                      scorer: Callable[[Dict[str, Any]], float]) -> float:
    """消息组的重要性 = 组内成员重要性之和 (整组保留的收益)。"""
    return sum(scorer(m) for m in g)


# ---------------------------------------------------------------- 压缩形状


def build_compaction_elision_text(omitted_tokens: int) -> str:
    return (
        f"[compaction] 此处省略了部分中间历史: 上方为最早的用户输入, 下方为最近的上下文, "
        f"中间约 {omitted_tokens} tokens 的内容已被折叠, 其要点由对话末尾的摘要覆盖。"
    )


def build_compaction_summary_text(summary: str) -> str:
    return f"[早期上下文摘要]\n{summary}"


def _rescue_groups(
    middle: List[Tuple[int, List[Dict[str, Any]]]],
    remaining: int,
    scorer: Callable[[Dict[str, Any]], float],
) -> List[Tuple[int, List[Dict[str, Any]]]]:
    """从被省略的中间区段里, 按「信息密度」贪婪捞回高价值消息组。

    只选取 token 代价 <= remaining 的组, 输出保持原始顺序, 保证消息时序不乱。
    返回 [(原位置下标, 消息组), ...]。
    """
    if remaining <= 0 or not middle:
        return []
    scored = []
    for idx, g in middle:
        cost = _group_tokens(g)
        if cost <= remaining:
            density = _group_importance(g, scorer) / max(cost, 1)
            scored.append((density, idx, g))
    picked = sorted(scored, key=lambda t: t[0], reverse=True)
    chosen: List[Tuple[int, List[Dict[str, Any]]]] = []
    used = 0
    for _, idx, g in picked:
        cost = _group_tokens(g)
        if used + cost <= remaining:
            chosen.append((idx, g))
            used += cost
    chosen.sort(key=lambda t: t[0])
    return chosen


def compact_head_tail_elision(
    messages: List[Dict[str, Any]],
    summary: str,
    *,
    max_tokens: int = 20_000,
    head_tokens: int = 2_000,
    importance_scorer: Optional[Callable[[Dict[str, Any]], float]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """构造 head / tail / elision 压缩形状 (原生 dict 版本, 对齐 kernel build_compaction_shape)。

    - system 前缀 (若存在) 永远置首且不被压缩。
    - 以消息组为单位在 head/tokens 预算内选取最旧(head)与最新(tail), 中间用 elision 标记省略。
    - 为摘要/省略标记预留 token 开销, 避免压缩结果"表面达标、实际超预算"。
    - importance_scorer 注入时启用「高价值救援」: 富余预算优先捞回中段高重要性消息组。
    - 末尾追加 summary 摘要消息。
    返回新的消息列表; 若总 token 已 <= max_tokens 则原样返回 (无需压缩)。
    """
    system: List[Dict[str, Any]] = []
    body = messages
    if body and body[0].get("role") == "system":
        system = [body[0]]
        body = body[1:]

    groups = _group(body)
    # 「总 token」应含 system 前缀 (system 永不压缩但占用预算): 否则当 body 本身
    # 未超预算而 system+body 已超时, 会错误地原样返回, 导致长会话无法压缩。
    total = estimate_messages(messages)
    if total <= max_tokens:
        return list(messages)

    # ---- 预算分配: 先为摘要 + 省略标记预留开销 ----
    overhead = (
        estimate_tokens(build_compaction_elision_text(0))
        + estimate_tokens(build_compaction_summary_text(summary))
    )
    reserve = min(overhead, max(max_tokens // 3, 1))
    available = max(1, max_tokens - reserve)

    # head/tail 分账: 无重要性评分时保持旧语义 (head 优先), 有评分时对半分账,
    # 保证 tail 永远有预算保留最近上下文 (对标「最近上下文最宝贵」不变量)。
    if importance_scorer is None:
        head_budget = min(max(head_tokens, 0), available)
        tail_budget = available - head_budget
        if tail_budget <= 0:
            head_budget = available // 2
            tail_budget = available - head_budget
    else:
        head_budget = available // 2
        tail_budget = available - head_budget

    # ---- 从末尾构建 tail ----
    tail_groups: List[List[Dict[str, Any]]] = []
    tail_rem = tail_budget
    head_end = len(groups)
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        cost = _group_tokens(g)
        if cost <= tail_rem:
            tail_groups.insert(0, g)
            tail_rem -= cost
            head_end = i
            continue
        # 单组超预算: 不再裁剪 (elision/summary 已覆盖), 停止。
        head_end = i
        break

    # ---- 从头构建 head (groups[:head_end]) ----
    head_groups: List[List[Dict[str, Any]]] = []
    head_rem = head_budget
    for i in range(head_end):
        g = groups[i]
        cost = _group_tokens(g)
        if cost <= head_rem:
            head_groups.append(g)
            head_rem -= cost
            continue
        break

    # ---- 高价值救援: 富余预算捞回中段重要消息组 (仅开启重要性评分时) ----
    rescued: List[Tuple[int, List[Dict[str, Any]]]] = []
    if importance_scorer is not None:
        remaining = (head_rem + tail_rem) + reserve  # 预留 + 头尾未花完都可用于救援
        middle = [
            (i, g) for i in range(len(head_groups), head_end)
            if all(importance_scorer(m) > 0.2 for m in g)
        ]
        rescued = _rescue_groups(middle, remaining, importance_scorer)

    elided = (head_end > len(head_groups)) or bool(tail_groups)
    kept_groups = head_groups + [g for _, g in rescued] + tail_groups
    kept_tokens = sum(_group_tokens(g) for g in kept_groups)
    omitted = max(0, total - kept_tokens)

    result: List[Dict[str, Any]] = []
    result.extend(system)
    result.extend(_flatten(head_groups))
    if rescued:
        result.extend(_flatten([g for _, g in rescued]))
    if elided:
        result.append({"role": "user", "content": build_compaction_elision_text(omitted)})
    result.extend(_flatten(tail_groups))
    result.append({"role": "user", "content": build_compaction_summary_text(summary)})

    if stats is not None:
        stats.update(compaction_metrics(messages, result, importance_scorer))
    return result


# ---------------------------------------------------------------- 质量指标


def compaction_metrics(
    messages: List[Dict[str, Any]],
    compacted: List[Dict[str, Any]],
    importance_scorer: Optional[Callable[[Dict[str, Any]], float]] = None,
) -> Dict[str, Any]:
    """压缩质量评估: 压缩率 + 逐字保留率 + 摘要恢复率估计。

    - retention_verbatim: 原样保留的消息占全部重要性的比例 (0~1)。
    - recovery_estimate: 加上「摘要按相对长度折算的信号恢复」后的估计保留率 (0~1)。
      摘要越详细 (相对省略量越大), 估计恢复越接近 1。
    """
    scorer = importance_scorer or message_importance
    before = estimate_messages(messages)
    after = estimate_messages(compacted)
    total_sig = sum(scorer(m) for m in messages if m.get("role") != "system")
    if total_sig <= 0:
        return {
            "tokens_before": before, "tokens_after": after,
            "compression_ratio": 0.0, "retention_verbatim": 1.0,
            "recovery_estimate": 1.0,
        }
    kept_sig = 0.0
    summary_text = ""
    for m in compacted:
        if m.get("role") == "system":
            continue
        content = _msg_text(m)
        if content.startswith("[早期上下文摘要]"):
            summary_text = content
            continue
        if content.startswith("[compaction]"):
            continue
        kept_sig += scorer(m)
    elided_sig = max(0.0, total_sig - kept_sig)
    summary_tokens = estimate_tokens(summary_text)
    elided_tokens = max(1, before - max(after - summary_tokens, 0))
    summary_ratio = min(1.0, summary_tokens / elided_tokens)
    return {
        "tokens_before": before,
        "tokens_after": after,
        "compression_ratio": round(1.0 - after / before, 4) if before else 0.0,
        "retention_verbatim": round(kept_sig / total_sig, 4),
        "recovery_estimate": round((kept_sig + elided_sig * summary_ratio) / total_sig, 4),
    }


__all__ = [
    "compact_head_tail_elision",
    "build_compaction_elision_text",
    "build_compaction_summary_text",
    "estimate_tokens",
    "estimate_message",
    "estimate_messages",
    "message_importance",
    "compaction_metrics",
]
