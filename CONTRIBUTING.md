# 贡献指南 (Contributing)

感谢你对 **青小团 (Qingxiaotuan)** 感兴趣！

青小团是一个以「最小影响半径」为核心理念的纯 Python Agent CLI：shell 执行前静态风险拦截、自我改进闭环、10 个进程内外部能力引擎。设计上借鉴了 DeepSeek Harness 的插件化微内核与 Hermes Agent 的自进化技能蒸馏思路，并在此基础上有自己的工程取舍。

我们欢迎一切让"青小团"更聪明、更稳、更能干的贡献。

## 开发环境

```bash
git clone <your-fork>
cd Qingxiaotuan-Agent
python -m venv .venv && source .venv/Scripts/activate   # Windows (Git Bash); PowerShell 用 .venv\Scripts\Activate.ps1
# macOS/Linux: python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```

> 测试全部离线（Mock 模型，不联网、不读真实密钥）。CI 与本机都用同一套 `.venv`。

## 架构速览

```
qingxiaotuan/
  app.py            内核装配 (build_kernel) 与 Agent 工厂 (create_agent)
  core/             agent(ReAct 循环) / prompts / context / subagents / background / sandbox / retry / swarm
  models/           模型适配层: openai_compat / anthropic / router(运行时热切换) / provider_catalog / plugin
  tools/            内置工具 + 派发 (dispatch_tasks 并发子任务)
  memory/           三层记忆 (FTS5) + 会话事件流 (SessionStore)
  config/           四层组合配置 (默认 → 用户 → profile → patch)
  ext/              9 个纯 Python 外部能力引擎 (diff/crypto/index/ansi/safety/json/search/notify/rules) + 注册中心（skill_market 仅为规划，未接入注册表）
  skills/           技能系统 (加载 / 蒸馏 / 技能市场)
  self_improve/     自我改进闭环 (执行后复盘 → 护栏下次分发前生效)
  cron/             定时任务 (持久化调度 + 常驻守护进程)
  audit/            审计日志
  context/          上下文构建与 codebase 地图
  cli/              qxt 命令行入口与子命令
  ui/               终端界面 (Rich REPL / 全屏 TUI / 吉祥物 / 共享主题 theme.py)
```

设计原则：**模型可插拔、工具即插件、记忆可成长、会话可回放**。任何新能力优先以 Plugin / Tool / Skill 形式接入，避免在主干里堆 if-else。

## 提交流程

1. Fork 并切出特性分支：`git checkout -b feat/your-feature`
2. 写代码 + **补测试**（新功能必须有对应离线测试）
3. 跑通 `pytest tests/ -q` 与 `qxt config validate`
4. 提交信息清晰说明「为什么」：`feat: 支持 Claude 网关热切换`
5. 发起 PR，描述动机与测试覆盖

## 代码规范

- Python 3.11+，类型注解尽量完整
- 不允许在源码/日志里硬编码密钥；密钥一律走环境变量 + `.env`（已被 `.gitignore` 排除）
- 中文注释与中文用户文案为主（项目面向中文用户），标识符用英文
- 工具/插件改动需同步更新 README 的相关章节

## 报告问题

- Bug：开 Issue，附复现步骤、`qxt doctor` 输出、`qxt config dump` 脱敏版
- 安全漏洞：**请勿公开 Issue**，按 [SECURITY.md](./SECURITY.md) 通过 GitHub 私密漏洞报告提交

## 行为准则

见 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)。我们对所有参与者一视同仁，零容忍骚扰与歧视。
