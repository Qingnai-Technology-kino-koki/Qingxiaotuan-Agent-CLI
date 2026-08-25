# 青小团 CLI (Qingxiaotuan Agent CLI)

> 一个以"最小影响半径"为核心设计理念的 Agent CLI：在 Agent 替你改东西之前，先想清楚这一步会动到哪里、会不会翻车。
>
> 当前状态：**纯 Python 全栈** — 核心内核 + 外部能力引擎全部使用 Python 实现。

一个独立的 Agent 命令行工具，融合多项开源 Agent 项目的核心理念二次开发：

| 来源 | 吸收的理念 |
| --- | --- |
| **DeepSeek Harness (dsh)** | 微内核（Cordis 风格）、一切皆插件、Profile 组合式配置、模型中立适配、headless 一次性任务、append-only 会话事件流 |
| **Hermes Agent** | 三层记忆（会话/事实/技能）、技能自进化闭环、SOUL.md 身份、SQLite FTS5 跨会话检索、自注册工具、cron 定时任务 |
| **Claude Code** | 流畅 CLI（多行输入/命令历史）、实时思考过程与工具状态展示、大上下文机制（整库结构+文件+历史智能管理）、`/clear` `/help` 等交互命令 |
| **Hermes 自主进化** | Agent 持续自主迭代开发 Loop（分析→规划→实现→自测→核实→汇报），直到用户明确满意才停止 |

## 吉祥物：青小团 (Mascot)

青小团是一只圆润的青色小团子，呼应项目名 Qingxiaotuan。它不是静态 logo，而是**一个有"生命体征"的状态机**——随任务推进实时变化，让你一眼看清 Agent 现在在干什么：

```
  idle    thinking    working     alert      done
  ( ◡ )   ? ⠋        ( • • )    ( @ • )    ✨
 ╭─────╮ ( ◠ ◠ )    ╭─────╮    ╭─────╮    ( ^ ^ )
 ╰─────╯ ╭─────╮    ╰─────╯    ╰─────╯    ╭─────╮
         ╰─────╯     ▔▔▔▔▔      ⚠ 拦截     ╰─────╯
  待命    思考(摇摆)  干活(浮动)  安全拦截    完成(弯月)
```

| 状态 | 触发时机 | 视觉 |
| --- | --- | --- |
| `idle` 待命 | 空闲 / 等待输入 | 平静呼吸 |
| `thinking` 思考 | 模型推理中 | 左右摇摆 + 旋转光标 |
| `working` 干活 | 工具调用执行中 | 身体浮动 + 进度环 |
| `alert` 警戒 | **安全护栏拦截**危险命令 | 变橙、抖动（最小影响半径生效的直观信号） |
| `done` 完成 | 任务收尾 | 弯月眼 + 星光 |

## 设计理念 (Design Principles)

1. **最小影响半径 (Minimum Blast Radius)** — 每次 shell 命令执行前，Python `safety` 引擎会做静态风险分析，`critical` 级命令（`rm -rf /` / `git push --force` / `DROP TABLE`）在 `run_shell` 执行前被**默认拦截**。
2. **可重放的工作流 (Replayable Workflow)** — 把经验固化为可审计、可重放的规则与技能。
3. **自我改进闭环 (Self-Improve Loop)** — 每次执行后自动复盘，从成功/失败中抽取经验生成护栏。
4. **纯 Python 能力内核** — 所有外部引擎（diff/crypto/index/ansi/safety/json/search/notify）均以 Python 实现，统一 JSONL IPC 协议与内核对话。

## 安装

> 要求 Python ≥ 3.11，纯 Python 全栈，无需编译 C/TS。

```bash
pip install -e .
qxt setup                 # 30秒向导: 选供应商 + 填 API Key
qxt chat                  # 开聊
```

**可选增强**：安装 cryptography 获得生产级 AES-256-GCM 加密（未安装时 crypto 引擎自动使用标准库回退）：

```bash
pip install cryptography
```

## 架构

```
qingxiaotuan/
├── cli/          命令行层
├── core/         内核与编排: kernel / agent / devloop / background
├── config/       配置: defaults / loader / plugin / validate
├── ext/          纯 Python 外部引擎: diff/crypto/index/ansi/safety/json/search/notify + registry
├── models/       模型适配: openai_compat / anthropic / provider_catalog
├── memory/       记忆: store(FTS5) / sessions(事件流)
├── skills/       技能: manager / plugin
├── tools/        工具: base / shell / filesystem / web / external(引擎集成)
├── context/      上下文: indexer / manager
├── cron/         定时任务
├── ui/           TUI (repl + fullscreen)
└── resources/    内置技能模板 + SOUL.md
```

## 外部能力引擎 (纯 Python)

| 引擎 | 功能 |
| --- | --- |
| `diff` | 行/词级 diff + patch + 3-way merge |
| `crypto` | AES-256-GCM + PBKDF2-HMAC-SHA256 + 指纹 |
| `index` | FNV-1a 增量符号索引 |
| `ansi` | 终端转义解析/剥离/渲染 |
| `safety` | 最小影响半径护栏: 风险评分 + blast radius + 阻断 |
| `json` | RFC 6901 Pointer / 逐路径 diff / 深合并 |
| `search` | 递归正则检索 (忽略 node_modules/.git) |
| `notify` | 跨平台桌面通知 |

```bash
qxt ext engines                 # 列出可用引擎
qxt ext selftest                # 健康检查
```

## 开源

青小团是 **MIT 许可** 的开源项目。模型中立，可随时切换 DeepSeek / Claude / Gemini / 本地模型。

- **许可证**：[MIT](./LICENSE)
- **参与贡献**：见 [CONTRIBUTING.md](./CONTRIBUTING.md)
