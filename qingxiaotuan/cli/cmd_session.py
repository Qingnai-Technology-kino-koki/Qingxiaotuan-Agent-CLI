"""`qxt session` —— 会话管理 (拆分自 cmd_services.py)。"""

from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
from typing import List

from ..config import home_dir
from ..i18n import t
from ._ui_singleton import console
from ..ui.format import Table


# ---- app 链惰性加载: 仅 resume 真正恢复会话时才构建内核 ----
def build_kernel(*a, **k):
    from ..app import build_kernel as _f
    return _f(*a, **k)


def create_agent(*a, **k):
    from ..app import create_agent as _f
    return _f(*a, **k)


def seed_builtin_skills(*a, **k):
    from ..app import seed_builtin_skills as _f
    return _f(*a, **k)


def _format_session_time(mtime: float, now: float) -> str:
    """会话时间: 近期显示相对时间, 更早显示绝对时间。"""
    delta = now - mtime
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} 天前"
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def _count_session_messages(path: Path) -> int:
    """统计会话中的消息条数。"""
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") in ("user", "assistant", "tool"):
                    n += 1
    except OSError:
        pass
    return n


def _resolve_session_target(files: List[Path], target: str) -> List[Path]:
    """把会话目标解析为文件列表。"""
    if target == "all":
        return list(files)
    if target.isdigit():
        idx = int(target) - 1
        return [files[idx]] if 0 <= idx < len(files) else []
    return [f for f in files if f.stem.startswith(target)]


def _print_tree(res, roots: List[str]) -> None:
    """用缩进打印会话分叉树 (以多个根开始, 每层缩进子分支)。"""
    by_id = {s["session_id"]: s for s in res.list_sessions()}

    def _label(sid):
        info = by_id.get(sid, {})
        title = (info.get("title") or "(无标题)")
        mark = "*" if info.get("forked") else ""
        tail = []
        if info.get("branch_point"):
            tail.append(f"@{info['branch_point']}")
        return f"{sid}{mark}  {title}" + ("  [分支点" + " ".join(tail) + "]" if tail else "")

    seen: set = set()

    def _walk(sid, depth):
        if sid in seen:
            print(f"{'  ' * depth}· {sid}  (环/重复, 已截断)")
            return
        seen.add(sid)
        prefix = "  " * depth
        print(f"{prefix}{'└─' if depth else '● '} {_label(sid)}")
        for child in by_id.values():
            if child.get("forked") and child.get("parent") == sid:
                _walk(child["session_id"], depth + 1)

    for rid in roots:
        _walk(rid, 0)


def cmd_session(args) -> int:
    session_cmd = getattr(args, "session_cmd", None)
    sessions_dir = home_dir() / "sessions"
    if not sessions_dir.exists():
        console.print("没有会话记录")
        return 0
    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)

    if session_cmd == "list":
        from ..memory.sessions import SessionStore
        limit = getattr(args, "limit", None)
        show_all = getattr(args, "all", False)
        sessions = files if show_all else files[:int(limit) if limit else 10]
        if not sessions:
            console.print("没有会话记录")
            return 0
        table = Table(title="历史会话")
        table.add_column("#", justify="right")
        table.add_column("时间", justify="left")
        table.add_column("标题", justify="left")
        table.add_column("消息", justify="right")
        table.add_column("大小", justify="right")
        table.add_column("会话ID", justify="left")
        now = time.time()
        for i, f in enumerate(sessions, 1):
            title = SessionStore.peek_title(f) or "(无标题)"
            size_kb = f.stat().st_size / 1024
            table.add_row(
                str(i),
                _format_session_time(f.stat().st_mtime, now),
                title,
                str(_count_session_messages(f)),
                f"{size_kb:.0f}KB" if size_kb >= 1 else f"{f.stat().st_size}B",
                f.stem,
            )
        console.print(table)
        console.print("使用 qxt session resume <编号|会话ID> 恢复会话")
        return 0

    if session_cmd == "resume":
        target = getattr(args, "target", None) or "latest"
        from .cmd_chat import _resume_session, _apply_mode_override, _run_chat_repl
        try:
            kernel = build_kernel()
        except Exception as exc:
            console.print(f"启动失败: {exc}")
            return 1
        config = kernel.require("config")
        workspace = getattr(args, "workspace", None) or os.getcwd()
        agent = create_agent(kernel, workspace)
        seed_builtin_skills(kernel)
        loaded = _resume_session(agent, target)
        if loaded is None:
            return 1
        console.print(f"{t('chat.resumed', path=loaded, count=len(agent.messages))}")
        mode = _apply_mode_override(kernel, getattr(args, "mode", None),
                                    yes=getattr(args, "yolo", False))
        effort = config.get("agent.effort", "high")
        return _run_chat_repl(agent, config, workspace, mode, effort)

    if session_cmd == "delete":
        target = getattr(args, "target", None) or "1"
        if target == "latest":
            target = "1"
        if not files:
            console.print("没有会话记录")
            return 0
        targets = _resolve_session_target(files, target)
        if not targets:
            console.print(f"未找到匹配的会话: {target}")
            return 1
        if not getattr(args, "yes", False):
            total_size = sum(f.stat().st_size for f in targets)
            names = ", ".join(f.stem for f in targets[:5])
            if len(targets) > 5:
                names += f" …(共 {len(targets)} 个)"
            try:
                ans = input(
                    f"  确认删除 {len(targets)} 个会话 ({names})? "
                    f"(共 {total_size / 1024:.0f}KB) [y/N] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans != "y":
                console.print("已取消")
                return 0
        for f in targets:
            try:
                f.unlink()
            except OSError as exc:
                console.print(f"删除失败 {f.name}: {exc}")
                return 1
        console.print(f"已删除 {len(targets)} 个会话")
        return 0

    if session_cmd == "export":
        target = getattr(args, "target", None) or "1"
        output = getattr(args, "output", None)
        targets = _resolve_session_target(files, target)
        if not targets:
            console.print(f"未找到匹配的会话: {target}")
            return 1
        src = targets[0]
        if output:
            import shutil
            dest = Path(output).expanduser()
            if dest.is_dir():
                dest = dest / src.name
            shutil.copy2(src, dest)
            console.print(f"已导出到: {dest}")
        else:
            # 输出到 stdout
            try:
                content = src.read_text(encoding="utf-8")
                print(content)
            except OSError as exc:
                console.print(f"读取失败: {exc}")
                return 1
        return 0

    if session_cmd == "clean":
        # 清理过期会话 (默认 30 天前)
        max_age_days = getattr(args, "older_than", 30)
        cutoff = time.time() - max_age_days * 86400
        old_files = [f for f in files if f.stat().st_mtime < cutoff]
        if not old_files:
            console.print(f"没有超过 {max_age_days} 天的会话")
            return 0
        total_size = sum(f.stat().st_size for f in old_files)
        if not getattr(args, "yes", False):
            try:
                ans = input(
                    f"  将清理 {len(old_files)} 个过期会话 "
                    f"(>{max_age_days}天, 共 {total_size / 1024:.0f}KB)? [y/N] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans != "y":
                console.print("已取消")
                return 0
        for f in old_files:
            try:
                f.unlink()
            except OSError:
                pass
        console.print(f"已清理 {len(old_files)} 个过期会话 (释放 {total_size / 1024:.0f}KB)")
        return 0

    if session_cmd == "stats":
        if not files:
            console.print("没有会话记录")
            return 0
        total_size = sum(f.stat().st_size for f in files)
        total_msgs = sum(_count_session_messages(f) for f in files)
        ages = [(time.time() - f.stat().st_mtime) / 86400 for f in files]
        oldest = max(ages) if ages else 0
        newest = min(ages) if ages else 0
        console.print(f"会话统计")
        console.print(f"  总数: {len(files)} 个会话")
        console.print(f"  总大小: {total_size / 1024:.0f}KB ({total_size / 1024 / 1024:.1f}MB)")
        console.print(f"  总消息: {total_msgs} 条")
        console.print(f"  最旧: {oldest:.0f} 天前")
        console.print(f"  最新: {newest:.0f} 天前")
        # 按月统计
        month_counts: dict[str, int] = {}
        for f in files:
            import datetime as _dt
            month = _dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m")
            month_counts[month] = month_counts.get(month, 0) + 1
        if month_counts:
            console.print(f"  月度分布:")
            for month in sorted(month_counts)[-6:]:
                bar = "#" * min(month_counts[month], 30)
                console.print(f"    {month}: {month_counts[month]:3d} {bar}")
        return 0

    # --- 跨会话 / 分叉 (迭代 3) ---
    if session_cmd in ("fork", "tree", "ref"):
        from ..core.cross_session import CrossSessionResolver
        from ..memory.sessions import SessionStore as _SS

        if session_cmd == "fork":
            target = getattr(args, "target", None) or "1"
            targets = _resolve_session_target(files, target)
            if not targets:
                console.print(f"未找到匹配的会话: {target}")
                return 1
            src = targets[0]
            store = _SS(sessions_dir)
            store.dir = sessions_dir
            store.file = src
            child = store.fork(at_message=getattr(args, "at_message", None),
                               note=getattr(args, "note", None) or "")
            b = child.branch_info()
            console.print(f"已分叉会话 {src.stem} -> {child.session_id}")
            console.print(f"  父会话: {b.get('parent')}  分支点: {b.get('branch_point') or '整份'}"
                          + (f"  备注: {b.get('note')}" if b.get("note") else ""))
            console.print("可在其它对话用 @session:<id> 引用这个分支")
            return 0

        res = CrossSessionResolver(home=home_dir())

        if session_cmd == "tree":
            root = getattr(args, "root", None)
            if root:
                targets = _resolve_session_target(files, root)
                if not targets:
                    console.print(f"未找到匹配的会话: {root}")
                    return 1
                root_id = targets[0].stem
            else:
                _sess_dir = res._sessions_dir()
                roots = [p.stem for p in _sess_dir.glob("*.jsonl")
                         if ((_SS.read_meta(p) or {}).get("kind") != "fork")]
                if not roots:
                    roots = [files[0].stem] if files else []
                _print_tree(res, roots)
                return 0
            _print_tree(res, [root_id])
            return 0

        if session_cmd == "ref":
            text = " ".join(getattr(args, "text", []))
            out = res.inject(text)
            if out == text:
                console.print("(文本中没有可解析的 @session / @# 引用)")
                return 0
            print(out)
            return 0

    console.print("用法: qxt session list|resume|delete|export|clean|stats|fork|tree|ref")
    return 0
