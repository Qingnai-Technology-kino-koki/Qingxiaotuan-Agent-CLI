"""自定义斜杠命令 (<QXT_HOME>/commands、<workspace>/.qxt/commands、<workspace>/.claude/commands)。

对标 Claude Code 的自定义 slash commands: 用户放一个 Markdown 文件即获得一条
/文件名 命令, 无需改 CLI 代码。frontmatter 支持 description / argument-hint;
正文是提示词模板, 支持占位符:
- $ARGUMENTS  → 整段参数原文
- $1..$9      → 按空白切分的第 N 个参数
- @<path>     → 工作区内文件的当前内容 (注入上下文, 单文件截断保护)
- !`command`  → 预执行 shell 命令, 用其 stdout 替换 (超时保护)

展开顺序固定为 @file → !`cmd` → $参数: 先解析模板级引用, 再执行动态命令,
且命令输出不再做二次展开 (防止命令结果被当作模板语法注入)。

安装 (install_user_commands): 把分发钩子包装进 cli.commands._handle_slash ——
commands.py 是禁改稳定面, 故用运行期包装注入; 命令表存模块级 _TABLE,
重复安装只刷新表格不叠加包装 (幂等)。命中用户命令则展开模板并作为一轮
新对话输入执行; 未命中回落原处理链。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# 与 SkillManager 同款 frontmatter 约定
_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_MAX_FILE_INJECT = 50_000   # @file 注入的单文件字符上限 (防上下文被撑爆)
_SHELL_TIMEOUT = 15.0       # !`cmd` 预执行超时 (秒)


@dataclass(frozen=True)
class UserCommand:
    """一条用户自定义斜杠命令。"""

    name: str           # 不带斜杠的命令名 (= 文件名去 .md)
    description: str
    argument_hint: str
    body: str           # 提示词模板正文
    path: Path


def command_dirs(config: Any, workspace: str = "") -> List[Path]:
    """指令来源: 用户级 <home>/commands → 项目级 .qxt/commands → 项目级 .claude/commands。

    末尾目录优先级最高 (加载时表[名称]被后续覆盖) —— Claude Code 的标准位置
    <workspace>/.claude/commands 放在最末, 使从 Claude Code 迁移的用户命令
    开箱即用且不违法既有 .qxt 语义。
    """
    dirs: List[Path] = []
    home = getattr(config, "home", None)
    if home is None:
        from ..config.loader import home_dir
        home = home_dir()
    dirs.append(Path(home) / "commands")
    if workspace:
        dirs.append(Path(workspace) / ".qxt" / "commands")
        dirs.append(Path(workspace) / ".claude" / "commands")
    return dirs


def parse_command(path: Path) -> Optional[UserCommand]:
    """解析单个 commands/*.md; name 取 frontmatter 或回落文件名; 失败返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta: Dict[str, str] = {}
    body = text
    m = _FRONT_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[m.end():]
    name = meta.get("name", "").strip() or path.stem
    if not re.fullmatch(r"[a-zA-Z0-9_\-]+", name):
        return None  # 非法命令名 (会破坏 /name 匹配), 跳过
    return UserCommand(
        name=name,
        description=meta.get("description", "").strip() or f"自定义命令 /{name}",
        argument_hint=meta.get("argument-hint", "").strip(),
        body=body.strip(),
        path=path,
    )


def load_user_commands(config: Any, workspace: str = "") -> Dict[str, UserCommand]:
    """扫描两级目录构建命令表; 项目级覆盖同名用户级 (就近优先)。"""
    table: Dict[str, UserCommand] = {}
    for d in command_dirs(config, workspace):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            uc = parse_command(p)
            if uc is not None:
                table[uc.name] = uc
    return table


def expand_command(uc: UserCommand, arg: str, workspace: str = "",
                   extra_env: Optional[Dict[str, str]] = None) -> str:
    """把模板展开成最终提示词 (@file → !`cmd` → $ARGUMENTS/$N)。"""
    text = uc.body

    # 1) @<path>: 注入工作区文件内容 (相对路径; 不存在则原样保留)
    def _read_file(m: "re.Match[str]") -> str:
        rel = m.group(1).strip()
        p = Path(rel)
        if not p.is_absolute():
            p = Path(workspace) / rel if workspace else p
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            return m.group(0)
        if len(content) > _MAX_FILE_INJECT:
            content = content[:_MAX_FILE_INJECT] + f"\n…[截断, 原文 {len(content)} 字符]"
        return f"<file path=\"{rel}\">\n{content}\n</file>"

    text = re.sub(r"@([^\s`]+)", _read_file, text)

    # 2) !`cmd`: 预执行并嵌入 stdout (失败/超时以占位说明呈现, 不中断)
    def _run_shell(m: "re.Match[str]") -> str:
        cmd = m.group(1).strip()
        if not cmd:
            return ""
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                cwd=workspace or None, timeout=_SHELL_TIMEOUT,
                env={**os.environ, **(extra_env or {})},
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            if out:
                return out
            if err:
                return f"(stderr) {err}"
            return "(无输出)"
        except subprocess.TimeoutExpired:
            return f"(命令超时 {_SHELL_TIMEOUT:.0f}s)"
        except Exception as exc:  # noqa: BLE001
            return f"(命令执行失败: {exc})"

    text = re.sub(r"!`([^`]*)`", _run_shell, text)

    # 3) 参数替换 ($N 先于 $ARGUMENTS, 防止整段参数里出现 "$1" 字样被二次替换)
    parts = arg.split()
    for i in range(9, 0, -1):
        text = text.replace(f"${i}", parts[i - 1] if len(parts) >= i else "")
    text = text.replace("$ARGUMENTS", arg.strip())
    return text.strip()


# ------------------------------------------------------------------ 安装进分发链

_TABLE: Dict[str, UserCommand] = {}


def install_user_commands(agent: Any, config: Any, workspace: str = "") -> int:
    """加载命令表并确保 _handle_slash 已被包装 (幂等)。返回装上的命令数。"""
    global _TABLE
    _TABLE = load_user_commands(config, workspace)

    import qingxiaotuan.cli.commands as _cmds

    orig = getattr(_cmds._handle_slash, "_qxt_orig", None) or _cmds._handle_slash
    if not getattr(_cmds._handle_slash, "_qxt_wrapped", False):
        def _patched(cmd_text: str, agent_: Any, config_: Any, workspace_: str) -> bool:
            parts = cmd_text.strip().split(None, 1)
            head = parts[0].lower() if parts else ""
            arg = parts[1] if len(parts) > 1 else ""
            uc = _TABLE.get(head[1:]) if head.startswith("/") else None
            if uc is None:
                return bool(orig(cmd_text, agent_, config_, workspace_))
            expanded = expand_command(uc, arg, workspace_)
            # 复用主循环的 UI 实例与回合执行器: REPL 下与普通输入完全同路径;
            # TUI 下直接写 console (alternate screen 显示可能简陋, 但功能完整)。
            _cmds.ui.info(f"/{uc.name} (自定义命令) 已展开, 开始执行…")
            _cmds._run_turn(agent_, expanded, config_)
            return True

        _patched._qxt_orig = orig          # type: ignore[attr-defined]
        _patched._qxt_wrapped = True       # type: ignore[attr-defined]
        _cmds._handle_slash = _patched

    _sync_ui_lists(_TABLE)
    return len(_TABLE)


def _sync_ui_lists(table: Dict[str, UserCommand]) -> None:
    """把用户命令追加进输入栏补全与 /help 帮助文本 (各一次性, 幂等)。"""
    names = [f"/{uc.name}" for uc in sorted(table.values(), key=lambda c: c.name)]
    if not names:
        return
    try:
        from ..ui import repl as _repl
        new = [n for n in names if n not in _repl._SLASH_COMMANDS]
        _repl._SLASH_COMMANDS.extend(new)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..ui import fullscreen as _fs
        new = [n for n in names if n not in _fs._SLASH_COMMANDS]
        _fs._SLASH_COMMANDS.extend(new)
    except Exception:  # noqa: BLE001
        pass
    # /help 追加段落 (只在首次安装时拼一次, 用标记防重复)
    import qingxiaotuan.cli.commands as _cmds
    marker = "\n\n## 自定义斜杠命令"
    if marker not in _cmds._HELP:
        lines = ["", "", "自定义斜杠命令 (<QXT_HOME>/commands、<工作区>/.qxt/commands、<工作区>/.claude/commands 的 *.md):"]
        for n in names:
            uc = table[n[1:]]
            hint = f" [{uc.argument_hint}]" if uc.argument_hint else ""
            lines.append(f"  {n}{hint}  {uc.description}")
        _cmds._HELP += marker + "\n".join(lines)
