"""qxt gh —— GitHub 原生绑定 + 安全沙箱。

`qxt gh <任意 gh 参数>` 直接调用本机 `gh` CLI (原生输出/退出码/`--json`/环境透传),
执行前由安全策略引擎分级: 读/本地克隆放行, 远端变更受保护, 破坏性操作
(删除仓库/注销账户/任意 `-X DELETE`) 硬性拦截且不可绕过。

保留的青小团专属子命令:
  qxt gh borrow <任务>        借鉴相似开源项目实现 (带署名, 非全抄)
  qxt gh policy [show|guard|allow|verbose]   查看/微调安全策略 (受保护可加, 破坏性不可放宽)

`--rest` 仅在特定只读命令下做内置 REST 回退 (无需本机 gh)。
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

from ..gh.native import NativeGH, GHNotInstalledError, EXIT_FORBIDDEN, EXIT_NO_GH, _install_hint
from ..gh.policy import Action, classify, load_policy, write_policy, SafetyPolicy
from ..ui.plain_console import console


def _print(obj) -> None:
    if sys.stdout.isatty():
        console.print(obj)
    else:
        print(obj)


def _new_native(args, rest: bool = False) -> Optional[NativeGH]:
    pol = load_policy()
    gh = NativeGH(policy=pol)
    if rest and not gh.has_gh():
        return None  # 走 REST 回退
    return gh


# ---------------------------------------------------------------- 认证引导 (fx3)

_AUTH_FAIL_MARKERS = (
    "not logged in", "unauthorized", "authentication", "token", "401",
    "please run 'gh auth login'", "try running gh auth login", "bad credentials",
)


def _looks_like_auth_failure(err: str) -> bool:
    low = (err or "").lower()
    return any(m in low for m in _AUTH_FAIL_MARKERS)


def _auth_guidance() -> str:
    return (
        "当前 gh 未认证或认证已失效。刷新方式:\n"
        "  qxt gh auth login      (浏览器/粘贴 token 登录)\n"
        "  qxt gh auth status     查看当前认证账号\n"
        "  export GH_TOKEN=ghp_xxx  或 GITHUB_TOKEN=xxx (只读时也可)\n"
        "认证完成后重跑刚才的命令即可。"
    )


def _handle_audit(gh_args: List[str]) -> int:
    """qxt gh audit [N] [--clear] —— 查看/清空安全审计日志 (拦截/取消/放行记录)。"""
    from ..gh.audit import audit_log_path
    try:
        n = int(gh_args[1]) if len(gh_args) > 1 and gh_args[1].isdigit() else 20
    except ValueError:
        n = 20
    if "--clear" in gh_args:
        p = audit_log_path()
        removed = 0
        try:
            if p.exists():
                removed = sum(1 for _ in p.read_text(encoding="utf-8").splitlines() if _)
                p.unlink()
        except OSError as exc:
            console.print(f"[错误] 清空审计日志失败: {exc}")
            return 1
        console.print(f"已清空审计日志 ({removed} 条)")
        return 0
    p = audit_log_path()
    if not p.exists():
        console.print("尚无审计记录 (拦截/取消/受保护通过事件都会落盘). 也可执行 `qxt gh doctor` 查看日志路径.")
        return 0
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        console.print(f"[错误] 读取审计日志失败: {exc}")
        return 1
    if not lines:
        console.print("审计日志为空")
        return 0
    for row in lines[-max(1, n):]:
        try:
            rec = json.loads(row)
        except json.JSONDecodeError:
            console.print("  (无法解析的一条审计记录)")
            continue
        argv = " ".join(rec.get("argv", [])) or "(空)"
        console.print(f"  {rec.get('ts','?'):19}  {rec.get('event'):16} "
                      f"decision={rec.get('decision','?'):9}  gh {argv[:160]}")
    return 0


def _handle_doctor() -> int:
    """qxt gh doctor —— 本地体检: gh 安装 / 版本 / 认证 / token / 策略。"""
    gh = NativeGH(policy=load_policy())

    if not gh.has_gh():
        console.print("[bold red]✗ gh CLI 未安装[/bold red]")
        console.print(_install_hint())
        return EXIT_NO_GH

    console.print(f"[bold green]✓ gh CLI 已安装[/bold green]  {gh.version()}")
    ok = True

    # 认证状态
    rc, out, err = gh.run(["auth", "status"])
    if rc == 0:
        console.print("[bold green]✓ 已认证[/bold green]")
        token = (out or err)
        console.print("   " + " ".join(token.splitlines()) if token else "   (gh auth status 输出为空)")
    else:
        ok = False
        console.print("[bold red]✗ 未认证或认证失效[/bold red]")
        if _looks_like_auth_failure(err or out):
            console.print(_auth_guidance())
        else:
            console.print(err or out or "(gh auth status 无输出)")

    # token 环境变量 (只读回退的凭据)
    token_env = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token_env:
        console.print("[bold green]✓[/bold green] 发现 GH_TOKEN/GITHUB_TOKEN 环境变量 (仅读回退可用)")
    else:
        console.print("[dim]-[/dim] 未设置 GH_TOKEN/GITHUB_TOKEN 环境变量 (仅读回退将走匿名 60req/h)")

    # 审计日志位置
    from ..gh.audit import audit_log_path
    console.print(f"[dim]-[/dim] 审计日志: {audit_log_path()}  (拦截/取消均落盘)")

    return 0 if ok else 1


def _handle_auth_status(gh_args: List[str], args) -> int:
    """qxt gh auth-status [--refresh] —— 认证状态 + 可选刷新引导。原样透传 gh 输出。"""
    refresh = _pop_flag(gh_args, "refresh")
    rc, out, err = NativeGH(policy=load_policy()).run(gh_args or ["auth", "status"])
    if out:
        _print(out.rstrip("\n"))
    if err:
        sys.stderr.write(err.rstrip("\n") + "\n")
    if refresh or (rc != 0 and _looks_like_auth_failure(err + out)):
        console.print("[dim]" + _auth_guidance().replace("\n", "\n  ") + "[/dim]")
    return rc


# ---------------------------------------------------------------- 子命令处理

def _handle_borrow(gh_args: List[str]) -> int:
    """borrow <任务> [--mode code|repo] [--limit N] [--json]"""
    from ..tools import gh_borrow
    query = _pop_option(gh_args, "query") or " ".join(
        _pop_positional(gh_args))
    if not query:
        console.print("[错误] borrow 需提供借鉴任务/关键词")
        return 1
    mode = _pop_option(gh_args, "mode", default="code")
    limit = int(_pop_option(gh_args, "limit", default="5") or 5)
    want_json = _pop_flag(gh_args, "json")
    try:
        res = gh_borrow.borrow(query, mode=mode, limit=limit)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[错误] 借鉴失败: {exc}")
        return 1
    if want_json:
        _print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _print(gh_borrow.format_borrow(res))
    return 0


def _handle_policy(gh_args: List[str]) -> int:
    """policy [show|guard <词...>|allow <词...>|verbose on|off] 管理 (仅能收紧, 不能放宽破坏性)。"""
    pol = load_policy()
    sub = gh_args[1] if len(gh_args) > 1 else "show"
    if sub == "show":
        console.print("GitHub 安全策略（破坏性拦截为内置硬规则，不可放宽）:")
        console.print(f"  受保护命令编辑词: {', '.join(pol.guard_extra) or '(默认规则)'}")
        console.print(f"  额外放行词(stdin 仅读): {', '.join(pol.allow_extra) or '(无)'}")
        console.print(f"  受保护命令打印提示: {'开' if pol.verbose_guarded else '关'}")
        console.print("  内置禁止(不可改): repo/gist/release/... delete · 任意 -X DELETE")
        return 0
    if sub == "guard" and len(gh_args) > 2:
        pol.guard_extra += [w for w in gh_args[2:] if w not in pol.guard_extra and w not in pol.allow_extra]
        write_policy(pol)
        console.print(f"已把以下词加入受保护(非破坏): {gh_args[2:]}")
        return 0
    if sub == "allow" and len(gh_args) > 2:
        pol.allow_extra += [w for w in gh_args[2:] if w not in pol.allow_extra]
        write_policy(pol)
        console.print("提示: allow 仅对非破坏操作生效; 破坏性判定为硬编码, 不受影响。")
        return 0
    if sub == "verbose" and len(gh_args) > 2 and gh_args[2] in ("on", "off"):
        pol.verbose_guarded = gh_args[2] == "on"
        write_policy(pol)
        console.print(f"受保护命令影响提示已{'开启' if pol.verbose_guarded else '关闭'}")
        return 0
    console.print("用法: qxt gh policy [show|guard <词...>|allow <词...>|verbose on|off]")
    return 1


def _run_native(gh_args: List[str], args) -> int:
    """原生 gh 透传 (含安全闸门)。"""
    gh = NativeGH(policy=load_policy())
    try:
        rc, out, err = gh.run(gh_args)
    except GHNotInstalledError as exc:
        console.print(str(exc))
        return EXIT_NO_GH
    if out:
        _print(out.rstrip("\n"))
    if err:
        sys.stderr.write(err.rstrip("\n") + "\n")
    if _looks_like_auth_failure(err + out):
        # 认证失败 → 追加刷新引导, 不吞 gh 的原生报错与退出码。
        sys.stderr.write("\n[提示] " + _auth_guidance().replace("\n", "\n  ") + "\n")
    return rc


def _run_rest(gh_args: List[str], args) -> int:
    """无本机 gh 时的内置 REST 回退 (仅支持少量只读能力)。"""
    from ..tools import gh_borrow
    client = gh_borrow.GitHubClient()
    head = gh_args[0] if gh_args else ""
    try:
        if head == "api" and len(gh_args) >= 2:
            _print(json.dumps(client._gh_api(gh_args[1]), ensure_ascii=False, indent=2))
            return 0
        if head == "repo" and len(gh_args) >= 3 and gh_args[1] == "view":
            data = client._gh_api(f"repos/{gh_args[2].strip('/')}")
            _print(f"{data.get('full_name')}  ⭐{data.get('stargazers_count')}  "
                   f"fork:{data.get('forks_count')}")
            _print(data.get("description") or "")
            return 0
        if head == "search" and len(gh_args) >= 3 and gh_args[1] in ("repos", "code"):
            limit = 10
            if "--limit" in gh_args:
                i = gh_args.index("--limit")
                if i + 1 < len(gh_args):
                    limit = int(gh_args[i + 1])
            q = gh_args[2]
            rows = (client.search_repos(q, limit) if gh_args[1] == "repos"
                    else client.search_code(q, limit))
            for it in rows:
                _print(it.get("full_name") or it.get("path") or "")
            return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[错误] REST 回退失败: {exc}")
        return 1
    console.print("REST 回退仅支持: repo view / search repos|code / api <path>。装 `gh` 可获完整能力。")
    return 1


# ---------------------------------------------------------------- 入口

def cmd_gh(args) -> int:
    gh_args: List[str] = list(getattr(args, "gh_args", None) or [])
    rest = bool(getattr(args, "rest", False))

    if not gh_args or gh_args[0] in ("help", "--help", "-h"):
        _usage()
        return 0

    head = gh_args[0]
    if head == "borrow":
        return _handle_borrow(gh_args)
    if head == "policy":
        return _handle_policy(gh_args)
    if head == "audit":
        return _handle_audit(gh_args)
    if head == "doctor":
        return _handle_doctor()
    if head in ("auth-status", "auth_status"):
        return _handle_auth_status(gh_args[1:], args)
    if head in ("version", "--version"):
        _print(NativeGH(policy=load_policy()).version())
        return 0

    # 原生透传 (首选): 安全闸门 + 完整 gh 能力 + 原生体验。
    if not rest and NativeGH(policy=load_policy()).has_gh():
        return _run_native(gh_args, args)
    # 无 gh 或显式 --rest: 只读回退。
    return _run_rest(gh_args, args)


# ---------------------------------------------------------------- 解析辅助

def _pop_flag(argv: List[str], key: str) -> bool:
    """若 argv 含 ``--key`` / ``--key=1`` / ``-key`` 则移除并返回 True。"""
    removed = False
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in (f"--{key}", f"-{key}"):
            del argv[i]
            removed = True
            continue
        if tok.startswith(f"--{key}="):
            del argv[i]
            removed = True
            continue
        i += 1
    return removed


def _pop_option(argv: List[str], key: str, *, default: Optional[str] = None) -> Optional[str]:
    """弹出 ``--key VALUE`` / ``--key=VALUE``, 返回 VALUE; 无则 default。"""
    out: Optional[str] = default
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == f"--{key}" and i + 1 < len(argv):
            out = argv[i + 1]
            del argv[i:i + 2]
            continue
        if tok.startswith(f"--{key}="):
            out = tok.split("=", 1)[1]
            del argv[i]
            continue
        i += 1
    return out


def _pop_positional(argv: List[str]) -> List[str]:
    """收集并移除剩余非旗标的位置参数。"""
    pos: List[str] = []
    rest: List[str] = []
    for t in argv:
        if t.startswith("-"):
            rest.append(t)
        else:
            pos.append(t)
    argv[:] = rest
    return pos


def _usage() -> None:
    _print(
        "用法: qxt gh [--rest] <gh 命令与参数...>\n"
        "\n原生透传 (调用本机 gh CLI, 同样输出/退出码/--json, 自动安全分级):\n"
        "  qxt gh repo view OWNER/REPO\n"
        "  qxt gh repo view OWNER/REPO --web\n"
        "  qxt gh issue list --repo OWNER/REPO --json number,title\n"
        "  qxt gh pr list --repo OWNER/REPO --state merged\n"
        "  qxt gh search repos \"lang:python stars:>1000\"\n"
        "  qxt gh repo clone OWNER/REPO\n"
        "  qxt gh api repos/OWNER/REPO\n"
        "\n青小团专属:\n"
        "  qxt gh doctor          本地体检: gh 安装 / 认证 / token / 审计日志\n"
        "  qxt gh auth-status     查看 gh 认证状态 (失败自动附刷新引导)\n"
        "  qxt gh audit [N] [--clear]   查看最近 N 条安全审计日志 (拦截/取消/放行)\n"
        "  qxt gh borrow <任务> [--mode code|repo] [--limit N] [--json]\n"
        "  qxt gh policy [show|guard <词...>|allow <词...>|verbose on|off]\n"
        "\n安全沙箱 (不可绕过): 删除仓库/注销账户/任意 `-X DELETE` 一律硬性拦截。\n"
        "只读命令可加 `--rest` 走内置 REST 回退 (无需本机 gh)。"
    )


def run_borrow_for_slash(query: str, mode: str = "code", limit: int = 5) -> str:
    """供斜杠命令 /gh-borrow 复用: 返回渲染好的借鉴结果文本。"""
    from ..tools import gh_borrow
    try:
        res = gh_borrow.borrow(query, mode=mode, limit=limit)
        return gh_borrow.format_borrow(res)
    except Exception as exc:  # noqa: BLE001
        return f"[错误] 借鉴失败: {exc}"