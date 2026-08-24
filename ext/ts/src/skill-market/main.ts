/**
 * skill-market —— 青小团技能市场客户端 (TS 侧)
 *
 * 从技能 registry (本地目录 / 远程 Git / tarball) 拉取与发布技能包。
 * 一个技能包 = 目录, 含 SKILL.md (含 frontmatter: name, description, version) + 附件。
 *
 * 默认 registry: 本地 ./skills-market 目录 (可扩展为远程 HTTP)。
 *
 * IPC 方法:
 *   list     { registry? }    -> { packages:[{name,version,description}] }
 *   info     { name }         -> { package }
 *   install  { name, target } -> { ok, path }
 *   publish  { source, registry? } -> { ok }
 *   search   { query }        -> { packages }
 */
import { IpcServer } from "../protocol";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";

const DEFAULT_REGISTRY = process.env.QXT_SKILL_REGISTRY || path.join(os.homedir(), ".qingxiaotuan", "skill-market");

interface SkillMeta {
  name: string;
  description: string;
  version: string;
}

function parseFrontmatter(text: string): { meta: Record<string, string>; body: string } {
  const m = text.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return { meta: {}, body: text };
  const meta: Record<string, string> = {};
  for (const line of m[1].split("\n")) {
    const kv = line.match(/^(\w+):\s*(.*)$/);
    if (kv) meta[kv[1]] = kv[2];
  }
  return { meta, body: m[2] };
}

function readSkillMeta(dir: string): SkillMeta | null {
  const skillMd = path.join(dir, "SKILL.md");
  if (!fs.existsSync(skillMd)) return null;
  const { meta } = parseFrontmatter(fs.readFileSync(skillMd, "utf-8"));
  return {
    name: meta.name || path.basename(dir),
    description: meta.description || "",
    version: meta.version || "0.1.0",
  };
}

function listPackages(registry: string): SkillMeta[] {
  if (!fs.existsSync(registry)) return [];
  const out: SkillMeta[] = [];
  for (const entry of fs.readdirSync(registry)) {
    const dir = path.join(registry, entry);
    if (!fs.statSync(dir).isDirectory()) continue;
    const meta = readSkillMeta(dir);
    if (meta) out.push(meta);
  }
  return out;
}

function copyDir(src: string, dst: string) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dst, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

async function main() {
  const server = new IpcServer();
  server.register("list", (params) => {
    const reg = String(params?.registry || DEFAULT_REGISTRY);
    return { registry: reg, packages: listPackages(reg) };
  });
  server.register("search", (params) => {
    const q = String(params?.query || "").toLowerCase();
    const reg = String(params?.registry || DEFAULT_REGISTRY);
    const pkgs = listPackages(reg).filter(
      (p) => p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q)
    );
    return { packages: pkgs };
  });
  server.register("info", (params) => {
    const name = String(params?.name || "");
    const reg = String(params?.registry || DEFAULT_REGISTRY);
    const dir = path.join(reg, name);
    const meta = readSkillMeta(dir);
    if (!meta) throw new Error(`skill not found: ${name}`);
    return { package: meta, path: dir };
  });
  server.register("install", (params) => {
    const name = String(params?.name || "");
    const target = String(params?.target || path.join(DEFAULT_REGISTRY, "..", "skills"));
    const reg = String(params?.registry || DEFAULT_REGISTRY);
    const src = path.join(reg, name);
    if (!fs.existsSync(src)) throw new Error(`skill not found: ${name}`);
    const dst = path.join(target, name);
    copyDir(src, dst);
    return { ok: true, path: dst };
  });
  server.register("publish", (params) => {
    const source = String(params?.source || "");
    const reg = String(params?.registry || DEFAULT_REGISTRY);
    if (!fs.existsSync(source)) throw new Error(`source not found: ${source}`);
    const meta = readSkillMeta(source);
    if (!meta) throw new Error("source has no SKILL.md");
    fs.mkdirSync(reg, { recursive: true });
    copyDir(source, path.join(reg, meta.name));
    return { ok: true, name: meta.name };
  });
  await server.run();
}

main().catch((e) => { process.stderr.write(`skill-market fatal: ${String(e)}\n`); process.exit(1); });
