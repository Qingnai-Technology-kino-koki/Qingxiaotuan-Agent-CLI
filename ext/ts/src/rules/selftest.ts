/**
 * rules 引擎自测: 用 node --experimental-strip-types 直接加载真实模块,
 * 覆盖之前空违规的 bug (assert 被外层引号当字符串)。
 */
import { RuleEngine, parseRules } from "./main";

let pass = 0;
let fail = 0;
function check(name: string, cond: boolean) {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}`); }
}

// 1) parseRules 正确解析单层 + 嵌套 match + 外层引号 stripping
const yaml = `- id: no-todo
  severity: error
  match:
    path: "*.py"
  assert: 'not contains(content, "TODO")'
  message: 发现TODO注释
- id: no-print
  severity: warn
  assert: 'not contains(content, "print(")'
  message: 不要用print
`;
const rules = parseRules(yaml);
check("解析出2条规则", rules.length === 2);
check("规则1 id", rules[0].id === "no-todo");
check("规则1 match.path 去掉外层双引号", rules[0].match?.path === "*.py");
check("规则1 assert 去掉外层单引号", rules[0].assert === 'not contains(content, "TODO")');
check("规则2 assert 去掉外层单引号", rules[1].assert === 'not contains(content, "print(")');

// 2) 关键 bug: 含 TODO 的 .py 文件应触发 violation
const engine = new RuleEngine();
engine.loadYamlText(yaml);
const v1 = engine.check("app.py", "def f():\n    # TODO fix this\n    pass\n", "file");
check("app.py 含 TODO 触发 violation", v1.length === 1 && v1[0].id === "no-todo");
check("passed=false", v1.length === 0 ? false : true);

// 3) 不含 TODO 的 .py 文件不触发
const v2 = engine.check("app.py", "def f():\n    pass\n", "file");
check("app.py 无 TODO 不触发 no-todo", !v2.some((x: any) => x.id === "no-todo"));

// 4) 非 .py 文件不匹配 path
const v3 = engine.check("app.js", "# TODO x", "file");
check("app.js 即使含TODO也不匹配 *.py", !v3.some((x: any) => x.id === "no-todo"));

// 5) warn 规则: print( 触发
const v4 = engine.check("lib.py", "print('hi')\n", "file");
check("print( 触发 no-print warn", v4.some((x: any) => x.id === "no-print"));

// 6) 新断言: entropy / has_secret / forbidden / len
const secYaml = `- id: no-secret
  severity: error
  assert: 'not has_secret(content)'
  message: 疑似泄露密钥
- id: no-eval
  severity: error
  assert: 'not forbidden(content)'
  message: 禁止危险调用
- id: max-len
  severity: warn
  assert: 'max_len(content, 100)'
  message: 文件过长
- id: needs-body
  severity: error
  assert: 'required(content)'
  message: 内容不能为空
`;
const eng2 = new RuleEngine();
eng2.loadYamlText(secYaml);
const s1 = eng2.check("cfg.env", "API_KEY=abcdef0123456789abcdef0123456789\n", "file");
check("has_secret 命中长十六进制密钥", s1.some((x: any) => x.id === "no-secret"));
const s2 = eng2.check("run.js", "const x = eval(userInput);\n", "file");
check("forbidden 命中 eval(", s2.some((x: any) => x.id === "no-eval"));
const s3 = eng2.check("big.txt", "a".repeat(200), "file");
check("max_len 超过100触发 warn", s3.some((x: any) => x.id === "max-len"));
const s4 = eng2.check("empty.txt", "", "file");
check("required 对空内容触发 error", s4.some((x: any) => x.id === "needs-body"));
const s5 = eng2.check("ok.txt", "hello world, no secrets here\n", "file");
check("正常内容无违规", s5.length === 0);

// 7) validate: 非法 severity 应被报告
const badYaml = `- id: x
  severity: critical
  assert: 'contains(content, "y")'
  message: m
`;
const eng3 = new RuleEngine();
eng3.loadYamlText(badYaml);
const vres = eng3.validate();
check("validate 报告非法 severity", vres.valid === false && vres.issues.some((i: string) => i.includes("severity")));

console.log(`\nrules 自测: ${pass} 通过, ${fail} 失败`);
if (fail > 0) process.exit(1);
