# Context Summary

更新时间：2026-08-23 (最新)

## 当前项目



## 本轮新增

- `core/background_store.py`：跨进程 JSON manifest，原子替换、心跳和失联回收。
- `core/background_worker.py`：独立 worker 进程入口，任务状态/会话日志/取消检查。
- `core/background.py` 与 `cli/commands.py`：detached 提交和跨 CLI 查询路径。
- `ui/fullscreen.py`：全屏工作台；`qxt chat --tui` 接入。
- `models/base.py`：`ModelCapabilities` 能力矩阵；Gemini provider 预设。
- `README.md`：同步 TUI、worker、Anthropic/Gemini 使用说明。
- 确认 `ext/ts` 工程未丢失，包含 IPC、Agent SDK、插件宿主、MCP、规则和 dashboard。
- 修复 `ext/ts/tsconfig.json` 在新 TypeScript 下的 `moduleResolution` 弃用诊断。
- 增强 `ext/ts/src/protocol.ts`：并发 handler、pending 等待、超大行保护和优雅退出。
- 修复 `ext/ts/src/agent-sdk/main.ts` 流式实现：OpenAI SSE 增量解析并通过 IPC emit 推送。
- 新增 `tools/languages.py`：Python/TypeScript 工程识别与固定质量检查。
- `ext/ts/src/plugin-host/main.ts`：增加插件 manifest 名称、版本、描述和方法名校验，目录 stat 异常隔离。
- 修复 `ext/ts/src/agent-sdk/main.ts` 与 `plugin-host/main.ts` 对新版 `HandlerCtx` 的错误传参，统一使用 `emit.emit`。
- `install.ps1`：用户 PATH 幂等写入、大小写不敏感去重、当前进程刷新和旧终端提示。
- `core/prompts.py` / `core/agent.py`：短任务跳过语义召回，复杂任务限制首轮任务上下文长度，减少 Token 消耗。
- `core/devloop.py` / `config/defaults.py`：新增 `loop.reflect_every`，默认每两轮反思，减少重复验证模型调用。
- `models/anthropic.py`：兼容统一工厂传入的 `prompt_cache` 参数。
- 修复 `ext/ts` CommonJS/Node16 配置冲突：统一 `module=Node16` 并移除源码 `.ts` 导入后缀，解决 TS5097/TS1343。
- `core/ipc_client.py`：并发发送使用独立写锁，quiet 模式消费 stderr，支持按请求注册 stream 回调，关闭路径避免写入竞态。
- 修复 `cli/commands.py` 中跨进程 `qxt bg wait` 仍调用空线程对象的问题。
- 修复 `context/manager.py` 的压缩边界，避免拆开 assistant tool call 与 tool result；新增 `tests/test_cache_compact.py` 回归测试。

## 最近验证

- 工作区文件和任务状态均保留，未发生沙箱重置。
- 相关修改通过编辑器错误检查；`py_compile` 曾通过。
- pytest 仍未获得可靠终端回显，不能宣称完整测试通过。

## 下一步

1. 为 detached worker 增加无模型集成测试，验证新 CLI 实例的 list/logs/wait/cancel。
2. 实现 Windows worker 进程终止和恢复协议。
3. 扩展 M4：文件路径/MCP 资源策略和审计查询。

## 本轮新增

- `tools/permissions.py`：统一工具权限决策，支持 allow/confirm/deny。
- `ToolRegistry.dispatch`：所有工具执行统一经过权限策略。
- 默认配置增加 shell 拒绝规则、网络域名允许列表和 YOLO 红线。
- `tests/test_tools.py`：增加 shell、网络和 YOLO 红线回归测试；聚焦测试 9 条通过。

## 本轮限制

- detached worker 的取消目前是持久化请求，worker 在轮次边界检查；模型请求或 shell 阻塞期间尚不能强制终止。

## 本轮恢复与新增

- 确认工作区未重置，上一轮代码和任务文件均保留。
- 修复 `qxt bg wait` 对 detached 任务错误调用空线程的问题。
- 新增 `tools/languages.py` 和 `tests/test_languages.py`：识别 Python/TypeScript 工程并执行固定质量门禁。
- `tools/__init__.py` 已注册 `LanguagePlugin`；README 和 TASK_QUEUE 已同步。
- 相关文件通过编辑器错误检查；完整 pytest 仍因终端无可靠回显/历史依赖环境问题未能确认。
- TypeScript 相关文件通过编辑器错误检查；`npm run build` 未获得可靠终端回显，不能宣称构建通过。
- 本轮 Python/TS/安装脚本相关文件均通过编辑器错误检查；Python `py_compile` 无错误输出，TS 构建无新的错误输出。
- 随后 `npm run build` 仅输出 tsc 启动信息且无错误，`dist` 已有编译产物；仍未获得退出码统计。
## 已完成的既有改动

- 已加入原生 Anthropic 适配器：`qingxiaotuan/models/anthropic.py`。
- 已把 `anthropic` 接入 `models/__init__.py` provider 工厂。
- 已增加 TUI `/status` 状态面板。
- 文件系统和代码工具已增加工作区边界校验；`tests/test_tools.py` 有越界回归测试。

## 本轮扫描结论

- 后台能力的核心缺陷：`BackgroundRunner` 使用 daemon 线程，CLI 退出后任务消失；新 CLI 进程无法从 `_jobs` 查询旧任务。
- `qxt bg list/logs/wait/cancel` 当前只操作当前进程内存对象。
- 需要优先建立持久化任务 manifest、worker 入口和跨进程命令协议。
- 当前环境过去出现 `ModuleNotFoundError: yaml`，并且 pip 清理临时目录异常；测试环境需要重新确认。

## 当前里程碑

- M1 跨命令 worker 进程：进行中。已实现 `core/background_store.py` 原子 manifest、`core/background_worker.py` 独立进程、Runner detached 提交、心跳/失联判定和跨 CLI 查询；待补 detached 集成测试及 Windows 取消/恢复验证。
- M2 全屏 TUI：进行中。新增 `ui/fullscreen.py`，并以 `qxt chat --tui` 接入；当前具备布局、日志、状态栏、多行输入、异步提交和快捷键，待补取消/面板切换/TTY 集成测试。
- M3 模型能力矩阵：进行中。新增 `ModelCapabilities`，OpenAI-compatible 与 Anthropic 已声明能力，新增 Gemini OpenAI-compatible 预设；待补 Gemini 原生多模态和协议 mock 测试。
- M4 48 家供应商扩展：已完成。
  - 新增 `models/provider_catalog.py` — 48 家 LLM API 平台结构化目录 (6大分类)
  - `models/__init__.py` 导入 provider_catalog, PROVIDER_PRESETS 从 12→48 家
  - `models/router.py` MODEL_PRESETS 新增 15 个模型预设 (覆盖3个tier)
  - `cli/commands.py` cmd_setup 重写为 Hermes 风格三模式引导 (快速/完整/空白)
  - `cli/commands.py` cmd_model 交互选择器重写为分类浏览+搜索+过滤
  - README.md 更新 48 家供应商描述和 setup 流程

## 工作规则

每完成一个里程碑，更新本文件的状态、关键文件、测试结果和下一步，并同步更新 `TASK_QUEUE.md`。
