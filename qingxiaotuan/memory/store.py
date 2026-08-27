"""持久事实记忆 + FTS5 全文检索 + 标签/时间索引。

目录结构 (Hermes 风格):
    ~/.qingxiaotuan/memories/MEMORY.md   长期事实
    ~/.qingxiaotuan/memories/USER.md     用户画像
    ~/.qingxiaotuan/memories/index.db    FTS5 索引 (记忆 + 技能 + 会话摘要统一检索)
    ~/.qingxiaotuan/memories/tags.db     标签索引 (支持按标签/时间/类型检索)

增强能力:
- 标签系统: 每条记忆可带标签, 支持按标签检索
- 时间检索: 支持按时间范围检索历史记忆
- 上下文感知召回: 会话启动时自动检索相关记忆注入上下文
- 结构化检索: 支持按 kind/source/tag 组合查询
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("qingxiaotuan.memory")


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写文本文件: 先写同目录临时文件, fsync 后 os.replace 替换。

    直接就地覆盖在进程崩溃/断电/磁盘满时会把原文件截断成半个,
    记忆历史全部丢失; 走"临时文件 + 原子替换"则失败时原文件完好无损。
    (与 CronStore 的持久化范式一致)
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(path))
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


class MemoryStore:
    # 单文件条目上限: 超过则裁剪最旧条目, 防止长期运行无限膨胀。
    MAX_MEMORY_LINES = 2000
    MAX_USER_LINES = 500

    def __init__(self, home: Path, fts_enabled: bool = True) -> None:
        self.dir = home / "memories"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.dir / "MEMORY.md"
        self.user_file = self.dir / "USER.md"
        self.fts_enabled = fts_enabled
        self._db: Optional[sqlite3.Connection] = None
        self._tags_db: Optional[sqlite3.Connection] = None
        # 可重入锁: 序列化文件读改写与 SQLite 访问, 允许跨线程共享实例
        # (插件以单例提供 memory_store, swarm/后台线程可能经内核拿到同一实例)
        self._lock = threading.RLock()
        if fts_enabled:
            # 检测 FTS5 可用性: 部分 Python 发行版未编译 FTS5 支持
            if self._check_fts5_available():
                self._init_db()
                self._init_tags_db()
            else:
                log.warning("SQLite FTS5 不可用, 降级为纯文本搜索")
                self.fts_enabled = False

    # ------------------------------------------------------------- 文件层

    def read_memory(self) -> str:
        return self.memory_file.read_text(encoding="utf-8") if self.memory_file.exists() else ""

    def read_user(self) -> str:
        return self.user_file.read_text(encoding="utf-8") if self.user_file.exists() else ""

    def append_memory(self, fact: str, section: str = "事实", tags: Optional[List[str]] = None) -> str:
        """把一条事实追加进 MEMORY.md, 并同步进 FTS 索引和标签索引。

        文件写入与 FTS 索引各自隔离: 任一环节失败都只记日志、不影响主流程
        (记忆是"锦上添花", 绝不能因为 FTS5 不可用而炸掉 Agent 循环)。
        超过 MAX_MEMORY_LINES 时裁剪最旧条目, 防止无限膨胀。
        """
        timestamp = time.strftime('%Y-%m-%d')
        tag_str = f" [{','.join(tags)}]" if tags else ""
        line = f"- [{timestamp}] ({section}){tag_str} {fact.strip()}"
        with self._lock:
            try:
                self._append_rotated(self.memory_file, line + "\n", self.MAX_MEMORY_LINES)
            except OSError as exc:
                log.warning("写入 MEMORY.md 失败: %s", exc)
            try:
                self.index("memory", fact, source="MEMORY.md", tags=tags)
            except Exception as exc:  # noqa: BLE001
                log.warning("记忆 FTS 索引失败 (已忽略): %s", exc)
        return line

    @staticmethod
    def _append_rotated(path: Path, new_line: str, max_lines: int) -> None:
        """追加一行并在超过上限时丢弃最旧的行 (保持文件为最近 max_lines 条)。"""
        lines: List[str] = []
        if path.exists():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
        lines.append(new_line.rstrip("\n"))
        if len(lines) > max_lines:
            dropped = len(lines) - max_lines
            lines = lines[dropped:]
            lines.insert(0, f"- [系统] 已自动裁剪 {dropped} 条最旧记忆以控制体积")
        _atomic_write_text(path, "\n".join(lines) + "\n")

    def update_user(self, key: str, value: str) -> str:
        """以 '- key: value' 形式更新 USER.md 中的一条记录。新增键时受轮转上限保护。"""
        with self._lock:
            try:
                lines = self.user_file.read_text(encoding="utf-8").splitlines() if self.user_file.exists() else []
                prefix = f"- {key}:"
                replaced = False
                for i, line in enumerate(lines):
                    if line.strip().startswith(prefix):
                        lines[i] = f"- {key}: {value}"
                        replaced = True
                        break
                if not replaced:
                    lines.append(f"- {key}: {value}")
                    if len(lines) > self.MAX_USER_LINES:
                        dropped = len(lines) - self.MAX_USER_LINES
                        lines = lines[dropped:]
                        lines.insert(0, f"- [系统] 已自动裁剪 {dropped} 条最旧画像字段")
                _atomic_write_text(self.user_file, "\n".join(lines) + "\n")
            except OSError as exc:
                log.warning("写入 USER.md 失败: %s", exc)
        try:
            self.index("user", f"{key}: {value}", source="USER.md")
        except Exception as exc:  # noqa: BLE001
            log.warning("用户画像 FTS 索引失败 (已忽略): %s", exc)
        return f"{key}: {value}"

    # ------------------------------------------------------------- FTS5 层

    @staticmethod
    def _check_fts5_available() -> bool:
        """检测 SQLite FTS5 扩展是否可用。部分 Python 发行版未编译此支持。"""
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _test_fts USING fts5(content)")
            conn.execute("DROP TABLE _test_fts")
            conn.close()
            return True
        except sqlite3.OperationalError:
            return False

    def _init_db(self) -> None:
        # check_same_thread=False: 连接可能被后台线程使用, 由 self._lock 串行化
        self._db = sqlite3.connect(str(self.dir / "index.db"), check_same_thread=False)
        # trigram 分词器: 支持 CJK 与任意子串检索 (FTS5 默认分词器对中文不友好)
        try:
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                "USING fts5(kind, content, source, created_at, tokenize='trigram')"
            )
        except sqlite3.OperationalError:
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                "USING fts5(kind, content, source, created_at)"
            )
        self._db.commit()

    def _init_tags_db(self) -> None:
        """初始化标签索引数据库。"""
        self._tags_db = sqlite3.connect(str(self.dir / "tags.db"), check_same_thread=False)
        self._tags_db.execute(
            "CREATE TABLE IF NOT EXISTS memory_tags ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "kind TEXT NOT NULL,"
            "content TEXT NOT NULL,"
            "source TEXT,"
            "tags TEXT,"  # JSON 数组
            "created_at TEXT NOT NULL,"
            "section TEXT)"
        )
        self._tags_db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tags ON memory_tags(tags)"
        )
        self._tags_db.execute(
            "CREATE INDEX IF NOT EXISTS idx_created ON memory_tags(created_at)"
        )
        self._tags_db.commit()

    def index(self, kind: str, content: str, source: str = "", tags: Optional[List[str]] = None) -> None:
        """索引一条记忆到 FTS 和标签数据库。"""
        now = str(int(time.time()))

        with self._lock:
            # FTS 索引
            if self._db:
                try:
                    self._db.execute(
                        "INSERT INTO memory_fts (kind, content, source, created_at) VALUES (?,?,?,?)",
                        (kind, content, source, now),
                    )
                    self._db.commit()
                except sqlite3.Error as exc:
                    log.warning("FTS 写入失败 (已忽略): %s", exc)

            # 标签索引
            if self._tags_db:
                try:
                    tags_json = json.dumps(tags or [], ensure_ascii=False)
                    self._tags_db.execute(
                        "INSERT INTO memory_tags (kind, content, source, tags, created_at) VALUES (?,?,?,?,?)",
                        (kind, content, source, tags_json, now),
                    )
                    self._tags_db.commit()
                except sqlite3.Error as exc:
                    log.warning("标签索引写入失败 (已忽略): %s", exc)

    def search(self, query: str, limit: int = 5) -> List[Dict[str, str]]:
        """全文检索记忆。"""
        if not self._db:
            return []
        with self._lock:
            terms = [t for t in query.replace("?", " ").split() if t]
            if not terms:
                return []
            # trigram 要求查询词 >= 3 字符, 短词走 LIKE; 先 FTS 后 LIKE 兜底
            hits: List[Dict[str, str]] = []
            fts_terms = [t for t in terms[:6] if len(t) >= 3]
            if fts_terms:
                fts_query = " OR ".join(f'"{t}"' for t in fts_terms)
                try:
                    rows = self._db.execute(
                        "SELECT kind, content, source, rank FROM memory_fts "
                        "WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
                        (fts_query, limit),
                    ).fetchall()
                    hits = [{"kind": r[0], "content": r[1], "source": r[2]} for r in rows]
                except sqlite3.OperationalError:
                    hits = []
            if not hits:
                like_terms = terms[:4]
                clause = " OR ".join("content LIKE ?" for _ in like_terms)
                rows = self._db.execute(
                    f"SELECT kind, content, source FROM memory_fts WHERE {clause} LIMIT ?",
                    [*(f"%{t}%" for t in like_terms), limit],
                ).fetchall()
                hits = [{"kind": r[0], "content": r[1], "source": r[2]} for r in rows]
            return hits

    # ------------------------------------------------------------- 增强检索

    def search_by_tags(self, tags: List[str], limit: int = 10) -> List[Dict[str, str]]:
        """按标签检索记忆。"""
        if not self._tags_db or not tags:
            return []

        with self._lock:
            results: List[Dict[str, str]] = []
            for tag in tags:
                try:
                    rows = self._tags_db.execute(
                        "SELECT kind, content, source, tags, created_at FROM memory_tags "
                        "WHERE tags LIKE ? ORDER BY created_at DESC LIMIT ?",
                        (f'%"{tag}"%', limit),
                    ).fetchall()
                    for r in rows:
                        results.append({
                            "kind": r[0], "content": r[1], "source": r[2],
                            "tags": r[3], "created_at": r[4],
                        })
                except sqlite3.Error as exc:
                    log.warning("标签检索失败 (已忽略): %s", exc)

            # 去重
            seen = set()
            unique: List[Dict[str, str]] = []
            for r in results:
                key = r["content"][:100]
                if key not in seen:
                    seen.add(key)
                    unique.append(r)
            return unique[:limit]

    def search_by_time(
        self,
        hours: int = 24,
        kind: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, str]]:
        """按时间范围检索记忆。"""
        if not self._tags_db:
            return []

        since = str(int(time.time() - hours * 3600))
        with self._lock:
            try:
                if kind:
                    rows = self._tags_db.execute(
                        "SELECT kind, content, source, tags, created_at FROM memory_tags "
                        "WHERE created_at >= ? AND kind = ? ORDER BY created_at DESC LIMIT ?",
                        (since, kind, limit),
                    ).fetchall()
                else:
                    rows = self._tags_db.execute(
                        "SELECT kind, content, source, tags, created_at FROM memory_tags "
                        "WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
                        (since, limit),
                    ).fetchall()
                return [
                    {"kind": r[0], "content": r[1], "source": r[2], "tags": r[3], "created_at": r[4]}
                    for r in rows
                ]
            except sqlite3.Error as exc:
                log.warning("时间检索失败 (已忽略): %s", exc)
                return []

    def recall_context(self, task: str, limit: int = 5) -> str:
        """上下文感知召回: 根据当前任务自动检索相关记忆, 返回注入上下文的文本。

        策略:
        1. 全文检索匹配任务关键词
        2. 检索最近 24h 的记忆
        3. 合并去重, 按相关性排序
        """
        recalled: List[str] = []
        seen_contents: set[str] = set()

        # 全文检索
        fts_hits = self.search(task, limit=limit)
        for h in fts_hits:
            content = h["content"][:200]
            if content not in seen_contents:
                seen_contents.add(content)
                recalled.append(f"[记忆] {content}")

        # 最近 24h 的记忆
        recent = self.search_by_time(hours=24, limit=3)
        for r in recent:
            content = r["content"][:200]
            if content not in seen_contents:
                seen_contents.add(content)
                recalled.append(f"[近期] {content}")

        if not recalled:
            return ""

        header = f"[上下文召回] 找到 {len(recalled)} 条相关记忆:"
        return header + "\n" + "\n".join(f"  {i+1}. {r}" for i, r in enumerate(recalled[:limit]))

    def get_stats(self) -> Dict[str, Any]:
        """获取记忆统计信息。"""
        with self._lock:
            stats: Dict[str, Any] = {
                "memory_lines": 0,
                "user_lines": 0,
                "fts_entries": 0,
                "tag_entries": 0,
            }

            if self.memory_file.exists():
                try:
                    stats["memory_lines"] = len(self.memory_file.read_text(encoding="utf-8").splitlines())
                except OSError:
                    pass

            if self.user_file.exists():
                try:
                    stats["user_lines"] = len(self.user_file.read_text(encoding="utf-8").splitlines())
                except OSError:
                    pass

            if self._db:
                try:
                    row = self._db.execute("SELECT COUNT(*) FROM memory_fts").fetchone()
                    stats["fts_entries"] = row[0] if row else 0
                except sqlite3.Error:
                    pass

            if self._tags_db:
                try:
                    row = self._tags_db.execute("SELECT COUNT(*) FROM memory_tags").fetchone()
                    stats["tag_entries"] = row[0] if row else 0
                except sqlite3.Error:
                    pass

            return stats

    def close(self) -> None:
        with self._lock:
            if self._db:
                self._db.close()
                self._db = None
            if self._tags_db:
                self._tags_db.close()
                self._tags_db = None
