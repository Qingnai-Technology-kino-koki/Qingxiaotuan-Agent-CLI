"""技能系统 —— Hermes 自进化闭环的核心。

技能 = Markdown 格式的程序性记忆, 存放在 ~/.qingxiaotuan/skills/*.md。
Agent 完成任务后通过 skill_save 工具把"可复用的方法"蒸馏成技能;
下次遇到相似任务时, 系统提示词自动注入相关技能, Agent 直接复用并继续改进。
"""

from .manager import Skill, SkillManager

__all__ = ["Skill", "SkillManager"]
