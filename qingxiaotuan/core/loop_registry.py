"""LoopRegistry —— [已弃用] 事件管线已合并到 arch/execution.py。

此模块保留仅为向后兼容; 新代码请使用:
  - arch.execution.LoopRegistry / SemanticBus / ToolPipeline (5 种事件语义 + waterfall)
  - core.loop_provider.LoopProvider / ReActLoop / PlannerExecuteLoop (Agent Loop 策略)
  - core.loop_plugin.LoopPlugin (Kernel 注册)

事件管线 (EventPipeline) 的功能已由 arch/execution.py 的 SemanticBus + ToolPipeline
统一替代, 后者支持更丰富的 5 种语义 (EMIT/OBSERVE/DECIDE/REFLECT/TERMINATE)
加中间件链、handler 异常隔离、错误积累上限。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .loop_provider import LoopProvider

log = logging.getLogger(__name__)


# ================================================================ Event Pipeline

@dataclass
class PipelineStage:
    """瀑布管线的一个阶段。"""
    name: str
    handler: Callable[..., Any]
    order: int = 0
    enabled: bool = True


class EventPipeline:
    """事件管线 —— 支持 5 种事件语义的执行管线。

    工具执行的 waterfall 管线: pre-execute → execute → post-execute。
    安全策略注册为不可变 guard, 无法被重排。
    """

    def __init__(self):
        # waterfall 管线 (pre→exec→post)
        self._pre_execute: List[PipelineStage] = []
        self._execute: Optional[PipelineStage] = None
        self._post_execute: List[PipelineStage] = []

        # bail 条件 (遇到则中止)
        self._bail_guards: List[Callable[[Dict[str, Any]], bool]] = []

        # serial 事件处理器
        self._serial_handlers: List[Callable[..., Any]] = []

        # parallel 事件处理器
        self._parallel_handlers: List[Callable[..., Any]] = []

    def register_pre_execute(self, name: str, handler: Callable, order: int = 0):
        """注册 pre-execute 阶段 (安全策略在此注册, 不可重排)。"""
        stage = PipelineStage(name=name, handler=handler, order=order)
        self._pre_execute.append(stage)
        self._pre_execute.sort(key=lambda s: s.order)

    def register_execute(self, handler: Callable):
        """注册 execute 阶段 (实际执行)。"""
        self._execute = PipelineStage(name="execute", handler=handler, order=100)

    def register_post_execute(self, name: str, handler: Callable, order: int = 200):
        """注册 post-execute 阶段 (审计/日志/清理)。"""
        stage = PipelineStage(name=name, handler=handler, order=order)
        self._post_execute.append(stage)
        self._post_execute.sort(key=lambda s: s.order)

    def register_bail_guard(self, guard: Callable[[Dict[str, Any]], bool]):
        """注册 bail guard (返回 True 则中止当前工具执行)。"""
        self._bail_guards.append(guard)

    def register_serial_handler(self, handler: Callable[..., Any]):
        """注册串行事件处理器。"""
        self._serial_handlers.append(handler)

    def register_parallel_handler(self, handler: Callable[..., Any]):
        """注册并行事件处理器。"""
        self._parallel_handlers.append(handler)

    def execute_waterfall(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行 waterfall 管线 (pre→exec→post)。

        Args:
            context: 工具执行上下文 {name, args, workspace, ...}

        Returns:
            管线执行结果 {ok, result, stages_completed, ...}
        """
        stages_completed = []
        ctx = dict(context)

        # Bail guard 检查
        for guard in self._bail_guards:
            if guard(ctx):
                return {
                    "ok": False, "result": "bail",
                    "error": "bail guard triggered",
                    "stages_completed": stages_completed,
                }

        # Pre-execute 阶段
        for stage in self._pre_execute:
            if not stage.enabled:
                continue
            try:
                result = stage.handler(ctx)
                if isinstance(result, dict):
                    ctx.update(result)
                stages_completed.append(stage.name)
                # 如果 pre-execute 返回 deny, 中止管线
                if isinstance(result, dict) and result.get("action") == "deny":
                    return {
                        "ok": False, "result": "denied",
                        "error": f"pre-execute guard '{stage.name}' denied",
                        "stages_completed": stages_completed,
                    }
            except Exception as exc:
                log.warning("pre-execute stage '%s' failed: %s", stage.name, exc)
                return {
                    "ok": False, "result": "error",
                    "error": f"pre-execute '{stage.name}': {exc}",
                    "stages_completed": stages_completed,
                }

        # Execute 阶段
        if self._execute is not None:
            try:
                result = self._execute.handler(ctx)
                if isinstance(result, dict):
                    ctx.update(result)
                stages_completed.append("execute")
            except Exception as exc:
                log.warning("execute stage failed: %s", exc)
                return {
                    "ok": False, "result": "error",
                    "error": f"execute: {exc}",
                    "stages_completed": stages_completed,
                }

        # Post-execute 阶段
        for stage in self._post_execute:
            if not stage.enabled:
                continue
            try:
                result = stage.handler(ctx)
                if isinstance(result, dict):
                    ctx.update(result)
                stages_completed.append(stage.name)
            except Exception as exc:
                log.warning("post-execute stage '%s' failed: %s", stage.name, exc)
                # post-execute 失败不阻塞管线

        return {
            "ok": True,
            "result": ctx.get("result", ""),
            "stages_completed": stages_completed,
        }

    def execute_parallel(self, tasks: List[Callable[..., Any]]) -> List[Any]:
        """执行并行事件组。"""
        import concurrent.futures
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as pool:
            futures = {pool.submit(task): i for i, task in enumerate(tasks)}
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    results.append((idx, future.result()))
                except Exception as exc:
                    results.append((idx, {"error": str(exc)}))
        return [r for _, r in sorted(results)]

    def execute_serial(self, events: List[Dict[str, Any]]) -> List[Any]:
        """执行串行事件序列 (严格顺序)。"""
        results = []
        for event in events:
            for handler in self._serial_handlers:
                try:
                    result = handler(event)
                    results.append(result)
                except Exception as exc:
                    results.append({"error": str(exc)})
                    break
        return results


# ================================================================ LoopRegistry

class LoopRegistry:
    """[已弃用] Agent Loop 注册表。

    .. deprecated:: 0.2.015
        此类已弃用, 请使用 core.loop_provider.LoopRegistry。
        EventPipeline 功能已合并到 arch/execution.py 的 SemanticBus + ToolPipeline。
    """

    def __init__(self):
        import warnings
        warnings.warn(
            "core.loop_registry.LoopRegistry 已弃用, 请使用 core.loop_provider.LoopRegistry",
            DeprecationWarning, stacklevel=2,
        )
        self._loops: Dict[str, "LoopProvider"] = {}
        self._pipeline = EventPipeline()
        self._current_name: str = ""

    def register(self, name: str, loop: "LoopProvider"):
        """注册一个 LoopProvider。"""
        self._loops[name] = loop
        log.debug("Loop registered: %s (%s)", name, loop.description)

    def unregister(self, name: str) -> bool:
        """注销一个 LoopProvider。"""
        if name in self._loops:
            del self._loops[name]
            if self._current_name == name:
                self._current_name = ""
            return True
        return False

    def get(self, name: str) -> Optional["LoopProvider"]:
        """按名称获取 LoopProvider。"""
        return self._loops.get(name)

    def select(self, config=None) -> "LoopProvider":
        """根据配置选择 LoopProvider。

        优先级: config.loop.provider > config.router.plan_execute > 默认 react
        """
        if config is not None:
            # 显式指定
            provider_name = config.get("loop.provider", "")
            if provider_name and provider_name in self._loops:
                self._current_name = provider_name
                return self._loops[provider_name]

            # Plan/Execute 模式
            if config.get("router.plan_execute", False):
                if "plan_execute" in self._loops:
                    self._current_name = "plan_execute"
                    return self._loops["plan_execute"]

            # DevLoop
            if config.get("loop.devloop", False):
                if "devloop" in self._loops:
                    self._current_name = "devloop"
                    return self._loops["devloop"]

        # 默认 react
        if "react" in self._loops:
            self._current_name = "react"
            return self._loops["react"]

        # fallback: 任意一个
        if self._loops:
            name = next(iter(self._loops))
            self._current_name = name
            return self._loops[name]

        raise RuntimeError("没有注册任何 LoopProvider")

    @property
    def current_name(self) -> str:
        return self._current_name

    @property
    def pipeline(self) -> EventPipeline:
        """获取事件管线。"""
        return self._pipeline

    def list_available(self) -> List[Dict[str, str]]:
        """列出所有可用 loop。"""
        return [
            {"name": name, "description": loop.description}
            for name, loop in self._loops.items()
        ]

    def healthcheck(self) -> Dict[str, Any]:
        """健康检查: 每个注册 loop 的状态。"""
        return {
            "total": len(self._loops),
            "current": self._current_name,
            "available": list(self._loops.keys()),
        }
