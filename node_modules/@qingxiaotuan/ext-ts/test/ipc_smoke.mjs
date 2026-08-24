/**
 * ipc_smoke.mjs —— 青小团 TS 外部模块 IPC 冒烟测试。
 * 逐个 spawn 各模块 (node --experimental-strip-types), 等待 {ready:true},
 * 发送请求并校验响应。覆盖: rules / plugin-host / mcp-client / skill-market / dashboard / agent-sdk。
 */
import { spawn } from "child_process";
import * as path from "path";
import * as fs from "fs";
import * as os from "os";

const NODE = "C:/Users/28726/.workbuddy/binaries/node/versions/22.22.2/node.exe";
const SRC = path.resolve("src");
const ROOT = process.cwd();
const FIX = path.join(ROOT, "test", "fixtures");

let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name} ${extra}`); }
}

/**
 * 启动一个模块, 依次发送 requests, 返回每个请求的响应。
 * @param {string} mainRel 例如 "rules/main.ts"
 * @param {Array<{method:string,params?:object}>} requests
 * @param {object} opts { env, cwd, expectReady }
 */
function talk(mainRel, requests, opts = {}) {
  return new Promise((resolve) => {
    const p = spawn(NODE, ["--experimental-strip-types", path.join(SRC, mainRel)], {
      stdio: ["pipe", "pipe", "inherit"],
      env: { ...process.env, ...(opts.env || {}) },
      cwd: opts.cwd || ROOT,
    });
    let buf = "";
    let ready = false;
    const responses = [];
    let idx = 0;
    let idSeq = 1;

    function sendNext() {
      if (idx >= requests.length) {
        // 全部发完, 发送 _quit 结束
        p.stdin.write(JSON.stringify({ id: 0, method: "_quit" }) + "\n");
        return;
      }
      const r = requests[idx];
      const id = idSeq++;
      p.stdin.write(JSON.stringify({ id, method: r.method, params: r.params || {} }) + "\n");
    }

    p.stdout.on("data", (chunk) => {
      buf += chunk.toString();
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let msg;
        try { msg = JSON.parse(line); } catch { continue; }
        if (msg.ready === true) {
          if (!ready) { ready = true; sendNext(); }
          continue;
        }
        if (msg.stream === true) continue;
        // 响应
        responses.push(msg);
        idx++;
        if (idx < requests.length) sendNext();
        else p.stdin.write(JSON.stringify({ id: 0, method: "_quit" }) + "\n");
      }
    });
    p.on("exit", () => resolve({ ready, responses }));
    p.on("error", (e) => resolve({ ready, responses, error: String(e) }));
  });
}

async function run() {
  // ---- rules ----
  console.log("[rules]");
  const r = await talk("rules/main.ts", [
    { method: "load", params: { rules_yaml: `- id: no-todo\n  severity: error\n  match:\n    path: "*.py"\n  assert: 'not contains(content, "TODO")'\n  message: 发现TODO` } },
    { method: "check", params: { path: "app.py", content: "x = 1 # TODO fix" } },
    { method: "check", params: { path: "app.py", content: "x = 1" } },
  ]);
  ok("ready", r.ready);
  ok("load 返回 count=1", r.responses[0]?.result?.count === 1, JSON.stringify(r.responses[0]));
  ok("含TODO触发violation", r.responses[1]?.result?.violations?.length === 1, JSON.stringify(r.responses[1]));
  ok("无TODO不触发", r.responses[2]?.result?.passed === true, JSON.stringify(r.responses[2]));

  // ---- plugin-host ----
  console.log("[plugin-host]");
  const pluginsDir = path.join(FIX, "plugins");
  const ph = await talk("plugin-host/main.ts", [
    { method: "list" },
    { method: "invoke", params: { plugin: "hello", method: "greet", params: { name: "qing" } } },
    { method: "invoke", params: { plugin: "hello", method: "add", params: { a: 2, b: 3 } } },
  ], { env: { QXT_PLUGINS_DIR: pluginsDir } });
  ok("ready", ph.ready);
  ok("list 含 hello 插件", ph.responses[0]?.result?.plugins?.some((p) => p.name === "hello"), JSON.stringify(ph.responses[0]));
  ok("invoke greet 正确", ph.responses[1]?.result === "hello, qing!", JSON.stringify(ph.responses[1]));
  ok("invoke add 正确", ph.responses[2]?.result === 5, JSON.stringify(ph.responses[2]));

  // ---- mcp-client ----
  console.log("[mcp-client]");
  const mockPath = path.join(FIX, "mock_mcp.mjs");
  const mc = await talk("mcp-client/main.ts", [
    { method: "connect", params: { command: NODE, args: [mockPath] } },
    { method: "list" },
    { method: "call", params: { name: "echo", arguments: { text: "hi" } } },
    { method: "disconnect" },
  ]);
  ok("ready", mc.ready);
  ok("connect 返回 tools 含 echo", mc.responses[0]?.result?.tools?.some((t) => t.name === "echo"), JSON.stringify(mc.responses[0]));
  ok("list 含 echo", mc.responses[1]?.result?.tools?.some((t) => t.name === "echo"), JSON.stringify(mc.responses[1]));
  ok("call echo 正确", mc.responses[2]?.result?.content?.[0]?.text === "echo: hi", JSON.stringify(mc.responses[2]));
  ok("disconnect ok", mc.responses[3]?.result?.ok === true, JSON.stringify(mc.responses[3]));

  // ---- skill-market ----
  console.log("[skill-market]");
  const reg = path.join(FIX, "skill-market");
  const sm = await talk("skill-market/main.ts", [
    { method: "list", params: { registry: reg } },
    { method: "search", params: { query: "greet", registry: reg } },
    { method: "info", params: { name: "greet", registry: reg } },
  ], { env: { QXT_SKILL_REGISTRY: reg } });
  ok("ready", sm.ready);
  ok("list 含 greet", sm.responses[0]?.result?.packages?.some((p) => p.name === "greet"), JSON.stringify(sm.responses[0]));
  ok("search 命中 greet", sm.responses[1]?.result?.packages?.length === 1, JSON.stringify(sm.responses[1]));
  ok("info 返回版本", sm.responses[2]?.result?.package?.version === "0.2.1", JSON.stringify(sm.responses[2]));

  // ---- dashboard (用临时 home) ----
  console.log("[dashboard]");
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "qxt-dash-"));
  fs.mkdirSync(path.join(tmpHome, "sessions"), { recursive: true });
  fs.writeFileSync(path.join(tmpHome, "sessions", "s1.jsonl"), '{"type":"user","message":{"content":"hi"}}\n{"type":"assistant","message":{"content":"hello"}}\n');
  const dh = await talk("dashboard/main.ts", [
    { method: "start", params: { home: tmpHome, port: 18799 } },
    { method: "snapshot", params: {} },
    { method: "stop", params: {} },
  ]);
  ok("ready", dh.ready);
  ok("start 返回 url", !!dh.responses[0]?.result?.url, JSON.stringify(dh.responses[0]));
  ok("snapshot 含 sessions", Array.isArray(dh.responses[1]?.result?.sessions) && dh.responses[1].result.sessions.length === 1, JSON.stringify(dh.responses[1]));
  ok("stop ok", dh.responses[2]?.result?.ok === true, JSON.stringify(dh.responses[2]));

  // ---- agent-sdk ----
  console.log("[agent-sdk]");
  const as = await talk("agent-sdk/main.ts", [
    { method: "config", params: { base_url: "http://127.0.0.1:1", api_key: "x", model: "m" } },
    { method: "tool_register", params: { name: "read_file" } },
    { method: "reset" },
  ]);
  ok("ready", as.ready);
  ok("config ok", as.responses[0]?.result?.ok === true, JSON.stringify(as.responses[0]));
  ok("tool_register ok", as.responses[1]?.result?.ok === true, JSON.stringify(as.responses[1]));
  ok("reset ok", as.responses[2]?.result?.ok === true, JSON.stringify(as.responses[2]));

  console.log(`\nIPC 冒烟测试: ${pass} 通过, ${fail} 失败`);
  process.exit(fail > 0 ? 1 : 0);
}

run();
