# 开源发布清单 (Open-Source Release Checklist)

把青小团推到 GitHub / Gitee 前的逐项核对。已完成的打 `[x]`，待办的打 `[ ]`。

## 仓库初始化

- [ ] `git init` 并配置 `.gitignore`（已含 `.env` / `.venv` / `.demo-home` / `*.egg-info` / `__pycache__`）
- [ ] 首次提交（建议：`git add -A && git commit -m "feat: 青小团 CLI 首发 — 融合 dsh + hermes 的自研 Agent"`）
- [ ] 创建 GitHub 仓库并在本地 `git remote add origin <url>`
- [ ] `git push -u origin main`（注意分支名：GitHub 默认 `main`）

## 仓库元数据

- [x] `LICENSE`（MIT）
- [x] `README.md`（定位 / 架构 / 快速开始 / 多供应商 / 路线图 / 致谢）
- [x] `CONTRIBUTING.md`
- [x] `CODE_OF_CONDUCT.md`
- [ ] 仓库 Description + Topics（`agent`, `cli`, `deepseek`, `llm`, `openai`, `autonomous-agent`, `plugin-architecture`）
- [ ] 勾选 Issues / Discussions / 安全策略（Security Policy）

## 敏感信息复核

- [x] `.env.example` 仅含占位符（`sk-your-...-here`），无真实密钥
- [x] 源码/测试/文档无硬编码真实 API Key（测试用 `sk-ABC...` 占位）
- [x] `.demo-home/` 已被 `.gitignore` 排除
- [ ] 提交前再跑一次：`grep -rInE "sk-[A-Za-z0-9]{20,}" . --exclude-dir=.venv` 应为空

## 可安装性 & 测试

- [x] `pip install -e .` 成功（`qxt` 入口可用）
- [x] `qxt --version` / `qxt --help` 正常
- [x] `qxt config validate` 通过
- [x] `pytest tests/ -q` 离线全绿（环境干净时）
  - 注意：`tests/test_opencode_zen_config.py::test_dotenv_loading` 依赖干净环境变量；
    若本机已设 `OPENCODE_ZEN_API_KEY` 会"失败"（属预期，因 `load_dotenv` 不覆盖已有变量）。
    用 `env -u OPENCODE_ZEN_API_KEY pytest ...` 可验证。

## 首次发布建议

1. 先在私有仓库跑一轮，确认 `qxt chat` 用 OpenCode Zen 免费层能正常对话。
2. 写一篇 release notes，强调差异化：**后台自主 + 进程级沙箱并发 + 多供应商热切换 + 缓存/compact 工程**。
3. 在 README 顶部放一张架构图（可选）和一句能打动人的 slogan。
4. 准备 1~2 个 demo 录屏（后台任务 + 并行子 Agent）放 README，开源项目的第一印象很重要。

## 版本与迭代

- 当前版本 `0.1.0`（见 `pyproject.toml`）。
- 首发后建议立即打 `v0.1.0` tag：`git tag v0.1.0 && git push --tags`。
- 路线图见 README「路线图」一节。
