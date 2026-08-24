/**
 * plugin-host —— 青小团外部插件宿主 (TS 侧)
 *
 * 加载并运行用户/社区编写的 TS/JS 插件, 通过 JSONL IPC 与 Python 内核通信。
 * 每个插件是一个目录, 含 manifest.json + index.js:
 *   manifest.json: { name, version, description, methods: ["foo","bar"] }
 *   index.js:      module.exports = { async foo(params, ctx){...}, async bar(params, ctx){...} }
 *
 * ctx 提供: emit(chunk), log(msg)
 *
 * IPC 方法:
 *   list    {}                  -> { plugins:[{name,version,methods,description}] }
 *   invoke  { plugin, method, params } -> { result }
 *   reload  {}                  -> { count }
 */
import * as fs from "fs";
import * as path from "path";
import { createRequire } from "module";
import { IpcServer } from "../protocol";

// 在 ESM 与 CommonJS 两种模式下均可加载 CommonJS 插件 (index.js)
const requirePlugin = createRequire(__filename);

interface PluginManifest {
  name: string;
  version: string;
  description: string;
  methods: string[];
  capabilities?: string[];
}

interface LoadedPlugin {
  manifest: PluginManifest;
  mod: any;
}

const SAFE_NAME = /^[a-zA-Z0-9_.-]{1,80}$/;

function validateManifest(value: unknown): PluginManifest {
  if (!value || typeof value !== "object") throw new Error("manifest must be an object");
  const raw = value as Partial<PluginManifest>;
  if (typeof raw.name !== "string" || !SAFE_NAME.test(raw.name)) throw new Error("invalid plugin name");
  if (typeof raw.version !== "string" || raw.version.length > 40) throw new Error("invalid plugin version");
  if (typeof raw.description !== "string" || raw.description.length > 500) throw new Error("invalid plugin description");
  if (!Array.isArray(raw.methods) || raw.methods.length > 100 ||
      !raw.methods.every((method) => typeof method === "string" && SAFE_NAME.test(method))) {
    throw new Error("invalid plugin methods");
  }
  if (raw.capabilities !== undefined &&
      (!Array.isArray(raw.capabilities) || !raw.capabilities.every((cap) => cap === "workspace.read"))) {
    throw new Error("unsupported plugin capabilities");
  }
  return {
    name: raw.name, version: raw.version, description: raw.description,
    methods: [...raw.methods], capabilities: [...(raw.capabilities || [])],
  };
}

const PLUGINS_DIR = process.env.QXT_PLUGINS_DIR || path.join(process.cwd(), "plugins");

class PluginHost {
  private plugins = new Map<string, LoadedPlugin>();

  loadAll(): number {
    this.plugins.clear();
    if (!fs.existsSync(PLUGINS_DIR)) return 0;
    for (const entry of fs.readdirSync(PLUGINS_DIR)) {
      const dir = path.join(PLUGINS_DIR, entry);
      const manifestPath = path.join(dir, "manifest.json");
      const indexJs = path.join(dir, "index.js");
      try {
        if (!fs.statSync(dir).isDirectory()) continue;
      } catch {
        continue;
      }
      if (!fs.existsSync(manifestPath) || !fs.existsSync(indexJs)) continue;
      try {
        const manifest = validateManifest(JSON.parse(fs.readFileSync(manifestPath, "utf-8")));
        const mod = requirePlugin(indexJs);
        this.plugins.set(manifest.name, { manifest, mod });
      } catch (e) {
        process.stderr.write(`plugin-host: load ${entry} failed: ${String(e)}\n`);
      }
    }
    return this.plugins.size;
  }

  list() {
    return Array.from(this.plugins.values()).map((p) => ({
      name: p.manifest.name,
      version: p.manifest.version,
      description: p.manifest.description,
      methods: p.manifest.methods,
    }));
  }

  async invoke(name: string, method: string, params: any, emit: (c: any) => void): Promise<any> {
    const p = this.plugins.get(name);
    if (!p) throw new Error(`plugin not found: ${name}`);
    if (!p.manifest.methods.includes(method)) throw new Error(`plugin ${name} has no method ${method}`);
    const fn = p.mod[method];
    if (typeof fn !== "function") throw new Error(`method ${method} is not a function`);
    const workspace = path.resolve(process.env.QXT_PLUGIN_WORKSPACE || process.cwd());
    const ctx = {
      emit,
      log: (msg: string) => process.stderr.write(`[${name}.${method}] ${msg}\n`),
      workspace,
      readFile: (relativePath: string) => {
        if (!p.manifest.capabilities?.includes("workspace.read")) {
          throw new Error("plugin capability denied: workspace.read");
        }
        const target = path.resolve(workspace, relativePath);
        if (!target.startsWith(`${workspace}${path.sep}`) && target !== workspace) {
          throw new Error("plugin path escapes workspace");
        }
        return fs.readFileSync(target, "utf-8").slice(0, 100_000);
      },
    };
    return await fn(params || {}, ctx);
  }
}

async function main() {
  const host = new PluginHost();
  const count = host.loadAll();
  process.stderr.write(`plugin-host: loaded ${count} plugins from ${PLUGINS_DIR}\n`);

  const server = new IpcServer();
  server.register("list", () => ({ plugins: host.list() }));
  server.register("reload", () => {
    const n = host.loadAll();
    return { count: n };
  });
  server.register("invoke", async (params, emit) => {
    const name = String(params?.plugin || "");
    const method = String(params?.method || "");
    return await host.invoke(name, method, params?.params, emit.emit);
  });
  await server.run();
}

main().catch((e) => {
  process.stderr.write(`plugin-host fatal: ${String(e)}\n`);
  process.exit(1);
});
