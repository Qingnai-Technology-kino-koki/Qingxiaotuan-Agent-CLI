"""工具基类与注册表 —— Hermes 风格: 每个工具模块自我注册, 内核按需发现。

增强 (对标 Claude Code 2.1.214):
- ToolResult 结构化返回: dispatch 返回 ToolResult 而非纯字符串,
  调用方可程序化判断成功/失败/拒绝, 而非靠文本前缀猜测。
- 进度心跳: 长时间运行的工具调用期间, 通过 on_progress 回调定期报告状态,
  对标 Claude Code 的 Periodic Progress Heartbeat。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from .cache import ToolResultCache
from .permissions import PermissionPolicy


@dataclass
class ToolResult:
    """结构化工具执行结果 (对标 Claude Code 的结构化工具输出)。

    status: "ok" | "error" | "denied" | "cached" | "timeout"
    让 Agent 主循环可以程序化判断, 不再依赖字符串前缀。
    """
    status: str  # ok | error | denied | cached | timeout
    content: str  # 工具输出文本
    tool_name: str = ""
    elapsed: float = 0.0  # 执行耗时 (秒)
    error_type: Optional[str] = None  # 异常类型名 (仅 error 状态)
    cached: bool = False  # 是否命中缓存

    def __str__(self) -> str:
        return self.content

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"status": self.status, "content": self.content}
        if self.elapsed > 0:
            d["elapsed"] = round(self.elapsed, 3)
        if self.error_type:
            d["error_type"] = self.error_type
        if self.cached:
            d["cached"] = True
        return d


@dataclass
class ToolContext:
    """工具执行上下文: 通过内核拿到其他插件的服务 (记忆、技能、配置……)。"""
    kernel: Any
    workspace: str
    confirm: Optional[Callable[[str], bool]] = None  # 危险操作确认回调
    yolo: bool = False                                # YOLO 模式: 危险工具默认自动批准
    on_auto_approve: Optional[Callable[[str], None]] = None  # YOLO 下危险工具自动批准时的通知钩子
    on_progress: Optional[Callable[[str, str], None]] = None  # 进度心跳回调 (tool_name, message)
    permissions: Optional[PermissionPolicy] = None
    safety_advice: Optional[str] = None                      # 执行前安全护栏给出的风险提示 (供确认环节展示)
    ui: Optional[Any] = None                                 # 可选 UI 句柄 (用于吉祥物状态切换等)

    def config(self, dotted: str, default: Any = None) -> Any:
        cfg = self.kernel.get("config")
        return cfg.get(dotted, default) if cfg else default

    def heartbeat(self, tool_name: str, message: str) -> None:
        """进度心跳: 长时间运行的工具调用期间定期调用, 报告进度。"""
        if self.on_progress:
            try:
                self.on_progress(tool_name, message)
            except Exception:
                pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]                     # JSON Schema
    handler: Callable[..., str]
    dangerous: bool = False                        # 需要用户确认
    yolo_confirm: bool = False                     # YOLO 模式下仍强制确认 (最后红线)
    group: str = "general"
    long_running: bool = False                     # 长时间运行的工具 (需要进度心跳)

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, cache: Optional[ToolResultCache] = None) -> None:
        self._tools: Dict[str, Tool] = {}
        self.cache = cache

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    @property
    def tools(self) -> List[Tool]:
        return list(self._tools.values())

    def dispatch(self, name: str, arguments_json: str, ctx: ToolContext) -> str:
        """执行工具并返回字符串结果。

        向后兼容: 返回 str (旧调用方不受影响)。
        新调用方可使用 dispatch_result() 获取结构化 ToolResult。
        """
        result = self.dispatch_result(name, arguments_json, ctx)
        return result.content

    def dispatch_result(self, name: str, arguments_json: str, ctx: ToolContext) -> ToolResult:
        """执行工具并返回结构化 ToolResult (对标 Claude Code)。"""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(status="error", content=f"[错误] 未知工具: {name}", tool_name=name)
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as exc:
            return ToolResult(status="error", content=f"[错误] 工具参数不是合法 JSON: {exc}", tool_name=name)
        cfg = ctx.kernel.get("config") if ctx.kernel is not None else None
        policy = ctx.permissions or PermissionPolicy(cfg)
        decision = policy.decide(tool, args, yolo=getattr(ctx, "yolo", False))
        if decision.action == "deny":
            return ToolResult(status="denied", content=f"[已拒绝] {decision.reason}", tool_name=name)
        # self-improve 实时闭环: 查询 learned 护栏规则 (复盘生成的经验)
        # 命中则升级为"必须人工确认" —— 把控制权留给人类, 不静默硬阻断。
        learned = self._query_learned(ctx, name, args)
        if learned is not None:
            tool = Tool(**{**tool.__dict__, "dangerous": True})
            ctx.safety_advice = f"[learned 护栏] {learned.get('message', '历史经验建议确认')}"
        # 只读工具先查缓存 (危险工具始终跳过)
        if self.cache is not None and not tool.dangerous:
            cached = self.cache.get(name, arguments_json, dangerous=tool.dangerous)
            if cached is not None:
                self._emit_exec(ctx, name, "ok", cached=True)
                return ToolResult(status="ok", content=cached, tool_name=name, cached=True)
        if tool.dangerous:
            # YOLO 模式: 若该工具不在红名单, 且 ctx 标注了 yolo 上下文, 则自动批准
            if decision.action == "allow" and getattr(ctx, "yolo", False):
                if ctx.on_auto_approve:
                    ctx.on_auto_approve(name)
            else:
                prompt = f"工具 {name} 请求执行: {json.dumps(args, ensure_ascii=False)[:300]}"
                if getattr(ctx, "safety_advice", None):
                    prompt += f"\n⚠️ 安全护栏提示: {ctx.safety_advice}"
                allowed = ctx.confirm(prompt) if ctx.confirm else False
                if not allowed:
                    self._emit_exec(ctx, name, "denied")
                    if ctx.ui is not None:
                        try:
                            ctx.ui.mascot_set("alert")
                        except Exception:
                            pass
                    reason = "用户未批准该操作。"
                    if getattr(ctx, "safety_advice", None):
                        reason += f" {ctx.safety_advice}"
                    return ToolResult(status="denied", content=f"[已拒绝] {reason}", tool_name=name)
        started = time.monotonic()
        try:
            result = tool.handler(ctx, **args)
            out = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            elapsed = time.monotonic() - started
            if self.cache is not None:
                if tool.dangerous:
                    self.cache.clear()
                else:
                    self.cache.put(name, arguments_json, out, dangerous=tool.dangerous)
            self._emit_exec(ctx, name, "ok", elapsed=elapsed)
            return ToolResult(status="ok", content=out, tool_name=name, elapsed=elapsed)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            self._emit_exec(ctx, name, "error", elapsed=elapsed, error_type=type(exc).__name__)
            return ToolResult(
                status="error",
                content=f"[错误] 工具 {name} 执行失败: {type(exc).__name__}: {exc}",
                tool_name=name, elapsed=elapsed, error_type=type(exc).__name__,
            )

    @staticmethod
    def _query_learned(ctx: ToolContext, name: str, args: dict) -> Optional[dict]:
        """查询 self-improve learned 规则; 命中返回规则 dict, 否则 None。
        引擎不可用/未配置时安全返回 None (不影响正常分发)。"""
        try:
            kernel = ctx.kernel
            if kernel is None:
                return None
            store = kernel.get("self_improve_rules")
            if store is None:
                return None
            return store.query(name, args)
        except Exception:  # noqa: BLE001
            return None

    def _emit_exec(self, ctx: ToolContext, name: str, status: str, elapsed: float = 0.0,
                   error_type: Optional[str] = None, cached: bool = False) -> None:
        """每个工具执行后向内核 emit tool.executed 事件, 供 self-improve 复盘订阅。"""
        try:
            kernel = ctx.kernel
            if kernel is None:
                return
            payload = {"name": name, "status": status, "elapsed": round(elapsed, 3), "cached": cached}
            if error_type:
                payload["error_type"] = error_type
            kernel.emit("tool.executed", payload)
        except Exception:
            pass


def string_prop(desc: str) -> Dict[str, str]:
    return {"type": "string", "description": desc}
