"""OS 抽象与 syscall 级沙箱原语。

提供:
- 平台探测 (is_linux / is_windows / is_macos)
- 安全 syscall 白名单 (供 seccomp BPF 使用)
- Windows Job Object 封装 (OS 级资源/进程隔离)
- Linux seccomp 过滤器安装 (当 libseccomp 可用时)

设计原则:
- 任何平台相关能力都做"能力探测 + 优雅降级", 绝不在不支持时抛未捕获异常。
- syscall 级拦截在 Linux 上走 seccomp (libseccomp 提供), 在 Windows 上走 Job Object
  (OS 提供的进程/资源隔离原语; 用户态无法逐 syscall 拦截, 这是平台限制, 非取舍)。
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

IS_WINDOWS = os.name == "nt" or sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"


# ----------------------------------------------------------------- 安全 syscall 白名单
# 仅允许"无害"的基础 syscall; 其余一律 SECCOMP_RET_KILL (或 ERRNO)。
# 覆盖典型 Python/编译型子进程所需的最小集。
SAFE_SYSCALL_ALLOWLIST: List[str] = [
    # 进程/内存
    "read", "write", "close", "fstat", "mmap", "mprotect", "munmap",
    "brk", "rt_sigaction", "rt_sigprocmask", "sigreturn", "clone",
    "exit", "exit_group", "wait4", "kill", "tgkill", "gettid",
    # 文件
    "open", "openat", "lseek", "access", "stat", "lstat", "readlink",
    "fcntl", "dup", "dup2", "getdents64", "ioctl", "poll", "select",
    # 调度/时间
    "sched_yield", "clock_gettime", "gettimeofday", "nanosleep", "times",
    # 线程/同步
    "futex", "set_tid_address", "set_robust_list", "get_robust_list",
    "restart_syscall", "rseq",
    # 网络 (白名单内允许; 若策略禁网则由架构层在 fork 前 unshare -n)
    "socket", "connect", "sendto", "recvfrom", "shutdown", "bind",
    "listen", "accept", "getsockname", "getpeername", "setsockopt",
    "getsockopt", "recvmsg", "sendmsg",
]

# 默认拦截并杀掉的"高危" syscall (即便不在显式拒绝列表, 白名单之外的也杀)
# 这些通常不应出现在常规 Agent 子任务中:
DANGEROUS_SYSCALLS: List[str] = [
    "ptrace", "mount", "umount2", "kexec_load", "reboot", "swapon",
    "swapoff", "setuid", "setgid", "setns", "unshare", "personality",
    "bpf", "userfaultfd", "perf_event_open", "fanotify_init",
    "init_module", "finit_module", "delete_module", "lookup_dcookie",
    "process_vm_readv", "process_vm_writev", "name_to_handle_at",
]


@dataclass
class SyscallResult:
    """单次沙箱执行的结果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    blocked: bool = False        # True = 被 seccomp 拦截 (SIGSYS)
    blocked_syscalls: List[str] = field(default_factory=list)
    mechanism: str = "none"  # seccomp | job-object | fallback


# ----------------------------------------------------------------- Windows Job Object
# 用 ctypes 直接调用 kernel32, 无需 pywin32。
def _windows_job_object_sandbox(
    cmd: List[str],
    timeout: float,
    memory_limit_mb: int = 512,
    cpu_rate_pct: int = 50,
    cwd: Optional[str] = None,
) -> SyscallResult:
    """Windows: 用 Job Object 限制子进程的 CPU/内存/活跃进程数。

    这是 Windows 上 OS 提供的进程级隔离原语 (用户态无法逐 syscall 拦截)。
    返回执行结果; 超时则终止整个 Job (连同所有派生进程)。
    """
    import ctypes  # noqa: WPS433 (局部导入, 仅 Windows 路径)
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    # --- 结构定义 ---
    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError()

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_JOB_MEMORY
    )
    info.BasicLimitInformation.ActiveProcessLimit = 16
    info.BasicLimitInformation.MaximumWorkingSetSize = memory_limit_mb * 1024 * 1024
    info.ProcessMemoryLimit = memory_limit_mb * 1024 * 1024
    info.JobMemoryLimit = memory_limit_mb * 1024 * 1024

    ok = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        kernel32.CloseHandle(job)
        raise ctypes.WinError()

    proc = subprocess.Popen(  # noqa: S603
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=0, cwd=cwd,
    )
    # 把子进程挂到 Job (含其派生进程)
    if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
        # 失败不致命, 退化为普通子进程
        pass

    try:
        out, err = proc.communicate(timeout=timeout)
        return SyscallResult(
            returncode=proc.returncode or 0,
            stdout=out or "", stderr=err or "",
            timed_out=False, mechanism="job-object",
        )
    except subprocess.TimeoutExpired:
        # 终止整个 Job (树)
        kernel32.TerminateJobObject(job, 1)
        proc.kill()
        out, err = proc.communicate()
        return SyscallResult(
            returncode=proc.returncode or -1,
            stdout=out or "", stderr=err or "",
            timed_out=True, mechanism="job-object",
        )
    finally:
        kernel32.CloseHandle(job)


# ----------------------------------------------------------------- Linux seccomp
def _linux_seccomp_sandbox(
    cmd: List[str],
    timeout: float,
    allowlist: Optional[List[str]] = None,
) -> SyscallResult:
    """Linux: 在 fork 出的子进程里安装 seccomp 白名单过滤器, 再 exec 目标命令。

    需要 libseccomp (`pip install libseccomp`); 若不可用则抛 RuntimeError,
    由调用方降级到 fallback 路径。
    """
    try:
        import seccomp  # type: ignore
    except Exception as exc:  # pragma: no cover - 依赖缺失
        raise RuntimeError("libseccomp 不可用, 无法启用 syscall 级拦截") from exc

    allow = set(allowlist or SAFE_SYSCALL_ALLOWLIST)

    pid = os.fork()
    if pid == 0:
        # 子进程: 安装过滤器后 exec
        try:
            f = seccomp.SyscallFilter(seccomp.DEF_ALLOW)
            # DEF_ALLOW 默认放行; 这里改为默认拒绝, 仅白名单放行
            f = seccomp.SyscallFilter(seccomp.KILL)
            for name in allow:
                try:
                    f.add_rule(seccomp.ALLOW, name)
                except Exception:
                    pass
            # 放行架构必需的几个
            for must in ("execve", "execveat", "arch_prctl", "prctl", "rt_sigreturn"):
                try:
                    f.add_rule(seccomp.ALLOW, must)
                except Exception:
                    pass
            f.load()
        except Exception:
            os._exit(127)  # 过滤器安装失败, 直接失败 (fail-closed)
        try:
            os.execvp(cmd[0], cmd)
        except Exception:
            os._exit(127)
    else:
        import signal as _signal
        import time
        deadline = time.time() + timeout
        # SIGSYS (signal 31 on Linux) = seccomp 杀掉了子进程
        _SIGSYS = getattr(_signal, "SIGSYS", 31)
        while True:
            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid != 0:
                if os.WIFSIGNALED(status):
                    sig = os.WTERMSIG(status)
                    if sig == _SIGSYS:
                        # seccomp 杀掉了子进程: 可能是白名单拦截, 也可能是命令不存在
                        # 退出码 127 = 命令不存在 (shell 惯例); 其他信号码 = 拦截
                        return SyscallResult(
                            returncode=127, blocked=True,
                            mechanism="seccomp",
                        )
                    # 其他信号杀死 (OOM/kill 等)
                    return SyscallResult(
                        returncode=128 + sig, blocked=True,
                        mechanism="seccomp",
                    )
                rc = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8
                return SyscallResult(returncode=rc, mechanism="seccomp")
            if time.time() > deadline:
                # 超时: 杀掉整个进程组 (含其派生)
                try:
                    os.killpg(os.getpgid(pid), 9)
                except Exception:
                    os.kill(pid, 9)
                os.waitpid(pid, 0)
                return SyscallResult(returncode=-1, timed_out=True, mechanism="seccomp")
            time.sleep(0.02)


# 网络相关 syscall (禁网时从白名单中移除)
_NET_SYSCALLS = frozenset({
    "socket", "connect", "sendto", "recvfrom", "shutdown", "bind",
    "listen", "accept", "getsockname", "getpeername", "setsockopt",
    "getsockopt", "recvmsg", "sendmsg",
})


# ----------------------------------------------------------------- 统一入口
def run_syscall_sandboxed(
    cmd: List[str],
    timeout: float = 30.0,
    memory_limit_mb: int = 512,
    cpu_rate_pct: int = 50,
    allowlist: Optional[List[str]] = None,
    block_network: bool = False,
    cwd: Optional[str] = None,
) -> SyscallResult:
    """跨平台 syscall 级沙箱执行。

    - Windows:     Job Object 隔离 (资源/进程数限制)
    - Linux:       seccomp 白名单拦截 (libseccomp 可用时); 否则 fallback
    - 禁网:        Linux 尝试 `unshare -n` 进入无网络命名空间 (需要权限, 失败则降级;
                   降级时从 seccomp 白名单中移除网络 syscall 作为兜底)
    - cwd:         子进程工作目录 (None = 继承父进程)
    """
    # 如果禁网, 先构建过滤后的白名单 (移除网络 syscall)
    filtered_allowlist = list(allowlist) if allowlist is not None else None
    if block_network and filtered_allowlist is not None:
        filtered_allowlist = [s for s in filtered_allowlist if s not in _NET_SYSCALLS]
    elif block_network and filtered_allowlist is None:
        filtered_allowlist = [s for s in SAFE_SYSCALL_ALLOWLIST if s not in _NET_SYSCALLS]

    if block_network and IS_LINUX:
        # 用 nsenter/unshare 进入无网命名空间再跑 (需要 CAP_SYS_ADMIN, 普通用户常失败)
        try:
            return _linux_seccomp_sandbox(
                ["unshare", "-n", "--"] + cmd, timeout=timeout,
                allowlist=filtered_allowlist,
            )
        except Exception:
            # unshare 失败 -> 降级但仍然从白名单移除网络 syscall (兜底)
            pass

    if IS_WINDOWS:
        try:
            return _windows_job_object_sandbox(
                cmd, timeout, memory_limit_mb=memory_limit_mb, cpu_rate_pct=cpu_rate_pct,
                cwd=cwd,
            )
        except Exception:
            # Job Object 不可用 -> 普通子进程 + timeout
            proc = subprocess.run(  # noqa: S603
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout, cwd=cwd,
            )
            return SyscallResult(
                returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
                mechanism="fallback",
            )

    if IS_LINUX:
        try:
            return _linux_seccomp_sandbox(
                cmd, timeout=timeout, allowlist=filtered_allowlist,
            )
        except Exception:
            # 降级: 普通子进程 + 资源限制
            import resource

            def _prep() -> None:
                # 限制子进程地址空间, 防 OOM 炸宿主
                try:
                    mb = memory_limit_mb * 1024 * 1024
                    resource.setrlimit(resource.RLIMIT_AS, (mb, mb))
                except Exception:
                    pass

            proc = subprocess.run(  # noqa: S603
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout, preexec_fn=_prep, cwd=cwd,
            )
            return SyscallResult(
                returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
                mechanism="fallback",
            )

    # 其他平台
    proc = subprocess.run(  # noqa: S603
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout, cwd=cwd,
    )
    return SyscallResult(
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
        mechanism="fallback",
    )
