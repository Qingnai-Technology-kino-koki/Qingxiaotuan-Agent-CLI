/**
 * dashboard —— 青小团本地 Web 仪表盘 (TS 侧)
 *
 * 启动一个本地 HTTP + WebSocket 服务, 可视化:
 *   - 会话流 (sessions/*.jsonl)
 *   - 记忆 (memories/MEMORY.md, USER.md)
 *   - 技能 (resources/skills, use_count)
 *   - 多 Agent 协作 (cron / background 状态)
 *
 * 通过 IPC 暴露: start { home, port } / stop / snapshot
 * 同时自身作为 HTTP 服务运行 (端口默认 18765)。
 */
import { IpcServer } from "../protocol";
import * as http from "http";
import * as fs from "fs";
import * as path from "path";
import { WebSocketServer, WebSocket } from "ws";

interface DashCtx {
  home: string;
  port: number;
  server: http.Server | null;
  wss: WebSocketServer | null;
}

const ctx: DashCtx = { home: "", port: 18765, server: null, wss: null };

function readSessions(home: string): any[] {
  const dir = path.join(home, "sessions");
  if (!fs.existsSync(dir)) return [];
  const out: any[] = [];
  for (const f of fs.readdirSync(dir)) {
    if (!f.endsWith(".jsonl")) continue;
    const fp = path.join(dir, f);
    let turns = 0, lastUser = "", lastAssistant = "";
    const text = fs.readFileSync(fp, "utf-8");
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      try {
        const rec = JSON.parse(line);
        if (rec.type === "user") { turns++; lastUser = rec.message?.content || ""; }
        else if (rec.type === "assistant") lastAssistant = rec.message?.content || "";
      } catch { /* ignore */ }
    }
    out.push({ file: f, turns, lastUser: lastUser.slice(0, 80), lastAssistant: lastAssistant.slice(0, 80) });
  }
  return out.sort((a, b) => b.file.localeCompare(a.file));
}

function readMemory(home: string): any {
  const mem = path.join(home, "memories", "MEMORY.md");
  const user = path.join(home, "memories", "USER.md");
  return {
    memory: fs.existsSync(mem) ? fs.readFileSync(mem, "utf-8").split("\n").length : 0,
    user: fs.existsSync(user) ? fs.readFileSync(user, "utf-8").split("\n").length : 0,
    memoryPreview: fs.existsSync(mem) ? fs.readFileSync(mem, "utf-8").slice(0, 2000) : "",
    userPreview: fs.existsSync(user) ? fs.readFileSync(user, "utf-8").slice(0, 2000) : "",
  };
}

function snapshot(): any {
  return {
    home: ctx.home,
    sessions: readSessions(ctx.home),
    memory: readMemory(ctx.home),
    time: Date.now(),
  };
}

const HTML = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>青小团仪表盘</title>
<style>
 body{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:14px 20px;background:#1a1d24;border-bottom:1px solid #2a2f3a;display:flex;justify-content:space-between;align-items:center}
 h1{font-size:18px;margin:0}
 .meta{color:#8a93a6;font-size:13px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:20px}
 .card{background:#1a1d24;border:1px solid #2a2f3a;border-radius:10px;padding:16px}
 .card h2{font-size:15px;margin:0 0 10px;color:#7fd1ff}
 .sess{padding:8px;border-radius:6px;background:#222631;margin-bottom:8px}
 .sess .f{color:#6f7a8d;font-size:12px}
 .sess .u{color:#cfd6e4;font-size:13px;margin-top:3px}
 pre{white-space:pre-wrap;font-size:12px;color:#b9c2d0;max-height:240px;overflow:auto}
 .badge{display:inline-block;background:#2a3340;color:#9fb3c8;padding:1px 8px;border-radius:10px;font-size:11px;margin-left:6px}
</style></head>
<body>
<header><h1>青小团 · 本地仪表盘</h1><span class="meta" id="meta"></span></header>
<div class="grid">
  <div class="card"><h2>会话 (Sessions)</h2><div id="sessions"></div></div>
  <div class="card"><h2>记忆 (Memory)</h2><div id="memory"></div></div>
</div>
<script>
 const ws = new WebSocket("ws://"+location.host);
 function render(s){
   document.getElementById('meta').textContent = 'home: '+s.home+' · '+new Date(s.time).toLocaleTimeString();
   const se = document.getElementById('sessions'); se.innerHTML='';
   for(const x of s.sessions){ const d=document.createElement('div'); d.className='sess';
     d.innerHTML='<div class="f">'+x.file+' · '+x.turns+' turns</div><div class="u">'+escapeHtml(x.lastUser)+'</div>'; se.appendChild(d); }
   const me = document.getElementById('memory');
   me.innerHTML='<div>MEMORY.md: '+s.memory.memory+' 行 · USER.md: '+s.memory.user+' 行</div><pre>'+escapeHtml(s.memory.memoryPreview)+'</pre>';
 }
 function escapeHtml(t){return (t||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
 ws.onmessage = e => render(JSON.parse(e.data));
</script>
</body></html>`;

function startServer(home: string, port: number): void {
  ctx.home = home; ctx.port = port;
  const server = http.createServer((req, res) => {
    if (req.url === "/" || req.url === "/index.html") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(HTML);
    } else if (req.url === "/api/snapshot") {
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(snapshot()));
    } else {
      res.writeHead(404); res.end("not found");
    }
  });
  const wss = new WebSocketServer({ server });
  wss.on("connection", (ws) => {
    ws.send(JSON.stringify(snapshot()));
  });
  server.listen(port, () => {
    process.stderr.write(`dashboard: listening on http://localhost:${port}\n`);
  });
  ctx.server = server; ctx.wss = wss;
  // 定时推送快照
  setInterval(() => {
    if (wss.clients.size > 0) {
      const snap = JSON.stringify(snapshot());
      wss.clients.forEach((c) => { if (c.readyState === WebSocket.OPEN) c.send(snap); });
    }
  }, 3000);
}

async function main() {
  const server = new IpcServer();
  server.register("start", (params) => {
    const home = String(params?.home || ctx.home || ".");
    const port = Number(params?.port || 18765);
    if (ctx.server) throw new Error("already running");
    startServer(home, port);
    return { ok: true, port, url: `http://localhost:${port}` };
  });
  server.register("stop", () => {
    if (ctx.wss) ctx.wss.close();
    if (ctx.server) ctx.server.close();
    ctx.server = null; ctx.wss = null;
    return { ok: true };
  });
  server.register("snapshot", () => snapshot());
  await server.run();
}

main().catch((e) => { process.stderr.write(`dashboard fatal: ${String(e)}\n`); process.exit(1); });
