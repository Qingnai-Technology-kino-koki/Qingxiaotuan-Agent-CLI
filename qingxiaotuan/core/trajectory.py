"""Trajectory —— 把会话事件流重建为结构化、可导出、可压缩的轨迹。

吸收 DeepSeek Harness 的理念: Trajectory 是一等公民对象, 既能给人看 (summary/markdown),
也能给程序消费 (to_dict/to_json), 还能被压缩后塞回上下文 (compact 的输入)。

输入: SessionStore 的 JSONL 事件流 (规范 + 既有别名皆可, 经 event_protocol.normalize_record)。
输出: 一个 Step 列表 + 指标。Step 是「一轮原子行为」: 一条用户消息 / 一段助手回复 /
一次工具调用(含结果) / 一次验证 / 一次目标判定。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .event_protocol import AgentEvent, normalize_record


@dataclass
class Step:
    """轨迹中的一个原子步骤。"""
    seq: int
    kind: str               # message | tool | verify | goal | system | session
    role: str = ""          # user | assistant | tool | system
    content: str = ""       # 文本 (message/system) 或工具名/结果摘要
    tool: str = ""          # 工具名 (kind==tool)
    args: str = ""          # 工具入参摘要
    result: str = ""        # 工具结果摘要
    status: str = ""        # 状态 (tool: completed/failed; task: running/completed/failed)
    started: float = 0.0
    ended: float = 0.0
    duration_ms: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        if self.kind == "tool":
            return f"{self.tool}"
        if self.kind == "message":
            return f"{self.role}"
        return self.kind


class Trajectory:
    """从事件流重建的轨迹。"""

    def __init__(self, session_id: str = "", steps: Optional[List[Step]] = None,
                 meta: Optional[Dict[str, Any]] = None,
                 raw_events: Optional[List[Dict[str, Any]]] = None) -> None:
        self.session_id = session_id
        self.steps: List[Step] = steps or []
        self.meta: Dict[str, Any] = meta or {}
        self.raw_events: List[Dict[str, Any]] = raw_events or []
        self._total_tokens = 0
        self._total_cost = 0.0

    # ---------------------------------------------------------- 构建

    @classmethod
    def from_records(cls, records: List[Dict[str, Any]],
                     session_id: str = "") -> "Trajectory":
        """从原始事件记录列表重建 Trajectory。"""
        recs = [normalize_record(r) for r in records]
        steps: List[Step] = []
        seq = 0
        chunk_buf: List[str] = []
        chunk_start = 0.0
        pending_tool: Optional[Step] = None
        meta: Dict[str, Any] = {}
        total_tokens = 0
        total_cost = 0.0

        def flush_chunk() -> None:
            nonlocal chunk_buf, chunk_start, seq
            if not chunk_buf:
                return
            text = "".join(chunk_buf)
            seq += 1
            steps.append(Step(
                seq=seq, kind="message", role="assistant",
                content=text, started=chunk_start,
                ended=chunk_start + 0.001,
            ))
            chunk_buf = []
            chunk_start = 0.0

        for r in recs:
            et = r.get("type", "")
            ts = float(r.get("ts", time.time()))
            if et == AgentEvent.SESSION_UPDATE and r.get("status") == "initialized":
                meta.setdefault("initialized_at", ts)
            elif et == "session.meta":
                meta.setdefault("task", r.get("task", ""))
            elif et == AgentEvent.USER:
                flush_chunk()
                msg = r.get("message", {})
                content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
                if isinstance(content, list):  # 多模态 content 块
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict))
                seq += 1
                steps.append(Step(seq=seq, kind="message", role="user",
                                 content=str(content)[:2000], started=ts, ended=ts))
            elif et == AgentEvent.AGENT_MESSAGE_CHUNK:
                if not chunk_buf:
                    chunk_start = ts
                chunk_buf.append(str(r.get("text", "")))
            elif et == AgentEvent.ASSISTANT:
                # 若已经由 chunk 累积出完整回复, 跳过这条重复的最终事件
                if chunk_buf:
                    flush_chunk()
                    continue
                msg = r.get("message", {})
                content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
                seq += 1
                steps.append(Step(seq=seq, kind="message", role="assistant",
                                 content=str(content)[:4000], started=ts, ended=ts))
            elif et == AgentEvent.TOOL_CALL:
                flush_chunk()
                seq += 1
                pending_tool = Step(
                    seq=seq, kind="tool", role="tool",
                    tool=str(r.get("name", "")),
                    args=str(r.get("arguments", ""))[:800],
                    started=ts,
                    raw={"toolCallId": r.get("toolCallId", "")},
                )
                steps.append(pending_tool)
            elif et in (AgentEvent.TOOL_CALL_UPDATE, AgentEvent.TOOL_RESULT):
                status = str(r.get("status", "completed"))
                result = str(r.get("result", ""))[:1500]
                rid = r.get("toolCallId", "")
                pid = pending_tool.raw.get("toolCallId", "") if pending_tool is not None else ""
                # 顺序流中, tool_call_update 优先挂到最近一次 tool_call;
                # 若双方都带 toolCallId 则以 id 对齐 (支持并行/ACP)。
                if pending_tool is not None and (rid == "" or pid == "" or rid == pid):
                    t = pending_tool
                else:
                    # 找不到对应 tool_call, 新建一个结果步骤 (避免丢失)
                    seq += 1
                    t = Step(seq=seq, kind="tool", role="tool",
                             tool=str(r.get("name", "")), started=ts, ended=ts,
                             raw={"toolCallId": rid})
                    steps.append(t)
                t.status = status
                t.result = result
                t.ended = ts
                if t.started:
                    t.duration_ms = max(0, int((ts - t.started) * 1000))
                pending_tool = None
            elif et in (AgentEvent.VERIFY_PASSED, AgentEvent.VERIFY_FAILED):
                flush_chunk()
                seq += 1
                steps.append(Step(seq=seq, kind="verify", role="system",
                                 content=f"{et.split('.')[-1]}", started=ts, ended=ts))
            elif et in (AgentEvent.GOAL_CONTINUE, AgentEvent.GOAL_ACHIEVED):
                flush_chunk()
                seq += 1
                steps.append(Step(seq=seq, kind="goal", role="system",
                                 content=str(r.get("goal", ""))[:200], status=et.split('.')[-1],
                                 started=ts, ended=ts))
            elif et == AgentEvent.BUDGET_EXCEEDED:
                flush_chunk()
                seq += 1
                steps.append(Step(seq=seq, kind="system", role="system",
                                 content=f"budget exceeded: {r.get('cost')} / {r.get('budget')}",
                                 started=ts, ended=ts))
            elif et == AgentEvent.USAGE:
                delta = r.get("delta") or {}
                for v in delta.values():
                    if isinstance(v, (int, float)):
                        total_tokens += int(v)
                c = r.get("cost_usd")
                if isinstance(c, (int, float)):
                    total_cost += float(c)
            # 其余事件 (session.update done / task.update 等) 忽略为 step, 但保留在 raw

        flush_chunk()
        traj = cls(session_id=session_id, steps=steps, meta=meta, raw_events=recs)
        traj._total_tokens = total_tokens
        traj._total_cost = total_cost
        return traj

    @classmethod
    def from_session_store(cls, store, session_id: str = "") -> "Trajectory":
        """从 SessionStore 实例重建。"""
        sid = session_id or getattr(store, "session_id", "")
        return cls.from_records(store.read_all(), session_id=sid)

    # ---------------------------------------------------------- 指标

    def metrics(self) -> Dict[str, Any]:
        tools = [s for s in self.steps if s.kind == "tool"]
        ok = [s for s in tools if s.status == "completed"]
        fail = [s for s in tools if s.status == "failed"]
        user_turns = [s for s in self.steps if s.role == "user"]
        assistant = [s for s in self.steps if s.kind == "message" and s.role == "assistant"]
        tool_ms = sum(s.duration_ms for s in tools)
        return {
            "session_id": self.session_id,
            "steps": len(self.steps),
            "user_turns": len(user_turns),
            "assistant_messages": len(assistant),
            "tool_calls": len(tools),
            "tool_ok": len(ok),
            "tool_failed": len(fail),
            "tool_success_rate": round(len(ok) / len(tools), 3) if tools else None,
            "tool_time_ms": tool_ms,
            "verify": len([s for s in self.steps if s.kind == "verify"]),
            "goal": len([s for s in self.steps if s.kind == "goal"]),
            "total_tokens": getattr(self, "_total_tokens", 0),
            "total_cost_usd": round(getattr(self, "_total_cost", 0.0), 6),
        }

    # ---------------------------------------------------------- 序列化

    def summary(self, max_chars: int = 2000) -> str:
        """给人看的紧凑摘要 (用户/助手交替 + 工具列表)。"""
        lines: List[str] = []
        for s in self.steps:
            if s.kind == "message":
                who = "用户" if s.role == "user" else "青小团"
                lines.append(f"{who}: {s.content[:280]}")
            elif s.kind == "tool":
                mark = "✓" if s.status == "completed" else ("⊘" if s.status == "failed" else "…")
                lines.append(f"  工具 {mark} {s.tool} ({s.duration_ms}ms)")
            elif s.kind == "verify":
                lines.append(f"  验证: {s.content}")
            elif s.kind == "goal":
                lines.append(f"  目标[{s.status}]: {s.content[:80]}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[摘要截断]"
        return text

    def to_markdown(self) -> str:
        m = self.metrics()
        out = [f"# Trajectory · `{self.session_id}`", ""]
        if self.meta.get("task"):
            out.append(f"> 任务: {self.meta['task']}")
        out.append(f"> 步数: {m['steps']} · 工具: {m['tool_calls']} "
                   f"(成功 {m['tool_ok']}/失败 {m['tool_failed']}) · "
                   f"Token: {m['total_tokens']} · 花费: ${m['total_cost_usd']}")
        out.append("")
        for s in self.steps:
            if s.kind == "message":
                who = "**用户**" if s.role == "user" else "**青小团**"
                out.append(f"{who}: {s.content[:600]}")
            elif s.kind == "tool":
                mark = "✓" if s.status == "completed" else ("⊘" if s.status == "failed" else "…")
                out.append(f"- 工具 {mark} `{s.tool}` · {s.duration_ms}ms")
                if s.args:
                    out.append(f"  - 入参: `{s.args[:200]}`")
                if s.result:
                    out.append(f"  - 结果: {s.result[:200]}")
            elif s.kind in ("verify", "goal", "system"):
                out.append(f"- {s.kind}: {s.content[:200]}")
        return "\n".join(out)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "meta": self.meta,
            "metrics": self.metrics(),
            "steps": [
                {
                    "seq": s.seq, "kind": s.kind, "role": s.role,
                    "content": s.content, "tool": s.tool, "args": s.args,
                    "result": s.result, "status": s.status,
                    "started": s.started, "ended": s.ended,
                    "duration_ms": s.duration_ms,
                }
                for s in self.steps
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
