"""Universal Skill Format Adapter — 兼容所有 Agent 的技能格式。

支持的源格式:
1. SKILL.md (Agent Skills 开放标准) — Claude Code / Cursor Skills / Windsurf Skills / Gemini CLI
2. .cursorrules (Cursor Legacy) — 单文件规则, 旧版 Cursor
3. CLAUDE.md (Claude Code 项目级指令) — 项目根目录或 .claude/ 目录
4. AGENTS.md (Vercel AI SDK) — 项目级 Agent 指令
5. .windsurfrules (Windsurf Legacy) — Windsurf 旧版规则
6. .github/copilot-instructions.md (GitHub Copilot) — 项目级指令
7. .clinerules (Cline) — Cline Agent 规则
8. .aiderignore / .aider.conf.yml (Aider) — Aider 配置
9. rules/*.md (Cursor Rules 目录) — Cursor 新版规则目录
10. Any Markdown with YAML frontmatter — 通用格式兜底

每个格式通过一个 FormatAdapter 子类处理, 统一输出 SkillPackage:
- name, description, body (markdown), source_format, metadata
- 可直接序列化为标准 SKILL.md
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ================================================================ 数据结构

@dataclass
class SkillPackage:
    """标准化的技能包 — 所有格式转换的目标。"""
    name: str
    description: str
    body: str                               # markdown 正文
    source_format: str = "unknown"           # 来源格式标识
    metadata: Dict[str, str] = field(default_factory=dict)
    version: str = "0.1.0"
    license: str = ""
    compatibility: str = ""                  # 环境要求
    allowed_tools: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    source_path: Optional[str] = None        # 原始文件路径

    def to_skill_md(self) -> str:
        """序列化为标准 SKILL.md 格式。"""
        lines = ["---"]
        lines.append(f"name: {self._sanitize_name(self.name)}")
        lines.append(f"description: {self.description}")
        if self.version and self.version != "0.1.0":
            lines.append(f"version: {self.version}")
        if self.license:
            lines.append(f"license: {self.license}")
        if self.compatibility:
            lines.append(f"compatibility: {self.compatibility}")
        if self.allowed_tools:
            lines.append(f"allowed-tools: {' '.join(self.allowed_tools)}")
        if self.metadata:
            lines.append("metadata:")
            for k, v in self.metadata.items():
                lines.append(f"  {k}: {v}")
        if self.tags:
            lines.append(f"tags: {', '.join(self.tags)}")
        lines.append("---")
        lines.append("")
        lines.append(self.body.strip())
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """按 Agent Skills 规范清理名称: 小写、连字符、无连续连字符。"""
        name = name.lower().strip()
        name = re.sub(r"[^a-z0-9\-]+", "-", name)
        name = re.sub(r"-{2,}", "-", name)
        name = name.strip("-")
        return name or "unnamed-skill"


# ================================================================ 格式检测

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 已知格式的文件名/路径模式
FORMAT_DETECTORS: List[Tuple[str, Callable[[Path], bool]]] = [
    # SKILL.md (Agent Skills 标准)
    ("skill_md", lambda p: p.name == "SKILL.md"),
    # .cursorrules (Cursor Legacy)
    ("cursorrules", lambda p: p.name == ".cursorrules"),
    # CLAUDE.md
    ("claude_md", lambda p: p.name in ("CLAUDE.md", "claude.md")),
    # AGENTS.md
    ("agents_md", lambda p: p.name in ("AGENTS.md", "agents.md")),
    # .windsurfrules
    ("windsurfrules", lambda p: p.name == ".windsurfrules"),
    # .github/copilot-instructions.md
    ("copilot_instructions", lambda p: str(p).replace("\\", "/").endswith(".github/copilot-instructions.md")),
    # .clinerules
    ("clinerules", lambda p: p.name == ".clinerules"),
    # rules/*.md (Cursor Rules 目录)
    ("cursor_rules_dir", lambda p: "rules" in p.parts and p.suffix == ".md" and p.parent.name == "rules"),
    # Markdown with frontmatter (通用兜底)
    ("generic_frontmatter", lambda p: p.suffix == ".md" and _FRONT_RE.match(p.read_text(encoding="utf-8", errors="ignore")[:2000] or "")),
]


def detect_format(file_path: Path) -> str:
    """检测文件的技能格式。"""
    for fmt, detector in FORMAT_DETECTORS:
        try:
            if detector(file_path):
                return fmt
        except Exception:
            continue
    return "unknown"


# ================================================================ 格式适配器基类

class FormatAdapter:
    """格式适配器基类。"""

    format_id: str = "base"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        """解析文件, 返回 SkillPackage 或 None (格式不匹配)。"""
        raise NotImplementedError

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _parse_frontmatter(self, text: str) -> Tuple[Dict[str, str], str]:
        """解析 YAML frontmatter, 返回 (meta_dict, body)。"""
        meta = {}
        m = _FRONT_RE.match(text)
        body = text
        if m:
            for line in m.group(1).splitlines():
                kv = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
                if kv:
                    meta[kv.group(1)] = kv.group(2).strip()
            body = text[m.end():]
        return meta, body


# ================================================================ SKILL.md 适配器

class SkillMdAdapter(FormatAdapter):
    """Agent Skills 开放标准格式。"""
    format_id = "skill_md"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.name != "SKILL.md":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        meta, body = self._parse_frontmatter(text)
        name = meta.get("name") or file_path.parent.name
        description = meta.get("description", "")
        if not description and body.strip():
            # 取第一段非空文本作为描述
            for line in body.strip().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    description = line[:200]
                    break
        return SkillPackage(
            name=name,
            description=description,
            body=body,
            source_format=self.format_id,
            version=meta.get("version", "0.1.0"),
            license=meta.get("license", ""),
            compatibility=meta.get("compatibility", ""),
            allowed_tools=meta.get("allowed-tools", "").split() if meta.get("allowed-tools") else [],
            metadata={k: v for k, v in meta.items() if k not in (
                "name", "description", "version", "license", "compatibility", "allowed-tools"
            )},
            source_path=str(file_path),
        )


# ================================================================ .cursorrules 适配器

class CursorRulesAdapter(FormatAdapter):
    """Cursor Legacy .cursorrules 格式。"""
    format_id = "cursorrules"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.name != ".cursorrules":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        # .cursorrules 无 frontmatter, 整个文件就是规则
        # 从文件内容提取名称 (取第一个 # 标题或用目录名)
        name = file_path.parent.name
        lines = text.strip().splitlines()
        for line in lines:
            if line.startswith("# "):
                name = line[2:].strip()
                break
        return SkillPackage(
            name=name,
            description=f"Cursor rules migrated from .cursorrules",
            body=text.strip(),
            source_format=self.format_id,
            metadata={"original_format": "cursorrules"},
            source_path=str(file_path),
        )


# ================================================================ CLAUDE.md 适配器

class ClaudeMdAdapter(FormatAdapter):
    """Claude Code CLAUDE.md 项目级指令格式。"""
    format_id = "claude_md"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.name.lower() != "claude.md":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        # CLAUDE.md 可能有 frontmatter, 也可能纯 markdown
        meta, body = self._parse_frontmatter(text)
        name = meta.get("name") or file_path.parent.name + "-claude-instructions"
        description = meta.get("description", "")
        if not description:
            # 从内容提取: 第一个 # 标题
            for line in body.strip().splitlines():
                if line.startswith("# "):
                    description = line[2:].strip()[:200]
                    break
            if not description:
                description = f"Claude Code project instructions from {file_path.parent.name}"
        return SkillPackage(
            name=name,
            description=description,
            body=body,
            source_format=self.format_id,
            metadata={**meta, "source_project": str(file_path.parent)},
            source_path=str(file_path),
        )


# ================================================================ AGENTS.md 适配器

class AgentsMdAdapter(FormatAdapter):
    """Vercel AI SDK AGENTS.md 格式。"""
    format_id = "agents_md"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.name.lower() != "agents.md":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        meta, body = self._parse_frontmatter(text)
        name = meta.get("name") or file_path.parent.name + "-agents"
        description = meta.get("description", f"Vercel AGENTS.md instructions from {file_path.parent.name}")
        return SkillPackage(
            name=name,
            description=description,
            body=body,
            source_format=self.format_id,
            metadata={**meta, "source_project": str(file_path.parent)},
            source_path=str(file_path),
        )


# ================================================================ .windsurfrules 适配器

class WindsurfRulesAdapter(FormatAdapter):
    """Windsurf .windsurfrules 格式。"""
    format_id = "windsurfrules"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.name != ".windsurfrules":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        name = file_path.parent.name + "-windsurf-rules"
        # 提取第一个标题
        for line in text.strip().splitlines():
            if line.startswith("# "):
                name = line[2:].strip()
                break
        return SkillPackage(
            name=name,
            description=f"Windsurf rules migrated from .windsurfrules",
            body=text.strip(),
            source_format=self.format_id,
            metadata={"original_format": "windsurfrules"},
            source_path=str(file_path),
        )


# ================================================================ Copilot Instructions 适配器

class CopilotInstructionsAdapter(FormatAdapter):
    """GitHub Copilot .github/copilot-instructions.md 格式。"""
    format_id = "copilot_instructions"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        normalized = str(file_path).replace("\\", "/")
        if not normalized.endswith(".github/copilot-instructions.md"):
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        meta, body = self._parse_frontmatter(text)
        name = meta.get("name") or file_path.parent.parent.name + "-copilot-instructions"
        description = meta.get("description", f"GitHub Copilot instructions from {file_path.parent.parent.name}")
        return SkillPackage(
            name=name,
            description=description,
            body=body,
            source_format=self.format_id,
            metadata={**meta, "source_project": str(file_path.parent.parent)},
            source_path=str(file_path),
        )


# ================================================================ .clinerules 适配器

class ClineRulesAdapter(FormatAdapter):
    """Cline Agent .clinerules 格式。"""
    format_id = "clinerules"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.name != ".clinerules":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        name = file_path.parent.name + "-cline-rules"
        for line in text.strip().splitlines():
            if line.startswith("# "):
                name = line[2:].strip()
                break
        return SkillPackage(
            name=name,
            description=f"Cline agent rules migrated from .clinerules",
            body=text.strip(),
            source_format=self.format_id,
            metadata={"original_format": "clinerules"},
            source_path=str(file_path),
        )


# ================================================================ Cursor Rules 目录适配器

class CursorRulesDirAdapter(FormatAdapter):
    """Cursor rules/*.md 目录格式。"""
    format_id = "cursor_rules_dir"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.parent.name != "rules" or file_path.suffix != ".md":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        meta, body = self._parse_frontmatter(text)
        name = meta.get("name") or file_path.stem
        description = meta.get("description", "")
        if not description:
            for line in body.strip().splitlines():
                if line.startswith("# "):
                    description = line[2:].strip()[:200]
                    break
            if not description:
                description = f"Cursor rule: {file_path.stem}"
        return SkillPackage(
            name=name,
            description=description,
            body=body,
            source_format=self.format_id,
            metadata={**meta, "cursor_rules_dir": str(file_path.parent)},
            source_path=str(file_path),
        )


# ================================================================ 通用 Frontmatter 适配器

class GenericFrontmatterAdapter(FormatAdapter):
    """任何带 YAML frontmatter 的 Markdown 文件。"""
    format_id = "generic_frontmatter"

    def parse(self, file_path: Path) -> Optional[SkillPackage]:
        if file_path.suffix != ".md":
            return None
        text = self._read_text(file_path)
        if not text.strip():
            return None
        meta, body = self._parse_frontmatter(text)
        if not meta:
            return None  # 无 frontmatter, 不处理
        name = meta.get("name") or file_path.stem
        description = meta.get("description", "")
        if not description:
            for line in body.strip().splitlines():
                if line.startswith("# "):
                    description = line[2:].strip()[:200]
                    break
        if not description:
            return None  # 无描述, 不算有效技能
        return SkillPackage(
            name=name,
            description=description,
            body=body,
            source_format=self.format_id,
            metadata=meta,
            source_path=str(file_path),
        )


# ================================================================ 适配器注册表

ALL_ADAPTERS: List[FormatAdapter] = [
    SkillMdAdapter(),
    CursorRulesAdapter(),
    ClaudeMdAdapter(),
    AgentsMdAdapter(),
    WindsurfRulesAdapter(),
    CopilotInstructionsAdapter(),
    ClineRulesAdapter(),
    CursorRulesDirAdapter(),
    GenericFrontmatterAdapter(),
]


# ================================================================ 统一解析入口

def parse_skill_file(file_path: Path) -> Optional[SkillPackage]:
    """自动检测格式并解析技能文件。

    按优先级尝试所有适配器, 返回第一个成功解析的结果。
    """
    for adapter in ALL_ADAPTERS:
        try:
            result = adapter.parse(file_path)
            if result is not None:
                return result
        except Exception:
            continue
    return None


def parse_skill_directory(dir_path: Path) -> List[SkillPackage]:
    """扫描目录, 解析所有可识别的技能文件。

    支持:
    - 目录本身包含 SKILL.md
    - 目录包含 .cursorrules / CLAUDE.md / AGENTS.md 等
    - 子目录包含 SKILL.md (嵌套技能)
    - rules/*.md (Cursor Rules 目录)
    """
    packages: List[SkillPackage] = []
    if not dir_path.is_dir():
        return packages

    # 1. 直接检查 SKILL.md
    skill_md = dir_path / "SKILL.md"
    if skill_md.exists():
        pkg = parse_skill_file(skill_md)
        if pkg:
            packages.append(pkg)
            return packages  # SKILL.md 是标准格式, 直接返回

    # 2. 检查单文件规则 (Windows 不区分大小写, 用 set 去重)
    seen_names: set = set()
    for name in (".cursorrules", "CLAUDE.md", "claude.md", "AGENTS.md",
                 "agents.md", ".windsurfrules", ".clinerules"):
        fp = dir_path / name
        real = fp.resolve()
        if real in seen_names:
            continue
        seen_names.add(real)
        if fp.exists():
            pkg = parse_skill_file(fp)
            if pkg:
                packages.append(pkg)

    # 3. 检查 .github/copilot-instructions.md
    copilot = dir_path / ".github" / "copilot-instructions.md"
    if copilot.exists():
        pkg = parse_skill_file(copilot)
        if pkg:
            packages.append(pkg)

    # 4. 检查 rules/*.md (Cursor Rules 目录)
    rules_dir = dir_path / "rules"
    if rules_dir.is_dir():
        for md_file in sorted(rules_dir.glob("*.md")):
            pkg = parse_skill_file(md_file)
            if pkg:
                packages.append(pkg)

    # 5. 递归扫描子目录 (最多 2 层)
    for child in sorted(dir_path.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and child.name not in ("rules", ".github"):
            sub_pkgs = parse_skill_directory(child)
            packages.extend(sub_pkgs)

    return packages


def import_from_agent_project(
    project_path: Path,
    target_name: Optional[str] = None,
) -> List[SkillPackage]:
    """从任意 Agent 项目导入技能。

    扫描项目目录中的所有已知格式, 转换为标准 SkillPackage。
    返回所有成功解析的技能列表。

    用法:
        from pathlib import Path
        pkgs = import_from_agent_project(Path("./my-cursor-project"))
        for pkg in pkgs:
            print(f"Imported: {pkg.name} ({pkg.source_format})")
            print(pkg.to_skill_md())
    """
    project_path = Path(project_path).resolve()
    if not project_path.is_dir():
        return []

    all_pkgs: List[SkillPackage] = []

    # 扫描项目根目录
    root_pkgs = parse_skill_directory(project_path)
    all_pkgs.extend(root_pkgs)

    # 扫描 .claude/skills/ (Claude Code 技能目录)
    claude_skills = project_path / ".claude" / "skills"
    if claude_skills.is_dir():
        for skill_dir in sorted(claude_skills.iterdir()):
            if skill_dir.is_dir():
                pkgs = parse_skill_directory(skill_dir)
                all_pkgs.extend(pkgs)

    # 扫描 .cursor/skills/ (Cursor 技能目录)
    cursor_skills = project_path / ".cursor" / "skills"
    if cursor_skills.is_dir():
        for skill_dir in sorted(cursor_skills.iterdir()):
            if skill_dir.is_dir():
                pkgs = parse_skill_directory(skill_dir)
                all_pkgs.extend(pkgs)

    # 扫描 .windsurf/skills/ (Windsurf 技能目录)
    windsurf_skills = project_path / ".windsurf" / "skills"
    if windsurf_skills.is_dir():
        for skill_dir in sorted(windsurf_skills.iterdir()):
            if skill_dir.is_dir():
                pkgs = parse_skill_directory(skill_dir)
                all_pkgs.extend(pkgs)

    # 去重 (按 name)
    seen: Dict[str, SkillPackage] = {}
    for pkg in all_pkgs:
        key = pkg.name.lower()
        if key not in seen:
            seen[key] = pkg

    result = list(seen.values())
    if target_name:
        # 重命名所有包
        for pkg in result:
            pkg.metadata["original_name"] = pkg.name
            pkg.name = target_name
    return result


def batch_import(
    paths: List[Path],
    output_dir: Path,
    overwrite: bool = False,
) -> Dict[str, str]:
    """批量导入: 解析多个路径, 输出为标准 SKILL.md 文件。

    返回 {name: output_path} 映射。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: Dict[str, str] = {}

    for path in paths:
        path = Path(path).resolve()
        if path.is_file():
            pkg = parse_skill_file(path)
            if pkg:
                slug = SkillPackage._sanitize_name(pkg.name)
                out_path = output_dir / slug / "SKILL.md"
                if out_path.exists() and not overwrite:
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(pkg.to_skill_md(), encoding="utf-8")
                results[pkg.name] = str(out_path)
        elif path.is_dir():
            pkgs = import_from_agent_project(path)
            for pkg in pkgs:
                slug = SkillPackage._sanitize_name(pkg.name)
                out_path = output_dir / slug / "SKILL.md"
                if out_path.exists() and not overwrite:
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(pkg.to_skill_md(), encoding="utf-8")
                results[pkg.name] = str(out_path)

    return results


# ================================================================ 格式信息

FORMAT_INFO: Dict[str, Dict[str, str]] = {
    "skill_md": {
        "name": "Agent Skills (SKILL.md)",
        "description": "开放标准, 被 Claude Code / Cursor / Windsurf / Gemini CLI 支持",
        "file_pattern": "SKILL.md",
    },
    "cursorrules": {
        "name": "Cursor Legacy (.cursorrules)",
        "description": "Cursor 旧版单文件规则格式",
        "file_pattern": ".cursorrules",
    },
    "claude_md": {
        "name": "Claude Code (CLAUDE.md)",
        "description": "Claude Code 项目级指令文件",
        "file_pattern": "CLAUDE.md",
    },
    "agents_md": {
        "name": "Vercel AI SDK (AGENTS.md)",
        "description": "Vercel AI SDK Agent 指令格式",
        "file_pattern": "AGENTS.md",
    },
    "windsurfrules": {
        "name": "Windsurf Legacy (.windsurfrules)",
        "description": "Windsurf 旧版规则格式",
        "file_pattern": ".windsurfrules",
    },
    "copilot_instructions": {
        "name": "GitHub Copilot",
        "description": "GitHub Copilot 项目级指令",
        "file_pattern": ".github/copilot-instructions.md",
    },
    "clinerules": {
        "name": "Cline Agent (.clinerules)",
        "description": "Cline Agent 规则格式",
        "file_pattern": ".clinerules",
    },
    "cursor_rules_dir": {
        "name": "Cursor Rules 目录",
        "description": "Cursor 新版 rules/*.md 目录格式",
        "file_pattern": "rules/*.md",
    },
    "generic_frontmatter": {
        "name": "通用 Frontmatter Markdown",
        "description": "任何带 YAML frontmatter 的 Markdown 文件",
        "file_pattern": "*.md (with frontmatter)",
    },
}
