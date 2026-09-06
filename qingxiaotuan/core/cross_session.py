"""跨会话引用 —— 会话分叉后的 @ 会话通信 (对标 Claude Code 的 session 引用)。

目标: 让一次对话能引用其它会话的历史作为上下文注入, 例如:
- `@session:20240801-120000-ab12cd` 引用某个分叉/历史会话的摘要。
- `@#12` 引用当前会话里第 12 条 user/assistant 消息。

三要素:
1. 解析  —— 从用户输入里抽取出 @mention 标记 (会话 id / 消息序)。
2. 解析器 —— 把标记解析成实际内容 (读 JSONL 事件流)。
3. 注入  —— 把解析结果拼装成可读的上下文块, 经 Agent.run 注入 user 消息。

设计要点:
- 解析只识别白名单前缀 (session: / #), 避免与普通 @ 邮箱/提及混洗。
- 引用内容默认走「摘要 + 关键片段」, 不支持原生跨会话拉全量 (耗 token)。
- 解析失败不阻断主流程: 校验失败/找不到会话时返回明确错误文本, 由 Agent 决定是否继续。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..memory.sessions import SessionStore

# 白名单提及: `@session:<id>` / `@session:<id>#<n>` / `@#<num>` (空格/换行/结尾可选)
_MENTION_RE = re.compile(
    r"@(?:session:([A-Za-z0-9_\-]+)(?:#(\d+))?)|@#(\d+)\b"
)


@dataclass
class Mention:
    kind: str                # "session" | "message" | "session_msg"
    target: str              # 会话 id 或消息序号 (字符串)
    index: Optional[str] = None  # 仅 session_msg: 该会话内的第 N 条消息
    line: int = 0            # 在输入中的起始位置 (便于脱敏原文时定位)
    resolved: bool = False   # 是否成功解析
    summary: str = ""        # 注入用的内容 (摘要)
    error: str = ""          # 解析失败时的错误说明
    extra: Dict[str, Any] = field(default_factory=dict)


class CrossSessionResolver:
    """把 @mention 解析成可注入的上下文块。

    home: 会话目录的父目录 (i.e. config.home), 会话文件位于 home/sessions/*.jsonl。
    """

    def __init__(self, home: Optional[Path] = None,
                 session_store: Optional[SessionStore] = None) -> None:
        self.home = Path(home) if home is not None else None
        self.store = session_store
        self._limit = 4000          # 注入块最大字符数
        self._max_blocks = 6        # 单条输入最多解析的引用数

    # ------------------------------------------------------------ 解析

    def find_mentions(self, text: str) -> List[Mention]:
        """从文本里抽出所有白名单 @mention。"""
        out: List[Mention] = []
        for m in _MENTION_RE.finditer(text):
            if m.group(1):  # session[:#idx]
                if m.group(2):
                    out.append(Mention(kind="session_msg", target=m.group(1),
                                       index=m.group(2), line=m.start()))
                else:
                    out.append(Mention(kind="session", target=m.group(1), line=m.start()))
            elif m.group(3):  # #n 当前会话消息
                out.append(Mention(kind="message", target=m.group(3), line=m.start()))
            if len(out) >= self._max_blocks:
                break
        return out

    # ------------------------------------------------------------ 解析目标内容

    def _session_path(self, sid: str) -> Path:
        if self.store is not None:
            return self.store.dir / f"{sid}.jsonl"
        if self.home is not None:
            return self.home / "sessions" / f"{sid}.jsonl"
        raise ValueError("未配置会话目录 (需要 home 或 session_store)")

    def resolve(self, mention: Mention) -> Mention:
        """解析单个 mention, 填充 summary 或 error。"""
        try:
            if mention.kind == "session":
                return self._resolve_session(mention)
            if mention.kind == "session_msg":
                return self._resolve_session_message(mention)
            if mention.kind == "message":
                return self._resolve_message(mention)
        except FileNotFoundError:
            mention.error = f"找不到目标会话: {mention.target}"
        except ValueError as exc:
            mention.error = str(exc) if exc else "引用解析失败"
        except OSError as exc:
            mention.error = f"读取会话失败: {exc}"
        mention.resolved = False
        return mention

    def _resolve_session(self, mention: Mention) -> Mention:
        path = self._session_path(mention.target)
        if not path.exists():
            mention.error = f"找不到会话 {mention.target} (会话文件不存在)"
            return mention

        meta = SessionStore.read_meta(path) or {}
        # 摘要: 优先读 meta 里的 task, 其次拼 event 摘要
        task = str(meta.get("task", ""))
        lineage: List[str] = []
        if meta.get("kind") == "fork":
            lineage.append(f"分叉自 {meta.get('parent')}" +
                           (f" @第{meta.get('branch_point')}条" if meta.get('branch_point') else ""))

        # export_summary 是实例方法 (无 path 时读 self.file), 构造临时 store 指向目标文件
        tmp_store = SessionStore(path.parent)
        tmp_store.dir = path.parent
        tmp_store.file = path
        body = tmp_store.export_summary()
        parts: List[str] = []
        if task:
            parts.append(f"任务: {task}")
        if lineage:
            parts.append(f"谱系: {'; '.join(lineage)}")
        if body:
            parts.append(body)
        content = "\n".join(parts)[: self._limit]
        if not content.strip():
            content = "(该会话暂无内容)"

        mention.summary = content
        mention.resolved = True
        mention.extra = {"session_id": mention.target, "task": task,
                         "forked": meta.get("kind") == "fork",
                         "parent": meta.get("parent"),
                         "branch_point": meta.get("branch_point")}
        return mention

    def _resolve_message(self, mention: Mention) -> Mention:
        """解析 @#<num>: 在当前会话 (SessionStore.file) 里取第 num 条 user/assistant 消息。"""
        if self.store is None:
            mention.error = "@# 消息引用需要提供当前 session (session_store)"
            return mention
        idx = int(mention.target)
        if idx < 1:
            mention.error = f"消息序号需 >= 1 (得到 {idx})"
            return mention

        found, count = self._find_message(self.store.file, idx)
        if found is None:
            mention.error = f"当前会话没有第 {idx} 条消息 (共 {count} 条)"
            return mention
        self._fill_message(mention, found, idx, scope="当前会话")
        return mention

    def _resolve_session_message(self, mention: Mention) -> Mention:
        """解析 @session:<id>#<n>: 取指定会话里的第 n 条消息。"""
        path = self._session_path(mention.target)
        if not path.exists():
            mention.error = f"找不到会话 {mention.target}"
            return mention
        idx = int(mention.index or "0")
        uri = f"{mention.target}#{idx}"
        if idx < 1:
            mention.error = f"消息序号需 >= 1 (得到 {idx})"
            return mention
        found, count = self._find_message(path, idx)
        if found is None:
            mention.error = f"会话 {mention.target} 没有第 {idx} 条消息 (共 {count} 条)"
            return mention
        self._fill_message(mention, found, idx, scope=f"会话 {mention.target}", uri=uri)
        mention.extra["session_id"] = mention.target
        return mention

    # ------------------------------------------------------------ 消息提取 (按文件)

    def _find_message(self, path: Path, idx: int):
        """扫描会话文件里第 idx 条 user/assistant 消息。返回 (message_dict 或 None, 总数)。"""
        count = 0
        found: Optional[Dict[str, Any]] = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") in ("user", "assistant") and "message" in rec:
                        count += 1
                        if count == idx:
                            found = rec["message"]
                            break
        except OSError as exc:
            raise  # 由调用方转为错误文本
        return found, count

    def _fill_message(self, mention: Mention, found: Dict[str, Any], idx: int,
                      scope: str, uri: Optional[str] = None) -> None:
        role = found.get("role", "assistant")
        content = found.get("content", "")
        if isinstance(content, list):
            # 多模态/多块内容: 只取文本块
            text_parts = [b.get("text", "") for b in content
                          if isinstance(b, dict) and b.get("type") == "text"]
            content = "\n".join(text_parts)
        summary = f"[{scope} {role} 第{idx}条] {str(content)[: self._limit]}"
        # 附带该消息的工具调用 (若有)
        tcs = found.get("tool_calls")
        if tcs:
            try:
                summary += "\n工具调用: " + json.dumps(
                    [{"name": tc.get("function", {}).get("name"), "args": tc.get("function", {}).get("arguments") or ""}
                     for tc in tcs], ensure_ascii=False)[:1200]
            except Exception:  # noqa: BLE001
                pass
        mention.summary = summary
        mention.resolved = True
        mention.extra = {"index": idx, "role": role}
        if uri:
            mention.extra["uri"] = uri

    # ------------------------------------------------------------ 批量解析与注入

    def resolve_all(self, text: str) -> List[Mention]:
        mentions = self.find_mentions(text)
        for m in mentions:
            self.resolve(m)
        return mentions

    def inject(self, text: str) -> str:
        """解析 text 里的 @mention, 返回注入了引用上下文的扩展文本。

        - 找不到可注入上下文时返回原样 text。
        - 解析失败的引用会在末尾附上错误提示, 但不清除原文本。
        """
        mentions = self.find_mentions(text)
        if not mentions:
            return text
        blocks: List[str] = []
        errors: List[str] = []
        for m in mentions:
            self.resolve(m)
            if m.resolved and m.summary:
                label, ref = _block_label(m)
                blocks.append(f"[引用{label} {ref}]\n{m.summary}")
            elif m.error:
                errors.append(m.error)
        if not blocks and not errors:
            return text
        tail: List[str] = []
        if blocks:
            tail.append("\n\n—— 以下为引用的其它会话/消息内容 (只读上下文) ——\n" +
                        "\n\n".join(blocks))
        if errors:
            tail.append("\n[引用解析告警] " + " | ".join(errors))
        return text + "".join(tail)


# ------------------------------------------------------------ 谱系 / 目录 (fork tree)

    def _sessions_dir(self) -> Path:
        if self.store is not None:
            return Path(self.store.dir)
        if self.home is not None:
            return Path(self.home) / "sessions"
        raise ValueError("未配置会话目录 (需要 home 或 session_store)")

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出可引用的会话 (含标题/任务/分叉谱系/时间), 供用户挑 id 做 @session 引用。"""
        d = self._sessions_dir()
        out: List[Dict[str, Any]] = []
        if not d.exists():
            return out
        for p in sorted(d.glob("*.jsonl"), key=lambda x: x.name, reverse=True):
            meta = SessionStore.read_meta(p) or {}
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            out.append({
                "session_id": p.stem,
                "title": SessionStore.peek_title(p),
                "task": meta.get("task", ""),
                "forked": meta.get("kind") == "fork",
                "parent": meta.get("parent"),
                "branch_point": meta.get("branch_point"),
                "mtime": mtime,
            })
        return out

    def lineage(self, session_id: str) -> List[Dict[str, Any]]:
        """返回 lineage 链: 从根祖先到 ``session_id`` (含自身), 由远及近。"""
        chain: List[Dict[str, Any]] = []
        cur = session_id
        seen: set = set()
        while cur and cur not in seen:
            seen.add(cur)
            path = self._session_path(cur)
            meta = (SessionStore.read_meta(path) or {}) if path.exists() else {}
            chain.append({"session_id": cur,
                          "forked": meta.get("kind") == "fork",
                          "parent": meta.get("parent"),
                          "branch_point": meta.get("branch_point"),
                          "task": meta.get("task", "")})
            cur = meta.get("parent") if meta.get("kind") == "fork" else None
        chain.reverse()
        return chain

    def descendants(self, session_id: str) -> List[Dict[str, Any]]:
        """返回该会话的所有子孙分支 (BFS, 沿 fork.parent 反向建树)。"""
        by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for info in self.list_sessions():
            if info["forked"] and info["parent"]:
                by_parent.setdefault(info["parent"], []).append(info)
        result: List[Dict[str, Any]] = []
        stack = [session_id]
        while stack:
            cur = stack.pop()
            for child in by_parent.get(cur, []):
                result.append(child)
                stack.append(child["session_id"])
        return result

    def search(self, query: str, last_n: int = 20) -> List[Dict[str, Any]]:
        """按关键词搜索会话内容。

        搜索范围: 会话元数据 (task) + 最近的消息内容。
        返回匹配的会话列表, 按相关度排序。
        """
        query_lower = query.lower()
        results: List[Dict[str, Any]] = []

        for info in self.list_sessions()[:100]:
            score = 0
            # 搜索 task
            task = info.get("task", "")
            if query_lower in task.lower():
                score += 10
            # 搜索消息内容
            path = self._session_path(info["session_id"])
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                data = json.loads(line.strip())
                                content = ""
                                msg = data.get("message", {})
                                if isinstance(msg, dict):
                                    content = str(msg.get("content", ""))
                                if query_lower in content.lower():
                                    score += 1
                            except Exception:
                                continue
                except Exception:
                    pass

            if score > 0:
                results.append({"session_id": info["session_id"], "score": score, **info})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:last_n]

    def compare(self, session_id_a: str, session_id_b: str) -> Dict[str, Any]:
        """比较两个会话的差异。

        返回: 事件数差异、工具使用差异、安全事件差异等。
        """
        meta_a = self._read_meta(session_id_a)
        meta_b = self._read_meta(session_id_b)

        # 读取事件统计
        events_a = self._read_events_summary(session_id_a)
        events_b = self._read_events_summary(session_id_b)

        return {
            "session_a": session_id_a,
            "session_b": session_id_b,
            "meta_a": meta_a,
            "meta_b": meta_b,
            "events_a": events_a,
            "events_b": events_b,
            "event_count_diff": events_a.get("total", 0) - events_b.get("total", 0),
            "tool_diff": {
                k: events_a.get("by_tool", {}).get(k, 0) - events_b.get("by_tool", {}).get(k, 0)
                for k in set(list(events_a.get("by_tool", {}).keys()) +
                             list(events_b.get("by_tool", {}).keys()))
            },
        }

    def _read_meta(self, session_id: str) -> Dict[str, Any]:
        """读取会话元数据。"""
        path = self._session_path(session_id)
        if not path.exists():
            return {}
        return SessionStore.read_meta(path) or {}

    def _read_events_summary(self, session_id: str) -> Dict[str, Any]:
        """读取会话事件摘要。"""
        path = self._session_path(session_id)
        if not path.exists():
            return {"total": 0, "by_type": {}, "by_tool": {}}

        total = 0
        by_type: Dict[str, int] = {}
        by_tool: Dict[str, int] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        total += 1
                        etype = data.get("type", data.get("event_type", "unknown"))
                        by_type[etype] = by_type.get(etype, 0) + 1
                        tool = data.get("name", data.get("tool_name", ""))
                        if tool:
                            by_tool[tool] = by_tool.get(tool, 0) + 1
                    except Exception:
                        continue
        except Exception:
            pass

        return {"total": total, "by_type": by_type, "by_tool": by_tool}


# 便捷入口
def _block_label(m: Mention):
    """返回 (标签, 引用标识) 供注入块头显示。"""
    if m.kind == "session":
        return "会话", m.target
    if m.kind == "session_msg":
        return "会话消息", f"{m.target}#{m.index}"
    return "消息", f"#{m.target}"


def resolve_mentions(text: str, home: Optional[Path] = None,
                     session_store: Optional[SessionStore] = None) -> str:
    """把 text 里的 @session / @# 引用解析并注入, 返回扩展后的文本。"""
    return CrossSessionResolver(home=home, session_store=session_store).inject(text)