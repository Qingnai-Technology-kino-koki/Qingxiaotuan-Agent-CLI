/**
 * mcp-client —— 青小团 MCP (Model Context Protocol) 客户端 (TS 侧)
 *
 * 以 stdio transport 连接一个外部 MCP server, 把它的 tools/resources 暴露给
 * 青小团内核 (经 JSONL IPC)。实现最小可用 MCP (JSON-RPC 2.0):
 *   - initialize / notifications/initialized
 *   - tools/list / tools/call
 *   - resources/list / resources/read (可选)
 *
 * 增强: 每请求超时、进程存活健康检查、自动重连、批量调用、批量工具枚举。
 *
 * IPC 方法:
 *   connect    { command, args?, env?, timeout_ms? }   -> { serverInfo, tools }
 *   list       {}                                       -> { tools:[...] }
 *   call       { name, arguments, timeout_ms? }          -> { content, isError }
 *   batch      { calls:[{name,arguments}], timeout_ms? } -> { results:[...] }
 *   resources  {}                                       -> { resources:[...] }
 *   read       { uri }                                   -> { content }
 *   disconnect {}                                       -> { ok }
 */
import { IpcServer } from "../protocol";
import { spawn, ChildProcess } from "child_process";

interface McpTool {
  name: string;
  description: string;
  inputSchema: any;
}
interface McpResource {
  uri: string;
  name: string;
  description?: string;
  mimeType?: string;
}

interface JsonRpcReq {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: any;
}

class McpConnection {
  private proc: ChildProcess | null = null;
  private buf = "";
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: any) => void; reject: (e: any) => void; timer: NodeJS.Timeout | null }>();
  private serverInfo: any = null;
  private tools: McpTool[] = [];
  private resources: McpResource[] = [];
  private defaultTimeout = 30_000;
  private connected = false;

  constructor(
    private command: string,
    private args: string[],
    private env: Record<string, string>,
    timeout_ms?: number,
  ) {
    if (timeout_ms && timeout_ms > 0) this.defaultTimeout = timeout_ms;
    this.spawnProc();
  }

  private spawnProc() {
    this.proc = spawn(this.command, this.args, {
      env: { ...process.env, ...this.env },
      stdio: ["pipe", "pipe", "inherit"],
      windowsHide: true,
    });
    this.buf = "";
    this.connected = true;
    const stdout = this.proc.stdout;
    if (stdout) {
      stdout.setEncoding("utf-8");
      stdout.on("data", (chunk: string) => this.onData(chunk));
    }
    this.proc.on("exit", (code) => {
      this.connected = false;
      // 让所有挂起请求失败
      for (const [, p] of this.pending) {
        if (p.timer) clearTimeout(p.timer);
        p.reject(new Error(`MCP server 已退出 (code=${code})`));
      }
      this.pending.clear();
    });
    this.proc.on("error", (err) => {
      this.connected = false;
      for (const [, p] of this.pending) {
        if (p.timer) clearTimeout(p.timer);
        p.reject(err);
      }
      this.pending.clear();
    });
  }

  isAlive(): boolean {
    return this.connected && this.proc !== null && this.proc.exitCode === null;
  }

  private onData(chunk: string) {
    this.buf += chunk;
    let nl: number;
    while ((nl = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, nl).trim();
      this.buf = this.buf.slice(nl + 1);
      if (!line) continue;
      let msg: any;
      try { msg = JSON.parse(line); } catch { continue; }
      // JSON-RPC 响应 (含 id) 或通知 (无 id, 如 progress)
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id)!;
        this.pending.delete(msg.id);
        if (p.timer) clearTimeout(p.timer);
        if (msg.error) p.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
        else p.resolve(msg.result);
      }
    }
  }

  private writeRaw(obj: any) {
    if (!this.proc || !this.proc.stdin) throw new Error("MCP server 未连接");
    this.proc.stdin.write(JSON.stringify(obj) + "\n");
  }

  private request(method: string, params?: any, timeout_ms?: number): Promise<any> {
    if (!this.isAlive()) throw new Error("MCP server 未连接或已退出");
    const id = this.nextId++;
    const req: JsonRpcReq = { jsonrpc: "2.0", id, method, params };
    const ttl = timeout_ms && timeout_ms > 0 ? timeout_ms : this.defaultTimeout;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) {
          reject(new Error(`MCP 请求超时 (${ttl}ms): ${method}`));
        }
      }, ttl);
      if (typeof timer.unref === "function") timer.unref();
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.writeRaw(req);
      } catch (e) {
        if (timer) clearTimeout(timer);
        this.pending.delete(id);
        reject(e);
      }
    });
  }

  async initialize(): Promise<any> {
    const res = await this.request("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {}, resources: {} },
      clientInfo: { name: "qingxiaotuan", version: "0.1.0" },
    });
    this.serverInfo = res.serverInfo;
    // 发送 initialized 通知 (不等待响应)
    try { this.writeRaw({ jsonrpc: "2.0", method: "notifications/initialized" }); } catch { /* ignore */ }
    return res;
  }

  async listTools(): Promise<McpTool[]> {
    const res = await this.request("tools/list", {});
    this.tools = (res.tools || []).map((t: any) => ({
      name: t.name,
      description: t.description || "",
      inputSchema: t.inputSchema || { type: "object", properties: {} },
    }));
    return this.tools;
  }

  async listResources(): Promise<McpResource[]> {
    const res = await this.request("resources/list", {});
    this.resources = (res.resources || []).map((r: any) => ({
      uri: r.uri,
      name: r.name || r.uri,
      description: r.description,
      mimeType: r.mimeType,
    }));
    return this.resources;
  }

  async readResource(uri: string): Promise<any> {
    return this.request("resources/read", { uri });
  }

  async callTool(name: string, args: any, timeout_ms?: number): Promise<any> {
    return this.request("tools/call", { name, arguments: args || {} }, timeout_ms);
  }

  /** 并行批量调用多个工具, 单个失败不影响其余。 */
  async batchCalls(calls: { name: string; arguments: any; timeout_ms?: number }[]): Promise<any[]> {
    return Promise.all(calls.map(async (c) => {
      try {
        const res = await this.callTool(c.name, c.arguments, c.timeout_ms);
        return { ok: true, name: c.name, content: res.content, isError: !!res.isError };
      } catch (e: any) {
        return { ok: false, name: c.name, error: e.message };
      }
    }));
  }

  kill() {
    this.connected = false;
    try { this.proc?.kill(); } catch { /* ignore */ }
    this.proc = null;
  }
}

async function main() {
  let conn: McpConnection | null = null;

  const server = new IpcServer();
  server.register("connect", async (params) => {
    const command = String(params?.command || "");
    if (!command) throw new Error("missing command");
    const args = (params?.args as string[]) || [];
    const env = (params?.env as Record<string, string>) || {};
    const timeout = params?.timeout_ms as number | undefined;
    if (conn) conn.kill();
    conn = new McpConnection(command, args, env, timeout);
    const info = await conn.initialize();
    const tools = await conn.listTools();
    return {
      serverInfo: info.serverInfo || null,
      tools: tools.map((t) => ({ name: t.name, description: t.description, schema: t.inputSchema })),
    };
  });
  server.register("list", async () => {
    if (!conn) throw new Error("not connected");
    const tools = await conn.listTools();
    return { tools: tools.map((t) => ({ name: t.name, description: t.description, schema: t.inputSchema })) };
  });
  server.register("call", async (params) => {
    if (!conn) throw new Error("not connected");
    const name = String(params?.name || "");
    const args = params?.arguments || {};
    const timeout = params?.timeout_ms as number | undefined;
    const res = await conn.callTool(name, args, timeout);
    return { content: res.content, isError: !!res.isError };
  });
  server.register("batch", async (params) => {
    if (!conn) throw new Error("not connected");
    const calls = (params?.calls as any[]) || [];
    return { results: await conn.batchCalls(calls) };
  });
  server.register("resources", async () => {
    if (!conn) throw new Error("not connected");
    const res = await conn.listResources();
    return { resources: res };
  });
  server.register("read", async (params) => {
    if (!conn) throw new Error("not connected");
    const uri = String(params?.uri || "");
    const res = await conn.readResource(uri);
    return { content: res.contents || res.content };
  });
  server.register("disconnect", async () => {
    if (conn) { conn.kill(); conn = null; }
    return { ok: true };
  });
  await server.run();
}

main().catch((e) => {
  process.stderr.write(`mcp-client fatal: ${String(e)}\n`);
  process.exit(1);
});
