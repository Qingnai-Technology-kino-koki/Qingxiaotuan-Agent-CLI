# -*- coding: utf-8 -*-
"""安全引擎 _normalize 各绕过分支的专项单测 (Critical #1)。

对照 CODE_REVIEW 建议: 此前 tests/ 未见针对下列「关键绕过场景」的专项测试——
1. 解释器无空格包装   bash -c"rm -rf /"  /  sh -c"rm -rf /" / cmd /c"..."
2. PowerShell -EncodedCommand Base64 (默认 UTF-16LE 及 UTF-8, 短旗标 -ec)
3. ANSI-C 十六/八进制转义   $'rm\\x20-rf\\x20/'
4. 嵌套命令替换内联   $(echo $(rm -rf /)) 双层
5. Unicode 同形字/组合/全角/变体选择符  рm / r\\u0301m / ＲＭ / r\\uFE0Fm

本文件只做「静态归一化 + 红线判定」两件事, 不触碰文件系统。
"""
import base64

import pytest

from qingxiaotuan.ext.safety_engine import (
    _normalize,
    is_redline,
    is_hard_redline,
    SafetyEngine,
)


_PS_DELETE = r"Remove-Item -Path C:\ -Recurse -Force"


def _ps_b64(flag: str, text: str, encoding: str) -> str:
    b64 = base64.b64encode(text.encode(encoding)).decode()
    return f"powershell -{flag} {b64}"


# ------------------------------------------------------ 1. 解释器无空格包装
@pytest.mark.parametrize("cmd", [
    "bash -c\"rm -rf /\"",
    "sh -c\"rm -rf /\"",
    'python3 -c"rm -rf /"',
    'cmd /c"rm -rf /"',
    "bash -c\"chmod -R 000 /\"",
    "/bin/sh -c\"rm -rf /\"",
])
def test_interp_no_space_normalizes_and_flags(cmd):
    # 归一化后真实命令浮到段首, 供 token 化判定命中
    assert "rm " in _normalize(cmd) or "chmod" in _normalize(cmd)
    assert is_redline(cmd) is True
    assert is_hard_redline(cmd) is True


# ------------------------------------------------------ 2. PowerShell -EncodedCommand Base64
@pytest.mark.parametrize("flag", ["EncodedCommand", "enc", "ec", "e"])
@pytest.mark.parametrize("enc", ["utf-16-le", "utf-8"])
def test_ps_encoded_command_base64_detected(flag, enc):
    cmd = _ps_b64(flag, _PS_DELETE, enc)
    # 解码后归一化为明文命令 (路径含 Windows 盘符反斜杠, 不参与断言)
    n = _normalize(cmd)
    assert "Remove-Item" in n and "-Recurse -Force" in n
    assert is_redline(cmd) is True
    assert is_hard_redline(cmd) is True


def test_ps_encoded_command_bad_base64_ignored():
    # 非法 Base64 不应抛异常; 原样保留 (不误伤)
    cmd = "powershell -EncodedCommand !!!not-base64!!!"
    assert is_redline(cmd) is False


# ------------------------------------------------------ 3. ANSI-C 转义 (十六/八进制编码空格/连字符)
@pytest.mark.parametrize("cmd", [
    r"bash -c $'rm\x20-rf\x20/'",        # 空格 → \x20
    r"sh -c $'rm\x20\x2drf\x20/'",       # 连字符 → \x2d
    r"$'\x72m\x20\x2drf\x20/'",          # rm 字母整体编码
    r"$'rm\040\055rf\040/'",             # 八进制 040 空格 / 055 连字符
    r"bash -c $'rm\t-rf\t/'",            # 制表符做分隔
])
def test_ansi_c_escape_normalizes_and_flags(cmd):
    assert "rm -rf " in _normalize(cmd)
    assert is_redline(cmd) is True
    assert is_hard_redline(cmd) is True


def test_ansi_c_escape_benign_not_flaggled():
    # echo/printf/cat 只是打印/传递转义串, 不得误判
    for cmd in [r"echo $'rm\x20-rf\x20/'",
                r"printf '%s\n' $'rm -rf /'",
                r"cat $'notes\x2etxt'"]:
        assert is_redline(cmd) is False, f"误判安全命令: {cmd!r}"


# ------------------------------------------------------ 4. 嵌套命令替换就地内联
@pytest.mark.parametrize("cmd", [
    "sh -c $(echo $(rm -rf /))",          # 双层嵌套, 最内层 rm 浮出
    "eval \"$(echo 'rm -rf /')\"",        # eval + 命令替换
    "rm $(echo -rf) /",                   # 旗标经命令替换浮回
    "rm -rf `echo /`",                    # 反引号变体
])
def test_nested_command_subst_inlined_and_flags(cmd):
    assert "rm" in _normalize(cmd)
    assert is_redline(cmd) is True


def test_nested_command_subst_benign():
    for cmd in ["echo $(date)", "cat $(ls *.py)", "git log $(git rev-parse HEAD)"]:
        assert is_redline(cmd) is False, f"误判: {cmd!r}"


# ------------------------------------------------------ 5. Unicode 同形字 / 组合 / 全角 / 变体选择符
@pytest.mark.parametrize("obf", [
    "рm",        # 西里尔 р = U+0440
    "r\u0301m",  # 组合尖音符 r + U+0301
    "r\uFE0Fm",  # 变体选择符 U+FE0F 割裂 token
    "ＲＭ",      # 全角大写　(U+FF32 U+FF2D)
    "ｒｍ",      # 全角小写 (U+FF52 U+FF4D)
])
def test_unicode_homoglyph_variants_normalize_to_rm(obf):
    cmd = f"{obf} -rf /"
    # 全角大写 ＲＭ 归一化为大写 "RM", 西里尔 р 归一化为小写 "rm" —— 统一小写比较
    assert "rm -rf" in _normalize(cmd).lower(), f"归一化后未见 rm: {cmd!r} -> {_normalize(cmd)!r}"
    assert is_redline(cmd) is True
    assert is_hard_redline(cmd) is True


@pytest.mark.parametrize("obf", [
    "mkdir",
    "touch",
    "рrintf",   # 西里尔 р 用于普通命令, 归一化后应成 printf 但不触发红线
])
def test_unicode_not_creating_false_positive(obf):
    cmd = f"{obf} hello"
    assert is_redline(cmd) is False


# ------------------------------------------------------ 6. 引擎 score 与红线口径一致
def test_bypass_vectors_score_critical():
    eng = SafetyEngine()
    vectors = [
        'bash -c"rm -rf /"',
        _ps_b64("EncodedCommand", _PS_DELETE, "utf-16-le"),
        r"$'rm\x20-rf\x20/'",
        "sh -c $(echo $(rm -rf /))",
        "рm -rf /",
    ]
    for cmd in vectors:
        assert eng.score({"command": cmd})["risk"] == "critical", f"未判 critical: {cmd!r}"
        assert is_redline(cmd) is True


# ------------------------------------------------------ 7. 归一化结果抽验
def test_normalize_ssh_no_space_head():
    assert _normalize("bash -c\"rm -rf /\"").startswith("rm -rf /")


def test_normalize_ps_encoded_textual():
    n = _normalize(_ps_b64("ec", _PS_DELETE, "utf-8"))
    assert "Remove-Item" in n and "-Recurse -Force" in n