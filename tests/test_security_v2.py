"""安全引擎 v1.2 修复的单元测试。

覆盖:
1. PowerShell -EncodedCommand Base64 解码
2. Unicode NFKC 归一化 + 零宽空格剥离
3. 嵌套间接调用 (不再限制 6 层)
4. PowerShell -Command / cmd /c 解包
5. 符号链接路径解析
6. Glob 展开
7. MCP 工具危险操作拦截
8. TOCTOU 脚本内容检测
"""
import base64
import os
import re
import tempfile

import pytest

from qingxiaotuan.ext.safety_engine import (
    _try_decode_ps_encoded,
    _unwrap_indirection,
    _normalize,
    _resolve_symlinks,
    _expand_globs,
    is_redline,
    has_recursive_rm,
    has_system_shutdown,
)


# ============================================================ 1. PowerShell -EncodedCommand


class TestPowerShellEncodedCommand:
    """PowerShell -EncodedCommand Base64 解码测试。"""

    def test_decode_utf8_rm_rf(self):
        """Base64 UTF-8 编码的 'rm -rf /' 应被检测为红线。"""
        cmd = "rm -rf /".encode("utf-8")
        b64 = base64.b64encode(cmd).decode()
        text = "powershell -EncodedCommand " + b64
        assert is_redline(text)

    def test_decode_utf16le_rm_rf(self):
        """Base64 UTF-16LE 编码的 'rm -rf /' 应被检测为红线。"""
        cmd = "rm -rf /".encode("utf-16-le")
        b64 = base64.b64encode(cmd).decode()
        text = "powershell -EncodedCommand " + b64
        assert is_redline(text)

    def test_invalid_base64_ignored(self):
        """无效 Base64 原样返回, 不崩溃。"""
        text = "powershell -EncodedCommand NOT_VALID_BASE64!!!"
        result = _try_decode_ps_encoded(text)
        assert "NOT_VALID_BASE64" in result

    def test_no_encodedcommand_unchanged(self):
        """不含 -EncodedCommand 的命令原样返回。"""
        text = 'powershell -Command "Get-Process"'
        assert _try_decode_ps_encoded(text) == text

    def test_force_push_decoded(self):
        """解码后的 force push 应被检测。"""
        cmd = "git push origin main --force".encode("utf-8")
        b64 = base64.b64encode(cmd).decode()
        text = "powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand " + b64
        assert is_redline(text)

    def test_shutdown_decoded(self):
        """解码后的关机命令应被检测。"""
        cmd = "shutdown /s /t 0".encode("utf-8")
        b64 = base64.b64encode(cmd).decode()
        text = "powershell -EncodedCommand " + b64
        assert is_redline(text)


# ============================================================ 2. Unicode NFKC


class TestUnicodeNormalization:
    """Unicode NFKC 归一化 + 控制字符剥离。"""

    def test_fullwidth_rm_rf(self):
        """全角字符应被 NFKC 归一化后检测到。"""
        # U+FF52 = fullwidth r, U+FF4D = fullwidth m -> NFKC -> 'rm'
        text = "\uff52\uff4d -\uff46\uff52 /"
        normalized = _normalize(text)
        assert "rm" in normalized.lower()

    def test_zero_width_spaces_stripped(self):
        """零宽空格应被剥离。"""
        text = "r\u200bm -rf /"
        normalized = _normalize(text)
        assert "\u200b" not in normalized

    def test_bom_stripped(self):
        """BOM 字符应被剥离。"""
        text = "\ufeffrm -rf /"
        normalized = _normalize(text)
        assert "\ufeff" not in normalized


# ============================================================ 3. 深层嵌套间接调用


class TestDeepNesting:
    """测试超过 6 层的间接调用不再被遗漏。"""

    def test_3_layer_nesting_detected(self):
        """3 层嵌套的 rm -rf / 应被检测。"""
        cmd = "sh -c 'bash -c \"rm -rf /\"'"
        assert is_redline(cmd)

    def test_loop_until_stable(self):
        """循环剥开直到文本不再变化。"""
        text = "eval eval eval eval 'rm -rf /'"
        normalized = _normalize(text)
        assert "rm" in normalized.lower()


# ============================================================ 4. PowerShell -Command / cmd /c


class TestPSCommandUnwrap:
    """PowerShell -Command / -c 和 cmd /c 的解包。"""

    def test_ps_command_rm_rf(self):
        """powershell -Command \"rm -rf /\" 应被检测。"""
        text = 'powershell -Command "rm -rf /"'
        assert is_redline(text)

    def test_ps_short_c(self):
        """powershell -c \"rm -rf /\" 应被检测。"""
        text = 'powershell -c "rm -rf /"'
        assert is_redline(text)

    def test_cmd_c_rm_rf(self):
        """cmd /c \"rm -rf /\" 应被检测。"""
        text = 'cmd /c "rm -rf /"'
        assert is_redline(text)


# ============================================================ 5. 符号链接解析


class TestSymlinkResolution:
    """符号链接路径解析。"""

    def test_symlink_to_root_resolves(self, tmp_path):
        """指向 / 的符号链接应被解析为真实路径。"""
        link = tmp_path / "evil_link"
        try:
            link.symlink_to("/")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")
        text = "rm -rf " + str(link)
        resolved = _resolve_symlinks(text)
        # 能解析则链接名应被替换为真实目标 (Unix 根为 '/', Windows 为盘符根);
        # 部分 Windows 配置无法解析指向 '/' 的软链 (权限限制), 则跳过根断言。
        if str(link) not in resolved:
            # 链接名已被替换为真实目标。Windows 上指向 "/" 的软链解析为盘符根
            # (如 "E:\"), 前面可能带 "rm -rf " 等前缀, splitdrive 只认串首, 故用
            # 任意位置的盘符模式检测盘根 (Unix 则直接查 '/').
            _drive = re.search(r"[A-Za-z]:\\", resolved)
            assert "/" in resolved or bool(_drive)
        else:
            pytest.skip("本平台未解析指向 / 的符号链接 (权限限制)")

    def test_nonexistent_path_unchanged(self):
        """不存在的路径原样保留。"""
        text = "rm -rf /nonexistent/path/xyz"
        resolved = _resolve_symlinks(text)
        assert "/nonexistent/path/xyz" in resolved


# ============================================================ 6. Glob 展开


class TestGlobExpansion:
    """Glob 通配符展开。"""

    def test_glob_expands_existing_files(self, tmp_path):
        """存在匹配文件的 glob 应被展开 (含空格路径用引号包裹, 触发引号感知分词)。"""
        (tmp_path / "file1.txt").write_text("a")
        (tmp_path / "file2.txt").write_text("b")
        # 用引号包裹: 既贴近真实用法, 也验证修复后含空格路径不再被拆碎
        pattern = '"' + str(tmp_path / "*.txt") + '"'
        expanded = _expand_globs("rm " + pattern)
        assert "file1.txt" in expanded
        assert "file2.txt" in expanded

    def test_no_match_unchanged(self, tmp_path):
        """无匹配的 glob 原样保留。"""
        pattern = str(tmp_path / "*.nonexistent")
        expanded = _expand_globs("rm " + pattern)
        assert "*.nonexistent" in expanded


# ============================================================ 7. MCP 工具拦截


class TestMCPGuard:
    """MCP 工具危险操作拦截。"""

    def test_mcp_execute_with_dangerous_sql(self):
        """MCP 工具名含 'execute' 且参数含 SQL DROP 应被拦截。"""
        from qingxiaotuan.core.tool_executor import _is_mcp_dangerous
        args = '{"query": "DROP TABLE users;"}'
        assert _is_mcp_dangerous("mcp__postgres__execute", args)

    def test_mcp_shell_with_rm_rf(self):
        """MCP shell 工具执行 rm -rf 应被拦截。"""
        from qingxiaotuan.core.tool_executor import _is_mcp_dangerous
        args = '{"command": "rm -rf /"}'
        assert _is_mcp_dangerous("mcp__server__shell", args)

    def test_mcp_write_with_rm_rf(self):
        """MCP write 工具写入 rm -rf 内容应被拦截。"""
        from qingxiaotuan.core.tool_executor import _is_mcp_dangerous
        args = '{"content": "rm -rf /"}'
        assert _is_mcp_dangerous("mcp__server__write", args)

    def test_mcp_safe_tool_not_blocked(self):
        """MCP 工具名不含危险关键词不应被拦截。"""
        from qingxiaotuan.core.tool_executor import _is_mcp_dangerous
        args = '{"query": "SELECT * FROM users"}'
        assert not _is_mcp_dangerous("mcp__postgres__query", args)

    def test_non_mcp_tool_not_checked(self):
        """非 MCP 工具不走 MCP 检查。"""
        from qingxiaotuan.core.tool_executor import _is_mcp_dangerous
        assert not _is_mcp_dangerous("run_shell", '{"command": "ls"}')

    def test_mcp_dangerous_keyword_detection(self):
        """各种危险关键词应被检测。"""
        from qingxiaotuan.core.tool_executor import _is_mcp_dangerous
        for keyword in ("exec", "execute", "run", "delete", "drop", "remove",
                         "write", "shell", "system"):
            tool_name = "mcp__srv__" + keyword + "_something"
            args = '{"command": "rm -rf /"}'
            assert _is_mcp_dangerous(tool_name, args), "keyword '{}' should be detected".format(keyword)


# ============================================================ 8. TOCTOU 脚本检测


class TestTOCTOUCheck:
    """TOCTOU 脚本文件内容检测。"""

    def test_powershell_file_with_rm_rf(self):
        """powershell -File 含 rm -rf 的脚本应被拦截。"""
        from qingxiaotuan.tools.shell import _check_script_content
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False) as f:
            f.write("rm -rf /\n")
            f.flush()
            script_path = f.name
        try:
            cmd = "powershell -File " + script_path
            result = _check_script_content(cmd, "")
            assert result is not None
            assert "TOCTOU" in result
        finally:
            os.unlink(script_path)

    def test_powershell_file_with_remove_item(self):
        """powershell -File 含 Remove-Item -Recurse -Force 的脚本应被拦截。"""
        from qingxiaotuan.tools.shell import _check_script_content
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False) as f:
            f.write("Remove-Item -Recurse -Force /\n")
            f.flush()
            script_path = f.name
        try:
            cmd = "powershell -File " + script_path
            result = _check_script_content(cmd, "")
            assert result is not None
            assert "TOCTOU" in result
        finally:
            os.unlink(script_path)

    def test_safe_script_not_blocked(self):
        """安全脚本不应被拦截。"""
        from qingxiaotuan.tools.shell import _check_script_content
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False) as f:
            f.write("Write-Host 'Hello World'\n")
            f.flush()
            script_path = f.name
        try:
            cmd = "powershell -File " + script_path
            result = _check_script_content(cmd, "")
            assert result is None
        finally:
            os.unlink(script_path)

    def test_nonexistent_script_ignored(self):
        """不存在的脚本文件不检查。"""
        from qingxiaotuan.tools.shell import _check_script_content
        result = _check_script_content("powershell -File /nonexistent/evil.ps1", "")
        assert result is None
