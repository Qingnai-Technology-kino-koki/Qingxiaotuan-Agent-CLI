# -*- coding: utf-8 -*-
"""批量将非 TUI 模块的自贬「移植」注释改为『自研实现（接口对齐）』表述。
仅在源文件首个 docstring / 归属注释内做精确替换，避免大改代码。
"""
import io

BASE = r"E:\Qingxiaotuan Agent CLI"

# (相对路径, [(old, new), ...])
PLAN = {
    r"qingxiaotuan\kernel\__init__.py": [
        ("青小团对 Kimi Code `kernel` 大模型抽象层的 Python 忠实移植。",
         "青小团自研的大模型抽象层（接口对齐 Kimi Code `kernel`，独立 Python 实现）。"),
    ],
    r"qingxiaotuan\kernel\contract.py": [
        ("忠实移植 Kimi Code", "自研实现（接口对齐 Kimi Code"),
    ],
    r"qingxiaotuan\kernel\session\contracts.py": [
        ("忠实移植：", "自研实现（接口对齐 Kimi Code）："),
    ],
    r"qingxiaotuan\kernel\agent\step_retry.py": [("移植自 Kimi", "自研实现（接口对齐 Kimi")],
    r"qingxiaotuan\kernel\agent\step_request.py": [("移植自 Kimi", "自研实现（接口对齐 Kimi")],
    r"qingxiaotuan\kernel\agent\step_queue.py": [("移植自 Kimi", "自研实现（接口对齐 Kimi")],
    r"qingxiaotuan\kernel\agent\loop.py": [("移植自 Kimi", "自研实现（接口对齐 Kimi")],
    r"qingxiaotuan\kernel\agent\errors.py": [("移植自 Kimi", "自研实现（接口对齐 Kimi")],
    r"qingxiaotuan\kernel\agent\continuation.py": [("移植自 Kimi", "自研实现（接口对齐 Kimi")],
    r"qingxiaotuan\kernel\agent\config.py": [("移植自 Kimi", "自研实现（接口对齐 Kimi")],
    r"qingxiaotuan\kernel\tools\__init__.py": [
        ("把 Kimi Code 的「工具系统 + 权限」子系统 port 进 Python 青小团 CLI。",
         "青小团自研的工具系统 + 权限子系统（接口对齐 Kimi Code 的「工具系统 + 权限」）。"),
    ],
    r"qingxiaotuan\kernel\tools\scheduler.py": [("移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\tools\registry.py": [("移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\tools\permission.py": [("移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\tools\gate.py": [("移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\tools\executor.py": [("移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\tools\contract.py": [("移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\tools\before_execute_event.py": [("移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\tools\args.py": [("简化移植 Kimi Code", "自研实现（接口对齐 Kimi Code")],
    r"qingxiaotuan\kernel\mcp\__init__.py": [
        ("把 Kimi Code 的 ``mcpCore`` + ``agent/mcp`` 移植到 Python。",
         "自研实现（接口对齐 Kimi Code 的 ``mcpCore`` + ``agent/mcp``）。"),
    ],
    r"qingxiaotuan\kernel\mcp\naming.py": [
        ("逐字节移植 ``mcpCore/tool-naming.ts``。", "自研实现（接口对齐 ``mcpCore/tool-naming.ts``）。"),
    ],
    r"qingxiaotuan\kernel\acp\__init__.py": [
        ("kimi-code acp-server 的 Python 忠实移植（精简同步版）。",
         "kimi-code acp-server 的自研实现（接口对齐，精简同步版）。"),
    ],
    r"qingxiaotuan\kernel\acp\server.py": [
        ("port of kimi-code", "自研实现（接口对齐 kimi-code"),
    ],
    r"qingxiaotuan\acp\__init__.py": [
        ("桥接：复用 kimi-code acp-server 的 Python 移植（qingxiaotuan.kernel.acp），",
         "桥接：复用自研 kernel.acp（接口对齐 kimi-code acp-server），"),
    ],
    r"qingxiaotuan\cli\cmd_acp.py": [
        ("吸收 kimi-code", "自研实现（接口对齐 kimi-code"),
    ],
}

report = []
for rel, pairs in PLAN.items():
    path = BASE + "\\" + rel
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    for old, new in pairs:
        n = text.count(old)
        if n == 0:
            report.append(f"!! [{rel}] 未找到: {old!r}")
            continue
        text = text.replace(old, new)
        report.append(f"ok [{rel}] x{n}: {old[:28]!r} -> {new[:28]!r}")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)

print("\n".join(report))