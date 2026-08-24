"""自我改进闭环 (Self-Improve Loop) —— 青小团世界级差异化能力之一。

理念 (对标 Self-Improve Agent 视频):
  Agent 不被任何一次失误定义, 只看它如何从失误里长出血肉。
  每次执行后自动复盘 -> 从成功/失败/被拒的事件中抽取经验 ->
  生成或修正「规则」(rules 引擎) 与「技能草稿」(skills 目录)。

模块组成:
  - reflector: 从内核 tool.executed 事件流抽取经验 (失败模式 / 高频危险操作 / 慢操作)
  - rulegen:   把经验固化为 rules 引擎可加载的 .jsonl 规则 (可审计、可重放)
  - skillgen:  把重复成功模式提炼为技能草稿 (SKILL.md 骨架)
  - plugin:    把 self-improve 注册为内核插件, 提供 improve 服务
"""
from .reflector import Reflector, Experience
from .rulegen import RuleGenerator
from .skillgen import SkillGenerator
from .plugin import SelfImprovePlugin

__all__ = ["Reflector", "Experience", "RuleGenerator", "SkillGenerator", "SelfImprovePlugin"]
