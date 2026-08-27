"""最小 MCP Server (stdio) —— 仅用于 qingxiaotuan 的 MCP 桥接测试。

实现协议最小集:
  - initialize        -> 返回 protocolVersion / capabilities / serverInfo
  - notifications/initialized -> 忽略 (无响应)
  - tools/list        -> 返回两个演示工具
  - tools/call        -> 调用演示工具并返回文本结果

不依赖任何第三方库, 纯标准库即可运行:
    python mcp_demo_server.py
"""

from __future__ import annotations

import json
import sys


TOOLS = [
    {
        "name": "echo_text",
        "description": "回显输入的文本 (演示 MCP 桥接)",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_numbers",
        "description": "返回两个整数之和 (演示 MCP 桥接)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "加数"},
                "b": {"type": "integer", "description": "加数"},
            },
            "required": ["a", "b"],
        },
    },
]


def _handle(method: str, params: dict, rid) -> dict:
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "demo", "version": "0.1"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        if name == "echo_text":
            content = [{"type": "text", "text": f"echo: {args.get('text', '')}"}]
        elif name == "add_numbers":
            try:
                s = int(args.get("a", 0)) + int(args.get("b", 0))
            except (TypeError, ValueError):
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": "参数必须是整数"}], "isError": True},
                }
            content = [{"type": "text", "text": str(s)}]
        else:
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True},
            }
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": content}}
    return {
        "jsonrpc": "2.0", "id": rid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> None:
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params", {}) or {}
        if rid is None:
            # 通知 (如 notifications/initialized): 不响应
            continue
        resp = _handle(method, params, rid)
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
