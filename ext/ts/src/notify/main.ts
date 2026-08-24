/**
 * notify —— 青小团桌面通知引擎 (TS 侧)
 *
 * 跨平台发送桌面通知, 封装各系统原生能力:
 *   - Windows: PowerShell 的 BurntToast / 回退到 msg * (无需额外依赖)
 *   - macOS:   osascript display notification
 *   - Linux:   notify-send (libnotify)
 *
 * 也支持 "toast" 风格的轻量本地回退 (写入工作区 .notifications/ 供前端轮询)。
 *
 * IPC 方法:
 *   send   { title, message, level?, timeout_ms? } -> { ok, method }
 *   history{}                                      -> { items:[...] }
 *   clear {}                                       -> { ok }
 */
import { IpcServer } from "../protocol.ts";
import { spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";

type Level = "info" | "success" | "warning" | "error";

interface Notification {
  ts: number;
  title: string;
  message: string;
  level: Level;
}

class Notifier {
  private history: Notification[] = [];
  private historyFile: string;
  private fallbackDir: string;

  constructor() {
    const base = process.env.QXT_NOTIFY_DIR || path.join(os.tmpdir(), "qxt-notify");
    this.fallbackDir = base;
    this.historyFile = path.join(base, "history.json");
    try {
      fs.mkdirSync(base, { recursive: true });
      if (fs.existsSync(this.historyFile)) {
        this.history = JSON.parse(fs.readFileSync(this.historyFile, "utf-8"));
      }
    } catch { /* ignore */ }
  }

  private persist() {
    try { fs.writeFileSync(this.historyFile, JSON.stringify(this.history.slice(-100)), "utf-8"); }
    catch { /* ignore */ }
  }

  /** 检测当前平台可用的原生通知方式。 */
  detectMethod(): string {
    const platform = os.platform();
    if (platform === "win32") {
      // 优先尝试 BurntToast (PowerShell 模块), 否则用 msg
      try {
        const r = spawn("powershell", ["-NoProfile", "-Command", "Get-Command", "New-BurntToastNotification", "-ErrorAction", "SilentlyContinue"], { windowsHide: true });
        // 简单判定: 不抛错即可, 实际发送时再决定
        r.on("error", () => {});
        return "windows";
      } catch { return "windows"; }
    }
    if (platform === "darwin") return "macos";
    return "linux";
  }

  send(title: string, message: string, level: Level, timeout_ms = 5000): { ok: boolean; method: string } {
    const platform = os.platform();
    let method = "fallback";
    try {
      if (platform === "win32") {
        method = this.sendWindows(title, message, level);
      } else if (platform === "darwin") {
        method = this.sendMacos(title, message, level);
      } else {
        method = this.sendLinux(title, message, level);
      }
    } catch {
      method = "fallback";
    }
    if (method === "fallback") this.writeFallback(title, message, level);
    this.history.push({ ts: Date.now(), title, message, level });
    if (this.history.length > 100) this.history = this.history.slice(-100);
    this.persist();
    return { ok: true, method };
  }

  private sendWindows(title: string, message: string, _level: Level): string {
    // 使用 PowerShell 直接调用 Shell.Application 的 Toast (无需 BurntToast 模块)
    const safe = (s: string) => s.replace(/"/g, "'").replace(/\n/g, " ");
    const ps = `
$ErrorActionPreference='SilentlyContinue'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts=$template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode('${safe(title)}')) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode('${safe(message)}')) | Out-Null
$notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Qingxiaotuan')
$notifier.Show($template)
`;
    const p = spawn("powershell", ["-NoProfile", "-Command", ps], { windowsHide: true, stdio: "ignore" });
    p.on("error", () => {});
    return "windows-toast";
  }

  private sendMacos(title: string, message: string, _level: Level): string {
    const safe = (s: string) => s.replace(/"/g, '\\"').replace(/\\/g, "\\\\");
    const p = spawn("osascript", ["-e", `display notification "${safe(message)}" with title "${safe(title)}"`], { stdio: "ignore" });
    p.on("error", () => {});
    return "macos-osa";
  }

  private sendLinux(title: string, message: string, level: Level): string {
    const icon = level === "error" ? "dialog-error" : level === "warning" ? "dialog-warning" : "dialog-information";
    const p = spawn("notify-send", ["-i", icon, title, message], { stdio: "ignore" });
    p.on("error", () => {});
    return "linux-notify-send";
  }

  private writeFallback(title: string, message: string, _level: Level) {
    try {
      const p = path.join(this.fallbackDir, `n_${Date.now()}.json`);
      fs.writeFileSync(p, JSON.stringify({ title, message, ts: Date.now() }), "utf-8");
    } catch { /* ignore */ }
  }

  getHistory(): Notification[] { return this.history; }
  clearHistory(): void { this.history = []; this.persist(); }
}

async function main() {
  const notifier = new Notifier();
  const server = new IpcServer();
  server.register("send", (params) => {
    const title = String(params?.title || "青小团");
    const message = String(params?.message || "");
    const level = (params?.level as Level) || "info";
    const timeout = (params?.timeout_ms as number) || 5000;
    if (!message) throw new Error("missing message");
    return notifier.send(title, message, level, timeout);
  });
  server.register("history", () => ({ items: notifier.getHistory() }));
  server.register("clear", () => { notifier.clearHistory(); return { ok: true }; });
  server.register("method", () => ({ method: notifier.detectMethod() }));
  await server.run();
}

main().catch((e) => { process.stderr.write(`notify fatal: ${String(e)}\n`); process.exit(1); });
