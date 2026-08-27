"""fixture: 阻断型 PreToolUse hook。

读取 stdin JSON, 若工具名以 delete 开头则输出 {"decision":"block",...},
否则输出空 (放行)。用于测试 PreToolUse 阻断链路。
"""
import json
import sys

data = json.loads(sys.stdin.read() or "{}")
tool = data.get("tool_name", "")
if tool.startswith("delete"):
    print(json.dumps({"decision": "block", "reason": f"hook 禁止危险工具 {tool}"}))
