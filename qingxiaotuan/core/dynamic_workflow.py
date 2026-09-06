"""Dynamic Workflows —— 并行后台代理编排 (对标 Claude Code 的 Dynamic Workflows)。

能力 (对标 Claude Code 2.1.246 的 Task 动态编排层):
- **并行后台**: 一次派出一批独立子代理 (每步内并行, 步骤间顺序), 不阻塞主循环;
- **动态加步骤**: 工作流运行中可随时追加步骤, 编排器取到新步骤自动继续;
- **持久化状态**: 工作流/每一步/每个任务的状态都落盘 JSON, 进程重启后仍可查询;
- **结果聚合**: 每步完成后把子结果聚合成带来源标注的报告, 供主 Agent / 调用方消费;
- **失败隔离 + 重试**: 单任务失败不连累同批其它任务; 可重跑失败任务。

状态模型 (复用 task_dag.TaskStatus 语义, 用字符串避免跨模块循环依赖):
    workflow: pending | running | done | failed | cancelled
    task    : pending | running | done | failed | retryable

编排器是一个进程内守护线程; 每步通过 SubAgentPool.dispatch 并发执行该步的所有任务。
测试可注入 ``pool_factory`` 替换真实子代理池, 从而无需真实模型即可验证编排逻辑。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


@dataclass
class WorkflowTask:
    title: str             # 任务短标题 (展示用)
    prompt: str            # 自包含任务说明
    role: str = "general-purpose"
    task_id: str = ""      # 留空则引擎自动生成


@dataclass
class WorkflowStep:
    tasks: List[WorkflowTask]
    step_idx: int = 0      # 由引擎在持久化时分配
    status: str = "pending"  # pending | running | done | failed
    results: List[Dict[str, Any]] = field(default_factory=list)


class WorkflowStore:
    """持久化工作流清单: <home>/background/workflows/<wf_id>.json。"""

    def __init__(self, home: Path) -> None:
        self.dir = Path(home) / "background" / "workflows"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, wf_id: str) -> Path:
        return self.dir / f"{wf_id}.json"

    def write(self, data: Dict[str, Any]) -> None:
        """原子落盘: 先写临时文件再 rename, 避免读端读到半截 JSON。

        编排线程与查询方并发访问, 普通 ``write_text`` 先截断再写会让并发 ``get``
        读到空/截断内容 → JSONDecodeError → 误判记录不存在 (崩溃续跑时的竞态)。
        rename 是同卷原子操作, 读端要么看到旧完整文件, 要么看到新完整文件。
        """
        path = self.path(data["workflow_id"])
        tmp = self.dir / f".{data['workflow_id']}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, wf_id: str) -> Optional[Dict[str, Any]]:
        p = self.path(wf_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def list(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not self.dir.exists():
            return out
        for p in self.dir.glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rec["workflow_id"] = rec.get("workflow_id") or p.stem
            out.append(rec)
        out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        return out


class DynamicWorkflowEngine:
    """编排并行后台工作流。

    ``pool_factory``: 测试注入点, 形如 ``pool_factory(engine) -> SubAgentPool``
    (返回项需有 ``dispatch(tasks) -> List[SubResult]``)。
    """

    def __init__(
        self,
        home: Path,
        pool_factory: Optional[Callable[["DynamicWorkflowEngine"], Any]] = None,
    ) -> None:
        self.store = WorkflowStore(home)
        self.pool_factory = pool_factory
        # 生产路径的运行时资源 (configure 注入)
        self.kernel: Optional[Kernel] = None
        self.config: Optional[Config] = None
        self.workspace: str = ""
        self.main_agent: Any = None
        # 每步最大并行任务数 (硬上限, 防资源打爆)
        self.max_parallel = 5
        self._lock = threading.RLock()
        self._workflows: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._stop: Dict[str, threading.Event] = {}

    def configure(self, kernel: Kernel, config: Config, workspace: str,
                  main_agent: Any = None, max_parallel: int = 5) -> None:
        """注入生产运行时资源 (内核 / 配置 / 工作区 / 主 Agent), 供默认池构造。"""
        self.kernel = kernel
        self.config = config
        self.workspace = workspace
        self.main_agent = main_agent
        self.max_parallel = max(1, min(int(max_parallel), 8))

    # ------------------------------------------------------------ 读/写 (加锁 + 落盘)

    def _read(self, wf_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._workflows.get(wf_id)
            if rec is None:
                rec = self.store.get(wf_id)
                if rec is None:
                    raise KeyError(wf_id)
                self._workflows[wf_id] = rec
            return rec

    def _mutate(self, wf_id: str, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        with self._lock:
            rec = self._read(wf_id)
            change(rec)
            self.store.write(rec)
            return rec

    # ------------------------------------------------------------ 创建 / 签发

    def create(self, name: str, steps: Sequence[Sequence[Dict[str, Any]]],
               parallel: bool = True, note: str = "") -> str:
        """创建一个工作流并立即返回 wf_id (编排线程异步执行)。

        steps: 二维结构, 每个元素是一步, 每步内是 [task dict]: {title, prompt, role}。
        """
        wf_id = new_workflow_id()
        steps_rec = []
        for i, st in enumerate(steps):
            tasks = []
            for j, t in enumerate(st or []):
                tasks.append({
                    "task_id": f"S{i+1}T{j+1}",
                    "title": t.get("title", f"步骤{i+1}任务{j+1}"),
                    "prompt": t.get("prompt", ""),
                    "role": t.get("role", "general-purpose"),
                    "status": "pending",
                    "result": "",
                    "error": "",
                })
            steps_rec.append({"step_idx": i, "status": "pending", "tasks": tasks})
        rec = {
            "workflow_id": wf_id,
            "name": name,
            "created_at": time.time(),
            "updated_at": time.time(),
            "note": note,
            "parallel": bool(parallel),
            "status": "pending",
            "steps": steps_rec,
        }
        with self._lock:
            self._workflows[wf_id] = rec
            self.store.write(rec)
        self._kick(wf_id)
        return wf_id

    # ------------------------------------------------------------ 动态加步骤

    def add_step(self, wf_id: str, tasks: Sequence[Dict[str, Any]]) -> int:
        """动态追加一步 (运行中也可), 返回新步骤的 idx。"""
        rec = self._read(wf_id)
        if rec.get("status") in ("done", "failed", "cancelled"):
            raise RuntimeError(f"工作流已结束 ({rec['status']}), 无法追加步骤")
        stamp = uuid.uuid4().hex[:6]  # 防同秒多次追加的 task_id 碰撞
        new_tasks = []
        for j, t in enumerate(tasks or []):
            new_tasks.append({
                "task_id": f"A{stamp}-T{j+1}",
                "title": t.get("title", f"追加任务{j+1}"),
                "prompt": t.get("prompt", ""),
                "role": t.get("role", "general-purpose"),
                "status": "pending",
                "result": "",
                "error": "",
            })
        with self._lock:
            new_idx = len(rec["steps"])
            rec["steps"].append({"step_idx": new_idx, "status": "pending", "tasks": new_tasks})
            rec["updated_at"] = time.time()
            self.store.write(rec)
        # 若当前已完成且无编排线程, 需新起线程跑追加步骤
        self._kick(wf_id)
        return new_idx

    # ------------------------------------------------------------ 状态

    def status(self, wf_id: str) -> Dict[str, Any]:
        rec = self._read(wf_id)
        steps = []
        for st in rec.get("steps", []):
            steps.append({
                "step": st.get("step_idx"),
                "status": st.get("status"),
                "n_tasks": len(st.get("tasks", [])),
                "done_tasks": sum(1 for t in st.get("tasks", [])
                                  if t.get("status") in ("done", "failed")),
            })
        return {
            "workflow_id": rec["workflow_id"],
            "name": rec.get("name", ""),
            "status": rec.get("status"),
            "parallel": rec.get("parallel", True),
            "n_steps": len(steps),
            "steps": steps,
            "updated_at": rec.get("updated_at"),
        }

    def list(self) -> List[Dict[str, Any]]:
        return [{
            "workflow_id": r.get("workflow_id"),
            "name": r.get("name", ""),
            "status": r.get("status"),
            "n_steps": len(r.get("steps", [])),
            "created_at": r.get("created_at"),
        } for r in self.store.list()]

    def result(self, wf_id: str) -> Dict[str, Any]:
        """返回按步骤聚合的结果报告 (供主 Agent 消费)。"""
        rec = self._read(wf_id)
        lines: List[str] = []
        ok_total = 0
        for st in rec.get("steps", []):
            lines.append(f"### 步骤 {st.get('step_idx') + 1} · {st.get('status')}")
            for t in st.get("tasks", []):
                if t.get("status") == "failed":
                    lines.append(f"- ❌ {t.get('title')} ({t.get('task_id')}): "
                                 f"{t.get('error') or t.get('result') or '无输出'}")
                    continue
                if t.get("status") != "done":
                    lines.append(f"- ⏳ {t.get('title')} ({t.get('task_id')}): 未完成")
                    continue
                ok_total += 1
                body = (t.get("result") or "").strip() or "(无输出)"
                lines.append(f"- ✅ {t.get('title')} ({t.get('task_id')})\n    {body[:1200]}")
        return {
            "workflow_id": wf_id,
            "name": rec.get("name", ""),
            "status": rec.get("status"),
            "summary": "\n\n".join(lines),
            "ok_tasks": ok_total,
            "total_done": sum(1 for st in rec.get("steps", [])
                              for t in st.get("tasks", []) if t.get("status") in ("done", "failed")),
            "steps": [{"step": st.get("step_idx"), "status": st.get("status")}
                      for st in rec.get("steps", [])],
        }

    def cancel(self, wf_id: str) -> bool:
        with self._lock:
            rec = self._read(wf_id)
            if rec.get("status") in ("done", "failed", "cancelled"):
                return False
            rec["status"] = "cancelled"
            rec["updated_at"] = time.time()
            self.store.write(rec)
            ev = self._stop.get(wf_id)
        if ev is not None:
            ev.set()
        return True

    # ------------------------------------------------------------ 编排线程

    def _kick(self, wf_id: str) -> None:
        with self._lock:
            th = self._threads.get(wf_id)
            if th is not None and th.is_alive():
                return  # 已有编排线程在跑, 它会在每步结束重读清单
            ev = threading.Event()
            self._stop[wf_id] = ev
            th = threading.Thread(target=self._run_loop, args=(wf_id, ev),
                                  name=f"dwf-{wf_id}", daemon=True)
            self._threads[wf_id] = th
        th.start()

    def _run_loop(self, wf_id: str, stop: threading.Event) -> None:
        try:
            self._orchestrate(wf_id, stop)
        except KeyError:
            pass  # 工作流中途被删除
        finally:
            with self._lock:
                self._threads.pop(wf_id, None)

    def _orchestrate(self, wf_id: str, stop: threading.Event) -> None:
        while not stop.is_set():
            rec = self._read(wf_id)
            if rec.get("status") in ("done", "failed", "cancelled"):
                return
            step_idx = self._next_pending_step(rec)
            if step_idx is None:
                if all(s.get("status") == "done" for s in rec["steps"]) and rec["steps"]:
                    self._finish(wf_id, "done")
                    return
                if not rec["steps"]:
                    self._finish(wf_id, "done")
                    return
                if stop.wait(0.1):  # 拾取动态追加 → 短暂等等再重读
                    return
                continue
            self._mark_running(wf_id, step_idx)
            self._execute_step(wf_id, step_idx, stop)
            if stop.is_set():
                self._mutate(wf_id, lambda r: set_step_status(r["steps"][step_idx], "cancelled"))
                return
            # 一步结束, 循环重读清单以拾取动态追加步骤

    def _next_pending_step(self, rec: Dict[str, Any]) -> Optional[int]:
        for st in rec.get("steps", []):
            if st.get("status") == "pending":
                return st.get("step_idx")
        return None

    def _mark_running(self, wf_id: str, step_idx: int) -> None:
        self._mutate(wf_id, lambda r: (
            set_step_status(r["steps"][step_idx], "running"),
            r["steps"][step_idx].__setitem__("started_at", time.time())))

    def _execute_step(self, wf_id: str, step_idx: int, stop: threading.Event) -> None:
        rec = self._read(wf_id)
        step = rec["steps"][step_idx]
        # 续跑复用: 只派发尚未完成的任务 (done/failed 保留原判), 不重复已完成劳动。
        tasks_rec = [t for t in step["tasks"] if t.get("status") not in ("done", "failed")]
        parallel = bool(rec.get("parallel", True))
        subtasks = _to_subtasks(tasks_rec)
        if not subtasks:
            self._mutate(wf_id, lambda r: set_step_status(r["steps"][step_idx], "done"))
            return

        try:
            pool = self._make_pool()
        except Exception as exc:  # noqa: BLE001 - 池初始化失败按整步失败处理
            def _fail_pool(r):
                set_step_status(r["steps"][step_idx], "failed")
                set_task_results(r["steps"][step_idx], [], force_error=str(exc))
                r["status"] = "failed"
                r["updated_at"] = time.time()
            self._mutate(wf_id, _fail_pool)
            return

        results: List[Any] = []
        try:
            if parallel:
                # 并行模式: 分批派发, 硬上限 max_parallel/批, 防资源打爆
                batch = self.max_parallel
                for i in range(0, len(subtasks), batch):
                    if stop.is_set():
                        break
                    results.extend(pool.dispatch(subtasks[i:i + batch], stream=False))
            else:
                # 串行模式 (parallel=False): 逐一执行, 依次取结果
                for sub in subtasks:
                    if stop.is_set():
                        break
                    results.extend(pool.dispatch([sub], stream=False))
        except Exception as exc:  # noqa: BLE001 - 运行期池异常, 未完成任务标失败
            results = _mark_aborted(results, subtasks, str(exc))

        # 把结果写回 (按 task_id 对齐)
        mapping = {t.get("task_id"): t for t in tasks_rec}
        for res in results:
            rec_task = mapping.get(getattr(res, "task_id", ""))
            if rec_task is None:
                continue
            rec_task["status"] = "done" if getattr(res, "ok", False) else "failed"
            rec_task["result"] = getattr(res, "output", "") or ""
            rec_task["error"] = getattr(res, "error", "") or ""
        # 重新加锁读取最新清单 (期间可能被 add_step 修改)
        with self._lock:
            rec = self._read(wf_id)
            step = rec["steps"][step_idx]
            tasks = step["tasks"]
            failed = [t for t in tasks if t.get("status") == "failed"]
            step["status"] = "failed" if failed else "done"
            step["results"] = [
                {"title": t.get("title"), "status": t.get("status"),
                 "result": t.get("result") or "", "error": t.get("error") or ""}
                for t in tasks
            ]
            rec["updated_at"] = time.time()
            if failed:
                rec["status"] = "failed"
            self.store.write(rec)

    def retry_failed(self, wf_id: str) -> bool:
        """把全部失败任务重置为 pending, 重新编排 (原地重跑失败步)。"""
        with self._lock:
            rec = self._read(wf_id)
            if rec.get("status") not in ("failed", "done"):
                return False
            reset = False
            for st in rec["steps"]:
                fails = [t for t in st["tasks"] if t.get("status") == "failed"]
                if fails:
                    for t in fails:
                        t["status"] = "pending"
                        t["result"] = ""
                        t["error"] = ""
                    st["status"] = "pending"
                    reset = True
            rec["status"] = "pending"
            rec["updated_at"] = time.time()
            self.store.write(rec)
        if reset:
            self._kick(wf_id)
        return reset

    # ------------------------------------------------------------ 续跑 (fx4)

    def resume_all(self) -> List[str]:
        """进程重启后自动续跑所有未结束的工作流 (running/pending)。

        回收崩溃遗留的状态: 凡处于 ``running`` 的步骤, 其 ``running`` 任务复位为
        ``pending`` (崩溃时这些任务的结果从未落盘, 重跑才一致), 步骤回到 ``pending``
        重新编排; 对已经 ``done`` 的任务保持完成, ``_execute_step`` 会跳过它们, 避免重复劳动。
        """
        resumed: List[str] = []
        for rec in self.store.list():
            wf_id = rec.get("workflow_id")
            if not wf_id:
                continue
            status = rec.get("status")
            if status not in ("running", "pending"):
                continue
            recycled = self._recycle_running_steps(rec)
            if recycled:
                rec["status"] = "pending"
                rec["updated_at"] = time.time()
                self.store.write(rec)
            with self._lock:
                self._workflows[wf_id] = rec
            self._kick(wf_id)
            resumed.append(wf_id)
        return resumed

    @staticmethod
    def _recycle_running_steps(rec: Dict[str, Any]) -> bool:
        """把崩溃遗留的 running 步骤/任务复位为 pending, 返回是否有回收。"""
        recycled = False
        for st in rec.get("steps", []):
            if st.get("status") != "running":
                continue
            for t in st.get("tasks", []):
                if t.get("status") == "running":
                    t["status"] = "pending"
                    t["result"] = ""
                    t["error"] = ""
                    recycled = True
            st["status"] = "pending"
            recycled = True
        return recycled

    def is_terminated(self, wf_id: str) -> bool:
        with self._lock:
            return self._read(wf_id).get("status") in ("done", "failed", "cancelled")

    def _finish(self, wf_id: str, status: str) -> None:
        with self._lock:
            rec = self._read(wf_id)
            rec["status"] = status
            rec["updated_at"] = time.time()
            self.store.write(rec)
        # 内核事件 (用于可观测性/订阅: workflow.completed)
        kernel = getattr(self, "kernel", None)
        if kernel is not None:
            try:
                res = self.result(wf_id)
                kernel.emit("workflow.completed", {
                    "workflow_id": wf_id,
                    "name": rec.get("name", ""),
                    "status": status,
                    "steps": len(rec.get("steps", [])),
                    "ok_tasks": res.get("ok_tasks", 0),
                })
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ 池构造 (测试可注入)

    def _make_pool(self):
        if self.pool_factory is not None:
            return self.pool_factory(self)
        # 生产路径: 用 configure 注入的运行时资源构造真实 SubAgentPool
        if self.kernel is None or self.config is None:
            raise RuntimeError(
                "DynamicWorkflowEngine 未配置 (需 pool_factory 或 configure 注入运行时资源)")
        from .subagents import SubAgentPool
        confirm = None
        if self.main_agent is not None and getattr(self.main_agent, "ctx", None) is not None:
            confirm = getattr(self.main_agent.ctx, "confirm", None)
        pool = SubAgentPool(
            kernel=self.kernel,
            config=self.config,
            workspace=self.workspace,
            main_agent=self.main_agent,
            confirm=confirm,
            max_workers=self.max_parallel,
            default_timeout=float(self.config.get("agent.subagent_timeout", 180)),
            isolation="process",
            model_overrides=dict(getattr(self, "model_overrides", {}) or {}),
        )
        return pool


def _to_subtasks(tasks_rec: Sequence[Dict[str, Any]]) -> List[Any]:
    from .subagents import SubTask
    out = []
    for t in tasks_rec:
        meta = {"system_extra": "", "role": t.get("role", "general-purpose")}
        role = t.get("role")
        if role and role != "general-purpose":
            from .agent_types import get_agent_type
            at = get_agent_type(role)
            if at is not None and at.system_extra:
                meta["system_extra"] = at.system_extra
        out.append(SubTask(task_id=t.get("task_id", "T"),
                           prompt=t.get("prompt", ""), meta=meta))
    return out


def _mark_aborted(results: List[Any], subtasks: Sequence[Any], reason: str) -> List[Any]:
    """池在运行期抛异常时, 给未产生结果的任务补一个失败结果, 保证状态完整。"""
    from .subagents import SubResult
    done_ids = {getattr(r, "task_id", "") for r in results}
    out = list(results)
    for sub in subtasks:
        if sub.task_id not in done_ids:
            out.append(SubResult(task_id=sub.task_id, prompt=sub.prompt, ok=False,
                                 output="", error=f"执行中断: {reason}"))
    return out


def set_step_status(step: Dict[str, Any], status: str) -> None:
    step["status"] = status


def set_task_results(step: Dict[str, Any], results: List[Any], force_error: str = "") -> None:
    for t in step.get("tasks", []):
        t["status"] = "failed"
        t["error"] = force_error or "池初始化失败"


def new_workflow_id() -> str:
    return "wf-" + uuid.uuid4().hex[:10]


# ------------------------------------------------------------ 进程级单例引擎

_ENGINES: Dict[str, DynamicWorkflowEngine] = {}
_ENGINES_LOCK = threading.Lock()


def get_engine(home: Path, auto_resume: bool = True) -> DynamicWorkflowEngine:
    """取/建进程级共享引擎 (同一 home 复用同实例, 编排线程互相可见)。

    首次创建引擎时 (进程重启后的首个访问) 会 ``resume_all`` 自动续跑之前未结束的
    工作流 —— 这样即使进程中途退出, 重新起来后任务也能接着跑完。
    """
    key = str(Path(home).resolve())
    with _ENGINES_LOCK:
        eng = _ENGINES.get(key)
        if eng is None:
            eng = DynamicWorkflowEngine(home)
            _ENGINES[key] = eng
            if auto_resume:
                try:
                    eng.resume_all()
                except Exception:  # noqa: BLE001 - 续跑失败不阻断引擎可用
                    pass
        return eng


def close_engines() -> None:
    """释放全部共享引擎 (测试用)。"""
    with _ENGINES_LOCK:
        _ENGINES.clear()


def parse_steps(steps_json: Any) -> List[List[Dict[str, Any]]]:
    """把工具传进来的 steps (可能是 JSON 字符串或列表) 规范为 [[taskdict, ...]]。"""
    if steps_json is None:
        return []
    if isinstance(steps_json, str):
        try:
            steps_json = json.loads(steps_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(steps_json, list):
        return []
    steps: List[List[Dict[str, Any]]] = []
    for st in steps_json:
        if isinstance(st, list):
            steps.append([t for t in st if isinstance(t, dict)])
        elif isinstance(st, dict):
            # 兼容单任务误传: 视作一步含一个任务
            steps.append([st])
        else:
            steps.append([])
    return steps