"""代码理解工具插件 —— 让 Agent 真正"懂"一个仓库, 而不仅是逐文件读。

能力:
- codebase_map: 当前仓库的结构/规模/关键文件地图 (大上下文机制的入口)。
- find_symbol: 跨文件定位符号定义 (函数/类/变量/导出)。
- find_references: 追踪符号被谁引用 (跨文件依赖)。
- run_tests: 自动探测测试框架并运行, 返回通过/失败结果 (自测闭环)。
- git_status: 只读地感知当前改动 (安全, 不写)。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, cast

from ..context.indexer import CodebaseIndexer
from ..core.kernel import Kernel, Plugin
from ..ext.safety_engine import (has_force_push, has_recursive_rm,
                                 has_win_recursive_delete)
from .base import Tool, ToolContext, string_prop


def _indexer(ctx: ToolContext):
    """返回已构建的 IndexResult (含 map_text)。已构建结果会缓存在内核, 避免重复扫描。"""
    cached = ctx.kernel.get("codebase_indexer")
    if cached is not None:
        return cached
    ws = ctx.config("context.index_max_files", 300)
    ml = ctx.config("context.index_max_loc", 200_000)
    idx = CodebaseIndexer(ctx.workspace, max_files=ws, max_loc=ml)
    res = idx.build()
    # 缓存 IndexResult 到内核服务表, 后续 codebase_map 调用直接复用, 不再重复扫描大仓库。
    # IndexerPlugin 激活时会先占位注册 codebase_indexer=None, 需先 unprovide 才能覆盖占位
    # (否则 ServiceContainer.provide 会因"服务已被提供"抛 PluginError)。
    ctx.kernel.unprovide("codebase_indexer")
    ctx.kernel.provide("codebase_indexer", res)
    return res


def codebase_map(ctx: ToolContext, max_tree_lines: int = 80) -> str:
    res = _indexer(ctx)
    extra = ""
    if ctx.config("context.pin_codebase", True):
        extra = "\n(系统提示里已包含同款地图, 此工具用于获取最新/更完整的视图)\n"
    return cast(str, res.map_text(max_tree_lines=max_tree_lines)) + extra


# 定义位置的常见模式
_DEF_PATTERNS = [
    r"^\s*(?:export\s+)?(?:async\s+)?def\s+{s}\s*\(",
    r"^\s*(?:export\s+)?class\s+{s}\s*[\(:]",
    r"^\s*(?:export\s+)?(?:const|let|var|function)\s+{s}\b",
    r"^\s*(?:export\s+)?interface\s+{s}\b",
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn|func)\s+{s}\s*\(",
    r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?(?:final\s+)?[\w<>\[\],\s]+\s+{s}\s*\(",
    r"^\s*{s}\s*[:=]\s",                          # 变量赋值 (python/go/ts)
    r"^\s*(?:func|function)\s+{s}\s*\(",
]


def find_symbol(ctx: ToolContext, symbol: str, path: str = ".") -> str:
    root = _resolve(ctx, path)
    if not root.exists():
        return f"[错误] 路径不存在: {root}"
    compiled = [re.compile(p.format(s=re.escape(symbol))) for p in _DEF_PATTERNS]
    hits: List[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for fname in files:
            fpath = Path(current) / fname
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if any(p.search(line) for p in compiled):
                            rel = str(fpath.relative_to(root))
                            hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                            if len(hits) >= 60:
                                return "\n".join(hits) + "\n...[超过 60 处, 截断]"
            except OSError:
                continue
    return "\n".join(hits) if hits else f"未找到符号 '{symbol}' 的定义"


def find_references(ctx: ToolContext, symbol: str, path: str = ".") -> str:
    root = _resolve(ctx, path)
    if not root.exists():
        return f"[错误] 路径不存在: {root}"
    # 引用 = 出现该标识符且不是定义行的位置
    compiled = [re.compile(p.format(s=re.escape(symbol))) for p in _DEF_PATTERNS]
    hits: List[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for fname in files:
            fpath = Path(current) / fname
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if symbol in line and not any(p.search(line) for p in compiled):
                            rel = str(fpath.relative_to(root))
                            hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                            if len(hits) >= 80:
                                return "\n".join(hits) + "\n...[超过 80 处, 截断]"
            except OSError:
                continue
    return "\n".join(hits) if hits else f"未找到 '{symbol}' 的引用"


def run_tests(ctx: ToolContext, command: str = "") -> str:
    """自动探测并运行测试。给定 command 时直接执行, 否则按语言探测。"""
    root = Path(ctx.workspace)
    if command:
        return _run(ctx, command)
    # 探测测试框架 (合并当前目录与子目录的测试文件, 避免 or 短路漏报)
    test_py = (
        list(root.glob("test_*.py"))
        + list(root.glob("*_test.py"))
        + list(root.rglob("test_*.py"))
        + list(root.rglob("*_test.py"))
    )
    if (root / "package.json").exists():
        pkg = (root / "package.json").read_text(encoding="utf-8", errors="replace")
        if '"test"' in pkg or '"vitest"' in pkg or '"jest"' in pkg:
            return _run(ctx, "npm test")
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() \
            or (root / "tests").is_dir() or (root / "test").is_dir() or test_py:
        return _run(ctx, "python -m pytest -q")
    if (root / "go.mod").exists():
        return _run(ctx, "go test ./...")
    if (root / "Cargo.toml").exists():
        return _run(ctx, "cargo test")
    if list(root.glob("*.csproj")) or list(root.glob("*.sln")):
        return _run(ctx, "dotnet test")
    return "[提示] 未探测到测试框架。可显式传入 command, 例如 run_tests command=\"pytest tests/\"。"


def git_status(ctx: ToolContext) -> str:
    return _run(ctx, "git status --short", timeout=15) + "\n" + _run(ctx, "git diff --stat", timeout=15)


def git_diff(ctx: ToolContext, target: str = "", staged: bool = False, stat: bool = True) -> str:
    """查看 git 差异 (只读)。

    - target 为空且 staged=False: 工作区相对暂存区的改动 (git diff)。
    - staged=True: 暂存区相对 HEAD 的改动 (git diff --cached)。
    - target 非空: 视作 git diff 参数, 如 "HEAD~3"、"main..HEAD"、"abc..def"。
    """
    if target.strip():
        cmd = f"git diff {target.strip()}"
        if stat:
            cmd += " --stat"
    elif staged:
        cmd = "git diff --cached" + (" --stat" if stat else "")
    else:
        cmd = "git diff" + (" --stat" if stat else "")
    return _run(ctx, cmd, timeout=20)


def git_log(ctx: ToolContext, max_count: int = 20, stat: bool = False, oneline: bool = True) -> str:
    """查看提交历史 (只读)。"""
    try:
        n = max(1, min(int(max_count), 200))
    except (TypeError, ValueError):
        n = 20
    fmt = " --oneline" if oneline else ""
    st = " --stat" if stat else ""
    return _run(ctx, f"git log{fmt} -n {n}{st}", timeout=20)


def doctor(ctx: ToolContext) -> str:
    """自检 Agent 运行健康度: 已注册工具数/分组、当前 Loop、架构服务、模型配置。

    用于排查"模型调用 / 工具不可调用"类问题 —— 不止安全模块能打, 开发代码与
    模型链路也应可观测、可诊断。
    """
    lines: List[str] = []
    # 1) 工具注册表
    reg = ctx.kernel.get("tool_registry")
    if reg is not None:
        tools = list(getattr(reg, "tools", []))
        by_group: dict = {}
        for t in tools:
            g = getattr(t, "group", "misc") or "misc"
            by_group.setdefault(g, []).append(getattr(t, "name", "?"))
        lines.append(f"已注册工具: {len(tools)} 个")
        for g in sorted(by_group):
            names = by_group[g]
            shown = ", ".join(names[:12]) + (f" …(+{len(names) - 12})" if len(names) > 12 else "")
            lines.append(f"  - {g}: {shown}")
    else:
        lines.append("工具注册表: 缺失 (!)")
    # 2) 当前 Loop
    lp = ctx.kernel.get("loop_provider")
    lines.append(f"LoopProvider: {'已注册' if lp is not None else '缺失'} "
                 f"({type(lp).__name__ if lp else '-'})")
    # 3) 五层架构服务 (arch.* 是否就位)
    arch_services = ["arch.security", "arch.execution", "arch.orchestration",
                     "arch.context", "arch.observability"]
    present = [s for s in arch_services if ctx.kernel.get(s) is not None]
    lines.append(f"架构服务: {len(present)}/{len(arch_services)}"
                 + (f" ({', '.join(present)})" if present else ""))
    # 4) 模型配置
    model = ctx.kernel.get("model")
    if model is not None:
        prov = (getattr(model, "provider", None)
                or getattr(model, "model_name", None)
                or type(model).__name__)
        lines.append(f"模型: {prov}")
    else:
        lines.append("模型: 未在内核中 (可能延迟初始化)")
    return "\n".join(lines)


def _open_in_editor(fpath: Path, line: int, editor: str = "") -> str:
    """在编辑器/默认应用中打开文件并定位到行。

    优先级: 配置的编辑器 (ui.editor) > 自动探测 VSCode `code` > 系统默认应用。
    """
    loc = f"{fpath}:{line}" if line else str(fpath)
    import shutil

    def _try(editor_cmd: str, goto: bool) -> Optional[str]:
        """尝试用 editor_cmd 打开; 成功返回描述, 失败返回 None。"""
        try:
            if goto:
                subprocess.Popen([editor_cmd, "--goto", loc],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"已在 {Path(editor_cmd).stem} 打开: {loc}"
            subprocess.Popen([editor_cmd, str(fpath)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"已用 {Path(editor_cmd).stem} 打开: {loc}"
        except OSError:
            return None

    # 1) 显式配置的编辑器 (支持完整路径或 PATH 命令)
    if editor:
        editor = editor.strip()
        if editor:
            resolved = editor if os.path.isabs(editor) else shutil.which(editor)
            if resolved:
                # 带 --goto 的编辑器 (VSCode 系) 优先; 失败回退到不带参数打开
                got = _try(resolved, goto=True) or _try(resolved, goto=False)
                if got:
                    return got
    # 2) 自动探测 VSCode
    code = shutil.which("code")
    if code:
        got = _try(code, goto=True)
        if got:
            return got
    # 3) 系统默认应用
    try:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", str(fpath)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(fpath)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(fpath)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return f"已用默认应用打开: {fpath}" + (f" (第 {line} 行)" if line else "")
    except OSError:
        return f"无法打开编辑器, 请手动打开: {loc}"


def open_file(ctx: ToolContext, path: str, line: int = 0) -> str:
    """在编辑器/默认应用中打开文件并定位到指定行 (精确引用跳转)。"""
    fpath = _resolve(ctx, path)
    if not fpath.exists():
        return f"[错误] 文件不存在: {fpath}"
    if line and line < 1:
        return "[错误] line 必须 >= 1"
    editor = ctx.config("ui.editor", "") if hasattr(ctx, "config") else ""
    return _open_in_editor(fpath, line, editor)


# 破坏性命令红名单: 即便 YOLO 模式自动批准, 这些也绝不经由只读/自测工具执行,
# 必须由用户显式走确认路径 (shell 工具/危险工具) 才能跑, 防误炸工作区或远端。
_DANGEROUS_PATTERNS = (
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b",  # rm -rf / rm -fr
    r"\brm\s+-[a-zA-Z]*\s+/",                                    # rm -r /
    r"\bmkfs\b",
    r"\bdd\b\s+if=",
    r"git\s+push\s+(--force|-f)\b",
    r"git\s+reset\s+--hard\b",
    r"git\s+clean\s+-[a-zA-Z]*[fd]",
    r":\(\)\s*\{\s*:\|:&\s*\}",                                  # fork 炸弹
    r">\s*/dev/sd[a-z]",
)


def _looks_dangerous(command: str) -> bool:
    """破坏性判定: 正则名单 + safety 引擎 token 化红线 (覆盖 rm -r -f / push -f 等变体)。"""
    c = command.strip()
    if c.startswith("sudo ") or c.startswith("su "):
        return True
    if has_recursive_rm(c) or has_force_push(c) or has_win_recursive_delete(c):
        return True
    return any(p.search(c) for p in (re.compile(p) for p in _DANGEROUS_PATTERNS))


def _run(ctx: ToolContext, command: str, timeout: int = 120) -> str:
    # 只读/自测工具里夹带破坏性命令: 直接拒绝, 不进 shell。
    if _looks_dangerous(command):
        return (
            f"[拒绝] 命令含破坏性操作, 不允许在 run_tests/git_status 内执行: {command}\n"
            "如需执行, 请走显式确认路径 (shell 危险工具)。"
        )
    limit = timeout or ctx.config("tools.shell.timeout", 60)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=ctx.workspace, capture_output=True,
            text=True, timeout=limit, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"[超时] 命令在 {limit}s 内未完成: {command}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > 6000:
        out = out[:6000] + "\n...[截断]"
    return f"$ {command}\nexit={proc.returncode}\n{out.strip()}"


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


_SKIP = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", "target", "out",
}


class CodeToolPlugin(Plugin):
    name = "tools.code"
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="codebase_map",
            description="代码库结构树/规模/关键入口, 理解全貌",
            parameters={
                "type": "object",
                "properties": {
                    "max_tree_lines": {"type": "integer", "description": "目录树行数, 默认 80"},
                },
                "required": [],
            },
            handler=codebase_map, group="code", read_only=True,
        ))
        registry.register(Tool(
            name="find_symbol",
            description="跨文件定位符号定义, 返回 file:line",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": string_prop("符号名 (不含参数)"),
                    "path": string_prop("根目录, 默认工作区"),
                },
                "required": ["symbol"],
            },
            handler=find_symbol, group="code", read_only=True,
        ))
        registry.register(Tool(
            name="find_references",
            description="追踪符号被哪些地方引用, 分析依赖",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": string_prop("符号名"),
                    "path": string_prop("根目录, 默认工作区"),
                },
                "required": ["symbol"],
            },
            handler=find_references, group="code", read_only=True,
        ))
        registry.register(Tool(
            name="run_tests",
            description="探测并运行测试 (pytest/npm/git/cargo/dotnet)",
            parameters={
                "type": "object",
                "properties": {
                    "command": string_prop("显式命令, 留空自动探测"),
                },
                "required": [],
            },
            handler=run_tests, group="code",
        ))
        registry.register(Tool(
            name="git_status",
            description="只读查看 git 改动 (status+diff)",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=git_status, group="code", read_only=True,
        ))
        registry.register(Tool(
            name="open_file",
            description="在编辑器/默认应用中打开文件并定位到行 (精确引用跳转, 供开发者查看)",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("文件路径 (相对于工作区)"),
                    "line": {"type": "integer", "description": "目标行号, 默认 0=文件开头"},
                },
                "required": ["path"],
            },
            handler=open_file, group="code", read_only=True,
        ))
        registry.register(Tool(
            name="git_diff",
            description="查看 git 差异 (只读): 未暂存改动 / 已暂存改动(--cached) / 指定 ref 范围",
            parameters={
                "type": "object",
                "properties": {
                    "target": string_prop("git diff 参数, 如 'HEAD~3' / 'main..HEAD' / 'a..b', 留空看未暂存改动"),
                    "staged": {"type": "boolean", "description": "true=查看已暂存改动 (git diff --cached)"},
                    "stat": {"type": "boolean", "description": "是否附带 --stat 统计, 默认 true"},
                },
                "required": [],
            },
            handler=git_diff, group="code", read_only=True,
        ))
        registry.register(Tool(
            name="git_log",
            description="查看提交历史 (只读), 支持条数与统计",
            parameters={
                "type": "object",
                "properties": {
                    "max_count": {"type": "integer", "description": "最大条数, 默认 20, 上限 200"},
                    "stat": {"type": "boolean", "description": "是否附带 --stat, 默认 false"},
                    "oneline": {"type": "boolean", "description": "是否单行精简输出, 默认 true"},
                },
                "required": [],
            },
            handler=git_log, group="code", read_only=True,
        ))
        registry.register(Tool(
            name="doctor",
            description="自检 Agent 健康度: 工具数/分组、Loop、架构服务、模型配置, 排查模型调用问题",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=doctor, group="agent", read_only=True,
        ))
