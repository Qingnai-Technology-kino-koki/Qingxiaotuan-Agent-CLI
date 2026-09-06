"""可观测层 — Trajectory 视图 + 每步"模型看到了什么" + 跨会话归因。

三块能力:
1. TrajectoryStore —— 把一次会话/编排记录成"步骤图" (每步含 决策/工具/观察/反思),
                        支持按会话/代理查询与可视化渲染 (Trajectory 视图)。
2. StepViewer      —— 逐步捕获"模型当时看到了什么 (输入 messages) 与返回了什么 (原始响应)",
                        敏感原文经 CryptoVault 加密落盘, 按需解密查看。
3. CrossSessionAttributor —— 把最终产出向上回溯到贡献它的会话/子代理/工具, 构建
                        跨会话溯源图 (provenance), 给出每个来源的贡献权重。
"""

from __future__ import annotations

import time
import uuid
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .security import CryptoVault


@dataclass
class TrajectoryStep:
    """轨迹中的一个步骤节点。"""

    step_id: str
    session_id: str
    agent_id: str
    parent_id: Optional[str]
    seq: int
    kind: str                       # decide / emit / observe / reflect / terminate
    summary: str = ""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    observation: str = ""
    ts: float = field(default_factory=time.time)
    # 加密存储的模型 I/O (见 StepViewer)
    _input_cipher: Optional[Dict[str, str]] = field(default=None, repr=False)
    _output_cipher: Optional[Dict[str, str]] = field(default=None, repr=False)


class TrajectoryStore:
    """会话/编排的轨迹图存储。"""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id or f"sess-{uuid.uuid4().hex[:8]}"
        self._steps: Dict[str, TrajectoryStep] = {}
        self._seq = 0

    def add_step(self, agent_id: str, kind: str, summary: str = "",
                 parent_id: Optional[str] = None, tool: str = "", args: Optional[Dict[str, Any]] = None,
                 observation: str = "") -> TrajectoryStep:
        self._seq += 1
        sid = uuid.uuid4().hex[:10]
        step = TrajectoryStep(
            step_id=sid, session_id=self.session_id, agent_id=agent_id,
            parent_id=parent_id, seq=self._seq, kind=kind, summary=summary,
            tool=tool, args=args or {}, observation=observation,
        )
        self._steps[sid] = step
        return step

    def get(self, step_id: str) -> Optional[TrajectoryStep]:
        return self._steps.get(step_id)

    def steps(self, agent_id: Optional[str] = None) -> List[TrajectoryStep]:
        out = [s for s in self._steps.values() if agent_id is None or s.agent_id == agent_id]
        return sorted(out, key=lambda s: s.seq)

    def render_tree(self) -> str:
        """渲染 Trajectory 视图 (文本树)。"""
        by_parent: Dict[Optional[str], List[TrajectoryStep]] = defaultdict(list)
        for s in self._steps.values():
            by_parent[s.parent_id].append(s)
        lines: List[str] = [f"Trajectory[{self.session_id}] steps={len(self._steps)}"]
        roots = sorted(by_parent.get(None, []), key=lambda s: s.seq)

        def _walk(step: TrajectoryStep, depth: int) -> None:
            prefix = "  " * depth + ("└─ " if depth else "")
            label = f"{step.kind}"
            if step.tool:
                label += f" {step.tool}({_short(step.args)})"
            if step.observation:
                label += f" -> {step.observation[:60]!r}"
            lines.append(f"{prefix}{label}")
            for child in sorted(by_parent.get(step.step_id, []), key=lambda s: s.seq):
                _walk(child, depth + 1)

        for r in roots:
            _walk(r, 0)
        return "\n".join(lines)


def _short(d: Dict[str, Any]) -> str:
    return ", ".join(f"{k}={str(v)[:20]}" for k, v in list(d.items())[:3])


# ============================================================ 每步"模型看到了什么"
class StepViewer:
    """逐步捕获并(加密)保存模型输入/输出, 按需查看。"""

    def __init__(self, vault: Optional[CryptoVault] = None) -> None:
        self.vault = vault

    def record(self, step: TrajectoryStep, model_input: Any, model_output: Any) -> None:
        """把"模型看到的"与"模型返回的"存进步骤 (加密或明文)。"""
        in_s = _serialize(model_input)
        out_s = _serialize(model_output)
        if self.vault is not None:
            step._input_cipher = self.vault.seal_text(in_s)
            step._output_cipher = self.vault.seal_text(out_s)
        else:
            step.summary = f"in={in_s[:80]!r} out={out_s[:80]!r}"

    def view_input(self, step: TrajectoryStep) -> str:
        if step._input_cipher and self.vault is not None:
            return self.vault.open_text(step._input_cipher)
        if step.summary:
            return step.summary
        return ""

    def view_output(self, step: TrajectoryStep) -> str:
        if step._output_cipher and self.vault is not None:
            return self.vault.open_text(step._output_cipher)
        return ""


def _serialize(obj: Any) -> str:
    try:
        return obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


# ============================================================ 跨会话归因
@dataclass
class Attribution:
    session_id: str
    agent_id: str
    weight: float
    steps: int


class CrossSessionAttributor:
    """把最终产出回溯到贡献来源, 构建跨会话溯源图。"""

    def __init__(self) -> None:
        self._graph: Dict[str, List[TrajectoryStep]] = defaultdict(list)

    def ingest(self, store: TrajectoryStore) -> None:
        for s in store.steps():
            self._graph[s.session_id].append(s)

    def attribute(self, target_step_id: str,
                  store: TrajectoryStore) -> List[Attribution]:
        """从目标步骤向上回溯父链, 聚合每个来源会话/代理的贡献权重。

        权重 = 该来源在回溯链中出现的次数 (越靠近目标、越频繁, 贡献越大)。
        """
        step = store.get(target_step_id)
        if step is None:
            return []
        # 向上回溯
        chain: List[TrajectoryStep] = []
        cur: Optional[TrajectoryStep] = step
        seen = set()
        while cur is not None and cur.step_id not in seen:
            seen.add(cur.step_id)
            chain.append(cur)
            cur = store.get(cur.parent_id) if cur.parent_id else None

        # 按 (session, agent) 聚合贡献次数
        contrib: Dict[tuple, int] = defaultdict(int)
        total = 0
        for i, s in enumerate(chain):
            # 越靠近目标权重越高 (depth penalty)
            w = 1.0 / (1 + i)
            contrib[(s.session_id, s.agent_id)] += w
            total += w
        out: List[Attribution] = []
        for (sid, aid), w in contrib.items():
            out.append(Attribution(
                session_id=sid, agent_id=aid, weight=round(w / total, 3),
                steps=sum(1 for s in chain if s.session_id == sid and s.agent_id == aid),
            ))
        out.sort(key=lambda a: a.weight, reverse=True)
        return out

    def render_attribution(self, target_step_id: str, store: TrajectoryStore) -> str:
        attrs = self.attribute(target_step_id, store)
        lines = [f"Attribution[{target_step_id}]"]
        for a in attrs:
            lines.append(f"  {a.session_id} / {a.agent_id}: weight={a.weight} steps={a.steps}")
        return "\n".join(lines)
