"""qxt 命令行参数解析与入口。

最简用法 (开箱即用):
    qxt              直接打开交互界面 (默认进 chat)
    qxt --yolo       无限制模式: 危险操作自动批准
    qxt model        交互式挑模型供应商 (开箱支持 12 家)

子命令一览:
    qxt chat                  交互式对话 (默认, qxt 不带子命令即等价)
    qxt dev "任务"            进入自主开发循环 (分析→实现→自测→核实→汇报, 直到你满意)
    qxt run "任务"            headless 一次性任务, 跑完退出 (适合脚本/CI)
    qxt agent "任务"          后台自主任务 (终端不阻塞)
    qxt setup                 初始化向导 (API Key 等)
    qxt doctor                环境健康检查
    qxt model                 配置/热切换模型供应商 (12 家开箱即用)
    qxt config get/set/dump   配置管理
    qxt plugin list           查看微内核插件与服务
    qxt skill list/show       技能管理
    qxt memory list/search    记忆管理
    qxt cron add/list/remove/tick  定时任务
    qxt ext engines/call/selftest/info  外部能力引擎 (C / TypeScript) 调试
    qxt improve summarize/apply      自我改进闭环 (从执行历史提炼规则与技能草稿)
"""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from .commands import (
    cmd_agent, cmd_bench, cmd_bg, cmd_chat, cmd_config, cmd_cron, cmd_dev, cmd_doctor, cmd_mcp,
    cmd_memory, cmd_mode, cmd_model, cmd_plugin, cmd_run, cmd_session, cmd_setup, cmd_skill,
)
from .ext_cli import cmd_ext
from .improve_cli import cmd_improve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qxt", description="青小团 CLI - 会成长的插件化 Agent")
    parser.add_argument("--version", action="version", version=f"qxt {__version__}")
    parser.add_argument("--profile", default="default", help="使用指定 profile (~/.qingxiaotuan/profiles/<name>)")
    parser.add_argument("--patch", help="一次性配置覆盖文件 (dsh 风格, 整值替换)")
    parser.add_argument("--workspace", help="工作区目录 (默认当前目录)")
    parser.add_argument("--model", help="临时覆盖模型名 (如 deepseek-v4-flash-free)")
    parser.add_argument("--effort", choices=["low", "medium", "high"], help="推理投入级别 (low/medium/high)")
    parser.add_argument("--mode", choices=["standard", "yolo"], help="运行模式: standard(默认,需确认) / yolo(自动批准)")
    parser.add_argument("--yolo", action="store_true", help="无限制模式: 危险操作自动批准 (等同于 --mode yolo)")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("chat", help="交互式对话")
    p.add_argument("--resume", help="恢复指定会话文件 (.jsonl)")
    p.add_argument("--tui", action="store_true", help="启动全屏 TUI 工作台")
    p.add_argument("--model", help="临时覆盖模型名 (如 deepseek-v4-flash-free)")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="推理投入级别 (low/medium/high)")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("dev", help="自主开发循环 (分析→实现→自测→核实→汇报, 直到你满意)")
    p.add_argument("task", help="开发任务描述")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="推理投入级别 (low/medium/high)")
    p.set_defaults(func=cmd_dev)

    p = sub.add_parser("run", help="headless 一次性任务")
    p.add_argument("task", help="任务描述")
    p.add_argument("--yes", "-y", action="store_true", help="自动批准危险操作 (等同于 --mode yolo)")
    p.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    p.add_argument("--bg", action="store_true", help="后台运行, 不阻塞终端 (用 qxt bg 查看进度)")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="推理投入级别 (low/medium/high)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("agent", help="后台自主任务 (终端不阻塞, 青小团自己在后台干活)")
    p.add_argument("task", help="任务描述")
    p.add_argument("--yes", "-y", action="store_true", help="自动批准危险操作 (等同于 --mode yolo)")
    p.add_argument("--wait", action="store_true", help="提交后阻塞等待任务结束 (等价于 run --bg + 等待)")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("bg", help="后台任务管理 (list / logs / cancel / wait)")
    bgsub = p.add_subparsers(dest="bg_cmd", required=True)
    bgsub.add_parser("list", help="列出后台任务")
    lg = bgsub.add_parser("logs", help="查看某后台任务的进展")
    lg.add_argument("job_id", help="后台任务 ID")
    lg.add_argument("--tail", type=int, default=15, help="显示最近 N 条")
    c = bgsub.add_parser("cancel", help="取消正在运行的后台任务")
    c.add_argument("job_id")
    w = bgsub.add_parser("wait", help="阻塞等待某后台任务结束")
    w.add_argument("job_id")
    w.add_argument("--timeout", type=float, default=0, help="最长等待秒数 (0=不限)")
    p.set_defaults(func=cmd_bg)

    p = sub.add_parser("bench", help="基准测试 (缓存命中率等)")
    bsub = p.add_subparsers(dest="bench_cmd", required=True)
    bc = bsub.add_parser("cache", help="用固定长对话脚本实测 prompt cache 命中率")
    bc.add_argument("--rounds", type=int, default=8, help="对话轮数 (默认 8)")
    bc.add_argument("--task", default="解释一下当前工作区的结构", help="首轮任务描述")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("setup", help="初始化向导 (Hermes 风格: 快速/完整/空白)")
    p.add_argument("--quick", action="store_true", help="跳过模式选择, 直接进入快速设置")
    p.set_defaults(func=cmd_setup)
    sub.add_parser("doctor", help="环境健康检查").set_defaults(func=cmd_doctor)

    p = sub.add_parser("mode", help="查看/切换默认运行模式 (standard/yolo)")
    p.add_argument("value", nargs="?", choices=["standard", "yolo"], help="可选: 指定则切换默认模式")
    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("model", help="配置/热切换模型供应商 (开箱支持48家)")
    p.set_defaults(func=cmd_model)
    msub = p.add_subparsers(dest="model_cmd", required=False)
    msub.add_parser("current", help="显示当前生效的模型配置").set_defaults(model_cmd="current")
    info = msub.add_parser("info", help="查看某供应商详细信息 (文档/模型/定价)")
    info.add_argument("provider_name", help="供应商名称 (如 deepseek / ollama)")
    info.set_defaults(model_cmd="info")
    ms = msub.add_parser("set", help="切换模型供应商并写入用户配置 (下次启动生效)")
    ms.add_argument("provider", help="供应商名: deepseek / openai / openai-compatible / 自定义网关名")
    ms.add_argument("model", help="模型名, 如 deepseek-chat / claude-3-5-sonnet")
    ms.add_argument("base_url", nargs="?", default=None, help="可选: 网关 base_url (自定义/兼容网关必填)")
    ms.add_argument("api_key_env", nargs="?", default=None, help="可选: 密钥环境变量名 (默认沿用当前)")
    ms.set_defaults(model_cmd="set")
    msub.add_parser("list-providers", help="列出内置已知供应商与自定义网关提示").set_defaults(model_cmd="list-providers")
    sw = msub.add_parser("switch", help="运行时热切换 (当前会话立即换脑子, 不写盘)")
    sw.add_argument("provider", help="供应商名: deepseek / openai / openai-compatible / 自定义网关名")
    sw.add_argument("model", help="模型名, 如 deepseek-chat / claude-3-5-sonnet")
    sw.add_argument("base_url", nargs="?", default=None, help="可选: 网关 base_url (自定义/兼容网关必填)")
    sw.add_argument("api_key_env", nargs="?", default=None, help="可选: 密钥环境变量名 (默认沿用当前)")
    sw.set_defaults(model_cmd="switch")

    p = sub.add_parser("config", help="配置管理")
    csub = p.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("dump", help="打印叠加后的最终配置")
    csub.add_parser("dump-default", help="打印内置默认配置")
    csub.add_parser("profiles", help="列出可用预设 profile")
    csub.add_parser("validate", help="校验当前配置 (类型/范围/枚举/密钥)")
    g = csub.add_parser("get", help="读取配置项")
    g.add_argument("key")
    s = csub.add_parser("set", help="写入用户层配置")
    s.add_argument("key")
    s.add_argument("value")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("plugin", help="插件管理")
    psub = p.add_subparsers(dest="plugin_cmd", required=True)
    psub.add_parser("list", help="列出全部插件与服务")
    p.set_defaults(func=cmd_plugin)

    p = sub.add_parser("skill", help="技能管理")
    ssub = p.add_subparsers(dest="skill_cmd", required=True)
    ssub.add_parser("list", help="列出全部技能")
    sh = ssub.add_parser("show", help="查看技能详情")
    sh.add_argument("name")
    p.set_defaults(func=cmd_skill)

    p = sub.add_parser("memory", help="记忆管理")
    msub = p.add_subparsers(dest="memory_cmd", required=True)
    msub.add_parser("list", help="查看长期记忆")
    se = msub.add_parser("search", help="全文检索记忆")
    se.add_argument("query")
    p.set_defaults(func=cmd_memory)

    p = sub.add_parser("cron", help="定时任务")
    crsub = p.add_subparsers(dest="cron_cmd", required=True)
    a = crsub.add_parser("add", help="添加定时任务")
    a.add_argument("name")
    a.add_argument("prompt")
    a.add_argument("--interval", type=int, default=60, help="间隔分钟数")
    crsub.add_parser("list", help="列出任务")
    r = crsub.add_parser("remove", help="删除任务")
    r.add_argument("id")
    crsub.add_parser("tick", help="执行所有到期任务 (一次) — 也供系统计划任务/守护调用")
    st = crsub.add_parser("start", help="启动常驻调度守护 (自动执行到期任务, 无需系统计划任务)")
    st.add_argument("--detach", action="store_true", help="后台分离运行 (写 PID 文件, 返回终端)")
    st.add_argument("--check", type=int, default=60, help="守护检查间隔秒数 (默认 60)")
    p.set_defaults(func=cmd_cron)

    p = sub.add_parser("mcp", help="MCP 协议: 接入外部 MCP Server 工具")
    psub = p.add_subparsers(dest="mcp_cmd", required=True)
    psub.add_parser("list", help="列出已配置的 MCP server 与桥接工具")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("ext", help="外部能力引擎 (C / TypeScript) 调试与自检")
    exsub = p.add_subparsers(dest="ext_cmd", required=True)
    exsub.add_parser("engines", help="列出环境中真正可用的引擎").set_defaults(func=cmd_ext)
    ec = exsub.add_parser("call", help="调用某引擎的某方法 (参数用 JSON)")
    ec.add_argument("engine", help="引擎名, 如 crypto / rules / search")
    ec.add_argument("method", help="方法名, 如 selftest / load / search")
    ec.add_argument("params", nargs="?", default="{}", help="JSON 对象参数 (默认 {})")
    ec.add_argument("--timeout", type=float, default=60.0, help="请求超时秒数")
    ec.set_defaults(func=cmd_ext)
    est = exsub.add_parser("selftest", help="逐个启动引擎跑 list/ping, 报告健康度")
    est.add_argument("engines", nargs="*", help="可选: 只测指定的引擎名")
    est.add_argument("--timeout", type=float, default=15.0, help="单引擎超时秒数")
    est.set_defaults(func=cmd_ext)
    ei = exsub.add_parser("info", help="显示某引擎的元信息 (方法/版本)")
    ei.add_argument("engine", help="引擎名")
    ei.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    ei.set_defaults(func=cmd_ext)

    p = sub.add_parser("improve", help="自我改进闭环: 从执行历史提炼规则与技能草稿")
    impsub = p.add_subparsers(dest="improve_cmd", required=True)
    impsub.add_parser("summarize", help="复盘执行历史, 预览将生成的经验与规则 (dry-run)").set_defaults(func=cmd_improve)
    impsub.add_parser("apply", help="把经验固化为 rules .jsonl + 技能草稿并落盘").set_defaults(func=cmd_improve)

    p = sub.add_parser("session", help="会话管理 (列出/恢复历史会话)")
    sesub = p.add_subparsers(dest="session_cmd", required=True)
    sesub.add_parser("list", help="列出最近的会话")
    r = sesub.add_parser("resume", help="恢复会话 (默认最近一个)")
    r.add_argument("target", nargs="?", help="会话序号或文件名 (省略则恢复最近)")
    p.set_defaults(func=cmd_session)
    r.add_argument("target", nargs="?", help="会话序号或文件名 (省略则恢复最近)")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    # 交互式子命令需要真实终端 (TTY)。若在管道/重定向/IDE 内嵌终端等非交互环境里
    # 直接跑 qxt, stdin 不是 TTY, REPL 会静默卡在等输入 (表现为"没有输入框")。
    # 这里提前给出明确指引, 而不是让用户干等。
    _interactive_cmds = {None, "chat", "dev"}
    if getattr(args, "cmd", None) in _interactive_cmds and not sys.stdin.isatty():
        sys.stderr.write(
            "\n[青小团] 交互模式需要真实终端 (TTY)。\n"
            "  当前 stdin 不是终端, 无法显示输入框。\n"
            "  请在系统自带的终端里运行 (不要在本软件/IDE 的内嵌命令行里跑):\n"
            "    - Windows: 打开『命令提示符』或『Windows Terminal』, 输入 qxt\n"
            "    - 或在该终端里先 cd 到项目目录, 再 .venv\\Scripts\\Activate.ps1 后 qxt\n"
            "  非交互任务可用: qxt run \"你的任务\" (headless, 跑完退出)\n\n"
        )
        return 2
    # 无子命令: 默认进入交互式对话 (qxt 直接开界面)。
    # 顶层 flag (--yolo / --model / --effort) 已在 args 上, 直接交给 cmd_chat 即可。
    if not getattr(args, "cmd", None):
        if getattr(args, "yolo", False) and not getattr(args, "mode", None):
            args.mode = "yolo"
        return cmd_chat(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
