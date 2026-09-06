"""纯 Python 实现 rules 引擎

加载 YAML 规则, 对"代码补丁/对话/文件"做策略校验。每条规则有:
  - id / severity (error|warn|info)
  - match: path 通配 / content 正则 / kind 过滤
  - assert: 安全表达式 (禁止某些模式 / 必须满足某些条件)

表达式语言为极简安全子集 (无 eval):
  文本:  contains(s, "x"), matches(s, /re/), startsWith, endsWith,
         len(s), regex_contains, count_occurrences
  安全:  entropy(s), max_len(s), min_len(s), has_secret(s),
         forbidden(s), required(s)
  逻辑:  and / or / not, 比较 (== != > < >= <=), 字面量, 变量 (path/content/kind/text)

IPC 方法:
  load     { rules_yaml | rules_path } -> { count, valid, issues }
  check    { path, content, kind }     -> { violations, passed }
  lint     { files:[{path,content}] }  -> { report, errors, warns, infos }
  validate { rules_yaml }              -> { valid, issues }
"""
import json
import math
import re
import sys
from collections import Counter
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _glob_to_regex(glob: str) -> "re.Pattern":
    esc = re.escape(glob).replace(r"\*", ".*").replace(r"\?", ".")
    return re.compile("^" + esc + "$")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    freq = Counter(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


# ---------------------------------------------------------------- ReDoS 防护
# 规则来自「管理设置 / 组织策略」时, 恶意规则可嵌入灾难性回溯正则拖垮 Agent。
# 这里在编译前做长度与嵌套量词检查, 拒绝明显危险的 pattern。

_MAX_RULE_PATTERN_LEN = 1024

# 嵌套量词: 形如 (...含量词...) 紧跟 + / * / ? —— 典型 ReDoS 结构 (如 (a+)+)
_REDOS_NESTED = re.compile(r"\((?:[^()]*[+*?])\)[+*?]")


def _safe_compile(pattern: str, flags: int = 0) -> "re.Pattern":
    """编译规则正则, 拒绝过长或含嵌套量词的 pattern (防 ReDoS)。"""
    if len(pattern) > _MAX_RULE_PATTERN_LEN:
        raise ValueError(f"规则正则过长 (>{_MAX_RULE_PATTERN_LEN}), 拒绝编译 (防 ReDoS)")
    if _REDOS_NESTED.search(pattern):
        raise ValueError("规则正则含嵌套量词, 拒绝编译 (防 ReDoS)")
    return re.compile(pattern, flags)


_FORBIDDEN_PATTERNS = [
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\bchild_process\b"),
    re.compile(r"rm\s+-rf\s+/"),
    re.compile(r'require\s*\(\s*["\']child_process["\']'),
    re.compile(r"\bprocess\.env\b"),
]


def _has_secret(token: str) -> bool:
    secret_like = re.search(
        r"(api[_-]?key|secret|token|passwd|password|access[_-]?key)\s*[:=]",
        token, re.IGNORECASE)
    high_entropy = _shannon_entropy(token) > 4.0 and len(token) >= 16
    long_hex = re.fullmatch(r"[0-9a-fA-F]{32,}", token) is not None
    long_b64 = re.fullmatch(r"[A-Za-z0-9+/]{32,}={0,2}", token) is not None
    return bool(secret_like) or (high_entropy and (long_hex or long_b64))


# ---------------------------------------------------------------- 表达式求值

_TOKEN_RE = re.compile(r"""
    \s*
    ( //.*?// | /.*?/[gi]* | "[^"]*" | '[^']*'
    | [A-Za-z_][A-Za-z0-9_.]* | \( | \) | , | && | \|\| | !
    | == | != | >= | <= | > | < | = | \+ | - | \* | / | \d+\.?\d* )
    \s*
""", re.VERBOSE)


def _tokenize(expr: str) -> list:
    return [m.group(1) for m in _TOKEN_RE.finditer(expr)]


class _Parser:
    """递归下降: or -> and -> not -> compare -> primary。"""

    def __init__(self, toks: list):
        self.toks = toks
        self.pos = 0

    def parse(self):
        return self._parse_or()

    def _peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def _next(self):
        t = self._peek()
        self.pos += 1
        return t

    def _parse_or(self):
        left = self._parse_and()
        while self._peek() == "||":
            self._next()
            left = {"op": "or", "left": left, "right": self._parse_and()}
        return left

    def _parse_and(self):
        left = self._parse_not()
        while self._peek() == "&&":
            self._next()
            left = {"op": "and", "left": left, "right": self._parse_not()}
        return left

    def _parse_not(self):
        if self._peek() in ("!", "not"):
            self._next()
            return {"op": "not", "operand": self._parse_not()}
        return self._parse_compare()

    def _parse_compare(self):
        left = self._parse_primary()
        op = self._peek()
        if op in (">", "<", ">=", "<=", "==", "!=", "="):
            self._next()
            return {"op": "==" if op == "=" else op, "left": left,
                    "right": self._parse_primary()}
        return left

    def _parse_primary(self):
        t = self._next()
        if t is None:
            return {"lit": False}
        if t == "(":
            e = self._parse_or()
            self._next()  # )
            return e
        if t == "true":
            return {"lit": True}
        if t == "false":
            return {"lit": False}
        if re.fullmatch(r"\d+\.?\d*", t):
            return {"num": float(t)}
        if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
            return {"str": t[1:-1]}
        if t.startswith("//") or (t.startswith("/") and len(t) > 1):
            m = re.fullmatch(r"/(.*)/([gi]*)", t)
            if m:
                flags = 0
                if "i" in m.group(2):
                    flags |= re.IGNORECASE
                try:
                    return {"regex": _safe_compile(m.group(1), flags)}
                except ValueError:
                    return {"regex": None}  # 危险/非法正则: 视为不匹配, 不崩溃
            return {"regex": None}
        if self._peek() == "(":
            self._next()
            args = []
            while self._peek() and self._peek() != ")":
                args.append(self._parse_or())
                if self._peek() == ",":
                    self._next()
            self._next()  # )
            return {"call": t, "args": args}
        return {"var": t}


def _eval(node, scope: dict):
    if node is None:
        return False
    if "lit" in node:
        return node["lit"]
    if "num" in node:
        return node["num"]
    if "str" in node:
        return node["str"]
    if "regex" in node:
        return node["regex"]
    if node.get("op") == "and":
        return bool(_eval(node["left"], scope)) and bool(_eval(node["right"], scope))
    if node.get("op") == "or":
        return bool(_eval(node["left"], scope)) or bool(_eval(node["right"], scope))
    if node.get("op") == "not":
        return not bool(_eval(node["operand"], scope))
    if node.get("op") in (">", "<", ">=", "<=", "==", "!="):
        l, r = _eval(node["left"], scope), _eval(node["right"], scope)
        op = node["op"]
        if op == ">":
            return l > r
        if op == "<":
            return l < r
        if op == ">=":
            return l >= r
        if op == "<=":
            return l <= r
        if op == "==":
            return l == r
        return l != r
    if "call" in node:
        args = [_eval(a, scope) for a in node["args"]]
        fn = node["call"]
        if fn == "contains":
            return str(args[0]).__contains__(str(args[1]))
        if fn == "matches":
            # 语义: matches(subject, pattern) —— 判断 subject 是否匹配 pattern。
            # args[0]=待匹配文本(subject), args[1]=正则(pattern, 已编译或字符串)。
            subj = args[0]
            pat = args[1] if len(args) > 1 else None
            if isinstance(pat, str):
                try:
                    pat = _safe_compile(pat)
                except (ValueError, re.error):
                    return False
            if pat is None:
                return False
            return bool(pat.search(str(subj)))
        if fn == "regex_contains":
            # 同 matches: regex_contains(subject, pattern)
            subj = args[0]
            pat = args[1] if len(args) > 1 else None
            if isinstance(pat, str):
                try:
                    pat = _safe_compile(pat)
                except (ValueError, re.error):
                    return False
            if pat is None:
                return False
            return bool(pat.search(str(subj)))
        if fn == "startsWith":
            return str(args[0]).startswith(str(args[1]))
        if fn == "endsWith":
            return str(args[0]).endswith(str(args[1]))
        if fn == "len":
            return len(str(args[0]))
        if fn == "min_len":
            return len(str(args[0])) >= int(args[1] or 0)
        if fn == "max_len":
            return len(str(args[0])) <= int(args[1] or 0)
        if fn == "count_occurrences":
            return str(args[0]).count(str(args[1]))
        if fn == "entropy":
            return _shannon_entropy(str(args[0]))
        if fn == "has_secret":
            return _has_secret(str(args[0]))
        if fn == "forbidden":
            return any(p.search(str(args[0])) for p in _FORBIDDEN_PATTERNS)
        if fn == "required":
            return str(args[0]).strip() != ""
        raise ValueError(f"unknown fn {fn}")
    if "var" in node:
        v: Any = scope
        for part in str(node["var"]).split("."):
            if v is None:
                return None
            v = v.get(part) if isinstance(v, dict) else None
        return v
    return False


# ---------------------------------------------------------------- 规则引擎

class RuleEngine:
    def __init__(self):
        self.rules = []

    def load_yaml_text(self, text: str) -> int:
        self.rules = self._parse_rules(text)
        return len(self.rules)

    def load_path(self, path: str) -> int:
        with open(path, "r", encoding="utf-8") as f:
            return self.load_yaml_text(f.read())

    @staticmethod
    def _parse_rules(text: str) -> list:
        if yaml is None:
            return []
        try:
            data = yaml.safe_load(text) or []
        except Exception:
            return []
        if not isinstance(data, list):
            data = [data]
        rules = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if not item.get("id") or not item.get("assert"):
                continue
            match = item.get("match")
            if isinstance(match, str):
                match = {"content": match}
            rules.append({
                "id": item["id"],
                "severity": item.get("severity", "error"),
                "match": match or {},
                "assert": item["assert"],
                "message": item.get("message", ""),
            })
        return rules

    def validate(self) -> dict:
        issues = []
        seen = set()
        for r in self.rules:
            if not r["id"]:
                issues.append("规则缺少 id")
            elif r["id"] in seen:
                issues.append(f"重复的规则 id: {r['id']}")
            else:
                seen.add(r["id"])
            if r["severity"] not in ("error", "warn", "info"):
                issues.append(f"规则 {r['id']} 的 severity 非法: {r['severity']}")
            if not r["assert"]:
                issues.append(f"规则 {r['id']} 缺少 assert")
            else:
                try:
                    _eval(_Parser(_tokenize(r["assert"])).parse(),
                          {"path": "", "content": "", "kind": "", "text": ""})
                except Exception as exc:  # noqa: BLE001
                    issues.append(f"规则 {r['id']} 的 assert 解析失败: {exc}")
            if not r["message"]:
                issues.append(f"规则 {r['id']} 缺少 message")
        return {"valid": not issues, "issues": issues}

    @staticmethod
    def _hit_lines(content: str, needle: str) -> list:
        return [i + 1 for i, line in enumerate(content.split("\n")) if needle in line]

    def check(self, path: str, content: str, kind: str = "file") -> list:
        violations = []
        for r in self.rules:
            match = r["match"]
            if match.get("kind") and match["kind"] != kind:
                continue
            if match.get("path"):
                if not _glob_to_regex(str(match["path"])).match(path):
                    continue
            if match.get("content"):
                try:
                    pattern = _safe_compile(str(match["content"]))
                    if not pattern.search(content):
                        continue
                except (ValueError, re.error):
                    continue
            scope = {"path": path, "content": content, "kind": kind, "text": content}
            try:
                ok = _eval(_Parser(_tokenize(r["assert"])).parse(), scope)
            except Exception as exc:  # noqa: BLE001
                violations.append({
                    "id": r["id"], "severity": "error",
                    "message": f"rule eval error: {exc}", "path": path,
                    "rule": r["assert"],
                })
                continue
            if not ok:
                lines = []
                m = re.search(r'contains\s*\(\s*\w+\s*,\s*"([^"]+)"', r["assert"])
                if m:
                    lines = self._hit_lines(content, m.group(1))
                violations.append({
                    "id": r["id"], "severity": r["severity"],
                    "message": r["message"], "path": path,
                    "rule": r["assert"], "hit_lines": lines,
                })
        return violations

    def enforce(self, content: str, path: str, kind: str = "file") -> "str | None":
        """规则强制: 返回 None 表示通过; 返回拒绝原因字符串表示命中 error 级违规。

        让规则引擎从「仅检测」升级为「可执行门禁」——写文件/应用补丁前调用,
        error 级违规直接返回拒绝原因, 由调用方阻断写入。
        """
        violations = self.check(path, content, kind)
        blocking = [v for v in violations if v.get("severity") == "error"]
        if not blocking:
            return None
        msgs = "; ".join(v.get("message", v.get("id", "")) for v in blocking)
        return f"[策略拦截] 命中错误级规则: {msgs}"


    def handle(self, line: str) -> str:
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {}) or {}
            req_id = req.get("id")
            if method == "load":
                if params.get("rules_yaml"):
                    count = self.load_yaml_text(str(params["rules_yaml"]))
                elif params.get("rules_path"):
                    count = self.load_path(str(params["rules_path"]))
                else:
                    raise ValueError("missing rules_yaml or rules_path")
                v = self.validate()
                result = {"count": count, "valid": v["valid"], "issues": v["issues"]}
            elif method == "check":
                violations = self.check(
                    str(params.get("path", "")), str(params.get("content", "")),
                    str(params.get("kind", "file")))
                result = {"violations": violations, "passed": not violations}
            elif method == "lint":
                report, errors, warns, infos = [], 0, 0, 0
                for f in params.get("files", []) or []:
                    violations = self.check(str(f.get("path", "")), str(f.get("content", "")), "file")
                    report.append({"path": f.get("path", ""), "violations": violations})
                    for x in violations:
                        if x["severity"] == "error":
                            errors += 1
                        elif x["severity"] == "warn":
                            warns += 1
                        else:
                            infos += 1
                result = {"report": report, "errors": errors, "warns": warns, "infos": infos}
            elif method == "validate":
                if params.get("rules_yaml"):
                    self.load_yaml_text(str(params["rules_yaml"]))
                result = self.validate()
            elif method in ("_meta/list", "list"):
                result = {
                    "engine": "rules", "version": "1.0.0-python",
                    "methods": ["load", "check", "lint", "validate"],
                }
            else:
                return json.dumps({"id": req_id, "ok": False,
                                   "error": f"Unknown method: {method}"}, ensure_ascii=False)
            return json.dumps({"id": req_id, "ok": True, "result": result}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"id": None, "ok": False, "error": str(exc)}, ensure_ascii=False)

    def run(self) -> None:
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(self.handle(line) + "\n")
            sys.stdout.flush()


def enforce(content: str, path: str, kind: str = "file",
           engine: "RuleEngine | None" = None) -> "str | None":
    """模块级便捷封装: 对给定内容做规则强制, 返回拒绝原因或 None (通过)。"""
    if engine is None:
        return None
    return engine.enforce(content, path, kind)


if __name__ == "__main__":
    RuleEngine().run()
