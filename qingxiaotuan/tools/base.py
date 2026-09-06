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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union, cast

from .cache import ToolResultCache
from .permissions import PermissionPolicy
from .permission_fusion import build_permission_policy

if TYPE_CHECKING:  # 避免循环导入: ToolResult 仅做类型标注
    from ..vision import ImageRef


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
    images: Optional[List["ImageRef"]] = None  # 工具返回的图片 (多模态); 仅视觉模型可见

    def __str__(self) -> str:
        if self.images:
            n = len(self.images)
            return self.content + f"\n[附带 {n} 张图片, 供支持视觉的模型查看]"
        return self.content

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"status": self.status, "content": self.content}
        if self.elapsed > 0:
            d["elapsed"] = round(self.elapsed, 3)
        if self.error_type:
            d["error_type"] = self.error_type
        if self.cached:
            d["cached"] = True
        if self.images:
            d["image_count"] = len(self.images)
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
    safety_severity: Optional[str] = None                    # 风险级别 (语言无关): critical/high/medium/none, 供逻辑判定与测试, 不随 UI 本地化变化
    ui: Optional[Any] = None                                 # 可选 UI 句柄 (用于吉祥物状态切换等)
    ledger: Optional[Any] = None                             # 事务化操作账本 (MutationLedger), 支持精细回滚
    hooks: Optional[Any] = None                              # 用户级 Hooks 管理器 (HookManager), 支持 Pre/Post 编排
    plan_mode: bool = False                                  # Plan 模式: 只读, 禁止修改类工具
    conversation: Optional[List[Dict[str, Any]]] = None      # 主对话消息列表 (与 Agent.messages 同一对象, checkpoint 截断用)
    checkpoints: Optional[List[Dict[str, Any]]] = None       # 会话内检查点栈 (checkpoint 工具维护)
    # 后台任务 Runner (task 工具懒缓存): submit 与 background_status 必须共用同一实例才能查到 job
    background_runner: Optional[Any] = None
    # 会话任务清单 (todo_write/todo_read 维护): [{content, status}], 会话内共享
    todos: Optional[List[Dict[str, Any]]] = None
    # 宿主 Agent 的反向引用 (Agent.__init__ 注入): plan-mode 等工具需要同步 agent.plan_mode
    agent: Optional[Any] = None

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
    read_only: bool = False                        # 只读工具 (Plan 模式放行, 不修改任何状态)

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ================================================================ 工具集 (Tool Sets)
# 基于"当前已注册工具"按模式派生的可切换子集 —— 实现 Plan / YOLO / Standard 各自一套工具。
# 关键: predicate 在运行时基于已注册工具计算, 新增/移除工具会自动反映到对应工具集, 无需手工维护名单。

class ToolSet:
    """一个具名、可切换的工具子集。"""

    def __init__(self, name: str, predicate: Callable[["Tool"], bool]) -> None:
        self.name = name
        self._predicate = predicate

    def select(self, tools: "List[Tool]") -> "List[Tool]":
        return [t for t in tools if self._predicate(t)]


def _ts_all(_t: "Tool") -> bool:
    return True


def _ts_read_only(t: "Tool") -> bool:
    return bool(t.read_only)


# standard: 全量 (Standard 模式, 危险操作由权限闸门负责确认)
# yolo:     全量 (YOLO 模式, 危险操作自动批准, 工具集本身与 standard 一致)
# plan:     仅只读子集 (Plan 模式, 模型只能观察、不能修改)
TOOL_SETS: Dict[str, ToolSet] = {
    "standard": ToolSet("standard", _ts_all),
    "yolo": ToolSet("yolo", _ts_all),
    "plan": ToolSet("plan", _ts_read_only),
}


def resolve_tool_set(name: Optional[str]) -> ToolSet:
    if not name:
        return TOOL_SETS["standard"]
    return TOOL_SETS.get(name, TOOL_SETS["standard"])


class ToolRegistry:
    def __init__(self, cache: Optional[ToolResultCache] = None) -> None:
        self._tools: Dict[str, Tool] = {}
        self.cache = cache
        # 可选的 MCP Tool Search 引擎: 上下文降耗与按需检索 (见 mcp_tool_search.py)。
        # 非 None 时, schemas() 会在 MCP 工具 schema 侵占上下文过阈值时自动降耗。
        self.tool_search_engine: Optional[Any] = None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(
        self,
        *,
        tool_set: Optional[str] = None,
        exclude_tools: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """返回传给模型的工具清单 (OpenAI function 声明)。

        tool_set:
          - "plan"     仅暴露只读工具 (模型只能观察, 不能修改)
          - "yolo"     全量工具 (危险操作自动批准)
          - "standard" 全量工具 (默认; 危险操作由权限闸门确认)
        exclude_tools: 额外的名字黑名单, 叠加在工具集之上。
        """
        ts = resolve_tool_set(tool_set)
        tools = ts.select(list(self._tools.values()))
        if exclude_tools:
            excl = set(exclude_tools)
            tools = [t for t in tools if t.name not in excl]
        if self.tool_search_engine is not None:
            tools = self.tool_search_engine.reduce(tools)
        return [t.schema() for t in tools]

    def tools_for(
        self,
        *,
        tool_set: Optional[str] = None,
        exclude_tools: Optional[Any] = None,
    ) -> "List[Tool]":
        """同 schemas(), 但返回 Tool 对象 (供测试/调试)。"""
        ts = resolve_tool_set(tool_set)
        tools = ts.select(list(self._tools.values()))
        if exclude_tools:
            excl = set(exclude_tools)
            tools = [t for t in tools if t.name not in excl]
        return tools

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

    # ------------------------------------------------------------------ dispatch_result 拆分
    # 将原 ~145 行 God 函数拆分为 5 个聚焦的辅助方法, 保持相同语义:
    #   _pre_exec_check   → Plan 模式 + 权限策略 + learned 护栏 + 影响半径预览
    #   _try_cache_get    → 只读工具缓存命中
    #   _approve_dangerous → 危险工具审批 (YOLO 自动批准 / 人工确认)
    #   _apply_pre_hooks  → 用户级 PreToolUse hooks (阻断 / 参数改写)
    #   _exec_and_record  → 执行 + 账本快照 + 缓存写入 + PostToolUse hooks + 错误回滚

    def _pre_exec_check(
        self, tool: Tool, name: str, args: dict, ctx: ToolContext,
    ) -> tuple:
        """执行前校验: Plan 模式拦截、权限决策、learned 护栏、影响半径预览。

        Returns:
            (tool, args, decision, targets, impact) —— tool/args 可能被 learned 护栏升级。
            若返回 None 元组第一个元素为 ToolResult, 表示应立即拦截返回。
        """
        # Plan 模式: 只读放行, 修改类工具一律拦截
        if getattr(ctx, "plan_mode", False) and not tool.read_only:
            return (ToolResult(
                status="denied",
                content=f"[Plan 模式] 只读模式已启用, 已阻止修改操作 {name}。"
                        "请先输出分析与实施计划, 退出 Plan 模式后再执行修改。",
                tool_name=name,
            ),)
        cfg = ctx.kernel.get("config") if ctx.kernel is not None else None
        policy = ctx.permissions or build_permission_policy(cfg)
        decision = policy.decide(tool, args, yolo=getattr(ctx, "yolo", False))
        if decision.action == "deny":
            return (ToolResult(status="denied", content=f"[已拒绝] {decision.reason}", tool_name=name),)
        # self-improve 实时闭环: learned 护栏命中则升级为"必须人工确认"
        learned = self._query_learned(ctx, name, args)
        if learned is not None:
            tool = Tool(**{**tool.__dict__, "dangerous": True})
            ctx.safety_advice = f"[learned 护栏] {learned.get('message', '历史经验建议确认')}"
        # 事务化影响半径: 计算写类工具的目标与事前预览
        ledger = getattr(ctx, "ledger", None)
        targets = None
        impact = None
        extractor = _MUTATION_EXTRACTORS.get(name)
        if (ledger is not None and not getattr(ctx, "plan_mode", False)
                and not tool.read_only and extractor is not None):
            try:
                targets = extractor(args, ctx)
                if targets and _cfg_bool(cfg, "ledger.impact_preview", True):
                    impact = _build_impact_preview(name, args, ctx, targets)
            except Exception:
                targets = None
        return (tool, args, decision, targets, impact)

    def _try_cache_get(
        self, tool: Tool, name: str, arguments_json: str, ctx: ToolContext,
    ) -> Optional[ToolResult]:
        """只读工具缓存命中检查; 命中返回 ToolResult, 未命中返回 None。"""
        if self.cache is None or tool.dangerous:
            return None
        cached = self.cache.get(name, arguments_json, dangerous=tool.dangerous,
                                workspace=getattr(ctx, "workspace", "") or "")
        if cached is not None:
            self._emit_exec(ctx, name, "ok", cached=True)
            return ToolResult(status="ok", content=cached, tool_name=name, cached=True)
        return None

    def _approve_dangerous(
        self, tool: Tool, name: str, args: dict, decision: Any,
        ctx: ToolContext, impact: Optional[str],
    ) -> Optional[ToolResult]:
        """危险工具审批: YOLO 自动批准 / 人工确认。

        Returns:
            None 表示已批准可继续; ToolResult 表示被拒绝应立即返回。
        """
        if not tool.dangerous:
            return None
        has_learned = (
            getattr(ctx, "safety_advice", None)
            and "learned" in str(getattr(ctx, "safety_advice", ""))
        )
        if (decision.action == "allow"
                and (getattr(ctx, "yolo", False) or decision.explicit)
                and not has_learned):
            if ctx.on_auto_approve:
                ctx.on_auto_approve(name)
            return None
        # 需要人工确认
        prompt = f"工具 {name} 请求执行: {json.dumps(args, ensure_ascii=False)[:300]}"
        if impact:
            prompt += f"\n📐 影响半径预览:\n{impact}"
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
        return None

    def _apply_pre_hooks(
        self, name: str, args: dict, tool: Tool, ctx: ToolContext,
        impact: Optional[str], ledger: Any, extractor: Any, cfg: Any,
    ) -> tuple:
        """用户级 PreToolUse hooks: 可阻断 / 可改写参数。

        Returns:
            (args, arguments_json, targets, impact) —— 可能被 hooks 改写。
            若被阻断, 返回 (ToolResult,) 元组。
        """
        hooks = getattr(ctx, "hooks", None)
        if hooks is None:
            return (args, json.dumps(args, ensure_ascii=False, sort_keys=True), None, impact)
        try:
            _hd = hooks.run_pre(name, args, dangerous=tool.dangerous, impact=impact)
        except Exception:
            _hd = None
        if _hd is not None and getattr(_hd, "block", False) and not getattr(ctx, "plan_mode", False):
            self._emit_exec(ctx, name, "denied")
            return (ToolResult(status="denied",
                              content=f"[Hook 阻断] {_hd.reason}", tool_name=name),)
        targets = None
        if _hd is not None and getattr(_hd, "args", None) is not None:
            args = _hd.args
            arguments_json = json.dumps(args, ensure_ascii=False, sort_keys=True)
            # 参数被改写: 同步重算影响半径与快照目标
            if ledger is not None and extractor is not None and not getattr(ctx, "plan_mode", False):
                try:
                    targets = extractor(args, ctx)
                    impact = _build_impact_preview(name, args, ctx, targets) if targets else None
                except Exception:
                    targets = None
        else:
            arguments_json = json.dumps(args, ensure_ascii=False, sort_keys=True)
        return (args, arguments_json, targets, impact)

    def _exec_and_record(
        self, tool: Tool, name: str, args: dict, arguments_json: str,
        ctx: ToolContext, ledger: Any, targets: Optional[List[str]], cfg: Any,
    ) -> ToolResult:
        """执行工具并记录: 账本快照→执行→缓存→PostToolUse hooks→错误回滚。"""
        hooks = getattr(ctx, "hooks", None)
        started = time.monotonic()
        _snaps = None
        if ledger is not None and targets is not None and _cfg_bool(cfg, "ledger.enabled", True):
            try:
                _snaps = ledger.snapshot(targets)
            except Exception:
                _snaps = None
        try:
            result = tool.handler(ctx, **args)
            out = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            elapsed = time.monotonic() - started
            if ledger is not None and _snaps is not None:
                try:
                    ledger.record(name, targets, _snaps,
                                  summary=_ledger_summary(name, args, targets or []))
                except Exception:
                    pass
            if self.cache is not None:
                if tool.dangerous:
                    self.cache.clear()
                else:
                    self.cache.put(name, arguments_json, out, dangerous=tool.dangerous,
                                   workspace=getattr(ctx, "workspace", "") or "")
            self._emit_exec(ctx, name, "ok", elapsed=elapsed)
            # PostToolUse hooks (只读审计, 异常隔离)
            if hooks is not None and not getattr(ctx, "plan_mode", False):
                try:
                    hooks.run_post(name, args, ToolResult(
                        status="ok", content=out, tool_name=name, elapsed=elapsed))
                except Exception:
                    pass
            return ToolResult(status="ok", content=out, tool_name=name, elapsed=elapsed)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            # 事务保证: 工具异常时自动从快照恢复
            if ledger is not None and _snaps is not None and _cfg_bool(cfg, "ledger.auto_rollback_on_error", True):
                try:
                    ledger.restore(_snaps)
                except Exception:
                    pass
            self._emit_exec(ctx, name, "error", elapsed=elapsed, error_type=type(exc).__name__)
            return ToolResult(
                status="error",
                content=f"[错误] 工具 {name} 执行失败: {type(exc).__name__}: {exc}",
                tool_name=name, elapsed=elapsed, error_type=type(exc).__name__,
            )

    def dispatch_result(self, name: str, arguments_json: str, ctx: ToolContext) -> ToolResult:
        """执行工具并返回结构化 ToolResult (对标 Claude Code)。

        管线: 解析参数 → pre_exec_check → 缓存 → 危险审批 → PreToolUse hooks →
               exec_and_record → PostToolUse hooks。每个阶段可独立拦截返回。
        """
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(status="error", content=f"[错误] 未知工具: {name}", tool_name=name)
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as exc:
            return ToolResult(status="error", content=f"[错误] 工具参数不是合法 JSON: {exc}", tool_name=name)
        # Stage 1: Plan 模式 + 权限 + learned + 影响半径
        pre = self._pre_exec_check(tool, name, args, ctx)
        if len(pre) == 1:
            return pre[0]  # 拦截 (Plan 模式 / 权限拒绝)
        tool, args, decision, targets, impact = pre
        # Stage 2: 缓存命中
        cached = self._try_cache_get(tool, name, arguments_json, ctx)
        if cached is not None:
            return cached
        # Stage 3: 危险工具审批
        rejected = self._approve_dangerous(tool, name, args, decision, ctx, impact)
        if rejected is not None:
            return rejected
        # Stage 4: PreToolUse hooks (可改写参数 / 阻断)
        hook_result = self._apply_pre_hooks(name, args, tool, ctx, impact,
                                            getattr(ctx, 'ledger', None),
                                            _MUTATION_EXTRACTORS.get(name),
                                            ctx.kernel.get('config') if ctx.kernel else None)
        if len(hook_result) == 1:
            return hook_result[0]  # Hook 阻断
        args, arguments_json, hook_targets, impact = hook_result
        if hook_targets is not None:
            targets = hook_targets
        cfg = ctx.kernel.get('config') if ctx.kernel else None
        # Stage 5: 执行 + 记录
        return self._exec_and_record(tool, name, args, arguments_json, ctx,
                                     getattr(ctx, 'ledger', None), targets, cfg)

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
            return cast(Optional[dict], store.query(name, args))
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


# ------------------------------------------------------------------ 事务化影响半径 (ledger)

def _cfg_bool(cfg: Any, key: str, default: bool) -> bool:
    """读取布尔配置, 兼容 Config 对象与 dict。"""
    if cfg is None:
        return default
    try:
        v = cfg.get(key, default)
    except Exception:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _rel(ctx: ToolContext, path: str) -> str:
    """把路径规约为相对工作区的简短形式 (用于预览/摘要展示)。"""
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(ctx.workspace) / p
        p = p.resolve()
        return str(p.relative_to(Path(ctx.workspace).resolve()))
    except Exception:
        return path


# 写类工具 → 目标路径提取器。dispatch_result 在执行前据此快照, 实现精细回滚。
_MUTATION_EXTRACTORS: Dict[str, Callable[[dict, ToolContext], List[str]]] = {
    "write_file": lambda a, c: [a["path"]] if a.get("path") else [],
    "edit_file": lambda a, c: [a["path"]] if a.get("path") else [],
    "delete_file": lambda a, c: [a["path"]] if a.get("path") else [],
    "delete_dir": lambda a, c: [a["path"]] if a.get("path") else [],
    "move_file": lambda a, c: [x for x in (a.get("src"), a.get("dst")) if x],
    # run_shell: 命令本身作为「影响目标」呈现给用户 (可见化最小影响半径)。
    # 注意: 快照阶段对命令字符串做文件快照会失败并安全降级为 _snaps=None,
    # 即 shell 不被回滚 —— 这是正确行为 (任意命令无法安全回滚), 此处仅做事前可见化。
    "run_shell": lambda a, c: [a["command"]] if a.get("command") else [],
}


def _build_impact_preview(name: str, args: dict, ctx: ToolContext, targets: List[str]) -> str:
    """执行前的影响半径预览: 告诉用户这一步会动到哪里、改了什么。

    这是「最小影响半径」的事前可见化——主流 Agent 普遍只事后展示 diff,
    几乎不在确认前结构化呈现"将创建/覆盖/删除哪些文件、改了哪几行"。
    """
    lines: List[str] = []
    if name == "edit_file":
        path = args.get("path", "")
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        rel = _rel(ctx, path)
        try:
            ap = Path(ctx.workspace) / path
            exists = ap.exists()
        except Exception:
            exists = False
        lines.append(f"  ✎ 修改 {rel} ({'存在' if exists else '新建'})")
        if old and new:
            import difflib
            od = old.splitlines()
            nd = new.splitlines()
            diff = list(difflib.unified_diff(od, nd, lineterm="", n=1))
            shown = diff[:14]
            for d in shown:
                if d.startswith("+"):
                    lines.append(f"    [+] {d[1:][:80]}")
                elif d.startswith("-"):
                    lines.append(f"    [-] {d[1:][:80]}")
                elif d.startswith("@@"):
                    lines.append(f"    {d[:80]}")
            if len(diff) > len(shown):
                lines.append(f"    … 还有 {len(diff) - len(shown)} 行差异")
    elif name == "write_file":
        rel = _rel(ctx, args.get("path", ""))
        content = args.get("content", "")
        ap = Path(ctx.workspace) / args.get("path", "")
        try:
            exists = ap.exists()
        except Exception:
            exists = False
        lines.append(f"  ✎ {'覆盖' if exists else '新建'} {rel} ({len(content)} 字符)")
    elif name in ("delete_file", "delete_dir"):
        rel = _rel(ctx, args.get("path", ""))
        lines.append(f"  🗑 删除 {rel} ({'目录' if name == 'delete_dir' else '文件'})")
    elif name == "move_file":
        lines.append(f"  ➟ 移动 {_rel(ctx, args.get('src', ''))} → {_rel(ctx, args.get('dst', ''))}")
    elif name == "run_shell":
        cmd = args.get("command", "")
        lines.append(f"  ⚡ 执行命令 (不可回滚): {cmd[:160]}")
    else:
        for t in targets:
            lines.append(f"  • {_rel(ctx, t)}")
    return "\n".join(lines)


def _ledger_summary(name: str, args: dict, targets: List[str]) -> str:
    if name == "write_file":
        return f"write {len(args.get('content', ''))} 字符 → {_rel_to_ctx(args.get('path', ''), targets)}"
    if name == "edit_file":
        return f"edit {_rel_to_ctx(args.get('path', ''), targets)}"
    if name == "delete_file":
        return f"delete {_rel_to_ctx(args.get('path', ''), targets)}"
    if name == "delete_dir":
        return f"delete-dir {_rel_to_ctx(args.get('path', ''), targets)}"
    if name == "move_file":
        return f"move {args.get('src', '')} → {args.get('dst', '')}"
    return name


def _rel_to_ctx(rel: str, targets: List[str]) -> str:
    return rel or (targets[0] if targets else "")
