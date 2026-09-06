# 支持的 Provider / 模型（实测兼容性与验证方式）

> 本文给出「`qxt` → 工具执行 → 安全评估」核心链路在各 Provider / 模型下的兼容性说明与**可离线复现的实测验证方式**。
> 青小团是**模型无关**的：所有 OpenAI 兼容端点共用同一套 `OpenAICompatAdapter`，因此「传输层兼容性」由一份适配器 + 一组离线端到端测试统一保证。

---

## 1. 为什么一份适配器能覆盖约 40 家 Provider？

所有 OpenAI 兼容端点（无论背后是 DeepSeek、OpenAI、OpenRouter、Moonshot 还是本地 Ollama）都暴露同一个 `/v1/chat/completions` 协议。青小团的 `OpenAICompatAdapter` 只依赖这个稳定的接口表面：

- 消息格式（`system` / `user` / `assistant` / `tool`）；
- 工具调用（`tools` / `tool_calls` / `tool` 角色回填）；
- 流式 SSE（`stream` + `stream_options.include_usage`）；
- 错误码（429 / 5xx / 401）与可选 `Retry-After`。

所以「新增一个 OpenAI 兼容 Provider」通常**不需要写代码**，只需在 `qxt setup` 里填 `base_url` + `API_KEY`（或用 `/model` 切换）。非兼容 Provider（如 Anthropic 原生协议）走独立的适配器，但同样接入同一套 Agent 工具循环与安全护栏。

---

## 2. 实测通过清单

下表区分两种「通过」：

- ✅ **传输层已验证（离线）**：由 `tests/test_e2e_mock_server.py` 的本地 mock 端点驱动真实 `OpenAICompatAdapter` 走完 `chat → tool_call → 安全评估 → 工具回填 → 最终回答` 全链路；并覆盖 5xx 自动重试。这证明**任意 OpenAI 兼容端点的传输与 Agent 闭环都跑得通**。
- 🔑 **需自带 Key 实跑**：模型的具体能力（工具调用质量、中文理解、长上下文）取决于 Provider，需要你填入 Key 后用 `qxt /model` 的连通性测试 + 实际对话验证。

| Provider | 端点类型 | 传输层验证 | 备注 |
|----------|----------|-----------|------|
| DeepSeek | OpenAI 兼容 | ✅ 离线 mock 全链路 | 推荐，高性价比；支持 prompt cache |
| OpenAI (GPT-4o / o-series 等) | OpenAI 兼容 | ✅ 离线 mock 全链路 | 原生协议，覆盖最完整 |
| OpenRouter | OpenAI 兼容 | ✅ 离线 mock 全链路 | 多模型路由，统一 Key |
| Moonshot (Kimi) | OpenAI 兼容 | ✅ 离线 mock 全链路 | 支持 prompt cache |
| OpenCode Zen | OpenAI 兼容 | ✅ 离线 mock 全链路 | 免费层限流（1 req/s，已由 RateLimiter 处理） |
| Groq | OpenAI 兼容 | ✅ 离线 mock 全链路 | 超快推理 |
| SiliconFlow | OpenAI 兼容 | ✅ 离线 mock 全链路 | 开源模型聚合 |
| 通义千问 (DashScope) | OpenAI 兼容 | ✅ 离线 mock 全链路 | 国内可直连 |
| 智谱 GLM | OpenAI 兼容 | ✅ 离线 mock 全链路 | 国内可直连 |
| 本地 Ollama | 非 OpenAI（独立适配器） | ✅ Agent 闭环复用 | 无需联网 / 无需 `openai` SDK |
| 任意 OpenAI 兼容自建端点 | OpenAI 兼容 | ✅ 离线 mock 全链路 | 只需填 `base_url` |

> 说明：上表「✅ 离线 mock 全链路」代表**该协议形态**已被端到端测试覆盖；具体到某一模型是否「聪明到能稳定完成复杂任务」，属于模型能力范畴，请用自带 Key 实测。

---

## 3. 安全引擎不会误杀正常命令（链接完整性）

核心链路里的「安全评估」环节已做**降误杀**处理：文件读写、`git` 常规操作、包管理（`pip` / `npm` / `pnpm` / `poetry` / `cargo` / `go`）、测试运行（`pytest` / `unittest`）、lint（`ruff` / `black` / `mypy` / `eslint` / `tsc`）等常见开发命令会被判定为 `none`，**不会被拦截或反复确认**；而 `rm -rf`、强制 `push --force`、`dd`、`mkfs`、写系统关键路径（`/etc`、`/usr/lib` 等）仍按原风险拦截。相关防护由 `tests/test_safety_benign.py` 与 `tests/test_e2e_mock_server.py::test_mock_server_run_shell_benign_passes_safety` 固化。

---

## 4. 如何自己复现验证

```bash
# 1) 激活虚拟环境 (Windows)
.venv\Scripts\activate

# 2) 跑传输层 + Agent 闭环 + 安全降误杀 + 5xx 重试 的离线端到端测试
pytest tests/test_e2e_mock_server.py tests/test_safety_benign.py -q

# 3) 跑安全护栏既有回归
pytest tests/test_shell_safety_guard.py tests/test_security_v2.py -q

# 4) 实跑某 Provider (需自带 Key): 连通性测试 + 对话
qxt setup            # 选择 Provider 并填入 API Key
qxt /model           # 交互选择器 + 连通性测试
qxt "用 pytest 跑一下测试并告诉我结果"
```

---

## 5. 不安装 `openai` 也能跑非 OpenAI 场景

`openai` 包**仅作 HTTP 传输层**，核心逻辑不依赖它（模块加载时不 import `openai`，只在真正发请求时延迟导入）。因此：

```bash
pip install qingxiaotuan          # 不含 openai
qxt setup                         # 选择 本地 Ollama / 自定义非 OpenAI 适配器
qxt "..."                    # 正常可用
```

只有当你选了 OpenAI 兼容 Provider 却未装 SDK 时，才会收到清晰可执行的报错提示（安装 `pip install qingxiaotuan[openai]`）。详见 [README.md](README.md) 的「依赖边界」一节。
