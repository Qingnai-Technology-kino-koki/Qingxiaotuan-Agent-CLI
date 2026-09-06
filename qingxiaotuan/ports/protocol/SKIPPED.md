# 自研实现范围说明 (protocol 互操作层)

本目录自研实现上游 protocol 协议中**纯逻辑、零依赖**的核心类型，并对接口做对齐，
全部使用标准库（dataclasses / enum / typing / json / re），不引入任何第三方依赖
（不含 pydantic / zod）。

## 已实现（foundational, dependency-free）
- `envelope.py` — `Envelope` + `ok_envelope` / `err_envelope` / `parse_envelope`。
- `error_codes.py` — `ErrorCode` (IntEnum) + `ErrorCodeReason` 映射 + `reason_for` / `code_for`。
- `time.py` — ISO-8601 校验与归一化（`normalize_iso_date_time` / `is_iso_date_time`）。
- `request_id.py` — ULID 校验与生成（用 stdlib 实现 `ulid` 语义，Crockford base32）。
- `approval.py` — `ApprovalDecision` / `ApprovalScope` / `ApprovalRequest` / `ApprovalResponse`。
- `display.py` — `ToolInputDisplay` / `ToolResultDisplay` 判别联合（各变体 dataclass + `parse_*` 分发器）。
- `question.py` — `QuestionOption` / `QuestionItem` / `QuestionRequest` / `QuestionAnswer(×5)` / `QuestionResponse`。
- `pagination.py` — `CursorQuery`（互斥校验）/ `PageResponse`。
- `message.py` — `MessageRole` + `MessageContent` 判别联合（text / tool_use / tool_result / image / video / file / thinking）+ `Message`。
- `events.py` — 核心事件枚举 + ~45 个事件 dataclass + `parse_event` 分发器（含 `Event` 兜底）。
- `ws_control.py` — `WsOperationDefinition` + `ws_operations`（client control / server system / session_event），payload 以手写 JSON-Schema dict 表达（替代 zod）。
- `asyncapi.py` — `create_async_api_document` + 纯函数 `message_id` / `title_from_name`。

## 未实现 / 延后（标注原因）
- **`asyncapi.ts` 的 `z.toJSONSchema` 生成**：zod 专属。改为在 `ws_control` 中直接携带
  JSON-Schema dict，使 `create_async_api_document` 仍能产出完整 payload schema，行为等价。
- **`session.ts` / `workspace.ts` / `modelCatalog.ts` / `tool.ts` / `skill.ts` / `task.ts` /
  `fs.ts` / `file.ts`**：依赖更重的下游领域模型，且部分（如 `modelCatalog` 的 provider
  refresh diff）与 REST 层耦合，超出“foundational 零依赖核心”范围，按 STATUS.md 列为未实现。
- **`rest/*` 全部（REST 端点 schema）**：属于 HTTP 接口层，依赖 request/response 封装与
  上游 service，非协议核心类型，未纳入本次。
- **`*.test.ts`**：TS 测试。已用 Python pytest 重写关键行为覆盖（见 `tests/test_ports_protocol.py`）。
- **node / React 专属代码**：本包内无此类生成产物；若后续出现 asyncapi client codegen，
  将标注并跳过（node 运行时依赖）。

## 与 TS 的差异
- 泛型 `Envelope<T>` / `PageResponse<T>` 用 `Any` 表达（Python 运行时无需类型参数即可保证 wire 形状）。
- `events.ts` 中依赖 `Session` / `Workspace` / `ConfigResponse` / provider refresh diff 的事件，
  其对应字段在本实现中按 `dict` 透传（不深校验），保留 round-trip 能力，待下游模型对齐后收紧。
- 校验为轻量级（存在性 / 基本类型 / 必需非空字符串），未做 zod 的逐字段精确定制消息。