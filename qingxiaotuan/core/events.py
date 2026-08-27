"""内核事件类型定义 —— 所有 emit/on 的事件名与 payload 类型的单一来源。

用法::

    from qingxiaotuan.core.events import EventType, emit_event
    emit_event(kernel, EventType.TOOL_EXECUTED, {"name": "run_shell", "status": "ok"})

类型安全:
- EventType 枚举: 所有已知事件的唯一来源, 字符串值与历史行为一致。
- 为高频事件提供 payload 类型注释 (Dict[str, Any] + 文档注释), 防止拼写错误。
- Kernel.emit / Kernel.on 接受 EventType | str, 保持向后兼容;
  IDE 能自动补全已知事件名, 未知字符串也不报错 (方便扩展)。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Union

if TYPE_CHECKING:
    from .kernel import Kernel


class EventType(str, Enum):
    """内核事件名枚举 —— 所有已知事件的唯一来源。

    字符串值与历史行为完全一致 (向后兼容); 新增事件必须先在这里注册。
    """

    # ---- 插件生命周期 ----
    PLUGIN_REGISTERED = "plugin.registered"
    PLUGIN_ACTIVATED = "plugin.activated"
    PLUGIN_DEACTIVATED = "plugin.deactivated"
    PLUGIN_ERROR = "plugin.error"

    # ---- 服务 ----
    SERVICE_PROVIDED = "service.provided"

    # ---- 审计 ----
    AUDIT_READY = "audit.ready"

    # ---- 模型 ----
    MODEL_ROUTED = "model.routed"
    MODEL_SWITCHED = "model.switched"

    # ---- 工具 ----
    TOOL_EXECUTED = "tool.executed"

    # ---- 开发循环 (DevLoop) ----
    LOOP_ITERATION = "loop.iteration"
    LOOP_REFLECT = "loop.reflect"
    LOOP_REFLECT_ERROR = "loop.reflect_error"

    # ---- Hook ----
    HOOK_EXECUTED = "hook.executed"

    # ---- 内部运维 ----
    EVENT_OVERFLOW = "event.overflow"  # 事件历史截断时触发, payload: {"evicted": int, "remaining": int}


# ---- Payload 类型文档 (供 IDE/审查者参考) ----
#
# PluginPayload:        {"name": str, "version": str}       — plugin.* 事件
# ServicePayload:       {"service": str, "owner": str}      — service.provided
# AuditPayload:         {"enabled": bool, "persist": bool}  — audit.ready
# ModelSwitchedPayload: {"provider": str, "model": str}     — model.switched
# ToolExecutedPayload:  {"name": str, "status": str,        — tool.executed
#                        "elapsed": float, "cached": bool,
#                        "error_type": str}
# LoopIterationPayload: {"n": int, "report": str}           — loop.iteration
# LoopReflectPayload:   {"n": int, "decision": str,         — loop.reflect
#                        "failure_count": int, "diagnosis": str}
# HookExecutedPayload:  {"event": str, "status": str,       — hook.executed
#                        "detail": str, "hook": str}


# ---- 便捷 emit/on 函数 ----

def emit_event(
    kernel: "Kernel",
    event: Union[EventType, str],
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """类型安全的事件发射: 接受 EventType 枚举或字符串, IDE 可补全。"""
    kernel.emit(event, payload)


def on_event(
    kernel: "Kernel",
    event: Union[EventType, str],
    handler: Callable[[Dict[str, Any]], None],
) -> None:
    """类型安全的事件订阅: 接受 EventType 枚举或字符串, IDE 可补全。"""
    kernel.on(event, handler)
