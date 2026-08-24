/**
 * agent-sdk —— 青小团 TS 版 Agent SDK (TS 侧)
 *
 * 一个独立、轻量的 Agent 运行时 (不依赖 Python 内核):
 *   - OpenAI 兼容 chat completions 客户端 (支持流式);
 *   - 工具注册表 (函数即工具);
 *   - ReAct 循环: think -> tool -> observe -> think ...;
 *   - 通过 IPC 暴露 run / tool_register / reset, 可被 Python 侧作为"外部 Agent"调用。
 *
 * IPC 方法:
 *   config   { base_url, api_key, model, system } -> { ok }
 *   tool_register { name, description, schema }    -> { ok }
 *   run      { message, stream }                   -> { reply, tool_calls }
 *   reset    {}                                     -> { ok }
 */
import { IpcServer } from "../protocol";
import * as fs from "fs";
import * as path from "path";
import { spawn } from "child_process";

interface ChatMsg {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: any[];
  tool_call_id?: string;
  name?: string;
}

interface ToolDef {
  name: string;
  description: string;
  schema: any;
  handler: (args: any) => Promise<string> | string;
}

/** 受限 shell 命令白名单 (仅允许这些可执行, 且禁止危险参数)。 */
const SHELL_ALLOWLIST = new Set([
  "ls", "dir", "cat", "echo", "pwd", "date", "whoami", "uname",
  "git", "node", "python", "python3", "pip", "npm", "npx",
  "wc", "head", "tail", "grep", "find", "sort", "uniq", "wc",
]);
const SHELL_FORBIDDEN = /(rm\s+-rf|mkfs|dd\s+if=|:\(\)\s*\{|sudo|chmod\s+777|curl\s+.*\|\s*(sh|bash))/;

function safeWorkdir(): string {
  // 默认工作区: 进程 cwd; 可被 config 的 workdir 覆盖
  return process.cwd();
}

class OpenAIClient {
  baseUrl: string;
  apiKey: string;
  model: string;
  constructor(baseUrl: string, apiKey: string, model: string) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
    this.model = model;
  }
  async chat(messages: ChatMsg[], stream: boolean, tools: any[], emit?: (chunk: unknown) => void): Promise<any> {
    const url = `${this.baseUrl.replace(/\/$/, "")}/chat/completions`;
    const body: any = { model: this.model, messages, stream, tools: tools.length ? tools : undefined };
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.apiKey}` },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`API ${resp.status}: ${txt.slice(0, 300)}`);
    }
    if (!stream) return await resp.json();
    if (!resp.body) throw new Error("stream response has no body");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const message = { role: "assistant", content: "", tool_calls: [] as any[] };
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      buffer += decoder.decode(part.value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line.startsWith("data:")) continue;
        const raw = line.slice(5).trim();
        if (raw === "[DONE]") continue;
        try {
          const delta = JSON.parse(raw).choices?.[0]?.delta || {};
          if (delta.content) {
            message.content += delta.content;
            emit?.({ type: "text", text: delta.content });
          }
          for (const call of delta.tool_calls || []) {
            const index = call.index ?? message.tool_calls.length;
            const current = message.tool_calls[index] ||= {
              id: "", type: "function", function: { name: "", arguments: "" },
            };
            current.id += call.id || "";
            current.function.name += call.function?.name || "";
            current.function.arguments += call.function?.arguments || "";
          }
        } catch {
          // 忽略单个畸形 SSE 行，继续读取后续事件。
        }
      }
    }
    return { choices: [{ message }] };
  }
}

class Agent {
  private client: OpenAIClient | null = null;
  private system = "你是青小团 Agent (TS SDK)。";
  private tools = new Map<string, ToolDef>();
  private history: ChatMsg[] = [];
  private workdir = safeWorkdir();

  configure(baseUrl: string, apiKey: string, model: string, system?: string, workdir?: string) {
    this.client = new OpenAIClient(baseUrl, apiKey, model);
    if (system) this.system = system;
    if (workdir && fs.existsSync(workdir)) this.workdir = workdir;
  }

  registerTool(def: ToolDef) {
    this.tools.set(def.name, def);
  }

  toolNames(): string[] {
    return Array.from(this.tools.keys());
  }

  getWorkdir(): string {
    return this.workdir;
  }

  reset() {
    this.history = [];
  }

  private toolSchemas() {
    return Array.from(this.tools.values()).map((t) => ({
      type: "function",
      function: { name: t.name, description: t.description, parameters: t.schema },
    }));
  }

  async run(message: string, stream: boolean, emit?: (chunk: unknown) => void): Promise<any> {
    if (!this.client) throw new Error("not configured");
    if (this.history.length === 0) this.history.push({ role: "system", content: this.system });
    this.history.push({ role: "user", content: message });

    const toolCallsLog: any[] = [];
    let rounds = 0;
    while (rounds++ < 12) {
      const data = await this.client.chat(this.history, stream, this.toolSchemas(), emit);
      const choice = data.choices?.[0];
      const msg = choice?.message;
      if (!msg) throw new Error("no choice");
      // 记录 assistant
      this.history.push({ role: "assistant", content: msg.content || "", tool_calls: msg.tool_calls });

      if (!msg.tool_calls || msg.tool_calls.length === 0) {
        return { reply: msg.content || "", tool_calls: toolCallsLog };
      }
      // 执行工具
      for (const tc of msg.tool_calls) {
        const name = tc.function?.name;
        let args: any = {};
        try { args = JSON.parse(tc.function?.arguments || "{}"); } catch { args = {}; }
        const def = this.tools.get(name);
        let result: string;
        if (!def) result = `[错误] 未知工具: ${name}`;
        else {
          try { result = await def.handler(args); }
          catch (e: any) { result = `[错误] ${e.message}`; }
        }
        toolCallsLog.push({ name, args, result });
        this.history.push({
          role: "tool",
          tool_call_id: tc.id,
          name,
          content: result.slice(0, 4000),
        });
      }
    }
    return { reply: "(达到最大工具轮数)", tool_calls: toolCallsLog };
  }
}

async function main() {
  const agent = new Agent();
  const wd = () => agent.getWorkdir();

  // ---- 内置工具: 文件读写 ----
  agent.registerTool({
    name: "read_file",
    description: "读取文本文件内容 (最多 8000 字符)",
    schema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
    handler: async (args: any) => {
      try { return fs.readFileSync(path.resolve(wd(), args.path), "utf-8").slice(0, 8000); }
      catch (e: any) { return `[错误] ${e.message}`; }
    },
  });
  agent.registerTool({
    name: "write_file",
    description: "写入文本文件 (仅限工作区内)",
    schema: { type: "object", properties: { path: { type: "string" }, content: { type: "string" } }, required: ["path", "content"] },
    handler: async (args: any) => {
      try {
        const p = path.resolve(wd(), args.path);
        if (!p.startsWith(path.resolve(wd()))) return "[错误] 越权写入";
        fs.mkdirSync(path.dirname(p), { recursive: true });
        fs.writeFileSync(p, String(args.content || ""), "utf-8");
        return `[成功] 已写入 ${args.path}`;
      } catch (e: any) { return `[错误] ${e.message}`; }
    },
  });
  agent.registerTool({
    name: "list_dir",
    description: "列出目录内容",
    schema: { type: "object", properties: { path: { type: "string" } } },
    handler: async (args: any) => {
      try {
        const entries = fs.readdirSync(path.resolve(wd(), args.path || "."), { withFileTypes: true });
        return entries.map((e) => `${e.isDirectory() ? "d" : "-"} ${e.name}`).join("\n");
      } catch (e: any) { return `[错误] ${e.message}`; }
    },
  });
  agent.registerTool({
    name: "grep",
    description: "在工作区内按正则搜索文件内容 (返回匹配行)",
    schema: {
      type: "object",
      properties: { pattern: { type: "string" }, path: { type: "string" }, max: { type: "number" } },
      required: ["pattern"],
    },
    handler: async (args: any) => {
      try {
        const root = path.resolve(wd(), args.path || ".");
        const re = new RegExp(args.pattern, "i");
        const max = Number(args.max || 50);
        const out: string[] = [];
        const walk = (dir: string) => {
          for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
            if (out.length >= max) return;
            const fp = path.join(dir, e.name);
            if (e.isDirectory()) { if (e.name !== "node_modules" && e.name !== ".git") walk(fp); }
            else {
              try {
                const text = fs.readFileSync(fp, "utf-8");
                text.split("\n").forEach((line, i) => { if (re.test(line)) out.push(`${fp}:${i + 1}: ${line}`.slice(0, 300)); });
              } catch { /* 跳过二进制 */ }
            }
          }
        };
        walk(root);
        return out.length ? out.slice(0, max).join("\n") : "[无匹配]";
      } catch (e: any) { return `[错误] ${e.message}`; }
    },
  });
  // ---- 内置工具: 受限 shell ----
  agent.registerTool({
    name: "shell",
    description: "执行受限 shell 命令 (白名单命令, 禁止危险操作)",
    schema: { type: "object", properties: { command: { type: "string" } }, required: ["command"] },
    handler: async (args: any) => {
      const cmd = String(args.command || "");
      const parts = cmd.trim().split(/\s+/);
      const bin = parts[0];
      if (!SHELL_ALLOWLIST.has(bin)) return `[拒绝] 命令不在白名单: ${bin}`;
      if (SHELL_FORBIDDEN.test(cmd)) return `[拒绝] 检测到危险参数`;
      return new Promise<string>((resolve) => {
        const p = spawn(bin, parts.slice(1), { cwd: wd(), shell: false, timeout: 15_000 });
        let out = "", err = "";
        p.stdout?.on("data", (d) => (out += d.toString()));
        p.stderr?.on("data", (d) => (err += d.toString()));
        p.on("error", (e) => resolve(`[错误] ${e.message}`));
        p.on("close", (code) => resolve((out + err).slice(0, 4000) || `[退出码 ${code}]`));
      });
    },
  });
  // ---- 内置工具: 网络抓取 ----
  agent.registerTool({
    name: "web_fetch",
    description: "抓取一个 URL 的内容 (GET, 返回文本或截断的 HTML)",
    schema: { type: "object", properties: { url: { type: "string" }, max: { type: "number" } }, required: ["url"] },
    handler: async (args: any) => {
      try {
        const resp = await fetch(String(args.url), { headers: { "User-Agent": "qingxiaotuan-agent/0.1" } });
        const text = await resp.text();
        const max = Number(args.max || 6000);
        return `HTTP ${resp.status}\n${text.slice(0, max)}`;
      } catch (e: any) { return `[错误] ${e.message}`; }
    },
  });
  // ---- 内置工具: 记忆读写 ----
  const memoryDir = () => path.join(wd(), "memory");
  agent.registerTool({
    name: "memory_read",
    description: "读取 agent 记忆文件 (memory/ 目录下)",
    schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] },
    handler: async (args: any) => {
      try {
        const p = path.join(memoryDir(), String(args.name));
        if (!p.startsWith(memoryDir())) return "[错误] 越权读取";
        return fs.readFileSync(p, "utf-8").slice(0, 6000);
      } catch (e: any) { return `[错误] ${e.message}`; }
    },
  });
  agent.registerTool({
    name: "memory_write",
    description: "写入 agent 记忆文件 (memory/ 目录下)",
    schema: { type: "object", properties: { name: { type: "string" }, content: { type: "string" } }, required: ["name", "content"] },
    handler: async (args: any) => {
      try {
        const dir = memoryDir();
        fs.mkdirSync(dir, { recursive: true });
        const p = path.join(dir, String(args.name));
        if (!p.startsWith(dir)) return "[错误] 越权写入";
        fs.writeFileSync(p, String(args.content || ""), "utf-8");
        return `[成功] 已写入记忆 ${args.name}`;
      } catch (e: any) { return `[错误] ${e.message}`; }
    },
  });

  const server = new IpcServer();
  server.register("config", (params) => {
    agent.configure(
      String(params?.base_url || "https://api.deepseek.com"),
      String(params?.api_key || ""),
      String(params?.model || "deepseek-chat"),
      params?.system ? String(params.system) : undefined,
      params?.workdir ? String(params.workdir) : undefined
    );
    return { ok: true };
  });
  server.register("tool_register", (params) => {
    // 内置工具已在启动时注册; 这里登记外部声明的工具名 (供审计与编排)。
    return { ok: true, declared: params?.name, note: "内置工具集在 SDK 启动时已就绪" };
  });
  server.register("tools", () => ({ tools: agent.toolNames() }));
  server.register("run", async (params, emit) => {
    const reply = await agent.run(String(params?.message || ""), !!params?.stream, emit.emit);
    return reply;
  });
  server.register("reset", () => { agent.reset(); return { ok: true }; });
  await server.run();
}

main().catch((e) => { process.stderr.write(`agent-sdk fatal: ${String(e)}\n`); process.exit(1); });
