"""qxt 命令行参数解析与入口。

最简用法 (开箱即用):
    qxt              直接打开交互界面 (默认进对话)
    qxt --yolo       无限制模式: 危险操作自动批准
    qxt --plan       Plan 模式启动: 窗口 Agent 只读, 不修改任何文件
    qxt --isolation  引擎进程级隔离: 非判定类引擎以真实 JSONL 子进程运行 (崩溃/超时安全回落)
    qxt models       配置/热切换模型供应商 (开箱支持48家, 含 Ollama/llama.cpp 本地)
    qxt help [子命令]  显示帮助 (等效于 qxt --help / qxt <子命令> --help)

子命令一览:
    qxt dev "任务"            进入自主开发循环 (分析→实现→自测→核实→汇报, 直到你满意)
    qxt run "任务"            headless 一次性任务, 跑完退出 (适合脚本/CI)
    qxt agent "任务"          后台自主任务 (终端不阻塞)
    qxt setup                 初始化向导 (API Key 等)
    qxt doctor                环境健康检查
    qxt models                配置/热切换模型供应商 (48 家开箱即用, 含本地 Ollama/llama.cpp)
    qxt config get/set/dump   配置管理
    qxt plugin list           查看微内核插件与服务
    qxt skill list/show       技能管理
    qxt memory list/search    记忆管理
    qxt cron add/list/remove/enable/disable/edit/run/logs/tick/start/stop/status  定时任务 (含常驻守护)
    qxt open <file>[:<line>]  精确引用跳转 (在编辑器中打开指定行)
    qxt ext engines/call/selftest/info  外部能力引擎 (纯 Python) 调试
    qxt improve summarize/apply      自我改进闭环 (从执行历史提炼规则与技能草稿)
    qxt tutorial list/run <name>     任务驱动内置教程 (安全/撤销/协作/成本)
    qxt session list/resume/delete   会话管理 (列出/恢复/删除)
    qxt bench cache/latency          基准测试 (缓存命中率 / 响应延迟)
    qxt migrate detect/run/status    旧版 ~/.kimi 配置/会话 → 新版 一键迁移
    qxt network configuration      联网/搜索配置 (搜索上限/默认条数/截断/top_k/超时; 短写 qxt net con)
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
    elif name == "cmd_gh":
        mod = importlib.import_module(".cmd_gh", __package__)
    elif name == "cmd_acp":
        mod = importlib.import_module(".cmd_acp", __package__)
    elif name == "cmd_replay":
        mod = importlib.import_module(".cmd_replay", __package__)
    elif name == "cmd_trajectory":
        mod = importlib.import_module(".cmd_trajectory", __package__)
    elif name == "cmd_arch":
        mod = importlib.import_module(".cmd_arch", __package__)
    elif name == "cmd_codedev":
        mod = importlib.import_module(".cmd_codedev", __package__)
    elif name == "cmd_safe":
        mod = importlib.import_module(".cmd_safe", __package__)
    elif name == "cmd_harden":
        mod = importlib.import_module(".cmd_harden", __package__)
    elif name == "cmd_migrate":
        mod = importlib.import_module(".cmd_migrate", __package__)
    elif name == "cmd_network":
        mod = importlib.import_module(".cmd_network", __package__)
    elif name == "cmd_others":
        mod = importlib.import_module(".cmd_others", __package__)
    else:
        mod = importlib.import_module(".commands", __package__)
    return getattr(mod, name)


def _add_network_parser(sub, name: str, help_text: str) -> "argparse.ArgumentParser":
    """注册 `qxt <name> <configuration|con|config> [action key? value?]`。

    网络/搜索配置基础设施入口。``network`` 与 ``net`` 等价, command 层又提供
    ``configuration`` / ``con`` / ``config`` 三个等价别名, 满足「长写 qxt network
    configuration、短写 qxt net con」两种肌肉记忆。
    """
    p = sub.add_parser(name, help=help_text)
    p.set_defaults(func="cmd_network")
    nsub = p.add_subparsers(dest="network_cmd", required=True)
    for _n, _h in (
        ("configuration", "查看/设定联网/搜索配置 (别名 con/config)"),
        ("con", "configuration 的简化别名"),
        ("config", "configuration 的另一别名"),
    ):
        cp = nsub.add_parser(_n, help=_h)
        cp.add_argument("action", nargs="?", default="list",
                        help="list|dump|get|set (省略=list)")
        cp.add_argument("key", nargs="?", default=None, help="配置项 (get/set 用)")
        cp.add_argument("value", nargs="?", default=None, help="配置值 (set 用)")
        cp.set_defaults(func="cmd_network")
    return p


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qxt", description="青小团 CLI - 会成长的插件化 Agent")
    parser.add_argument("--version", action="version", version=f"qxt {__version__}")
    parser.add_argument("--profile", default="default", help="使用指定 profile (~/.qingxiaotuan/profiles/<name>)")
    parser.add_argument("--patch", help="一次性配置覆盖文件 (dsh 风格, 整值替换)")
    parser.add_argument("--workspace", help="工作区目录 (默认当前目录)")
    parser.add_argument("--model", help="临时覆盖模型名 (如 deepseek-v4-flash-free)")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="推理投入级别 (low/medium/high/xhigh/max)")
    parser.add_argument("--allowed-tools", dest="allowed_tools",
                        help="Claude Code 风格免确认工具/作用域: 如 'Bash(npm test),Read,Write' (命中者不提示)")
    parser.add_argument("--mode", choices=["standard", "yolo", "plan"], help="运行模式: standard(默认,需确认) / yolo(自动批准) / plan(只读规划)")
    parser.add_argument("--permission-mode",
                        choices=["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"],
                        dest="permission_mode",
                        help="Claude Code 风格权限模式 (映射到内部模式: dontAsk/bypassPermissions→yolo, plan→plan, 其余→standard)")
    parser.add_argument("--fallback-model",
                        help="主模型不可用时的备用模型 (对应 Claude Code --fallback-model)")
    parser.add_argument("--yolo", action="store_true", help="无限制模式: 危险操作自动批准 (等同于 --mode yolo)")
    parser.add_argument("--plan", action="store_true", help="Plan 模式: 窗口 Agent 只读, 不修改任何文件 (等同于 --mode plan)")
    parser.add_argument("--isolation", action="store_true",
                        help="引擎进程级隔离: 开启后非判定类引擎(diff/crypto/index/ansi/json/search/notify/rules)以真实 JSONL 子进程运行, 子进程崩溃/超时安全回落进程内; safety 引擎恒进程内(fail-closed)。默认关(进程内直调)。持久开启用 `qxt config set engine.isolation true`")
    parser.add_argument("--print", action="store_true", dest="print_mode",
                    help="非交互输出模式: 跑完任务直接输出结果到 stdout, 不进 REPL (可管道化)")
    parser.add_argument("--json-schema", dest="json_schema", default=None, metavar="SPEC",
                        help="结构化输出: 要求最终回答符合给定 JSON Schema (内联 JSON 或 @file.json), "
                             "输出通过校验的 JSON; 并提供 --json-schema-strict/--json-schema-retries")
    parser.add_argument("--json-schema-strict", dest="json_schema_strict", action="store_true",
                        help="--json-schema 严格模式: 回答必须仅含合法 JSON, 不含多余文字")
    parser.add_argument("--json-schema-retries", dest="json_schema_retries", type=int, default=3,
                        help="--json-schema 校验失败时的自动修复重试次数 (默认 3)")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="详细输出级别: -v=工具调用与计时, -vv=系统提示与完整请求, -vvv=原始 API 与完整 token")
    parser.add_argument("--max-cost", type=float, default=0.0,
                        help="单次会话最大花费上限 (USD), 超过自动停止; 0=不限制")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("acp", help="以 Agent Client Protocol server 运行, 供 IDE (VS Code/Zed/JetBrains) 驱动")
    p.add_argument("--login", action="store_true", help="执行终端鉴权后退出 (ACP 客户端触发的 AuthMethodTerminal 入口)")
    p.add_argument("--workspace", help="工作区目录 (默认当前目录)")
    p.set_defaults(func="cmd_acp")

    p = sub.add_parser("dev", help="自主开发循环 (分析→实现→自测→核实→汇报, 直到你满意)")
    p.add_argument("task", help="开发任务描述")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--permission-mode", choices=["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"], dest="permission_mode", default=argparse.SUPPRESS, help="Claude Code 风格权限模式")

    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="推理投入级别 (low/medium/high/xhigh/max)")
    p.set_defaults(func="cmd_dev")

    p = sub.add_parser("run", help="headless 一次性任务")
    p.add_argument("task", help="任务描述")
    p.add_argument("--yes", "-y", action="store_true", help="自动批准危险操作 (等同于 --mode yolo)")
    p.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    p.add_argument("--bg", action="store_true", help="后台运行, 不阻塞终端 (用 qxt bg 查看进度)")
    p.add_argument("--model", help="临时覆盖模型名")
    p.add_argument("--mode", choices=["standard", "yolo"], help="临时切换运行模式")
    p.add_argument("--permission-mode", choices=["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"], dest="permission_mode", default=argparse.SUPPRESS, help="Claude Code 风格权限模式")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="推理投入级别 (low/medium/high/xhigh/max)")
    p.add_argument("--max-turns", type=int, default=0, dest="max_turns",
                   help="限制代理交互轮数上限 (对应 Claude Code --max-turns; 0=不限)")
    p.add_argument("--allowed-tools", dest="allowed_tools",
                   help="Claude Code 风格免确认工具/作用域: 如 'Bash(npm test),Read,Write'")
    p.add_argument("--max-cost", type=float, default=0.0,
                    help="最大花费上限 (USD), 超过自动停止; 0=不限制")
    p.add_argument("--json-schema", dest="json_schema", default=None, metavar="SPEC",
                   help="结构化输出: 要求最终回答符合给定 JSON Schema (内联 JSON 或 @file.json)")
    p.add_argument("--json-schema-strict", dest="json_schema_strict", action="store_true",
                   help="--json-schema 严格模式: 回答必须仅含合法 JSON")
    p.add_argument("--json-schema-retries", dest="json_schema_retries", type=int, default=3,
                   help="--json-schema 校验失败时的自动修复重试次数 (默认 3)")
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
    sh = bgsub.add_parser("shell", help="管理用 `! 命令` 启动的后台 shell 任务")
    sh.add_argument("action", choices=["list", "status", "logs", "wait", "cancel", "prune"],
                    help="操作")
    sh.add_argument("job_id", nargs="?", default="", help="后台 shell 任务 ID")
    sh.add_argument("--tail", type=int, default=20, help="logs/wait 取输出的行数")
    sh.add_argument("--offset", type=int, default=0, help="logs 增量跟随游标")
    sh.add_argument("--timeout", type=float, default=0, help="wait 最长等待秒数 (0=不限)")
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

    p = sub.add_parser("models", help="配置/热切换模型供应商 (开箱支持48家, 含 Ollama/llama.cpp 本地)")
    p.set_defaults(func="cmd_model")
    msub = p.add_subparsers(dest="model_cmd", required=False)
    msub.add_parser("current", help="显示当前生效的模型配置").set_defaults(model_cmd="current")
    info = msub.add_parser("info", help="查看某供应商详细信息 (文档/模型/定价)")
    info.add_argument("provider_name", help="供应商名称 (如 deepseek / ollama)")
    info.set_defaults(model_cmd="info")
    ml = msub.add_parser("list", help="列出某供应商当前可选模型清单")
    ml.add_argument("provider_name", help="供应商名称 (如 openrouter / deepseek)")
    ml.set_defaults(model_cmd="list")
    mlocal = msub.add_parser("local", help="列出本机已安装的本地模型 (Ollama + llama.cpp)")
    mlocal.set_defaults(model_cmd="local")
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

    pmaud = psub.add_parser("audit", help="查看 MCP 调用审计日志 (持久化到 ~/.qingxiaotuan/mcp-audit.jsonl)")
    pmaud.add_argument("limit", nargs="?", type=int, default=20, help="显示条数 (默认 20)")
    pmaud.add_argument("--clear", action="store_true", help="清空审计日志")

    pms = psub.add_parser("security", help="显示各 MCP server 的安全策略 (权限/频率/审计/沙箱)")
    pms.add_argument("server", nargs="?", default=None, help="只显示该 server 的策略")
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

    p = sub.add_parser("arch", help="五层架构: 安全/执行/编排/上下文/可观测 自检与演示")
    asub = p.add_subparsers(dest="arch_cmd", required=True)
    asub.add_parser("demo", help="端到端跑一遍五层, 打印自检结果")
    asub.add_parser("status", help="查看五层组件可用性与内核注册状态")
    ap = asub.add_parser("policy", help="生成一个示例不可变安全策略 (seal 后落盘)")
    ap.add_argument("--out", default="immutable-policy.json", help="输出文件路径")
    ap.add_argument("--master-key", default="demo-master-key-change-me", help="签名主密钥")
    p.set_defaults(func="cmd_arch")

    p = sub.add_parser("codedev", help="代码开发子系统: 检索增强 + 验证闸门 + 规格分解 (对标 Claude Code)")
    csub = p.add_subparsers(dest="codedev_cmd", required=True)
    csub.add_parser("demo", help="造一个临时项目, 端到端演示检索/验证/分解/编排")
    csub.add_parser("doctor", help="检查子系统健康度 (索引/检测器/编排可用性)")
    cp = csub.add_parser("retrieve", help="对给定任务在项目里检索相关代码上下文")
    cp.add_argument("task", help="自然语言任务 (支持中文)")
    cp.add_argument("--root", default=".", help="待检索的项目根目录")
    cp.add_argument("--top-k", type=int, default=8, help="返回的相关符号数")
    cv = csub.add_parser("verify", help="在给定目录运行验证闸门 (build/test/lint)")
    cv.add_argument("--cwd", default=".", help="运行目录")
    p.set_defaults(func="cmd_codedev")

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

    p = sub.add_parser("session", help="会话管理 (列出/恢复/删除/导出/清理/统计)")
    sesub = p.add_subparsers(dest="session_cmd", required=True)
    sl = sesub.add_parser("list", help="列出最近的会话")
    sl.add_argument("--all", action="store_true", help="列出全部会话")
    sl.add_argument("--limit", type=int, default=10, help="显示数量 (默认 10)")
    r = sesub.add_parser("resume", help="恢复会话 (默认最近一个)")
    r.add_argument("target", nargs="?", help="会话序号或文件名 (省略则恢复最近)")
    d = sesub.add_parser("delete", help="删除会话 (按编号/会话ID/all)")
    d.add_argument("target", nargs="?", help="会话序号或会话ID (all 清空全部)")
    d.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    ex = sesub.add_parser("export", help="导出会话到文件或 stdout")
    ex.add_argument("target", nargs="?", help="会话序号或会话ID")
    ex.add_argument("-o", "--output", help="导出路径 (省略则输出到 stdout)")
    cl = sesub.add_parser("clean", help="清理过期会话 (默认 >30 天)")
    cl.add_argument("--older-than", type=int, default=30, help="清理超过 N 天的会话 (默认 30)")
    cl.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    sesub.add_parser("stats", help="显示会话统计信息")
    # --- 跨会话 / 分叉 (迭代 3) ---
    fr = sesub.add_parser("fork", help="分叉一个会话为新分支, 复用其历史")
    fr.add_argument("target", nargs="?", help="源会话序号或会话ID (省略取最近)")
    fr.add_argument("--at", type=int, default=None, dest="at_message",
                    help="分叉到第 N 条 user/assistant 消息 (默认整份)")
    fr.add_argument("--note", default="", help="为分支添加备注")
    tr = sesub.add_parser("tree", help="显示会话的分叉树 (谱系)")
    tr.add_argument("root", nargs="?", help="根会话序号或会话ID (省略取最近根)")
    rf = sesub.add_parser("ref", help="把 @session / @# 引用展开为内容")
    rf.add_argument("text", nargs="+", help="含引用的文本 (如 '参考 @session:<id>')")
    p.set_defaults(func="cmd_session")

    p = sub.add_parser("usercmd", help="用户自定义斜杠命令 (列出)")
    ussub = p.add_subparsers(dest="usercmd_cmd")
    ussub.add_parser("list", help="列出全部自定义命令")
    p.set_defaults(func="cmd_usercmd")

    p = sub.add_parser("agents", help="Agent View: 多会话管理面板")
    asub = p.add_subparsers(dest="agents_cmd")
    asub.add_parser("view", help="打开 Agent View 面板 (默认)")
    asub.add_parser("list", help="列出所有会话")
    p.set_defaults(func="cmd_agents")

    p = sub.add_parser("safe", help="安全总入口: 白名单 / 本地黑名单减负 / 状态 / 更新")
    ssub = p.add_subparsers(dest="safe_cmd", required=False)
    ssub.add_parser("status", help="安全系统状态 + 审计概览").set_defaults(safe_cmd="status")
    ssub.add_parser("list", help="列出白名单").set_defaults(safe_cmd="list")
    sa = ssub.add_parser("allow", help="把命令加入白名单 (用户在本地自主添加)")
    sa.add_argument("command", help="要加入白名单的命令")
    sa.add_argument("--description", help="描述 (可选)")
    sa.set_defaults(safe_cmd="allow")
    sd = ssub.add_parser("deny", help="从白名单移除命令")
    sd.add_argument("command", help="要移除的命令")
    sd.set_defaults(safe_cmd="deny")
    ssub.add_parser("blacklist", help="列出内置黑名单模式 (标注已减负)").set_defaults(safe_cmd="blacklist")
    sr = ssub.add_parser("reduce", help="抑制匹配关键字的黑名单模式 (本地减负)")
    sr.add_argument("keyword", help="黑名单模式 label 关键字 (如 dd / format / shutdown)")
    sr.set_defaults(safe_cmd="reduce")
    sre = ssub.add_parser("restore", help="恢复被抑制的黑名单模式")
    sre.add_argument("keyword", help="之前 reduce 的关键字")
    sre.set_defaults(safe_cmd="restore")
    ssub.add_parser("update", help="更新安全策略 (月度检查)").set_defaults(safe_cmd="update")
    p.set_defaults(func="cmd_safe")

    p = sub.add_parser("others", help="能力目录: 一句话看懂 qxt 能做什么 (新手入口)")
    p.add_argument("--show", action="store_true", help="同时显示每条对应的真实命令")
    p.set_defaults(func="cmd_others")

    p = sub.add_parser(
        "gh",
        help="GitHub 原生绑定: 调用本机 gh CLI (原生体验), 带不可绕过的安全沙箱",
    )
    p.add_argument("--rest", action="store_true",
                   help="只读命令走内置 REST 回退 (无需本机 gh CLI)")
    p.add_argument("gh_args", nargs=argparse.REMAINDER,
                   help="随 gh 的命令与参数, 原样透传本机 gh (可含 repo view/rclone/search/api 等)")
    p.set_defaults(func="cmd_gh")

    # ---- A4 事件溯源 / Trajectory 回放 ----
    p = sub.add_parser("replay", help="回放历史会话 (事件溯源重建)")
    p.add_argument("session", nargs="?", default=None,
                   help="会话 ID 或前缀 (默认列出最近会话)")
    p.add_argument("--json", action="store_true", help="以 JSON 输出 Trajectory")
    p.add_argument("--export", metavar="PATH", default=None,
                   help="把回放/轨迹导出为 Markdown 文件")
    p.add_argument("--list", action="store_true", help="列出最近会话")
    p.set_defaults(func="cmd_replay")

    p = sub.add_parser("trajectory", help="Trajectory 轨迹: 结构化导出/查看会话")
    trsub = p.add_subparsers(dest="traj_cmd")
    trs = trsub.add_parser("show", help="查看会话的 Trajectory 摘要")
    trs.add_argument("session", help="会话 ID 或前缀")
    tre = trsub.add_parser("export", help="导出 Trajectory 为 JSON / Markdown")
    tre.add_argument("session", help="会话 ID 或前缀")
    tre.add_argument("--format", choices=["json", "md"], default="md",
                     help="导出格式 (默认 md)")
    tre.add_argument("--out", metavar="PATH", default=None,
                     help="输出文件路径 (默认 stdout)")
    p.set_defaults(func="cmd_trajectory")

    # ---- tutorial (任务驱动内置教程, onboarding 核心能力) ----
    p = sub.add_parser("tutorial", help="任务驱动内置教程 (安全/撤销/协作/成本 等, 用真实命令引导)")
    tsub = p.add_subparsers(dest="tutorial_cmd", required=False)
    tsub.add_parser("list", help="列出可用教程")
    tr = tsub.add_parser("run", help="逐步运行某个教程 (默认只展示, 不自动执行)")
    tr.add_argument("name", help="教程名称 (如 safety / undo / swarm / cost)")
    tr.add_argument("--exec", dest="exec_demo", action="store_true",
                    help="同时执行标注为安全的演示命令")
    tr.add_argument("--step", type=int, default=1, help="从第 N 步开始 (默认 1)")
    tr.add_argument("--no-pause", dest="no_pause", action="store_true",
                    help="步骤间不暂停, 一口气跑完")
    p.set_defaults(func="cmd_tutorial")

    # ---- code-edit (代码编辑助手) ----
    p = sub.add_parser("code-edit", help="代码编辑助手: 标准化任务输入 / 验证闭环 / 五层安全闸门")
    ces = p.add_subparsers(dest="code_edit_cmd", required=True)
    ce = ces.add_parser("parse", help="解析任务为四要素简报 (Goal/Context/Constraints/Done)")
    ce.add_argument("task", help="任务文本，或含模板的文件路径")
    ce.add_argument("--no-ask", action="store_true", help="缺失项不交互补全")
    ces.add_parser("rules", help="加载并展示项目级规则 (.qingxiaotuan/rules.md / AGENTS.md)")
    cv = ces.add_parser("verify", help="运行测试命令并走预测—反馈闭环")
    cv.add_argument("command", help="测试命令 (如 'pytest -q')")
    cv.add_argument("--timeout", type=int, default=600, help="超时秒数")
    ckv = ces.add_parser("check-version", help="校验版本变更是否符合迭代策略 (版本锁死)")
    ckv.add_argument("old")
    ckv.add_argument("new")
    ckv.add_argument("--strategy", default="semver", help="迭代策略 (默认 semver)")
    cg = ces.add_parser("gate", help="对一个动作做风险分级与五层闸门判定")
    cg.add_argument("kind", help="动作类型 (edit_code/delete_files/run_command/bump_version/...)")
    cg.add_argument("--description", default=None)
    cg.add_argument("--target", action="append", default=[], help="目标 (可多次)")
    cg.add_argument("--command", default=None)
    cg.add_argument("--uncommitted", action="store_true", help="目标含未提交改动")
    ce2 = ces.add_parser("edit", help="解析+补全+加载规则+(可选)验证, 输出标准化任务简报")
    ce2.add_argument("task", help="任务文本，或含模板的文件路径")
    ce2.add_argument("--test", default=None, help="验证所用的测试命令")
    ce2.add_argument("--gate", action="store_true", help="走五层安全闸门预览")
    ce2.add_argument("--version-strategy", default="semver")
    ce2.add_argument("--max-retries", type=int, default=3)
    p.set_defaults(func="cmd_code_edit")

    # ---- harden (安全加固工具集) ----
    p = sub.add_parser("harden", help="安全加固工具: 审计导出/SBOM/加密后端/网络策略/可复现锁")
    hsub = p.add_subparsers(dest="harden_cmd", required=True)
    ae = hsub.add_parser("audit-export", help="把安全事件导出为 CEF/JSONL/Syslog, 可选推送")
    ae.add_argument("--format", default="jsonl",
                    help="导出格式, 可逗号分隔: cef,jsonl,syslog (默认 jsonl)")
    ae.add_argument("--from", dest="from_", default=None,
                    help="bus JSONL 源 (默认 ~/.qingxiaotuan/security-audit.jsonl)")
    ae.add_argument("--out", default=None, help="输出目录 (缺省打印到 stdout)")
    ae.add_argument("--push", default=None, help="可选: 把每条事件 POST 到该 URL")
    hsub.add_parser("sbom", help="生成 SPDX 2.3 软件物料清单").add_argument(
        "--out", default=None, help="输出路径 (默认 sbom.spdx.json)")
    hsub.add_parser("crypto-provider", help="列出可用的可插拔密码学后端")
    nc = hsub.add_parser("network-check", help="评估一条命令的网络出口策略")
    nc.add_argument("--command", required=True, help="待评估的命令")
    nc.add_argument("--egress-cidr", action="append", dest="egress_cidr", default=[],
                    help="出口 CIDR 白名单 (可多次, 如 10.0.0.0/8)")
    nc.add_argument("--one-way", action="store_true", help="单向模式 (禁止入站监听类命令)")
    rl = hsub.add_parser("repro-lock", help="生成/校验可复现构建锁")
    rl.add_argument("--out", default=None, help="锁文件路径 (默认 requirements.lock)")
    rl.add_argument("--verify", action="store_true", help="改为校验当前环境是否与锁一致")
    hsub.add_parser("status", help="展示当前加固态势: 加密后端/审计落盘/SBOM/网络策略")
    p.set_defaults(func="cmd_harden")

    # ---- migrate (旧版 ~/.kimi 配置/会话 → 新版 ~/.qingxiaotuan 迁移) ----
    p = sub.add_parser("migrate", help="旧版 ~/.kimi 配置/会话 → 新版 一键迁移 (检测/执行/状态)")
    mgsub = p.add_subparsers(dest="migrate_cmd", required=True)
    st = mgsub.add_parser("status", help="查看是否已迁移 (只读)")
    st.add_argument("--source", default=None, help="旧版源目录 (默认 ~/.kimi)")
    de = mgsub.add_parser("detect", help="只读扫描旧版状态并汇报 (不写任何东西)")
    de.add_argument("--source", default=None, help="旧版源目录 (默认 ~/.kimi)")
    de.add_argument("--target", dest="target", default=None,
                    help="目标主目录 (仅状态展示, 默认 ~/.qingxiaotuan)")
    ru = mgsub.add_parser("run", help="执行迁移 (幂等/可逆/绝不覆盖用户改过的目标文件)")
    ru.add_argument("--source", default=None, help="旧版源目录 (默认 ~/.kimi)")
    ru.add_argument("--target", default=None, help="目标主目录 (默认 ~/.qingxiaotuan)")
    p.set_defaults(func="cmd_migrate")

    # ---- network (联网/搜索配置基础设施, qxt net con = qxt network configuration) ----
    _add_network_parser(sub, "network", "联网/搜索配置管理 (查看/设定搜索上限、默认条数、截断、top_k、超时)")
    _add_network_parser(sub, "net", "network 的简化别名 (qxt net con = qxt network configuration)")

    # ---- help (只读打印帮助, 不进交互/不写配置) ----
    # 与 `qxt --help` 等效, 但符合 qxt help / qxt help <子命令> 的肌肉记忆。
    p = sub.add_parser("help", help="显示帮助 (qxt help [子命令])")
    p.add_argument("topic", nargs="?", help="子命令名 (省略则显示总览)")
    p.set_defaults(func="cmd_help")

    return parser


# ---------------------------------------------------------------------------
# 青小团 TUI —— 纯 Python 实现 (开箱即用, 不依赖 Node)
# ---------------------------------------------------------------------------
# qxt / qxt --plan / qxt --yolo 等交互式启动形态直接走内置 Python TUI:
#   qingxiaotuan/tui/tui.py::QxtTUI  (cmd_chat.fast_chat -> QxtTUI)
#   - 渲染/皮肤: 青小团自己的 Mascot (▐█▛█▛█▌ block art logo) + prompt_toolkit 主题;
#   - 底层:      build_kernel() + create_agent() + Agent.run() (青小团自己的 agent);
#   - 依赖:      仅 rich + prompt_toolkit (已随 qxt 安装), 无需 Node / 无需构建步骤。
# 历史上曾尝试复用 kimi 的 TS TUI (kimi-code-main/apps/qingxiaotuan-tui), 但那会
# 强依赖 Node 工具链 (tsdown + Node>=24.15 + 构建), 违背「开箱即用」, 故已移除该
# 启动路径, 仅保留下方纯 Python TUI。不再有任何 Node 进程被 qxt 拉起。


def main() -> int:
    # 先把 ~/.qingxiaotuan/.env 里的密钥加载进环境 (所有子命令共用)
    from ..config import load_dotenv
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    # 交互式/计划/免确认启动形态 (无子命令且非 --print) 直接走内置 Python TUI
    # (qingxiaotuan/tui/tui.py::QxtTUI, 仅依赖 rich + prompt_toolkit, 开箱即用,
    # 不依赖 Node / 任何 kimi 代码)。下方 fast_chat() 会接管终端。

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
    # 引擎进程级隔离: --isolation 强制开启当前进程 (默认跟随配置, 配置默认关)。
    # 路由由 core/engine_isolation.EngineIsolation 负责, safety 引擎恒进程内。
    if getattr(args, "isolation", False):
        from ..core.engine_isolation import set_isolation, EngineIsolation

        set_isolation(EngineIsolation(isolation_enabled=True))
    _interactive_cmds = {None, "dev"}
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
    # qxt help [<子命令>]: 只读打印帮助, 不进入交互、不写配置 (与 `qxt --help` / `qxt <子命令> --help` 等效)
    if getattr(args, "cmd", None) == "help":
        topic = getattr(args, "topic", None)
        if not topic:
            parser.print_help()
            return 0
        # 复用 argparse 公共能力: `qxt <topic> --help` 会打印该子命令帮助并退出。
        # 非法子命令由 argparse 自行报错 (exit 2); --help 触发 exit 0。
        try:
            parser.parse_args([topic, "--help"])
        except SystemExit as exc:
            return int(exc.code) if isinstance(exc.code, int) else 0
        return 2
    # 无子命令: 默认进入交互式对话 (qxt 直接开界面)。
    # fast_chat 内部把加载屏内嵌进 QxtTUI (单 Application), 内核后台构建, 杜绝双屏切换崩溃。
    if not getattr(args, "cmd", None):
        if getattr(args, "yolo", False) and not getattr(args, "mode", None):
            args.mode = "yolo"
        # 默认进入全屏 TUI (青小团对话界面); 终端异常时 cmd_chat 会回退 REPL。
        args.tui = True
        from .fast_start import fast_chat
        return int(fast_chat(args))
    func = args.func
    # 交互式对话: 同样走内嵌加载屏的 QxtTUI
    if func == "cmd_chat":
        from .fast_start import fast_chat
        return int(fast_chat(args))
    if callable(func):
        return int(func(args))
    return int(_resolve_func(func)(args))


if __name__ == "__main__":
    sys.exit(main())
