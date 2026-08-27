"""会话检查点工具插件: save / restore / list (对标 Claude Code 的 rewind)。

- save: 只记轻量元数据 (对话长度 + 账本标记), 不复制任何内容。
- restore: 回滚该检查点之后的全部文件变更 (账本快照逆向应用),
  并把对话流截回当时的边界; 截断点自动回退到协议合法位置
  (结尾消息不能悬着未回应的 tool_calls)。
- 安全: 整个工具标记 dangerous=True (restore 是写操作), 需用户确认;
  时间线被重写后, 该检查点及其后的检查点一并丢弃。
"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop


def _checkpoints(ctx: ToolContext) -> list:
    """惰性初始化检查点栈 (挂在 ctx 上, 会话生命周期内有效)。"""
    if ctx.checkpoints is None:
        ctx.checkpoints = []
    return ctx.checkpoints


def checkpoint_save(ctx: ToolContext, note: str = "") -> str:
    ledger = getattr(ctx, "ledger", None)
    mark = ledger.mark() if ledger is not None else -1
    msgs = ctx.conversation or []
    cps = _checkpoints(ctx)
    cp_id = f"cp{len(cps) + 1}"
    cps.append({"id": cp_id, "msg_len": len(msgs), "mark": mark, "note": note})
    detail = f"对话 {len(msgs)} 条"
    if mark >= 0:
        detail += f", 账本标记 {mark}"
    if note:
        detail += f", 备注: {note}"
    return f"检查点 {cp_id} 已保存 ({detail})"


def checkpoint_list(ctx: ToolContext) -> str:
    cps = ctx.checkpoints or []
    if not cps:
        return "没有已保存的检查点。"
    lines = []
    for c in cps:
        note = f" · {c['note']}" if c.get("note") else ""
        lines.append(f"- {c['id']}: 对话 {c['msg_len']} 条{note}")
    return "\n".join(lines)


def checkpoint_restore(ctx: ToolContext, checkpoint_id: str = "") -> str:
    cps = _checkpoints(ctx)
    if not cps:
        return "没有可恢复的检查点。"
    if checkpoint_id:
        idx = next((i for i, c in enumerate(cps) if c["id"] == checkpoint_id), -1)
        if idx < 0:
            ids = ", ".join(c["id"] for c in cps)
            return f"未找到检查点 {checkpoint_id} (可用: {ids})"
    else:
        idx = len(cps) - 1
    cp = cps[idx]
    parts: list = []

    # 1) 文件系统: 回滚该检查点之后记录的全部变更
    ledger = getattr(ctx, "ledger", None)
    if ledger is not None and cp.get("mark", -1) >= 0:
        undone = ledger.undo_since(cp["mark"])
        if undone:
            parts.append(f"文件回滚 {len(undone)} 项")

    # 2) 对话流: 截回当时长度 (自动回退到协议合法边界)
    msgs = ctx.conversation
    if msgs is not None and len(msgs) > cp["msg_len"]:
        j = max(2, min(cp["msg_len"], len(msgs)))
        while j > 2 and msgs[j - 1].get("tool_calls"):
            j -= 1  # 结尾不能悬着未回应的 tool_calls
        del msgs[j:]
        parts.append(f"对话截回 {len(msgs)} 条")

    # 3) 该检查点及其后的检查点一并丢弃 (时间线已重写)
    del cps[idx:]

    if not parts:
        return f"检查点 {cp['id']} 无需变更 (已是该状态)。"
    return f"已恢复到检查点 {cp['id']}: " + "; ".join(parts)


def checkpoint_tool(ctx: ToolContext, action: str = "list",
                    checkpoint_id: str = "", note: str = "") -> str:
    """checkpoint 工具统一入口 (save / restore / list)。"""
    if action == "save":
        return checkpoint_save(ctx, note)
    if action == "restore":
        return checkpoint_restore(ctx, checkpoint_id)
    return checkpoint_list(ctx)


class CheckpointPlugin(Plugin):
    name = "tools.checkpoint"
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="checkpoint",
            description="会话检查点: save 记录当前状态; restore 回滚之后的文件变更并把对话截回当时"
                        " (需用户确认); list 查看已有检查点。在执行大批量修改前先 save, 出错可整体回退。",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["save", "restore", "list"],
                               "description": "操作类型"},
                    "checkpoint_id": string_prop("要恢复到的检查点 id (仅 restore; 留空=最近一个)"),
                    "note": string_prop("备注 (仅 save)"),
                },
                "required": ["action"],
            },
            handler=checkpoint_tool, group="session", dangerous=True,
        ))
