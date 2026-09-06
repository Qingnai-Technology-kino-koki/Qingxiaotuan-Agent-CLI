"""并发子 Agent 编排 (SubAgentPool) —— 给青小团装上"多双手"。

设计要点 (融合 Harness 微内核 + Hermes 隔离上下文):
- 每个子任务跑在**隔离的 Agent 实例**里: 各自的 messages / 上下文, 互不污染主循环;
- 但**共享同一个内核的工具注册表** (shell / web_fetch / filesystem / code …),
  所以子 Agent 天然拥有和主 Agent 一样的"手";
- 支持**并发** (线程池) 或串行执行, 每个子任务有独立超时;
- 结果统一**汇总回主循环**, 带来源标注 (避免主上下文被无关细节淹没);
- 失败隔离: 单个子 Agent 出错不影响其它, 不拖垮整轮。

典型用途:
- 把"调研 A / 调研 B / 调研 C"三个独立子任务并发派出, 主 Agent 等齐结果再综合;
- DevLoop 的并行调研阶段 (见 core/devloop.py 的 parallel_research);
- /parallel 斜杠命令 (见 cli/commands.py)。
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence

from .kernel import Kernel

if TYPE_CHECKING:
    from .agent import Agent


@dataclass
class SubTask:
    """一个独立子任务。"""

    task_id: str
    prompt: str
    # 子 Agent 可临时排除的工具 (默认继承主 Agent 的 exclude_tools)
    exclude_tools: Sequence[str] = field(default_factory=tuple)
    # 子 Agent 的 effort / 模式等运行时提示 (可选)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SubResult:
    """单个子任务的执行结果 (带隔离状态, 对标 Claude Code 的 subagentStatusLine)。"""

    task_id: str
    prompt: str
    ok: bool
    output: str
    turns: int = 0
    elapsed: float = 0.0
    error: Optional[str] = None
    # 子 Agent 内部产生的工具调用摘要 (供主循环审计, 不回灌上下文)
    tool_calls: List[Dict[str, str]] = field(default_factory=list)
    # Claude Code 对标: 状态行信息 (model, effort, elapsed)
    status_line: str = ""  # 格式: "model=xxx · effort=high · 12.3s"

    def to_block(self) -> str:
        """汇总成带来源标注的文本块, 供主 Agent 消费。"""
        status = "✅完成" if self.ok else "❌失败"
        head = f"### 子任务 [{self.task_id}] · {status} · 用时 {self.elapsed:.1f}s"
        if self.status_line:
            head += f" · {self.status_line}"
        body = self.output.strip() or "(无输出)"
        if not self.ok and self.error:
            body += f"\n\n错误: {self.error}"
        return f"{head}\n\n{body}"


class SubAgentPool:
    """把独立子任务派发给隔离 Agent 实例, 并发或串行执行, 汇总结果。"""

    def __init__(
        self,
        kernel: Kernel,
        config,
        workspace: str,
        main_agent: Optional[Agent] = None,
        confirm: Optional[Callable[[str], bool]] = None,
        exclude_tools: Sequence[str] = (),
        max_workers: Optional[int] = None,
        default_timeout: float = 180.0,
        isolation: str = "process",
        worker_module: str = "qingxiaotuan.core._sandbox_entry",
        model_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.kernel = kernel
        self.config = config
        self.workspace = workspace
        self.main_agent = main_agent
        self.confirm = confirm
        self.exclude_tools = tuple(exclude_tools)
        # 并发度: 默认 5, 硬上限 5 (民用电脑带不动更多)
        # 可由配置 agent.subagent_max_workers 覆盖, 但不超过 5
        if max_workers is None:
            max_workers = int(config.get("agent.subagent_max_workers", 5))
        self.max_workers = max(1, min(max_workers, 5))
        self.default_timeout = default_timeout
        # isolation: "process" = 进程级沙箱隔离 (生产级, 默认); "thread" = 软隔离 (兼容/无 subprocess)
        self.isolation = isolation if isolation in ("process", "thread") else "process"
        # 子进程 worker 模块 (测试可覆盖为 fake worker, 无需真实模型)
        self.worker_module = worker_module
        # 子 Agent 进程内统一的模型覆盖层 (如 herdr 弱模型 provider)
        self.model_overrides = model_overrides or {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 派发

    def dispatch(
        self,
        tasks: Sequence[SubTask],
        stream: bool = False,
        on_sub_token: Optional[Callable[[str, str], None]] = None,
        on_sub_tool: Optional[Callable[[str, str, str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> List[SubResult]:
        """并发执行所有子任务, 返回与输入顺序一致的 SubResult 列表。

        on_sub_tool(task_id, name, args) 用于把子 Agent 的工具活动透出到主 UI。
        """
        if not tasks:
            return []
        # 任务少就别开多余线程
        workers = min(self.max_workers, len(tasks))
        results: List[Optional[SubResult]] = [None] * len(tasks)

        def _run(idx: int, task: SubTask) -> None:
            results[idx] = self._run_one(
                task, stream=stream,
                on_sub_token=on_sub_token, on_sub_tool=on_sub_tool, on_error=on_error,
            )
            # 用户级 Hooks: SubagentStop (单个子任务结束时通知, 异常隔离不影响派发)
            hooks = getattr(getattr(self.main_agent, "ctx", None), "hooks", None) \
                if self.main_agent is not None else None
            if hooks is not None:
                r = results[idx]
                try:
                    hooks.run_notify("SubagentStop", {
                        "task_id": task.task_id,
                        "prompt": (task.prompt or "")[:500],
                        "ok": bool(getattr(r, "ok", False)),
                        "elapsed": getattr(r, "elapsed", 0.0),
                        "error": getattr(r, "error", "") or "",
                    })
                except Exception:
                    pass

        # 把全局 model_overrides (如 herdr 弱模型) 写进每个子任务, 仅当任务本身未指定
        for t in tasks:
            if self.model_overrides and not t.meta.get("model_overrides"):
                t.meta = {**t.meta, "model_overrides": self.model_overrides}

        if workers <= 1 or len(tasks) == 1:
            # 串行
            for idx, task in enumerate(tasks):
                _run(idx, task)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(_run, idx, task) for idx, task in enumerate(tasks)]
                # 等待全部 (超时由各子任务内部控制, 这里只等线程结束)
                for fut in concurrent.futures.as_completed(futures):
                    fut.result()  # 异常已在 _run_one 内被吞掉, 这里不会抛

        # 保证顺序与输入一致
        return [r if r is not None else SubResult("", "", False, "", error="未执行") for r in results]

    # ------------------------------------------------------------ 单任务

    def _run_one(
        self,
        task: SubTask,
        stream: bool = False,
        on_sub_token: Optional[Callable[[str, str], None]] = None,
        on_sub_tool: Optional[Callable[[str, str, str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        timeout: Optional[float] = None,
    ) -> SubResult:
        timeout = timeout or self.default_timeout
        # 子任务级的模型覆盖 (来自 pool 的全局弱模型层 或 单个任务特例)
        model_overrides = task.meta.get("model_overrides") or self.model_overrides

        # 进程级沙箱隔离: 子 Agent 在独立子进程 + 临时沙箱副本里跑, 主仓库零风险
        if self.isolation == "process":
            return self._run_one_sandbox(task, timeout, model_overrides)

        # 线程级软隔离 (兼容 / 无 subprocess 环境)
        return self._run_one_thread(task, stream, on_sub_token, on_sub_tool, on_error, timeout, model_overrides)

    def _run_one_sandbox(self, task: SubTask, timeout: float, model_overrides: Optional[Dict[str, Any]] = None) -> SubResult:
        """进程级沙箱: 在临时副本里跑子 Agent, 结果只回传文本, 主仓库不被回写。"""
        from .sandbox import run_in_sandbox

        yolo = bool(self.main_agent.ctx.yolo) if self.main_agent is not None else False
        return run_in_sandbox(
            task,
            workspace=self.workspace,
            profile=getattr(self.config, "profile", "default"),
            qxt_home=str(self.config.home),
            exclude_tools=self.exclude_tools,
            yolo=yolo,
            timeout=timeout,
            model_overrides=model_overrides or {},
            # 类型化子代理: 角色指令随请求文件透传给沙箱子进程
            system_extra=str(task.meta.get("system_extra", "") or ""),
            worker_module=self.worker_module,
        )

    def _run_one_thread(
        self,
        task: SubTask,
        stream: bool = False,
        on_sub_token: Optional[Callable[[str, str], None]] = None,
        on_sub_tool: Optional[Callable[[str, str, str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        timeout: Optional[float] = None,
        model_overrides: Optional[Dict[str, Any]] = None,
    ) -> SubResult:
        if timeout is None:
            timeout = self.default_timeout
        started = time.time()

        def _worker() -> SubResult:
            # 临时热切换的还原上下文: (切换前的 model.* 配置视图, 切换前的内核适配器)。
            # 在调 switch_model 之前先记好, 即使切换半途失败也能完整还原。
            switch_ctx: Optional[tuple] = None
            try:
                # 延迟导入, 打破 core.agent <-> tools.dispatch 的循环依赖
                from .agent import Agent
                # 线程隔离模式下, 若提供了弱模型覆盖且为单 worker, 临时热切换内核模型
                # (并发多 worker 时内核 adapter 是共享状态, 切换会互相干扰 —— 此时请用 process 隔离)
                if model_overrides and self.max_workers == 1:
                    from ..models.plugin import ModelPlugin
                    ov = dict(model_overrides)
                    before = {f"model.{k}": self.config.get(f"model.{k}") for k in ov}
                    prev_adapter = self.kernel.get("model_adapter")
                    switch_ctx = (before, prev_adapter)
                    ModelPlugin.switch_model(self.kernel, ov, persist=False)
                # 隔离的 Agent 实例: 各自的 messages/上下文, 共享内核工具
                agent = Agent(
                    kernel=self.kernel,
                    config=self.config,
                    workspace=self.workspace,
                    confirm=self.confirm,
                    exclude_tools=tuple(set(self.exclude_tools) | set(task.exclude_tools)),
                    # 类型化子代理: 角色指令经 meta["system_extra"] 注入系统提示
                    system_extra=str(task.meta.get("system_extra", "") or ""),
                )
                if self.main_agent is not None and hasattr(self.main_agent, "ctx"):
                    # YOLO / 自动批准回调一并继承, 后台干活无需逐个点确认
                    agent.ctx.yolo = self.main_agent.ctx.yolo
                    agent.ctx.on_auto_approve = self.main_agent.ctx.on_auto_approve

                captured: List[Dict[str, str]] = []

                def _on_tool(name: str, args: str) -> None:
                    captured.append({"name": name, "args": args[:200]})
                    if on_sub_tool:
                        on_sub_tool(task.task_id, name, args)

                out = agent.run(
                    task.prompt,
                    stream=stream,
                    on_token=(lambda t: on_sub_token(task.task_id, t)) if on_sub_token else None,
                    on_tool=_on_tool,
                    on_reason=None,
                    on_tool_result=None,
                    on_error=on_error,
                )
                # agent.run 在模型错误时会把错误当文本返回 (前缀 [模型错误]),
                # 这里把它还原成"失败", 让主 Agent 知道该子任务没真正完成。
                run_err = None
                if (out or "").startswith("[模型错误]"):
                    ok = False
                    run_err = out
                else:
                    ok = True
                return SubResult(
                    task_id=task.task_id,
                    prompt=task.prompt,
                    ok=ok,
                    output=out or "",
                    turns=agent.turn_count,
                    elapsed=time.time() - started,
                    error=run_err,
                    tool_calls=captured,
                )
            except Exception as exc:  # noqa: BLE001
                return SubResult(
                    task_id=task.task_id,
                    prompt=task.prompt,
                    ok=False,
                    output="",
                    turns=0,
                    elapsed=time.time() - started,
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                # 无论成败都还原临时热切换 (与 Swarm._strong_switch_off 同一套语义):
                # 不还原的话内核里会残留弱模型适配器, 主 Agent 后续所有轮次都被劫持。
                if switch_ctx is not None:
                    before, prev_adapter = switch_ctx
                    # 还原内存配置视图 (persist=False 只改了内存层, 换回即无残留)
                    try:
                        from ..config.loader import patch_replace
                        self.config.data = patch_replace(self.config.data, before)
                    except Exception:  # noqa: BLE001
                        pass
                    # 把切换前的适配器实例装回内核
                    try:
                        self.kernel.unprovide("model_adapter")
                        if prev_adapter is not None:
                            self.kernel.provide("model_adapter", prev_adapter, owner="model")
                    except Exception:  # noqa: BLE001
                        pass

        # 超时控制: 用线程包装, 超时则标记失败而非无限阻塞
        result_holder: Dict[str, SubResult] = {}

        def _target() -> None:
            result_holder["r"] = _worker()

        t = threading.Thread(target=_target, name=f"subagent-{task.task_id}", daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            # 超时: 线程仍在跑 (daemon, 会被进程退出时回收), 这里只返回超时结果
            return SubResult(
                task_id=task.task_id,
                prompt=task.prompt,
                ok=False,
                output="",
                turns=0,
                elapsed=time.time() - started,
                error=f"执行超时 (>{timeout:.0f}s)",
            )
        return result_holder.get(
            "r",
            SubResult(task.task_id, task.prompt, False, "", error="未知执行错误"),
        )

    # ------------------------------------------------------------ 汇总

    @staticmethod
    def aggregate(results: Sequence[SubResult], title: str = "子任务汇总") -> str:
        """把多个子结果聚合成一份带来源标注的报告 (供主 Agent 直接消费)。"""
        ok = sum(1 for r in results if r.ok)
        lines = [
            f"# {title} (共 {len(results)} 项, 成功 {ok}, 失败 {len(results) - ok})",
            "",
        ]
        for r in results:
            lines.append(r.to_block())
            lines.append("")
        return "\n".join(lines).strip()


# 便捷构造: 从纯文本 prompt 列表造 SubTask
def make_tasks(prompts: Sequence[str], prefix: str = "T") -> List[SubTask]:
    return [SubTask(task_id=f"{prefix}{i+1}", prompt=p) for i, p in enumerate(prompts)]
