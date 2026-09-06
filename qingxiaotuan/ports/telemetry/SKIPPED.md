# 未实现模块

以下 TypeScript 源码模块依赖 Node.js 运行时 API（process、`node:fs`、`node:os`、
`node:crypto`、网络 `fetch`、信号处理），**未实现**。其中可移植的 node 无关辅助已
收拢进纯逻辑模块。

| TS module | Reason skipped | Python 等价物 |
| --- | --- | --- |
| `bootstrap.ts` | `process.env`, fs home dir, region resolver wiring | 无（composition-root 关切） |
| `crash.ts` | `process.on('uncaughtExceptionMonitor' / 'unhandledRejection')` | 无（进程信号处理） |
| `systemMetrics.ts` | `node:os` (`cpus`, `freemem`, `loadavg`), `process.cpuUsage` | 无（系统采样） |
| `transport.ts` (`AsyncTransport`) | `node:fs`, `node:crypto`, 网络 `fetch`, `AbortController` | 纯辅助保留在 `transport.py`：`build_user_id`, `apply_server_prefix`, `flatten_event`, `build_payload`, `handle_status`, `TransientTelemetryError` |

`transport.py` 中的 `TelemetryTransport` ABC 定义 sink 抽象，
调用方可提供仅 stdlib 的内存或文件传输实现。