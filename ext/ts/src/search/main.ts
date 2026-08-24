/**
 * search —— 青小团本地全文检索引擎 (TS 侧)
 *
 * 类似 ripgrep 的轻量实现: 在工作区递归搜索正则匹配, 返回匹配行与上下文,
 * 自动忽略 node_modules / .git / dist / 二进制文件。结果经 JSONL IPC 暴露。
 *
 * IPC 方法:
 *   search  { pattern, path?, glob?, context?, max?, case_insensitive? }
 *           -> { matches:[{file,line,col,text,match_start,match_end,before[],after[]}], count }
 *   files   { pattern?, path? } -> { files:[...] }   仅列出匹配文件
 *   index_stats {}             -> { indexed_dirs, ignored }
 */
import { IpcServer } from "../protocol.ts";
import * as fs from "fs";
import * as path from "path";

const DEFAULT_IGNORE = new Set([
  "node_modules", ".git", "dist", "build", ".venv", "venv",
  "__pycache__", ".idea", ".vscode", "target", "bin", "obj",
]);

interface MatchResult {
  file: string;
  line: number;
  col: number;
  text: string;
  match_start: number;
  match_end: number;
  before: string[];
  after: string[];
}

class Searcher {
  private root: string;

  constructor(root: string) {
    this.root = root;
  }

  /** 判定是否应忽略该目录/文件。 */
  private isIgnored(name: string, fullPath: string): boolean {
    if (DEFAULT_IGNORE.has(name)) return true;
    // 隐藏文件/目录 (类 Unix) 跳过, 但保留根下显式 path
    if (name.startsWith(".") && name !== ".github") {
      // 允许部分点文件, 但跳过 .cache 等
      if (name.length > 2 && /^\.(cache|tmp|DS_Store|min\..*)$/.test(name)) return true;
    }
    try {
      const st = fs.statSync(fullPath);
      // 跳过超大文件 (>8MB)
      if (st.isFile() && st.size > 8 * 1024 * 1024) return true;
    } catch {
      return true;
    }
    return false;
  }

  private globToRegex(glob: string): RegExp {
    const esc = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
    return new RegExp("^" + esc + "$");
  }

  private walk(dir: string, out: string[]): void {
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); }
    catch { return; }
    for (const e of entries) {
      if (this.isIgnored(e.name, path.join(dir, e.name))) continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) this.walk(full, out);
      else out.push(full);
    }
  }

  search(opts: {
    pattern: string;
    path?: string;
    glob?: string;
    context?: number;
    max?: number;
    caseInsensitive?: boolean;
  }): { matches: MatchResult[]; count: number; scanned: number } {
    const re = new RegExp(opts.pattern, opts.caseInsensitive ? "gi" : "g");
    const globRe = opts.glob ? this.globToRegex(opts.glob) : null;
    const root = path.resolve(this.root, opts.path || ".");
    const files: string[] = [];
    this.walk(root, files);
    const ctx = opts.context ?? 2;
    const max = opts.max ?? 200;
    const matches: MatchResult[] = [];
    let scanned = 0;
    for (const f of files) {
      if (matches.length >= max) break;
      if (globRe && !globRe.test(path.basename(f))) continue;
      let content: string;
      try {
        // 仅读取文本: 探测非打印字符过多则跳过
        const buf = fs.readFileSync(f);
        if (buf.indexOf(0) >= 0) continue; // 含 NUL => 二进制
        content = buf.toString("utf-8");
      } catch { continue; }
      scanned++;
      const lines = content.split("\n");
      for (let i = 0; i < lines.length; i++) {
        const lineText = lines[i];
        re.lastIndex = 0;
        let m: RegExpExecArray | null;
        let firstHit: { s: number; e: number } | null = null;
        while ((m = re.exec(lineText)) !== null) {
          if (firstHit === null) firstHit = { s: m.index, e: m.index + m[0].length };
          if (matches.length >= max) break;
          const before = lines.slice(Math.max(0, i - ctx), i).map((x) => x.slice(0, 400));
          const after = lines.slice(i + 1, i + 1 + ctx).map((x) => x.slice(0, 400));
          matches.push({
            file: path.relative(this.root, f).split(path.sep).join("/"),
            line: i + 1,
            col: (firstHit?.s ?? 0) + 1,
            text: lineText.slice(0, 400),
            match_start: firstHit?.s ?? 0,
            match_end: firstHit?.e ?? 0,
            before,
            after,
          });
          if (m.index === re.lastIndex) re.lastIndex++;
        }
        if (matches.length >= max) break;
      }
    }
    return { matches, count: matches.length, scanned };
  }

  listFiles(opts: { pattern?: string; path?: string; glob?: string }): string[] {
    const root = path.resolve(this.root, opts.path || ".");
    const files: string[] = [];
    this.walk(root, files);
    const globRe = opts.glob ? this.globToRegex(opts.glob) : null;
    const re = opts.pattern ? new RegExp(opts.pattern) : null;
    return files
      .filter((f) => !globRe || globRe.test(path.basename(f)))
      .filter((f) => !re || re.test(path.relative(this.root, f)))
      .map((f) => path.relative(this.root, f).split(path.sep).join("/"));
  }
}

async function main() {
  const root = process.env.QXT_SEARCH_ROOT || process.cwd();
  const searcher = new Searcher(root);
  const server = new IpcServer();
  server.register("search", (params) => {
    const pattern = String(params?.pattern || "");
    if (!pattern) throw new Error("missing pattern");
    const r = searcher.search({
      pattern,
      path: params?.path ? String(params.path) : undefined,
      glob: params?.glob ? String(params.glob) : undefined,
      context: params?.context as number | undefined,
      max: params?.max as number | undefined,
      caseInsensitive: !!params?.case_insensitive,
    });
    return r;
  });
  server.register("files", (params) => {
    return { files: searcher.listFiles({
      pattern: params?.pattern ? String(params.pattern) : undefined,
      path: params?.path ? String(params.path) : undefined,
      glob: params?.glob ? String(params.glob) : undefined,
    }) };
  });
  server.register("index_stats", () => {
    const files: string[] = [];
    searcher.search; // no-op ref
    return { root, note: "轻量按需检索, 无持久索引" };
  });
  await server.run();
}

main().catch((e) => { process.stderr.write(`search fatal: ${String(e)}\n`); process.exit(1); });
