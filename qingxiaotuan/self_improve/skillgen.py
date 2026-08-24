"""技能提炼器 (SkillGenerator) —— 把高频成功模式提炼为技能草稿。

对 repeated_ok 经验, 生成一个最小可用 SKILL.md 骨架, 写入 skills 目录,
由人类审阅后正式启用。技能草稿不自动激活, 避免无监督膨胀。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List

from .reflector import Experience


class SkillGenerator:
    def __init__(self, skills_dir: str) -> None:
        self.skills_dir = skills_dir
        os.makedirs(skills_dir, exist_ok=True)

    def draft(self, exps: List[Experience]) -> List[str]:
        """为 repeated_ok 经验生成技能草稿, 返回生成的文件路径列表。"""
        paths: List[str] = []
        for exp in exps:
            if exp.kind != "repeated_ok":
                continue
            slug = exp.tool.replace("_", "-").replace(".", "-")
            name = f"learned-{slug}"
            d = os.path.join(self.skills_dir, name)
            os.makedirs(d, exist_ok=True)
            md = self._skeleton(exp, name)
            p = os.path.join(d, "SKILL.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(md)
            paths.append(p)
        return paths

    def _skeleton(self, exp: Experience, name: str) -> str:
        ts = datetime.now().isoformat(timespec="seconds")
        return f"""---
name: {name}
description: 由 self-improve 自动提炼的技能草稿 (源自工具 {exp.tool} 高频成功)。待人工审阅后启用。
source: self-improve
generated_at: {ts}
status: draft
---

# {name}

> 本技能由青小团 self-improve 闭环从执行历史中自动提炼, **当前为草稿**, 尚未激活。
> 请人工审阅下方流程, 确认无误后删除 `status: draft` 即可启用。

## 触发场景
- 工具 `{exp.tool}` 在近期执行中成功 {exp.count} 次, 说明其使用模式已相对稳定。

## 推荐流程
1. 调用 `{exp.tool}` 前, 先通过 `ext_safety_analyze` 评估影响半径。
2. 对危险参数先走 plan / dry-run, 确认无误再执行。
3. 执行后用结构化 ToolResult 判断 status, 失败则回到第 1 步重试。

## 注意
- 不要盲目自动化; 涉及写/删/推送的操作始终保留人工确认。
"""
