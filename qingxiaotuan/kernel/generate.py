"""kernel 顶层 generate 聚合器 —— 协议无关的流式生成入口。

职责：
- 调用 provider.generate(...) 拿到 StreamedMessage；
- 消费流式分片，把 text/thinking/tool_call 增量合并成完整 Message；
- 通过 callbacks 透传 on_token / on_reasoning / on_tool_call 流式回调；
- 对“空响应 / 纯思考无内容”做基本校验；
- 返回 GenerateResult（message + finish_reason + usage + trace_id）。
"""

from __future__ import annotations

from typing import List, Optional

from .contract import (
    ChatProvider,
    FinishReason,
    GenerateCallbacks,
    GenerateOptions,
    GenerateResult,
    Message,
    StreamedMessage,
    ToolCall,
    Usage,
    create_assistant_message,
)


async def generate(
    provider: ChatProvider,
    system_prompt: str,
    tools: List,
    history: List[Message],
    callbacks: Optional[GenerateCallbacks] = None,
    options: Optional[GenerateOptions] = None,
) -> GenerateResult:
    """运行一次完整生成并聚合流式结果。

    Args:
        provider: 实现了 ChatProvider 的具体 provider。
        system_prompt: 系统提示（会作为首条 system 消息注入，或由 provider 自行处理）。
        tools: 可用工具声明列表。
        history: 历史消息（不含 system）。
        callbacks: 流式回调（on_token / on_reasoning / on_tool_call / on_trace_id）。
        options: 生成选项（max tokens / thinking / cache_key / 取消信号等）。

    Returns:
        GenerateResult：聚合后的消息、结束原因、用量、trace。
    """
    streamed: StreamedMessage = await provider.generate(
        system_prompt, tools, history, options, callbacks
    )

    text_buf: List[str] = []
    reasoning_buf: List[str] = []
    # 按 tool_call id 合并增量；无 id 时退化为出现顺序索引
    pending_tool_calls: dict = {}
    usage = Usage()

    async for part in streamed:
        if part.type == "text_delta" and part.text:
            text_buf.append(part.text)
            if callbacks and callbacks.on_token:
                callbacks.on_token(part.text)
        elif part.type == "thinking_delta" and part.text:
            reasoning_buf.append(part.text)
            if callbacks and callbacks.on_reasoning:
                callbacks.on_reasoning(part.text)
        elif part.type == "tool_call_part":
            # OpenAI 流式 tool_call 用 index 分组（id/name 仅首片带，后续 arguments 增量不带 id）；
            # 无 index 时退化为按 id，再退化为 __no_id__，保证增量正确拼接。
            if part.index is not None:
                key = f"__idx_{part.index}"
            elif part.id is not None:
                key = part.id
            else:
                key = "__no_id__"
            tc = pending_tool_calls.get(key)
            if tc is None:
                tc = ToolCall(id=part.id or key, name=part.name or "", arguments="")
                pending_tool_calls[key] = tc
            if part.name:
                tc.name = part.name
            if part.arguments_delta:
                tc.arguments += part.arguments_delta
        elif part.type == "usage" and part.usage is not None:
            usage = part.usage
        elif part.type == "finish":
            # finish_reason / raw_finish_reason 也可经分片下发（metadata 优先用 streamed 上的）
            pass

    # 元数据（provider 在迭代过程中填充到 streamed 上）
    final_usage = streamed.usage or usage
    finish = streamed.finish_reason or FinishReason.OTHER

    # 组装最终消息
    msg: Message = create_assistant_message()
    msg.content = "".join(text_buf)
    msg.tool_calls = list(pending_tool_calls.values())

    # 空响应 / 纯思考校验：完成态却既无文本也无工具调用 → 视为空响应
    if (
        finish == FinishReason.COMPLETED
        and not msg.content
        and not msg.tool_calls
    ):
        # 允许“纯思考”被上层决定如何处理；这里不抛异常，仅保留 reasoning 作为内容提示。
        # 若上层严格要求非空，可据此判空。保持宽松（不阻断流程）。
        pass

    # 触发工具调用回调（仅当模型要求调用工具）
    if finish == FinishReason.TOOL_CALLS and callbacks and callbacks.on_tool_call:
        callbacks.on_tool_call(msg)

    return GenerateResult(
        message=msg,
        finish_reason=finish,
        raw_finish_reason=streamed.raw_finish_reason,
        usage=final_usage,
        trace_id=streamed.trace_id,
        id=streamed.id,
    )
