#!/usr/bin/env python3
"""批量翻译英文注释为中文。

扫描 qingxiaotuan/ 下所有 .py 文件, 将常见的英文注释/文档字符串翻译为中文。
"""
import re
import os
from pathlib import Path

# 翻译映射表: 英文 -> 中文 (精确匹配, 大小写敏感)
TRANSLATIONS = {
    # 模块级文档
    "JSONL IPC client - pure Python engine adapter": "JSONL IPC 客户端 - 纯 Python 引擎适配",
    "Through unified JSONL IPC protocol with Python engines.": "通过统一 JSONL IPC 协议与 Python 引擎通信。",
    "Supports: request/response/streaming/ready frame.": "支持: 请求/响应/流式/首帧 ready。",
    "External engine manager - for CLI and tool layer": "外部引擎管理器 - 供 CLI 和工具层使用",
    "Get or create engine client": "获取或创建引擎客户端",
    "Call engine method": "调用引擎方法",
    "List all available engines": "列出所有可用引擎",
    "Health check": "健康检查",
    "Close all clients": "关闭所有客户端",
    "Get engine manager singleton": "获取引擎管理器单例",
    "Close all external engine processes (clean up after tests, prevent cross-test IPC handle leaks)": "关闭所有外部引擎进程 (测试后清理, 防止跨测试 IPC 句柄泄漏)",
    "Plugin base class": "插件基类",
    "All capabilities (tools, model adapters, memory, skills...) are plugins.": "所有能力 (工具、模型适配器、记忆、技能……) 都是插件。",
    "Called when plugin is activated, usually to register services with the kernel.": "插件被激活时调用, 通常在此向内核注册服务。",
    "Called when plugin is deactivated.": "插件被停用时调用。",
    "Activate all plugins in dependency topological order.": "按依赖拓扑序激活全部插件。",
    "Subscribe to kernel events.": "订阅内核事件。",
    "Emit a kernel event.": "发射内核事件。",
    "Kill process tree (Windows uses taskkill /T, POSIX uses killpg).": "终止进程树 (Windows 用 taskkill /T, POSIX 用 killpg)。",
    "Start engine process": "启动引擎进程",
    "Background read loop": "后台读取循环",
    "Send request and wait for response; on_stream callback receives streaming chunks.": "发送请求并等待响应; on_stream 回调接收流式 chunk。",
    "Send streaming request": "发送流式请求",
    "Close engine process and reclaim read thread, avoid residual _read_loop thread and subprocess.": "关闭引擎进程并回收读线程, 避免残留 _read_loop 线程与子进程。",
    "Persist事实记忆 + FTS5 全文检索 + 标签/时间索引。": "持久事实记忆 + FTS5 全文检索 + 标签/时间索引。",
    "Tag system: each memory can have tags, support search by tag": "标签系统: 每条记忆可带标签, 支持按标签检索",
    "Time search: support search by time range": "时间检索: 支持按时间范围检索历史记忆",
    "Context-aware recall: automatically retrieve relevant memories at session start": "上下文感知召回: 会话启动时自动检索相关记忆注入上下文",
    "Structured search: support combined queries by kind/source/tag": "结构化检索: 按 kind/source/tag 组合查询",
    "Full-text search memory": "全文检索记忆",
    "Search by tags": "按标签检索",
    "Search by time range": "按时间范围检索",
    "Context-aware recall: automatically retrieve relevant memories based on current task": "上下文感知召回: 根据当前任务自动检索相关记忆",
    "Get memory statistics": "获取记忆统计信息",
    # 工具相关
    "Shell tool plugin: execute terminal commands in workspace (dangerous operations, require user confirmation by default)": "Shell 工具插件: 在工作区执行终端命令 (危险操作, 默认需用户确认)",
    "Before execution safety guardrail (minimum blast radius)": "执行前安全拦截 (最小影响半径)",
    "Plan mode: only allow read commands, block write commands": "Plan 模式: 只读命令放行, 写命令拦截",
    "Execution-time safety guardrail (minimum impact radius)": "执行前安全护栏 (最小影响半径)",
    "Kill process tree (Windows uses taskkill /T, POSIX uses killpg)": "终止进程树 (Windows 用 taskkill /T, POSIX 用 killpg)",
    "Run shell command": "执行 shell 命令",
    "Register shell tool": "注册 shell 工具",
    # config相关
    "Configuration loading and composition view.": "配置加载与组合视图。",
    "Config class combines: built-in defaults -> user config -> profile -> CLI patch into a unified view;": "Config 类负责把 内置默认 -> 用户配置 -> Profile -> 命令行 patch 四层叠加成统一视图;",
    "with helper functions like home_dir / load_dotenv / deep_merge / patch_replace / dump_yaml.": "并附带 home_dir / load_dotenv / deep_merge / patch_replace / dump_yaml 等辅助函数。",
    "Load .env file into process environment variables (does not overwrite existing variables).": "把 .env 中的键值加载进进程环境变量 (不覆盖已有变量)。",
    "Deep merge: recursively merge dicts from overlay, other types directly override.": "深合并: overlay 中的 dict 递归合并, 其他类型直接覆盖。",
    "Read boolean config, compatible with Config object and dict.": "读取布尔配置, 兼容 Config 对象与 dict。",
    "Attempt to convert user/CLI string values to int/float/bool/JSON/original value.": "尽力把用户/CLI 传入的字符串值转为 int/float/bool/JSON/原值。",
    "Parse API key by priority: dedicated env name -> general QXT_API_KEY -> user config reference.": "按优先级解析密钥: 专用 env 名 -> 通用 QXT_API_KEY -> 用户配置中的引用。",
    "Create ~/.qingxiaotuan directory skeleton (Hermes style).": "创建 ~/.qingxiaotuan 目录骨架 (Hermes 风格)。",
    "Configuration validation - self-check entry for users before/after changes (qxt config validate).": "配置校验 - 在改动前后给用户一个自检入口 (qxt config validate)。",
    "Return (errors, warnings). errors are blocking, warnings are informational.": "返回 (errors, warnings)。error 阻断性, warning 提示性。",
    # Agent相关
    "Agent main loop - ReAct: think -> tool -> observe -> think ... until done.": "Agent 主循环 - ReAct: think -> tool -> observe -> think ... 直到完成。",
    "Append event to append-only session stream (auditable, replayable).": "把事件写入 append-only 会话流 (可审计、可回放)。",  
    "Accumulate model usage (including DeepSeek prompt cache hit fields).": "累计模型用量 (含 DeepSeek 的 prompt cache 命中字段)。",
    "DeepSeek prefix cache hit rate: hit / (hit + miss). Returns None if no data.": "DeepSeek 前缀缓存命中率: hit / (hit + miss)。无数据返回 None。",
    "Task progress nudge: after N turns, self-remind if current method is worth distilling into a skill.": "任务推进数轮后自我提醒: 本次方法是否值得蒸馏成技能。",
    "Cancel current task": "取消当前任务",
    "Clear cancel request, prepare for next task": "清除取消请求, 准备执行下一项任务",
    "Attach an image (local path / http(s) URL / data: URI), sent with next user message.": "挂接一张图片 (本地路径 / http(s) URL / data: URI), 随下一轮 user 消息发送。",
    "Clear pending image buffer, return count of cleared images.": "清空待发送的图片缓冲, 返回清除的数量。",
    "Tool execution - delegated to ToolExecutor": "工具执行 - 委托给 ToolExecutor",
    "Run one complete conversation round.": "运行一轮完整对话。",
    "Context statistics: budget, used, compact threshold, etc.": "上下文统计: 预算、已用、压缩阈值等。",
    "Compress context (if needed).": "压缩上下文 (如需要)。",
    "Manual context compression (compare to Claude Code /compact). Returns count of folded messages.": "手动压缩上下文 (对标 Claude Code /compact)。返回折叠的消息条数。",
    "Auto model route: auto-select/switch model by task difficulty/required capability (fail-safe: only switch to configured providers).": "自动模型路由: 按任务难度/必需能力自动选择/切换模型 (失败保险: 仅切换到已配置密钥的供应商)。",
    "Build 'absolutely stable' system prompt (no task-dependent semantic recall).": "组装「绝对稳定」的系统提示 (不含随任务变化的语义召回)。",
    # 工具基类
    "Tool base class and registry - Hermes style: each tool module self-registers, kernel discovers on demand.": "工具基类与注册表 - Hermes 风格: 每个工具模块自我注册, 内核按需发现。",
    "Structured tool execution result (compare to Claude Code's structured tool output).": "结构化工具执行结果 (对标 Claude Code 的结构化工具输出)。",
    "Tool execution context: access other plugins' services through the kernel (memory, skills, config...).": "工具执行上下文: 通过内核拿到其他插件的服务 (记忆、技能、配置……)。",
    "Progress heartbeat: periodically report status during long-running tool calls.": "进度心跳: 长时间运行的工具调用期间定期调用, 报告进度。",
    "Execute tool and return string result.": "执行工具并返回字符串结果。",
    "Execute tool and return structured ToolResult (compare to Claude Code).": "执行工具并返回结构化 ToolResult (对标 Claude Code)。",
    "Post each tool execution, emit tool.executed event to kernel for self-improve review.": "每个工具执行后向内核 emit tool.executed 事件, 供 self-improve 复盘订阅。",
    # 搜索/索引
    "Recursive regex search for files (ignores node_modules/.git)": "递归正则搜索文件 (忽略 node_modules/.git)",
    "Cross-platform desktop notifications": "跨平台桌面通知",
    "YAML policy validation (no-eval safe expressions)": "YAML 策略校验 (无 eval 安全表达式)",
    "Skill package registry: pull / publish / search": "技能包注册表: 拉取 / 发布 / 检索",
    # 杂项
    "Temporary file + atomic replace write, crash won't leave half-written config.": "临时文件 + 原子替换写入, 崩溃不留下半截配置。",
    "Atomic write (temp file + replace), crash-safe.": "原子写入 (临时文件 + 替换), 崩溃安全。",
    "Validate YAML rules": "校验 YAML 规则",
    "Build rules from YAML text": "从 YAML 文本构建规则",
    "Check file against loaded rules": "用已加载的规则检查文件",
    "List methods": "列出方法",
    "List loaded rules": "列出已加载规则",
    "Diff: line/word-level diff + patch + 3-way merge": "Diff: 行/词级 diff + patch + 3-way merge",
    "Crypto: PBKDF2-HMAC-SHA256 derivation + SHA256-keystream stream cipher + fingerprints": "Crypto: PBKDF2-HMAC-SHA256 派生 + SHA256-keystream 流式加密 + 指纹",
    "Index: FNV-1a incremental symbol index": "Index: FNV-1a 增量符号索引",
    "ANSI: terminal escape parse / strip / render": "ANSI: 终端转义解析/剥离/渲染",
    "Safety: minimum blast radius guardrail - risk scoring + blast radius + blocking": "Safety: 最小影响半径护栏 - 风险评分 + blast radius + 拦截",
    "JSON: RFC 6901 pointer / per-path diff / deep merge": "JSON: RFC 6901 Pointer / 逐路径 diff / 深合并",
}


def translate_file(filepath: Path) -> int:
    """翻译单个文件中的英文注释, 返回修改行数。"""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return 0

    changes = 0
    for eng, chn in TRANSLATIONS.items():
        # 只在注释行或文档字符串中翻译
        new_text = text.replace(eng, chn)
        if new_text != text:
            text = new_text
            changes += 1

    if changes:
        filepath.write_text(text, encoding="utf-8")
    return changes


def main():
    root = Path(__file__).resolve().parent.parent / "qingxiaotuan"
    total = 0
    for py_file in sorted(root.rglob("*.py")):
        n = translate_file(py_file)
        if n:
            print(f"  {py_file.relative_to(root.parent)}: {n} 处翻译")
            total += n
    print(f"\n共翻译 {total} 处英文注释为中文")


if __name__ == "__main__":
    main()
