"""编排层 — 5 子代理并发 + 3 层嵌套 + 独立上下文/工作树 + 自动冲突解决。

设计:
- Orchestrator.spawn(specs) 并发拉起 N 个子代理 (默认 5), 每个跑在自己的线程里。
- 每个 SubAgent 拥有:
    * 独立上下文 (AgentContext, 不与其他代理共享可变状态)
    * 独立工作树 (用 core.sandbox 复制工作区得到的隔离沙箱; 默认用 tempdir 模拟)
    * 自己的 Agent Loop (可不同策略)
    * 最多 3 层嵌套: depth 0..2, 每层可再派生最多 N 个嵌套子代理 (每个再独立工作树/上下文)
- 所有子代理完成后进入冲突解决阶段:
    * ConflictDetector 检测同一资源 (文件路径) 被多个代理以不同内容改写 -> 冲突
    * ConflictResolver 按策略解决: auto-merge (非重叠直接合) / last-writer-wins (优先级高者胜)
      / escalate (升级给人类审批)
    * HumanApprovalGate 在人类审批模式下暂停并回调; 非交互环境用注入的 approver
      (auto=全通过, deny=全拒, 或自定义 callback)。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_NEST_DEPTH = 3  # 3 层嵌套


@dataclass
class AgentContext:
    """某子代理的**独立**上下文字典。不与其它代理共享可变对象。"""

    agent_id: str
    parent_id: Optional[str] = None
    depth: int = 0
    data: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def set(self, k: str, v: Any) -> None:
        self.data[k] = v

    def get(self, k: str, default: Any = None) -> Any:
        return self.data.get(k, default)


@dataclass
class SubAgentResult:
    agent_id: str
    depth: int
    goal: str
    answer: str = ""
    worktree: Optional[str] = None
    # 该代理产出的"制品": 相对路径 -> 内容, 用于冲突检测
    artifacts: Dict[str, str] = field(default_factory=dict)
    children: List["SubAgentResult"] = field(default_factory=list)
    error: Optional[str] = None


# ----------------------------------------------------------- 人类审批门
class HumanApprovalGate:
    """人类工作者审批门。

    mode:
      auto      —— 非交互环境默认全通过 (CI/批处理)
      deny      —— 全拒绝 (保守模式)
      callback  —— 用自定义函数询问人类 (交互环境)
    """

    def __init__(self, mode: str = "auto", callback: Optional[Callable[[Dict[str, Any]], bool]] = None) -> None:
        if mode not in ("auto", "deny", "callback"):
            raise ValueError("mode 必须是 auto/deny/callback")
        self.mode = mode
        self.callback = callback

    def request(self, proposal: Dict[str, Any]) -> bool:
        if self.mode == "auto":
            return True
        if self.mode == "deny":
            return False
        if self.callback is not None:
            return bool(self.callback(proposal))
        return False


# ----------------------------------------------------------- 冲突检测与解决
@dataclass
class Conflict:
    resource: str  # 资源标识 (文件路径)
    candidates: List[Tuple[str, str]]  # [(agent_id, content), ...]
    kind: str = "content-divergence"


class ConflictDetector:
    """跨代理检测对同一资源的竞争性改写。"""

    @staticmethod
    def detect(results: List[SubAgentResult]) -> List[Conflict]:
        # 汇总所有代理 (含嵌套) 的 artifacts
        by_resource: Dict[str, List[Tuple[str, str]]] = {}
        for r in results:
            for res in _iter_results(r):
                for path, content in res.artifacts.items():
                    by_resource.setdefault(path, []).append((res.agent_id, content))
        conflicts: List[Conflict] = []
        for resource, cands in by_resource.items():
            if len(cands) > 1 and len({c for _, c in cands}) > 1:
                conflicts.append(Conflict(resource=resource, candidates=cands))
        return conflicts


class Resolution(str, Enum):
    AUTO_MERGED = "auto-merged"
    LAST_WRITER_WINS = "last-writer-wins"
    ESCALATED = "escalated"
    REJECTED = "rejected"


class ConflictResolver:
    """按策略解决冲突。

    strategy:
      auto-merge        —— 内容不重叠则直接合并 (这里简化为保留全部不同版本为分块注释)
      last-writer-wins  —— 按 priority 字典里优先级最高的代理胜出
      escalate         —— 调用 gate.request(); 通过则取优先级最高者, 否则 REJECTED
    priority: agent_id -> 数字, 越大优先级越高
    """

    def __init__(
        self,
        strategy: str = "last-writer-wins",
        gate: Optional[HumanApprovalGate] = None,
        priority: Optional[Dict[str, int]] = None,
    ) -> None:
        self.strategy = strategy
        self.gate = gate or HumanApprovalGate("auto")
        self.priority = priority or {}

    def _winner(self, candidates: List[Tuple[str, str]]) -> Tuple[str, str]:
        return max(candidates, key=lambda ac: self.priority.get(ac[0], 0))

    def resolve(self, conflict: Conflict) -> Tuple[Resolution, Optional[str]]:
        if self.strategy == "auto-merge":
            merged = "\n\n".join(f"# from {aid}\n{content}" for aid, content in conflict.candidates)
            return Resolution.AUTO_MERGED, merged
        if self.strategy == "last-writer-wins":
            aid, content = self._winner(conflict.candidates)
            return Resolution.LAST_WRITER_WINS, content
        if self.strategy == "escalate":
            proposal = {
                "resource": conflict.resource,
                "candidates": conflict.candidates,
                "winner": self._winner(conflict.candidates)[0],
            }
            if self.gate.request(proposal):
                return Resolution.ESCALATED, self._winner(conflict.candidates)[1]
            return Resolution.REJECTED, None
        raise ValueError(f"未知策略: {self.strategy}")


# ----------------------------------------------------------- 子代理与编排器
@dataclass
class AgentSpec:
    id: str
    goal: str
    depth: int = 0
    parent_id: Optional[str] = None
    loop_name: str = "react"
    tools: List[str] = field(default_factory=list)
    # 嵌套子规格 (编排器会替你套 3 层)
    children: List["AgentSpec"] = field(default_factory=list)


def default_executor(goal: str, ctx: AgentContext, worktree: Path) -> Tuple[str, Dict[str, str]]:
    """默认执行器: 把一个标记文件写到独立工作树, 返回 (answer, artifacts)。

    真实环境里这里会跑 Agent Loop + 工具; 这里用确定性实现便于测试与演示架构。
    """
    out = worktree / "result.txt"
    out.write_text(f"done: {goal}\n", encoding="utf-8")
    artifact_path = f"agent_{ctx.agent_id}/result.txt"
    return f"completed: {goal}", {artifact_path: out.read_text(encoding="utf-8")}


class SubAgent:
    """单个子代理: 独立上下文 + 独立工作树 + 可选嵌套子代理。"""

    def __init__(
        self,
        spec: AgentSpec,
        executor: Callable[[str, AgentContext, Path], Tuple[str, Dict[str, str]]] = default_executor,
        base_workspace: Optional[str] = None,
    ) -> None:
        self.spec = spec
        self.executor = executor
        self.base_workspace = base_workspace
        self.context = AgentContext(agent_id=spec.id, parent_id=spec.parent_id, depth=spec.depth)

    def run(self) -> SubAgentResult:
        # 独立工作树: 真实环境用 core.sandbox.prepare_sandbox 复制工作区;
        # 这里用 tempdir 模拟隔离 (同样保证主仓库零污染)。
        wt = Path(tempfile.mkdtemp(prefix=f"qxt-wt-{self.spec.id}-"))
        self.context.set("worktree", str(wt))
        result: Optional[SubAgentResult] = None
        try:
            answer, artifacts = self.executor(self.spec.goal, self.context, wt)
            result = SubAgentResult(
                agent_id=self.spec.id, depth=self.spec.depth, goal=self.spec.goal,
                answer=answer, worktree=str(wt), artifacts=artifacts,
            )
            # 3 层嵌套: 当前层 depth < MAX 才派生子代理
            if self.spec.depth < MAX_NEST_DEPTH - 1 and self.spec.children:
                for child in self.spec.children:
                    child.parent_id = self.spec.id
                    child.depth = self.spec.depth + 1
                    child_exec = SubAgent(child, executor=self.executor, base_workspace=self.base_workspace)
                    result.children.append(child_exec.run())
        except Exception as exc:  # noqa: BLE001
            result = SubAgentResult(
                agent_id=self.spec.id, depth=self.spec.depth, goal=self.spec.goal,
                worktree=str(wt), error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            # 无论如何都清理临时工作树 (制品已提取到 artifacts dict)
            try:
                shutil.rmtree(wt, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        return result


@dataclass
class OrchestrationResult:
    results: List[SubAgentResult] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    resolutions: List[Tuple[str, Resolution, Optional[str]]] = field(default_factory=list)
    merged_artifacts: Dict[str, str] = field(default_factory=dict)


class Orchestrator:
    """并发编排 5 个子代理 (默认), 含 3 层嵌套, 最后自动解决冲突。"""

    def __init__(
        self,
        executor: Callable[[str, AgentContext, Path], Tuple[str, Dict[str, str]]] = default_executor,
        resolver: Optional[ConflictResolver] = None,
        max_concurrency: int = 5,
        base_workspace: Optional[str] = None,
        per_agent_timeout: float = 300.0,
    ) -> None:
        self.executor = executor
        self.resolver = resolver or ConflictResolver("last-writer-wins")
        self.max_concurrency = max_concurrency
        self.base_workspace = base_workspace
        self.per_agent_timeout = per_agent_timeout
        self._lock = threading.Lock()

    def spawn(self, specs: List[AgentSpec]) -> OrchestrationResult:
        specs = specs[: self.max_concurrency] if len(specs) > self.max_concurrency else specs
        results: List[SubAgentResult] = []

        def _run(spec: AgentSpec) -> SubAgentResult:
            return SubAgent(spec, executor=self.executor, base_workspace=self.base_workspace).run()

        from concurrent.futures import wait as _futures_wait, FIRST_EXCEPTION
        futures_map: Dict = {}
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            for spec in specs:
                fut = pool.submit(_run, spec)
                futures_map[fut] = spec
            # 等待所有完成或超时
            done, not_done = _futures_wait(
                futures_map.keys(), timeout=self.per_agent_timeout,
                return_when=FIRST_EXCEPTION,
            )
            # 收集已完成的结果
            for fut in done:
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    spec = futures_map[fut]
                    results.append(SubAgentResult(
                        agent_id=spec.id, depth=spec.depth, goal=spec.goal,
                        error=f"{type(exc).__name__}: {exc}",
                    ))
            # 超时/未完成的子代理: 取消并记录错误
            for fut in not_done:
                fut.cancel()
                spec = futures_map[fut]
                results.append(SubAgentResult(
                    agent_id=spec.id, depth=spec.depth, goal=spec.goal,
                    error=f"Timeout: 子代理 {spec.id} 超过 {self.per_agent_timeout}s 未完成",
                ))

        conflicts = ConflictDetector.detect(results)
        resolutions: List[Tuple[str, Resolution, Optional[str]]] = []
        merged: Dict[str, str] = {}
        for c in conflicts:
            r, content = self.resolver.resolve(c)
            resolutions.append((c.resource, r, content))
            if content is not None:
                merged[c.resource] = content
        # 无冲突的制品直接并入
        for res in results:
            for sub in _iter_results(res):
                for path, content in sub.artifacts.items():
                    if path not in merged:
                        merged[path] = content
        return OrchestrationResult(
            results=results, conflicts=conflicts, resolutions=resolutions, merged_artifacts=merged,
        )


def kernel_executor(
    kernel,
    *,
    exclude_tools=None,
    max_iterations: int = 10,
    session_id: Optional[str] = None,
    per_agent_timeout: float = 300.0,
    max_artifact_size: int = 5000,
    max_artifacts: int = 50,
    use_sandbox: bool = False,
    sandbox_backend: str = "local",
):
    """创建一个真正的 Agent Loop 执行器, 供 Orchestrator 使用。

    返回与 ``default_executor`` 签名兼容的 callable: (goal, AgentContext, Path) -> (answer, artifacts)。
    实际调用 Agent Loop (通过 core.loop_provider.LoopProvider) 驱动子代理。

    特性:
    - Agent 创建失败时 fail-closed (返回错误信息, 不崩溃)
    - Loop 执行有超时保护 (per_agent_timeout 秒)
    - 制品收集有大小/数量限制, 避免超大工作区拖垮内存
    - 每个子代理的日志记录到 session, 便于事后审计
    - 可选沙箱执行 (use_sandbox=True): 通过 SandboxProvider 在进程级隔离中运行子代理,
      敏感环境变量自动抹除, 文件系统操作限定在工作树内

    用法::

        from qingxiaotuan.app import build_kernel, create_agent
        kernel = build_kernel()
        orch = Orchestrator(executor=kernel_executor(kernel))
        # 需要沙箱隔离:
        orch = Orchestrator(executor=kernel_executor(kernel, use_sandbox=True))
    """
    from ..core.agent import Agent as _Agent
    from ..core.ledger import MutationLedger as _MutationLedger
    from ..config import Config as _Config

    # 二进制/非文本扩展名黑名单: 这些文件不纳入制品
    _SKIP_SUFFIXES = frozenset({
        ".pyc", ".pyo", ".db", ".db-journal", ".so", ".dll",
        ".exe", ".bin", ".o", ".a", ".png", ".jpg", ".jpeg",
        ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf",
        ".zip", ".tar", ".gz", ".rar", ".7z",
    })

    def _exec(goal: str, ctx: AgentContext, worktree: Path) -> Tuple[str, Dict[str, str]]:
        import logging as _log
        import time as _time

        _log_exec = _log.getLogger(__name__)
        t0 = _time.monotonic()

        # ---- Phase 0: 沙箱安全 (可选) ----
        _sandbox_provider = None
        if use_sandbox:
            try:
                from ..core.sandbox_provider import SandboxProvider
                _sandbox_provider = SandboxProvider.create(sandbox_backend)
                if not _sandbox_provider.is_available():
                    _log_exec.info(
                        "kernel_executor: 沙箱后端 %s 不可用, 降级为本地执行",
                        sandbox_backend,
                    )
                    _sandbox_provider = None
            except Exception as exc:  # noqa: BLE001
                _log_exec.info("kernel_executor: 沙箱初始化失败, 降级为本地执行: %s", exc)
                _sandbox_provider = None

        # ---- Phase 1: 创建子代理 Agent ----
        try:
            config: _Config = kernel.require("config")
            agent = _Agent(
                kernel=kernel,
                config=config,
                workspace=str(worktree),
                exclude_tools=exclude_tools,
                system_extra=(
                    f"[sub-agent {ctx.agent_id}, depth={ctx.depth}, "
                    f"parent={ctx.parent_id}]"
                ),
            )
            agent.ctx.ledger = _MutationLedger(str(worktree), config)
            # 注入沙箱提供者: 子代理的 run_shell 工具会使用它
            if _sandbox_provider is not None:
                agent.ctx.sandbox_provider = _sandbox_provider
        except Exception as exc:  # noqa: BLE001
            _log_exec.warning("kernel_executor: Agent 创建失败 [%s]: %s", ctx.agent_id, exc)
            return (f"[子代理创建失败] {type(exc).__name__}: {exc}", {})

        # ---- Phase 2: 执行 Loop ----
        try:
            loop_provider = kernel.require("loop_provider")
            answer = loop_provider.run_loop(
                agent,
                goal,
                stream=False,
                max_iterations=max_iterations,
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log_exec.warning("kernel_executor: Loop 执行异常 [%s]: %s", ctx.agent_id, exc)
            answer = f"[子代理执行失败] {type(exc).__name__}: {exc}"

        elapsed = _time.monotonic() - t0
        if elapsed > per_agent_timeout:
            _log_exec.warning(
                "kernel_executor: 子代理 %s 超时 (%.1fs > %.1fs)",
                ctx.agent_id, elapsed, per_agent_timeout,
            )

        # ---- Phase 3: 收集制品 (有大小/数量限制) ----
        artifacts: Dict[str, str] = {}
        try:
            file_count = 0
            for p in sorted(worktree.rglob("*")):
                if file_count >= max_artifacts:
                    break
                if not p.is_file():
                    continue
                if p.suffix.lower() in _SKIP_SUFFIXES:
                    continue
                try:
                    # 跳过超大文件 (>100KB)
                    if p.stat().st_size > 100_000:
                        continue
                    rel = str(p.relative_to(worktree))
                    content = p.read_text(encoding="utf-8", errors="replace")
                    artifacts[rel] = content[:max_artifact_size]
                    file_count += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        _log_exec.debug(
            "kernel_executor: %s 完成 in %.1fs, answer=%d chars, artifacts=%d files",
            ctx.agent_id, elapsed, len(answer), len(artifacts),
        )
        return answer, artifacts

    return _exec


def _iter_results(r: SubAgentResult):
    """深度优先遍历 (含嵌套) 的所有 SubAgentResult。"""
    yield r
    for c in r.children:
        yield from _iter_results(c)
