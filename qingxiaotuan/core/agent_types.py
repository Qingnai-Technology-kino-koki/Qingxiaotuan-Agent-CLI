"""Subagent 类型注册表 (对标 Claude Code 的 Task 工具类型化委派)。

Claude Code 允许主 Agent 通过 Task 工具以「类型」委派单个子任务: 每种类型有
独立的角色指令 (追加进子代理系统提示) 与能力边界 (只读与否), 例如:
- general-purpose: 通用全能型;
- explore / plan: 只读调研型, 被剥夺一切修改类工具, 物理上无法写仓库。

青小团的对标实现: AgentType 数据类 + 内置类型表 + readonly_tool_names()。
task 工具按类型构造 SubTask (exclude_tools / meta["system_extra"]),
由 SubAgentPool 在隔离的 Agent 实例里执行 (线程软隔离或进程级沙箱)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

if TYPE_CHECKING:  # 仅类型标注, 避免循环导入
    from ..tools.base import ToolRegistry


@dataclass(frozen=True)
class AgentType:
    """一种可委派的子 Agent 类型。"""

    name: str                 # 类型名 (task 工具的 subagent_type 参数值)
    description: str          # 何时该用这种类型 (给主 Agent 选型时看)
    system_extra: str = ""    # 追加到子 Agent 系统提示末尾的角色指令
    read_only: bool = False   # True = 排除所有非只读工具 (物理防写)


# 内置类型 (对标 Claude Code 的 general-purpose / Explore / Plan)
_BUILTIN_TYPES: List[AgentType] = [
    AgentType(
        name="general-purpose",
        description=(
            "通用研究与多步执行子代理。拥有与主代理相同的全部工具, "
            "适合复杂检索、代码搜索和需要动手的多步骤任务。"
        ),
    ),
    AgentType(
        name="explore",
        description=(
            "只读代码库探索者。用于跨文件搜索关键词、梳理调用关系、回答"
            "\"这段逻辑在哪/如何工作\"一类问题; 无法写入任何文件。"
        ),
        system_extra=(
            "你是代码库探索专家。只做只读调查: 定位代码、梳理调用链、汇总事实。\n"
            "不得修改任何文件或系统状态; 结论必须给出具体文件路径 (尽量带行号) 作为依据。"
        ),
        read_only=True,
    ),
    AgentType(
        name="plan",
        description=(
            "只读实现规划者。先充分探索代码, 再产出分步实施计划 "
            "(涉及哪些文件/函数、每步怎么改、有何取舍); 不实际改代码。"
        ),
        system_extra=(
            "你是软件架构规划专家。先充分阅读相关代码, 再输出一份可执行的"
            "分步实施计划: 每步写明改动哪个文件的哪个函数、为什么这么改,"
            "并列出备选方案与取舍。你不执行任何修改操作。"
        ),
        read_only=True,
    ),
    AgentType(
        name="coder",
        description="通用软件工程执行者, 可读写文件并运行命令, 用于落实具体的编码改动。",
        system_extra=(
            "你是资深软件工程师。聚焦完成交给你的具体编码任务: 改动最小化,"
            "遵循周边代码风格, 完成后自查 (跑相关测试/类型检查)。"
            "不要顺手重构与任务无关的代码。"
        ),
    ),
]


def readonly_tool_names(registry: "ToolRegistry") -> List[str]:
    """收集注册表里「非只读」的工具名, 用作 read_only 类型的 exclude_tools。

    注意: task 工具自身不是只读工具, 会被一并排除 —— 从物理上杜绝了
    只读子代理再派生孙代理的递归风险。
    """
    return [t.name for t in registry.tools if not getattr(t, "read_only", False)]


# ------------------------------------------------------------------ 自定义类型 (agents/*.md)
#
# 对标 Claude Code 的自定义 subagent: 用户在 <QXT_HOME>/agents/ 下放 Markdown 文件
# 即可扩展 task 工具可委派的类型, 无需改代码:
#     ---
#     name: reviewer
#     description: 代码审查专家, 用于改动后的质量把关
#     read_only: true
#     ---
#     你是资深代码审查员。…… (正文 = 追加到子代理系统提示的角色指令)

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)  # 与 SkillManager 同款约定

# 进程内缓存: TaskToolPlugin.activate 时加载一次并固化进工具 description;
# 用户改动 agents/*.md 后重启会话生效 (与 Claude Code 一致)。
_custom_types: List[AgentType] = []
_custom_loaded: bool = False


def _parse_agent_file(path: Path) -> Optional[AgentType]:
    """解析单个 agents/*.md → AgentType; 缺 name 用文件名, 解析失败返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta: dict = {}
    body = text
    m = _FRONT_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[m.end():]
    name = meta.get("name", "").strip() or path.stem
    read_only = meta.get("read_only", "").strip().lower() in ("true", "yes", "1", "on")
    return AgentType(
        name=name,
        description=meta.get("description", "").strip() or f"自定义子代理类型 {name}",
        system_extra=body.strip(),
        read_only=read_only,
    )


def load_custom_types(home: Union[str, Path]) -> List[AgentType]:
    """扫描 <home>/agents/*.md 加载自定义 subagent 类型 (幂等, 可重复调用刷新)。

    与内置同名的自定义文件被忽略 (内置优先, 防止覆盖内置类型的防写护栏)。
    """
    global _custom_types, _custom_loaded
    agents_dir = Path(home) / "agents"
    types: List[AgentType] = []
    if agents_dir.is_dir():
        builtin_names = {t.name for t in _BUILTIN_TYPES}
        for path in sorted(agents_dir.glob("*.md")):
            at = _parse_agent_file(path)
            if at is not None and at.name not in builtin_names and at.name:
                types.append(at)
    _custom_types = types
    _custom_loaded = True
    return list(types)


def all_types() -> List[AgentType]:
    """内置 + 已加载的自定义类型 (自定义未加载时自动惰性补扫常见位置)。"""
    global _custom_types, _custom_loaded
    if not _custom_loaded:
        # 兜底: 直接使用本模块而未经过插件激活时也能看到自定义类型
        from ..config.loader import home_dir  # 延迟导入避免循环依赖
        try:
            load_custom_types(home_dir())
        except Exception:  # noqa: BLE001
            _custom_types = []
            _custom_loaded = True
    return list(_BUILTIN_TYPES) + list(_custom_types)


def get_agent_type(name: str) -> Optional[AgentType]:
    """按名字取类型 (先内置后自定义); 未知返回 None (由调用方报错)。"""
    for t in all_types():
        if t.name == name:
            return t
    return None


def type_names() -> List[str]:
    """全部可用类型名 (用作 task 工具参数的 enum)。"""
    return [t.name for t in all_types()]


def render_types_for_prompt() -> str:
    """渲染成给模型看的类型清单 (拼进 task 工具 description 与错误提示)。"""
    builtin_ids = {id(t) for t in _BUILTIN_TYPES}
    lines = []
    for t in all_types():
        tag = ", 只读" if t.read_only else ""
        suffix = "" if id(t) in builtin_ids else " (自定义)"
        lines.append(f"- {t.name}{tag}{suffix}: {t.description}")
    return "\n".join(lines)
