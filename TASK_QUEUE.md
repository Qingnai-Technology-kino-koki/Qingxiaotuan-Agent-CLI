# QXT Task Queue

本文件是当前开发任务的唯一权威清单。每项任务必须完成实现、测试和静态检查后才能标记完成。

## Milestones

- [~] M1: 跨命令 worker 进程
  - 目标：将后台任务从 CLI daemon 线程分离为独立 worker，持久化任务元数据、状态、心跳和结果。
  - 验收：提交命令立即返回；新 CLI 进程可 `list/logs/wait/cancel`；worker 崩溃可识别并恢复或标记失败；任务文件无半写入。
  - 已完成：原子 manifest、独立 worker、心跳、失联判定、跨实例查询、持久取消请求。
  - 待完成：detached worker 集成测试，以及 Windows 进程取消与恢复验证。
- [~] M2: 全屏 TUI 工作台
  - 目标：使用现有 prompt_toolkit/Rich 能力实现面板、日志区、输入区、状态栏和快捷键。
  - 验收：TTY 中可启动；输入、取消、查看工具活动和切换面板均可用；非 TTY 保持 headless 提示；退出无残留监听器。
  - 已完成：`qxt chat --tui`、全屏布局、状态栏、活动侧栏、滚动日志、多行输入、异步提交、Ctrl+Enter/Ctrl+L/Ctrl+Q。
  - 已补充：定时吉祥物动画帧、工作状态跳动、任务互斥、Ctrl+C 协作式取消、slash 命令回调、正式 close 接口和离线测试。
  - 待完成：面板切换、真实 TTY 集成测试、实时 token/context 面板。
- [~] M3: 统一模型能力矩阵
  - 目标：统一 OpenAI、Anthropic、Gemini、OpenAI-compatible、本地模型协议，声明工具、流式、视觉、JSON 能力。
  - 验收：离线协议测试覆盖请求转换、响应转换、错误和能力声明；运行时热切换不破坏 Agent 契约。
  - 已完成：统一 `ModelCapabilities`；OpenAI-compatible/Anthropic 能力声明；Gemini provider 预设。
  - 待完成：Gemini 原生多模态转换、协议 mock 测试、视觉和 JSON 模式端到端策略。

- [~] Token 与 loop 调度优化
  - 已完成：短任务跳过语义召回、任务上下文长度上限、loop 反思间隔配置、Anthropic 工厂参数兼容。
  - 待完成：按模型真实 tokenizer 估算、工具 schema 分层注入、预算耗尽后的自动降级。
- [~] M4: 细粒度权限策略
  - 目标：对文件、网络、shell 命令、外部 MCP 建立可审计的 allow/deny/confirm 策略。
  - 已完成：统一 `PermissionPolicy` 接入工具注册表；shell 拒绝规则、网络域名允许列表、YOLO 红线和回归测试。
  - 待完成：MCP 工具资源级策略、文件路径 allow/deny、审计事件和策略配置 CLI。
  - 验收：工作区边界、命令白名单/黑名单、网络域名策略、YOLO 红线均有测试。
- [ ] M5: 任务恢复与安全取消
  - 目标：任务可从持久化状态恢复；取消能终止 worker、子进程和可取消工具调用。
  - 验收：进程重启、worker 崩溃、超时、取消场景均有离线测试。
- [ ] M6: 审计日志与查询
  - 目标：记录模型、工具、权限、任务生命周期事件，支持过滤和脱敏查询。
  - 验收：关键操作可回放，密钥和敏感参数不落盘。
- [ ] M7: 上下文与 Agent 可靠性
  - 目标：按完整 tool-call group 压缩上下文，改进循环终止、重试、错误恢复。
  - 验收：压缩后消息序列符合协议，长会话和异常路径有测试。
  - 已完成：压缩边界不会从 tool 消息组中间截断，并新增回归测试。
- [ ] M8: 真实端到端与发布质量
  - 目标：补 CLI、TUI、worker、模型 mock server 的端到端测试，完善文档和 CI 质量门禁。
  - 验收：`pytest -q`、类型/静态检查、打包安装和关键命令全部通过。

## Language Integration

- [~] Python/TypeScript 工程接入
  - 已完成：`tools.languages` 插件、工程识别、Python compileall/pytest、TypeScript tsc/test 固定质量命令、离线测试。
  - 待完成：真实 npm/tsc mock 端到端测试、配置化脚本策略和 TUI 中的质量任务面板。

## TypeScript Runtime

- [~] TS IPC 与 Agent SDK
  - 已完成：现有 `ext/ts` 工程扫描；修复 Node16/导入后缀构建错误；IPC 并发/优雅退出/输入上限；Agent SDK OpenAI SSE 流式输出与工具调用累积；插件 manifest 校验与扫描异常隔离；Python bridge 并发写锁与按请求流回调。
  - 待完成：插件宿主权限隔离、Python bridge、SDK 单元测试与真实 mock server 测试。

## Quality Gates

- Python：`python -m py_compile` 与 `pytest -q`。
- 静态检查：编辑器错误检查；若加入 ruff/mypy，则纳入 CI。
- 每个核心逻辑至少一条离线测试路径。
- 不提交密钥、临时目录、未清理的 worker 或线程。
