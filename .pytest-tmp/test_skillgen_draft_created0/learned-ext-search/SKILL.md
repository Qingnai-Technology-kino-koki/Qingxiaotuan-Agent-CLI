---
name: learned-ext-search
description: 由 self-improve 自动提炼的技能草稿 (源自工具 ext_search 高频成功)。待人工审阅后启用。
source: self-improve
generated_at: 2026-08-27T19:05:40
status: draft
---

# learned-ext-search

> 本技能由青小团 self-improve 闭环从执行历史中自动提炼, **当前为草稿**, 尚未激活。
> 请人工审阅下方流程, 确认无误后删除 `status: draft` 即可启用。

## 触发场景
- 工具 `ext_search` 在近期执行中成功 6 次, 说明其使用模式已相对稳定。

## 推荐流程
1. 调用 `ext_search` 前, 先通过 `ext_safety_analyze` 评估影响半径。
2. 对危险参数先走 plan / dry-run, 确认无误再执行。
3. 执行后用结构化 ToolResult 判断 status, 失败则回到第 1 步重试。

## 注意
- 不要盲目自动化; 涉及写/删/推送的操作始终保留人工确认。
