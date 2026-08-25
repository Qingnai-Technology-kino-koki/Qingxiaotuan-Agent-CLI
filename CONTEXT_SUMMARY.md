# Context Summary

更新时间：2026-08-25 (最新)

## 当前项目

青小团 CLI (Qingxiaotuan Agent CLI) — 纯 Python 全栈版本

## 完整测试套件 (309 passed)

`pytest tests/ -q` → **309 passed, 1 skipped** (环境相关)。

## 新增: /compact 与 /cost 斜杠命令 (对标 Claude Code)

- `/compact` — 手动上下文压缩: `ContextManager.compact_force()` (忽略预算阈值折叠旧历史) + `Agent.compact()` + 斜杠接线。
- `/cost` — 成本估算: `router.estimate_cost()` 按内置定价表估算; 展示 prompt/completion token、缓存命中/未命中与命中率、估算费用。
- 合并了重复的 `/context` 分支, 更新 `/help` 文本。
- 测试: `tests/test_slash_compact_cost.py` (6 项) + `test_context.py` 新增 compact_force 测试。

## 修复: IpcClient 线程泄漏

- `IpcClient.close()` 现在会终止子进程、关闭管道并 join `_read_loop` 线程, 不再残留跨测试的 IPC 句柄/线程。
- `test_fullscreen_tui_close_leaves_no_threads` 改为断言"无新增线程" (更符合 M2 验收语义, 不受其他组件线程退出影响)。
- 新增 `test_ipc_client_close_reclaims_read_thread` 回归测试。

## M8 发布质量完成 (286 passed)

完整测试套件 `pytest tests/ -q` → **286 passed, 1 skipped** (环境相关)。

新增端到端测试 (全部离线, 不访问外网):
- `tests/test_e2e_mock_server.py` — 本地 OpenAI 兼容 mock 端点, 真实 HTTP 往返驱动 Agent 工具循环 (非流式/流式/流式工具调用分片累积)。
- `tests/test_cli_e2e.py` — 真实 `qxt` 子进程连 mock server 跑 headless 任务 (非流式 + 流式)。
- `tests/test_worker_e2e.py` — 真实 `background_worker` 独立进程跑任务到 done / 模型错误快速失败。
- `tests/test_fullscreen_tui.py` — 扩展到 9 项 (状态栏 token/context、日志上限、焦点循环、状态钳制)。

共享设施: `tests/conftest.py` 提供 `MockOpenAIServer` + `mock_server` fixture。

CI 质量门禁: `.github/workflows/ci.yml` — py_compile + pytest (Python 3.11/3.12/3.13)。

修复: worker 对持续模型错误空转到 max_turns 的缺陷, 改为快速失败标记 failed。

## Python 全栈改造完成

已将原先的 C + TypeScript 外部引擎全部替换为纯 Python 实现：
- `qingxiaotuan/ext/` 包含 8 个引擎: diff, crypto, index, ansi, safety, json, search, notify
- `qingxiaotuan/ext/registry.py` 引擎注册中心 + 健康检查
- `qingxiaotuan/core/ipc_client.py` 重写: 包含 `ExternalEngineManager` 和 `IpcError`
- `qingxiaotuan/tools/external.py` 重写: 使用正确的 `Tool(handler=...)` 接口注册 8 个外部工具
- `pyproject.toml` 新增 `cryptography>=42.0` 依赖, 升级到 0.2.0

### 验证结果 (全部通过)
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

## TUI 界面

已按用户要求采用 DeepSeek 风格双层框终端工作台 (`qingxiaotuan/ui/repl.py`):
- 顶部双线欢迎框 (╭─╮ + ▐█▛█▛█▌ logo + 目录/会话/模型/版本)
- 中间提示行 (✦ Try ...)
- 底部双线输入框 (╭─╮ > 用户输入 ╰─╯)
- 最底部状态栏: 目录 分支 | 快捷键提示 | context%
- 已实测: `qxt` 启动后界面正常渲染

## TUI 风格统一 (已完成)

新增共享主题模块 `qingxiaotuan/ui/theme.py` (hex 色为唯一事实来源):
- `C` -> Rich 颜色名 (repl.py 用), `PT_STYLE` -> prompt_toolkit 样式表 (fullscreen.py 用)
- `MASCOT_ICONS` 状态图标、`context_bar()` 占用条、`context_style()` 语义级 (ok/warn/err)
- repl.py 与 fullscreen.py 均已改为从 theme 导入, 不再各自维护配色

全屏版新增实时 token/context 面板:
- `set_context_info(estimated, budget)` / `context_bar(info)` (与 repl 同签名)
- 侧栏渲染 `上下文 62% [████░░░░]` + `tok 6,200 / 10,000`, 高占用变红

## 下一步

1. 运行完整测试套件 `pytest tests/ -q` (当前 294 passed, 1 skipped)
2. 继续 M1~M3 收尾验证
