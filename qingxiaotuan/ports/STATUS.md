# 青小团自研能力层状态追踪（历史归档）

青小团的 `kernel/`、`core/`、`acp/`、`tools/` 等能力层均为 **Python 独立自研实现**，仅对相关交互/协议做接口对齐，不再以任何上游 TS 代码为移植来源。

> **历史说明（2026-08-29）**：早期确有一条「上游 TS 源码 → Python 1:1 移植」工作流，后经评估已**整体终止**：多余 8 个 TS 包（acp-adapter / acp-server / klient / node-sdk / agent-core / agent-core-v2 / minidb / kap-server，约 200k+ TS 行）对 Python CLI 无实质影响，一律**不再移植**；整个 TS 源码目录已移除（移至 `.trash-上游 TS 源码树/`，已 gitignore，可恢复）。删除脚本见仓库根 `delete-kimi-ts.ps1`（保留备用）。自研能力层与原 TS 参考树相互解耦，仅作接口对齐依据。

`qingxiaotuan/ports/` 目录则是青小团**自研的互操作层**，用于与各类外部系统（协议/转录/遥测/登录/迁移）交换数据，属独立命名空间，不影响主模块。

原则：
- 原生默认零破坏：`ports/` 是独立命名空间，不改动现有 `qingxiaotuan/` 模块。
- 每项互操作层带 pytest 测试、可 `py_compile`、可 import。
- 仅实现纯逻辑；对强依赖 node/React/原生绑定的部分标注 skip。

## 能力层 / 互操作层（Python 落地、测试全绿）

| 能力层 | Python 位置 | 测试 | 说明 |
|---|---|---|---|
| kernel（大模型抽象/Agent/Tools/Session/MCP/ACP） | qingxiaotuan/kernel | 82 passed | 自研能力层
| protocol | qingxiaotuan/ports/protocol | 32 passed | 核心类型/envelope/error-codes |
| transcript | qingxiaotuan/ports/transcript | 9 passed | 会话转录纯逻辑 |
| telemetry | qingxiaotuan/ports/telemetry | 26 passed | 遥测事件 |
| kaos | qingxiaotuan/ports/kaos | 46 passed | 环境/登录/路径 |
| oauth | qingxiaotuan/ports/oauth | 34 passed | token/identity 纯逻辑 |
| migration-legacy | qingxiaotuan/ports/migration_legacy | 40 passed | 原子写/路径检测 |

**合计：能力层 + 6 个互操作层落地，相关测试 178 passed + 1 skipped。** 原 TS 移植工作已全部终止，TS 源码已移除。

## 未实现且已归档（原上游 TS 源码已移除）

`acp-adapter, acp-server, klient, node-sdk, agent-core, agent-core-v2, minidb, kap-server`（大型/独立产品/原生绑定），以及明确不移植的 `pi-tui`（TUI/React 前端）、`tree-sitter-bash`（原生绑定）。其中 agent-core / acp 相关能力已由 `qingxiaotuan/kernel`、`qingxiaotuan/acp`、`qingxiaotuan/tools` 等自研模块覆盖。

图例：✅ 完成并测试  ⛔ 不移植/已放弃
