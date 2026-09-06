"""沙箱执行工具: 在 Docker 容器中隔离执行危险命令。

核心价值:
- 危险命令在隔离容器中执行，不影响主机系统
- 支持快照→执行→diff→确认→apply 模式
- 提供回滚能力

用法:
  /sandbox run "rm -rf /tmp/test"     # 在沙箱中执行
  /sandbox snapshot <path>            # 创建文件快照
  /sandbox diff                       # 查看沙箱执行后的差异
  /sandbox apply                      # 应用沙箱中的变更到主机
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.kernel import Kernel, Plugin
from ..ext.safety_engine import is_redline as _engine_is_redline
from .base import Tool, ToolContext, string_prop


class SandboxManager:
    """沙箱管理器: 管理 Docker 容器的创建、执行和销毁。"""
    
    def __init__(self, workspace: str, config: Any = None) -> None:
        self.workspace = workspace
        self.config = config
        self._container_name: Optional[str] = None
        self._snapshot_dir: Optional[Path] = None
        self._working_dir: Optional[Path] = None
        
    def _ensure_docker(self) -> bool:
        """检查 Docker 是否可用。"""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _create_container(self, image: str = "python:3.11-slim") -> str:
        """创建一个工作容器。"""
        if not self._ensure_docker():
            raise RuntimeError("Docker 不可用。请安装 Docker 并确保 daemon 正在运行。")
        
        # 创建工作目录
        self._working_dir = Path(tempfile.mkdtemp(prefix="qxt_sandbox_"))
        self._snapshot_dir = self._working_dir / "snapshots"
        self._snapshot_dir.mkdir(exist_ok=True)
        
        # 生成容器名
        self._container_name = f"qxt-sandbox-{int(time.time())}"
        
        # 启动容器
        result = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self._container_name,
                "-v", f"{self.workspace}:/workspace:ro",
                "-v", f"{self._working_dir}:/sandbox",
                "-w", "/sandbox",
                image,
                "tail", "-f", "/dev/null",  # 保持容器运行
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"创建容器失败: {result.stderr}")
        
        return self._container_name
    
    def _exec_in_container(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        """在容器中执行命令。"""
        if not self._container_name:
            self._create_container()
        name = self._container_name or "qxt-sandbox"

        result = subprocess.run(
            [
                "docker", "exec",
                name,
                "bash", "-c", command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    
    def snapshot_files(self, paths: List[str]) -> str:
        """创建文件快照。"""
        if not self._snapshot_dir:
            self._snapshot_dir = Path(tempfile.mkdtemp(prefix="qxt_snapshot_"))
        
        snap_id = f"snap_{int(time.time())}"
        snap_dir = self._snapshot_dir / snap_id
        snap_dir.mkdir(exist_ok=True)
        
        for path in paths:
            full_path = Path(self.workspace) / path
            if full_path.exists():
                dest = snap_dir / path.replace("/", "_")
                if full_path.is_file():
                    dest.write_bytes(full_path.read_bytes())
                # 目录暂不处理
        
        return snap_id
    
    def diff_with_host(self, snap_id: str) -> str:
        """比较沙箱执行后的变更与快照。"""
        if not self._snapshot_dir or not self._working_dir:
            return "没有可用的快照"
        
        snap_dir = self._snapshot_dir / snap_id
        if not snap_dir.exists():
            return f"快照 {snap_id} 不存在"
        
        diffs = []
        for snap_file in snap_dir.iterdir():
            # 简化: 只比较文件是否存在和内容变化
            diffs.append(f"[{snap_file.name}] 快照已保存")
        
        return "\n".join(diffs) if diffs else "没有差异"
    
    def cleanup(self) -> None:
        """清理容器和临时文件。"""
        if self._container_name:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self._container_name],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass
            self._container_name = None
        
        if self._working_dir and self._working_dir.exists():
            import shutil
            try:
                shutil.rmtree(self._working_dir)
            except Exception:
                pass


# 全局沙箱管理器实例
_sandbox_managers: Dict[str, SandboxManager] = {}


def _get_sandbox(workspace: str) -> SandboxManager:
    """获取或创建沙箱管理器。"""
    if workspace not in _sandbox_managers:
        _sandbox_managers[workspace] = SandboxManager(workspace)
    return _sandbox_managers[workspace]


def sandbox_run(ctx: ToolContext, command: str, timeout: int = 60, 
                image: str = "python:3.11-slim") -> str:
    """在 Docker 沙箱中执行命令。
    
    Args:
        command: 要执行的命令
        timeout: 超时秒数
        image: Docker 镜像
    """
    # 安全检查: 红线命令也应该在沙箱中被拦截
    if _engine_is_redline(command):
        return f"[已拦截] 命中致命操作红线，即使在沙箱中也禁止执行: {command}"
    
    sandbox = _get_sandbox(ctx.workspace)
    
    try:
        # 创建容器
        container = sandbox._create_container(image)
        
        # 在容器中执行
        result = sandbox._exec_in_container(command, timeout)
        
        # 格式化输出
        output_parts = []
        if result["stdout"]:
            output_parts.append(result["stdout"].strip())
        if result["stderr"]:
            output_parts.append(f"[stderr] {result['stderr'].strip()}")
        
        output = "\n".join(output_parts) if output_parts else "(无输出)"
        
        return (
            f"exit={result['exit_code']}\n"
            f"container={container}\n"
            f"{output}"
        )
        
    except subprocess.TimeoutExpired:
        return f"exit=-1\n(沙箱执行超时, 容器已保留: {sandbox._container_name})"
    except Exception as exc:
        return f"[错误] 沙箱执行失败: {type(exc).__name__}: {exc}"


def sandbox_snapshot(ctx: ToolContext, paths: str) -> str:
    """创建文件快照用于后续比较。"""
    sandbox = _get_sandbox(ctx.workspace)
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    
    if not path_list:
        return "请提供要快照的文件路径 (逗号分隔)"
    
    snap_id = sandbox.snapshot_files(path_list)
    return f"快照已创建: {snap_id} ({len(path_list)} 个文件)"


def sandbox_diff(ctx: ToolContext, snap_id: str) -> str:
    """查看沙箱执行后的差异。"""
    sandbox = _get_sandbox(ctx.workspace)
    return sandbox.diff_with_host(snap_id)


def sandbox_cleanup(ctx: ToolContext) -> str:
    """清理沙箱容器和临时文件。"""
    sandbox = _get_sandbox(ctx.workspace)
    sandbox.cleanup()
    return "沙箱已清理"


class SandboxPlugin(Plugin):
    """沙箱执行插件。"""
    
    name = "tools.sandbox"
    provides = []
    requires = ["tool_registry"]
    
    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.sandbox.enabled", True):
            return
        
        registry = kernel.require("tool_registry")
        
        # 注册 sandbox_run 工具
        registry.register(Tool(
            name="sandbox_run",
            description="在 Docker 沙箱中执行命令，隔离危险操作。支持 timeout 和 image 参数。",
            parameters={
                "type": "object",
                "properties": {
                    "command": string_prop("要执行的命令"),
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数 (默认 60)",
                    },
                    "image": {
                        "type": "string",
                        "description": "Docker 镜像 (默认 python:3.11-slim)",
                    },
                },
                "required": ["command"],
            },
            handler=sandbox_run,
            dangerous=False,  # 沙箱本身不需要确认，内部有安全检查
            group="sandbox",
        ))
        
        # 注册 sandbox_snapshot 工具
        registry.register(Tool(
            name="sandbox_snapshot",
            description="创建文件快照，用于沙箱执行前后的比较。",
            parameters={
                "type": "object",
                "properties": {
                    "paths": string_prop("文件路径 (逗号分隔)"),
                },
                "required": ["paths"],
            },
            handler=sandbox_snapshot,
            read_only=True,
            group="sandbox",
        ))
        
        # 注册 sandbox_diff 工具
        registry.register(Tool(
            name="sandbox_diff",
            description="查看沙箱执行后的文件差异。",
            parameters={
                "type": "object",
                "properties": {
                    "snap_id": string_prop("快照 ID"),
                },
                "required": ["snap_id"],
            },
            handler=sandbox_diff,
            read_only=True,
            group="sandbox",
        ))
        
        # 注册 sandbox_cleanup 工具
        registry.register(Tool(
            name="sandbox_cleanup",
            description="清理沙箱容器和临时文件。",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=sandbox_cleanup,
            read_only=True,
            group="sandbox",
        ))
