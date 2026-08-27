# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - Unreleased

### Added

- **Safety-first agent core**: static risk scoring before every shell execution — critical-level commands are blocked by default; unified red-line rules (recursive `rm`, force-push, Windows `rd /s`) shared by the safety engine, YOLO mode and code tools.
- **Plan mode**: read-only enforcement with fail-closed semantics — unknown commands are denied rather than silently allowed.
- **Self-improvement loop**: post-execution review distills guardrails that take effect before the next dispatch.
- **10 pure-Python external engines** behind an in-process registry (`qxt ext selftest`): diff, crypto, index, ansi, safety, json, search, notify, rules, skill_market — zero compilation, zero IPC overhead.
- **Model layer**: OpenAI-compatible protocol plus an Anthropic adapter, runtime hot-swap router and provider catalog; model-neutral by design.
- **Memory**: three-tier memory on SQLite FTS5 plus an append-only session event stream.
- **Cron subsystem**: persistent jobs, detached daemon (`qxt cron start --detach`), desktop notifications, output-to-file for CI consumption, per-job logs.
- **Swarm multi-agent collaboration** (`/swarm`): strong-model planner → concurrent sandboxed weak-model workers → strong-model acceptor, communicating over a shared blackboard.
- **RetryPolicy component**: exponential backoff with jitter, `Retry-After` aware, fast-fail on auth errors, injectable sleep/rng.
- **Client-side rate limiter** (`RateLimiter`): token-bucket throttling + concurrency semaphore applied before every model request — protects free-tier endpoints (e.g. OpenCode Zen) from tripping server-side limits. Opt-in via `model.rate_limit.enabled` (default off, so paid models are unaffected); injectable sleep/clock for testing.
- **Audit logging** of tool calls and risk decisions.
- **Terminal UI**: fullscreen workbench, DeepSeek-style dual-frame REPL, dynamic mascot state machine (idle/thinking/working/alert/done), live token/context meters, shared theme module as the single source of color truth.
- **Plain-text terminal output**: every CLI command prints pure text — no colors, no bold, no ANSI escapes — via a `PlainConsole` that strips Rich markup and syntax highlighting; the interactive TUI keeps a single Kimi Code–style accent color (`#4FA8FF` light blue) for titles, badges and prompts, with all other prompt_toolkit default styles explicitly reset.
- **Fast cold start**: heavy SDKs (`openai`, `httpx`, `anthropic`) and command modules are lazy-imported, cutting `qxt --help` / `--version` and REPL startup from ~4.3s to ~1.7s (imported modules 1607 → 664).
- **CLI surface**: ~20 slash commands, `qxt doctor` health check, `qxt bench`, `qxt open file:line` precise-reference jump, four-layer config merge with profiles.
- **Skills system** with loading, distillation and a marketplace engine.
- **Hooks event surface**: 8 lifecycle events (PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStop, PreCompact, SessionStart, SessionEnd) with shell-command hooks configurable per event; PreToolUse can deny tool calls, UserPromptSubmit can inject context.
- **Checkpoint / rewind**: a `checkpoint` session tool — `save` marks the conversation position and snapshots pending file mutations via the mutation ledger, `restore` undoes every file change since the mark and truncates the dialogue back to that point, `list` shows saved checkpoints. Enables "try a risky edit, roll back in one step" workflows inside one session.
- **Layered instruction memory** (对标 Claude Code 的 CLAUDE.md 分层): three-tier discovery — user-global (`<qxt_home>/AGENTS.md` and the community-standard `~/.agents/AGENTS.md`), project ancestors (each directory from the workspace up to the nearest git repo root, never escaping the repository boundary), then the workspace root with candidate priority QXT.md > AGENTS.md > CLAUDE.md > .qxt.md. Byte-stable output per environment, prompt-cache friendly.
- **Typed subagent delegation** (`task` / `background_status` tools, 对标 Claude Code 的 Task 工具): the main agent delegates a single self-contained task to an isolated typed sub-agent — four built-in types (`general-purpose`, `explore`, `plan`, `coder`) each carrying a role directive appended to the sub-agent's system prompt (`AgentType.system_extra`, threaded through thread isolation and the process sandbox) and, for read-only types (`explore` / `plan`), a physical write block via `exclude_tools=readonly_tool_names()` that also excludes `task` itself to prevent recursive spawning. Foreground mode returns the aggregated result block; `run_in_background=true` returns a job id immediately with progress/results queryable later through `background_status` (runner instance cached on the tool context so submit and status share one registry).
- **Internationalized README** in 10 languages with consistent content across translations.
- **Interface i18n (10 UI languages)**: lazy-loaded locale modules (简体中文/繁體中文/English/日本語/한국어/Español/Português do Brasil/Français/Deutsch/Русский) with a current→zh-CN→en→key fallback chain that never raises on missing keys, alias normalization (`zh_CN`/`chs`→`zh-CN`, `jp`→`ja`, `pt-br`→`pt-BR`, …), a first-run numbered language picker (interactive TTY only — never blocks tests/CI/pipes), and reply-language injection into the agent system prompt (zh-CN keeps the historical prompt byte-for-byte for prompt-cache friendliness).

### Changed

- Full stack is now pure Python: the legacy C engine sources and Node/TS build chain were removed from the repository in favor of in-process engines.
- Config directory references unified through `home_dir()` (respects `QXT_HOME`); skill-market registry also honors `QXT_HOME`.
- Silent exception handlers across core modules (background, IPC, ledger, hooks, config loader) now log at debug/warning level instead of swallowing errors.
- REPL banner/status bar/tips, fullscreen workbench states and hints, keymap panel and the `qxt setup` wizard all render through the i18n catalog; agent chat replies follow the configured language. Deep engine logs and raw tool output remain Chinese for now.
- Version bumped to 0.2.0.

### Fixed

- YOLO red-line detection no longer depends on safety-engine availability — a local fallback keeps blocking lethal commands when the engine is missing (fail-closed).
- Plan-mode readonly check no longer fails open on unknown commands; `sed -i`-style writes are now detected.
- Windows cron daemon liveness check no longer risks signaling unrelated processes via `os.kill(pid, 0)`.

### Security

- `scripts/push.sh` no longer disables TLS verification (`http.sslVerify=false` removed); the Windows schannel revocation-check workaround is now opt-in guidance shown on failure instead of a silent default.
- The script also no longer deletes and recreates the `origin` remote destructively; it updates the URL only when it differs.

[0.2.0]: https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/releases/tag/v0.2.0
