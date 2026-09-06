"""技能管理器: 加载 / 保存 / 检索 / 渲染 / 热度追踪。

对标 Claude Code 的 Skills 子系统:
- 技能 = Markdown + frontmatter, 存于 <home>/skills/*.md
- 同名保存 = refine (Hermes 语义): use_count 递增, 内容更新
- 原子写入 (临时文件 + os.replace): 崩溃不损坏磁盘技能
- 检索: 任务关键词 → 语义标签映射 + 名称/描述/内容打分 + 热度加权
- notify_used(): 技能被实际采用时热度自增, 让高频技能自然浮出
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    """一个可复用技能: frontmatter 元数据 + Markdown 正文。"""

    name: str
    description: str
    body: str
    path: Path
    updated_at: float = 0.0
    use_count: int = 0
    tags: List[str] = field(default_factory=list)   # 语义标签: ["debugging", "python"]
    priority: int = 0                                # 越高越先注入
    # 扩展元数据 (向后兼容: 旧文件缺省即默认值)
    version: int = 1
    activation: str = "lazy"                         # lazy | auto | always | proactive
    triggers: List[str] = field(default_factory=list)
    source: str = "manual"                           # manual | self-improve | auto-distill


class SkillManager:
    """基础技能管理器: 磁盘读写 + 语义检索。"""

    def __init__(self, home: Path, memory_store=None) -> None:
        self.dir = Path(home) / "skills"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.memory = memory_store  # 可选: 同步进 FTS 索引

    # ------------------------------------------------------------- 工具

    @staticmethod
    def slugify(name: str) -> str:
        """技能名 → 文件名 slug (与旧版规则完全一致)。"""
        return re.sub(r"[^a-z0-9\-]+", "-", name.lower()).strip("-") or "skill"

    # ------------------------------------------------------------- 读写

    def save(self, name: str, description: str, body: str) -> Skill:
        """保存 (或改进) 一个技能。同名覆盖 = refine 语义, use_count 递增。"""
        slug = self.slugify(name)
        path = self.dir / f"{slug}.md"
        old = self.load(slug)
        use_count = (old.use_count + 1) if old else 0
        skill = Skill(
            name=name, description=description, body=body.strip(),
            path=path, updated_at=time.time(), use_count=use_count,
            tags=list(old.tags) if old else [],
            priority=old.priority if old else 0,
            version=(old.version + 1) if old else 1,
            activation=old.activation if old else "lazy",
            triggers=list(old.triggers) if old else [],
            source=old.source if old else "manual",
        )
        self._write(skill)
        if self.memory:
            self.memory.index("skill", f"{name}: {description}\n{body}", source=path.name)
        return skill

    def notify_used(self, slug: str) -> Optional[Skill]:
        """标记技能被实际采用: 热度自增并落盘 (对标 CC 的 use 计数)。"""
        skill = self.load(slug)
        if skill is None:
            return None
        skill.use_count += 1
        skill.updated_at = time.time()
        self._write(skill)
        return skill

    def _write(self, skill: Skill) -> None:
        """原子写入: 临时文件 + os.replace, 崩溃/中断不损坏原技能。"""
        text = (
            "---\n"
            f"name: {skill.name}\n"
            f"description: {skill.description}\n"
            f"updated_at: {int(skill.updated_at)}\n"
            f"use_count: {skill.use_count}\n"
            f"version: {skill.version}\n"
            f"activation: {skill.activation}\n"
            f"priority: {skill.priority}\n"
            f"tags: {', '.join(skill.tags)}\n"
            "---\n\n"
            f"{skill.body.strip()}\n"
        )
        tmp = skill.path.with_name(skill.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, skill.path)

    def load(self, slug: str) -> Optional[Skill]:
        path = self.dir / f"{slug}.md"
        if not path.exists():
            return None
        return self._parse(path)

    def _parse(self, path: Path) -> Skill:
        text = path.read_text(encoding="utf-8")
        meta: Dict[str, str] = {}
        body = text
        m = _FRONT_RE.match(text)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[m.end():]
        tags_raw = meta.get("tags", "")
        if tags_raw.startswith("["):
            # YAML 列表格式: ["tag1", "tag2"]
            tags = [t.strip().strip('"').strip("'") for t in tags_raw.strip("[]").split(",")]
        else:
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        triggers_raw = meta.get("triggers", "")
        triggers = [t.strip().strip('"').strip("'")
                    for t in re.split(r"[,\[\]\"']+", triggers_raw) if t.strip()]
        return Skill(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            body=body.strip(),
            path=path,
            updated_at=float(meta.get("updated_at", 0) or 0),
            use_count=int(meta.get("use_count", 0) or 0),
            tags=tags,
            priority=int(meta.get("priority", 0) or 0),
            version=int(meta.get("version", 1) or 1),
            activation=meta.get("activation", "lazy"),
            triggers=triggers,
            source=meta.get("source", "manual"),
        )

    def list_all(self) -> List[Skill]:
        skills = [self._parse(p) for p in sorted(self.dir.glob("*.md"))]
        return sorted(skills, key=lambda s: (s.use_count, s.updated_at), reverse=True)

    # ------------------------------------------------------------- 语义检索 (提到才读)

    # 任务关键词 → 技能标签 的语义映射表
    # 当用户任务包含左侧关键词时, 右侧标签的技能被激活
    _TASK_TAG_MAP = {
        # 调试
        "bug": ["debugging", "troubleshooting"],
        "fix": ["debugging", "troubleshooting"],
        "error": ["debugging", "troubleshooting"],
        "crash": ["debugging"],
        "exception": ["debugging"],
        "调试": ["debugging"],
        "修复": ["debugging", "troubleshooting"],
        # 重构
        "refactor": ["refactoring", "clean-code"],
        "重构": ["refactoring", "clean-code"],
        "clean": ["refactoring", "clean-code"],
        "restructure": ["refactoring"],
        # 测试
        "test": ["testing", "tdd"],
        "测试": ["testing", "tdd"],
        "coverage": ["testing"],
        "pytest": ["testing"],
        "unittest": ["testing"],
        # 架构
        "architecture": ["architecture", "design-pattern"],
        "架构": ["architecture", "design-pattern"],
        "design": ["architecture", "design-pattern"],
        "设计": ["architecture"],
        "module": ["architecture"],
        "模块": ["architecture"],
        # 代码编辑
        "edit": ["code-editing", "precise-edit"],
        "编辑": ["code-editing"],
        "修改": ["code-editing"],
        "改代码": ["code-editing"],
        "write code": ["code-editing"],
        # 性能
        "performance": ["performance", "optimization"],
        "性能": ["performance", "optimization"],
        "优化": ["performance", "optimization"],
        "slow": ["performance"],
        "内存": ["performance", "memory"],
        "memory": ["performance", "memory"],
        # 安全
        "security": ["security", "hardening"],
        "安全": ["security", "hardening"],
        "vulnerability": ["security"],
        "注入": ["security"],
        "injection": ["security"],
        # API
        "api": ["api-design", "rest"],
        "接口": ["api-design"],
        "endpoint": ["api-design"],
        "路由": ["api-design", "routing"],
        # 数据库
        "database": ["database", "sql"],
        "数据库": ["database", "sql"],
        "sql": ["database", "sql"],
        "migration": ["database"],
        # Git
        "git": ["git", "version-control"],
        "commit": ["git"],
        "branch": ["git"],
        "merge": ["git"],
        "rebase": ["git"],
        # 前端
        "frontend": ["frontend", "ui"],
        "前端": ["frontend", "ui"],
        "react": ["frontend", "react"],
        "vue": ["frontend", "vue"],
        "css": ["frontend", "css"],
        "html": ["frontend"],
        # DevOps
        "deploy": ["devops", "deployment"],
        "部署": ["devops", "deployment"],
        "ci": ["devops", "ci-cd"],
        "cd": ["devops", "ci-cd"],
        "docker": ["devops", "container"],
        "kubernetes": ["devops", "k8s"],
        # 文档
        "document": ["documentation"],
        "文档": ["documentation"],
        "readme": ["documentation"],
        "docstring": ["documentation"],
        # 类型
        "type": ["typing", "type-safety"],
        "类型": ["typing"],
        "mypy": ["typing"],
        # 日志
        "log": ["logging", "observability"],
        "日志": ["logging"],
        "monitor": ["observability"],
    }

    def search(self, query: str, limit: int = 5) -> List[Skill]:
        """语义匹配检索: 任务描述 → 标签映射 + 关键词打分, 返回最相关的技能。

        匹配策略 (提到才读):
        1. 从任务描述中提取关键词, 通过 _TASK_TAG_MAP 映射到语义标签
        2. 标签匹配的技能获得高权重
        3. 名称/描述/内容的关键词匹配作为补充
        4. 优先级 (priority) 与使用频率作为加分项
        """
        terms = [t.lower() for t in re.split(r"\s+", query) if len(t) > 1]
        if not terms:
            return []

        # 1. 从任务描述提取语义标签
        task_tags: set = set()
        for term in terms:
            task_tags.update(self._TASK_TAG_MAP.get(term, []))
        # 也检查多词组合 (如 "code review" → review tag)
        query_lower = query.lower()
        for key, tags in self._TASK_TAG_MAP.items():
            if len(key) > 2 and key in query_lower:
                task_tags.update(tags)

        scored = []
        for skill in self.list_all():
            score = 0.0

            # 标签匹配 (最高权重: 5 分/命中)
            if task_tags and skill.tags:
                tag_hits = task_tags.intersection(set(skill.tags))
                score += len(tag_hits) * 5.0

            # 名称/描述匹配 (3 分/命中)
            hay = f"{skill.name} {skill.description}".lower()
            score += sum(3.0 for t in terms if t in hay)

            # 内容匹配 (1 分/命中)
            body = skill.body.lower()
            score += sum(1.0 for t in terms if t in body)

            # 优先级加分
            score += skill.priority * 0.5

            # 使用频率加分 (对数缩放, 避免高频技能永远霸榜)
            score += math.log1p(skill.use_count) * 0.3

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def activate_for_task(self, task_hint: str, limit: int = 5) -> List[Skill]:
        """为特定任务激活最相关的技能 (提到才读的核心入口)。

        与 search() 的区别:
        - 如果任务与任何技能都不相关, 返回空列表 (不注入无关技能)
        - 标签匹配的技能优先于纯关键词匹配
        """
        results = self.search(task_hint, limit=limit)
        if not results and task_hint.strip():
            # 无匹配: 不注入无关技能 (避免 system prompt 膨胀)
            return []
        return results

    def render_for_prompt(self, skills: List[Skill]) -> str:
        if not skills:
            return ""
        parts = ["## 可复用技能 (来自过往经验, 优先复用并按需改进)"]
        for s in skills:
            parts.append(f"### {s.name}\n{s.description}\n\n{s.body}")
        return "\n\n".join(parts)
