"""qxt others —— 能力目录 (新手上手的总入口)。

把散落在 `qxt` 下的长尾子命令收敛成一个**友好的功能目录**, 降低上手难度:
新人看到的是「能做什么」, 而不是一长串原始子命令名与参数。

默认只输出「功能目录」(按主题分组, 中文友好名 + 一句话说明), 不暴露原始子命令名;
加 ``--show`` 才会把对应的真实命令列在旁边, 供想直接用的老手复制。

说明: 底层子命令 (run / agent / bench / ...) 仍然可用, ``qxt others`` 只是它们的
「发现层 / 目录」, 不删功能、不破兼容。
"""

from __future__ import annotations

from typing import List, Tuple

from ._ui_singleton import console

# 目录数据: (主题, [(友好名, 一句话说明, 真实命令)])
_CATALOG: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    ("对话与执行", [
        ("直接聊天", "打开交互界面, 边聊边干, 像用终端里的搭档", "qxt"),
        ("一次性跑完", "给个任务, 它跑完把结果交回来就退出 (适合脚本/CI)", "qxt run"),
        ("后台自主干活", "派它去后台自己做, 终端不阻塞, 你接着干别的", "qxt agent"),
        ("看着后台进度", "随时看后台任务跑到哪了、取日志、取消或等待", "qxt bg"),
        ("自主开发循环", "分析→实现→自测→核实→汇报, 直到你满意为止", "qxt dev"),
    ]),
    ("模型与配置", [
        ("换个脑子", "配置/热切换模型供应商, 开箱支持 48 家含本地 Ollama/llama.cpp", "qxt models"),
        ("改设置", "读取/写入/校验用户配置项", "qxt config"),
        ("切换运行模式", "在「标准 / 无限制(YOLO)」之间切换默认模式", "qxt mode"),
    ]),
    ("安全与审计", [
        ("安全总入口", "白名单管理、本地黑名单减负、安全状态与更新", "qxt safe"),
    ]),
    ("工程能力", [
        ("代码开发子系统", "检索增强 + 验证闸门 + 规格分解, 对标专业编码 Agent", "qxt codedev"),
        ("代码编辑助手", "把编辑任务拆成标准简报, 走五层安全闸门与验证闭环", "qxt code-edit"),
        ("撤销回滚", "事务化精确回滚, 账本跨进程持久化", "qxt undo"),
        ("影响半径", "看看这次操作改了哪些文件、改了几步", "qxt impact"),
        ("架构自检", "五层架构 (安全/执行/编排/上下文/可观测) 自检与演示", "qxt arch"),
    ]),
    ("扩展与集成", [
        ("插件", "查看微内核已装载的插件与服务", "qxt plugin"),
        ("技能", "查看与管理可复用的技能", "qxt skill"),
        ("记忆", "查看长期记忆 / 全文检索", "qxt memory"),
        ("接入外部工具", "把外部 MCP Server 的工具接进来用", "qxt mcp"),
        ("挂自己的脚本", "在工具执行前后挂载你的钩子脚本", "qxt hooks"),
        ("自定义斜杠命令", "列出你自己定义的快捷命令", "qxt usercmd"),
        ("多会话面板", "Agent View, 一眼看多个会话", "qxt agents"),
        ("GitHub 原生绑定", "直接调本机 gh CLI: view/clone/search/api…, 破坏性操作硬性拦截", "qxt gh"),
        ("借鉴开源", "让青小团去 GitHub 找相似实现来借鉴 (带署名, 非全抄)", "qxt gh borrow"),
    ]),
    ("运维与可观测", [
        ("初始化向导", "首次使用快速配置 API Key 等", "qxt setup"),
        ("健康检查", "环境/依赖/连通性一键体检", "qxt doctor"),
        ("基准测试", "测缓存命中率与模型响应延迟", "qxt bench"),
        ("定时任务", "把重复活排成定时/常驻守护", "qxt cron"),
        ("联网/搜索配置", "查看/调整搜索上限、摘要截断、top_k、超时 (基础设施)", "qxt network configuration"),
        ("会话管理", "列出 / 恢复 / 删除 / 导出历史会话", "qxt session"),
        ("回放轨迹", "把历史会话按事件溯源重建、结构化导出", "qxt replay / trajectory"),
        ("自我改进", "从执行历史里提炼规则与技能草稿", "qxt improve"),
        ("内置教程", "任务驱动的安全/撤销/协作/成本教程", "qxt tutorial"),
        ("精确跳转", "在编辑器里打开某文件并定位到行", "qxt open"),
        ("当 IDE 大脑", "以 ACP 协议运行, 供 VS Code/Zed/JetBrains 驱动", "qxt acp"),
    ]),
]


def cmd_others(args) -> int:
    """能力目录: 友好地列出 qxt 能做的所有事。"""
    show_cmd = bool(getattr(args, "show", False))

    console.print("\n[bold]青小团 · 能力目录[/bold]  (想直接用某功能? 末尾加 [cyan]--show[/cyan] 看真实命令)\n")

    for idx, (theme, items) in enumerate(_CATALOG, 1):
        console.print(f"[bold cyan]{idx}. {theme}[/bold cyan]")
        for title, blurb, cmd in items:
            if show_cmd:
                console.print(f"   • [bold]{title}[/bold] — {blurb}  [dim]{cmd}[/dim]")
            else:
                console.print(f"   • [bold]{title}[/bold] — {blurb}")
        console.print("")

    if not show_cmd:
        console.print("[dim]提示: qxt others --show 可显示每条对应的真实命令; qxt help 看完整参数。[/dim]")
    return 0
