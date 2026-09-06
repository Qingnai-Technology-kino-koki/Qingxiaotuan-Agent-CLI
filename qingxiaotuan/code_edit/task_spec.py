"""代码编辑任务的标准化输入模板（四要素）。

把用户自由文本或模板化输入归一成 Goal / Context / Constraints / Done when，
并能在缺失关键字段时驱动一次 AskUserQuestion 补全，避免靠猜测填约束。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 各要素的归一化标记（只取不会出现在正文里的「标题级」词，避免误判正文为分段）
_MARKERS: Dict[str, str] = {
    "goal": r"(goal|目标|任务目标|要做什么)",
    "context": r"(context|上下文|背景)",
    "constraints": r"(constraints|约束)",
    "done_when": r"(done[\s_]*when|完成标准|验收标准|怎么算完成|acceptance)",
}

# 纯标题行：## Goal（目标） / ## Context / - 约束： 等，内容在后续行
_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|\*\*\s*|\-\s*)*"
    r"(?P<label>" + "|".join(_MARKERS.values()) + r")"
    r"(?:\s*[:：]\s*|\s*[（(][^）)]*[）)]|\s*$)",
    re.IGNORECASE,
)
# 同行内联：目标: 加缓存 / Context: x.py
_INLINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|\*\*\s*|\-\s*)*"
    r"(?P<label>" + "|".join(_MARKERS.values()) + r")"
    r"\s*[:：]\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

_PATH_RE = re.compile(r"`?([\w./\\-]+\.[\w]+)`?")
_DEP_RE = re.compile(r"(?:依赖|dependency|require|import)[^a-zA-Z0-9]*([a-zA-Z0-9_\-\.]+)")


@dataclass
class TaskSpec:
    goal: str = ""
    context: str = ""
    constraints: str = ""
    done_when: str = ""
    raw: str = ""
    context_files: List[str] = field(default_factory=list)
    constraint_deps: List[str] = field(default_factory=list)

    REQUIRED: tuple = ("goal", "context", "constraints", "done_when")

    def missing(self) -> List[str]:
        """返回缺失的必填字段名（中文）。"""
        label_cn = {
            "goal": "Goal（目标）",
            "context": "Context（上下文）",
            "constraints": "Constraints（约束）",
            "done_when": "Done when（完成标准）",
        }
        return [label_cn[f] for f in self.REQUIRED if not getattr(self, f).strip()]

    def is_complete(self) -> bool:
        return not self.missing()

    def render(self) -> str:
        """渲染为标准化任务简报，供 Agent 直接消费。"""
        lines = ["# 代码编辑任务简报", ""]
        lines.append(f"## Goal（目标）\n{self.goal.strip() or '（未提供）'}")
        lines.append("")
        ctx = self.context.strip() or "（未提供）"
        if self.context_files:
            ctx += "\n\n涉及文件: " + ", ".join(self.context_files)
        lines.append(f"## Context（上下文）\n{ctx}")
        lines.append("")
        cons = self.constraints.strip() or "（未提供）"
        if self.constraint_deps:
            cons += "\n\n识别到的依赖约束: " + ", ".join(self.constraint_deps)
        lines.append(f"## Constraints（约束）\n{cons}")
        lines.append("")
        lines.append(f"## Done when（完成标准）\n{self.done_when.strip() or '（未提供）'}")
        return "\n".join(lines)


# 给用户填空的模板
TEMPLATE = """# 代码编辑任务

## Goal（目标）
<要达成什么、为什么>

## Context（上下文）
<涉及的文件 / 模块 / 函数；相关报错、日志、复现步骤>

## Constraints（约束）
<代码风格、命名、依赖范围（禁止引入的新依赖）、兼容性/性能红线、禁区>

## Done when（完成标准）
<哪些测试通过、何种输出、哪些既有行为必须保持不变>
"""


def parse_task_spec(raw: str, partial: Optional[Dict[str, str]] = None) -> TaskSpec:
    """把自由文本解析为 TaskSpec。

    - 若含四要素标记（**Goal** / ## Context / 约束: 等），按段归属；
    - 否则整段当作 goal，其余字段留空待补全；
    - partial 中已明确的字段优先，不会因解析而被清空。
    """
    raw = (raw or "").strip()
    spec = TaskSpec(raw=raw)
    sections: Dict[str, List[str]] = {k: [] for k in _MARKERS}
    current: Optional[str] = None

    for line in raw.splitlines():
        mi = _INLINE_RE.match(line)
        if mi:
            current = _match_key(mi.group("label"))
            rest = mi.group("rest").strip()
            if rest:
                sections[current].append(rest)
            continue
        mh = _HEADER_RE.match(line)
        if mh:
            current = _match_key(mh.group("label"))
            continue
        if current is not None:
            sections[current].append(line)

    for key, buf in sections.items():
        text = "\n".join(buf).strip()
        if text:
            setattr(spec, key, text)

    # 无标记时，整段归入 goal
    if not any(sections.values()):
        spec.goal = raw

    if partial:
        for k, v in partial.items():
            if k in TaskSpec.REQUIRED and v and not getattr(spec, k).strip():
                setattr(spec, k, v)

    spec.context_files = sorted(set(_PATH_RE.findall(spec.context)))
    spec.constraint_deps = sorted(set(_DEP_RE.findall(spec.constraints)))
    return spec


def _match_key(label: str) -> str:
    low = label.lower()
    for key, pat in _MARKERS.items():
        if re.search(pat, low, re.IGNORECASE):
            return key
    return "goal"
