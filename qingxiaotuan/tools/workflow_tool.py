"""动态工作流工具 (对标 Claude Code Dynamic Workflows)。

Claude Code 的 Dynamic Workflows:
- Claude 编写一个脚本, 在后台运行大量 subagent
- 返回一个综合结果
- 适用于: 代码库审计、大型迁移、多角度规划

青小团实现:
- workflow 工具: 定义一组子任务 (JSON), 支持 parallel/sequential 模式
- 每个子任务在隔离上下文中执行 (复用 subagent 机制)
- 支持 cross_check: 第二组 agent 验证第一组的发现
- 返回合并后的结构化结果
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from ..core.kernel import Kernel, Plugin
from ..tools.base import Tool, ToolContext, string_prop

log = logging.getLogger(__name__)

# 工作流最大并行子任务数
_MAX_PARALLEL = 8
# 单个工作流最大子任务数
_MAX_TASKS = 20


def _run_workflow(
    ctx: ToolContext,
    tasks_json: str,
    mode: str = "parallel",
    cross_check: bool = False,
) -> str:
    """执行动态工作流: 多个子任务并行/串行执行。

    Args:
        tasks_json: JSON 数组, 每项 {"task": "...", "instructions": "..."} 或简单字符串
        mode: parallel (并行) 或 sequential (串行)
        cross_check: 是否用第二组 agent 验证第一组结果
    """
    # 延迟导入避免循环
    from ..app import create_agent
    from ..tools.subagent_tool import _MAX_SUMMARY_LEN

    kernel = ctx.kernel
    config = kernel.require("config")
    workspace = ctx.workspace

    # 解析任务列表
    try:
        tasks = json.loads(tasks_json) if isinstance(tasks_json, str) else tasks_json
    except (json.JSONDecodeError, TypeError):
        return f"[错误] 无法解析任务 JSON: {tasks_json[:200]}"

    if not isinstance(tasks, list):
        tasks = [{"task": str(tasks)}]

    # 规范化: 字符串任务转为 dict
    normalized: List[Dict[str, str]] = []
    for t in tasks[:_MAX_TASKS]:
        if isinstance(t, str):
            normalized.append({"task": t})
        elif isinstance(t, dict):
            normalized.append({
                "task": t.get("task", ""),
                "instructions": t.get("instructions", ""),
            })

    if not normalized:
        return "[错误] 工作流无有效任务"

    results: List[str] = []

    if mode == "sequential":
        # 串行: 每个子任务完成后将结果注入下一个子任务的上下文
        context_chain = ""
        for i, task_def in enumerate(normalized):
            task_text = task_def["task"]
            if context_chain:
                task_text = f"前置上下文:\n{context_chain}\n\n当前任务: {task_text}"

            result = _execute_subtask(kernel, workspace, config, task_text, task_def.get("instructions", ""))
            results.append(f"[任务 {i + 1}/{len(normalized)}] {task_def['task'][:80]}\n{result}")

            # 更新上下文链 (截断防止无限增长)
            context_chain = "\n".join(results)[-2000:]

    else:
        # 并行 (默认): 所有子任务同时执行
        max_workers = min(len(normalized), _MAX_PARALLEL)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {}
            for i, task_def in enumerate(normalized):
                future = executor.submit(
                    _execute_subtask,
                    kernel, workspace, config,
                    task_def["task"],
                    task_def.get("instructions", ""),
                )
                future_to_idx[future] = i

            # 按完成顺序收集结果, 超时的跳过
            subagent_timeout = config.get("agent.subagent_timeout", 180)
            for future in as_completed(future_to_idx, timeout=subagent_timeout + 30):
                idx = future_to_idx[future]
                try:
                    result = future.result(timeout=5)
                except Exception as exc:  # noqa: BLE001
                    result = f"[子任务超时/错误] {type(exc).__name__}: {exc}"
                task_def = normalized[idx]
                results.append(
                    f"[任务 {idx + 1}/{len(normalized)}] {task_def['task'][:80]}\n{result}"
                )

    # 交叉验证 (cross_check): 用新子任务审查第一组结果
    if cross_check and len(results) > 1:
        review_text = (
            "请审查以下多个并行任务的发现, 检查是否有冲突、遗漏或不一致:\n\n"
            + "\n\n---\n\n".join(results)
        )
        review_result = _execute_subtask(
            kernel, workspace, config, review_text,
            "你是一个审查者。检查多组结果的一致性和完整性, 指出冲突和遗漏。",
        )
        results.append(f"\n[交叉验证结果]\n{review_result}")

    # 合并输出 (截断保护)
    combined = "\n\n---\n\n".join(results)
    max_total = config.get("tools.workflow.max_output_chars", 30000)
    if len(combined) > max_total:
        combined = combined[:max_total] + f"\n...[工作流输出截断, 原始 {len(combined)} 字符]"

    # 统计
    kernel.emit("workflow.completed", {
        "tasks": len(normalized),
        "mode": mode,
        "cross_check": cross_check,
        "total_len": len(combined),
    })

    return combined


def _execute_subtask(
    kernel, workspace, config, task, instructions=""
) -> str:
    """在隔离上下文中执行单个子任务。"""
    from ..app import create_agent

    excluded = {"subagent", "workflow", "send_message", "list_agents", "receive_messages"}
    sub_agent = create_agent(
        kernel, workspace,
        exclude_tools=tuple(excluded),
        system_extra=instructions.strip() if instructions else "",
    )

    sub_max_iter = config.get("agent.subagent_max_iterations", 15)
    try:
        answer = sub_agent.run(task, stream=False, max_iterations=sub_max_iter)
    except Exception as exc:  # noqa: BLE001
        return f"[子任务错误] {type(exc).__name__}: {exc}"

    return answer or "(无输出)"


class WorkflowPlugin(Plugin):
    name = "tools.workflow"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.workflow.enabled", True):
            return

        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="workflow",
            description=(
                "执行动态工作流: 多个子任务并行/串行执行, 返回合并结果。"
                "适用于: 代码库审计、大型迁移、多角度规划。"
                "可启用 cross_check 让第二组 agent 验证第一组发现。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tasks_json": string_prop(
                        'JSON 数组: [{"task":"...", "instructions":"..."}, ...] '
                        '或字符串数组 ["task1", "task2"]'
                    ),
                    "mode": {
                        "type": "string",
                        "enum": ["parallel", "sequential"],
                        "description": "执行模式: parallel(并行, 默认) / sequential(串行, 结果传递)",
                    },
                    "cross_check": {
                        "type": "boolean",
                        "description": "启用交叉验证: 第二组 agent 审查第一组结果 (默认 false)",
                    },
                },
                "required": ["tasks_json"],
            },
            handler=_run_workflow,
            group="agent",
        ))
