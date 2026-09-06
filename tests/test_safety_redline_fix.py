"""safety 引擎红线修复测试 (对应 CODE_REVIEW.md §4.2)。

验证两类修复:
1. is_redline 现覆盖 score() 的全部 critical 口径 (CRITICAL_PATTERNS 正则库):
   chmod -R 000 /, chown -R root /, mkfs, dd, format, DROP/DELETE 等,
   在 YOLO 红线判定中不再被放行。
2. _normalize 递归展开执行间接层, 让真实命令浮到段首:
   sh -c / bash -c / eval / python -c(字面 shell) / sudo -u / xargs / env /
   find -exec / find -delete 等绕过写法均命中红线, 同时安全命令不误判。
"""
from qingxiaotuan.ext.safety_engine import is_redline
from qingxiaotuan.tools.shell import is_redline as shell_is_redline


# -------------------------------------------------- 修复 1: is_redline 覆盖 CRITICAL_PATTERNS
def test_is_redline_covers_critical_regex_patterns():
    """此前 is_redline 只调 4 个 token 函数, 这些正则致命命令在 YOLO 下被放行。"""
    commands = [
        "chmod -R 000 /",
        "chmod -r 000 /",
        "chown -R root /",
        "mkfs.ext4 /dev/sdb",
        "dd if=/dev/zero of=/dev/sda",
        "format c:",
        "DROP TABLE users",
        "drop table logs",
        "DELETE FROM logs",
        "shutdown -h now",
        "init 0",
        "systemctl poweroff",
    ]
    for cmd in commands:
        assert is_redline(cmd), f"is_redline 漏判 critical 正则命令: {cmd!r}"
        assert shell_is_redline(cmd), f"shell.is_redline 漏判 critical 正则命令: {cmd!r}"


# -------------------------------------------------- 修复 2: 执行间接层绕过
def test_redline_interpreter_wrappers():
    hits = [
        'sh -c "rm -rf /"',
        "bash -c 'rm -rf /'",
        "zsh -c 'rm -rf /'",
        'eval "rm -rf /"',
        'python -c "rm -rf /"',          # -c 参数为字面 shell 命令字符串
        'sh -c "chmod -R 000 /"',
        "bash -c 'mkfs.ext4 /dev/sdb'",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判解释器包装: {cmd!r}"


def test_redline_nested_interpreter_wrappers():
    """嵌套包装: sh -c "bash -c 'rm -rf /'" 也应收敛到真实命令。"""
    hits = [
        'sh -c "bash -c \'rm -rf /\'"',
        'sh -c "eval \'rm -rf /\'"',
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判嵌套解释器包装: {cmd!r}"


def test_redline_sudo_with_flags():
    """sudo -u / -g / -i 等带参变体此前只剥首个 sudo token, 留 -u 导致漏判。"""
    hits = [
        "sudo -u alice rm -rf /",
        "sudo -g root rm -rf /",
        "sudo -i rm -rf /",
        "sudo -u deploy -g deploy rm -rf /data",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判 sudo 带参变体: {cmd!r}"


def test_redline_passthrough_wrappers():
    """xargs / env / timeout / nice 等透传包装剥离后内层命令命中红线。"""
    hits = [
        "xargs rm -rf",
        "env rm -rf /",
        "timeout 5 rm -rf /",
        "nice -n 5 rm -rf /",
        "nohup rm -rf /",
        "command rm -rf /",
        r"find / -name '*.pyc' -exec rm -rf {} \;",
        r"find / -exec sh -c 'rm -rf /' \;",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判透传包装: {cmd!r}"


def test_redline_find_delete():
    """find -delete 递归删除文件, 判为 critical, 红线拦截。"""
    hits = [
        "find / -delete",
        "find /var/log -name '*.log' -delete",
        "find . -path '*/cache/*' -delete",
    ]
    for cmd in hits:
        assert is_redline(cmd), f"红线漏判 find -delete: {cmd!r}"


def test_redline_indirection_not_overblocking():
    """间接写法展开不得误伤安全命令 (echo 内容 / 普通脚本 / grep 含 rm 字符串)。"""
    safe = [
        'echo "rm -rf /"',
        "echo 'sh -c \"rm -rf /\"'",
        "grep -r 'rm -rf /' src/",
        "bash script.sh",
        "find . -name '*.py'",
        "cat file | sh -c 'echo hi'",          # 管道到 sh, 但 sh 参数为 echo, 非危险
        "python -c 'print(1)'",
        "sudo systemctl status nginx",
    ]
    for cmd in safe:
        assert not is_redline(cmd), f"红线误判安全命令: {cmd!r}"


def test_redline_consistent_with_score_critical():
    """is_redline 与 score() 的 critical 判定口径必须一致。"""
    from qingxiaotuan.ext.safety_engine import SafetyEngine
    eng = SafetyEngine()
    for cmd in ["chmod -R 000 /", "mkfs.ext4 /dev/sdb", "DROP TABLE users",
                "rm -rf /", "git push --force", "format c:", "dd if=/dev/zero of=/dev/sda"]:
        risk = eng.score({"command": cmd})["risk"]
        assert (risk == "critical") == is_redline(cmd), (
            f"is_redline 与 score 不一致: {cmd!r} risk={risk} is_redline={is_redline(cmd)}")


def test_dd_block_device_blind_spot_closed():
    """修复: dd 写裸盘此前只拦 /dev/sd*, NVMe(/dev/nvme*)/virtio(/dev/vd*)/IDE(/dev/hd*) 可写穿。

    裸盘擦除是最危险的不可逆操作之一, 必须覆盖全部常见块设备命名。
    """
    blocked = [
        "dd if=/dev/zero of=/dev/nvme0n1",
        "dd if=/dev/zero of=/dev/nvme1n1p1",
        "dd if=/dev/urandom of=/dev/vda",
        "dd if=/dev/zero of=/dev/hda",
        "echo x > /dev/nvme0n1",
        "echo payload > /dev/vdb",
    ]
    for cmd in blocked:
        assert is_redline(cmd), f"dd 裸盘盲区未拦截: {cmd!r}"
        assert shell_is_redline(cmd), f"shell.is_redline 裸盘盲区未拦截: {cmd!r}"
    # 普通安全重定向 (含 /dev/null) 不应被误判为红线
    assert not is_redline("echo x > /dev/null"), "误伤 /dev/null 重定向"
