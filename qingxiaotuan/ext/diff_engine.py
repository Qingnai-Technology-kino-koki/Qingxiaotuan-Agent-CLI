"""纯 Python 实现 diff 引擎
行/词级 Myers diff + patch + 3-way merge
"""
import json
import sys
import difflib
import os


class DiffEngine:
    """统一 JSONL IPC 协议的 diff 引擎"""

    def __init__(self):
        self.methods = {
            "diff": self.diff,
            "patch": self.patch,
            "merge3": self.merge3,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "diff",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["line_diff", "word_diff", "patch", "merge3"],
        }

    def diff(self, params):
        """行级 Myers diff"""
        old = params.get("old", "")
        new = params.get("new", "")
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=params.get("fromfile", "old"),
            tofile=params.get("tofile", "new"),
            lineterm="\n",
        )
        result = "".join(diff)

        # 修复: ndiff 返回 generator, 先转 list 再统计
        ndiff_lines = list(difflib.ndiff(old_lines, new_lines))
        added = sum(1 for d in ndiff_lines if d.startswith("+ "))
        removed = sum(1 for d in ndiff_lines if d.startswith("- "))

        return {
            "diff": result,
            "added": added,
            "removed": removed,
            # 修复: changed 必须反映「实际有增删」, 旧实现用 len(ndiff_lines) > 0
            # (ndiff 恒含未变更行) 导致即使内容相同也恒为 True
            "changed": added > 0 or removed > 0,
        }

    def patch(self, params):
        """应用 unified diff patch (逐 hunk 模拟, context/remove/add 按序消费源行)"""
        source = params.get("source", "") or params.get("content", "")
        patch_text = params.get("patch", "")

        try:
            source_lines = source.splitlines(keepends=True)
            patch_lines = patch_text.splitlines(keepends=True)

            import re

            # 解析所有 hunk, 每个 hunk 是 [(type, text)] 按序排列
            hunks = []  # list of [(op, text), ...]
            i = 0
            while i < len(patch_lines):
                line = patch_lines[i]
                if line.startswith("@@"):
                    m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @", line)
                    if m:
                        old_start = int(m.group(1)) - 1
                        i += 1
                        ops = []
                        while i < len(patch_lines) and not patch_lines[i].startswith("@@") and not patch_lines[i].startswith("---") and not patch_lines[i].startswith("+++"):
                            pl = patch_lines[i]
                            if pl.startswith("-"):
                                ops.append(("-", pl[1:]))
                            elif pl.startswith("+"):
                                ops.append(("+", pl[1:]))
                            elif pl.startswith(" ") or pl == "\n":
                                ops.append((" ", pl[1:] if pl.startswith(" ") else ""))
                            i += 1
                        hunks.append((old_start, ops))
                    else:
                        i += 1
                else:
                        i += 1

            # 按 hunk 顺序应用: 维护 src_idx 跟踪源文件消费位置
            result = []
            src_idx = 0
            for old_start, ops in hunks:
                # 复制 old_start 之前未被消费的源行
                while src_idx < old_start and src_idx < len(source_lines):
                    result.append(source_lines[src_idx])
                    src_idx += 1
                # 按 hunk 操作序列表处理, 并对上下文行做一致性校验:
                # 若 hunk 期望的上下文与源文件实际行不一致, 说明 patch 与源不匹配,
                # 必须 fail-closed 返回 applied:False, 避免静默把错误内容写入文件。
                for op, text in ops:
                    if op == " ":  # context: 必须与源文件对应行一致
                        if src_idx >= len(source_lines):
                            return {"result": source, "applied": False,
                                    "error": f"patch 上下文越界 @ 源行 {src_idx + 1}: "
                                             f"hunk 期望上下文但源文件已结束"}
                        src_line = source_lines[src_idx]
                        exp, act = text.rstrip("\n"), src_line.rstrip("\n")
                        if exp != act:
                            return {"result": source, "applied": False,
                                    "error": f"patch 上下文不匹配 @ 源行 {src_idx + 1}: "
                                             f"期望 {exp!r}, 实际 {act!r}"}
                        result.append(src_line)
                        src_idx += 1
                    elif op == "-":  # remove: 消费一行源行, 不输出
                        if src_idx >= len(source_lines):
                            return {"result": source, "applied": False,
                                    "error": f"patch 删除越界 @ 源行 {src_idx + 1}: "
                                             f"源文件已结束"}
                        src_idx += 1
                    elif op == "+":  # add: 输出, 不消费源行
                        result.append(text)
            # 复制剩余源行
            while src_idx < len(source_lines):
                result.append(source_lines[src_idx])
                src_idx += 1

            return {"result": "".join(result), "applied": True}
        except Exception as e:
            return {"result": source, "applied": False, "error": str(e)}

    def merge3(self, params):
        """3-way merge: base + ours + theirs -> result

        算法: 对 base->ours 和 base->theirs 分别做 diff,
        如果两者修改了同一区域 -> 冲突, 否则应用非冲突变更。
        """
        base = params.get("base", "")
        theirs = params.get("theirs", "")
        ours = params.get("ours", "")

        base_lines = base.splitlines(keepends=True)
        ours_lines = ours.splitlines(keepends=True)
        theirs_lines = theirs.splitlines(keepends=True)

        sm_ours = difflib.SequenceMatcher(None, base_lines, ours_lines)
        sm_theirs = difflib.SequenceMatcher(None, base_lines, theirs_lines)

        ours_changes = []
        for tag, i1, i2, j1, j2 in sm_ours.get_opcodes():
            if tag in ("replace", "insert", "delete"):
                ours_changes.append((i1, i2, ours_lines[j1:j2]))

        theirs_changes = []
        for tag, i1, i2, j1, j2 in sm_theirs.get_opcodes():
            if tag in ("replace", "insert", "delete"):
                theirs_changes.append((i1, i2, theirs_lines[j1:j2]))

        conflicts = []
        conflict_bases = set()   # 冲突覆盖的 base 行下标
        conflict_gaps = set()    # 冲突涉及插入的空隙下标 (i1==i2 的空区间)
        for o_i1, o_i2, _ in ours_changes:
            for t_i1, t_i2, _ in theirs_changes:
                # 每个改动"触及"的位置: 行区间, 或插入所在的空隙下标。
                # 两侧触及同一位置即冲突 —— 覆盖 行-行 / 行-插入 / 插入-插入 三类。
                o_touch = set(range(o_i1, o_i2)) or {o_i1}
                t_touch = set(range(t_i1, t_i2)) or {t_i1}
                if not (o_touch & t_touch):
                    continue
                conflicts.append({
                    "base_start": min(o_i1, t_i1), "base_end": max(o_i2, t_i2)})
                conflict_bases.update(o_touch & t_touch)
                conflict_bases.update(o_touch)
                conflict_bases.update(t_touch)
                if o_i1 == o_i2:
                    conflict_gaps.add(o_i1)
                if t_i1 == t_i2:
                    conflict_gaps.add(t_i1)

        # 修复: 冲突主体用"改动集合"判定 (行区间 + 插入空隙), 而非只按行区间算 ——
        # 旧实现下两侧在同一空隙的并发插入 (o_i1==o_i2) 因空区间不重叠而漏判,
        # 会静默把两侧插入都并入 (如 A Y X B 而非报冲突)。
        # 非冲突改动按 base 坐标升序统一单遍应用, offset 随已应用改动单调递增。
        result_lines = list(base_lines)
        all_changes = [(i1, i2, nl) for (i1, i2, nl) in theirs_changes] + \
                      [(i1, i2, nl) for (i1, i2, nl) in ours_changes]
        all_changes.sort(key=lambda c: c[0])
        offset = 0
        for i1, i2, new_lines in all_changes:
            if any(k in conflict_bases for k in range(i1, i2)):
                continue
            if i1 == i2 and i1 in conflict_gaps:
                continue
            result_lines[i1 + offset:i2 + offset] = new_lines
            offset += len(new_lines) - (i2 - i1)

        return {"result": "".join(result_lines), "conflicts": conflicts}

    def handle(self, line):
        """处理一行 JSON 请求"""
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
            req = {}
            resp = {"id": req.get("id") if isinstance(req, dict) else None, "ok": False, "error": str(e)}
            return json.dumps(resp, ensure_ascii=False)

    def run(self):
        """主循环: 读取 stdin 逐行处理"""
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            resp = self.handle(line)
            sys.stdout.write(resp + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    engine = DiffEngine()
    engine.run()
