# 本层自研实现范围：跳过部分（OAuth 互操作层）

本层自研实现 OAuth 协议的纯逻辑、仅 stdlib 接口面。以下 `上游 TS 源码树/packages/oauth/src`
中的部分**未实现**：它们需要网络传输、浏览器表面或 node-only API，在本环境里没有
干净、零依赖的 Python 等价物。

| Source module | Symbol(s) | Why skipped |
| --- | --- | --- |
| `api-error.ts` | `readApiErrorMessage` | 包装一个 `fetch` `Response`；需要传输层。改为对齐接口的纯 `extract_api_error_message`。 |
| `oauth.ts` | `requestDeviceAuthorization`, `pollDeviceToken`, `refreshAccessToken`, `postForm` | 活跃的 `fetch` + `AbortSignal`/超时传输。其纯校验/分类逻辑已对齐到 `error_classification.py`。 |
| `custom-registry.ts` | `fetchCustomRegistry` | 对 api.json 文档做 HTTP `fetch`。解析/应用辅助已对齐实现。 |
| `oauth-manager.ts` | `OAuthManager`, `defaultRefreshThreshold`, `newInstanceId` | 驱动网络上的活跃流程（polling/refresh/store）。 |
| `storage.ts` | `FileTokenStorage` | 用 node `fs`/`process.pid`/`Buffer` 做原子文件 I/O。Python 可移植但超出范围（焦点是数据模型 + 解析/校验）。 |
| `managed-*.ts`, `open-platform.ts`, `region.ts`, `toolkit.ts`, `refreshProviderModels.ts`, `oauth-token-transaction.ts` | fetch/exec 包装 | 网络/CLI/浏览器交互。 |

此处不会发起真实网络调用。