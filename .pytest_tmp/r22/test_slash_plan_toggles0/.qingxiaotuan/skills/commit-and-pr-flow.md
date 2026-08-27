---
name: 安全提交与 PR 流程
description: 完成代码改动后, 如何安全地自测、提交并创建 PR (含消息规范与检查清单)
updated_at: 0
use_count: 0
---

# 安全提交与 PR 流程

改完代码、准备交付时按此顺序:

1. 自测: `run_tests` 跑通 (无测试则至少 `run_shell` 做语法/lint 检查)。
2. 审查: `git_status` + `git diff` 看清改了什么, 确认没有调试残留、密钥泄露。
3. 提交: 小步提交, 消息用祈使句、说明"为什么"而非"做了什么"。
   - 例: `git commit -m "fix(auth): 修复 token 过期未刷新的竞态"`
4. 推送与 PR: 推送分支, 用 `run_shell` 调 `gh pr create`(若仓库在 GitHub)。
   PR 描述写清: 背景、改动点、测试结果、风险。

红线: 绝不在提交里包含 `.env`、密钥、凭证; 删除类操作先确认可恢复。
