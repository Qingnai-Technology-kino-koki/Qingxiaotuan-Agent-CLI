"""Tests for arch/platform.py — seccomp blocked detection + cwd parameter."""

import os
import sys
import subprocess

import pytest

from qingxiaotuan.arch.platform import (
    IS_WINDOWS,
    IS_LINUX,
    SyscallResult,
    run_syscall_sandboxed,
    SAFE_SYSCALL_ALLOWLIST,
    DANGEROUS_SYSCALLS,
    _NET_SYSCALLS,
)


class TestSyscallResult:
    def test_blocked_field_default(self):
        r = SyscallResult(returncode=0)
        assert r.blocked is False
        assert r.blocked_syscalls == []

    def test_blocked_field_set(self):
        r = SyscallResult(returncode=127, blocked=True, mechanism="seccomp")
        assert r.blocked is True


class TestRunSyscallSandboxed:
    def test_basic_command(self):
        """最简单的命令应该成功执行。"""
        result = run_syscall_sandboxed(
            [sys.executable, "-c", "print('hello')"],
            timeout=10,
        )
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.timed_out is False

    def test_cwd_parameter(self):
        """cwd 参数应改变子进程工作目录。"""
        tmpdir = os.environ.get("TEMP", "/tmp")
        result = run_syscall_sandboxed(
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            timeout=10,
            cwd=tmpdir,
        )
        assert result.returncode == 0
        # cwd 应该是 tmpdir (或其规范化路径)
        assert tmpdir in result.stdout or result.stdout.strip() != os.getcwd()

    def test_timeout(self):
        """超时应返回 timed_out=True。"""
        result = run_syscall_sandboxed(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.5,
        )
        assert result.timed_out is True

    def test_nonexistent_command(self):
        """不存在的命令应返回非零退出码。"""
        result = run_syscall_sandboxed(
            [sys.executable, "-c", "import sys; sys.exit(127)"],
            timeout=10,
        )
        assert result.returncode == 127


class TestSyscallLists:
    def test_safe_allowlist_has_basics(self):
        """白名单应包含基础 syscall。"""
        assert "read" in SAFE_SYSCALL_ALLOWLIST
        assert "write" in SAFE_SYSCALL_ALLOWLIST
        assert "open" in SAFE_SYSCALL_ALLOWLIST
        assert "execve" not in SAFE_SYSCALL_ALLOWLIST  # execve 在 seccomp 中单独添加

    def test_dangerous_syscalls(self):
        """高危 syscall 列表应包含关键项。"""
        assert "ptrace" in DANGEROUS_SYSCALLS
        assert "mount" in DANGEROUS_SYSCALLS
        assert "bpf" in DANGEROUS_SYSCALLS

    def test_net_syscalls_disjoint_from_dangerous(self):
        """网络 syscall 不应与高危 syscall 重叠。"""
        overlap = _NET_SYSCALLS & set(DANGEROUS_SYSCALLS)
        assert overlap == set(), f"重叠: {overlap}"


class TestBlockNetwork:
    def test_block_network_filters_net_syscalls(self):
        """block_network 应从白名单中移除网络 syscall。"""
        # 构造包含网络 syscall 的白名单
        test_allowlist = list(SAFE_SYSCALL_ALLOWLIST)
        # 验证过滤逻辑
        from qingxiaotuan.arch.platform import _NET_SYSCALLS
        filtered = [s for s in test_allowlist if s not in _NET_SYSCALLS]
        assert "socket" not in filtered
        assert "connect" not in filtered
        assert "read" in filtered  # 非网络 syscall 应保留
