"""Agent 事件协议 —— 统一 CLI/TUI 与 ACP(IDE) 的事件词汇。

设计来源 (借鉴而非照搬):
- kimi-code 的 ACP (Agent Client Protocol) session 事件词汇:
  session/update(initialized) · task/update(running|completed|failed) ·
  agent_message_chunk · tool_call · tool_call_update · permission_request ·
  available_commands_update
- 青小团既有的 SessionStore 事件 (user/assistant/tool_call/tool_result/usage)
- DeepSeek Harness 的「Trajectory 是一等公民、可导出、可压缩」理念

目标: 让 CLI 会话、TUI 会话、IDE(ACP) 会话都落进**同一套事件流**,
从而任意会话都能被 Trajectory 重建、被 replay 回放、被 /stats 复盘。

注意: 本模块只定义「词汇表 + 归一类」, 不负责落盘; 落盘仍由 SessionStore 承担。
"""

from __future__ import annotations

from typing import Any, Dict

# ------------------------------------------------------------------ 规范事件名
# 规范名 (canonical) 采用 kimi ACP 风格的点分命名, 作为 Trajectory/Replay 的唯一依据。

class AgentEvent(str):
    """事件类型名 (str 子类, 可直接当字符串用, 也支持 == 比较)。"""

    # 会话生命周期
    SESSION_UPDATE = "session.update"          # 会话状态变更 (initialized/done/compact)
    TASK_UPDATE = "task.update"                # 一轮任务的 running/completed/failed
    AVAILABLE_COMMANDS = "available_commands_update"

    # 消息 / 流式
    USER = "user"                              # 用户输入 (既有)
    ASSISTANT = "assistant"                    # 助手完整回复 (既有)
    AGENT_MESSAGE_CHUNK = "agent_message_chunk"  # 流式增量 (kimi 词汇)

    # 工具
    TOOL_CALL = "tool_call"                    # 工具调用开始 (name/arguments/toolCallId)
    TOOL_CALL_UPDATE = "tool_call_update"      # 工具结果/状态 (status/result)
    TOOL_RESULT = "tool_result"                # 既有别名 (与 TOOL_CALL_UPDATE 等价)

    # 权限 (ACP 握手, 主要走 IDE; CLI 预留)
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"

    # 验证 / 目标 / 预算 (青小团自有语义, 保留)
    VERIFY_PASSED = "verify.passed"
    VERIFY_FAILED = "verify.failed"
    GOAL_CONTINUE = "goal.continue"
    GOAL_ACHIEVED = "goal.achieved"
    BUDGET_EXCEEDED = "budget.exceeded"

    # 用量
    USAGE = "usage"


# 既有事件名 -> 规范名 的别名映射 (兼容历史 SessionStore 文件)
_LEGACY_ALIASES = {
    "tool_result": AgentEvent.TOOL_CALL_UPDATE,
    "usage": AgentEvent.USAGE,
}


def canonicalize(event_type: str) -> str:
    """把任意事件类型规整为规范名 (未知类型原样返回, 便于向前兼容)。"""
    if event_type in _LEGACY_ALIASES:
        return _LEGACY_ALIASES[event_type]
    return event_type


def normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """把一条原始事件记录规整为规范形态 (不改原 dict, 返回新 dict)。

    处理:
    - type 字段归一到 canonical
    - 把历史 tool_result 的 {name,result,status} 折成 tool_call_update 的字段
    """
    out = dict(record)
    et = str(out.get("type", ""))
    canon = canonicalize(et)
    if canon != et:
        out["type"] = canon
    # 历史 tool_result 可能用 result/status 平铺, 规整为 tool_call_update 语义
    if canon == AgentEvent.TOOL_CALL_UPDATE and "name" in out and "toolCallId" not in out:
        out.setdefault("toolCallId", out.get("tool_call_id", ""))
    return out


# ------------------------------------------------------------------ 工具构造器
def session_update(status: str, **extra: Any) -> Dict[str, Any]:
    return {"type": AgentEvent.SESSION_UPDATE, "status": status, **extra}


def task_update(status: str, **extra: Any) -> Dict[str, Any]:
    return {"type": AgentEvent.TASK_UPDATE, "status": status, **extra}


def tool_call(name: str, arguments: str = "", tool_call_id: str = "") -> Dict[str, Any]:
    return {
        "type": AgentEvent.TOOL_CALL,
        "name": name,
        "arguments": arguments,
        "toolCallId": tool_call_id,
    }


def tool_call_update(tool_call_id: str, status: str = "completed",
                    result: str = "", name: str = "") -> Dict[str, Any]:
    return {
        "type": AgentEvent.TOOL_CALL_UPDATE,
        "toolCallId": tool_call_id,
        "status": status,
        "result": result,
        "name": name,
    }


def agent_message_chunk(text: str) -> Dict[str, Any]:
    return {"type": AgentEvent.AGENT_MESSAGE_CHUNK, "text": text}
