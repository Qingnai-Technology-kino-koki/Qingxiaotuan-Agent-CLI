"""自定义 Agent 注册表 (对标 Claude Code 的 `.claude/agents/*.md`)。

Claude Code 允许把任何 Markdown 文件放到 ``.claude/agents/`` 目录, 用 frontmatter
声明名字/描述/工具约束, 正文作为该 agent 的系统提示。运行时可按名调用, 让子代理
"各司其职"。

青小团同样支持三层发现 (同名由更高层级覆盖, 项目 > 用户 > 内置):

- ``builtin`` : 内置 agents 目录 (见 :data:`BUILTIN_AGENTS_DIR`)
- ``project`` : 工作区 ``.claude/agents/*.md``
- ``user``    : 用户目录 ``.qingxiaotuan/agents/*.md``

每份定义解析极简 frontmatter (不引入 YAML 依赖, 覆盖 Claude Code 常用子集):

    ---
    name: my-agent
    description: 我是做什么的
    tools: Read, Grep, Glob, RunCommand   # 逗号分隔工具白名单
    exclude_tools: Write, Edit            # 额外排除
    model: sonnet                         # 可选模型偏好
    ---
    这里是 agent 的系统提示正文 (markdown)。

本模块是纯函数、可测的: 只负责「发现 + 解析 + 渲染」, 不触碰 Agent 运行时;
由 subagent 工具在按名派生子代理时消费。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    from importlib.resources import files as _pkg_files
except ImportError:  # pragma: no cover - py<3.9
    from importlib_resources import files as _pkg_files  # type: ignore

# 允许分配的层级权重: 值越大越优先 (消除同名歧义)。
_LAYER_WEIGHT = {"builtin": 1, "user": 2, "project": 3}

# 应用内置 agents 目录包名 (包内若存在 agents/*.md 则作为 builtin 层)。
BUILTIN_AGENTS_DIR = _pkg_files("qingxiaotuan") / "agents"

_FM_NAME_RE = re.compile(r"^\s*name\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FM_DESC_RE = re.compile(r"^\s*description\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FM_TOOLS_RE = re.compile(r"^\s*tools\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FM_EXCLUDE_RE = re.compile(r"^\s*exclude(?:_tools)?\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FM_MODEL_RE = re.compile(r"^\s*model\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FM_FILE_PATTERN_RE = re.compile(r"^\s*(?:file_pattern|file_patterns)\s*:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class AgentSpec:
    """一份解析后的自定义 agent 定义。"""

    name: str
    description: str
    body: str                                  # 系统提示正文 (markdown)
    tools: Tuple[str, ...] = ()                # 工具白名单 (空 = 继承全部可用)
    exclude_tools: Tuple[str, ...] = ()        # 额外排除的工具
    model: str = ""                            # 可选模型偏好
    file_patterns: Tuple[str, ...] = ()        # 可选: 适用文件名/目录约束
    source_kind: str = "builtin"               # builtin | user | project
    source_path: str = ""

    @property
    def system_extra(self) -> str:
        """渲染成注入子代理系统提示的文本 (角色定位 + 工具约束注解)。"""
        head = f"你的角色是 {self.name}。{self.description}" if self.description else f"你的角色是 {self.name}。"
        parts = [head.strip(), "", self.body.strip()]
        if self.tools:
            parts.append("\n可用工具 (仅限以下白名单): " + ", ".join(self.tools))
        if self.file_patterns:
            parts.append("适用文件 (优先处理这些, 其余仅按需): " + ", ".join(self.file_patterns))
        return "\n".join(parts)


# ---------------------------------------------------------------- frontmatter 解析

def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and (v[0] in "'\"" and v[-1] == v[0]):
        return v[1:-1].strip()
    return v


def _split_tools(v: str) -> Tuple[str, ...]:
    """解析单行逗号/空白分隔、或缩进 `- name` 列表块。"""
    out: List[str] = []
    for line in v.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        for piece in re.split(r"[,\s]+", line):
            if piece and piece not in out:
                out.append(piece)
    return tuple(out)


def _parse_frontmatter(text: str) -> Dict[str, str]:
    """抽取并用极简规则解析 markdown 开头的 ``---`` frontmatter 块。"""
    meta: Dict[str, str] = {}
    m = re.match(r"^\ufeff?---\r?\n", text)
    if not m:
        return meta
    end = re.search(r"\r?\n---\s*(?:\r?\n|$)", text[m.end():])
    if not end:
        return meta
    block = text[m.end():m.end() + end.start()]
    lines = block.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        colon = line.find(":")
        if colon < 0:
            i += 1
            continue
        key = line[:colon].strip().lower()
        first_val = line[colon + 1:].strip()
        # 多行值 (`|` / `>` 或后续缩进行)
        if first_val in ("|", ">"):
            acc = []
            i += 1
            while i < n and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                acc.append(lines[i].strip())
                i += 1
            meta[key] = "\n".join(acc).strip()
        elif first_val == "":
            # 值在下一行——尝试收集首行非空内容
            acc = []
            i += 1
            while i < n:
                nxt = lines[i].strip().lstrip("-").strip()
                if nxt:
                    acc.append(nxt)
                i += 1
                if acc and (i >= n or not (lines[i].startswith(" ") or lines[i].startswith("\t") or lines[i].startswith("-"))):
                    break
            meta[key] = " ".join(acc).strip()
        else:
            meta[key] = _unquote(first_val)
            i += 1
    return meta


# ---------------------------------------------------------------- markdown 定义解析

def parse_agent_md(
    text: str,
    *,
    source_kind: str = "user",
    source_path: str = "",
) -> AgentSpec:
    """把一份 agents/*.md 文本解析为 AgentSpec。

    名字缺失时回退到文件名 (source_path 的 stem); 全部缺失返回 name="".
    """
    meta = _parse_frontmatter(text)
    body = _extract_body(text)

    name = _unquote(meta.get("name", "")).strip()
    if not name and source_path:
        name = Path(source_path).stem
    desc = _unquote(meta.get("description", "")).strip()
    model = _unquote(meta.get("model", "")).strip()

    tools = _split_tools(meta.get("tools", ""))
    exclude = _split_tools(meta.get("exclude_tools", "") or meta.get("exclude", ""))
    patterns = _split_tools(meta.get("file_pattern", "") or meta.get("file_patterns", ""))

    return AgentSpec(
        name=name,
        description=desc,
        body=body,
        tools=tools,
        exclude_tools=exclude,
        model=model,
        file_patterns=patterns,
        source_kind=source_kind,
        source_path=source_path,
    )


def _extract_body(text: str) -> str:
    """去掉 frontmatter 块, 返回正文 markdown。"""
    m = re.match(r"^\ufeff?---\r?\n", text)
    if not m:
        return text.strip()
    end = re.search(r"\r?\n---\s*(?:\r?\n|$)", text[m.end():])
    if not end:
        return text.strip()
    return text[m.end() + end.end():].strip()


# ---------------------------------------------------------------- 发现

def _files_in(dir_path: Path) -> List[Path]:
    if not dir_path or not dir_path.exists() or not dir_path.is_dir():
        return []
    return sorted(p for p in dir_path.glob("*.md") if p.is_file())


def discover_agents(
    workspace: Optional[str] = None,
    home: Optional[str] = None,
    *,
    _builtin_dir: Optional[Path] = None,
    _claude_user_dir: Optional[Path] = None,
) -> Dict[str, AgentSpec]:
    """按三层扫描命名 agent, 返回 ``{name: AgentSpec}``。

    优先级 project > user > builtin: 高海拔成熟同名覆盖低层。扫描失败跳过低层,
    永不因某个目录异常而整体失败。
    """
    layers: List[Tuple[int, Path, str]] = []
    bdir = _builtin_dir or Path(str(BUILTIN_AGENTS_DIR))
    if bdir is not None:
        layers.append((_LAYER_WEIGHT["builtin"], bdir, "builtin"))
    if home:
        # 用户层: 自家 <home>/agents 与 Claude Code 标准 ~/.claude/agents 都要扫,
        # 使从 Claude Code 迁移的 agent 开箱即用。目录不存在时 _files_in 返回 []。
        qxt_dir = Path(home) / "agents"
        claude_dir = _claude_user_dir if _claude_user_dir is not None else Path.home() / ".claude" / "agents"
        layers.append((_LAYER_WEIGHT["user"], qxt_dir, "user"))
        if claude_dir != qxt_dir:
            layers.append((_LAYER_WEIGHT["user"], claude_dir, "user"))
    if workspace:
        layers.append((_LAYER_WEIGHT["project"], Path(workspace) / ".claude" / "agents", "project"))

    result: Dict[str, AgentSpec] = {}
    for weight, d, kind in sorted(layers, key=lambda t: t[0]):
        try:
            files = _files_in(d)
        except OSError:
            continue
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            spec = parse_agent_md(text, source_kind=kind, source_path=str(p))
            if not spec.name:
                continue
            result[spec.name] = spec  # 高层覆盖低层
    return result


def load_agent(
    name: str,
    workspace: Optional[str] = None,
    home: Optional[str] = None,
    *,
    _builtin_dir: Optional[Path] = None,
    _claude_user_dir: Optional[Path] = None,
) -> Optional[AgentSpec]:
    """按名取一份 agent 定义 (不存在返回 None)。"""
    return discover_agents(
        workspace, home,
        _builtin_dir=_builtin_dir, _claude_user_dir=_claude_user_dir,
    ).get(name)


# ---------------------------------------------------------------- 辅助

def format_agent_list(agents: Dict[str, AgentSpec]) -> str:
    """把发现的 agent 渲染成可打印清单 (供 agents_list 工具返回)。"""
    if not agents:
        return "(未发现任何自定义 agent)"
    lines = []
    for name in sorted(agents):
        a = agents[name]
        tags = [a.source_kind]
        if a.tools:
            tags.append(f"{len(a.tools)}工具")
        if a.model:
            tags.append(a.model)
        desc = a.description or ""
        lines.append(f"  {name:24} [{', '.join(tags)}]  {desc[:60]}")
    return "\n".join(lines)


__all__ = [
    "AgentSpec", "discover_agents", "load_agent", "parse_agent_md",
    "format_agent_list", "BUILTIN_AGENTS_DIR",
]