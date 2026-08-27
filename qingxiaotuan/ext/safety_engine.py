"""纯 Python 实现 safety 引擎
最小影响半径护栏: 命令/SQL/写操作执行前静态风险评分 (none→critical)

本模块只依赖标准库, 既作为 IPC 引擎进程运行, 也作为包内模块被 tools/shell.py、
tools/code.py 直接 import —— 危险命令判定的「单一来源」在这里维护。
"""
import json
import sys
import re


# ------------------------------------------------------------ 致命红线判定 (token 化)
# 正则容易被旗标顺序/写法变体绕过 (rm -r -f / git push -f / del /s /q),
# 这里用分词 + 旗标归一的方式判定, 供引擎与各工具共用。

def _segments(text: str):
    """按命令分隔符切段 (; && || | 及换行), 每段独立分析。"""
    return [s for s in re.split(r"&&|\|\||[;|&\n]", text) if s.strip()]


def _strip_sudo(parts):
    while parts and parts[0] in ("sudo", "doas"):
        parts = parts[1:]
    return parts


# 前缀变量赋值 (NAME=value, 值可为带引号串), 仅在文本开头或分隔符后识别,
# 避免误收 `echo foo=bar` / `--opt=val` 这类普通参数。
_VAR_DEF_RE = re.compile(
    r"(?:^|[;|&\n])\s*([A-Za-z_]\w*)=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")


def _normalize(text: str) -> str:
    """归一化间接写法, 让红线判定穿透子壳 / 变量 / 引号包裹。

    - 命令替换 $(...) 与 `...`: 内层命令会被真实执行, 抽出作为待分析片段;
    - NAME=value 前缀赋值: 收集后把后续文本里的 $NAME/${NAME} 替换为字面值
      (如 R="rm"; F="-rf"; $R $F /data → rm -rf /data);
    - token 首尾的包裹符 () ' " ` 剥掉 ((rm -rf /)、"rm -rf x" 同样命中)。
    注意: 变量替换须在 lower() 之前做 —— 变量名大小写敏感。
    """
    extra = []

    def _grab(m):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        if inner and inner.strip():
            extra.append(inner)
        return " "

    text = re.sub(r"\$\(([^()]*)\)|`([^`]*)`", _grab, text)

    env = {}

    def _collect(m):
        name, raw = m.group(1), m.group(2)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
            raw = raw[1:-1]
        env[name] = raw
        return " "

    if "=" in text:
        text = _VAR_DEF_RE.sub(_collect, text)

    if env:
        def _expand(m):
            return env.get(m.group(1) or m.group(2), m.group(0))
        text = re.sub(r"\$\{(\w+)\}|\$(\w+)", _expand, text)

    cleaned = " ".join(tok.strip("()'\"`") for tok in text.split())
    if extra:
        cleaned += "\n" + "\n".join(extra)
    return cleaned


def has_recursive_rm(text: str) -> bool:
    """递归+强制删除: rm -rf / -fr / -r -f / --recursive --force / sudo rm 均命中。

    旗标后置写法 (GNU getopt 重排, 如 `rm dir -rf`) 同样命中 ——
    因此扫描全部 token 收集旗标, 不在首个操作数处截断; `--` 之后的才是真参数。
    判定前先经 _normalize 穿透子壳/变量/引号等间接写法。"""
    for seg in _segments(_normalize(text).lower()):
        parts = _strip_sudo(seg.split())
        if not parts or parts[0] != "rm":
            continue
        long_flags, short_letters = set(), ""
        for tok in parts[1:]:
            if tok == "--":
                break
            if tok.startswith("--"):
                long_flags.add(tok)
            elif tok.startswith("-") and len(tok) > 1:
                short_letters += tok[1:]
        recursive = "--recursive" in long_flags or "r" in short_letters.lower()
        force = "--force" in long_flags or "f" in short_letters
        if recursive and force:
            return True
    return False


def has_force_push(text: str) -> bool:
    """强推: git push -f / --force / --force-with-lease[=x], 不受旗标顺序影响;
    `+refspec` (git 强推语法, 如 push origin +main) 同样视为强推。

    全量扫描 push 之后的所有 token (不在首个 remote/refspec 操作数处截断),
    覆盖 `git push origin main --force` 这类旗标后置写法。"""
    for seg in _segments(_normalize(text).lower()):
        parts = seg.split()
        if "push" not in parts:
            continue
        rest = parts[parts.index("push") + 1:]
        short_letters = ""
        for tok in rest:
            if tok == "--":
                break  # -- 之后是纯位置参数, 不再收集旗标
            if tok.startswith("+"):
                return True  # +refspec: git 的强制推送语法
            if tok.startswith("--force"):
                return True  # --force / --force-with-lease[=...]
            if tok.startswith("-") and len(tok) > 1:
                short_letters += tok[1:]
            # 操作数 (remote/refspec) 不截断扫描, 覆盖旗标后置变体
        if "f" in short_letters:
            return True
    return False


def has_win_recursive_delete(text: str) -> bool:
    """Windows 递归删除: del /s /q、rd /s、rmdir /s (同样先归一化)。"""
    for seg in _segments(_normalize(text).lower()):
        parts = seg.split()
        if not parts or parts[0] not in ("del", "rd", "rmdir"):
            continue
        if "/s" in {t for t in parts[1:] if t.startswith("/")}:
            return True
    return False


def is_redline(command: str) -> bool:
    """致命操作红线 (单一来源): shell 工具 YOLO 红线与本引擎 critical 共用。

    各 has_* 判定内部会先 _normalize 穿透子壳/变量/引号间接写法。"""
    return has_recursive_rm(command) or has_force_push(command) or has_win_recursive_delete(command)


class SafetyEngine:
    """统一 JSONL IPC 协议的 safety 引擎"""

    # 危险模式库 (label 双语: 英文便于测试/日志, 中文便于展示)
    # 注: rm 递归强删与 force push 由上方 token 化函数覆盖 (正则易被写法变体绕过), 不再重复列出。
    CRITICAL_PATTERNS = [
        (r"DROP\s+TABLE", "drop table (删除数据表)"),
        (r"DELETE\s+FROM\s+.*\s*;?", "delete all rows (删除全部数据)"),
        (r"format\s+[a-zA-Z]:", "format disk (格式化磁盘)"),
        (r"mkfs\.", "create filesystem (创建文件系统)"),
        (r"dd\s+if=.*\s+of=/dev/sd", "write raw device (直接写磁盘设备)"),
    ]

    HIGH_PATTERNS = [
        # 注: rm 递归强删已升级为 critical (见 has_recursive_rm), 不再列在 high。
        (r"chmod\s+777", "chmod 777 (全权限修改)"),
        (r"git\s+reset\s+--hard", "git reset --hard (硬重置)"),
        (r"ALTER\s+TABLE\s+.*\s+DROP", "alter table drop column (修改表结构删除列)"),
        (r"UPDATE\s+.*\s+SET.*WHERE.*=", "bulk update (批量更新数据)"),
        (r"\|.*\b(sh|bash|zsh)\b", "pipe to shell (管道到 shell 执行)"),
    ]

    MEDIUM_PATTERNS = [
        (r"sudo\s+", "超级用户权限"),
        (r">\s*/etc/", "写入系统配置"),
        (r"mv\s+.*/etc/", "移动系统文件"),
        (r"kill\s+-9", "强制终止进程"),
    ]

    def __init__(self):
        self.methods = {
            "score": self.score,
            "analyze": self.analyze,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "safety",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["risk_scoring", "command_analysis", "sql_analysis", "blast_radius"],
        }

    def _match_patterns(self, text: str, patterns):
        """匹配模式"""
        matches = []
        for pattern, label in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                matches.append(label)
        return matches

    def score(self, params):
        """评分: command/sql/write"""
        text = params.get("command", "") or params.get("sql", "") or params.get("text", "")
        action_type = params.get("type", "shell")

        critical = self._match_patterns(text, self.CRITICAL_PATTERNS)
        high = self._match_patterns(text, self.HIGH_PATTERNS)
        medium = self._match_patterns(text, self.MEDIUM_PATTERNS)

        # token 化补充检测: 覆盖正则易绕过的变体 (rm -r -f / git push -f / del /s 等)
        if has_recursive_rm(text):
            critical.append("recursive force delete (递归强制删除)")
        if has_force_push(text):
            critical.append("force push (强制推送)")
        if has_win_recursive_delete(text):
            critical.append("windows recursive delete (Windows 递归删除)")

        # 确定风险级别
        if critical:
            risk = "critical"
            score_val = 100
        elif high:
            risk = "high"
            score_val = 70
        elif medium:
            risk = "medium"
            score_val = 40
        else:
            risk = "none"
            score_val = 0

        # 计算 blast radius
        blast_radius = {"files": [], "services": [], "data": []}
        if "rm" in text:
            files = re.findall(r"rm\s+(?:-{1,2}[\w-]+\s+)+(.+)", text)
            blast_radius["files"].extend(f.split()[0] for f in files if f.strip())
        if "git" in text and "push" in text:
            blast_radius["services"].append("git-remote")
        if "DROP" in text.upper() or "DELETE" in text.upper():
            blast_radius["data"].append("database")

        # 替代建议
        suggestions = []
        if risk == "critical":
            suggestions = [
                "使用更精确的路径: rm -rf ./specific-path",
                "先备份再删除: cp -r ./target ./backup",
                "使用 git revert 代替 git reset --hard",
            ]
        elif risk == "high":
            suggestions = [
                "确认操作范围后再执行",
                "建议添加 --dry-run 先预览",
            ]

        return {
            "risk": risk,
            "score": score_val,
            "block": risk == "critical",
            "reasons": critical + high + medium,
            "blast_radius": blast_radius,
            "suggestions": suggestions,
        }

    def analyze(self, params):
        """批量分析一组计划中的操作 (dry-run), 返回整体风险与逐条理由。"""
        ops = params.get("ops", [])
        if not isinstance(ops, list):
            ops = [ops]
        items = []
        for op in ops:
            kind = op.get("kind", "command")
            text = op.get("text", "")
            target = op.get("target", "")
            scored = self.score({"command": text, "type": "shell"})
            items.append({
                "kind": kind,
                "target": target,
                "text": text,
                "risk": scored["risk"],
                "score": scored["score"],
                "reasons": scored["reasons"],
            })
        overall = "critical" if any(it["risk"] == "critical" for it in items) else (
            "high" if any(it["risk"] == "high" for it in items) else (
            "medium" if any(it["risk"] == "medium" for it in items) else "none"))
        advice = {
            "critical": "BLOCK: 存在致命风险操作, 必须人工确认后执行",
            "high": "CONFIRM: 存在高风险操作, 建议逐条确认",
            "medium": "REVIEW: 存在中风险操作, 建议复核",
            "none": "ok: 未发现明显风险",
        }[overall]
        return {"overall": overall, "advice": advice, "items": items}

    def handle(self, line):
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id", None)
            if method in self.methods:
                result = self.methods[method](params)
                resp = {"id": req_id, "ok": True, "result": result}
            else:
                resp = {"id": req_id, "ok": False, "error": f"Unknown method: {method}"}
            return json.dumps(resp, ensure_ascii=False)
        except Exception as e:
            resp = {"id": None, "ok": False, "error": str(e)}
            return json.dumps(resp, ensure_ascii=False)

    def run(self):
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(self.handle(line) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    SafetyEngine().run()
