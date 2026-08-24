"""技能管理器: 加载 / 保存 / 检索 / 渲染技能。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    updated_at: float = 0.0
    use_count: int = 0


_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class SkillManager:
    def __init__(self, home: Path, memory_store=None) -> None:
        self.dir = home / "skills"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.memory = memory_store  # 可选: 同步进 FTS 索引

    # ------------------------------------------------------------- 读写

    def save(self, name: str, description: str, body: str) -> Skill:
        """保存 (或改进) 一个技能。同名覆盖 = Hermes 的 refine 语义。"""
        slug = re.sub(r"[^a-z0-9\-]+", "-", name.lower()).strip("-") or "skill"
        path = self.dir / f"{slug}.md"
        old = self.load(slug)
        use_count = (old.use_count + 1) if old else 0
        text = (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"updated_at: {int(time.time())}\n"
            f"use_count: {use_count}\n"
            "---\n\n"
            f"{body.strip()}\n"
        )
        path.write_text(text, encoding="utf-8")
        if self.memory:
            self.memory.index("skill", f"{name}: {description}\n{body}", source=path.name)
        return Skill(name=name, description=description, body=body.strip(),
                     path=path, updated_at=time.time(), use_count=use_count)

    def load(self, slug: str) -> Optional[Skill]:
        path = self.dir / f"{slug}.md"
        if not path.exists():
            return None
        return self._parse(path)

    def _parse(self, path: Path) -> Skill:
        text = path.read_text(encoding="utf-8")
        meta = {}
        m = _FRONT_RE.match(text)
        body = text
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[m.end():]
        return Skill(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            body=body.strip(),
            path=path,
            updated_at=float(meta.get("updated_at", 0) or 0),
            use_count=int(meta.get("use_count", 0) or 0),
        )

    def list_all(self) -> List[Skill]:
        skills = [self._parse(p) for p in sorted(self.dir.glob("*.md"))]
        return sorted(skills, key=lambda s: (s.use_count, s.updated_at), reverse=True)

    # ------------------------------------------------------------- 检索注入

    def search(self, query: str, limit: int = 3) -> List[Skill]:
        """关键词打分检索 (名称/描述命中权重高)。"""
        terms = [t.lower() for t in re.split(r"\s+", query) if len(t) > 1]
        if not terms:
            return []
        scored = []
        for skill in self.list_all():
            hay = f"{skill.name} {skill.description}".lower()
            body = skill.body.lower()
            score = sum(3 for t in terms if t in hay) + sum(1 for t in terms if t in body)
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def render_for_prompt(self, skills: List[Skill]) -> str:
        if not skills:
            return ""
        parts = ["## 可复用技能 (来自过往经验, 优先复用并按需改进)"]
        for s in skills:
            parts.append(f"### {s.name}\n{s.description}\n\n{s.body}")
        return "\n\n".join(parts)
