"""代码依赖图工具：生成代码依赖关系的可视化图谱。

能力:
- 生成模块依赖图
- 生成符号依赖图
- 支持多种输出格式 (DOT, Mermaid, JSON)
- 帮助用户理解代码库架构和依赖关系
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop


def _get_python_files(ctx: ToolContext, path: str = ".") -> List[Path]:
    """获取指定路径下的所有Python文件。"""
    root = Path(ctx.workspace) / path
    if not root.exists():
        return []
    
    files: List[Path] = []
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__",
                 ".tox", ".mypy_cache", ".pytest_cache", "dist", "build"}
    
    for current, dirs, filenames in os.walk(root):
        # 跳过不需要的目录
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(current) / fname)
    
    return files


def _extract_imports(file_path: Path) -> List[str]:
    """从Python文件中提取导入的模块。"""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return []
    
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # 如果语法解析失败，尝试使用正则表达式作为后备方案
        imports = []
        lines = content.splitlines()
        for line in lines:
            line = line.strip()
            if line.startswith("import ") or line.startswith("from "):
                # 简单处理import语句
                if line.startswith("import "):
                    modules = line[7:].split(",")
                    for module in modules:
                        module = module.strip().split()[0]  # 取第一部分（可能有as）
                        if module:
                            imports.append(module)
                elif line.startswith("from "):
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "from":
                        module = parts[1]
                        if module and not module.startswith("."):
                            imports.append(module)
        return imports
    
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:  # 可能为None（如from . import something）
                imports.append(node.module)
    
    return imports


def _build_dependency_graph(files: List[Path], workspace: Path) -> Dict[str, List[str]]:
    """构建依赖图。"""
    graph: Dict[str, List[str]] = {}
    
    for file_path in files:
        try:
            rel_path = str(file_path.relative_to(workspace))
        except ValueError:
            # 如果文件不在工作区内，跳过
            continue
        
        imports = _extract_imports(file_path)
        # 将导入转换为相对路径或保持为模块名
        graph[rel_path] = imports
    
    return graph


def _generate_dot(graph: Dict[str, List[str]]) -> str:
    """生成DOT格式的依赖图。"""
    lines = ["digraph CodeDependency {", "    rankdir=LR;", "    node [shape=box];"]
    
    # 添加节点
    for file_path in graph.keys():
        # 简化节点名称，仅保留文件名
        node_name = Path(file_path).name.replace(".py", "")
        # 转义特殊字符
        safe_name = node_name.replace("-", "_").replace(".", "_")
        lines.append(f'    "{safe_name}" [label="{node_name}"];')
    
    # 添加边
    for file_path, imports in graph.items():
        from_name = Path(file_path).name.replace(".py", "")
        from_safe = from_name.replace("-", "_").replace(".", "_")
        
        for imp in imports:
            # 尝试将导入转换为文件路径
            to_file = None
            # 检查是否是相对导入
            if imp.replace(".", "/").endswith(".py"):
                to_file = Path(imp)
            else:
                # 尝试添加.py后缀
                to_file = Path(f"{imp.replace('.', '/')}.py")
            
            # 查找匹配的文件
            found = False
            for file_path in graph.keys():
                if str(Path(file_path).name) == str(to_file.name):
                    to_name = Path(file_path).name.replace(".py", "")
                    to_safe = to_name.replace("-", "_").replace(".", "_")
                    lines.append(f'    "{from_safe}" -> "{to_safe}";')
                    found = True
                    break
            
            # 如果没有找到对应的文件，创建一个外部依赖节点
            if not found:
                imp_safe = imp.replace("-", "_").replace(".", "_")
                lines.append(f'    "{imp_safe}" [label="{imp}", shape=ellipse, style=dashed];')
                lines.append(f'    "{from_safe}" -> "{imp_safe}";')
    
    lines.append("}")
    return "\n".join(lines)


def _generate_mermaid(graph: Dict[str, List[str]]) -> str:
    """生成Mermaid格式的依赖图。"""
    lines = ["graph LR"]
    
    # 添加节点和边
    for file_path, imports in graph.items():
        from_name = Path(file_path).name.replace(".py", "")
        from_safe = from_name.replace("-", "_").replace(".", "_")
        
        # 确保节点被定义
        lines.append(f'    {from_safe}["{from_name}"]')
        
        for imp in imports:
            # 尝试将导入转换为文件路径
            to_file = None
            if imp.replace(".", "/").endswith(".py"):
                to_file = Path(imp)
            else:
                to_file = Path(f"{imp.replace('.', '/')}.py")
            
            # 查找匹配的文件
            found = False
            for file_path2 in graph.keys():
                if str(Path(file_path2).name) == str(to_file.name):
                    to_name = Path(file_path2).name.replace(".py", "")
                    to_safe = to_name.replace("-", "_").replace(".", "_")
                    lines.append(f'    {from_safe} --> {to_safe}["{to_name}"]')
                    found = True
                    break
            
            # 如果没有找到对应的文件，创建一个外部依赖节点
            if not found:
                imp_safe = imp.replace("-", "_").replace(".", "_")
                lines.append(f'    {imp_safe}["{imp}"]')
                lines.append(f'    {from_safe} --> {imp_safe}')
    
    return "\n".join(lines)


def code_dependency_graph(
    ctx: ToolContext,
    path: str = ".",
    format: str = "dot",
    max_nodes: int = 50
) -> str:
    """生成代码依赖图。
    
    Args:
        ctx: 工具上下文
        path: 要分析的路径（相对于工作区），默认为工作区根目录
        format: 输出格式，可选 "dot", "mermaid", "json"
        max_nodes: 最大节点数，防止图过大
        
    Returns:
        依赖图的字符串表示
    """
    # 获取Python文件
    files = _get_python_files(ctx, path)
    
    if not files:
        return "[提示] 未找到Python文件"
    
    # 限制文件数量以防止图过大
    if len(files) > max_nodes:
        files = files[:max_nodes]
        note = f"\n[注意: 仅显示前 {max_nodes} 个文件，共找到 {len(_get_python_files(ctx, path))} 个文件]"
    else:
        note = ""
    
    # 构建依赖图
    graph = _build_dependency_graph(files, Path(ctx.workspace))
    
    # 生成指定格式的输出
    if format == "dot":
        result = _generate_dot(graph)
    elif format == "mermaid":
        result = _generate_mermaid(graph)
    elif format == "json":
        result = json.dumps(graph, indent=2, ensure_ascii=False)
    else:
        return f"[错误] 不支持的格式: {format}。支持的格式: dot, mermaid, json"
    
    return result + note


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


class CodeGraphPlugin(Plugin):
    name = "tools.code_graph"
    requires = ["tool_registry"]

    def activate(self, kernel: Kernel) -> None:
        registry = kernel.require("tool_registry")
        registry.register(Tool(
            name="code_dependency_graph",
            description="生成代码依赖关系图，支持DOT、Mermaid和JSON格式",
            parameters={
                "type": "object",
                "properties": {
                    "path": string_prop("要分析的路径 (相对于工作区), 默认工作区"),
                    "format": {"type": "string", "description": "输出格式: dot, mermaid, json (默认: dot)"},
                    "max_nodes": {"type": "integer", "description": "最大节点数 (默认: 50)"},
                },
                "required": [],
            },
            handler=code_dependency_graph, group="code", read_only=True,
        ))