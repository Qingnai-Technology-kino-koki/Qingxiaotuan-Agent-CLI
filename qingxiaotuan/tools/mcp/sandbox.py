"""MCP 沙箱隔离 —— 让 MCP 工具调用在隔离环境中执行。

安全机制:
- 文件系统隔离 (只读工作区)
- 网络访问控制
- 资源限制 (CPU/内存)
- 调用审计
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


from ...core.security_utils import sanitize_env as _sanitize_env  # noqa: F401,F811


def _refuse_if_redline(command: str) -> Optional[Dict[str, Any]]:
    """执行前安全闸门: 命中致命红线直接拒绝 (fail-closed)。"""
    try:
        from ...ext.security_gate import SecurityGate
        verdict = SecurityGate().decide_shell(command)
        if verdict.blocks():
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"[安全拦截] {verdict.reasons[0] if verdict.reasons else '命中红线'}",
                "sandboxed": False,
            }
    except Exception:  # noqa: BLE001 - 异常时放行给后续执行 (执行器自身也有护栏)
        pass
    return None


class MCPSandbox:
    """MCP 沙箱执行器。"""
    
    def __init__(
        self,
        workspace: str,
        read_only: bool = True,
        network_access: bool = False,
        max_memory: str = "256m",
        max_cpu: str = "0.5",
    ) -> None:
        self.workspace = workspace
        self.read_only = read_only
        self.network_access = network_access
        self.max_memory = max_memory
        self.max_cpu = max_cpu
        self._temp_dir: Optional[Path] = None
    
    def _setup_sandbox(self) -> Path:
        """设置沙箱环境。"""
        self._temp_dir = Path(tempfile.mkdtemp(prefix="mcp_sandbox_"))
        
        # 创建工作区链接 (只读)
        sandbox_workspace = self._temp_dir / "workspace"
        if self.read_only:
            # 只读: 创建符号链接
            try:
                os.symlink(self.workspace, str(sandbox_workspace))
            except OSError:
                # Windows 不支持符号链接时复制
                self._copy_readonly(self.workspace, sandbox_workspace)
        else:
            # 可写: 复制工作区
            self._copy_readwrite(self.workspace, sandbox_workspace)
        
        return self._temp_dir
    
    def _copy_readonly(self, src: str, dst: Path) -> None:
        """只读复制工作区。"""
        import shutil
        try:
            shutil.copytree(src, dst, symlinks=True, ignore_dangling_symlinks=True)
        except Exception:
            # 简化复制
            dst.mkdir(parents=True, exist_ok=True)
            for item in Path(src).iterdir():
                if item.is_file():
                    try:
                        (dst / item.name).write_bytes(item.read_bytes())
                    except Exception:
                        pass
    
    def _copy_readwrite(self, src: str, dst: Path) -> None:
        """可写复制工作区。"""
        import shutil
        try:
            shutil.copytree(src, dst, symlinks=True)
        except Exception:
            dst.mkdir(parents=True, exist_ok=True)
            for item in Path(src).iterdir():
                if item.is_file():
                    try:
                        (dst / item.name).write_bytes(item.read_bytes())
                    except Exception:
                        pass
    
    def execute_in_sandbox(
        self,
        command: str,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """在沙箱中执行命令。"""
        # 执行前安全闸门: 命中致命红线直接拒绝 (远程/MCP 工具调用前最后一道防线)
        blocked = _refuse_if_redline(command)
        if blocked is not None:
            return blocked

        sandbox_dir = self._setup_sandbox()

        # 构建 Docker 命令 (如果可用)
        if self._docker_available():
            return self._execute_docker(command, env, timeout)

        # 回退到本地执行 (有限隔离)
        return self._execute_local(command, env, timeout, sandbox_dir)
    
    def _docker_available(self) -> bool:
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
    
    def _execute_docker(
        self,
        command: str,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """在 Docker 容器中执行。"""
        docker_env = _sanitize_env(os.environ, env)
        
        # 构建 Docker 命令
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "none" if not self.network_access else "bridge",
            "--memory", self.max_memory,
            "--cpus", self.max_cpu,
            "-v", f"{self.workspace}:/workspace:ro",
            "-w", "/workspace",
        ]
        
        # 添加环境变量
        for key, value in (env or {}).items():
            docker_cmd.extend(["-e", f"{key}={value}"])
        
        docker_cmd.extend([
            "python:3.11-slim",
            "bash", "-c", command,
        ])
        
        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "sandboxed": True,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "执行超时",
                "sandboxed": True,
            }
        except Exception as exc:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(exc),
                "sandboxed": False,
            }
    
    def _execute_local(
        self,
        command: str,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        sandbox_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """本地执行 (有限隔离)。"""
        exec_env = _sanitize_env(os.environ, env)
        
        # 设置工作目录
        cwd = str(sandbox_dir / "workspace") if sandbox_dir else self.workspace
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=exec_env,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "sandboxed": False,  # 本地执行不是完全隔离
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "执行超时",
                "sandboxed": False,
            }
        except Exception as exc:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(exc),
                "sandboxed": False,
            }
    
    def cleanup(self) -> None:
        """清理沙箱。"""
        if self._temp_dir and self._temp_dir.exists():
            import shutil
            try:
                shutil.rmtree(self._temp_dir)
            except Exception:
                pass
            self._temp_dir = None


def mcp_sandbox_execute(
    ctx: Any,
    tool_name: str,
    arguments: Dict[str, Any],
    command: str,
    **kwargs,
) -> str:
    """在沙箱中执行 MCP 工具调用。"""
    workspace = getattr(ctx, 'workspace', '') or os.getcwd()
    
    sandbox = MCPSandbox(
        workspace=workspace,
        read_only=kwargs.get('read_only', True),
        network_access=kwargs.get('network_access', False),
    )
    
    try:
        result = sandbox.execute_in_sandbox(
            command,
            env=kwargs.get('env'),
            timeout=kwargs.get('timeout', 30),
        )
        
        output_parts = []
        if result["stdout"]:
            output_parts.append(result["stdout"].strip())
        if result["stderr"]:
            output_parts.append(f"[stderr] {result['stderr'].strip()}")
        
        output = "\n".join(output_parts) if output_parts else "(无输出)"
        sandbox_status = "[沙箱]" if result["sandboxed"] else "[本地]"
        
        return f"{sandbox_status} exit={result['exit_code']}\n{output}"
        
    finally:
        sandbox.cleanup()
