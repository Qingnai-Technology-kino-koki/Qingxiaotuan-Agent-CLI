"""纯 Python 实现 safety 引擎 (替代 ext/c/safety.c)
最小影响半径护栏: 命令/SQL/写操作执行前静态风险评分 (none→critical)
"""
import json
import sys
import re


class SafetyEngine:
    """统一 JSONL IPC 协议的 safety 引擎"""

    # 危险模式库 (label 双语: 英文便于测试/日志, 中文便于展示)
    CRITICAL_PATTERNS = [
        (r"rm\s+-rf\s+\S+", "recursive delete with path (递归删除指定路径)"),
        (r"git\s+push\s+--force", "force push (强制推送)"),
        (r"DROP\s+TABLE", "drop table (删除数据表)"),
        (r"DELETE\s+FROM\s+.*\s*;?", "delete all rows (删除全部数据)"),
        (r"format\s+[a-zA-Z]:", "format disk (格式化磁盘)"),
        (r"mkfs\.", "create filesystem (创建文件系统)"),
        (r"dd\s+if=.*\s+of=/dev/sd", "write raw device (直接写磁盘设备)"),
    ]

    HIGH_PATTERNS = [
        (r"rm\s+-rf", "recursive delete (递归删除)"),
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
            files = re.findall(r"rm\s+-rf\s+([^\s]+)", text)
            blast_radius["files"].extend(files)
        if "git" in text and "push" in text:
            blast_radius["services"].append("git-remote")
        if "DROP" in text or "DELETE" in text:
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
