# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.014] - Unreleased

### Fixed

- **LoopProviderBridge 安全降级修复** (`arch/execution.py`): `LoopProviderBridge._execute()` 不再直接调用 `registry.dispatch()` 绕过安全层, 改为通过 `agent._execute_tools()` 执行工具, 确保 ToolPipeline 5 阶段安全管线、HookManager、事务账本、MCP 安全加固全部生效。这是 P0 级安全修复。
- **ToolPipeline gate 拦截审计修复** (`arch/execution.py`): `ToolPipeline._audit()` 现在对被 gate/hook 拦截的工具调用也发布 `DECIDE` 语义事件到 SemanticBus, 保证所有安全决策 (包括拦截) 都可审计追踪。
- **系统提示词编号修复** (`core/prompts.py`): 行为准则列表中 `rules` (1-12) 与 `coding_standards` (13-20) 合并为统一的 `rules` 列表 (1-20), 消除编号跳号。
- **LoopRegistry 弃用** (`core/loop_registry.py`): 此模块标记为已弃用, 新代码请使用 `core.loop_provider.LoopRegistry`。EventPipeline 功能已由 `arch/execution.py` 的 SemanticBus + ToolPipeline 统一替代。
- **安全加固能力真正接入运行时** (`core/network_guard.py` + `core/security_plugin.py` + `core/security_bus.py`): 修复两处集成断层——(1) `tools/shell` 通过 `get_network_guard()` 全局单例做网络门控, 但此前 `security_plugin` 把配置好的守卫只注册到内核 `network_guard` 服务、未设置该全局单例, 导致域名白名单乃至新增的 CIDR 出口白名单/单向模式在 shell 执行路径上从未生效; 现 `security_plugin` 从 `security.network.egress_cidr_allow` / `one_way_mode` 读取配置并 `set_network_guard()`, 使 shell 真正受控。(2) 内核 `security_bus` 与 `_emitSecurityEvent` 使用的全局单例是不同实例, 且非交互式会话全局总线无 `persist_path`, 导致审计事件不落盘、`audit-export` 开箱无数据; 现 `security_plugin` 用全局单例作为内核 `security_bus` (与审计器订阅同一实例), 且 `get_security_bus()` 默认落盘到 `~/.qingxiaotuan/security-audit.jsonl`, 任何会话安全事件均可追溯。CIDR 出口白名单与单向模式逻辑从 `harden` 下沉到 `core.NetworkGuard`, shell 直接受益; `harden.EgressRestrictedNetworkGuard` 保留为薄封装供 CLI 使用。

### Changed

- **移除融合层开关, 功能默认生效** (`core/agent_loop_fusion.py` + `core/agent.py` + `tools/permission_fusion.py` + `context/manager.py` + `context/compaction_fusion.py` + `tools/mcp/plugin.py`): 5 个 `fusion.*` 配置开关的行为由「默认关闭, 需手动开启」改为「默认生效」:
  - **瞬时错误重试**: `retry_step` 现在始终包裹工具执行, 网络抖动/超时/限流自动指数退避重试 (此前需 `fusion.agent_loop_enhanced=True`)。
  - **续跑上限提升**: 未显式指定 `max_iterations` 时默认 40 步 (此前 20, 需开启融合层)。
  - **敏感文件纵深防御**: `build_permission_policy` 始终包裹 `FusionPermissionPolicy`, 写工具触及 SSH key/env 等敏感路径时自动升级为 `confirm` (此前需 `fusion.tools_permission_gate=True`)。
  - **head/tail/elision 上下文压缩**: 有摘要能力时默认使用「保留首尾, 省略中间」压缩形状 (此前需 `fusion.context_compaction=True`)。
  - **MCP 多传输**: `url`/`http`/`sse` 类 MCP server 默认可连接 (此前需 `fusion.mcp_multitransport=True`)。
  - **MCP 碰撞安全命名**: 工具名超长时默认走 FNV-1a 哈希截断 (此前需 `fusion.mcp_safe_names=True`)。
  - 保留 `fusion.*` 配置键用于显式禁用 (`False`), 但不再需要显式启用。

- **arch/execution.py 模块文档更新**: 明确标注 LoopRegistry/BaseAgentLoop/ReActLoop 已迁移到 `core.loop_provider`, 本模块仅保留桥接器和事件/管线抽象。

### Added

- **代码编辑助手可执行模块** (`code_edit/` + `cli/cmd_code_edit.py`): 把「代码编辑助手提示词」里可落地的机制落成真正可运行的代码（非仅文档），融合 Aider 式仓库感知/精确编辑/测试驱动验证闭环，且在原生默认行为零改动的前提下通过 `qxt code-edit` 子命令可用。
  - `task_spec`: 标准化四要素输入模板（Goal/Context/Constraints/Done when）解析、完整性校验、缺失项列举、标准化渲染；支持 `## Goal（目标）` 标题式与 `目标: 内容` 同行内联式两种写法，自动抽取涉及文件与依赖约束。
  - `safety_gate`: 把五层按钮式防误操作落成可执行策略引擎——`classify_risk` 动作定级（SAFE→CRITICAL）、`requires_confirmation`（达 HIGH 必须显式确认）、`recheck` 执行前二次校验（未提交/批量删除预警）、`audit` 审计轨迹；`check_version_lock` 版本锁死（展示旧→新、校验单调/禁止降级、主版本跃升提示破坏性）；`grant/revoke/is_valid` 权限授予带 TTL 且可随时回收。
  - `verify_loop`: 预测—反馈闭环——`run_tests` 运行并解析 pytest 风格结果，`VerifyLoop` 在失败时调用诊断回调（Agent 据此修代码）并最多重试 N 次，返回结构化 `TestResult`。
  - `rules`: 规则外置——`load_project_rules` 会话开始自动加载 `.qingxiaotuan/rules.md` / `AGENTS.md`；`UserPrefsMemory` 仅接受用户偏好类条目，写入项目事实直接抛 `ValueError`，天然满足「记忆只存偏好」。
  - `assistant`: `CodeEditAssistant` 编排器（依赖注入 ask/confirm/edit/run_test，便于测试与不同承载环境复用），串起 解析→补全→加载规则→五层闸门→委托编辑→验证闭环→结构化报告。
  - CLI 子命令：`parse`(解析简报) / `verify`(跑测试闭环) / `rules`(加载项目规则) / `check-version`(版本锁死校验) / `gate`(动作风险定级) / `edit`(解析+补全+加载规则+可选验证，输出标准化简报交 Agent 执行)。
  - `tests/test_code_edit.py` 19 项（模板解析/风险分级/确认拦截/复核预警/权限 TTL 回收/版本锁死/验证重试/规则外置/编排拦截）全过；既有融合与原生套件 102 项无回归。

### Added

- **安全加固工具集 `qingxiaotuan.harden` + `qxt harden` 命令** (`harden/*` + `cli/cmd_harden.py`): 把代码层「可对接、可验证、可审计」的安全能力做实（**不**声称国防级/军工认证——那需国密认证、HSM 实体、等保资质等代码之外的门槛）。
  - `crypto_provider`: 可插拔密码学后端 `CryptoProvider` 抽象 + `SoftwareProvider`(AES-GCM 优先/CTR-HMAC 回退，与 `ext/crypto_engine` 密文信封兼容) + `GmsslProvider`(国密 SM4，需 `pip install gmssl`) + `HsmProvider`(PKCS#11 预留接口，缺失依赖时清晰报错) + `get_crypto_provider()/available_providers()` 工厂。
  - `audit_export`: 订阅 `SecurityEventBus`，将安全事件导出为 **CEF / JSONL / RFC5424 Syslog** 三种标准格式，支持可选 HTTP 推送（超时可控、失败不反作用于安全裁决），支持从落盘 bus JSONL 批量重放。
  - `network_policy`: `EgressRestrictedNetworkGuard` 在既有域名白/黑名单之上增加 **CIDR 出口白名单**（显式 IP 字面量越界即拒、纯域名降级确认）与 **单向模式**（禁止 `nc -l`/`socat LISTEN`/`python -m http.server`/`ssh -D`/`sshd` 等入站监听），适配气隙/光闸场景。**逻辑已下沉至 `core.NetworkGuard`**，经 `security_plugin` 注入 shell 执行路径，运行时真正生效（非仅 CLI 工具层）。
  - `sbom`: 纯标准库生成 **SPDX 2.3** 软件物料清单（枚举 `importlib.metadata` 已装包 + 项目根组件），可选 CycloneDX；附 `sbom_hash` 内容指纹。
  - `repro_build`: 基于 `pip freeze --all` 生成带 `lock-hash` 的 **可复现构建锁** `requirements.lock`，并提供 `verify_lock` 依赖漂移检测（不联网）。
  - `qxt harden` 子命令: `audit-export` / `sbom` / `crypto-provider` / `network-check` / `repro-lock`。
  - 新增 `.github/workflows/security-ci.yml`：bandit 静态扫描 + pip-audit 依赖漏洞扫描 + 加固模块单测。
  - `tests/test_harden_*.py` 共 29 项全过（crypto_provider / audit_export / network_policy / sbom / repro_build）。

### Changed

- **可插拔加密后端真正驱动审计加密** (`core/security_auditor.py` + `harden/crypto_provider.py` + `core/security_plugin.py` + `config/defaults.py`): 此前 `SecurityAuditor` 使用私有 `_AeadCrypto`，新写的 `CryptoProvider`（software/gmssl/hsm 三后端）并未被它使用。现 `SecurityAuditor` 移除 `_AeadCrypto`，改用 `get_crypto_provider()` 选择后端，通过 `seal_raw`/`open_raw` 加解密审计日志；`seal_raw`/`open_raw` 与旧 `_AeadCrypto` 二进制格式**字节级兼容**（CTR 路径按 32 字节分块、域分隔符 `qxt-audit`、AES 密钥派生 `sha256(b"qxt-audit-aesgcm"+key)`，既有审计日志无需迁移即可继续解密）。`security_plugin` 从 `security.crypto.provider` 读取后端并注入审计器；`gmssl`/`hsm` 依赖缺失或不适用时降级到 `software` 并明确告警（HSM 不适用于流式审计日志，密钥不出卡，故降级）。
- **集中安全加固配置项** (`config/defaults.py`): 新增顶层 `security` 配置块（`crypto.provider` / `network.allowed_domains|blocked_domains|deny_remote_exec|deny_data_exfil|egress_cidr_allow|one_way_mode` / `mcp.*`），原 `security_plugin` 内联默认值集中于此以便发现与覆盖。
- **`qxt harden status` 子命令** (`cli/cmd_harden.py` + `cli/parser.py`): 一键展示加密后端/审计落盘/SBOM/网络策略加固态势。
- 新增 `docs/HARDENING.md`：说明各加固模块、启用方式与配置速查（回应"配置项无 schema/文档稀薄"的早期批评）。
- 新增 `tests/test_crypto_provider_raw.py`（raw 往返 + 与旧 `_AeadCrypto` 字节级兼容 + HSM 报错）、`tests/test_security_auditor_provider.py`（持久化/防篡改链/后端选型降级）；加固与集成测试共 48 项通过、1 项跳过（gmssl 未装）。

### Added

- **统一安全闸门接入主工具执行链（安全默认强制，非可选）** (`core/tool_executor.py`): 此前 `SecurityGate`/`security_auditor` 虽已实现并注册为内核服务，但 `ToolExecutor` 主工具执行链并未走它们——非 MCP 内置工具的参数从不扫红线、`write_file`/`edit_file` 没过 `decide_file_write` 闸门、`security_classifier` 注册后零消费、工具调用无统一审计记录。现**每个工具调用（shell / mcp / 文件写 / 其它内置）都经 `SecurityGate` 统一裁决（fail-closed），危险操作默认拦截**；审计溯源默认开启（拒绝与放行均留痕，依赖 `security_auditor` 内核服务，缺失时降级为基础红线扫描但绝不放开危险操作）。重构 `_execute_single`/`_execute_parallel` 走统一闸门，移除散落的重复红线/注入检查。`_is_readonly_tool` 对缺少 `get` 的注册表做容错（不再崩溃）。
- **SemanticBus 中间件 fail-closed 拦截修复** (`arch/execution.py`): 中间件返回 `None` 现被正确解释为「拦截该事件」——停止传播、不派发 handler、计入 `_blocked_count`；此前会带着 `None` 继续循环并在 `event.kind` 处崩溃。`ToolPipeline` 前置 stage 异常现 fail-closed 短路为 `DECIDE`/blocked 审计事件（与既有 hook-pre 处理一致）。
- 新增 `tests/test_security_integration_tool_executor.py`（9 项：write/edit 危险内容拦截 / mcp 危险参数拦截 / 良性工具放行 / 审计记录 deny 与 allow / 串行执行追加 denied 消息）。

## [0.2.013] - Unreleased

### Security

- **安全事件总线集成** (`tools/shell.py`): shell 安全护栏的所有关键决策点 (硬红线拦截/网络门控/分类器兜底) 现通过 SecurityEventBus 记录审计事件, 支持事后审计和实时告警。
- **MCP 注入检测集成** (`tools/mcp/plugin.py`): MCP 工具注册时自动扫描工具描述中的提示注入 (20+ 模式); 工具调用时自动扫描参数中的注入 (eval/系统调用/HTML注入); 检测到注入时标记为需确认。

### Added

- **安全子系统内核插件** (`core/security_plugin.py`): 将 security_classifier / network_guard / security_bus / mcp_security_guard 统一注册为内核服务, 通过 kernel.require() 依赖注入, 替代全局单例。
- **协作子系统内核插件** (`core/collaboration_plugin.py`): 将 CollaborationProtocol / 角色注册表 / 结果聚合器工厂 注册为内核服务。
- **DevLoop V2** (`core/devloop.py`): 新增 `run_with_roles()` 方法, 集成 TaskDAG 依赖调度 + 专业化角色分配 + 协作黑板 + 结果聚合 + 矛盾检测 + 强模型验收。
- **dispatch_tasks V2** (`tools/dispatch.py`): 新增三种调度模式: parallel (默认, 带结果聚合) / dag (依赖图分波调度) / role (角色化并行); 新增参数 mode / task_roles / task_dependencies。
- **MCP 多传输融合层** (`tools/mcp/multitransport.py` + `tools/mcp/plugin.py`): 把已测试通过的 MCP 多传输实现**增强**进原生 CLI——原生 stdio(`command`) 路径行为零改变；仅当 `fusion.mcp_multitransport` 开启时，额外接管 `url`/`http`/`sse` 类 server，复用该层自研的 http/sse 异步客户端 + `McpConnectionManager`(连接状态/重连) + `qualify_mcp_tool_name`(>64 字符 FNV-1a 哈希截断防工具名超限) 碰撞安全命名。融合层经独立事件循环线程暴露为与原生一致的同步 `Tool` 注册接口，`register_mcp_tool` 与原生共用同一安全策略(黑名单/注入扫描/确认)。`fusion.mcp_safe_names` 可让原生 stdio 也走碰撞安全命名（默认关以保兼容）。`tests/test_mcp_multitransport.py` 9 项（配置识别/结果格式化/命名截断/注册+调用全链路/状态/禁用过滤）与 `tests/test_mcp.py` 11 项原生测试全过。
- **ACP 增强融合层** (`acp/version.py` + `acp/protocol.py` + `acp/server.py` + `cli/cmd_acp.py`): 本层把 ACP 的**增强**能力设计**叠加**到原生 ACP server，**原生单会话行为零改变**（默认 `fusion.acp_enhanced=False`）。具体融合点：①协议版本协商 `version.negotiate_version`（无 `protocolVersion` 的旧客户端回退当前版本 1，不破坏兼容）；②`protocol.LineBuffer`/`parse_frame`/`read_messages_robust` 增量字节帧 + 错误容忍解析（畸形帧跳过而非中断 stdio 主循环），仅 enhanced 下替换原生 `read_messages`；③`_on_reason` 富事件把模型推理过程作为 `agent_thought_chunk` 流式回报；④`permission_request` 在 enhanced 下附带审批选项集 `approve_once/approve_always/reject/plan_review`，并与 ACP 语义对齐（`approve_once`/`approve_always`/`plan_review` 视为放行，`reject`/`denied` 视为拒绝）。`tests/test_acp_enhanced.py` 12 项（版本协商/帧解析/健壮读取/富事件/审批选项集）与 `tests/test_acp_server.py` 5 项原生测试全过。
- **工具权限融合层（敏感文件纵深防御）** (`tools/permission_fusion.py` + `tools/base.py` + `tools/__init__.py`): 集成纵深敏感文件检测（`tools/args.is_sensitive_file`）**叠加**进原生 `PermissionPolicy`，而非替换（融合接缝 `build_permission_policy` 取代 `base.py` 中的 `PermissionPolicy(cfg)`）。原生仍是唯一决策源，本层仅做 defense-in-depth：**原生 deny/confirm 一律尊重（绝不降级）**；仅当工具为写/改类（`not read_only`）且任一参数命中敏感文件纵深判定（env/凭据/SSH key 等）时，把原生 `allow` **升级为 `confirm`** 要求人工确认。默认 `fusion.tools_permission_gate=False`：**完全等价原生，零行为改变**（纵深检测依赖仅在开关开启且确实命中时才惰性 import，保证冷启动干净）。`tests/test_permission_fusion.py` 9 项（关闭透传/敏感升级/嵌套路径/读类不升级/deny 不降级/confirm 透传/工厂行为）全过；既有 `test_permission_rules.py`/`test_security_hardening.py` 无回归。
- **上下文压缩融合层（head/tail/elision）** (`context/compaction_fusion.py` + `context/manager.py` + `core/agent.py`): 以 head/tail/elision 上下文压缩形状（head 保留最旧 / tail 保留最新 / 中间 elision 省略 / 末尾 summary 摘要）**叠加**到原生 `ContextManager`，与原生「保留最近 + 折叠中间」策略并存、由 `fusion.context_compaction` 开关切换（默认 False，原生行为零改变）。融合层以「消息组」为单位选取 head/tail，绝不拆分 `assistant(tool_calls)` 与其 `tool` 结果，保证协议合法；`system` 前缀永远置首不动。`compact_head_tail_elision` 为纯函数（原生 dict 形态，无需上下文对象转换），可独立测试。`tests/test_compaction_fusion.py` 6 项（形状/system 保留/工具组不拆分/未超预算原样/head+tail 保留/token 估算）全过；既有 `test_context.py` 12 项无回归。
- **Agent loop 融合层（续跑/重试原语）** (`core/agent_loop_fusion.py` + `core/agent.py`): 把「续跑 (continuation / LoopControl)」与「瞬时错误重试 (step_retry)」控制原语**叠加**到原生主循环，原生默认零改变（开关 `fusion.agent_loop_enhanced=False`）。①`resolve_max_iterations` 开启时把每轮对话的迭代上限从 `agent.max_iterations`(默认 20) 提升为 `fusion.agent_loop_max_iterations`(默认 40)，实现复杂任务一轮自动续跑更多步（显式 `max_iterations` 仍优先，融合只提升默认上限）；②内联 ReAct 循环开启时对工具执行外包裹 `retry_step`，以指数退避重试「瞬时」异常（网络抖动/超时/限流），非瞬时异常立即上抛。`classify_transient` 关键字分类、`should_continue` 续跑谓词均为纯函数。`tests/test_agent_loop_fusion.py` 9 项（瞬时分类/重试瞬时/不重试非瞬时/全失败上抛/续跑上限/谓词）全过；原生主循环在开关关闭时行为完全不变。

## [0.2.012] - Unreleased

### Security

- **GuardFall D类绕过防御** (`ext/safety_engine.py`): 新增 Base64 管道检测 (`_is_base64_pipeline_dangerous`), 拦截 `echo payload | base64 -d | sh` 等编码管道绕过; 尝试解码 Base64 内容并对解码后文本做红线检测; 即使解码失败也保守拦截。
- **独立安全分类器** (`core/security_classifier.py`): 参考 Claude Code 分类器架构, 新增基于语义特征的独立评估层; 14 类特征提取 (编码/网络/权限/隐藏操作等) + 组合风险加分; 与 safety_engine 联合决策: 规则层优先, 分类器兜底。
- **网络出口门控** (`core/network_guard.py`): shell 命令的网络出口做分级拦截; 检测数据外泄通道 (编码+管道到网络)、域名黑名单 (Pastebin/ngrok 等匿名上传)、端口扫描、远程执行; 写操作+网络 = 高风险拦截。
- **MCP 安全加固** (`tools/mcp/security.py`): 工具描述注入检测 (20+ 提示注入模式); 参数消毒 (检测 eval/系统调用/HTML 注入); 可疑 URL 检测; 审计日志。
- **安全事件总线** (`core/security_bus.py`): 统一安全审计流; 所有安全决策汇聚到事件流; 实时告警 + 统计分析 + 事件溯源。

### Added

- **专业化角色系统** (`core/specialized_roles.py`): 7 种预定义角色 (reviewer/tester/debugger/architect/security_auditor/documenter/implementer); 每个角色有专属系统提示、推荐工具集、质量标准、协作提示; 基于关键词的任务角色推荐。
- **任务依赖图** (`core/task_dag.py`): DAG 表示 + 关键路径分析 + 动态调度 + 失败重试 + 死锁检测; 支持 from_plan 从规划结果自动构建。
- **协作协议** (`core/collaboration_protocol.py`): 结构化消息 + 消息路由 + 版本控制 (CAS) + 订阅/通知 + 冲突检测与解决; 升级版共享黑板。
- **结果聚合器** (`core/result_aggregator.py`): 去重 + 矛盾检测 + 质量评估 + 优先级合并 + 按角色分组输出。
- **自研通用 Provider 抽象层** (原 `qingxiaotuan/kosong/`, 现归入 `qingxiaotuan/kernel/`): 接口对齐 Kimi Code `kosong` 大模型抽象层——契约层 (`contract.py`: Message/Tool/ToolCall/ContentPart/Usage/StreamedMessage/FinishReason + ChatProviderError 错误层级 + classify_api_error)、顶层 `generate()` 流式聚合器 (`generate.py`: 协议无关，分片合并 + tool_call 增量拼接 + 空响应校验)、provider 实现 (`providers/`: OpenAI chat/completions、OpenAI Responses、Anthropic、Kimi，均用 httpx 直连零额外依赖)、轻量 `ProviderService` 注册表 (DI 等价物，按 ProviderConfig 选协议/厂商)。含参考版本 0.29→0.39 缺陷修复落地：`prompt_cache_key` 在 nvidia provider 请求发出前被强制剔除（带空值保护，不改变其它 provider 结构）。
- **KosongModelAdapter 桥接** (`models/kosong_adapter.py` + `models/__init__.create_adapter`): 新增 `model.backend=kosong` 开关，把现有 `ModelAdapter` 接口委托给 kosong `ChatProvider`，使现有 `core/agent.py` 与 `cli/cmd_chat.py` 无缝用上 Kimi 风格 provider 架构；默认保持 legacy 后端不破坏现状。配套 `tests/kosong/` 20 项测试（契约/聚合器/ProviderService/nvidia 修复/桥接/真实 SSE 集成）全过。
- **自研 Agent 循环原语层** (归入 `qingxiaotuan/kernel/agent/`): 接口对齐 Kimi Code 多步代理循环设计——`AgentLoopService.run` 驱动 step 提交/执行/续跑；`step_request`/`step_queue` 提交队列、`config.LoopControl` 控制 max_steps/continuation、`continuation` 自动续跑、`step_retry` 失败重试、`errors` 错误层级 (LoopError / max_steps_exceeded)；`bridge.KosongAgentLoopAdapter` + `run_agent_loop` 把新循环接入现有 `Agent`。
- **自研工具契约 + 权限层** (归入 `qingxiaotuan/kernel/tools/`): `contract.ExecutableTool` 工具契约 + `ToolAccesses` 读写冲突检测；`registry` 注册表、`scheduler` 调度（并行读取 / 串行写入的 asyncio 协调）、`executor.ToolExecutor` 执行器；`permission.PermissionPolicy` 策略链 + `gate.PermissionGate` 准入门（统一接入 `before_execute_event` 挂点）；`bridge.wrap_existing_tool` 把现有青小团工具包装为 kosong 可调度工具；`args` 参数解析。
- **自研会话 + 压缩层** (归入 `qingxiaotuan/kernel/session/`): `contracts.ContextMessage`/`OriginKind` 上下文契约；`memory.ContextMemory` 上下文内存；`wire.WireStore` append-only JSONL 持久化 + replay 重放；`compaction` 压缩（head/tail/elision 选区 + `FullCompaction` + `estimate_tokens` 估算）；`strategy.DEFAULT_COMPACTION_CONFIG` 默认压缩策略；`projector` 投影、`bridge` 桥接。
- **自研 MCP 客户端层** (归入 `qingxiaotuan/kernel/mcp/`): `MCPClient` 协议 + `config`；`naming.qualify_mcp_tool_name` 工具名限定（FNV-1a 8 位哈希防冲突）；`client_stdio`（asyncio.subprocess + JSON-RPC 行帧）/ `client_http` / `client_sse` 三种传输；`connection_manager` 连接管理、`tool`/`create_mcp_tool` 工具桥、`registry_integration` 注册集成、`bridge` 桥接。零额外依赖。
- **自研 ACP 协议层** (归入 `qingxiaotuan/kernel/acp/`): `codec` NDJSON `LineBuffer` 行帧、`version` 协议版本协商、`protocol.SessionUpdate` 构造器、`convert` 转换、`events_map` 工具调用懒创建、`session.AcpSession.prompt`、`server.AcpServer.serve_stdio`、`interaction_bridge` 交互桥、`handle_approval` 审批、`bridge` 桥接——使青小团可被 IDE 经 NDJSON-JSON-RPC stdio 驱动。
- **抽象层全量测试与集成验证**: 本轮新增 `tests/kosong/{agent,tools,session,mcp,acp}` 共 62 项（agent 10 / tools 8 / session 17 / mcp 10 / acp 17），连同第一轮 20 项总计 82 项全过；所有新模块 `py_compile` 通过、顶层 `import qingxiaotuan.kosong.{agent,tools,session,mcp,acp}` 冒烟通过，未破坏现有 legacy CLI（`model.backend='kosong'` 默认关闭）。

## [0.2.011] - Unreleased

### Security

- **统一失败即拒绝安全闸门** (`ext/security_gate.py`)：所有副作用入口（shell、MCP 工具、文件写入、远程 prompt、自我改写、cron、skill）统一经过一个 fail-closed 闸门；任何异常或未知一律拒绝。整合 `safety_engine` + `security_policy` + `rules_engine`。
- **crypto 引擎失败即拒绝**：`CryptoEngine.open()` 现在强制校验 HMAC（不再可选 / fail-open），并新增 PBKDF2 迭代次数上限以防护 DoS。
- **远程控制加固**：配对令牌单次使用（确认与断开后失效）、绑定设备指纹、确认尝试锁、所有远程 prompt 经安全闸门分类（硬红线命令直接拒绝、非良性命令需确认）。
- **MCP 沙箱执行闸门**：执行前经安全闸门拦截硬红线；本地回退执行剥离 secret 类环境变量。
- **工作区信任强制执行**：`workspace_trust` 接入 shell 守卫（opt-in），untrusted/unknown 工作区无法获得无限制 shell。
- **规则引擎执行 + ReDoS 防护**：新增 `enforce()`（错误级违规 ⇒ 文件写入被拒）；正则编译加入灾难性回溯 / 超长模式防护。
- **沙箱环境脱敏**：`core/sandbox.run_in_sandbox` 默认剥离 API Key / secret 类环境变量，防止密钥泄漏到沙箱子进程。
- 版本回落：依据 VERSION_POLICY.md，从非合规的 `0.2.1` 回落并 `+0.0.001` 命名为 `0.2.011`。

### Changed

- 版本号回落并依据 VERSION_POLICY.md 重新命名为 `0.2.011`（原非合规 `0.2.1`）。

### Added

- **Safety-first agent core**: static risk scoring before every shell execution — critical-level commands are blocked by default; unified red-line rules (recursive `rm`, force-push, Windows `rd /s`, **system shutdown/reboot**, **chmod -R 000 /**, **chown -R root /**) shared by the safety engine, YOLO mode and code tools. HIGH patterns expanded: `git clean -f`, `docker rm -f`, `kubectl delete`, `iptables -F`. MEDIUM patterns expanded: `systemctl stop`, `pkill`, `killall`, `chmod 000`, `chown root`.
- **`/log` slash command**: show recent tool call history from the session event stream (`/log [N]`, default 15).
- **`/impact` enhanced**: now shows file type distribution, tool usage distribution, and a visual bar chart alongside the traditional file list and timeline. Added time-density histogram (operations per 5-minute bucket) and compact summary line.
- **Verify Loop (编码验证闭环)**: new `core/verify_loop.py` engine auto-detects project type (Python/Node/Rust/Go), infers test/typecheck/lint commands, runs them in sequence, and feeds failures back for self-healing (max N rounds). New `/verify` slash command (`/verify check` for one-shot, `/verify heal` for self-healing loop). Configuration via `verify.enabled/auto/max_heal_rounds/checks` in config.yaml.
- **Parallel background tasks**: `BackgroundRunner.submit_parallel(tasks)` atomically submits multiple background tasks; `status_all()` aggregates status of all jobs. Foundation for multi-agent parallel workflows.
- **MCP mainstream server docs**: comprehensive integration guide in `mcp-integration.md` skill covering 8 mainstream MCP servers (filesystem, GitHub, Brave Search, SQLite, PostgreSQL, fetch, memory, Puppeteer) with ready-to-paste config snippets.
- **Plan mode**: read-only enforcement with fail-closed semantics — unknown commands are denied rather than silently allowed.
- **Self-improvement loop**: post-execution review distills guardrails that take effect before the next dispatch.
- **9 pure-Python external engines** behind an in-process registry (`qxt ext selftest`): diff, crypto, index, ansi, safety, json, search, notify, rules — zero compilation, zero IPC overhead. (注：`skill_market` 曾规划但未接入注册表，当前可运行引擎数为 9。)
- **Model layer**: OpenAI-compatible protocol plus an Anthropic adapter, runtime hot-swap router and provider catalog; model-neutral by design.
- **Memory**: three-tier memory on SQLite FTS5 plus an append-only session event stream.
- **Cron subsystem**: persistent jobs, detached daemon (`qxt cron start --detach`), desktop notifications, output-to-file for CI consumption, per-job logs.
- **Swarm multi-agent collaboration** (`/swarm`): strong-model planner → concurrent sandboxed weak-model workers → strong-model acceptor, communicating over a shared blackboard.
- **RetryPolicy component**: exponential backoff with jitter, `Retry-After` aware, fast-fail on auth errors, injectable sleep/rng.
- **Client-side rate limiter** (`RateLimiter`): token-bucket throttling + concurrency semaphore applied before every model request — protects free-tier endpoints (e.g. OpenCode Zen) from tripping server-side limits. Opt-in via `model.rate_limit.enabled` (default off, so paid models are unaffected); injectable sleep/clock for testing.
- **Circuit breaker** (`CircuitBreaker`): the third resilience layer for model calls, complementing `RetryPolicy` (backoff-retry) and `RateLimiter` (pre-throttle). After `failure_threshold` consecutive failures it opens and fails fast for `cooldown` seconds instead of burning retry budget against an already-dead upstream (saves tokens/time). Half-open probe recovers after `success_threshold` consecutive successes. Wired into `Agent._chat_with_retry` as an opt-in, config-gated layer (`agent.circuit_breaker.enabled`, default off → zero behavior change); injectable clock for testing.
- **`/status` resilience panel**: the in-session `/status` command now surfaces the three resilience layers via `Agent.resilience_status()` — circuit breaker (enabled + `closed`/`half_open`/`open` state with threshold/cooldown, `open` rendered in red), client rate limiter (req/min + concurrency) and retry policy (max retries, backoff, retry-on status codes) — so operators can confirm whether the protections are active and whether an upstream is currently tripped, all without leaving the session.
- **Agent-loop observability (telemetry)**: the pre-existing `TelemetryCollector` (`core/telemetry.py`) is now wired into the agent loop — every `run()` opens a trace, each model call emits a `model.chat` span (latency + token usage + breaker-state event) and every tool call emits a `tool.<name>` span (latency + success/failure), with read-only/write tools recorded consistently including the parallel-readonly path. The collector is thread-safe (locked) for the concurrent tool executor. Opt-in via `observability.telemetry.enabled` (default off → zero behavior change, zero overhead). A new `/stats` slash command surfaces the accumulated trace/span counts, error rate, average model/tool latency and token totals, so operators can watch live performance without leaving the session.
- **Audit logging** of tool calls and risk decisions.
- **Terminal UI**: fullscreen workbench, DeepSeek-style dual-frame REPL, dynamic mascot state machine (idle/thinking/working/alert/done), live token/context meters, shared theme module as the single source of color truth.
- **Plain-text terminal output**: every CLI command prints pure text — no colors, no bold, no ANSI escapes — via a `PlainConsole` that strips Rich markup and syntax highlighting; the interactive TUI keeps a single Kimi Code–style accent color (`#4FA8FF` light blue) for titles, badges and prompts, with all other prompt_toolkit default styles explicitly reset.
- **Fast cold start**: heavy SDKs (`openai`, `httpx`, `anthropic`) and command modules are lazy-imported, cutting `qxt --help` / `--version` and REPL startup from ~4.3s to ~1.7s (imported modules 1607 → 664).
- **CLI surface**: ~20 slash commands, `qxt doctor` health check, `qxt bench`, `qxt open file:line` precise-reference jump, four-layer config merge with profiles.
- **Skills system** with loading, distillation and a marketplace engine.
- **Hooks event surface**: 8 lifecycle events (PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStop, PreCompact, SessionStart, SessionEnd) with shell-command hooks configurable per event; PreToolUse can deny tool calls, UserPromptSubmit can inject context.
- **Checkpoint / rewind**: a `checkpoint` session tool — `save` marks the conversation position and snapshots pending file mutations via the mutation ledger, `restore` undoes every file change since the mark and truncates the dialogue back to that point, `list` shows saved checkpoints. Enables "try a risky edit, roll back in one step" workflows inside one session.
- **Layered instruction memory** (对标 Claude Code 的 CLAUDE.md 分层): three-tier discovery — user-global (`<qxt_home>/AGENTS.md` and the community-standard `~/.agents/AGENTS.md`), project ancestors (each directory from the workspace up to the nearest git repo root, never escaping the repository boundary), then the workspace root with candidate priority QXT.md > AGENTS.md > CLAUDE.md > .qxt.md. Byte-stable output per environment, prompt-cache friendly.
- **Typed subagent delegation** (`task` / `background_status` tools, 对标 Claude Code 的 Task 工具): the main agent delegates a single self-contained task to an isolated typed sub-agent — four built-in types (`general-purpose`, `explore`, `plan`, `coder`) each carrying a role directive appended to the sub-agent's system prompt (`AgentType.system_extra`, threaded through thread isolation and the process sandbox) and, for read-only types (`explore` / `plan`), a physical write block via `exclude_tools=readonly_tool_names()` that also excludes `task` itself to prevent recursive spawning. Foreground mode returns the aggregated result block; `run_in_background=true` returns a job id immediately with progress/results queryable later through `background_status` (runner instance cached on the tool context so submit and status share one registry).
- **Internationalized README** in 10 languages with consistent content across translations.
- **Interface i18n (10 UI languages)**: lazy-loaded locale modules (简体中文/繁體中文/English/日本語/한국어/Español/Português do Brasil/Français/Deutsch/Русский) with a current→zh-CN→en→key fallback chain that never raises on missing keys, alias normalization (`zh_CN`/`chs`→`zh-CN`, `jp`→`ja`, `pt-br`→`pt-BR`, …), a first-run numbered language picker (interactive TTY only — never blocks tests/CI/pipes), and reply-language injection into the agent system prompt (zh-CN keeps the historical prompt byte-for-byte for prompt-cache friendliness).

- **kimi-code TUI (`--tui`)**: the interactive REPL now runs on `KimiTUI` — a faithful Python/prompt_toolkit port of kimi-code's actual terminal UI (no redesign, used as-is). Uses kimi's original dark palette (`#4FA8FF` primary, `#FFCB6B` user bullet), the two-line status bar (mode badges · model · cwd · git · rotating tips / `context: N%`), kimi's exact symbols (`✨` user, `●` assistant, `✓ done` / `✗ failed` tools), and the moon-phase thinking spinner (`🌑…🌘` @120ms). Streaming, live tool status, shell mode (`!`), plan mode (Shift-Tab), external editor (Ctrl-G) and `@`-file completion are wired through `on_token` / `on_tool` / `on_tool_result` hooks.
- **GitHub fusion — `qxt gh borrow`**: the CLI can now autonomously find open-source / similar projects to *borrow patterns* (not wholesale copy) with mandatory attribution. `qxt gh borrow "<task>"` searches GitHub repos/code via the `gh` CLI (REST API fallback through `httpx`), returns ≤20 KB read-only snippets with SPDX license + source links, and embeds a "borrow pattern, not copy" pledge. Also exposed as the `/gh-borrow` slash command and a built-in skill (`resources/skills/builtin/gh_borrow.md`) so the agent proactively consults OSS before reinventing.
- **归档 TS 参考工作区改名 (`qingxiaotuan-cli`)**: 由 kimi-code 官方 TS 参考树派生的私有工作区（仅供作者自身的 TS 侧工作使用），已统一改名并归档至 `12345/`——包名 `@moonshot-ai/kimi-code` → `@qingxiaotuan/cli`、内层应用 `apps/kimi-code` → `apps/qingxiaotuan-cli`、bin `kimi` → `qingxiaotuan-cli`、根 monorepo `@qingxiaotuan/monorepo` 与 pnpm workspace override 均已改指；独立发布的 `@moonshot-ai/kimi-code-sdk` / `-oauth` 保留下游引用。该参考树与 Python CLI 主产品相互解耦，仅作对照存档，不参与主产品构建。
- **ACP (Agent Client Protocol) server — `qxt acp`**: 自研的 ACP (Agent Client Protocol) 服务端（纯 Python），接口对齐 kimi-code 的 IDE 集成设计，使青小团可被 IDEs (VS Code / Zed / JetBrains) 经 NDJSON-JSON-RPC stdio 驱动。 `initialize` returns session/agentInfo/model/tools/slash_commands/workspaceFolder and broadcasts `session/update`(initialized) + `available_commands_update`; `prompt` runs the existing `Agent.run` loop asynchronously and streams `session/update`(agent_message_chunk / tool_call / tool_call_update / permission_request) and `task/update`(running/completed/failed); dangerous operations hand-shake with the IDE via a blocking `permission_request` → `update`(permission_response); `cancel` and `shutdown` are wired to `agent.cancel()`. Reuses the real `Agent` via dependency injection (no Node dependency). Verified by `tests/test_acp_server.py` (5 tests: initialize/prompt-approval/denial/cancel/confirm).

### Changed

- Full stack is now pure Python: the legacy C engine sources and Node/TS build chain were removed from the repository in favor of in-process engines.
- Config directory references unified through `home_dir()` (respects `QXT_HOME`); skill-market registry also honors `QXT_HOME`.
- Silent exception handlers across core modules (background, IPC, ledger, hooks, config loader) now log at debug/warning level instead of swallowing errors.
- REPL banner/status bar/tips, fullscreen workbench states and hints, keymap panel and the `qxt setup` wizard all render through the i18n catalog; agent chat replies follow the configured language. Deep engine logs and raw tool output remain Chinese for now.
- Version rolled back from non-compliant `0.2.1` and renamed to `0.2.011` per VERSION_POLICY.md (one `+0.0.001` step for the security hardening above).

### Fixed

- `ManagedSettings._deep_merge` no longer mutates the global `DEFAULT_CONFIG` in place — environment overrides are deep-copied, eliminating a process-wide contamination bug where any `QXT_*` env override corrupted default config for every later `Config()` (this was the root cause of 4 routing/plan tests failing only when run after unrelated tests).
- Rules engine `matches` / `regex_contains` had reversed argument order (subject treated as the regex pattern) and crashed on regex metacharacters in the subject — now both take `(subject, pattern)` correctly and compile defensively. Added regression tests.
- Code tool `codebase_indexer` registration no longer raises `PluginError`: the tool now `unprovide` the placeholder before `provide`-ing the real indexer (mirroring the app path).
- Cold-engines crypto test updated for the mandatory-nonce CTR stream cipher fix.
- `test_ui_folding` no longer hard-codes the `master` branch (uses the repo's actual default branch).
- `test_plan_execute` leaked a module-level `pytest.MonkeyPatch()` across the whole session — replaced with the `monkeypatch` fixture.
- Safety engine now has cold-start unit tests (12 direct-instantiation tests for CRITICAL/HIGH/MEDIUM patterns, analyze batch, normalization, envelope) and IPC subprocess smoke tests (5 via ExternalEngineManager), bringing safety-engine test coverage from 30 (integration-only) to 47 (unit + IPC + integration).
- YOLO red-line detection no longer depends on safety-engine availability — a local fallback keeps blocking lethal commands when the engine is missing (fail-closed).
- Plan-mode readonly check no longer fails open on unknown commands; `sed -i`-style writes are now detected.
- Windows cron daemon liveness check no longer risks signaling unrelated processes via `os.kill(pid, 0)`.
- **Safety guard decoupled from UI locale**: the shell pre-exec guard now records a language-independent `ctx.safety_severity` (`critical`/`high`/`medium`/`none`), and the regression tests assert on it instead of localized message substrings — CI no longer flips red under a non-English locale. Added `tests/test_diff_engine.py` and expanded `tests/test_shell_safety_guard.py` / `tests/test_security_v2.py` with regression coverage for the fixes below.
- **`diff_engine` fixes**: `merge3` no longer resets the offset between the theirs/ours passes (both-sided non-conflicting changes were misaligned); `diff().changed` now reflects actual add/remove instead of being permanently `True`.
- **`_expand_globs` uses a quote-aware tokenizer** so glob expansion no longer breaks on paths containing spaces (e.g. the project directory name).
- **Two-tier critical model**: `is_hard_redline` (filesystem/OS destruction — never auto-executed, even with confirm) is now distinct from `is_redline` (comprehensive danger detector, still used by the MCP guard and script-content checks). SQL destructive ops (`DROP`/`DELETE`/`TRUNCATE`) moved to a *confirmable* critical tier (extreme 5-stage confirm, fail-closed under YOLO / no-confirm-channel) so the shell guard's confirm path is reachable and coherent.
- **`diff_engine.patch` context validation**: the unified-diff applier previously assumed every context line matched and silently returned `applied: True` even when the patch did not correspond to the source (a "最小影响半径" hazard — wrong patches could be written without warning). It now verifies each context line against the source and fails closed (`applied: False` with a precise line/offset error) on context mismatch or a source shorter than the hunk expects. Added `test_patch_*` regression tests.
- **Index cache scoped by workspace**: `ext_index_build`/`ext_index_query` no longer share one global index slot; the cache is keyed by `ctx.workspace`, so switching workspaces cannot surface a stale prior index. Added `test_index_cache_scoped_by_workspace`.
- **Tool-result cache keyed by workspace**: `ToolResultCache` (used by every cacheable read tool — `read_file`, `search_files`, `ext_index_query`, …) now folds `ctx.workspace` into its key, preventing one workspace from being served another workspace' cached result. Added `tests/test_tool_cache.py`.
- **Test fixture fix**: `test_mcp_tool_bridge`'s `FakeClient` now supplies a `security_policy` stub matching the real `MCPClient` contract (pre-existing test/code drift that raised `AttributeError` on `client.security_policy`).
- **Env cleanup**: removed the stray repo-root `.env` (all-comment placeholder, never referenced — code loads `~/.qingxiaotuan/.env` via `load_dotenv`). Refreshed `.env.example` to include `OPENAI_API_KEY` and `QXT_HOME` and kept variable naming consistent (`<PROVIDER>_API_KEY` + `QXT_API_KEY` fallback). Confirmed `.gitignore` ignores `.env*` while tracking `.env.example`.
- **Safety false-positive reduction (降误杀)**: `safety_engine.score()` and `whitelist.get_warning_level()` now short-circuit to `none`/`0` for benign dev commands — file read/write, `git` ops, package management (`pip`/`npm`/`pnpm`/`poetry`/`cargo`/`go`), test runs (`pytest`/`unittest`), lint (`ruff`/`black`/`mypy`/`eslint`/`tsc`) — so common commands are neither blocked nor repeatedly confirmed. Irreversible ops (`rm -rf`, force push, `dd`, `mkfs`, `shutdown`) and system-path writes (`/etc`, `/usr/lib`) are excluded from the benign set and still blocked by the hard red line. The benign-recognition unit tests live in `tests/test_shell_safety_guard.py` (merged from the earlier standalone `tests/test_safety_benign.py`, which is now redundant and pending removal).
- **Provider-agnostic core link proven**: extended `tests/test_e2e_mock_server.py` to drive `run_shell` through the full `chat → tool → safety` loop (a benign `echo` passes the guard) and to cover 5xx retry via `RetryPolicy` (mock returns 503 twice, then 200). This verifies the chain across **any** OpenAI-compatible provider at the transport level, not just one model.
- **`openai` SDK is lazy / optional**: module load never imports `openai`; it is imported only when a request is actually sent. `pyproject.toml` now lists `openai` under an optional `openai` extra, so non-OpenAI paths (Ollama / offline / custom adapters) run without it. Added `tests/test_entrypoint_and_lazy_openai.py` verifying the `qxt` entry point resolves (`qingxiaotuan.cli:main` → `cli/__init__.py` → `parser.main`) and that `openai` is not imported at module load.
- **Docs**: added `VERSION_FAQ.md` (why we stay at 0.x, release-timing criteria, breaking-change handling) and `SUPPORTED_MODELS.md` (provider/model compatibility matrix + offline reproduction commands); linked both from `README.md` / `README_zh-CN.md`.

### Security

- Safety red-line coverage broadened: added `wipefs`, `shred`, fork bomb (`:(){ :|:& };:`), `diskpart`, `cipher /w`, `takeown /f /r`, `bcdedit`, `reg delete`, raw-disk redirect (`> /dev/sd*`), and volume/partition destruction (`lvremove` / `zfs destroy` / `parted rm`) to the critical set; added `crontab -r`, `truncate -s 0`, `systemctl mask/unmask` to the high set. Normalization now also unwraps ANSI-C quoting (`$'...'` / `$"..."`) to defeat `$'rm -rf /'`-style obfuscation.
- The MCP danger guard and script-content TOCTOU check continue to use the comprehensive `is_redline` (so dangerous SQL through MCP/postgres tools is still intercepted).
- `notify_engine` macOS branch now escapes backslash and double-quote in the title/message before interpolating into the `osascript` AppleScript string, closing a string-injection / truncation hole (the call already used `shell=False` with a list argv).
- **False-positive reduction does not weaken the red line**: benign short-circuiting in `score()` / `get_warning_level()` only applies to `is_benign_dev_command()` output, which itself excludes irreversible ops (`rm -rf`, force push, `dd`, `mkfs`) and any command touching system paths (`/etc`, `/usr/lib`, `/dev`). The hard red line (`is_hard_redline`, step-1 of the shell guard) is evaluated *before* scoring and is untouched, so lethal commands are still blocked in YOLO mode and without a confirm channel.
- **红线检测绕过面加固**（`ext/safety_engine.py` + `tests/test_safety_redline_bypass.py`）：评分前归一化 ANSI-C `$'...'` 转义、八进制/十六进制/Unicode 转义、IFS/`$VAR` 变量替换、解释器无空格调用、递归间接调用展开（最多 32 层，穿透 `sh -c` / `sudo -u` / 命令替换）、PowerShell Base64 载荷解码与 Unicode NFKC 归一化。
- **MCP 沙箱升级为进程级隔离（无需 Docker）**：`sandbox: true` 时 server 子进程以脱敏环境（抹除 `API_KEY`/`SECRET`/`TOKEN`/`PASSWORD`/`QXT_*`/`OPENAI*`/`AWS_*`/`DATABASE_URL` 等密钥类变量）+ 临时隔离工作目录启动；每次工具调用前经 fail-closed 安全闸门对参数文本字段做红线检测（命中即拒绝，不发送给 server），闸门异常同样保守拒绝。统一 `SecurityGate.decide_mcp_tool` 递归扫描参数（JSON 字符串字段亦解析后检测）。
- **MCP 审计持久化**：`MCPAuditStore` 将每次调用记录（server、工具名、脱敏参数、成败、拦截原因、频率限制）以 JSONL 落盘到 `~/.qingxiaotuan/mcp-audit.jsonl`，支持跨进程查看（`/mcp audit`）。
- **多阶段确认强制最小间隔**：`MultiStageConfirm` 两次警告之间至少间隔 2 秒（`min_interval`，默认 2.0s），防止程序自动连续点击绕过不同位置按键；用户任一次拒绝立即终止。
- **白名单语义收敛**：确认通道中「放行」的命令仅本次生效，绝不自动写入白名单；白名单仅由 `qxt whitelist add/remove/clear` 手动维护（`core/whitelist.py` 单一实现），`is_redline` 命中即拒绝加入。MCP 白名单支持 `"*"` 通配符（允许全部工具，黑名单优先，`allowed_tools`/`denied_tools` 语义与 `MCP_SECURITY.md` 一致）。
- **安全文档修正**：`SECURITY.md` / `SECURITY_FEATURES.md` / `SECURITY_POLICY.md` / `MCP_SECURITY.md` / `README.md`(zh-CN) 澄清 SQL 破坏性操作为「可确认关键级」而非硬红线、白名单为手动维护、删除虚构的 `qxt safety check` / `/mcp config` 命令与 YAML 白名单路径、沙箱说明与进程级实现保持一致、测试数更新为 1100+。

- `scripts/push.sh` no longer disables TLS verification (`http.sslVerify=false` removed); the Windows schannel revocation-check workaround is now opt-in guidance shown on failure instead of a silent default.
- The script also no longer deletes and recreates the `origin` remote destructively; it updates the URL only when it differs.

[0.2.011]: https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/releases/tag/v0.2.011
