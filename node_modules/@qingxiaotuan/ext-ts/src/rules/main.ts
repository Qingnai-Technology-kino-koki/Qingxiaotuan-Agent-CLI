/**
 * rules —— 青小团规则引擎 (TS 侧)
 *
 * 加载 YAML 规则文件, 对"代码补丁/对话/文件"做策略校验。每条规则有:
 *   - id / severity (error|warn|info)
 *   - match: 针对什么 (path 通配 / content 正则 / kind 过滤)
 *   - assert: 一个安全表达式 (禁止某些模式 / 必须满足某些条件)
 *
 * 表达式语言: 极简安全子集 (不允许函数调用/赋值), 支持:
 *   文本:    contains(s, "x"), matches(s, /re/), startsWith, endsWith,
 *            len(s), regex_contains(s, /re/), count_occurrences(s, "x")
 *   安全:    entropy(s)  -> 字节香农熵 (0..8), max_len(s), min_len(s)
 *            has_secret(s) -> 粗略命中密钥/令牌模式
 *            forbidden(s)  -> 命中内置危险模式 (eval, exec, rm -rf ...)
 *            required(s)   -> 必须存在某关键结构 (默认非空)
 *   逻辑:    and / or / not, 比较 (== != > < >= <=), 字面量, 变量 (path/content/kind/text)
 *
 * IPC 方法:
 *   load    { rules_yaml | rules_path } -> { count, valid, issues }
 *   check   { path, content, kind }     -> { violations:[...], passed, hit_lines }
 *   lint    { files:[{path,content}] }  -> { report, errors, warns, infos }
 *   validate{ rules_yaml }              -> { valid, issues:[...] }
 */
import { IpcServer } from "../protocol";
import * as fs from "fs";

interface Rule {
  id: string;
  severity: "error" | "warn" | "info";
  match?: { path?: string; content?: string; kind?: string };
  assert: string; // 表达式
  message: string;
}

/* ---------------- 极简表达式求值 (安全沙箱: 无 eval) ---------------- */
function tokenize(expr: string): string[] {
  const toks: string[] = [];
  const re = /\s*(\/\/.*?\/\/|\/.*?\/[gi]*|"[^"]*"|'[^']*'|[A-Za-z_][A-Za-z0-9_.]*|\(|\)|,|&&|\|\||!|==|!=|>=|<=|>|<|=|\+|-|\*|\/|\d+\.?\d*)\s*/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(expr)) !== null) {
    if (m[1] !== undefined) toks.push(m[1]);
  }
  return toks;
}

// 递归下降: or -> and -> not -> primary
class ExprParser {
  private toks: string[];
  private pos = 0;
  constructor(toks: string[]) { this.toks = toks; }
  parse(): any { return this.parseOr(); }
  private peek() { return this.toks[this.pos]; }
  private next() { return this.toks[this.pos++]; }
  private parseOr(): any {
    let left = this.parseAnd();
    while (this.peek() === "||") { this.next(); const r = this.parseAnd(); left = { op: "or", left, right: r }; }
    return left;
  }
  private parseAnd(): any {
    let left = this.parseNot();
    while (this.peek() === "&&") { this.next(); const r = this.parseNot(); left = { op: "and", left, right: r }; }
    return left;
  }
  private parseNot(): any {
    if (this.peek() === "!") { this.next(); return { op: "not", operand: this.parseNot() }; }
    if (this.peek() === "not") { this.next(); return { op: "not", operand: this.parseNot() }; }
    return this.parseCompare();
  }
  private parseCompare(): any {
    const left = this.parsePrimary();
    const op = this.peek();
    if ([">", "<", ">=", "<=", "==", "!=", "="].includes(op)) {
      this.next();
      const right = this.parsePrimary();
      return { op: op === "=" ? "==" : op, left, right };
    }
    return left;
  }
  private parsePrimary(): any {
    const t = this.next();
    if (t === "(") { const e = this.parseOr(); this.next(); return e; }
    if (t === "true") return { lit: true };
    if (t === "false") return { lit: false };
    if (/^\d/.test(t)) return { num: parseFloat(t) };
    if (t.startsWith('"') || t.startsWith("'")) return { str: t.slice(1, -1) };
    if (t.startsWith("//") || (t.startsWith("/") && t.length > 1)) {
      const m = t.match(/^\/(.*)\/([gi]*)$/);
      return { regex: m ? new RegExp(m[1], m[2]) : null };
    }
    // 标识符或函数调用: func(arg)
    if (this.peek() === "(") {
      this.next();
      const args: any[] = [];
      while (this.peek() && this.peek() !== ")") {
        args.push(this.parseOr());
        if (this.peek() === ",") this.next();
      }
      this.next(); // )
      return { call: t, args };
    }
    return { var: t };
  }
}

/* 香农熵 (字节级), 用于检测高随机性字符串 (密钥/令牌)
 * 自定义函数可在此扩展, 全部为纯函数, 不触碰 IO。 */
function shannonEntropy(s: string): number {
  if (!s) return 0;
  const freq = new Map<string, number>();
  for (const ch of s) freq.set(ch, (freq.get(ch) || 0) + 1);
  let h = 0;
  const n = s.length;
  for (const c of freq.values()) {
    const p = c / n;
    h -= p * Math.log2(p);
  }
  return h;
}

const FORBIDDEN_PATTERNS: RegExp[] = [
  /\beval\s*\(/, /\bexec\s*\(/, /\bchild_process\b/, /rm\s+-rf\s+\//,
  /require\s*\(\s*["']child_process["']/, /\bprocess\.env\b/,
];

function hasSecret(token: string): boolean {
  // 粗略: 长十六进制/ base64 且高熵, 或常见前缀密钥
  const secretLike = /(api[_-]?key|secret|token|passwd|password|access[_-]?key)\s*[:=]/i.test(token);
  const highEntropy = shannonEntropy(token) > 4.0 && token.length >= 16;
  const longHex = /^[0-9a-fA-F]{32,}$/.test(token);
  const longB64 = /^[A-Za-z0-9+/]{32,}={0,2}$/.test(token);
  return secretLike || (highEntropy && (longHex || longB64));
}

function evalExpr(node: any, scope: Record<string, any>): any {
  if (node === undefined || node === null) return false;
  if (node.lit !== undefined) return node.lit;
  if (node.num !== undefined) return node.num;
  if (node.str !== undefined) return node.str;
  if (node.regex) return node.regex;
  if (node.op === "and") return evalExpr(node.left, scope) && evalExpr(node.right, scope);
  if (node.op === "or") return evalExpr(node.left, scope) || evalExpr(node.right, scope);
  if (node.op === "not") return !evalExpr(node.operand, scope);
  if ([">", "<", ">=", "<=", "==", "!="].includes(node.op)) {
    const l = evalExpr(node.left, scope); const r = evalExpr(node.right, scope);
    switch (node.op) {
      case ">": return l > r; case "<": return l < r;
      case ">=": return l >= r; case "<=": return l <= r;
      case "==": return l == r; case "!=": return l != r;
    }
  }
  if (node.call) {
    const a = node.args.map((x: any) => evalExpr(x, scope));
    switch (node.call) {
      case "contains": return String(a[0]).includes(String(a[1]));
      case "matches": return a[0] instanceof RegExp ? a[0].test(String(a[1])) : false;
      case "regex_contains": return a[0] instanceof RegExp ? a[0].test(String(a[1])) : String(a[1]).includes(String(a[0]));
      case "startsWith": return String(a[0]).startsWith(String(a[1]));
      case "endsWith": return String(a[0]).endsWith(String(a[1]));
      case "len": return String(a[0]).length;
      case "min_len": return String(a[0]).length >= Number(a[1] || 0);
      case "max_len": return String(a[0]).length <= Number(a[1] || Infinity);
      case "count_occurrences": return String(a[0]).split(String(a[1])).length - 1;
      case "entropy": return shannonEntropy(String(a[0]));
      case "has_secret": return hasSecret(String(a[0]));
      case "forbidden": return FORBIDDEN_PATTERNS.some((re) => re.test(String(a[0])));
      case "required": return String(a[0]).trim().length > 0;
      default: throw new Error(`unknown fn ${node.call}`);
    }
  }
  if (node.var) {
    const parts = node.var.split(".");
    let v: any = scope;
    for (const p of parts) { if (v == null) return undefined; v = v[p]; }
    return v;
  }
  return false;
}

export class RuleEngine {
  private rules: Rule[] = [];

  loadYamlText(text: string): number {
    this.rules = parseRules(text);
    return this.rules.length;
  }
  loadPath(p: string): number {
    return this.loadYamlText(fs.readFileSync(p, "utf-8"));
  }

  /** 校验规则定义本身 (返回结构化问题), 不抛出。 */
  validate(): { valid: boolean; issues: string[] } {
    const issues: string[] = [];
    const ids = new Set<string>();
    for (const r of this.rules) {
      if (!r.id) issues.push("规则缺少 id");
      else if (ids.has(r.id)) issues.push(`重复的规则 id: ${r.id}`);
      else ids.add(r.id);
      if (!["error", "warn", "info"].includes(r.severity)) {
        issues.push(`规则 ${r.id} 的 severity 非法: ${r.severity} (应为 error|warn|info)`);
      }
      if (!r.assert) issues.push(`规则 ${r.id} 缺少 assert`);
      else {
        try {
          const ast = new ExprParser(tokenize(r.assert)).parse();
          evalExpr(ast, { path: "", content: "", kind: "", text: "" });
        } catch (e: any) {
          issues.push(`规则 ${r.id} 的 assert 解析失败: ${e.message}`);
        }
      }
      if (!r.message) issues.push(`规则 ${r.id} 缺少 message`);
    }
    return { valid: issues.length === 0, issues };
  }

  /** 返回命中的行号 (1-based) 用于上下文展示。 */
  private hitLines(content: string, needle: string): number[] {
    const out: number[] = [];
    const lines = content.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].includes(needle)) out.push(i + 1);
    }
    return out;
  }

  check(path: string, content: string, kind: string): any[] {
    const violations: any[] = [];
    for (const r of this.rules) {
      // match 过滤
      if (r.match?.kind && r.match.kind !== kind) continue;
      if (r.match?.path) {
        let pg = r.match.path.trim();
        if ((pg.startsWith('"') && pg.endsWith('"')) || (pg.startsWith("'") && pg.endsWith("'"))) {
          pg = pg.slice(1, -1);
        }
        const re = globToRegex(pg);
        if (!re.test(path)) continue;
      }
      if (r.match?.content) {
        const re = new RegExp(r.match.content);
        if (!re.test(content)) continue;
      }
      const scope = { path, content, kind, text: content };
      try {
        const ok = evalExpr(new ExprParser(tokenize(r.assert)).parse(), scope);
        if (!ok) {
          // 尝试给出更友好的上下文: 若 assert 含 contains("X"), 标出 X 出现的行
          let lines: number[] = [];
          const m = r.assert.match(/contains\s*\(\s*\w+\s*,\s*"([^"]+)"/);
          if (m) lines = this.hitLines(content, m[1]);
          violations.push({
            id: r.id, severity: r.severity, message: r.message, path,
            rule: r.assert, hit_lines: lines,
          });
        }
      } catch (e: any) {
        violations.push({ id: r.id, severity: "error", message: `rule eval error: ${e.message}`, path, rule: r.assert });
      }
    }
    return violations;
  }
}

function globToRegex(glob: string): RegExp {
  const esc = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp("^" + esc + "$");
}

/* 极简 rules YAML 解析 (够用子集) */
export function parseRules(text: string): Rule[] {
  const lines = text.split("\n");
  const rules: Rule[] = [];
  let cur: any = null;
  let depthKey = "";
  for (const raw of lines) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    const indent = raw.length - raw.trimStart().length;
    const line = raw.trim();
    if (line.startsWith("- ")) {
      if (cur) rules.push(cur as Rule);
      cur = {}; depthKey = "";
      const rest = line.slice(2);
      const kv = rest.match(/^(\w+):\s*(.*)$/);
      if (kv) { cur[kv[1]] = kv[2]; depthKey = kv[1]; }
    } else {
      const kv = line.match(/^(\w+):\s*(.*)$/);
      if (kv && cur) {
        const key = kv[1];
        let val = kv[2];
        if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
          val = val.slice(1, -1);
        }
        const inNested = depthKey && cur[depthKey] && typeof cur[depthKey] === "object" && indent > 2;
        if (inNested) {
          if (val === "") cur[depthKey][key] = {};
          else cur[depthKey][key] = val;
        } else if (val === "" && indent >= 2) {
          cur[key] = {};
          depthKey = key;
        } else {
          cur[key] = val;
          if (indent < 2) depthKey = key;
        }
      }
    }
  }
  if (cur) rules.push(cur as Rule);
  // 把 match 下的 path/content/kind 归一为对象
  return rules.filter((r) => r.id && r.assert).map((r) => {
    const nr = { ...r };
    if (typeof (r as any).match === "string") {
      nr.match = { content: (r as any).match };
    } else if ((r as any).match && typeof (r as any).match === "object") {
      nr.match = (r as any).match;
    }
    return nr as Rule;
  });
}

async function main() {
  const engine = new RuleEngine();
  const server = new IpcServer();
  server.register("load", (params) => {
    let count = 0;
    if (params?.rules_yaml) count = engine.loadYamlText(String(params.rules_yaml));
    else if (params?.rules_path) count = engine.loadPath(String(params.rules_path));
    else throw new Error("missing rules_yaml or rules_path");
    const v = engine.validate();
    return { count, valid: v.valid, issues: v.issues };
  });
  server.register("check", (params) => {
    const v = engine.check(String(params?.path || ""), String(params?.content || ""), String(params?.kind || "file"));
    return { violations: v, passed: v.length === 0 };
  });
  server.register("lint", (params) => {
    const files = (params?.files as any[]) || [];
    const report: any[] = [];
    let errors = 0, warns = 0, infos = 0;
    for (const f of files) {
      const v = engine.check(String(f.path), String(f.content), "file");
      report.push({ path: f.path, violations: v });
      for (const x of v) {
        if (x.severity === "error") errors++;
        else if (x.severity === "warn") warns++;
        else infos++;
      }
    }
    return { report, errors, warns, infos };
  });
  server.register("validate", (params) => {
    if (params?.rules_yaml) engine.loadYamlText(String(params.rules_yaml));
    return engine.validate();
  });
  await server.run();
}

main().catch((e) => { process.stderr.write(`rules fatal: ${String(e)}\n`); process.exit(1); });
