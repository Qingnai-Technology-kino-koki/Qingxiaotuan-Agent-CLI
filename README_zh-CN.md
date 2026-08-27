# 青小团 CLI (Qingxiaotuan Agent CLI)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

[English](README.md) | **简体中文** | [繁體中文](README_zh-TW.md) | [日本語](README_ja.md) | [한국어](README_ko.md) | [Español](README_es.md) | [Português (BR)](README_pt-BR.md) | [Français](README_fr.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> 一个以"**最小影响半径**"为核心设计理念的 Agent CLI：Agent 替你改东西之前，先想清楚这一步会动到哪里、会不会翻车。
> 每条 shell 命令执行前先做风险分析与评级——危险操作默认拦截；一只"活的"吉祥物实时告诉你 Agent 此刻处于什么状态。

纯 Python、零编译、模型中立。119 个模块、约 2.1 万行源码，由 **591 个离线测试**（含本地 mock server 端到端）与 Python 3.11–3.13 CI 矩阵护航。

<!-- 📹 TODO(demo): 此处放一段 30–60 秒终端录屏:
     qxt chat → 下发任务 → 吉祥物状态流转 → 安全拦截瞬间。 -->

## 吉祥物就是状态栏

青小团是一只圆润的青色小团子，它不是静态 logo，而是**一个有"生命体征"的状态机**，在终端里实时渲染，让你一眼看清 Agent 在干什么：

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ 已拦截     ╰─────╯
   待命      思考         工具调用      安全拦截      完成
```

| 状态 | 触发时机 | 视觉 |
| --- | --- | --- |
| `idle` 待命 | 空闲 / 等待输入 | 平静呼吸 |
| `thinking` 思考 | 模型推理中 | 左右摇摆 + 旋转光标 |
| `working` 干活 | 工具调用执行中 | 身体浮动 + 进度环 |
| `alert` 警戒 | **安全护栏拦截**危险命令 | 变橙、抖动——看得见的最小影响半径 |
| `done` 完成 | 任务收尾 | 弯月眼 + 星光 |

## 为什么选它

- **默认最小影响半径** — 每条 shell 命令执行前，纯 Python `safety` 引擎先做静态风险分析；`critical` 级命令（`rm -rf /`、`git push --force`、`DROP TABLE`）在执行前被**直接拦截**，而不是事后记日志。
- **工作区可回滚** — `/diff` 审查变更，`/undo` 快速回退（含 `--safe` 模式）；会话基于 append-only 事件流，可重放。
- **10 个能力引擎，全部纯 Python、零 IPC** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market，经注册表统一管理并带健康检查（`qxt ext selftest`）。
- **模型中立** — DeepSeek、Claude、Gemini 或本地模型，走 OpenAI 兼容 / Anthropic 适配器，随时 `/model` 切换；`/route` 可估算任务难度并建议模型（咨询式）。
- **多 Agent 协作** — `/swarm` 用强模型规划、并发派发给廉价模型执行子任务、再由强模型验收。
- **自我改进闭环** — 每次执行后自动复盘（识别 pytest/npm/git/cargo/dotnet 失败原因），沉淀为护栏规则，同一个坑不摔第二次。
- **记忆可跨会话** — 会话 / 事实 / 技能三层记忆，SQLite FTS5 全文检索历史。
- **纯文本终端输出** — 所有 CLI 命令输出纯文本：无颜色、无加粗、无 ANSI 转义（方便管道、日志与 CI 消费）。交互式 TUI 仅保留一个 Kimi Code 风格强调色（`#4FA8FF` 浅蓝）用于标题、徽章与提示符，其余一律无修饰。
- **冷启动快** — 重 SDK（`openai` / `httpx` / `anthropic`）与命令模块全部延迟导入，`qxt --help` / `qxt --version` 与 REPL 两秒内启动。

## 快速开始

> 要求 Python ≥ 3.11，纯 Python 全栈，无需编译。

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30 秒向导: 选供应商 + 填 API Key
qxt chat      # 开聊
```

也支持 headless 一次性任务:

```bash
qxt run "把 utils.py 拆成两个模块, 并保证测试通过"
```

> **加密说明**: crypto 引擎完全基于标准库（PBKDF2-HMAC-SHA256 密钥派生 + SHA256-keystream 流式加密 + SHA-256 指纹）。该流式加密是轻量设计（无认证标签），适合本地防篡改场景, 不属于高安全强度原语。

## 斜杠命令

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

常用亮点:

| 命令 | 说明 |
| --- | --- |
| `/plan` | 只读分析模式——修改类工具被拦截 |
| `/swarm` | 多 Agent 协作（强模型规划 → 廉价模型并发执行 → 强模型验收） |
| `/route` | 难度评估 + 模型建议（咨询式, 不自动切换） |
| `/compact` | 折叠旧历史, 释放上下文预算 |
| `/cost` | token 用量、缓存命中率、费用估算 |
| `/undo` | 工作区快速回滚（`all` / `--safe` / 单文件） |

## 能力引擎

| 引擎 | 功能 |
| --- | --- |
| `diff` | 行/词级 diff + patch + 3-way merge |
| `crypto` | PBKDF2-HMAC-SHA256 密钥派生 + SHA256-keystream 流式加密 + 指纹 |
| `index` | FNV-1a 增量符号索引 |
| `ansi` | 终端转义解析/剥离/渲染 |
| `safety` | 最小影响半径护栏: 风险评分 + blast radius + 阻断 |
| `json` | RFC 6901 Pointer / 逐路径 diff / 深合并 |
| `search` | 递归正则检索 (忽略 node_modules/.git) |
| `notify` | 跨平台桌面通知 |
| `rules` | YAML 规则策略校验 (无 eval 安全表达式) |
| `skill-market` | 技能包 registry: 拉取 / 发布 / 检索 |

```bash
qxt ext engines     # 列出可用引擎
qxt ext selftest    # 逐个启动引擎, 报告健康度
```

## 架构

```
qingxiaotuan/
├── cli/          命令行层 (+ 全屏 TUI)
├── core/         内核与编排: kernel / agent / devloop / background / swarm
├── config/       配置: defaults / loader / plugin / validate
├── ext/          10 个纯 Python 引擎 + registry
├── models/       模型适配: openai_compat / anthropic / provider_catalog / router
├── memory/       记忆: store(SQLite FTS5) / sessions(append-only 事件流)
├── skills/       技能: manager / plugin
├── tools/        base / shell / filesystem / web / external / code / audit
├── context/      上下文: indexer / manager
├── cron/         定时任务
├── ui/           repl + fullscreen TUI (共享主题)
└── resources/    内置技能模板 + SOUL.md 身份
```

## 质量

- **591 个离线测试**——全程无需联网, 含本地 OpenAI 兼容 mock server 的端到端测试（Agent 工具循环、流式、后台 worker、真实 `qxt` 子进程）。
- **CI 矩阵**: Python 3.11 / 3.12 / 3.13。
- 仅 5 个运行时依赖: `openai`, `pyyaml`, `rich`, `prompt_toolkit`, `httpx`。

## Roadmap

- [ ] 自动模型路由（把 `router.enabled` 接入 Agent 循环; 目前 `/route` 为咨询式）
- [ ] 发布 PyPI（`pip install qingxiaotuan`）
- [ ] 技能市场公共 registry

## 参与贡献

欢迎 Issue 与 PR——见 [CONTRIBUTING.md](./CONTRIBUTING.md)。本 README 的各语言版本可直接提 PR 修正。

## 许可与致敬

MIT——见 [LICENSE](./LICENSE)。

站在开源的肩膀上, 以下理念经消化后在 Python 中从零实现:

- **DeepSeek Harness (dsh)** — 微内核 (Cordis 风格)、一切皆插件、Profile 组合式配置、模型中立适配、headless 任务、append-only 会话事件流
- **Hermes Agent** — 三层记忆、技能自进化闭环、SOUL.md 身份、SQLite FTS5 跨会话检索、cron 定时任务
- **Claude Code** — 流畅 CLI 交互、实时思考/工具状态展示、大上下文管理、交互式斜杠命令
