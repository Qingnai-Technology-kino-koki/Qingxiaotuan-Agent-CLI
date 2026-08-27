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
        hooks = getattr(ctx, "hooks", None)  # 用户级 Hooks (无则跳过, 不影响正常分发)
        # Plan 模式: 只读放行, 修改类工具一律拦截 (对标 Claude Code 的 Plan Mode)
        if getattr(ctx, "plan_mode", False) and not tool.read_only:
            return ToolResult(
                status="denied",
                content=f"[Plan 模式] 只读模式已启用, 已阻止修改操作 {name}。"
                        "请先输出分析与实施计划, 退出 Plan 模式后再执行修改。",
                tool_name=name,
            )
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
        # 事务化影响半径: 计算写类工具的目标与事前预览 (执行前, 只读)
        ledger = getattr(ctx, "ledger", None)
        _targets = None
        _impact = None
        extractor = _MUTATION_EXTRACTORS.get(name)  # 提到外层: PreToolUse 改写参数后需重算
        if (ledger is not None and not getattr(ctx, "plan_mode", False)
                and not tool.read_only and extractor is not None):
            try:
                _targets = extractor(args, ctx)
                if _targets and _cfg_bool(cfg, "ledger.impact_preview", True):
                    _impact = _build_impact_preview(name, args, ctx, _targets)
            except Exception:
                _targets = None
        # 只读工具先查缓存 (危险工具始终跳过)
        if self.cache is not None and not tool.dangerous:
            cached = self.cache.get(name, arguments_json, dangerous=tool.dangerous)
            if cached is not None:
                self._emit_exec(ctx, name, "ok", cached=True)
                return ToolResult(status="ok", content=cached, tool_name=name, cached=True)
        if tool.dangerous:
            # 自动批准条件: YOLO 模式, 或用户权限规则表显式 allow (permissions.rules)
            if decision.action == "allow" and (getattr(ctx, "yolo", False) or decision.explicit):
                if ctx.on_auto_approve:
                    ctx.on_auto_approve(name)
            else:
                prompt = f"工具 {name} 请求执行: {json.dumps(args, ensure_ascii=False)[:300]}"
                if _impact:
                    prompt += f"\n📐 影响半径预览:\n{_impact}"
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
        # 用户级 Hooks: PreToolUse (可阻断 / 可改写参数, 受全局 allow_blocking/allow_edit_args 限制)
        if hooks is not None:
            try:
                _hd = hooks.run_pre(name, args, dangerous=tool.dangerous, impact=_impact)
            except Exception:
                _hd = None
            if _hd is not None and getattr(_hd, "block", False) and not getattr(ctx, "plan_mode", False):
                self._emit_exec(ctx, name, "denied")
                return ToolResult(status="denied",
                                  content=f"[Hook 阻断] {_hd.reason}", tool_name=name)
            if _hd is not None and getattr(_hd, "args", None) is not None:
                args = _hd.args
                arguments_json = json.dumps(args, ensure_ascii=False, sort_keys=True)
                # 参数被改写: 同步重算影响半径与快照目标, 保持事务一致性
                if ledger is not None and extractor is not None and not getattr(ctx, "plan_mode", False):
                    try:
                        _targets = extractor(args, ctx)
                        _impact = _build_impact_preview(name, args, ctx, _targets) if _targets else None
                    except Exception:
                        _targets = None
        started = time.monotonic()
        _snaps = None
        if ledger is not None and _targets is not None and _cfg_bool(cfg, "ledger.enabled", True):
            try:
                _snaps = ledger.snapshot(_targets)
            except Exception:
                _snaps = None
        try:
            result = tool.handler(ctx, **args)
            out = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            elapsed = time.monotonic() - started
            if ledger is not None and _snaps is not None:
                try:
                    ledger.record(name, _targets, _snaps, summary=_ledger_summary(name, args, _targets or []))
                except Exception:
                    pass
            if self.cache is not None:
                if tool.dangerous:
                    self.cache.clear()
                else:
                    self.cache.put(name, arguments_json, out, dangerous=tool.dangerous)
            self._emit_exec(ctx, name, "ok", elapsed=elapsed)
            # 用户级 Hooks: PostToolUse (只读审计, 异常隔离)
            if hooks is not None and not getattr(ctx, "plan_mode", False):
                try:
                    hooks.run_post(name, args, ToolResult(
                        status="ok", content=out, tool_name=name, elapsed=elapsed))
                except Exception:
                    pass
            return ToolResult(status="ok", content=out, tool_name=name, elapsed=elapsed)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            # 事务保证: 工具异常时自动从快照恢复, 文件系统不留半成品
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
