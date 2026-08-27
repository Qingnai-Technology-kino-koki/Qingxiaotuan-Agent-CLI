# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

**English** | [简体中文](README_zh-CN.md) | [繁體中文](README_zh-TW.md) | [日本語](README_ja.md) | [한국어](README_ko.md) | [Español](README_es.md) | [Português (BR)](README_pt-BR.md) | [Français](README_fr.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> An agent CLI built around one idea: **know your blast radius before the agent touches anything.**
> Every shell command is risk-analyzed and rated *before* it runs — critical operations are blocked by default, and a living mascot shows you exactly what state the agent is in.

Pure-Python, zero-compile, model-neutral. 119 modules, ~21k lines of source backed by **591 offline tests** (mock-server end-to-end included) on a Python 3.11–3.13 CI matrix.

<!-- 📹 TODO(demo): drop a 30–60s terminal recording here showing:
     qxt chat → task → mascot transitions → safety interception moment.
     Until then, the ASCII state machine below is the real thing. -->

## The Mascot Is the Status Bar

Qingxiaotuan ("little green dumpling") is not a static logo — it is a **state machine with vital signs**, rendered live in your terminal so you always know what the agent is doing:

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| State | When | Visual |
| --- | --- | --- |
| `idle` | waiting for input | calm breathing |
| `thinking` | model reasoning | swaying + spinner |
| `working` | executing a tool call | floating + progress ring |
| `alert` | **safety guardrail blocked** a dangerous command | turns orange, trembles — blast-radius control you can *see* |
| `done` | task finished | crescent eyes + sparkle |

## Why This One

- **Minimum Blast Radius by default** — before every shell command runs, the pure-Python `safety` engine does static risk analysis; `critical` commands (`rm -rf /`, `git push --force`, `DROP TABLE`) are **blocked before execution**, not merely logged after.
- **Reversible workspace moves** — `/diff` to review changes, `/undo` to roll back (`--safe` mode included), sessions replayable via an append-only event stream.
- **10 capability engines, all pure Python, zero IPC** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market, wired through a registry with health checks (`qxt ext selftest`).
- **Model-neutral** — DeepSeek, Claude, Gemini or local models behind OpenAI-compatible / Anthropic adapters; switch any time with `/model`. `/route` estimates task difficulty and suggests a model (advisory).
- **Multi-agent swarm** — `/swarm` plans with a strong model, executes subtasks with cheaper models concurrently, then has the strong model accept-or-reject the result.
- **Self-improve loop** — after each run the reflector diagnoses failures (pytest/npm/git/cargo/dotnet aware) and distills guardrails, so the same mistake is caught earlier next time.
- **Memory that survives restarts** — three layers (session / facts / skills) with SQLite FTS5 full-text recall across sessions.
- **UI in 10 languages** — 简体中文 / 繁體中文 / English / 日本語 / 한국어 / Español / Português (Brasil) / Français / Deutsch / Русский. Picked at first run (`qxt setup`), change any time in the config; agent replies follow your choice.
- **Plain-text terminal output** — every CLI command prints pure text: no colors, no bold, no ANSI escapes (great for piping, logging, and CI). The interactive TUI keeps a single Kimi Code–style accent color (`#4FA8FF` light blue) for titles, badges and prompts; everything else stays unadorned.
- **Fast cold start** — heavy SDKs (`openai`, `httpx`, `anthropic`) and command modules are lazy-imported, so `qxt --help` / `qxt --version` and the REPL start in well under two seconds.

## Quick Start

> Requires Python ≥ 3.11. Pure Python — nothing to compile.

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

Headless one-shot tasks work too:

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **Crypto note:** the `crypto` engine is standard-library only (PBKDF2-HMAC-SHA256 key derivation + SHA256-keystream stream cipher + SHA-256 fingerprints). The stream cipher is a lightweight design without authentication tags — fine for tamper-evident local use, not a high-security primitive.

## Slash Commands

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/impact` `/mcp` `/hooks` `/image` `/images` `/clear-images` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

Highlights:

| Command | What it does |
| --- | --- |
| `/plan` | read-only analysis mode — mutation tools are intercepted |
| `/swarm` | multi-agent collaboration (strong plan → parallel cheap execution → strong acceptance) |
| `/route` | difficulty estimate + model suggestion (advisory, no auto-switching) |
| `/compact` | fold old history to reclaim context budget |
| `/cost` | token usage, cache hit rate, estimated spend |
| `/undo` | transactional rollback via Mutation Ledger (`N` / `<file>` / `all` / `--safe`); git fallback |
| `/impact` | show operation ledger + blast radius (files changed, steps taken) |
| `/mcp` | list connected MCP servers and their tools (`/mcp <server>` shows that server's tools) |
| `/hooks` | user-level hooks: list configured scripts (`/hooks`); `/hooks test` triggers one |
| `/image` | attach an image to the next turn (`/image <path|url|data:URI>`); `/images` lists pending; `/clear-images` clears |

## Capability Engines

| Engine | What it provides |
| --- | --- |
| `diff` | line/word-level diff + patch + 3-way merge |
| `crypto` | PBKDF2-HMAC-SHA256 derivation + SHA256-keystream stream cipher + fingerprints |
| `index` | FNV-1a incremental symbol index |
| `ansi` | terminal escape parse / strip / render |
| `hooks` | user-level pre/post tool hooks — scriptable, fail-safe, argv-only (no shell) |
| `safety` | minimum-blast-radius guardrail: risk scoring + blast radius + blocking |
| `ledger` | transactional mutation ledger: pre-exec snapshots + auto-rollback + fine-grained undo (first-mover) |
| `vision` | multimodal: user/tool image attach → image_url blocks; gated by ModelCapabilities.vision; Anthropic translation |
| `json` | RFC 6901 pointer / per-path diff / deep merge |
| `search` | recursive regex search (ignores node_modules/.git) |
| `notify` | cross-platform desktop notifications |
| `rules` | YAML policy validation (no-eval safe expressions) |
| `skill-market` | skill package registry: pull / publish / search |

```bash
qxt ext engines     # list engines actually available
qxt ext selftest    # launch each engine, report health
```

## Architecture

```
qingxiaotuan/
├── cli/          command layer (+ fullscreen TUI)
├── core/         kernel & orchestration: kernel / agent / devloop / background / swarm
├── config/       defaults / loader / plugin / validate
├── ext/          10 pure-Python engines + registry
├── models/       adapters: openai_compat / anthropic / provider_catalog / router
├── memory/       store (SQLite FTS5) / sessions (append-only events)
├── skills/       manager / plugin
├── tools/        base / shell / filesystem / web / external / code / audit / mcp (stdio MCP bridge)
├── context/      indexer / manager
├── cron/         scheduled jobs
├── ui/           repl + fullscreen TUI (shared theme)
└── resources/    bundled skill templates + SOUL.md identity
```

## Quality

- **591 offline tests** — no network required, including end-to-end runs against a local OpenAI-compatible mock server (agent tool loop, streaming, background worker, real `qxt` subprocess).
- **CI matrix**: Python 3.11 / 3.12 / 3.13.
- Only five runtime dependencies: `openai`, `pyyaml`, `rich`, `prompt_toolkit`, `httpx`.

## Roadmap

- [ ] Automatic model routing (wire `router.enabled` into the agent loop; today `/route` is advisory)
- [ ] Publish to PyPI (`pip install qingxiaotuan`)
- [ ] Skill market public registry

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md). Translations for this README are coordinated in-file; fix any language via PR.

## License & Credits

MIT — see [LICENSE](./LICENSE).

Standing on the shoulders of open source; ideas absorbed and re-implemented from scratch in Python:

- **DeepSeek Harness (dsh)** — micro-kernel (Cordis-style), everything-is-a-plugin, profile-based config, model-neutral adaptation, headless tasks, append-only session event stream
- **Hermes Agent** — three-layer memory, skill self-evolution loop, SOUL.md identity, SQLite FTS5 cross-session recall, cron jobs
- **Claude Code** — fluid CLI interactions, live thinking/tool status display, large-context management, interactive slash commands
