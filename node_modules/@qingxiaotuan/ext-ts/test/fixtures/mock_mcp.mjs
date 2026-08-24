/**
 * mock_mcp.mjs —— 一个极简 MCP (Model Context Protocol) stdio 服务器, 仅用于测试。
 * 支持: initialize / notifications/initialized / tools/list / tools/call。
 * 暴露一个工具 echo: 把传入的文本原样返回 (带前缀)。
 */
import { createInterface } from "readline";

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
let id = 0;

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

const TOOLS = [{
  name: "echo",
  description: "回显文本",
  inputSchema: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
}];

rl.on("line", (line) => {
  let req;
  try { req = JSON.parse(line); } catch { return; }
  if (!req || req.jsonrpc !== "2.0") return;

  if (req.method === "initialize") {
    send({ jsonrpc: "2.0", id: req.id, result: { protocolVersion: "2024-11-05", serverInfo: { name: "mock-mcp", version: "1.0.0" }, capabilities: { tools: {} } } });
  } else if (req.method === "notifications/initialized") {
    // 通知, 无需响应
  } else if (req.method === "tools/list") {
    send({ jsonrpc: "2.0", id: req.id, result: { tools: TOOLS } });
  } else if (req.method === "tools/call") {
    const name = req.params?.name;
    const text = req.params?.arguments?.text || "";
    if (name === "echo") {
      send({ jsonrpc: "2.0", id: req.id, result: { content: [{ type: "text", text: `echo: ${text}` }] } });
    } else {
      send({ jsonrpc: "2.0", id: req.id, error: { code: -1, message: `unknown tool ${name}` } });
    }
  }
});
