"""fixture: 审计型 hook (只读)。

读取 stdin JSON 事件, 追加写入环境变量 HOOK_AUDIT_LOG 指向的文件 (一行 JSON),
stdout 不输出任何决策。用于验证 PreToolUse/PostToolUse/Session 各事件均被触发。
"""
import json
import os
import sys

data = json.loads(sys.stdin.read() or "{}")
log_path = os.environ.get("HOOK_AUDIT_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
