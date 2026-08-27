"""多 Agent 协作编排 (herdr 式: 强模型规划 + 弱模型并发执行 + 强模型验收)。

概念来源: Bilibili BV1yPuq6qEHE《AI超强终端herdr,让Agent互相通信》。
核心思想:
- **强模型负责规划与验收** (planner/acceptor): 拆解复杂任务、复核弱模型产物;
- **弱模型负责并发执行** (worker): 干脏活累活 (调研/改写/跑脚本), 便宜且不怕打满额度;
- **共享黑板 (Blackboard)**: 规划结果、各 worker 的中间产物、互相引用都写在黑板上,
  子 Agent 之间不必互相 ping, 而是"读黑板、写黑板", 形成弱耦合协作;
- 不依赖单一模型: planner 用贵的, worker 用免费档, 整体成本大幅下降, 且任一模型
  挂了不影响其它角色。

与 SubAgentPool 的关系:
- SubAgentPool 负责"把独立子任务并发派出 + 隔离 + 汇总"这一通用能力;
- 本模块的 Collaboration 在之上加了一层 **角色分工 + 黑板通信**, 复用 SubAgentPool
  的并发/沙箱/汇总机制, 但任务来自 planner 而非用户手敲的 || 列表。

弱模型 worker 通过 model_overrides 注入 (进程沙箱里切换成 worker 的 provider/model),
强模型 planner/acceptor 直接在主 Agent 进程内用主模型跑 (或按 model.planner 切换)。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .subagents import SubAgentPool, SubResult, SubTask


class Blackboard:
    """线程安全的共享黑板 —— 多 Agent 协作的中枢。

    结构: 一个扁平的 key->value 字典 + 一条有序的"事件流"(谁在何时写了什么)。
    worker 把中间产物写进黑板 (如 research:T1 = "…"), 其它 worker 或 acceptor
    读取后可以继续加工, 形成"规划 → 执行 → 引用 → 验收"的协作链。
    """

    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._log: List[Dict[str, str]] = []
        self._lock = threading.Lock()

    def write(self, key: str, value: str, by: str = "worker") -> None:
        with self._lock:
            self._store[key] = value
            self._log.append({"by": by, "key": key, "snippet": (value or "")[:160]})

    def read(self, key: str) -> Optional[str]:
        with self._lock:
            return self._store.get(key)

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._store)

    def log_text(self) -> str:
        with self._lock:
            if not self._log:
                return "(黑板为空)"
            lines = [f"- [{e['by']}] {e['key']}: {e['snippet']}" for e in self._log]
            return "\n".join(lines)

    def context_block(self) -> str:
        """给 acceptor / 后续 worker 用的可读黑板快照。"""
        with self._lock:
            if not self._store:
                return "(黑板暂无内容)"
            parts = [f"## 黑板 {k}\n{v}" for k, v in self._store.items()]
            return "\n\n".join(parts)

    def to_json(self) -> str:
        with self._lock:
            return json.dumps({"store": self._store, "log": self._log}, ensure_ascii=False)


@dataclass
class SwarmPlan:
    """planner 产出的拆解计划。"""

    goal: str
    tasks: List[Dict[str, str]] = field(default_factory=list)  # [{id, title, prompt, depends_on}]
    notes: str = ""


@dataclass
class Collaboration:
    """一次完整的多 Agent 协作结果。"""

    goal: str
    plan: SwarmPlan
    worker_results: List[SubResult]
    accepted: str = ""          # acceptor 验收后的最终报告
    blackboard_log: str = ""
    elapsed: float = 0.0

    def to_report(self) -> str:
        head = [
            f"# 多 Agent 协作报告 · {self.goal[:40]}",
            "",
            f"- 规划子任务: {len(self.plan.tasks)} 项",
            f"- 执行结果: 成功 {sum(1 for r in self.worker_results if r.ok)} / {len(self.worker_results)}",
            f"- 用时: {self.elapsed:.1f}s",
            "",
            "## 黑板通信记录",
            self.blackboard_log or "(无)",
            "",
            "## 验收结论 (强模型)",
            self.accepted or "(未生成)",
        ]
        return "\n".join(head).strip()


def _planner_prompt(goal: str, n_hint: int = 4) -> str:
    return (
        f"你是一个任务规划师。请把下面的复杂目标拆解成 {max(2, n_hint)} 个左右"
        "互相独立、可执行、可由便宜的弱模型并发完成的子任务。\n\n"
        "要求:\n"
        "1. 每个子任务自包含, 给出明确的 prompt (弱模型只看 prompt 就够了);\n"
        "2. 如果某个子任务依赖另一个的结果, 用 depends_on 指明其 id (强模型会把该结果先写进黑板);\n"
        "3. 只输出 JSON, 不要解释。格式:\n"
        '{"goal": "...", "notes": "...", '
        '"tasks": [{"id":"T1","title":"...","prompt":"...","depends_on":""}]}\n\n'
        f"目标: {goal}"
    )


def _acceptor_prompt(goal: str, plan_summary: str, results_text: str, board: str) -> str:
    return (
        "你是一个验收师 (强模型)。下面是一组弱模型并发执行的子任务结果, 以及它们写到共享黑板上的中间产物。\n"
        "请据此产出**最终交付物**: 综合、去重、纠正明显错误、给出可执行结论。\n"
        "若某子任务失败, 说明影响并给出兜底建议, 不要假装它成功了。\n\n"
        f"## 原始目标\n{goal}\n\n"
        f"## 规划\n{plan_summary}\n\n"
        f"## 共享黑板\n{board}\n\n"
        f"## 各子任务结果\n{results_text}\n"
    )


class Swarm:
    """herdr 式多 Agent 协作编排器。

    用法 (主 Agent 进程内):
        swarm = Swarm(kernel, config, workspace, agent)
        collab = swarm.run("给青小团写一份开源公告 + 配套 FAQ + 发推文案")
        report = collab.to_report()
    """

    def __init__(
        self,
        kernel,
        config,
        workspace: str,
        main_agent=None,
        confirm=None,
        exclude_tools: Sequence[str] = (),
        max_workers: Optional[int] = None,
        planner_fn: Optional[Callable[[str], SwarmPlan]] = None,
        acceptor_fn: Optional[Callable[[str, SwarmPlan, str, Blackboard], str]] = None,
        worker_overrides: Optional[Dict[str, Any]] = None,
        isolation: str = "process",
        pool: Optional[SubAgentPool] = None,
        n_hint: int = 4,
    ) -> None:
        self.kernel = kernel
        self.config = config
        self.workspace = workspace
        self.main_agent = main_agent
        self.confirm = confirm
        self.exclude_tools = tuple(exclude_tools)
        # 允许测试/特殊场景注入一个自定义 SubAgentPool (如离线假池), 否则按配置构造
        self._pool_override = pool
        # 角色模型 (从 config 读, provider 留空 = 复用主 model)
        self.planner = dict(config.get("model.planner") or {})
        self.worker_cfg = dict(config.get("model.worker") or {})
        self.planner_fn = planner_fn          # 注入用 (测试/可定制)
        self.acceptor_fn = acceptor_fn
        self.worker_overrides = worker_overrides or self._default_worker_overrides()
        self.isolation = isolation
        self.n_hint = max(2, min(n_hint, 8))
        # 并发度: 用户显式给了 max_workers 就用它; 否则用 n_hint 推导
        # (让 /swarm 目标 || 3 真的只并发 3 个弱模型, 而不是独立配置的默认 4)。
        # 仍夹在 [1, 16] 防越界。
        if max_workers is None:
            cfg_max = int(config.get("agent.subagent_max_workers", 5))
            max_workers = min(self.n_hint, cfg_max)
        self.max_workers = max(1, min(max_workers, 16))

    # ---------------------------------------------------------- 模型解析

    def _default_worker_overrides(self) -> Dict[str, Any]:
        """弱模型覆盖层: 从 model.worker 取 provider/model/base_url/api_key_env;
        缺省字段从主 model 继承 (保证只配 provider 也能跑)。"""
        main = self.config.get("model") or {}
        w = dict(self.worker_cfg)
        if not w.get("provider"):
            return {}
        ov = {"provider": w["provider"]}
        for k in ("model", "base_url", "api_key_env", "temperature"):
            if w.get(k):
                ov[k] = w[k]
            elif main.get(k) is not None:
                ov[k] = main[k]
        return ov

    def planner_overrides(self) -> Dict[str, Any]:
        """强模型覆盖层: provider 留空 = 不切换 (用主模型)。"""
        p = dict(self.planner)
        provider = (p.get("provider") or "").strip()
        if not provider:
            return {}
        ov = {"provider": provider}
        main = self.config.get("model") or {}
        for k in ("model", "base_url", "api_key_env", "temperature"):
            if p.get(k):
                ov[k] = p[k]
            elif main.get(k) is not None:
                ov[k] = main[k]
        return ov

    # ---------------------------------------------------------- 规划

    def _plan(self, goal: str) -> SwarmPlan:
        if self.planner_fn is not None:
            return self.planner_fn(goal)
        # 默认: 复用主 Agent 的 run (强模型在进程内规划, 解析其 JSON 输出)
        agent = self.main_agent
        if agent is None:
            # 无主 Agent (如测试) 退回一个朴素计划
            return SwarmPlan(goal=goal, tasks=[{"id": "T1", "title": goal, "prompt": goal, "depends_on": ""}])
        # 强模型切换统一由 _run 的生命周期管理 (_strong_switch_on/off)
        raw = agent.run(_planner_prompt(goal, self.n_hint), stream=False) or ""
        return self._parse_plan(goal, raw)

    @staticmethod
    def _parse_plan(goal: str, raw: str) -> SwarmPlan:
        # 容忍模型在 JSON 外裹了 ```json  fences 或多余文字
        text = raw.strip()
        if "```" in text:
            m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if m:
                text = m.group(1).strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # 退路: 把整段当单个任务
            return SwarmPlan(goal=goal, tasks=[{"id": "T1", "title": goal, "prompt": raw[:2000], "depends_on": ""}])
        tasks = []
        for i, t in enumerate(obj.get("tasks", []) or []):
            tasks.append({
                "id": str(t.get("id") or f"T{i+1}"),
                "title": str(t.get("title") or f"子任务{i+1}"),
                "prompt": str(t.get("prompt") or t.get("title") or ""),
                "depends_on": str(t.get("depends_on") or ""),
            })
        if not tasks:
            tasks = [{"id": "T1", "title": goal, "prompt": goal, "depends_on": ""}]
        return SwarmPlan(goal=goal, tasks=tasks, notes=str(obj.get("notes") or ""))

    # ---------------------------------------------------------- 执行

    @staticmethod
    def _split_dep_ids(raw: Optional[str]) -> List[str]:
        """拆依赖声明: "T1" / "T1,T2" / "T1; T2" 均可。"""
        return [d for d in re.split(r"[,，;；\s]+", str(raw or "").strip()) if d]

    @staticmethod
    def _build_waves(plan: SwarmPlan) -> List[List[Dict[str, str]]]:
        """按 depends_on 把任务拓扑分波 (Kahn 式贪心), 波内任务互相独立可并发。

        - 未知依赖 id 视为已满足 (planner 幻觉不应导致死锁);
        - 自引用依赖忽略;
        - 剩余任务若有循环依赖, 兜底整体放进最后一波 (宁可并发序不理想, 不丢任务)。
        """
        pos_of: Dict[str, List[int]] = {}
        for i, t in enumerate(plan.tasks):
            tid = str(t.get("id") or f"T{i+1}")
            t["id"] = tid
            pos_of.setdefault(tid, []).append(i)
        deps: List[set] = []
        for i, t in enumerate(plan.tasks):
            want: set = set()
            for d in Swarm._split_dep_ids(t.get("depends_on")):
                if d != str(t["id"]):
                    want.update(pos_of.get(d, []))
            deps.append(want)
        waves: List[List[Dict[str, str]]] = []
        placed: set = set()
        todo = list(range(len(plan.tasks)))
        while todo:
            wave = [i for i in todo if deps[i] <= placed]
            if not wave:
                wave = list(todo)   # 循环依赖兜底
                todo = []
            else:
                done = set(wave)
                todo = [i for i in todo if i not in done]
            placed.update(wave)
            waves.append([plan.tasks[i] for i in wave])
        return waves

    def _build_worker_prompts(self, plan: SwarmPlan, board: Blackboard) -> List[SubTask]:
        """把规划转成带"黑板上下文"的子任务 prompt。

        若某任务 depends_on 其它任务, 把被依赖方在黑板上的结果注入其 prompt,
        实现"弱模型读强模型/同伴写的中间产物"的协作 (支持逗号/分号分隔的多依赖)。
        """
        return self._prompts_for(plan.tasks, board)

    @staticmethod
    def _prompts_for(tasks: List[Dict[str, str]], board: Blackboard) -> List[SubTask]:
        out: List[SubTask] = []
        for t in tasks:
            prompt = t["prompt"]
            blocks = []
            for d in Swarm._split_dep_ids(t.get("depends_on")):
                dep_val = board.read(d)
                if dep_val:
                    blocks.append(f"[子任务 {d} 的结果]\n{dep_val[:1500]}")
            if blocks:
                prompt = "[上下文] 来自共享黑板:\n" + "\n\n".join(blocks) + f"\n\n[你的任务] {prompt}"
            out.append(SubTask(task_id=t["id"], prompt=prompt, meta={"title": t["title"]}))
        return out

    @staticmethod
    def _order_results(results: List[SubResult], plan: SwarmPlan) -> List[SubResult]:
        """分波执行不改变报告顺序: 按 plan.tasks 原顺序重排结果。"""
        order = {str(t["id"]): i for i, t in enumerate(plan.tasks)}
        return sorted(results, key=lambda r: order.get(str(r.task_id), len(order)))

    # ---------------------------------------------------------- 强模型生命周期

    def _sync_main_agent_model(self) -> None:
        """把内核当前适配器同步回主 Agent 的缓存引用。

        Agent.__init__ 缓存了 self.model, switch_model 重建适配器后若不同步,
        主 Agent 仍会用旧模型跑规划/验收。"""
        agent = self.main_agent
        if agent is not None and hasattr(agent, "model"):
            try:
                agent.model = self.kernel.require("model_adapter")
            except Exception:  # noqa: BLE001
                pass

    def _strong_switch_on(self, phase: str):
        """规划/验收前临时切到强模型, 返回还原上下文 (无需切换时返回 None)。

        phase: "plan" | "accept" —— 对应阶段注入了自定义函数时跳过切换。
        """
        fn = self.acceptor_fn if phase == "accept" else self.planner_fn
        if fn is not None or self.main_agent is None or not self.planner_overrides():
            return None
        from ..models.plugin import ModelPlugin
        ov = self.planner_overrides()
        before = {f"model.{k}": self.config.get(f"model.{k}") for k in ov}
        prev_adapter = self.kernel.get("model_adapter")
        ModelPlugin.switch_model(self.kernel, ov, persist=False)
        self._sync_main_agent_model()
        return (before, prev_adapter)

    def _strong_switch_off(self, ctx) -> None:
        """完整还原强模型切换的副作用: 配置视图、内核适配器、主 Agent 缓存。

        切换本身 persist=False 不落盘, 因此还原内存视图即无残留。"""
        if ctx is None:
            return
        before, prev_adapter = ctx
        try:
            from ..config.loader import patch_replace
            self.config.data = patch_replace(self.config.data, before)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.kernel.unprovide("model_adapter")
            if prev_adapter is not None:
                self.kernel.provide("model_adapter", prev_adapter, owner="model")
        except Exception:  # noqa: BLE001
            pass
        self._sync_main_agent_model()

    # ---------------------------------------------------------- 执行入口

    def _run(self, goal: str) -> Collaboration:
        import time
        started = time.time()
        board = Blackboard()

        # 阶段一: 强模型规划 (临时切换强模型, 结束后完整还原, 不污染用户配置)
        strong_ctx = self._strong_switch_on("plan")
        try:
            plan = self._plan(goal)
        finally:
            self._strong_switch_off(strong_ctx)
        # 把规划本身也写进黑板, 让 worker 知道全貌 (弱耦合通信)
        board.write("__plan__", json.dumps(plan.tasks, ensure_ascii=False), by="planner")

        # 阶段二: 弱模型按依赖分波并发执行 (进程沙箱隔离, 注入 worker 模型覆盖层)
        if self._pool_override is not None:
            # 注入池: 确保它也带上弱模型覆盖层 (dispatch 会把其写入各子任务 meta)
            self._pool_override.model_overrides = self.worker_overrides
        pool = self._pool_override or SubAgentPool(
            kernel=self.kernel, config=self.config, workspace=self.workspace,
            main_agent=self.main_agent, confirm=self.confirm,
            exclude_tools=self.exclude_tools,
            max_workers=self.max_workers,
            default_timeout=float(self.config.get("agent.subagent_timeout", 180)),
            isolation=self.isolation,
            model_overrides=self.worker_overrides,
        )
        all_results: List[SubResult] = []
        for wave in self._build_waves(plan):
            # 每波用当前黑板构建 prompt —— 前波结果已在黑板上, 后波任务能读到
            results = pool.dispatch(self._prompts_for(wave, board), stream=False, on_error=None)
            # 把每个 worker 的产物写回黑板 (后续波的 worker 或 acceptor 可引用)
            for r in results:
                board.write(r.task_id, r.output or f"(失败: {r.error})", by="worker")
            all_results.extend(results)
        results = self._order_results(all_results, plan)

        # 阶段三: 强模型验收 (同样走临时切换生命周期)
        results_text = SubAgentPool.aggregate(results, title="子任务产物")
        accept_ctx = self._strong_switch_on("accept")
        try:
            accepted = self._accept(board, goal, plan, results_text)
        finally:
            self._strong_switch_off(accept_ctx)
        return Collaboration(
            goal=goal, plan=plan, worker_results=results,
            accepted=accepted, blackboard_log=board.log_text(), elapsed=time.time() - started,
        )

    def _accept(self, board: Blackboard, goal: str, plan: SwarmPlan, results_text: str) -> str:
        if self.acceptor_fn is not None:
            return self.acceptor_fn(goal, plan, results_text, board)
        agent = self.main_agent
        if agent is None:
            return results_text  # 无主 Agent 时直接透传汇总
        # 强模型切换统一由 _run 的生命周期管理 (_strong_switch_on/off)
        return agent.run(
            _acceptor_prompt(goal, json.dumps(plan.tasks, ensure_ascii=False), results_text, board.context_block()),
            stream=False,
        ) or results_text

    # ---------------------------------------------------------- 对外入口

    def run(self, goal: str, stream: bool = False) -> Collaboration:
        """跑完整协作流程。stream 预留 (当前 acceptor 在进程内, 不向外流式)。"""
        return self._run(goal)
