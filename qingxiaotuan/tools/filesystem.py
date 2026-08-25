"""文件系统工具插件: 读 / 写 / 改 / 移动 / 删除 / 列目录 / 搜索 / glob。

补充的 Agent 核心能力 (对比原版):
- glob: 按通配符批量定位文件 (Agent 探索仓库的常用入口)。
- move_file / rename: 重命名与移动 (跨文件重构必备)。
- delete_file: 删除单文件 (危险, 需确认; YOLO 下自动批准)。
write_file 本身在 YOLO 下属于高风险写入, 也标记为 dangerous 以便统一管控。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop

MAX_READ_CHARS = 40000


def _resolve(ctx: ToolContext, path: str) -> Path:
    workspace = Path(ctx.workspace).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"路径必须位于工作区内: {path}") from exc
    return resolved


def _rel(ctx: ToolContext, p: Path) -> str:
    try:
        return str(p.relative_to(ctx.workspace))
    except ValueError:
        return str(p)


def glob(ctx: ToolContext, pattern: str, path: str = ".") -> str:
    """按 glob 通配符匹配文件 (如 '**/*.py', 'src/**/*.ts')。"""
    root = _resolve(ctx, path)
    if not root.exists():
        return f"[错误] 目录不存在: {root}"
    matches = sorted(p for p in root.glob(pattern) if p.is_file())
    if not matches:
        return f"未匹配到文件: {pattern}"
    lines = []
    for m in matches[:200]:
        lines.append(_rel(ctx, m))
    if len(matches) > 200:
        lines.append(f"...[共 {len(matches)} 个, 截断显示前 200]")
    return "\n".join(lines)


def move_file(ctx: ToolContext, src: str, dst: str) -> str:
    """移动或重命名文件/目录。"""
    s = _resolve(ctx, src)
    d = _resolve(ctx, dst)
    if not s.exists():
        return f"[错误] 源不存在: {s}"
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return f"已移动 {_rel(ctx, s)} -> {_rel(ctx, d)}"


def delete_file(ctx: ToolContext, path: str) -> str:
    """删除单个文件 (危险操作, 需确认)。"""
    p = _resolve(ctx, path)
    if not p.exists():
        return f"[错误] 文件不存在: {p}"
    if p.is_dir():
        return f"[错误] {p} 是目录, 请用 delete_dir"
    p.unlink()
    return f"已删除 {_rel(ctx, p)}"


def delete_dir(ctx: ToolContext, path: str) -> str:
    """递归删除整个目录 (极危险, 需确认, YOLO 仍建议保留红名单)。"""
    import shutil as _shutil

    p = _resolve(ctx, path)
    if not p.exists():
        return f"[错误] 目录不存在: {p}"
    if not p.is_dir():
        return f"[错误] {p} 不是目录"
    _shutil.rmtree(str(p))
    return f"已递归删除目录 {_rel(ctx, p)}"


def read_file(ctx: ToolContext, path: str, offset: int = 0, limit: int = 0) -> str:
    p = _resolve(ctx, path)
    if not p.exists():
        return f"[错误] 文件不存在: {p}"
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if offset:
        lines = lines[offset:]
    if limit:
        lines = lines[:limit]
    out = "\n".join(lines)
    if len(out) > MAX_READ_CHARS:
        out = out[:MAX_READ_CHARS] + f"\n...[截断, 全文 {len(text)} 字符]"
    return f"# {p}\n{out}"


def write_file(ctx: ToolContext, path: str, content: str) -> str:
    p = _resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p} ({len(content)} 字符)"


def edit_file(ctx: ToolContext, path: str, old_string: str, new_string: str) -> str:
    p = _resolve(ctx, path)
    if not p.exists():
        return f"[错误] 文件不存在: {p}"
    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        return "[错误] old_string 在文件中未找到"
    if count > 1:
        return f"[错误] old_string 出现 {count} 次, 请提供更多上下文使其唯一"
    p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    return f"已修改 {p}"


def list_dir(ctx: ToolContext, path: str = ".", depth: int = 2) -> str:
    root = _resolve(ctx, path)
    if not root.is_dir():
        return f"[错误] 目录不存在: {root}"
    lines: list[str] = []
    max_depth = min(depth, 4)
    for current, dirs, files in os.walk(root):
        rel = Path(current).relative_to(root)
        level = len(rel.parts)
        if level >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and d != "__pycache__"]
        indent = "  " * level
        name = "." if str(rel) == "." else rel.name + "/"
        lines.append(f"{indent}{name}")
        for f in sorted(files)[:50]:
            lines.append(f"{indent}  {f}")
        if len(lines) > 400:
            lines.append("...[截断]")
            break
    return "\n".join(lines)


def search_files(ctx: ToolContext, pattern: str, path: str = ".") -> str:
    import re

    root = _resolve(ctx, path)
    if not root.is_dir():
        return f"[错误] 目录不存在: {root}"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"[错误] 正则表达式无效: {exc}"
    hits: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__" and d != "node_modules"]
        for fname in files:
            fpath = Path(current) / fname
            try:
                for i, line in enumerate(fpath.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if regex.search(line):
                        hits.append(f"{fpath.relative_to(root)}:{i}: {line.strip()[:160]}")
                        if len(hits) >= 100:
                            return "\n".join(hits) + "\n...[超过 100 条, 截断]"
            except OSError:
                continue
    return "\n".join(hits) if hits else "未找到匹配内容"


class FilesystemPlugin(Plugin):
    name = "tools.filesystem"
    provides = []
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="read_file",
            description="读文件内容, 大文件用 offset/limit 分段",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("文件路径"),
                    "offset": {"type": "integer", "description": "起始行 (可选)"},
                    "limit": {"type": "integer", "description": "读取行数 (可选)"},
                },
                "required": ["path"],
            },
            handler=read_file, group="filesystem", read_only=True,
        ))
        registry.register(Tool(
            name="write_file",
            description="创建或覆盖文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("文件路径"),
                    "content": string_prop("完整内容"),
                },
                "required": ["path", "content"],
            },
            handler=write_file, group="filesystem", dangerous=True,
        ))
        registry.register(Tool(
            name="edit_file",
            description="精确替换文本, old_string 须唯一",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("文件路径"),
                    "old_string": string_prop("被替换原文"),
                    "new_string": string_prop("新文本"),
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=edit_file, group="filesystem", dangerous=True,
        ))
        registry.register(Tool(
            name="list_dir",
            description="树形列出目录",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("目录, 默认工作区"),
                    "depth": {"type": "integer", "description": "深度, 默认 2"},
                },
                "required": [],
            },
            handler=list_dir, group="filesystem", read_only=True,
        ))
        registry.register(Tool(
            name="search_files",
            description="正则搜索文件内容",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": string_prop("正则"),
                    "path": string_prop("根目录, 默认工作区"),
                },
                "required": ["pattern"],
            },
            handler=search_files, group="filesystem", read_only=True,
        ))
        registry.register(Tool(
            name="glob",
            description="按通配符批量定位文件, 如 '**/*.py'",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": string_prop("glob 模式"),
                    "path": string_prop("根目录, 默认工作区"),
                },
                "required": ["pattern"],
            },
            handler=glob, group="filesystem", read_only=True,
        ))
        registry.register(Tool(
            name="move_file",
            description="移动或重命名文件/目录",
            parameters={
                "type": "object",
                "properties": {
                    "src": string_prop("源路径"),
                    "dst": string_prop("目标路径"),
                },
                "required": ["src", "dst"],
            },
            handler=move_file, group="filesystem", dangerous=True,
        ))
        registry.register(Tool(
            name="delete_file",
            description="删除单个文件 (不可逆, 需确认)",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("文件路径"),
                },
                "required": ["path"],
            },
            handler=delete_file, group="filesystem", dangerous=True,
        ))
        registry.register(Tool(
            name="delete_dir",
            description="递归删除整个目录 (极危险, 需确认)",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("目录路径"),
                },
                "required": ["path"],
            },
            handler=delete_dir, group="filesystem", dangerous=True,
        ))
