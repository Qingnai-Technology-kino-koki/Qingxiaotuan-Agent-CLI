# QXT Task Queue

本文件是当前开发任务的唯一权威清单。每项任务必须完成实现、测试和静态检查后才能标记完成。

## Milestones

- [x] M1: 跨命令 worker 进程
  - 目标：将后台任务从 CLI daemon 线程分离为独立 worker，持久化任务元数据、状态、心跳和结果。
  - 验收：提交命令立即返回；新 CLI 进程可 `list/logs/wait/cancel`；worker 崩溃可识别并恢复或标记失败；任务文件无半写入。
  - 落地：`core/background_store.py` 原子 manifest、`core/background_worker.py` 独立进程、`core/background.py` detached 提交、心跳/失联判定和跨 CLI 查询。
- [x] M2: 全屏 TUI 工作台
  - 目标：使用 prompt_toolkit/Rich 能力实现面板、日志区、输入区、状态栏和快捷键。
  - 验收：TTY 中可启动；输入、取消、查看工具活动和切换面板均可用；非 TTY 保持 headless 提示；退出无残留监听器。
  - 落地：`ui/fullscreen.py` 全屏工作台 + `ui/repl.py` DeepSeek 风格简约终端工作台。
  - 已实测：`qxt` 启动后 DeepSeek 风格界面正常渲染。
- [x] M3: 统一模型能力矩阵
  - 目标：统一 OpenAI、Anthropic、Gemini、OpenAI-compatible、本地模型协议，声明工具、流式、视觉、JSON 能力。
  - 落地：`models/base.py` 统一 `ModelCapabilities`；OpenAI-compatible/Anthropic 能力声明；Gemini provider 预设。

- [x] Token 与 loop 调度优化
  - 已完成：短任务跳过语义召回、任务上下文长度上限、loop 反思间隔配置、Anthropic 工厂参数兼容。
- [x] M4: 细粒度权限策略
  - 落地：统一 `PermissionPolicy` 接入工具注册表；shell 拒绝规则、网络域名允许列表、YOLO 红线和回归测试。
- [x] M5: 任务恢复与安全取消
  - 落地：`cancel()` 真正杀进程树（Windows `taskkill /T /F`、POSIX `killpg`+SIGTERM）；`recoverable()`+`BackgroundRunner.recover_queued()` 支持重启。
- [x] M6: 审计日志与查询
  - 落地：`qingxiaotuan/audit/` 三件套（store/plugin/__init__），JSONL 落盘 + 进程内环形缓冲（上限2000），敏感参数脱敏。
- [x] M7: 上下文与 Agent 可靠性
  - 落地：`ContextManager` 压缩边界保护；`Agent.run` 的 `max_iterations` 兜底终止；重试/退避/错误恢复。
- [x] M8: 真实端到端与发布质量
  - 目标：补 CLI、TUI、worker、模型 mock server 的端到端测试，完善文档和 CI 质量门禁。
  - 落地：
    - `tests/test_e2e_mock_server.py` 本地 OpenAI 兼容 mock 端点, 真实 HTTP 往返驱动 Agent 工具循环 (非流式/流式/流式工具调用分片累积)。
    - `tests/test_cli_e2e.py` 真实 `qxt` 子进程连 mock server 跑 headless 任务。
    - `tests/test_worker_e2e.py` 真实 `background_worker` 独立进程跑任务到 done / 模型错误快速失败。
    - `tests/test_fullscreen_tui.py` 扩展到 13 项 (状态栏 token/context、日志上限、焦点循环、context 面板)。
    - `.github/workflows/ci.yml` CI 质量门禁: py_compile + pytest (Python 3.11/3.12/3.13)。
    - 修复: worker 对持续模型错误空转到 max_turns 的缺陷, 改为快速失败标记 failed。
  - 验收: `pytest -q` 完整套件 294 passed, 1 skipped (环境相关)。

## TUI 风格统一 (已完成)

- [x] 共享主题模块 `ui/theme.py`: hex 色为唯一事实来源, 派生 `C` (Rich) 与 `PT_STYLE` (prompt_toolkit)。
- [x] repl.py 与 fullscreen.py 均改为从 theme 导入配色与 `MASCOT_ICONS` 状态图标。
- [x] 全屏版实时 token/context 面板: `set_context_info()` / `context_bar()` (与 repl 同签名), 侧栏渲染占用条 + tok 明细, 高占用变红。
- [x] 新增 `tests/test_theme.py` (5 项) + fullscreen context 面板测试 (4 项)。

## Claude Code 对齐: /compact 与 /cost (已完成)

- [x] `/compact` 手动上下文压缩: `ContextManager.compact_force()` (忽略预算阈值折叠旧历史) + `Agent.compact()` + 斜杠接线。
- [x] `/cost` 成本估算: `router.estimate_cost()` 按内置定价表估算; 展示 token / 缓存命中率 / 估算费用。
- [x] 合并重复 `/context` 分支, 更新 `/help` 文本。
- [x] 修复 `IpcClient.close()` 线程泄漏: 终止子进程 + 关闭管道 + join `_read_loop` 线程。
- [x] 测试: `test_slash_compact_cost.py` (6 项) + `test_context.py` compact_force + `test_external_engines.py` IPC close 回归。
- [x] 完整套件 `pytest -q` → **309 passed, 1 skipped**。

## Claude Code 对齐: Plan Mode 只读模式 (已完成)

- [x] `Tool` 增加 `read_only` 标志; `ToolContext` 增加 `plan_mode`; `ToolRegistry.dispatch_result` 在 Plan 模式下拦截修改类工具 (返回 `denied`)。
- [x] 全量工具分类: 文件/代码/评审/语言/记忆/技能/网页等只读工具标记 `read_only=True`, 写工具保持默认。
- [x] shell 命令只读判定 `_is_readonly_command`: 写特征优先 (修复 `echo x > file` 被误判为只读的 bug), 再按只读前缀放行。
- [x] `/plan` 斜杠命令: 无参切换, `/plan on|off` 显式设置; 同步 `agent.plan_mode` 与 `ctx.plan_mode`。
- [x] UI 指示: REPL 状态栏与全屏 TUI 状态栏/侧栏显示 `[PLAN]` 徽标。
- [x] 系统提示注入 Plan 模式准则 (只分析不修改, 等用户确认后执行)。
- [x] 测试: `tests/test_plan_mode.py` (10 项) 覆盖工具拦截/放行、shell 判定、斜杠切换、ctx 同步。

## Claude Code 对齐: 会话恢复 --resume (已完成)

- [x] `chat --resume` 接线: 从历史会话 JSONL 重建 messages 并补回系统提示, 继续对话 (对标 Claude Code --continue/--resume)。
- [x] `/resume` 斜杠命令: 无参列出最近会话 (编号+标题+时间), 支持按编号 / 会话id前缀 / 文件路径 / `latest` 恢复。
- [x] 修复: 全数字会话 id 前缀 (如 `20260825`) 被 `isdigit()` 误判为编号导致越界, 改为仅当数字在有效范围内才按编号处理。
- [x] UI: `/help` 与快捷键面板补充 `/resume` 条目。
- [x] 测试: `tests/test_resume.py` (8 项) 覆盖系统提示补回、路径/前缀/编号/latest 恢复、未找到处理、斜杠列出与恢复。

## UI: Kimi Code 风格改造 (已完成)

- [x] 欢迎横幅改为圆角框 `╭─╮│╰─╯`, 双行品牌 logo `▐█▛█▛█▌/▐█████▌`, 保留 Directory/Session/Model/Version 字段。
- [x] 横幅下方提示区 (对标 Kimi 的 Web UI 引导): 全屏工作台 + 会话自动创建提示。
- [x] 状态栏改双行 Kimi 风格: 左 `吉祥物 model cwd branch[±]` + 右 `/help: show commands`, 下行右对齐 `context: pct% (used/budget)`。
- [x] 输入框改为圆角框 + `│ > ` 提示符, 多行续行前缀 `│ `。
- [x] 修复全角/块字符宽度计算 (`_disp_width`), 中文与 `▐█▛█▛█▌` 不再导致边框错位; token 人性化显示 (`128k`/`1.5M`)。
- [x] git 脏状态检测 `[±]` 标记; 测试更新至新格式。

## UI: 全屏 TUI 三区布局 + 模式切换 (已完成)

对标 Kimi Code CLI 的全屏工作台交互, 改造 `ui/fullscreen.py`:

- [x] 布局改为三区: 对话视图 (侧栏+日志) + 输入框 + 底部状态栏。
- [x] 底部状态栏徽章式显示: `[模式]` 徽章 (agent/plan/shell) + `[PLAN]` 只读徽章 + `tok=` token 计数 + `ctx=` 上下文占用, 右侧快捷键提示。
- [x] 输入框提示符随模式变化: Agent `✨ > ` / Plan `📋 > ` / Shell `$ > ` (通过 `BeforeInput` 处理器动态更新, 修复 `TextArea` 无 `prompt` 属性的坑)。
- [x] `Ctrl-X` 循环切换输入模式 (agent → plan → shell → agent), `cycle_mode()` 同步 `_plan_mode` 与状态栏/侧栏 `[PLAN]` 指示。
- [x] `theme.py` 新增 `badge`/`badge-plan`/`badge-yolo` 徽章样式。
- [x] 测试: `tests/test_fullscreen_tui.py` 扩展至 17 项 (模式提示符切换、plan 同步、cycle_mode 循环、底部徽章渲染)。
- [x] 完整套件 `pytest -q` → **409 passed, 1 skipped** (环境相关)。

## Python 全栈改造 (已完成)

- [x] `qingxiaotuan/ext/` 创建 10 个纯 Python 引擎: diff, crypto, index, ansi, safety, json, search, notify, rules, skill_market
- [x] `qingxiaotuan/ext/registry.py` 引擎注册中心 + 健康检查
- [x] `qingxiaotuan/core/ipc_client.py` 重写: 包含 `ExternalEngineManager` 和 `IpcError`
- [x] `qingxiaotuan/tools/external.py` 重写: 使用正确的 `Tool(handler=...)` 接口注册 10 个外部工具
- [x] `pyproject.toml` 版本升级到 0.2.0 (依赖保持纯 Python: openai/pyyaml/rich/prompt_toolkit/httpx, 无编译扩展)

## 验证结果

```
Tool OK
ipc_client OK
external OK
builtin_tool_plugins OK: 11
ENGINES: ['diff', 'crypto', 'index', 'ansi', 'safety', 'json', 'search', 'notify', 'rules', 'skill-market']
  diff: OK
  crypto: OK
  index: OK
  ansi: OK
  safety: OK
  json: OK
  search: OK
  notify: OK
  rules: OK
  skill-market: OK
ALL DONE
```

## 定时任务 (Cron) 完整落地 (已完成)

对标 Hermes cron / Kimi Code 报告的「任务队列与持久化」设计, 补全定时任务子系统:

- [x] `config/defaults.py` 新增 cron 配置: `check_interval`(守护检查间隔) / `notify`(桌面通知) / `max_output_chars`(会话流输出上限)。
- [x] `cron/daemon.py` 新增常驻守护进程模块 (`python -m qingxiaotuan.cron.daemon`): 信号处理 (SIGTERM/SIGINT 优雅退出, 分片睡眠及时响应)、心跳文件 `daemon.heartbeat`、复用 `run_due_jobs` 与 tick 行为一致。
- [x] `cron/runner.py` 增强: 任务完成后按 `cron.notify` 发送桌面通知 (成功/失败都提示, 复用 NotifyEngine); 输出长度走配置。
- [x] `cli/commands.py` `cmd_cron` 完整实现: `add/list/remove/tick/start/stop/status`; `start --detach` 用子进程拉起守护 (Windows DETACHED_PROCESS, 复用 background_worker 模式); 存活检查复用 `_is_process_alive` (修复 Windows 上 `os.kill(pid,0)` 会误杀进程的坑)。
- [x] `cli/parser.py` 新增 `stop`/`status` 子命令。
- [x] `cli/commands.py` `cmd_cron` 扩展: `enable`/`disable`(启停任务)、`edit`(改间隔/提示词/名称)、`run`(立即执行指定任务)、`logs`(查看任务执行历史, 读会话流)。
- [x] `cron/store.py` 扩展: `get`/`set_enabled`/`update`。
- [x] 结果落盘: `cron add --output <file>` 把每次执行结果写入指定文件 (供监控/CI 消费); `cron edit --output` 可改/清 (传空字符串清除); `runner._write_output` 成功写正文、失败写 `[失败] <原因>`, 失败静默。
- [x] 桌面通知: 任务完成后按 `cron.notify` 发送通知 (成功/失败都提示, 失败带原因)。
- [x] 测试: `tests/test_cron_cli.py` (13 项) 覆盖 store CRUD、cmd_cron add/list/remove/enable/disable/edit/logs/tick/status、守护模块、open 跳转。

## 精确引用跳转 (file:line) (已完成)

对标报告「精确引用」设计, 让 AI 输出的代码引用可一键跳转:

- [x] `tools/code.py` 新增 `open_file` 工具: 在编辑器/默认应用中打开文件并定位到行 (优先 VSCode `code --goto`, 否则系统默认打开)。
- [x] `cli/commands.py` 新增 `cmd_open`: `qxt open <file>[:<line>]` 直接从 CLI 跳转到指定代码行。
- [x] `cli/parser.py` 注册 `open` 子命令。
- [x] `core/prompts.py` 系统提示准则新增: 所有代码引用必须用 `path/to/file.ext:line` 精确格式 (第 3 条)。
- [x] 测试: `tests/test_cron_cli.py` 覆盖 open 解析/缺失文件/非法行号。

## 多 Agent 协作: /swarm 斜杠命令接线 (已完成)

对标报告「多 Agent 协作」旗舰主题, 把已实现的 herdr 式协作库暴露给用户:

- [x] `core/swarm.py` 已实现完整协作链: 强模型规划 (planner) → 弱模型并发执行 (worker, 进程沙箱 + model.worker 覆盖层) → 强模型验收 (acceptor), 共享黑板 (Blackboard) 弱耦合通信, depends_on 依赖注入。
- [x] `cli/commands.py` `_handle_slash` 新增 `/swarm <目标>`: 构造 Swarm 跑协作, `ui.answer_md` 展示报告, 并把验收结论回灌主 Agent 便于继续追问。
- [x] `ui/repl.py` 命令面板 + `_HELP` 补充 `/swarm` 条目。
- [x] 测试: `tests/test_swarm.py` 新增 2 项 (接线成功回灌 / 无参提示用法), 共 14 项全过。

## 验证结果 (本次)

- `tests/test_cron_runner.py` + `tests/test_cron_cli.py` + `tests/test_code_tools.py` + `tests/test_config.py` + `tests/test_cache_compact.py` → 全部通过。
- 完整套件 409 项通过, 1 跳过 (环境相关)。
- 实测 `qxt cron add/list/status/start --detach/stop` 端到端: 守护进程拉起→执行到期任务→结果落会话流 (job.done)→停止。
- 实测 `qxt open <file>:<line>` 打开文件定位行号。
- 注: 本机 pytest 会话结束清理 `E:\Temp\pytest-of-28726\pytest-current` 时偶发 PermissionError (Windows junction 被进程占用), 属既有环境问题, 不影响测试结果。

## 安全加固与重试策略组件化 (已完成)

### 安全红线统一 (单一来源)
- [x] `ext/safety_engine.py` 新增 token 化判定函数: `has_recursive_rm` / `has_force_push` / `has_win_recursive_delete` / `is_redline`, 按分词+旗标归一判定, 不再依赖脆弱正则。
- [x] 覆盖此前可绕过的写法变体: `rm -r -f`、`sudo rm --recursive --force`、`git push -f`、`--force-with-lease`、`rd /s`、`rmdir /s`。
- [x] safety 引擎 critical 评分、shell 工具 YOLO 红线、code 工具 `_looks_dangerous` 三处规则统一复用同一套逻辑 (单一来源)。
- [x] YOLO 红线判定不再依赖 safety 服务可用性: 引擎缺失时本地兜底仍拦截致命命令 (fail-closed); 普通命令仍安全降级放行。
- [x] 修复 `_is_readonly_command` fail-open 漏洞: 未知命令在 Plan 模式下默认拒绝 (fail-closed), 并补 `sed -i` 写特征。

### RetryPolicy 组件化 (拆分 agent.py)
- [x] 新建 `core/retry.py`: `RetryPolicy` 独立组件 — 指数退避+抖动、限流优先 Retry-After、鉴权快速失败、retry_on 白名单、等待封顶 60s; sleep/rng 可注入。
- [x] `Agent._chat_with_retry` 改为委托策略; `max_retries`/`retry_backoff`/`retry_jitter`/`retry_on` 属性代理保持旧接口兼容; `_classify`/`_retry_after` 委托模块函数。
- [x] 测试: `tests/test_retry_policy.py` (10 项) + 更新既有重试测试至新模块路径。

### 可观测性
- [x] 静默异常补 debug 日志: agent 会话事件写入/codebase 地图构建、shell 护栏异常、repl git 探测, 均落文件日志便于排障。

## M9: 事务化操作账本 Mutation Ledger (先机能力, 已完成)

> 先机定位: 主流 Agent (含 Claude Code) 的"撤销"依赖用户的版本控制习惯, 自身不维护操作级快照账本, 更没有"执行前自动快照 + 异常自动回滚"的事务语义。本能力把"最小影响半径"补齐到「事后可逆 + 事前可见」, 形成青小团的直接差异化。

- [x] 新引擎 `core/ledger.py` (零第三方依赖, 仅标准库): `MutationLedger` 对写类工具目标做字节级快照 (文件 `.bin` / 目录 `.zip`), 落盘 JSONL 审计日志 `ledger.jsonl` 于工作区 `.qxt/ledger`。
- [x] 事务闭环接入 `tools/base.py dispatch_result`: 执行前算目标 + 影响半径预览; 执行前 `snapshot`; 成功 `record`; 异常 `restore` 自动回滚 (文件系统不留半成品)。
- [x] 精细回滚: `undo_last(n)` / `undo_file(rel)` / `undo_all()`; 跨进程持久 (会话结束后单独跑 `qxt undo` 也能回滚, 快照与凭证均落工作区)。
- [x] 影响半径预览: 危险工具确认提示注入 `📐 影响半径预览`, edit 用 difflib 生成 unified diff 预览。
- [x] `qxt undo [target]` / `qxt impact` 子命令 + 斜杠 `/undo` `/impact` 接线; `/undo` 账本优先、git 兜底。
- [x] `config/defaults.py` 增加 `ledger.*` 配置块 (enabled / snapshot_dir / auto_rollback_on_error / keep_snapshots / max_records / impact_preview)。
- [x] `.gitignore` 忽略 `.qxt/` 运行时状态。
- [x] 测试: `tests/test_ledger.py` (15 项) 覆盖快照/回滚/undo_last/undo_file/undo_all/stats/history/auto-rollback/影响半径/dispatch 集成/跨进程持久。
- [x] 验收: `pytest tests/test_ledger.py -q` → **15 passed**; CLI 跨进程冒烟: `qxt undo` 正确将 v2-CHANGED 还原为 v1, `qxt impact` 正确报告影响半径。
- [ ] 注: 完整套件中 `tests/test_web_fetch.py` (6 项) 因沙箱无网络失败、`tests/test_enhancements.py::test_cmd_undo_safe_stash` 因沙箱拦截嵌套 `git stash` 在整跑时失败 (隔离运行时通过), 均与本能力无关、非回归。

## M10: MCP 真正接入内核 (补齐 Claude Code 级硬缺口)

> 先机/补齐定位: 项目早有 `tools/mcp/{client,plugin}.py` 的完整实现, 但 `MCPPlugin` 从未在 `app.py` 注册、`cmd_mcp` 停留在"开发中" —— 即"写了没接"。本次把 MCP 真正接通, 并修掉 client 的**死锁 bug**: 原实现在 async 函数里用同步 `for raw in proc.stdout` 阻塞读, 会卡死事件循环导致 `initialize` 握手永远发不出去 (远端 server 收不到初始化, 自然无响应, 客户端 30s 超时)。改为 `asyncio.create_subprocess_exec` + 异步 readline/write, boot 阶段先起 read_loop 任务再握手。

- [x] 重写 `tools/mcp/client.py`: 真异步 stdio I/O (create_subprocess_exec + 异步 readline/write), 启动即并发读响应; 优雅 shutdown (shutdown→exit→terminate→停事件循环线程); Windows ProactorEventLoop 下字节流写入修正。
- [x] `app.py` `build_kernel` 注册 `MCPPlugin()` (拓扑排序保证 `tool_registry`/`config` 先就绪); 无 `mcp.servers` 配置时为零副作用空操作。
- [x] `cmd_mcp` 实现 5 个子命令: `list` (server 列表 + 连接状态 + 工具数) / `tools [server]` (列工具 schema) / `call server tool --json` (调用) / `add name command --args --env --timeout` (持久化到用户配置) / `test name` (连通性)。
- [x] `parser.py` 扩展 `qxt mcp` 子命令参数; 斜杠 `/mcp` 与 `/mcp <server>` 接线, 并补 `_HELP` 说明。
- [x] 测试: `tests/test_mcp.py` (11 项) + `tests/fixtures/mcp_demo_server.py` (纯标准库最小 MCP server, 无网络依赖); 覆盖 client 握手/调用/报错、plugin 桥接注册为 `mcp__<server>__<tool>` 并转发调用、plugin 禁用跳过、CLI list/tools/call/add。
- [x] 验收: `pytest tests/test_mcp.py -q` → **11 passed**; 真实 CLI 分发冒烟 `qxt mcp list` / `qxt mcp call` 行为正确。

## M11: 用户级 Hooks (把 Agent 变成可编排的 — 先机能力)

> 先机/补齐定位: 几乎所有轻量 Agent 都没有用户级 Hooks; 即便 Claude Code 有, 青小团也把安全边界钉得更死。内核已有 append-only 事件总线 (`on`/`emit`), 本能力把它暴露为"配置驱动的外部脚本钩子", 挂到 PreToolUse / PostToolUse / SessionStart / SessionEnd, 让用户在工具执行前/后挂载自己的逻辑 (审计、拦截、改参、通知), 构成"可编排的 Agent"。

- [x] 新增 `qingxiaotuan/hooks/manager.py` + `__init__.py` (零第三方依赖): `HookManager` 从 config 读取 hooks 配置, 按事件分组, matcher 支持 glob/排除 (`*`, `|`, `!`); `run_pre` 返回 `HookDecision` (可阻断/改参), `run_post`/`run_session` 只读审计; `list_hooks` 枚举; 所有调用 emit `hook.executed` 审计事件。
- [x] 安全模型 (对标 Claude Code 但更克制): 命令**强制 list(argv), 拒绝裸字符串** → 杜绝 shell 注入; 超时强杀 (`subprocess.timeout`); 异常 fail-safe (不阻断、只记录); PreToolUse 阻断权与改参权**默认需显式声明** (`blocking` / `allow_edit_args` + 全局 `allow_blocking`/`allow_edit_args` 一键关闭)。
- [x] 接入: `tools/base.py` `ToolContext` 加 `hooks` 字段; `dispatch_result` 执行前触发 PreToolUse (可阻断/改参并同步重算影响半径与快照), 执行后触发 PostToolUse; `app.py` `create_agent` 注入 `HookManager`; `cmd_chat` 建好 agent 后跑 SessionStart、repl 退出前跑 SessionEnd。
- [x] CLI/斜杠: `qxt hooks list` (列出生效 Hooks) / `qxt hooks test <event> --tool --json` (连通性测试); `/hooks` 斜杠命令; `_HELP` 补充。
- [x] `config/defaults.py` 加 `hooks` 配置块 (`enabled`/`default_timeout`/`allow_blocking`/`allow_edit_args`/`audit_log`)。
- [x] 测试: `tests/test_hooks.py` (13 项) + `tests/fixtures/hook_{block,audit,args,timeout}.py` (纯标准库, 无网络); 覆盖解析/matcher/字符串命令拒绝/阻断(全局开/关 fail-safe)/审计/改参(授权/未授权)/超时强杀/dispatch 集成阻断返回 denied/Session 触发/配置默认值。
- [x] 验收: `pytest tests/test_hooks.py -q` → **13 passed**; 真实 CLI 分发冒烟 `qxt hooks list` / `qxt hooks test` 行为正确; 相关套件回归 53 passed (唯一失败为预存沙箱抖动 `test_cmd_undo_safe_stash`, 与本能力无关)。

## M12: 多模态视觉 (把 ModelCapabilities.vision 真正落地 — 达到 Claude Code 级别的硬缺口)

> 先机/补齐定位: `ModelCapabilities.vision=True` 早已声明, 但消息管线永远把 content 拼成纯字符串, 没有任何"挂图"入口 → 视觉是"假声明"。本能力落地真正的多模态: 用户把图片挂接进会话, 模型 (OpenAI 兼容 / Anthropic) 实际收到 image_url 块; 工具也能返回图片。并按 `capabilities.vision` **门控**——非视觉模型优雅降级为"仅记录路径", 杜绝静默失明。

- [x] 新增 `qingxiaotuan/vision/__init__.py` + `blocks.py` + `builder.py` (零第三方依赖): `ImageRef` 抽象三种图片来源 (本地文件 base64 / 远程 URL 透传 / data URI); `encode_image_source` 强制校验类型 (魔数+扩展名) 与体积上限 (默认 15MB); `build_user_content`/`build_tool_content` 按 vision 能力构造 OpenAI 风格 content 块 (不支持视觉则降级为路径注记)。
- [x] 接入: `core/agent.py` 加 `pending_images` 缓冲与 `attach_image`/`clear_pending_images`; `run()` 构造 user_msg 时按 `model.capabilities.vision` 注入 image_url 块并清空缓冲; `ToolResult` 加 `images` 字段; `_execute_single`/`_execute_parallel` 在工具返回图片且模型支持视觉时构造多模态 tool 消息 (OpenAI 兼容适配器原样透传)。
- [x] Anthropic 适配器翻译: `anthropic.py` `_convert_messages` 对 list content 做块转发, 把 image_url 数据 URI 翻译为 Anthropic image source (base64), tool 消息的 list content 翻译为 tool_result 内 image 块。
- [x] CLI/斜杠: `/image <path|url|data:URI>` 挂接、`/images` 列出待发送、`/clear-images` 清除; `_HELP` 补充。
- [x] 配置: `agent.vision_max_mb` (默认 15) 控制单图体积上限。
- [x] 测试: `tests/test_vision.py` (17 项) 覆盖编码(文件/URL/data URI)/类型与体积拒绝/builder 门控(视觉开/关)/Anthropic 翻译(用户与 tool_result)/Agent 集成(挂图发送 blocks、非视觉降级、工具返回图、拒绝非图片); 用 FakeModel 记录发出的 messages, 无网络依赖。
- [x] 验收: `pytest tests/test_vision.py -q` → **17 passed**; 相关套件 (cli/ledger/mcp/hooks/vision) 回归全绿 (唯一失败为预存沙箱抖动 `test_cmd_undo_safe_stash`, 与本能力无关)。

## 模型目录全量抓取 + 付费/免费标注 (已完成)

> 目标: 把所有支持供应商的模型全部抓下来, 保证模型名称 / API 通道 URL 无误, 并标注付费/免费。

- [x] 权威 API 直取 (免鉴权): OpenRouter `/api/v1/models` → **419 个** (21 免费), NVIDIA NIM `/v1/models` → **95 个**, Novita `/v3/openai/models` → **150 个**; 数据落盘 `_or_models.json` / `_nim_models.json` / `_novita_models.json`。
- [x] 官方文档抓取 (3 个 Explore 子代理并行): SiliconFlow / Together / Fireworks / Groq 模型清单 + 免费标注; 直连平台 (DeepSeek/OpenAI/Anthropic/Gemini/Qwen/Moonshot/Zhipu/Doubao) 当前支持模型核验。
- [x] `model_lists.py` 重构: 每个模型带 `|free` 或 `|paid` 标注; 聚合平台 50+ (OpenRouter 419 / NIM 95 / Novita 150 / SiliconFlow 93 / Together 85 / Fireworks 53 / Groq 27), 直连平台仅当前支持模型。总计 **1095 个模型**。
- [x] `provider_catalog.py`: 默认模型取清单第一项并剥离 `|free/|paid` 标注; 全部 base_url 核验无误 (OpenRouter/NIM/Novita/SiliconFlow/Together/Fireworks/Groq/直连平台)。
- [x] `commands.py`: `_pick_model_interactive` 返回剥离标注的纯模型 ID; `qxt model` 交互流程选完供应商后新增 **API Key 输入** (检测已有环境变量, 可覆盖); `qxt setup` 快速设置同步支持。
- [x] 测试: `tests/test_model_catalog.py` 重写 (OpenRouter 200+ / 付费标注 / 默认模型剥离 / 交互选择剥离), `test_cli_simplify.py` 适配新增 API Key 输入步。
- [x] 实测: `qxt model list openrouter` 显示 419 个带标注; `qxt model` 交互全流程 (分类→供应商→模型→API Key) 正常; 相关套件 54 passed。

## qxt model TUI 低饱和 Kimi Code 蓝色主题 (已完成)

> 目标: 把 `qxt model` 的终端界面改为低饱和度的 Kimi Code 风格蓝色。

- [x] 依据 Kimi Code 官方主题 token (primary=#4FA8FF / accent=#5BC0BE) 降饱和, 定义主题色: KIMI_BLUE=#5B8DEF (标题/强调)、KIMI_BLUE_DIM=#6E8CA8 (编号/索引)、KIMI_TEAL=#7FB0AE (成功/确认)、KIMI_AMBER=#C9A86A (警告)、KIMI_RED=#C77B7B (错误)、KIMI_TEXT=#D7E3F4 (正文)。
- [x] 应用到 `qxt model` 全部 TUI: 交互选择 (分类→供应商→模型→API Key)、`model list`、`model info`、`model list-providers`、`model set/switch` 成功提示; 共享的 `_pick_model_interactive` 让 `qxt setup` 的模型选择也自动获得主题。
- [x] 实测: 各子命令与交互流程渲染正常; 相关套件 54 passed。

## CLI 补全: bench cache / config validate / session list (已完成)

> 目标: 把 `qxt bench` / `qxt config validate` / `qxt session list` 三个占位命令补成可用功能。

- [x] `qxt bench cache`: 固定长对话脚本多轮实测 prompt cache 命中率 — 逐轮明细表 (轮次/耗时/prompt/输出/缓存命中/缓存未命中/命中率) + 汇总 (总 token / 命中率 / 估算成本, 复用 `router.estimate_cost`); 排除写类工具避免副作用; 无 API Key 时优雅提示并返回 1。
- [x] `qxt config validate`: 详细校验 — 供应商已知性/自定义网关 base_url、模型名非空、base_url 合法性、temperature 0~2、max_tokens 正整数、API Key 存在性、mode 枚举 (standard/yolo)、agent.effort 枚举 (low/medium/high)、max_iterations 正整数、context.budget_tokens 正数; 输出 ✓/!/✗ 分级结果, 有错误返回 1。
- [x] `qxt session list`: Rich 表格展示最近 10 个会话 (编号/相对时间/标题/消息数/会话ID), 时间近期显示相对 (刚刚/N 分钟前/N 小时前/N 天前)、更早显示绝对时间; 底部提示 `qxt session resume <编号|会话ID>`。
- [x] 测试: `tests/test_bench_config_session.py` (7 项) 覆盖校验通过/捕获错误/自定义网关 warn/时间格式化/消息计数/bench 无 Key 优雅退出/未知子命令。

## 完善: 路由失败保险回归修复 + 会话/基准细节 (已完成)

> 目标: 修复自动模型路由的失败保险缺陷, 并完善 `qxt session list` 与 `qxt bench cache` 的细节。

- [x] **路由失败保险回归修复** (`models/router.py`): `decide()` 收到显式空 `available_providers` (无任何供应商配置密钥) 时**绝不自动切换** — 此前空列表被当作「不过滤」, 路由会切到无凭证的预设模型 (doubao 等), 把请求发到未配置 API Key 的端点, 导致 e2e mock / loop / cache 三组测试全挂。新增回归测试 `test_decide_no_switch_when_no_provider_has_key`。
- [x] **`qxt session list` 尊重 QXT_HOME** (`cli/commands.py`): 不再硬编码 `~/.qingxiaotuan/sessions`, 改用 `home_dir()` (识别 `QXT_HOME` 环境变量与自定义 profile)。新增测试 `test_session_list_respects_qxt_home`。
- [x] **`qxt bench cache` 逐轮明细改为单轮增量** (`cli/commands.py`): `agent.total_usage` 是跨轮累计值, 逐轮表格改为展示本轮增量 (更符合「逐轮明细」语义), 汇总仍用总累计。新增测试 `test_bench_cache_shows_per_round_deltas`。
- [x] **`setup.wip` 文案更新** (10 语言): 完整/空白设置已实现, 该键仅剩「无效输入」分支使用, 文案从「开发中…」改为「无效选择, 请重新运行 qxt setup」。
- [x] **测试适配 Agent 重构**: `tests/test_enhancements.py` 的 fake agent 补上 `_tool_executor` (Agent 重构后 `_execute_tools` 委托给 `ToolExecutor`); `tests/test_slash_compact_cost.py` 的 `/route` 无参提示断言从「咨询式」更新为「自动路由」。
- [x] 验收: 完整套件 **520 passed, 1 skipped** (环境相关)。

## 完善: doctor 健康检查 / session delete / model test 覆盖 / bench latency (已完成)

> 目标: 补齐 CLI 运维与诊断能力 —— 让 `qxt doctor` 成为真正的健康检查, 会话可删除, 模型连通性可临时覆盖测试, 新增延迟基准。

- [x] **`qxt doctor` 全面增强** (`cli/commands.py`): 复用 `_validate_config` 做配置健康检查 (✓/!/✗ 分级), 引擎健康, 网络连通 (httpx 短超时探测 base_url, 失败仅警告), 模型连通 (复用 adapter, 失败仅警告), 工作区 git 状态; 有 err 返回 1, 仅 warn 返回 0。
- [x] **`qxt session delete`** (`cli/commands.py` + `parser.py`): 按编号 / 会话ID前缀 / `all` 删除会话, 带确认 (除非 `--yes`), 取消/未找到/删除失败均有明确反馈; 新增 `_resolve_session_target` 解析辅助。
- [x] **`qxt model test` 临时覆盖参数** (`cli/commands.py` + `parser.py`): `--provider/--model/--base-url/--api-key-env` 不写盘临时测试任意端点; 新增只读 `_ConfigOverride` 视图 (覆盖键 + 密钥 env 重定向)。
- [x] **`qxt bench latency`** (`cli/commands.py` + `parser.py`): 多轮最小请求实测响应延迟, 报告 avg/min/max/P95 + 总输出 token + tokens/s 吞吐; 无 API Key 优雅退出。
- [x] 测试: `tests/test_bench_config_session.py` 新增 12 项 (session delete 6 / model test 覆盖 2 / bench latency 2 / doctor 2), 共 **28 项全过**; 相关套件 92 passed。
- [x] 验收: 完整套件 **535 passed, 1 skipped** (环境相关)。

## UI: 取消所有高亮字体 (已完成)

> 目标: 所有终端输出统一为纯文本, 无颜色/无加粗/无 ANSI 转义。

- [x] 新增 `ui/plain_console.py`: `PlainConsole` 统一剥离 Rich 样式标记 (`strip_markup`, 不误伤 `Optional[...]`/`[web_fetch]` 等字面内容), 禁用语法高亮 (`highlight=False`), 并把 Markdown/Table 等 renderable 经无颜色渲染器转纯文本 (`render_plain` + `strip_ansi`)。
- [x] `ui/theme.py`: `C` (Rich) 与 `PT_STYLE` (prompt_toolkit) 字典全部置空, 所有 `style=C[...]` / `class:xxx` 引用退化为无样式; 新增 `blank_pt_style()` 动态覆盖 prompt_toolkit 全部 265 条默认样式规则 (补全菜单/滚动条/光标列/选中/自动补全等), 彻底禁用交互界面高亮。
- [x] `ui/mascot.py`: `_wrap` 不再添加 ANSI 颜色, ascii 帧输出纯文本。
- [x] `ui/repl.py`: 默认 console 换为 `_plain_console`; `bottom_toolbar` 移除字面 `[dim]...[/]` 标记 (prompt_toolkit 不解析 Rich 标记, 会原样显示); `PromptSession` 传入 `blank_pt_style()` 空样式表。
- [x] `ui/fullscreen.py`: FormattedText 全部使用空 class 样式, `Application` 改用 `blank_pt_style()` 空样式表。
- [x] `cli/commands.py` / `ext_cli.py` / `improve_cli.py`: 统一改用 `plain_console.console`, 表格移除 header_style, 单元格移除 `[yellow]` 等标记。
- [x] `logging_conf.py`: 终端格式化器输出纯文本 (仅级别+消息, 无颜色)。
- [x] 测试: 新增 `tests/test_plain_console.py` (7 项) 覆盖 strip_markup/strip_ansi/render_plain/PlainConsole; `test_theme.py` 新增 `blank_pt_style` 覆盖全部默认样式断言; `test_mascot.py` 断言输出不含 ANSI。
- [x] 验收: 完整套件 **578 passed, 1 skipped** (环境相关); 实测 `qxt ext engines` / `qxt improve summarize` 输出纯文本无 ANSI; force_terminal 模拟下 banner/状态栏/表格/Markdown/print_json 均无 ANSI。

## UI: 彻底清除残留高亮 + Kimi Code 唯一强调色 (已完成)

> 背景: 上一轮清理后 `commands.py` 曾从备份恢复, 把 215 处 Rich 标记与 KIMI_* 颜色常量带了回来; 且 `blank_pt_style()` 把 title/badge 也一并重置, 导致 TUI 的浅蓝强调色从未真正生效。本轮彻底清除所有残留高亮, 并让浅蓝 #4FA8FF 成为唯一强调色。

- [x] 重新剥离 `cli/commands.py` (215 处) / `ext_cli.py` (14 处) / `improve_cli.py` (13 处) 的全部 Rich 样式标记 (`[red]`/`[bold]`/`[dim]`/`[/]` 等), 删除 KIMI_* 颜色常量定义与全部引用, 修复动态样式 `[{'green' if ok else 'red'}]` 残留。
- [x] 全局扫描确认: 包内无任何 Rich 标记 / KIMI_* 常量 / ANSI 转义序列残留 (仅 `ext/ansi_engine.py` 作为 ANSI 文本处理工具引擎按需产生转义, 属功能而非高亮, 保留)。
- [x] `ui/theme.py`: 删除未使用的 12 个颜色常量 (ACCENT/BLUE/TEXT/DIM/MUTED/OK/WARN/ERR/BOX/PRIMARY/ROLE_USER/SHELL_MODE), 只保留 `ACCENT_COLOR = "#4FA8FF"` 唯一强调色。
- [x] `blank_pt_style()` 修正: 标题/徽章/提示符 (`title`/`badge`/`badge-plan`/`badge-yolo`/`prompt`/`text-area.prompt`) 保留浅蓝 #4FA8FF, 其余 (panel/log/input/bottom-toolbar/补全菜单/滚动条等) 全部显式重置为无样式。
- [x] `ui/repl.py`: 提示符 `│ > ` 经 `FormattedText([("class:prompt", ...)])` 应用浅蓝; `ui/fullscreen.py`: TextArea 提示符经内置 `class:text-area.prompt` 应用浅蓝 (TextArea 无 `prompt_style` 参数, 改用样式类)。
- [x] 实测: `qxt --help` / `qxt config validate` / `qxt ext engines` / `qxt model list` 输出全部纯文本; 合并样式解析确认 title/badge/prompt 为 #4FA8FF、panel/completion-menu 等为 default 无颜色无加粗。
- [x] 验收: 完整套件 **591 passed, 1 skipped** (环境相关)。

## 代码卫生 + 客户端限流 + 测试补齐 (已完成)

> 目标: 静态清理 + 可观测性 + 免费层保护 + 关键模块测试覆盖。

- [x] **代码卫生**: 删除 `core/agent.py` 未使用导入 (`system_prompt_hash`/`PARALLEL_SAFE_TOOLS`); 统一配置目录引用 — `cli/commands.py` 硬编码 `~/.qingxiaotuan` 改为 `home_dir()` (尊重 `QXT_HOME`), `ext/skill_market_engine.py` 技能市场目录同样优先 `QXT_HOME`。
- [x] **异常可观测**: 核心模块静默吞异常补日志 — `core/background.py` (会话流读取失败)、`core/ipc_client.py` (进程清理)、`core/ledger.py` (账本操作)、`hooks/manager.py` (钩子执行)、`config/loader.py` (配置操作), 区分 debug/warning 级别。
- [x] **客户端限流 `RateLimiter`** (`core/retry.py`): 令牌桶 (每分钟请求数) + 并发信号量, 发送前主动节流 — 与 `RetryPolicy` (被 429 打回来再退避) 互补, 保护免费层端点 (OpenCode Zen: 1 req/s, 10/min)。`sleep`/`clock` 可注入, 测试无需真等待。
- [x] **限流接入 Agent** (`core/agent.py`): `_chat_with_retry` 外包 `acquire()`/`release()`, 与重试策略协同。
- [x] **限流默认关闭 (opt-in)** (`config/defaults.py`): `model.rate_limit.enabled=False` — 付费模型无需限流, 开启后 agent 发送前主动节流。
- [x] **测试**: 新增 `tests/test_rate_limiter.py` (11 项: 令牌桶突发/回填/封顶、并发信号量阻塞与释放、配置解析、与 RetryPolicy 协同) + `tests/test_parser.py` (19 项: 子命令 func 注册、嵌套子命令必填、参数解析、`_resolve_func` 模块路由、main 分发/非 TTY 拦截); 适配 `test_opencode_zen_config.py` 两个用 `Agent.__new__` 的最小 Agent 测试补 `_rate_limiter` (禁用限流, 聚焦重试语义)。
- [x] 验收: `pytest tests/test_rate_limiter.py tests/test_parser.py -q` → **30 passed**; 完整套件 **626 passed, 1 skipped** (环境相关)。

## Quality Gates

- Python：`python -m py_compile` 与 `pytest -q`。
- 静态检查：编辑器错误检查；若加入 ruff/mypy，则纳入 CI。
- 每个核心逻辑至少一条离线测试路径。
- 不提交密钥、临时目录、未清理的 worker 或线程。