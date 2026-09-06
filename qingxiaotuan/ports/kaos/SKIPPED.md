# 本层自研实现范围：跳过/简化的部分（kaos 互操作层）

本包只实现上游 `kaos` TypeScript 源中**纯逻辑、零依赖**的部分，对接口做语义对齐。
依赖 OS 特定二进制或 node 内部机制（需要第三方依赖）的模块一律跳过。

| Source | Skipped symbols | Reason |
| --- | --- | --- |
| `environment.ts` | `detectEnvironmentFromNode`, `execFileText` | 读取 `process.platform`/`process.env`，经 `node:child_process` 起子进程。改为对齐接口的纯 `detect_environment`（依赖注入）。 |
| `login-shell-path.ts` | `applyLoginShellPathFromNode`, `userShellFromNode` | 调 `node:os.userInfo()` 并读 `process.env`。改为对齐接口的纯 `probe`/`merge`/`apply`（依赖注入）。 |
| `shell-path-bridge.ts` | `getShellPathBridge` | 包装 `node:child_process.execFileSync` / `fs.existsSync`。改为对齐接口的 `createShellPathBridge`（依赖注入）+ 词法 `translateShellDrivePath`。 |
| `internal.ts` | `BufferedReadable` | node `stream.Readable` 包装；stdlib 无对应物。`decodeTextWithErrors` 与 `globPatternToRegex` 已对齐实现（`bytes.decode` 已匹配目标 `errors` 语义）。 |
| `local.ts` | 整个 `LocalKaos` | ~29k LOC 具体后端：`spawn`、`node:fs/promises`、行尾扫描、真实 glob 遍历。需要真实文件系统/进程运行时。 |
| `ssh.ts` | 整个 `SSHKaos` | 基于第三方 `ssh2` 包的 ~31k LOC 具体后端。超出范围（不引新依赖）。 |
| `process.ts` | — | `KaosProcess` 携带 node stream 类型；在 `types.py` 中以运行时可校验的 `Protocol` 对齐表达。 |
| `index.ts` | `LocalKaos` 再导出 | 聚合器；`LocalKaos`/`SSHKaos` 这两个后端未实现，故其导出被跳过。`__init__.py` 再导出其余全部。 |

## 自研实现说明

- `current.ts` 的 `AsyncLocalStorage` 用 `contextvars.ContextVar` 对应实现
  （`run_with_kaos` 用 token 作用域，镜像 `AsyncLocalStorage.run`）。
- `decodeTextWithErrors` 收敛为 `bytes.decode(label, errors)` —— Python
  在 `errors="ignore"` 下会丢弃无效字节而保留有效 U+FFFD，
  恰与上游那套繁复 node 实现复现的行为一致。
- `Kaos`/`KaosProcess` 是 `typing.Protocol`，任何后端都可结构上满足它们。