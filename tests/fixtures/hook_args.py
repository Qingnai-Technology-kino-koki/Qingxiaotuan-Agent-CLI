"""fixture: 改写型 PreToolUse hook。

读取 stdin JSON, 把 tool_input 追加一个标记字段后输出 {"args": ...},
用于测试 allow_edit_args 开启时参数被改写。
"""
import json
import sys

data = json.loads(sys.stdin.read() or "{}")
tool_input = dict(data.get("tool_input", {}))
tool_input["_hook_injected"] = "yes"
print(json.dumps({"args": tool_input}))
