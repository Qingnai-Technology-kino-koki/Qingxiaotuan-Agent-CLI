# 未实现模块（本层自研实现范围之外）

以下 `上游 TS 源码树/packages/migration-legacy/src` 中的 TypeScript 模块**未实现**：
它们属于 node-only 编排，或依赖无 stdlib 对应的第三方包（`zod`、`smol-toml`、
`@moonshot-ai/agent-core`），或做 config/MCP/skills 内容的文件拷贝，超出本纯工具、零依赖实现范围。

| Module | 未实现原因 |
| --- | --- |
| `run-migration.ts` | 顶层编排器；驱动下面各 step 函数并写 report/marker。纯工具逻辑已对齐实现；runner 未实现。 |
| `steps/config.ts` | 用 `smol-toml` 解析/合并 `config.toml`；改写 provider/model/hook 条目。Node-only。 |
| `steps/mcp.ts` | 合并 `mcp.json` servers；node-only 文件逻辑。 |
| `steps/skills.ts` | 拷贝 skills 目录；node-only fs copy。 |
| `steps/user-history.ts` | 拷贝 user-history 文件；node-only fs copy。 |
| `sessions/index.ts` | 完整 session 迁移流水线（编排）。 |
| `sessions/migrate-one.ts` | 翻译单个 session；依赖 agent-core wire 格式。 |
| `sessions/state-writer.ts` | 写新 session state；node-only。 |
| `sessions/wire-writer.ts` | 写 wire.jsonl；node-only。 |
| `sessions/content-part.ts` | 翻译用 content-part 归一化。 |
| `sessions/tool-call-display.ts` | UI display 元数据；node-only。 |
| `sessions/close-tool-calls.ts` | tool-call 收尾修复；node-only。 |
| `sessions/translator.ts` | 全量 context 翻译（`translateContextLines`）。仅那个小的 `analyzeContextContent` 辅助已对齐到 `classify.py`。 |
| `sessions/workdir-bucket.ts` | `computeWorkdirBucket` 别名对应 agent-core 的 `encodeWorkDirKey`（node-only）。仅纯 `oldMd5BucketName` 辅助已对齐到 `workdir_bucket.py`。 |

## 已实现（纯逻辑，stdlib-only）

- `atomic_write.py` — 对齐 `atomic-write.ts`
- `paths.py` — 对齐 `paths.ts`
- `kimi_cli_schema.py` — 对齐上游 legacy schema 模块 (validation, passthrough)
- `marker.py` — 对齐 `marker.ts`
- `migration_errors_log.py` — 对齐 `migration-errors-log.ts`
- `prompt.py` — 对齐 `prompt.ts`
- `report.py` — 对齐 `report.ts`
- `session_index.py` — 对齐 `session-index.ts`
- `stub_detect.py` — 对齐 `stub-detect.ts` (uses stdlib `tomllib`)
- `classify.py` — 对齐 `sessions/classify.ts` + `analyzeContextContent` (取自 `sessions/translator.ts`)
- `workdir_bucket.py` — 对齐 `oldMd5BucketName` (取自 `sessions/workdir-bucket.ts`)
- `detect.py` — 对齐 `detect.ts`
- `types.py` — 对齐 `types.ts` (dataclasses)