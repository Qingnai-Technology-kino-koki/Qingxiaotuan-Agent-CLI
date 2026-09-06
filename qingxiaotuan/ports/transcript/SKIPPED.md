# 覆盖范围说明

本层自研实现了整个 `transcript` 协议。**没有缺失部分。**

`上游 TS 源码树/packages/transcript/src` 下每个源模块都是纯逻辑（无 Node/React、
无代码生成），已用仅 stdlib 的 Python 对齐实现：

| TS module | Python module |
| --- | --- |
| `model/*` | `model.py` |
| `ops/operation.ts` | `operation.py` |
| `ops/apply.ts` | `apply.py` |
| `store/agentTranscript.ts`, `store/transcriptStore.ts` | `store.py` |
| `contract/schema.ts` | `serialize.py` (json round-trip + `TranscriptDecodeError`) |
| `contract/events.ts` | `events.py` |
| `contract/mediaRef.ts` | `media_ref.py` |
| `granularity/grade.ts`, `granularity/filterOps.ts` | `granularity.py` |
| `view/registry.ts` | `view.py` |
| `pagination/paginate.ts` | `paginate.py` |
| `history/groupTurns.ts`, `history/foldFacts.ts` | `history.py` |
| `ids.ts` | `ids.py` |

## 说明 / 有意的偏差

- TS 源码用 `zod` 做 wire 校验。由于本实现只用标准库，等价 `(de)serialization`
  以 `json` + 结构校验实现，遇到畸形输入抛 `TranscriptDecodeError`。Round-trip 行为保留。
- 自由形式 payload（`origin`、`payload`、`request`/`response`、工具
  `input`/`output`/`display`、`phase` 等）保留为普通值（dict）而非类型化 dataclass，
  与 TS 的 `unknown` 处理一致。
- “无变化”检测用的相等性为结构化 `==`（深相等），而非 TS 引用 `===`；
  对本包所应用的操作用行上等价。