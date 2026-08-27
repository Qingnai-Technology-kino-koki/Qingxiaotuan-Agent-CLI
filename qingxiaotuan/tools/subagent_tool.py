"""Subagent 工具: 隔离上下文的子代理 (对标 Claude Code 2.1 Subagent)。

Claude Code 的 subagent:
- 独立的上下文窗口, 不消耗主会话 token
- 可以读取大量文件但只返回摘要
- 有自己的工具集和系统提示
- 适合研究任务、并行工作、专业 worker

青小团 subagent:
- 复用微内核的工具注册表, 但拥有独立的 messages 列表
- 主会话只收到最终摘要, 不被子任务的中间输出污染
- 支持 skill 预加载 (通过 skills 参数指定)
- 支持可选的工具过滤 (exclude_tools)
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

from ..core.kernel import Kernel, Plugin
from ..tools.base import Tool, ToolContext, string_prop

log = logging.getLogger(__name__)

# Subagent 默认超时 (秒)
_DEFAULT_TIMEOUT = 180
# 子任务返回摘要的最大长度
_MAX_SUMMARY_LEN = 8000


def _run_subagent(
    ctx: ToolContext,
    task: str,
    instructions: str = "",
    skills: str = "",
    exclude_tools: str = "",
    timeout: int = 0,
) -> str:
    """在隔离上下文中执行子任务, 返回摘要。

    Args:
        task: 子任务描述
        instructions: 额外系统指令 (可选, 追加到子代理系统提示)
        skills: 逗号分隔的技能名列表 (可选, 子代理启动时预加载)
        exclude_tools: 逗号分隔的工具名列表 (可选, 从子代理工具集中排除)
        timeout: 超时秒数 (0=默认180s)
    """
    # 延迟导入避免循环: app.py -> tools -> core -> app.py
    from ..app import create_agent

    kernel = ctx.kernel
    config = kernel.require("config")
    workspace = ctx.workspace

    # 构建子代理的排除工具列表 (子代理不应: 管理会话/触发蒸馏/发消息给主会话)
    excluded = set()
    if exclude_tools:
        excluded = {t.strip() for t in exclude_tools.split(",") if t.strip()}
    # 默认排除: subagent 自身不能递归嵌套, session/mcp 不需要子代理管理
    excluded.update({"subagent", "session_list", "session_resume", "session_delete"})

    # 系统提示扩展 (子代理专属指令)
    system_extra = ""
    if instructions:
        system_extra = instructions.strip()

    # 创建隔离的子代理: 独立的 messages 列表
    sub_agent = create_agent(
        kernel, workspace,
        exclude_tools=tuple(excluded),
        system_extra=system_extra,
    )

    # 预加载技能 (如果有)
    if skills:
        skill_names = [s.strip() for s in skills.split(",") if s.strip()]
        sm = kernel.get("skill_manager")
        if sm:
            for name in skill_names:
                try:
                    skill = sm.get(name)
                    if skill:
                        content = sm.render_for_prompt([skill])
                        if content:
                            sub_agent.messages.append({
                                "role": "user",
                                "content": f"[已加载技能: {name}]\n{content}",
                            })
                            sub_agent.messages.append({
                                "role": "assistant",
                                "content": f"已加载技能 {name}, 继续任务。",
                            })
                except Exception as exc:  # noqa: BLE001
                    log.debug("子代理技能加载失败 %s: %s", name, exc)

    # 执行子任务 (限制迭代次数, 防止子代理失控)
    limit = timeout or _DEFAULT_TIMEOUT
    sub_max_iter = config.get("agent.subagent_max_iterations", 15)
    try:
        answer = sub_agent.run(
            task,
            stream=False,
            max_iterations=sub_max_iter,
        )
    except Exception as exc:  # noqa: BLE001
        return f"[子代理错误] {type(exc).__name__}: {exc}"

    # 截断过长的摘要
    if answer and len(answer) > _MAX_SUMMARY_LEN:
        answer = answer[:_MAX_SUMMARY_LEN] + f"\n...[摘要截断, 完整输出 {len(answer)} 字符]"

    # 记录子代理用量到主会话 (便于成本追踪)
    sub_usage = sub_agent.total_usage
    ctx.kernel.emit("subagent.completed", {
        "task": task[:200],
        "summary_len": len(answer) if answer else 0,
        "turns": sub_agent.turn_count,
        "usage": sub_usage,
    })

    return answer or "(子代理未产生输出)"


class SubagentPlugin(Plugin):
    name = "tools.subagent"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.subagent.enabled", True):
            return

        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="subagent",
            description=(
                "在隔离上下文中执行子任务, 只返回摘要。适合研究任务、并行工作、"
                "大量文件读取等不希望污染主会话上下文的场景。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": string_prop("子任务描述"),
                    "instructions": string_prop("额外系统指令 (可选, 如 '你是一个代码审查专家')"),
                    "skills": string_prop("逗号分隔的技能名列表 (可选, 子代理启动时预加载)"),
                    "exclude_tools": string_prop("逗号分隔的工具名列表 (可选, 从子代理工具集中排除)"),
                    "timeout": {"type": "integer", "description": "超时秒数 (默认 180)"},
                },
                "required": ["task"],
            },
            handler=_run_subagent,
            group="agent",
            read_only=True,
        ))
