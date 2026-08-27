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
    qxt cron add/list/remove/enable/disable/edit/run/logs/tick/start/stop/status  定时任务 (含常驻守护)
    qxt open <file>[:<line>]  精确引用跳转 (在编辑器中打开指定行)
    qxt ext engines/call/selftest/info  外部能力引擎 (纯 Python) 调试
    qxt improve summarize/apply      自我改进闭环 (从执行历史提炼规则与技能草稿)
    qxt session list/resume/delete   会话管理 (列出/恢复/删除)
    qxt bench cache/latency          基准测试 (缓存命中率 / 响应延迟)
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

from .. import __version__


def _resolve_func(name: str):
    """延迟导入命令函数: 轻量命令 (--version/--help) 不加载 app 链。"""
    if name == "cmd_ext":
        mod = importlib.import_module(".ext_cli", __package__)
    elif name == "cmd_improve":
        mod = importlib.import_module(".improve_cli", __package__)
    else:
        mod = importlib.import_module(".commands", __package__)
    return getattr(mod, name)


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
    parser.add_argument("--print", action="store_true", dest="print_mode",
                        help="非交互输出模式: 跑完任务直接输出结果到 stdout, 不进 REPL (可管道化)")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="详细输出级别: -v=工具调用与计时, -vv=系统提示与完整请求, -vvv=原始 API 与完整 token")
    parser.add_argument("--max-cost", type=float, default=0.0,
                        help="单次会话最大花费上限 (USD), 超过自动停止; 0=不限制")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("chat", help="交互式对话")
    p.add_argument("--resume", help="恢复指定会话文件 (.jsonl)")
    p.add_argument("--tui", action="store_true", help="启动全屏 TUI 工作台")
    p.add_argument("--model", help="临时覆盖模型名 (如 deepseek-v4-flash-free)")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="推理投入级别 (low/medium/high)")
    p.set_defaults(func="cmd_chat")

    p = sub.add_parser("dev", help="自主开发循环 (分析→实现→自测→核实→汇报, 直到你满意)")
    p.add_argument("task", help="开发任务描述")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="推理投入级别 (low/medium/high)")
    p.set_defaults(func="cmd_dev")

    p = sub.add_parser("run", help="headless 一次性任务")
    p.add_argument("task", help="任务描述")
    p.add_argument("--yes", "-y", action="store_true", help="自动批准危险操作 (等同于 --mode yolo)")
    p.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    p.add_argument("--bg", action="store_true", help="后台运行, 不阻塞终端 (用 qxt bg 查看进度)")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="推理投入级别 (low/medium/high)")
    p.add_argument("--max-cost", type=float, default=0.0,
                    help="最大花费上限 (USD), 超过自动停止; 0=不限制")
    p.set_defaults(func="cmd_run")

    p = sub.add_parser("agent", help="后台自主任务 (终端不阻塞, 青小团自己在后台干活)")
    p.add_argument("task", help="任务描述")
    p.add_argument("--yes", "-y", action="store_true", help="自动批准危险操作 (等同于 --mode yolo)")
    p.add_argument("--wait", action="store_true", help="提交后阻塞等待任务结束 (等价于 run --bg + 等待)")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.set_defaults(func="cmd_agent")

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
    p.set_defaults(func="cmd_bg")

    p = sub.add_parser("bench", help="基准测试 (缓存命中率 / 延迟)")
    bsub = p.add_subparsers(dest="bench_cmd", required=True)
    bc = bsub.add_parser("cache", help="用固定长对话脚本实测 prompt cache 命中率")
    bc.add_argument("--rounds", type=int, default=8, help="对话轮数 (默认 8)")
    bc.add_argument("--task", default="解释一下当前工作区的结构", help="首轮任务描述")
    bl = bsub.add_parser("latency", help="多轮实测模型响应延迟与吞吐")
    bl.add_argument("--rounds", type=int, default=5, help="请求轮数 (默认 5)")
    bl.add_argument("--task", default="ping", help="请求内容 (默认 ping)")
    p.set_defaults(func="cmd_bench")

    p = sub.add_parser("setup", help="初始化向导 (Hermes 风格: 快速/完整/空白)")
    p.add_argument("--quick", action="store_true", help="跳过模式选择, 直接进入快速设置")
    p.set_defaults(func="cmd_setup")
    sub.add_parser("doctor", help="环境健康检查").set_defaults(func="cmd_doctor")

    p = sub.add_parser("mode", help="查看/切换默认运行模式 (standard/yolo)")
    p.add_argument("value", nargs="?", choices=["standard", "yolo"], help="可选: 指定则切换默认模式")
    p.set_defaults(func="cmd_mode")

    p = sub.add_parser("model", help="配置/热切换模型供应商 (开箱支持48家)")
    p.set_defaults(func="cmd_model")
    msub = p.add_subparsers(dest="model_cmd", required=False)
    msub.add_parser("current", help="显示当前生效的模型配置").set_defaults(model_cmd="current")
    info = msub.add_parser("info", help="查看某供应商详细信息 (文档/模型/定价)")
    info.add_argument("provider_name", help="供应商名称 (如 deepseek / ollama)")
    info.set_defaults(model_cmd="info")
    ml = msub.add_parser("list", help="列出某供应商当前可选模型清单")
    ml.add_argument("provider_name", help="供应商名称 (如 openrouter / deepseek)")
    ml.set_defaults(model_cmd="list")
    mt = msub.add_parser("test", help="连通性测试: 向当前端点发一条最小请求验证 base_url/Key/模型名")
    mt.add_argument("--provider", help="临时覆盖供应商 (不写盘)")
    mt.add_argument("--model", help="临时覆盖模型名 (不写盘)")
    mt.add_argument("--base-url", dest="base_url", help="临时覆盖 base_url (不写盘)")
    mt.add_argument("--api-key-env", dest="api_key_env", help="临时覆盖密钥环境变量名 (不写盘)")
    mt.set_defaults(model_cmd="test")
    ms = msub.add_parser("set", help="切换模型供应商并写入用户配置 (下次启动生效)")
    ms.add_argument("provider", help="供应商名: deepseek / openai / openai-compatible / 自定义网关名")
    ms.add_argument("model", help="模型名, 如 deepseek-chat / claude-3-5-sonnet")
    ms.add_argument("base_url", nargs="?", default=None, help="可选: 网关 base_url (自定义/兼容网关必填)")
    ms.add_argument("api_key_env", nargs="?", default=None, help="可选: 密钥环境变量名 (默认沿用当前)")
    ms.set_defaults(model_cmd="set")
    msub.add_parser("list-providers", help="列出内置已知供应商与自定义网关提示").set_defaults(model_cmd="list-providers")
    mf = msub.add_parser("find", help="跨供应商搜索模型 (关键词)")
    mf.add_argument("query", help="关键词: 模型名 / 供应商名 (如 deepseek / qwen / 7b)")
    mf.set_defaults(model_cmd="find")
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
    p.set_defaults(func="cmd_config")

    p = sub.add_parser("plugin", help="插件管理")
    psub = p.add_subparsers(dest="plugin_cmd", required=True)
    psub.add_parser("list", help="列出全部插件与服务")
    p.set_defaults(func="cmd_plugin")

    p = sub.add_parser("skill", help="技能管理")
    ssub = p.add_subparsers(dest="skill_cmd", required=True)
    ssub.add_parser("list", help="列出全部技能")
    sh = ssub.add_parser("show", help="查看技能详情")
    sh.add_argument("name")
    p.set_defaults(func="cmd_skill")

    p = sub.add_parser("memory", help="记忆管理")
    msub = p.add_subparsers(dest="memory_cmd", required=True)
    msub.add_parser("list", help="查看长期记忆")
    se = msub.add_parser("search", help="全文检索记忆")
    se.add_argument("query")
    p.set_defaults(func="cmd_memory")

    p = sub.add_parser("cron", help="定时任务")
    crsub = p.add_subparsers(dest="cron_cmd", required=True)
    a = crsub.add_parser("add", help="添加定时任务")
    a.add_argument("name")
    a.add_argument("prompt")
    a.add_argument("--interval", type=int, default=60, help="间隔分钟数")
    a.add_argument("--output", help="每次执行把结果写入该文件 (供监控/CI 消费)")
    crsub.add_parser("list", help="列出任务")
    r = crsub.add_parser("remove", help="删除任务")
    r.add_argument("id")
    en = crsub.add_parser("enable", help="启用任务")
    en.add_argument("id")
    dis = crsub.add_parser("disable", help="暂停任务")
    dis.add_argument("id")
    ed = crsub.add_parser("edit", help="修改任务 (间隔/提示词/名称/输出文件)")
    ed.add_argument("id")
    ed.add_argument("--interval", type=int, help="新的间隔分钟数")
    ed.add_argument("--prompt", help="新的任务提示词")
    ed.add_argument("--name", help="新的任务名称")
    ed.add_argument("--output", help="新的结果输出文件路径 (留空字符串则清除)")
    run = crsub.add_parser("run", help="立即执行指定任务 (一次)")
    run.add_argument("id")
    logs = crsub.add_parser("logs", help="查看任务执行历史")
    logs.add_argument("id")
    crsub.add_parser("tick", help="执行所有到期任务 (一次) — 也供系统计划任务/守护调用")
    st = crsub.add_parser("start", help="启动常驻调度守护 (自动执行到期任务, 无需系统计划任务)")
    st.add_argument("--detach", action="store_true", help="后台分离运行 (写 PID 文件, 返回终端)")
    st.add_argument("--check", type=int, default=60, help="守护检查间隔秒数 (默认 60)")
    crsub.add_parser("stop", help="停止常驻调度守护")
    crsub.add_parser("status", help="查看守护与任务状态")
    p.set_defaults(func="cmd_cron")

    p = sub.add_parser("open", help="打开文件并定位到行 (精确引用跳转)")
    p.add_argument("target", help="文件路径, 可带行号: <file>[:<line>]")
    p.set_defaults(func="cmd_open")

    p = sub.add_parser("mcp", help="MCP 协议: 接入外部 MCP Server 工具")
    psub = p.add_subparsers(dest="mcp_cmd", required=True)
    psub.add_parser("list", help="列出已配置的 MCP server 与桥接工具")

    pmt = psub.add_parser("tools", help="列出 MCP 工具 (可指定 server)")
    pmt.add_argument("server", nargs="?", default=None, help="只显示该 server 的工具")

    pmc = psub.add_parser("call", help="调用一个 MCP 工具")
    pmc.add_argument("server", help="MCP server 名")
    pmc.add_argument("tool", help="工具名")
    pmc.add_argument("--json", dest="json", default=None,
                     help="工具参数 (JSON 字符串, 如 '{\"text\":\"hi\"}')")

    pma = psub.add_parser("add", help="添加一个 MCP server 到用户配置")
    pma.add_argument("name", help="server 名 (本地引用)")
    pma.add_argument("command", help="启动命令 (如 npx / python)")
    pma.add_argument("--args", nargs="*", default=[], help="命令参数")
    pma.add_argument("--env", nargs="*", default=[], help="环境变量 KEY=VALUE")
    pma.add_argument("--timeout", type=float, default=None, help="单次调用超时(秒)")

    pmst = psub.add_parser("test", help="连通性测试: 启动 server 并列举工具")
    pmst.add_argument("name", help="已配置的 MCP server 名")
    p.set_defaults(func="cmd_mcp")

    p = sub.add_parser("hooks", help="用户级 Hooks: 在工具执行前/后挂载自己的脚本")
    hsub = p.add_subparsers(dest="hook_cmd", required=True)
    hsub.add_parser("list", help="列出已配置的 Hooks")
    hst = hsub.add_parser("test", help="触发一次 Hook 事件 (连通性测试)")
    hst.add_argument("event",
                     help="事件名: PreToolUse / PostToolUse / SessionStart / SessionEnd")
    hst.add_argument("--tool", default="echo_text",
                     help="工具名 (PreToolUse / PostToolUse 使用)")
    hst.add_argument("--json", dest="tool_input", default="{}",
                     help="工具参数 (JSON 字符串, 如 '{\"text\":\"hi\"}')")
    p.set_defaults(func="cmd_hooks")

    p = sub.add_parser("undo", help="事务化精确回滚 (账本持久化于 .qxt/ledger, 可跨进程)")
    p.add_argument("target", nargs="?", default="", help="可选: N(步数) / <file> / all / --safe")
    p.set_defaults(func="cmd_undo")

    p = sub.add_parser("impact", help="展示操作账本与影响半径 (哪些文件被改、改了几步)")
    p.set_defaults(func="cmd_impact")

    p = sub.add_parser("ext", help="外部能力引擎 (纯 Python) 调试与自检")
    exsub = p.add_subparsers(dest="ext_cmd", required=True)
    exsub.add_parser("engines", help="列出环境中真正可用的引擎").set_defaults(func="cmd_ext")
    ec = exsub.add_parser("call", help="调用某引擎的某方法 (参数用 JSON)")
    ec.add_argument("engine", help="引擎名, 如 crypto / rules / search")
    ec.add_argument("method", help="方法名, 如 selftest / load / search")
    ec.add_argument("params", nargs="?", default="{}", help="JSON 对象参数 (默认 {})")
    ec.add_argument("--timeout", type=float, default=60.0, help="请求超时秒数")
    ec.set_defaults(func="cmd_ext")
    est = exsub.add_parser("selftest", help="逐个启动引擎跑 list/ping, 报告健康度")
    est.add_argument("engines", nargs="*", help="可选: 只测指定的引擎名")
    est.add_argument("--timeout", type=float, default=15.0, help="单引擎超时秒数")
    est.set_defaults(func="cmd_ext")
    ei = exsub.add_parser("info", help="显示某引擎的元信息 (方法/版本)")
    ei.add_argument("engine", help="引擎名")
    ei.add_argument("--timeout", type=float, default=15.0, help="请求超时秒数")
    ei.set_defaults(func="cmd_ext")

    p = sub.add_parser("improve", help="自我改进闭环: 从执行历史提炼规则与技能草稿")
    impsub = p.add_subparsers(dest="improve_cmd", required=True)
    impsub.add_parser("summarize", help="复盘执行历史, 预览将生成的经验与规则 (dry-run)").set_defaults(func="cmd_improve")
    impsub.add_parser("apply", help="把经验固化为 rules .jsonl + 技能草稿并落盘").set_defaults(func="cmd_improve")

    p = sub.add_parser("session", help="会话管理 (列出/恢复/删除历史会话)")
    sesub = p.add_subparsers(dest="session_cmd", required=True)
    sesub.add_parser("list", help="列出最近的会话")
    r = sesub.add_parser("resume", help="恢复会话 (默认最近一个)")
    r.add_argument("target", nargs="?", help="会话序号或文件名 (省略则恢复最近)")
    d = sesub.add_parser("delete", help="删除会话 (按编号/会话ID/all)")
    d.add_argument("target", nargs="?", help="会话序号或会话ID (all 清空全部)")
    d.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    p.set_defaults(func="cmd_session")

    p = sub.add_parser("usercmd", help="用户自定义斜杠命令 (列出)")
    ussub = p.add_subparsers(dest="usercmd_cmd")
    ussub.add_parser("list", help="列出全部自定义命令")
    p.set_defaults(func="cmd_usercmd")

    return parser


def main() -> int:
    # 先把 ~/.qingxiaotuan/.env 里的密钥加载进环境 (所有子命令共用)
    from ..config import load_dotenv
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    # -v 详细输出: 配置对应级别的日志 (concise 模式下 REPL 不显示但调试可用)
    verbose = getattr(args, "verbose", 0)
    if verbose >= 3:
        logging.getLogger().setLevel(logging.DEBUG)
    elif verbose >= 2:
        logging.getLogger().setLevel(logging.INFO)
    elif verbose >= 1:
        logging.getLogger("qingxiaotuan").setLevel(logging.DEBUG)
    # --print 模式: 非交互输出, stdin 不是 TTY 时不再报错退出
    is_print = getattr(args, "print_mode", False)
    _interactive_cmds = {None, "chat", "dev"}
    if not is_print and getattr(args, "cmd", None) in _interactive_cmds and not sys.stdin.isatty():
        sys.stderr.write(
            "\n[青小团] 交互模式需要真实终端 (TTY)。\n"
            "  当前 stdin 不是终端, 无法显示输入框。\n"
            "  请在系统自带的终端里运行 (不要在本软件/IDE 的内嵌命令行里跑):\n"
            "    - Windows: 打开『命令提示符』或『Windows Terminal』, 输入 qxt\n"
            "    - 或在该终端里先 cd 到项目目录, 再 .venv\\Scripts\\Activate.ps1 后 qxt\n"
            "  非交互任务可用: qxt run \"你的任务\" (headless, 跑完退出)\n"
            "  管道输出可用: qxt --print chat \"你的任务\" (直接输出到 stdout)\n\n"
        )
        return 2
    # 无子命令: 默认进入交互式对话 (qxt 直接开界面)。
    if not getattr(args, "cmd", None):
        if getattr(args, "yolo", False) and not getattr(args, "mode", None):
            args.mode = "yolo"
        return int(_resolve_func("cmd_chat")(args))
    return int(_resolve_func(args.func)(args))


if __name__ == "__main__":
    sys.exit(main())
