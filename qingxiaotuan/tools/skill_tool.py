"""技能工具插件: Hermes 自进化闭环的执行端 —— 蒸馏 / 查阅技能。"""

from __future__ import annotations

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop


def skill_save(ctx: ToolContext, name: str, description: str, body: str) -> str:
    manager = ctx.kernel.require("skill_manager")
    skill = manager.save(name, description, body)
    return f"技能已保存: {skill.name} -> {skill.path} (累计使用 {skill.use_count} 次)"


def skill_list(ctx: ToolContext) -> str:
    manager = ctx.kernel.require("skill_manager")
    skills = manager.list_all()
    if not skills:
        return "还没有任何技能。完成一个可复用的任务后, 用 skill_save 蒸馏一个吧。"
    return "\n".join(f"- {s.name} (使用 {s.use_count} 次): {s.description}" for s in skills)


def skill_read(ctx: ToolContext, name: str) -> str:
    manager = ctx.kernel.require("skill_manager")
    slug = name.lower().replace(" ", "-")
    skill = manager.load(slug)
    if not skill:
        return f"[错误] 技能不存在: {name}。可用 skill_list 查看全部技能。"
    return f"# {skill.name}\n{skill.description}\n\n{skill.body}"


class SkillToolPlugin(Plugin):
    name = "tools.skills"
    requires = ["tool_registry", "skill_manager"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="skill_save",
            description="把验证有效的方法蒸馏为可复用技能 (同名即改进)",
            parameters={
                "type": "object",
                "properties": {
                    "name": string_prop("技能名"),
                    "description": string_prop("何时使用, 一句话"),
                    "body": string_prop("步骤/命令/注意 (Markdown)"),
                },
                "required": ["name", "description", "body"],
            },
            handler=skill_save, group="skills", dangerous=True,
        ))
        registry.register(Tool(
            name="skill_list",
            description="列出全部技能",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=skill_list, group="skills",
        ))
        registry.register(Tool(
            name="skill_read",
            description="阅读技能完整内容",
            parameters={
                "type": "object",
                "properties": {"name": string_prop("技能名")},
                "required": ["name"],
            },
            handler=skill_read, group="skills",
        ))
