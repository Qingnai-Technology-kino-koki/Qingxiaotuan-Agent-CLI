# Promotional Posts for Kawsut (青小团)

---

## Post 1: Hacker News — Show HN

**Title:**
Show HN: Kawsut – A safety-first AI agent CLI that blocks dangerous commands before execution

**Body:**

Hey HN,

I built Kawsut (青小团), an AI agent CLI for your terminal that takes a different approach: **every shell command gets risk-analyzed before it runs**, not after.

**The core idea — blast radius control:**

Most AI coding agents (Aider, Claude Code, Cursor) will happily run `rm -rf /` or `git push --force` if the model decides to. Kawsut's safety engine does static token-based analysis *before* execution and blocks critical commands by default.

It's not just regex — the analyzer穿透s command substitutions (`$(...)`), variable assignments (`R="rm"; $R -rf /data`), and quote wrapping to catch obfuscated dangerous commands.

**What's in the box:**

- **10 pure-Python capability engines** — diff, crypto, index, safety, json, search, notify, rules, skill-market — zero compilation, zero external processes
- **Model-neutral** — works with DeepSeek, Claude, Gemini, or any OpenAI-compatible endpoint via `/model` hot-swap
- **Multi-agent swarm** — `/swarm` plans with a strong model, executes subtasks with cheaper models concurrently
- **3-layer memory** — session / facts / skills on SQLite FTS5, surviving restarts
- **Self-improve loop** — post-execution reflection distills guardrails that take effect before the next run
- **Transactional undo** — `/undo` rolls back file changes via a mutation ledger; `/diff` shows what changed
- **Mascot state machine** — a live terminal character shows idle/thinking/working/alert(done states so you always know what the agent is doing
- **10 UI languages** — 简体中文, English, 日本語, 한국어, Español, Português, Français, Deutsch, Русский, 繁體中文

**Tech details:**

- Pure Python, 5 runtime dependencies (openai, pyyaml, rich, prompt_toolkit, httpx)
- 591 offline tests (no network required), mock-server E2E
- Python 3.11–3.13 CI matrix
- Cordis-style microkernel: everything is a plugin, services discovered by name
- Cold start under 2 seconds (`qxt --help`)

```bash
pip install qingxiaotuan
qxt setup   # pick provider, paste API key
qxt chat    # start talking
```

GitHub: https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI

I'd love feedback on the safety engine design and the microkernel architecture. What other dangerous patterns should it catch?

---

## Post 2: Reddit r/Python

**Title:**
Kawsut (青小团) — a safety-first AI agent CLI in pure Python with blast-radius control [Open Source]

**Body:**

I've been working on an AI agent CLI called **Kawsut** (青小团, "little green dumpling") that I think the Python community might find interesting.

**The key differentiator: minimum blast radius.**

Before every shell command runs, a pure-Python safety engine does static risk analysis. Critical commands (`rm -rf /`, `git push --force`, `DROP TABLE`) are blocked *before execution*, not logged after. The analyzer uses token-based normalization to穿透 command substitutions, variable assignments, and quote wrapping.

**Why it exists:**

I noticed most AI coding agents are willing to run anything the model suggests. That's fine for small tasks, but when an agent has access to your terminal, you want a guardrail that works at the shell level, not the prompt level.

**What it features:**

- **10 pure-Python engines** — diff, crypto, index, safety, json, search, notify, rules, skill-market. All in-process, zero IPC.
- **Model-neutral** — OpenAI-compatible protocol + Anthropic adapter. Switch providers at runtime with `/model`.
- **Multi-agent swarm** — delegate subtasks to cheaper models, have a strong model accept/reject results.
- **3-layer memory** — SQLite FTS5 full-text recall across sessions.
- **Self-improve loop** — post-execution reflection distills guardrails automatically.
- **Transactional undo** — mutation ledger with pre-exec snapshots and fine-grained rollback.
- **10 UI languages** — lazy-loaded locale system with fallback chain.
- **Mascot state machine** — live terminal character shows what the agent is doing.
- **591 offline tests** — everything runs against mock servers, no API keys needed for testing.

**Architecture:**

Cordis-style microkernel — 26 plugins register services, discovered by name. The agent loop (ReAct) is itself a plugin. Adding new tools means writing a handler and registering it; no core changes needed.

```bash
pip install qingxiaotuan
qxt setup
qxt chat
```

5 runtime dependencies: openai, pyyaml, rich, prompt_toolkit, httpx.

GitHub: https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI

Happy to answer questions about the architecture, the safety engine, or anything else.

---

## Post 3: V2EX — 中文

**Title:**
[开源] Kawsut (青小团) — 纯 Python AI Agent CLI，shell 执行前静态风险拦截

**Body:**

做了一个 AI Agent CLI 工具，核心理念是「最小影响半径」——每条 shell 命令执行前先做静态风险分析，`critical` 级操作（`rm -rf /`、`git push --force`、`DROP TABLE`）默认拦截，不是事后记日志。

**和市面上的 Agent CLI 有什么不同：**

大部分 AI 编程工具（Aider、Claude Code、Cursor）是模型说跑就跑。Kawsut 在 shell 层做了一道安全护栏，而且不是简单正则——能穿透命令替换 `$(...)`、变量赋值 `R="rm"; $R -rf /data`、引号包裹等间接写法。

**功能一览：**

- 10 个纯 Python 能力引擎（diff/crypto/index/ansi/safety/json/search/notify/rules/skill-market），零编译零 IPC
- 模型中立：DeepSeek / Claude / Gemini / 任意 OpenAI 兼容端点，`/model` 热切换
- 多 Agent 协作（`/swarm`）：强模型规划 → 便宜模型并行执行 → 强模型验收
- 三层记忆（SQLite FTS5）：会话 / 事实 / 技能，跨会话检索
- 自我改进闭环：执行后自动复盘，提炼护栏下次生效
- 事务化回滚（`/undo`）：操作账本 + 预快照 + 精细回滚
- 10 语言 UI：中/英/日/韩/西/葡/法/德/俄/繁中
- 吉祥物状态机：终端实时显示 idle/thinking/working/alert/done
- 591 离线测试，mock-server E2E，Python 3.11–3.13 CI

**技术栈：**

纯 Python，5 个运行时依赖（openai/pyyaml/rich/prompt_toolkit/httpx）。Cordis 风格微内核，26 个插件注册服务，名字发现而非直接 import。冷启动 <2s。

```bash
pip install qingxiaotuan
qxt setup
qxt chat
```

GitHub：https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI

欢迎提 issue 和 PR。安全引擎的模式库、微内核的插件机制、i18n 的回退链设计都欢迎讨论。
