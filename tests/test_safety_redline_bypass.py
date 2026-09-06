"""safety 引擎红线绕过加固测试 (v1.3)。

覆盖本轮新修的间接写法绕过:
1. 解释器旗标与命令无空格: sh -c"rm -rf /" / python3 -c"..."
2. ANSI-C quoting 转义解码: $'rm\\x20-rf\\x20/' (十六进制编码空格)
3. IFS 字段分隔符: rm${IFS}-rf${IFS}/ (无空字符串)
4. eval + 命令替换: 替换结果会被 eval 当作代码执行, 静态不可验证 → fail-closed 拒绝
5. 嵌套命令替换内联: rm $(echo -rf) / 中旗标浮回命令行

同时保证安全命令不被误判 (echo 内容 / 普通脚本 / grep 字符串 / 无旗标解释器调用)。
"""
from qingxiaotuan.ext.safety_engine import (
    _normalize,
    is_hard_redline,
    is_redline,
    is_benign_dev_command,
)
from qingxiaotuan.ext.safety_engine import SafetyEngine
from qingxiaotuan.tools.shell import is_redline as shell_is_redline


# -------------------------------------------------- 1. 旗标与命令无空格
def test_redline_interp_no_space():
    r"""sh -c"..." / python3 -c"..." 无空格写法此前因 _INTERP_RE 要求 \s+ 而漏判。"""
    hits = [
        'sh -c"rm -rf /"',
        "bash -c'rm -rf /'",
        'python3 -c"rm -rf /"',
        'sh -c"chmod -R 000 /"',
        'bash -c"mkfs.ext4 /dev/sdb"',
        'cmd /c"rm -rf /"',
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判无空格解释器包装: {cmd!r}"
        assert shell_is_redline(cmd), f"shell.is_redline 漏判: {cmd!r}"


def test_redline_interp_no_space_safe():
    """无空格包装的良性命令不得误判。"""
    safe = [
        'python3 -c"print(1)"',
        'python -c"print(\'hi\')"',
        'sh -c"echo hi"',
        'bash -c"exit 0"',
        "bash script.sh",
        "python script.py",
    ]
    for cmd in safe:
        assert not is_redline(cmd), f"红线误判安全命令: {cmd!r}"


# -------------------------------------------------- 2. ANSI-C 转义解码
def test_redline_ansi_c_hex_escape():
    r"""$\x20 把空格编码成十六进制转义, 未解码前是单个 token 会整体漏判。"""
    hits = [
        r"bash -c $'rm\x20-rf\x20/'",
        r"sh -c $'rm\x20-rf\x20/'",
        r"$'rm\x20-rf\x20/'",
        r"bash -c $'\x72m\x20\x2drf\x20/'",     # 连 rm 字母都编码
        r"sh -c $'rm\t-rf\t/'",                  # 制表符做分隔
        r"eval $'rm\x20-rf\x20/'",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判 ANSI-C 转义混淆: {cmd!r}"


def test_redline_ansi_c_octal_escape():
    """八进制转义 \\0 也要解码 (040 = 空格, 055 = 连字符 -)。"""
    hits = [
        r"bash -c $'rm\040\055rf\040/'",   # 040 空格 + 055 连字符 → rm -rf /
        r"$'rm\040\055rf\040/'",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判八进制转义: {cmd!r}"


def test_redline_ansi_c_escape_safe():
    """ANSI-C 转义解码不得误伤 benign (echo 输出转义串是打印, 非执行)。"""
    safe = [
        r"echo $'rm\x20-rf\x20/'",      # echo 只是打印
        r"printf '%s\n' $'rm -rf /'",   # printf 只是打印
        r"cat $'notes\x2etxt'",         # 普通文件路径含转义
    ]
    for cmd in safe:
        assert not is_redline(cmd), f"红线误判 ANSI-C 安全命令: {cmd!r}"


# -------------------------------------------------- 3. IFS 字段分隔符
def test_redline_ifs_word_splitting():
    """rm${IFS}-rf${IFS}/ 用 IFS 变量充当空格, token 化前必须还原为真实空白。"""
    hits = [
        "rm${IFS}-rf${IFS}/",
        "rm$IFS-rf$IFS/",
        "sh${IFS}-c${IFS}'rm${IFS}-rf${IFS}/'",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判 IFS 混淆: {cmd!r}"


def test_redline_ifs_safe():
    safe = [
        "echo $IFS",
        "ls -la $IFS/foo",
        "cat $IFS",
    ]
    for cmd in safe:
        assert not is_redline(cmd), f"红线误判 IFS 安全命令: {cmd!r}"


# -------------------------------------------------- 4. eval + 命令替换
def test_redline_eval_command_subst():
    """eval 包裹 $()/反引号: 替换结果会被 eval 当代码执行, 静态不可验证 → 拒绝。"""
    hits = [
        'eval "$(echo \'rm -rf /\')"',
        'eval `echo \'rm -rf /\'`',
        'eval "$(printf \'%s\' \'rm -rf /\')"',
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判 eval+命令替换: {cmd!r}"
        assert is_hard_redline(cmd), f"硬红线漏判 eval+命令替换: {cmd!r}"


def test_redline_eval_static_still_caught():
    """静态可见的 eval 内容仍走常规检测 (无回归)。"""
    hits = [
        'eval "rm -rf /"',
        'eval \'rm -rf /\'',
        'sh -c "eval \'rm -rf /\'"',
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判静态 eval: {cmd!r}"


def test_redline_eval_command_subst_safe():
    """eval 无命令替换时不做不可验证拦截; 良性命令不误判。"""
    safe = [
        'eval "echo hi"',
        "echo $(date)",
        "cat $(ls *.txt)",
    ]
    for cmd in safe:
        assert not is_redline(cmd), f"红线误判安全命令: {cmd!r}"


# -------------------------------------------------- 5. 嵌套命令替换内联
def test_redline_nested_cmd_subst_flags_surfaced():
    """就地内联: rm $(echo -rf) / 中旗标浮回命令行, 命中递归强删。"""
    hits = [
        "rm $(echo -rf) /",
        "rm -r $(echo -f) /",
        "rm -rf $(echo /)",
        "rm -rf `echo /`",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判命令替换内联: {cmd!r}"


def test_redline_nested_cmd_subst_safe():
    """命令替换内的 benign 内容不误判。"""
    safe = [
        "echo $(date)",
        "git log $(git rev-parse HEAD)",
        "ls $(find . -name '*.py')",
        "cat $(echo file.txt)",
    ]
    for cmd in safe:
        assert not is_redline(cmd), f"红线误判命令替换安全命令: {cmd!r}"


# -------------------------------------------------- 6. 引擎 score 口径一致
def test_score_consistent_with_redline():
    eng = SafetyEngine()
    for cmd in ['sh -c"rm -rf /"', r"bash -c $'rm\x20-rf\x20/'",
                "rm${IFS}-rf${IFS}/", 'eval "$(echo \'rm -rf /\')"']:
        risk = eng.score({"command": cmd})["risk"]
        assert risk == "critical", f"score 未判 critical: {cmd!r} risk={risk}"
        assert is_redline(cmd), f"is_redline 未命中: {cmd!r}"


# -------------------------------------------------- 7. 归一化结果抽查
def test_normalize_decodes_obfuscation():
    # 注: Windows 下 realpath('/') 会解析为盘符根 (如 E:\)，故只断言 "rm -rf" 前缀
    assert "rm -rf" in _normalize(r"sh -c $'rm\x20-rf\x20/'")
    assert "rm -rf" in _normalize("rm${IFS}-rf${IFS}/")
    assert "rm -rf" in _normalize(r"bash -c $'rm\040\055rf\040/'")
