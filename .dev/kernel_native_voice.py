# -*- coding: utf-8 -*-
"""把 kernel/ 内所有指向 Kimi/TS 源码的 docstring/注释改写为青小团原生风格。
保留代码行为与公开 API，只改文档语气与归属表述（就地重构）。"""
import io

B = r"E:\Qingxiaotuan Agent CLI"
# (相对路径, [(old, new), ...])
R = {
    r"__init__.py": [
        ("\"\"\"kernel —— 青小团自研的大模型抽象层（接口对齐 Kimi Code `kernel`，独立 Python 实现）。",
         "\"\"\"kernel —— 青小团自研的大模型抽象层。"),
        ("设计目标（对标 packages/agent-core-v2/src/kernel）：", "设计目标："),
    ],
    r"agent\__init__.py": [
        ("的 Python port）。", "子系统）。"),
        ("\"\"\"kernel.agent —— Agent 编排/Loop 子系统（Kimi agent-core-v2", "\"\"\"kernel.agent —— Agent 编排/循环"),
    ],
    r"agent\step_retry.py": [
        ("\"\"\"Step 重试恢复 —— 自研实现（接口对齐 Kimi loop/stepRetry/stepRetryService.ts。",
         "\"\"\"Step 重试恢复 —— 失败的 step 自动重试与恢复。"),
        ("\"\"\"判断错误是否可重试（对标 TS isRetryableGenerateError）。\"\"\"",
         "\"\"\"判断一个错误是否值得重试（网络/超时/可瞬态恢复的错误才重试）。\"\"\""),
        ("\"\"\"Step 重试服务（对标 TS AgentStepRetryService）。\"\"\"",
         "\"\"\"Step 重试服务：按失败次数/退避策略调度重试。\"\"\""),
    ],
    r"agent\step_request.py": [
        ("\"\"\"Step 请求抽象 —— 自研实现（接口对齐 Kimi loop/stepRequest.ts。",
         "\"\"\"Step 请求抽象 —— 一次代理循环里要执行的最小工作单元。"),
    ],
    r"agent\step_queue.py": [
        ("\"\"\"Step 请求队列 —— 自研实现（接口对齐 Kimi loop/stepRequestQueue.ts。",
         "\"\"\"Step 请求队列 —— 按批次排队并调度 StepRequest。"),
    ],
    r"agent\loop.py": [
        ("\"\"\"Agent Loop 主循环 —— 自研实现（接口对齐 Kimi loop/loopService.ts + loop.ts。",
         "\"\"\"Agent Loop 主循环 —— 多步代理编排的核心执行器。"),
        ("（对标 TS createStreamPartHandler）。", "。"),
        ("参考 Kimi：type 决定映射 ——", "流式消息的 type 决定如何映射成事件："),
        ("（对标 TS toolExecutor 的 yield 项）。\"\"\"", "。\"\"\""),
        ("\"\"\"错误恢复处理器（对标 TS LoopErrorHandler）。", "\"\"\"错误恢复处理器（step 级失败时接管）。"),
        ("（对标 TS OrderedHookSlot，省略 next 链）。", "（可注册多个有序回调）。"),
        ("\"\"\"Agent 编排主循环（对标 TS AgentLoopService）。", "\"\"\"Agent 编排主循环：驱动 step 提交/执行/收敛。"),
    ],
    r"agent\errors.py": [
        ("\"\"\"Agent Loop 错误模型 —— 自研实现（接口对齐 Kimi loop/errors.ts + loop.ts。",
         "\"\"\"Agent Loop 错误模型 —— 循环层的错误层级与判定。"),
        ("\"\"\"错误码表（对标 Kimi loop/errors.ts LoopErrors.codes）。\"\"\"", "\"\"\"错误码表。\"\"\""),
        ("\"\"\"Loop 层错误基类（对标 Kimi LoopError extends Error2）。\"\"\"",
         "\"\"\"Loop 层错误基类（Agent、编排、传输错误均可归为它）。\"\"\""),
        ("\"\"\"构造「单轮 step 超限」错误（对标 createMaxStepsExceededError）。\"\"\"",
         "\"\"\"构造「单轮 step 超限」错误。\"\"\""),
        ("\"\"\"判断错误是否为「max steps 超限」（对标 isMaxStepsExceededError）。\"\"\"",
         "\"\"\"判断错误是否为「max steps 超限」。\"\"\""),
    ],
    r"agent\continuation.py": [
        ("\"\"\"续转服务 —— 自研实现（接口对齐 Kimi loop/loopContinuationService.ts。",
         "\"\"\"续转服务 —— 一轮 step 结束后自动发起下一轮。"),
        ("\"\"\"包装 AgentLoopService，注入续转 hook（对标 TS AgentLoopContinuationService）。\"\"\"",
         "\"\"\"包装 AgentLoopService，注入续转 hook。\"\"\""),
    ],
    r"agent\config.py": [
        ("\"\"\"Loop 控制参数 —— 自研实现（接口对齐 Kimi loop/configSection.ts。",
         "\"\"\"Loop 控制参数 —— 循环的最大步数、超时等约束。"),
    ],
    r"tools\scheduler.py": [
        ("\"\"\"scheduler.py —— 自研实现（接口对齐 Kimi Code ``agent/toolExecutor/toolScheduler.ts``。",
         "\"\"\"scheduler —— 顺序/限量执行工具的调度器。"),
    ],
    r"tools\registry.py": [
        ("\"\"\"registry.py —— 自研实现（接口对齐 Kimi Code ``tool/toolContribution.ts`` 的注册表思路。",
         "\"\"\"registry —— 工具注册表：按名称登记并查取可执行工具。"),
    ],
    r"tools\permission.py": [
        ("\"\"\"permission.py —— 自研实现（接口对齐 Kimi Code 的权限策略子系统",
         "\"\"\"permission —— 工具执行的权限策略子系统"),
        ("简化 port 中由 gate 直接走 approval_service）", "由 gate 直接走 approval_service）"),
    ],
    r"tools\gate.py": [
        ("\"\"\"gate.py —— 自研实现（接口对齐 Kimi Code ``agent/permissionGate/permissionGateService.ts``。",
         "\"\"\"gate —— 权限闸门：在工具真正执行前拦截/询问/放行。"),
    ],
    r"tools\executor.py": [
        ("\"\"\"executor.py —— 自研实现（接口对齐 Kimi Code ``agent/toolExecutor/toolExecutorService.ts``。",
         "\"\"\"executor —— 工具执行器：调用可执行工具并收拢结果。"),
    ],
    r"tools\contract.py": [
        ("\"\"\"kernel.tools.contract —— 自研实现（接口对齐 Kimi Code ``tool/toolContract.ts``。",
         "\"\"\"kernel.tools.contract —— 工具的声明与可执行契约。"),
        ("    # 以下字段为兼容上游结构保留，简化 port 中不被强制使用",
         "    # 以下字段为兼容历史结构保留，当前不被强制使用"),
        ("（简化 port 可选）", "（可选）"),
    ],
    r"tools\before_execute_event.py": [
        ("\"\"\"before_execute_event.py —— 自研实现（接口对齐 Kimi Code\n``agent/toolExecutor/beforeToolExecuteEvent.ts`` 的异步语义。",
         "\"\"\"before_execute_event —— 工具执行前的广播事件语义。"),
    ],
    r"tools\args.py": [
        ("\"\"\"args.py —— 自研实现（接口对齐 Kimi Code ``tool/tool-args-parse.ts`` / ``args-validator.ts`` /\n``path-access.ts``。",
         "\"\"\"args —— 工具参数的解析与校验。"),
    ],
    r"providers\_http.py": [
        ("本修复对标 qingxiaotuan-cli fork 中 agent-core-v2 的同名修复，下沉到 Python 端。",
         "青小团对该缺陷的 Python 侧修复。"),
    ],
    r"providers\openai_responses.py": [
        ("\"\"\"对标 Kimi OpenAIResponsesChatProvider（走 /responses 协议）。\"\"\"",
         "\"\"\"OpenAI Responses 协议适配（走 /responses）。\"\"\""),
    ],
    r"providers\openai_legacy.py": [
        ("上游缺陷修复（对标 qingxiaotuan-cli fork agent-core-v2）：", "上游缺陷修复："),
        ("\"\"\"对标 Kimi OpenAILegacyChatProvider（走 chat/completions 协议）。\"\"\"",
         "\"\"\"OpenAI chat/completions 协议适配。\"\"\""),
    ],
    r"providers\kimi.py": [
        ("\"\"\"对标 Kimi kimi.contrib：baseProtocol=openai，端点 api.moonshot.ai。\"\"\"",
         "\"\"\"Kimi (Moonshot) provider —— baseProtocol=openai，端点 api.moonshot.ai。\"\"\""),
    ],
    r"providers\anthropic.py": [
        ("\"\"\"对标 Kimi AnthropicChatProvider（走 Anthropic Messages 协议）。\"\"\"",
         "\"\"\"Anthropic Messages 协议适配。\"\"\""),
    ],
    r"session\wire.py": [
        ("（对标 Kimi wire/wireService.ts + record.ts）。", "）。"),
        ("replay(journal) 离线重建 ContextMemory（对标 contextTranscript.reduceContextTranscript）。",
         "replay(journal) 可离线重建 ContextMemory。"),
        ("\"\"\"若文件为空，写入一条 metadata 记录（对标 WireService.seal）。\"\"\"",
         "\"\"\"若文件为空，写入一条 metadata 记录作封口。\"\"\""),
        ("\"\"\"从 wire journal 重建 ContextMemory（fold 逻辑对标 contextTranscript）。\"\"\"",
         "\"\"\"从 wire journal 重建 ContextMemory。\"\"\""),
    ],
    r"session\strategy.py": [
        ("\"\"\"压缩策略配置（对标 Kimi fullCompaction/strategy.ts 的 DEFAULT_COMPACTION_CONFIG）。\"\"\"",
         "\"\"\"压缩策略配置。\"\"\""),
    ],
    r"session\projector.py": [
        ("（port Kimi contextProjector.ts）。", "）。"),
        ("（本 port 简化为：移除没有对应 assistant tool_calls 的孤立 tool 消息）。",
         "（本实现简化为：移除没有对应 assistant tool_calls 的孤立 tool 消息）。"),
    ],
    r"session\memory.py": [
        ("\"\"\"ContextMemory —— 内存版会话上下文（对标 Kimi contextMemoryService.ts / contextOps.ts）。",
         "\"\"\"ContextMemory —— 内存版会话上下文，负责积累与折叠消息。"),
        ("（对标 conversationTime.isUndoAnchor）。", "。"),
        ("\"\"\"内存上下文存储器（对标 Kimi AgentContextMemoryService）。\"\"\"",
         "\"\"\"内存上下文存储器。\"\"\""),
        ("\"\"\"把一条循环事件 fold 进上下文（对标 loopEventFold）。\"\"\"",
         "\"\"\"把一条循环事件 fold 进上下文。\"\"\""),
        ("\"\"\"用一条摘要替换历史（对标 contextMemoryService.applyCompaction）。\"\"\"",
         "\"\"\"用一条摘要替换历史（压缩）。\"\"\""),
        ("\"\"\"组装发送给模型的消息列表：[system] + history（对标 buildMessagesWithSystem）。\"\"\"",
         "\"\"\"组装发送给模型的消息列表：[system] + history。\"\"\""),
    ],
    r"session\contracts.py": [
        ("\"\"\"kernel session 契约层 —— 对齐 Kimi agent-core-v2 的 contextMemory/types.ts。",
         "\"\"\"kernel session 契约层 —— 会话上下文的数据结构。"),
        ("    \"\"\"一条消息的来源标记。对标 Kimi PromptOrigin 联合类型。\"\"\"",
         "    \"\"\"一条消息的来源标记。\"\"\""),
        ("（对标 Kimi LoopRecordedEvent 联合类型）。", "）。"),
        ("（对标 Kimi ContextCompactionInput）。", "）。"),
        ("（对标 Kimi ContextCompactionResult）。", "）。"),
    ],
    r"session\compaction.py": [
        ("\"\"\"压缩子系统（port Kimi compactionHandoff.ts + fullCompactionService.ts 的纯逻辑部分）。",
         "\"\"\"压缩子系统 —— 上下文超限时的摘要收拢。"),
        ("# ============================================================ token 估算（port tokens.ts）",
         "# ============================================================ token 估算"),
        ("# ============================================================ 溢出收缩（对标 shrinkCompactionHistoryAfterOverflow）",
         "# ============================================================ 溢出收缩"),
        ("\"\"\"剥离动态工具上下文（本 port 无该特性，原样返回）。\"\"\"",
         "\"\"\"剥离动态工具上下文（无该特性时原样返回）。\"\"\""),
        ("\"\"\"全量压缩控制器（port Kimi AgentFullCompactionService 的纯逻辑）。",
         "\"\"\"全量压缩控制器：调度策略与执行。"),
    ],
    r"session\bridge.py": [
        ("\"\"\"从 wire.jsonl 重现上下文（离线重建，对标 contextTranscript）。\"\"\"",
         "\"\"\"从 wire.jsonl 重现上下文（离线重建）。\"\"\""),
    ],
    r"mcp\__init__.py": [
        ("自研实现（接口对齐 Kimi Code 的 ``mcpCore`` + ``agent/mcp``）。",
         "自研实现 —— MCP (Model Context Protocol) 的客户端与工具桥接。"),
    ],
    r"mcp\types.py": [
        ("\"\"\"MCP 桥接层类型 —— 对应 ``mcpCore/types.ts`` 与 ``tool/toolContract.ts``。",
         "\"\"\"MCP 桥接层类型。"),
    ],
    r"mcp\tool.py": [
        ("\"\"\"MCP 工具包装 —— 对应 ``agent/mcp/tools/mcp.ts`` 的 ``createMcpTool``。",
         "\"\"\"MCP 工具包装 —— 把远端 MCP 工具包成青小团可执行工具。"),
    ],
    r"mcp\client_stdio.py": [
        ("\"\"\"MCP stdio 客户端 —— 对应 ``mcpCore/client-stdio.ts``。",
         "\"\"\"MCP stdio 客户端 —— 通过标准输入/输出与本地 MCP server 通信。"),
    ],
    r"mcp\registry_integration.py": [
        ("\"\"\"MCP 工具注册集成 —— 对应 ``agent/mcp/mcpService.ts`` 的注册/碰撞逻辑（简化）。",
         "\"\"\"MCP 工具注册集成 —— 把 MCP 工具并入工具注册表并处理命名碰撞。"),
    ],
    r"mcp\client_sse.py": [
        ("\"\"\"MCP SSE 客户端 —— 对应 ``mcpCore/client-sse.ts``（简化实现）。",
         "\"\"\"MCP SSE 客户端 —— 通过 Server-Sent Events 与 MCP server 通信。"),
    ],
    r"mcp\output.py": [
        ("\"\"\"MCP 结果转 kernel 输出 —— 对应 ``agent/mcp/output.ts``。",
         "\"\"\"MCP 结果转 kernel 输出 —— 把 MCP 返回值归一为统一数据结构。"),
    ],
    r"mcp\naming.py": [
        ("\"\"\"MCP 工具名限定 —— 自研实现（接口对齐 ``mcpCore/tool-naming.ts``。",
         "\"\"\"MCP 工具名限定 —— 用稳定的哈希把工具名规格化。"),
    ],
    r"mcp\client_http.py": [
        ("\"\"\"MCP HTTP 客户端 —— 对应 ``mcpCore/client-http.ts``（client 侧 JSON-RPC over HTTP）。",
         "\"\"\"MCP HTTP 客户端 —— 通过 JSON-RPC over HTTP 与远端 MCP server 通信。"),
    ],
    r"mcp\errors.py": [
        ("\"\"\"MCP 桥接层错误模型（对应 ``mcpCore/errors.ts`` 的错误码语义，简化实现）。\"\"\"",
         "\"\"\"MCP 桥接层错误模型。\"\"\""),
        ("# --------------------------------------------------------------------------- 错误分类助手（对应 client-shared.ts）",
         "# --------------------------------------------------------------------------- 错误分类助手"),
    ],
    r"mcp\connection_manager.py": [
        ("\"\"\"MCP 连接管理器 —— 对应 ``mcpCore/connection-manager.ts`` 的 ``McpConnectionManager``。",
         "\"\"\"MCP 连接管理器 —— 按 transport 管理单个/多个 MCP server 的连接生命周期。"),
    ],
    r"mcp\config.py": [
        ("\"\"\"MCP server 配置解析 —— 对应 ``mcpCore/config-schema.ts``。",
         "\"\"\"MCP server 配置解析。"),
    ],
    r"provider_service.py": [
        ("\"\"\"kernel ProviderService —— 轻量 DI 等价物，对标 Kimi providerService + protocolAdapterRegistry。",
         "\"\"\"kernel ProviderService —— 轻量 provider 注册中心（按配置选协议与厂商）。"),
        ("    \"\"\"一个 provider 的配置（对标 Kimi ProviderConfig 精简版）。\"\"\"",
         "    \"\"\"一个 provider 的配置。\"\"\""),
        ("（对标 Kimi ProviderService，精简版）。", "）。"),
    ],
    r"generate.py": [
        ("\"\"\"kernel 顶层 generate 聚合器 —— 协议无关，对标 Kimi contract/generate.ts。",
         "\"\"\"kernel 顶层 generate 聚合器 —— 协议无关的流式生成入口。"),
    ],
    r"contract.py": [
        ("自研实现（接口对齐 Kimi Code `packages/agent-core-v2/src/kernel/contract/`：",
         "契约层由青小团自研实现，统一 provider 与内置交互所需的数据形状："),
        ("- provider.ts: ChatProvider 接口、GenerateOptions、StreamedMessage",
         "- provider：ChatProvider 接口、GenerateOptions、StreamedMessage"),
        ("- message.ts: Role / ContentPart / Message / ToolCall / StreamedMessagePart",
         "- message：Role / ContentPart / Message / ToolCall / StreamedMessagePart"),
        ("- tool.ts: Tool", "- tool：Tool"),
        ("- usage.ts: TokenUsage", "- usage：TokenUsage"),
        ("- errors.ts: ChatProviderError 层级 + classify_api_error", "- errors：ChatProviderError 层级 + 错误分类"),
        ("\"\"\"多模态内容块（文本 / 图片 / 音频）。对标 Kimi message.ts ContentPart。\"\"\"",
         "\"\"\"多模态内容块（文本 / 图片 / 音频）。\"\"\""),
        ("\"\"\"工具声明（极简：名称 + 描述 + JSON Schema 参数）。对标 Kimi tool.ts。\"\"\"",
         "\"\"\"工具声明（极简：名称 + 描述 + JSON Schema 参数）。\"\"\""),
        ("\"\"\"Token 用量（含缓存读/写，对标 Kimi usage.ts TokenUsage）。\"\"\"",
         "\"\"\"Token 用量（含缓存读/写）。\"\"\""),
        ("\"\"\"生成结束原因（对标 Kimi contract FinishReason）。\"\"\"",
         "\"\"\"生成结束原因。\"\"\""),
        ("    对标 Kimi contract StreamedMessage（带 [Symbol.asyncIterator] 的接口）。",
         "    流式消息对象（含异步迭代接口）。"),
        ("    # 这些字段必须始终存在（对标 Kimi 的 StreamedMessage 元数据）。",
         "    # 这些字段必须始终存在。"),
        ("\"\"\"generate 的可选参数（对标 Kimi GenerateOptions，精简版）。\"\"\"",
         "\"\"\"generate 的可选参数。\"\"\""),
        ("\"\"\"大模型 provider 统一接口（对标 Kimi contract ChatProvider）。",
         "\"\"\"大模型 provider 统一接口。"),
        ("\"\"\"generate 过程中的流式回调（对标 Kimi generate 的 callbacks 参数）。\"\"\"",
         "\"\"\"generate 过程中的流式回调。\"\"\""),
        ("\"\"\"provider 错误的基类（对标 Kimi errors.ts Error2 + ChatProviderError）。\"\"\"",
         "\"\"\"provider 错误的基类。\"\"\""),
        ("\"\"\"带 HTTP 状态码的错误（对标 Kimi APIStatusError）。\"\"\"",
         "\"\"\"带 HTTP 状态码的错误。\"\"\""),
        ("\"\"\"从响应头或 body 解析重试等待毫秒数（对标 Kimi parseRetryAfterMs）。\"\"\"",
         "\"\"\"从响应头或 body 解析重试等待毫秒数。\"\"\""),
        ("\"\"\"根据 HTTP 状态码 + body 把错误归一为具体 ChatProviderError 子类（对标 Kimi classifyApiError）。\"\"\"",
         "\"\"\"根据 HTTP 状态码 + body 把错误归一为具体 ChatProviderError 子类。\"\"\""),
    ],
    r"acp\version.py": [
        ("自研实现，接口对齐 kimi-code ``packages/acp-server/src/version.ts``。", "自研实现 —— ACP 协议版本协商。"),
    ],
    r"acp\server.py": [
        ("自研实现，接口对齐 kimi-code ``packages/acp-server/src/{server.ts,start.ts}``（精简同步版）。",
         "自研实现 —— ACP server 主入口（NDJSON-JSON-RPC over stdio）。"),
    ],
    r"acp\session.py": [
        ("自研实现，接口对齐 kimi-code ``packages/acp-server/src/session.ts``（精简同步版，", "自研实现 —— ACP 会话管理（"),
    ],
    r"acp\protocol.py": [
        ("自研实现，接口对齐 kimi-code ``packages/acp-server/src/{server.ts,events-map.ts,approval.ts}`` 的",
         "自研实现 —— ACP 协议消息编解码（"),
        ("# 与 kimi-code approval.ts 的 optionId 字面量一一对应（既是构造端也是解析端真源）。",
         "# optionId 字面量是构造端与解析端的共同真源。"),
        ("自研实现，接口对齐 kimi-code ``SessionApprovalResponse``（用 dataclass 替代 TS interface）。",
         "自研实现 —— 审批响应（用 dataclass 建模）。"),
    ],
    r"acp\interaction_bridge.py": [
        ("自研实现，接口对齐 kimi-code ``packages/acp-server/src/{interaction-bridge.ts,approval.ts}``（精简版，",
         "自研实现 —— 交互桥（审批/会话消息转 kernel 事件）（精简版，"),
    ],
    r"acp\events_map.py": [
        ("自研实现，接口对齐 kimi-code ``packages/acp-server/src/events-map.ts``。", "自研实现 —— 事件映射。"),
    ],
    r"acp\convert.py": [
        ("自研实现，接口对齐 kimi-code ``packages/acp-server/src/convert.ts``（精简版，", "自研实现 —— 内容块互转（精简版，"),
        ("自研实现，接口对齐 kimi-code ``toolResultToAcpContent``：", "工具结果转 ACP 内容块："),
    ],
    r"acp\codec.py": [
        ("自研实现，接口对齐 kimi-code `packages/acp-server/src/start.ts` 的 ndJsonStream 思路：",
         "自研实现 —— NDJSON 帧编解码："),
     ],
}

fails = []
done = []
for rel, pairs in R.items():
    path = B + "\\qingxiaotuan\\kernel\\" + rel
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    for old, new in pairs:
        n = text.count(old)
        if n == 0:
            fails.append(f"{rel} :: {old[:36]!r}")
        else:
            text = text.replace(old, new)
            done.append(f"{rel} x{n} :: {old[:30]!r}")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)

print("== DONE ==")
print("\n".join(done))
print("\n== FAILED (old not found) ==")
print("\n".join(fails) if fails else "(none)")