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

## Python 全栈改造 (已完成)

- [x] `qingxiaotuan/ext/` 创建 8 个纯 Python 引擎: diff, crypto, index, ansi, safety, json, search, notify
- [x] `qingxiaotuan/ext/registry.py` 引擎注册中心 + 健康检查
- [x] `qingxiaotuan/core/ipc_client.py` 重写: 包含 `ExternalEngineManager` 和 `IpcError`
- [x] `qingxiaotuan/tools/external.py` 重写: 使用正确的 `Tool(handler=...)` 接口注册 8 个外部工具
- [x] `pyproject.toml` 添加 `cryptography>=42.0` 依赖，升级到 0.2.0

## 验证结果

```
Tool OK
ipc_client OK
external OK
builtin_tool_plugins OK: 11
ENGINES: ['diff', 'crypto', 'index', 'ansi', 'safety', 'json', 'search', 'notify']
  diff: OK
  crypto: OK
  index: OK
  ansi: OK
  safety: OK
  json: OK
  search: OK
  notify: OK
ALL DONE
```

## Quality Gates

- Python：`python -m py_compile` 与 `pytest -q`。
- 静态检查：编辑器错误检查；若加入 ruff/mypy，则纳入 CI。
- 每个核心逻辑至少一条离线测试路径。
- 不提交密钥、临时目录、未清理的 worker 或线程。