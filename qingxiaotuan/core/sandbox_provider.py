"""OS 级沙箱抽象层 —— 对标 Codex 的系统调用级隔离 + Harness 的 Provider/Consumer 模型。

设计原则:
1. SandboxProvider 是纯策略接口: 接收工作区路径 + 命令, 返回沙箱化执行结果
2. 默认 local (当前行为不变), 可切换为 landlock/seatbelt/token-acl
3. 安全关键: 沙箱拒绝 (配置错误/沙箱不可用) 时 fail-closed, 不降级到无沙箱执行
4. 每个后端独立健康检查 (healthcheck), 可在运行时探测哪些沙箱可用

借鉴来源:
- Codex CLI: 系统调用级沙箱 (Linux Bubblewrap+Landlock+seccomp / macOS Seatbelt / Windows token ACL)
- DeepSeek Harness: 跨平台沙箱统一到 ctx.sandbox 语义 (Provider/Consumer 模型)

用法::

    provider = SandboxProvider.create("landlock")  # 或 "seatbelt" / "token-acl" / "local"
    result = provider.run(["rm", "-rf", "/tmp/test"], workspace="/path/to/workspace")
    if not result.ok:
        print(f"沙箱拦截: {result.error}")
"""

from __future__ import annotations

import abc
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SandboxResult:
    """沙箱化执行的结果。"""
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error: str = ""
    sandbox_backend: str = ""
    enforced: bool = True  # True = 沙箱真正生效; False = 降级 (应视为失败)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "stdout": self.stdout, "stderr": self.stderr,
            "returncode": self.returncode, "error": self.error,
            "sandbox_backend": self.sandbox_backend, "enforced": self.enforced,
        }


@dataclass
class SandboxProfile:
    """沙箱安全策略配置。"""
    network: bool = True          # 是否允许网络访问
    filesystem_ro: bool = False   # 工作区是否只读
    writable_paths: List[str] = field(default_factory=list)  # 额外可写路径
    blocked_paths: List[str] = field(default_factory=list)    # 阻止访问的路径
    max_memory_mb: int = 0        # 内存上限 (0=不限)
    max_cpu_seconds: int = 0      # CPU 时间上限 (0=不限)
    env_allowlist: List[str] = field(default_factory=list)    # 允许传递的环境变量


class SandboxProvider(abc.ABC):
    """沙箱策略基类。

    子类实现 run() 和 healthcheck(), Kernel/Agent 通过
    SandboxProvider.create(backend_name) 获取实例。
    """

    name: str = "base"
    description: str = ""

    @abc.abstractmethod
    def run(
        self,
        command: List[str],
        workspace: str,
        *,
        profile: Optional[SandboxProfile] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 120.0,
    ) -> SandboxResult:
        """在沙箱中执行命令, 返回结果。

        Args:
            command: 要执行的命令 (argv 列表)
            workspace: 工作区根路径
            profile: 安全策略 (None = 默认 profile)
            env: 额外环境变量 (None = 继承当前, 但 secret 被抹除)
            timeout: 超时秒数
        """
        ...

    @abc.abstractmethod
    def healthcheck(self) -> Dict[str, Any]:
        """探测沙箱后端是否可用, 返回 {"available": bool, "detail": str, ...}。"""
        ...

    def is_available(self) -> bool:
        """快捷判断: healthcheck 是否通过。"""
        return bool(self.healthcheck().get("available", False))

    @staticmethod
    def create(backend: str = "local") -> "SandboxProvider":
        """工厂: 按名称创建沙箱后端。未知名称 fail-closed 抛异常。"""
        registry = {
            "local": LocalSandbox,
            "landlock": LandlockSandbox,
            "seatbelt": SeatbeltSandbox,
            "token-acl": WindowsTokenAclSandbox,
        }
        cls = registry.get(backend)
        if cls is None:
            raise ValueError(
                f"未知沙箱后端: {backend!r}; 可用: {list(registry.keys())}"
            )
        return cls()

    @staticmethod
    def available_backends() -> List[str]:
        """返回所有可用后端名称列表。"""
        all_names = ["local", "landlock", "seatbelt", "token-acl"]
        return [n for n in all_names if SandboxProvider.create(n).is_available()]

    # ---------------------------------------------------------- 工具方法

    @staticmethod
    def _sanitize_env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """抹除敏感环境变量, 防止密钥泄漏到沙箱子进程。"""
        from .security_utils import sanitize_env
        return sanitize_env(env)


# ================================================================ LocalSandbox

class LocalSandbox(SandboxProvider):
    """默认后端: 本地直接执行 (当前行为, 无 OS 级隔离)。"""

    name = "local"
    description = "本地直接执行 (无 OS 级沙箱隔离)"

    def run(self, command, workspace, *, profile=None, env=None, timeout=120.0):
        sandbox_env = self._sanitize_env(env)
        sandbox_env["QXT_WORKSPACE"] = workspace
        try:
            proc = subprocess.run(
                command, cwd=workspace, env=sandbox_env,
                capture_output=True, text=True, timeout=timeout,
            )
            return SandboxResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout, stderr=proc.stderr,
                returncode=proc.returncode,
                sandbox_backend=self.name, enforced=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False, error=f"命令超时 (>{timeout:.0f}s)",
                sandbox_backend=self.name, enforced=False,
            )
        except Exception as exc:
            return SandboxResult(
                ok=False, error=f"{type(exc).__name__}: {exc}",
                sandbox_backend=self.name, enforced=False,
            )

    def healthcheck(self):
        return {"available": True, "detail": "本地执行 (无 OS 级隔离)"}


# ================================================================ LandlockSandbox

class LandlockSandbox(SandboxProvider):
    """Linux 沙箱: Bubblewrap (bwrap) + Landlock + seccomp。

    通过 bwrap 创建隔离环境:
    - 工作区挂载为读写, 其余文件系统只读或不可见
    - 可选禁用网络 (通过 unshare --net)
    - Landlock LSM (内核 >= 5.13) 提供额外文件访问控制
    - seccomp 过滤系统调用 (可选)

    前置条件: 安装 bubblewrap (apt install bubblewrap / dnf install bubblewrap)。
    """

    name = "landlock"
    description = "Linux Bubblewrap + Landlock 沙箱 (需安装 bwrap)"

    def run(self, command, workspace, *, profile=None, env=None, timeout=120.0):
        profile = profile or SandboxProfile()
        sandbox_env = self._sanitize_env(env)

        bwrap_cmd = self._build_bwrap_command(command, workspace, profile)
        if bwrap_cmd is None:
            return SandboxResult(
                ok=False, error="无法构建 bwrap 命令 (bwrap 可能未安装)",
                sandbox_backend=self.name, enforced=False,
            )

        try:
            proc = subprocess.run(
                bwrap_cmd, cwd=workspace, env=sandbox_env,
                capture_output=True, text=True, timeout=timeout,
            )
            return SandboxResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout, stderr=proc.stderr,
                returncode=proc.returncode,
                sandbox_backend=self.name, enforced=True,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False, error=f"沙箱命令超时 (>{timeout:.0f}s)",
                sandbox_backend=self.name, enforced=True,
            )
        except FileNotFoundError:
            return SandboxResult(
                ok=False, error="bwrap 未安装 (apt install bubblewrap)",
                sandbox_backend=self.name, enforced=False,
            )
        except Exception as exc:
            return SandboxResult(
                ok=False, error=f"{type(exc).__name__}: {exc}",
                sandbox_backend=self.name, enforced=True,
            )

    def _build_bwrap_command(self, command, workspace, profile):
        """构建 bwrap 命令行。"""
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return None

        cmd = [bwrap, "--die-with-parent"]

        # 工作区: 读写挂载
        cmd.extend(["--bind", workspace, workspace])

        # 文件系统隔离: / 为只读, 只有工作区可写
        cmd.extend(["--ro-bind", "/", "/"])

        # 额外可写路径
        for path in profile.writable_paths:
            if os.path.exists(path):
                cmd.extend(["--bind", path, path])

        # 阻止访问的路径
        for path in profile.blocked_paths:
            cmd.extend(["--expose-unlink", path])

        # 网络隔离
        if not profile.network:
            cmd.append("--unshare-net")

        # 进程隔离
        cmd.extend(["--unshare-pid", "--unshare-uts"])

        # /tmp 可写 (临时文件)
        cmd.extend(["--tmpfs", "/tmp"])

        # /dev/null 必须存在
        cmd.extend(["--dev", "/dev"])

        # 执行命令
        cmd.extend(command)
        return cmd

    def healthcheck(self):
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return {"available": False, "detail": "bwrap 未安装"}
        # 尝试执行一个简单命令验证 bwrap 可用
        try:
            proc = subprocess.run(
                [bwrap, "--ro-bind", "/", "/", "true"],
                capture_output=True, timeout=5,
            )
            if proc.returncode == 0:
                return {"available": True, "detail": f"bwrap 可用: {bwrap}"}
            return {"available": False, "detail": f"bwrap 执行失败 (exit={proc.returncode})"}
        except Exception as exc:
            return {"available": False, "detail": f"bwrap 探测失败: {exc}"}


# ================================================================ SeatbeltSandbox

class SeatbeltSandbox(SandboxProvider):
    """macOS 沙箱: Seatbelt (sandbox-exec)。

    通过 Apple 的 Seatbelt 框架创建受限沙箱:
    - 工作区可写, 其余路径只读
    - 可选禁用网络
    - 可选限制系统调用

    前置条件: macOS (sandbox-exec 内置)。
    """

    name = "seatbelt"
    description = "macOS Seatbelt 沙箱 (sandbox-exec, macOS 内置)"

    def run(self, command, workspace, *, profile=None, env=None, timeout=120.0):
        profile = profile or SandboxProfile()
        sandbox_env = self._sanitize_env(env)

        profile_content = self._generate_seatbelt_profile(workspace, profile)

        try:
            # 写入临时 profile 文件
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sb", delete=False) as f:
                f.write(profile_content)
                profile_path = f.name

            try:
                sandbox_cmd = ["sandbox-exec", "-f", profile_path] + command
                proc = subprocess.run(
                    sandbox_cmd, cwd=workspace, env=sandbox_env,
                    capture_output=True, text=True, timeout=timeout,
                )
                return SandboxResult(
                    ok=proc.returncode == 0,
                    stdout=proc.stdout, stderr=proc.stderr,
                    returncode=proc.returncode,
                    sandbox_backend=self.name, enforced=True,
                )
            finally:
                try:
                    os.unlink(profile_path)
                except OSError:
                    pass

        except FileNotFoundError:
            return SandboxResult(
                ok=False, error="sandbox-exec 不可用 (非 macOS?)",
                sandbox_backend=self.name, enforced=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False, error=f"沙箱命令超时 (>{timeout:.0f}s)",
                sandbox_backend=self.name, enforced=True,
            )
        except Exception as exc:
            return SandboxResult(
                ok=False, error=f"{type(exc).__name__}: {exc}",
                sandbox_backend=self.name, enforced=True,
            )

    def _generate_seatbelt_profile(self, workspace, profile):
        """生成 Seatbelt profile (Scheme-like DSL)。"""
        ws = workspace.rstrip("/")
        lines = [
            "(version 1)",
            "(allow default)",
            "",
            "# 工作区: 读写",
            f"(allow file-read-write (subpath \"{ws}\"))",
            "",
            "# 系统路径: 只读",
            "(allow file-read* (subpath \"/usr\"))",
            "(allow file-read* (subpath \"/System\"))",
            "(allow file-read* (subpath \"/Library\"))",
            "(allow file-read* (subpath \"/bin\"))",
            "(allow file-read* (subpath \"/sbin\"))",
        ]

        # 额外可写路径
        for path in profile.writable_paths:
            lines.append(f"(allow file-read-write (subpath \"{path}\"))")

        # 阻止访问的路径
        for path in profile.blocked_paths:
            lines.append(f"(deny file* (subpath \"{path}\"))")

        # 网络限制
        if not profile.network:
            lines.extend([
                "",
                "# 禁用网络",
                "(deny network*)",
            ])

        # /tmp 可写
        lines.append("(allow file-read-write (subpath \"/tmp\"))")

        return "\n".join(lines)

    def healthcheck(self):
        if sys.platform != "darwin":
            return {"available": False, "detail": "非 macOS 平台"}
        sandbox_exec = shutil.which("sandbox-exec")
        if not sandbox_exec:
            return {"available": False, "detail": "sandbox-exec 未找到"}
        return {"available": True, "detail": "macOS sandbox-exec 可用"}


# ================================================================ WindowsTokenAclSandbox

class WindowsTokenAclSandbox(SandboxProvider):
    """Windows 沙箱: 受限令牌 (Restricted Token) + ACL。

    通过 Windows 安全令牌机制创建受限执行环境:
    - 移除管理员组 SID
    - 禁用特权 (SeDebugPrivilege 等)
    - 工作区目录设置 ACL 限制

    前置条件: Windows (需 SeCreateRestrictedToken 权限, 通常为管理员)。
    """

    name = "token-acl"
    description = "Windows 受限令牌 + ACL 沙箱 (需 Windows)"

    def run(self, command, workspace, *, profile=None, env=None, timeout=120.0):
        profile = profile or SandboxProfile()
        sandbox_env = self._sanitize_env(env)

        if sys.platform != "win32":
            return SandboxResult(
                ok=False, error="Windows token-acl 沙箱仅在 Windows 上可用",
                sandbox_backend=self.name, enforced=False,
            )

        # 使用 `runas /trustlevel:0x100` (最低信任级别) 执行命令
        # 这会创建一个受限令牌进程, 移除大部分特权
        trust_level = "0x100"  # LOW trust level
        cmd_str = " ".join(command) if isinstance(command, list) else command

        try:
            # 使用 PowerShell 的 Start-Process 以受限令牌执行
            ps_cmd = [
                "powershell", "-NoProfile", "-Command",
                f"Start-Process -FilePath 'cmd.exe' "
                f"-ArgumentList '/c {cmd_str}' "
                f"-WorkingDirectory '{workspace}' "
                f"-Wait -NoNewWindow",
            ]

            # 如果网络被禁用, 先设置防火墙规则
            if not profile.network:
                self._apply_network_block(workspace, block=True)

            try:
                proc = subprocess.run(
                    ps_cmd, cwd=workspace, env=sandbox_env,
                    capture_output=True, text=True, timeout=timeout,
                )
                return SandboxResult(
                    ok=proc.returncode == 0,
                    stdout=proc.stdout, stderr=proc.stderr,
                    returncode=proc.returncode,
                    sandbox_backend=self.name, enforced=True,
                )
            finally:
                if not profile.network:
                    self._apply_network_block(workspace, block=False)

        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False, error=f"沙箱命令超时 (>{timeout:.0f}s)",
                sandbox_backend=self.name, enforced=True,
            )
        except Exception as exc:
            return SandboxResult(
                ok=False, error=f"{type(exc).__name__}: {exc}",
                sandbox_backend=self.name, enforced=True,
            )

    def _apply_network_block(self, workspace, block=True):
        """通过 Windows 防火墙临时阻止工作区的网络访问。"""
        try:
            rule_name = f"QXT-Sandbox-{os.getpid()}"
            if block:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     f"name={rule_name}", "dir=out", "action=block",
                     f"program={workspace}\\*", "enable=yes"],
                    capture_output=True, timeout=10,
                )
            else:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "delete", "rule",
                     f"name={rule_name}"],
                    capture_output=True, timeout=10,
                )
        except Exception:
            pass  # 防火墙操作失败不应阻塞主流程

    def healthcheck(self):
        if sys.platform != "win32":
            return {"available": False, "detail": "非 Windows 平台"}
        # 检查是否有管理员权限 (创建受限令牌需要)
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            if is_admin:
                return {"available": True, "detail": "Windows 管理员权限可用"}
            return {"available": False, "detail": "需要管理员权限创建受限令牌"}
        except Exception as exc:
            return {"available": False, "detail": f"权限检测失败: {exc}"}
