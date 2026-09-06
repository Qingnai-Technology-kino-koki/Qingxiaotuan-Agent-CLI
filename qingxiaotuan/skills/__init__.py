"""技能系统 —— Hermes 自进化闭环的核心。

技能 = Markdown 格式的程序性记忆, 存放在 ~/.qingxiaotuan/skills/*.md。
Agent 完成任务后通过 skill_save 工具把"可复用的方法"蒸馏成技能;
下次遇到相似任务时, 系统提示词自动注入相关技能, Agent 直接复用并继续改进。

增强架构 (v0.2):
  - 声明式能力 (provides/requires): skill 之间可声明依赖关系
  - 条件激活 (activation): lazy/auto/always/proactive 四种模式
  - 依赖图解析: requires 自动注入被依赖的 skill (递归)
  - 条件评估: 按语言/项目类型/文件类型条件激活
"""

from .manager import Skill, SkillManager
from .enhanced import EnhancedSkillManager, SkillMeta, ActivationContext

__all__ = ["Skill", "SkillManager", "EnhancedSkillManager", "SkillMeta", "ActivationContext"]
