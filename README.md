# Qingxiaotuan Agent CLI (青小团)

> **"Model + Harness = Agent."** — Split "how to think" from "how to run it safely," and hand you both keys.
> A safety-first, model-agnostic, pure-Python AI Agent harness. `v0.2.014` · MIT · Python ≥ 3.10

**Language:** **English** · [简体中文](README_zh-CN.md) · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [Español](README_es.md) · [Português (Brasil)](README_pt-BR.md) · [Français](README_fr.md) · [Deutsch](README_de.md) · [Русский](README_ru.md)

**Must-reads:** [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · `qxt models list-providers`

---

## TL;DR

Most agent CLIs ship *married to a vendor.* Qingxiaotuan is the opposite: it's a **brain-agnostic chassis**. Hot-swap across 48 providers, run fully offline, roll back destructive mistakes, and see the blast radius *before* the command fires. **The model does the thinking; it does the not-blowing-up.**

**What it is not**: a captive wrapper around one model (hot-swap / self-host / offline, your call); an IDE sidekick (it's a plain terminal tool, drivable via ACP by VSCode/Zed/JetBrains); or a single leaky abstraction (microkernel plugin architecture for builders, plus a one-shot `setup` for everyone else).

---

## Why it's different

| The thing | How far it goes |
|---|---|
| 🛡️ **Safety-first** | Four gates: static risk scoring, blast-radius pre-blocking, YOLO-redline floor, transactional ledger with exact `/undo` |
| 🔌 **Model-agnostic** | 48 providers + local Ollama + hot-swap + auto-routing (`router.*`) |
| 🧠 **Three main loops** | ReAct / Planner-Execute / DevLoop — pluggable; one kernel, different "thinking rhythms" |
| 🔧 **Microkernel** | One-line `@plugin`, service registry, append-only event bus, hook middleware — a hacker's playground |
| 🗂️ **Memory** | SQLite FTS5 + session event stream; three-tier memory, `/undo`, checkpoint, replay, Trajectory export |
| 🧩 **Ecosystem** | MCP + ACP — plug tools in, or let your IDE drive it |
| 🌍 **Ten languages** | zh-CN default, native localization of the whole UI |
| 🐍 **Pure Python** | ~414 `.py` files / ~77k LOC / 32 packages / 15+ plugins, MIT-licensed |

---

## Straight answers

**Q: "Is this really independent? No dirty laundry?"**
The kernel and the vast majority of capabilities (`kernel/`, `core/`, `acp/`, `tools/`, `ports/`) are built from scratch in Python, aligned to external protocols for interoperability only. There is exactly **one deliberate exception**: the `--tui` terminal skin intentionally keeps **Kimi Code's** signature interaction style and palette (`#4FA8FF` primary, the moon-phase spinner, the two-line status bar) — "if the feel's good, don't reinvent it." That's the *only* part that wears another project's look, and it's credited in [NOTICE](NOTICE). Everything else is our own flesh and blood.

**Q: Why does it block me before I run commands?**
Feature, not bug. Dangerous commands ask first; YOLO still respects hard redlines. If it's too chatty, `qxt safe allow <cmd>` to allowlist — don't disable safety.

**Q: What can `/undo` actually roll back?**
Every **write** that goes through the ledger: a single file, a single step, a whole turn. Under the hood it's the transactional ledger + diff `reverse_transform` + checkpoint snapshots. Not a miracle cure, but it turns "I f'd up" from a guaranteed loss into a probable save.

**Q: Will it read my private files?**
Tool scope is constrained by a domain allow-list, egress is gated to prevent exfiltration, and secrets are redacted from output.

**Q: Fully offline?**
`qxt models local` to probe, `/offline` to manage Ollama. No internet, no problem.

**Q: Can I write my own tools/plugins?**
Yes — `@plugin` metadata, `activate(kernel)` + `kernel.provide(...)` / `kernel.require(...)`. Three steps:

```python
from ..core.kernel import Kernel, Plugin

@plugin("my.tool", provides=["loop_registry"])
class MyTool(Plugin):
    def activate(self, kernel):
        def _handler(ctx, query: str) -> str:
            return f"echo: {query}"
        kernel.provide("tools.my", _handler)
```

---

## Quick start

```bash
# 1) Install (—[dev] is the full stack; drop to [openai]/[mcp] if you only need APIs)
git clone <this repo> && cd qingxiaotuan-agent-cli
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2) Point at a provider (14 built-in profiles: deepseek / groq-free / siliconflow-free / ollama…)
qxt setup
# or by hand: ~/.qingxiaotuan/config.yaml
#   model.provider: deepseek
#   model.model: deepseek-chat
#   model.api_key_env: DEEPSEEK_API_KEY      # keys never in plaintext

# 3) Chat
qxt
# or a smoketest:
qxt --print run "Hi, describe yourself in one sentence."
```

---

## The command surface: 25 subcommands + 36 slash commands

| Command | What it's for |
|---|---|
| `qxt` | Interactive TUI (Kimi Code skin) |
| `qxt setup` / `qxt models` | Configure providers / list 48 models |
| `qxt agent` | Named agents (`.claude/agents`-compatible, 3-tier discovery) |
| `qxt acp` | Run an ACP server so VSCode / Zed / JetBrains can drive you |
| `qxt cron` | Scheduled background tasks |
| `qxt doctor` / `qxt bench` | Health check / benchmark |
| `qxt arch demo` | One-command verify of the 5-layer architecture |

Slash commands you'll live in: `/plan` · `/model` · `/undo`·`/impact` · `/swarm` · `/log`·`/stats`·`/cost`·`/budget`·`/goal`·`/sandbox`·`/offline`·`/verify`·`/audit`·`/more`·`/help` — full list via `Ctrl-G` in the TUI.

---

## Three main loops, one steady hand

- **ReActLoop** — think → act → observe, the default rhythm.
- **PlannerExecuteLoop** — a strong model plans, a cheap one executes (`router.*` supports plan/execute compartmentalization). The saver of tokens.
- **DevLoop** — write-verify-heal: `/verify` auto-detects the project (Python/Node/Rust/Go), infers test commands, and self-heals for up to N rounds.

---

## The safety model: four gates + ledgered undo

1. **Static scoring** — every shell command scored `none→critical` by `safety_engine.score()`, with indirect-expansion unfolding (IFS, `$VAR`, command substitution, ANSI-C/octal/hex escapes, PowerShell Base64, NFKC; ≤32 recursion).
2. **Blast-radius pre-block** — you see what it'll touch *before* it fires (`--impact`).
3. **YOLO-redline floor** — YOLO kills per-step confirmations but **cannot** touch hard redlines (recursive `rm`, force-push, `chmod -R 000 /`… never auto-run).
4. **Transactional ledger** — every write has an audit trail; `/undo` restores via diff `reverse_transform` + snapshots.

Rules always resolve `deny > ask > allow`. Whitelist with `qxt safe allow <cmd>`; never disable safety to save clicks.

---

## Where your ideas go to run

- **Memory** — three tiers (user/project/session) + SQLite FTS5 (trigram), auto-degrades to plaintext if unavailable.
- **Subagents** — typed delegation (general-purpose / explore / plan / coder), isolated subagents, concurrent workers, Swarm for sharding long tasks.
- **Cron & background** — `qxt cron start --detach`; headless autonomy.
- **Hooks** — `PreToolUse` can `block` or rewrite `args` (`hooks.allow_edit_args`) — a big seam for builders.
- **Observability** — `/stats`·`/audit`·`/impact`·`/bench`·`/cost`; cost on the table.
- **crypto** — v2 is solid: AES-GCM(AEAD) with `cryptography`, else HMAC-SHA256 stream cipher; missing-MAC / tampered `open()` always fails closed.

---

## Dev & contract

```bash
python -m pytest tests/ -q        # 2000+ tests
python -m mypy qingxiaotuan       # typing gate
qxt --print run "Hi, one line."   # smoke
```

- **i18n discipline**: README_zh-CN is the authoritative master; every locale is a vivid, zero-drift localization — **no mechanical translation.**
- **Scale**: ~414 `.py` files / ~77k LOC / 32 packages / 15+ plugins.
- **Version**: `v0.2.014` (0.x / Beta); breaking changes get a minor-version heads-up + migration notes.
- **Deep dive**: engine signatures & a step-by-step tool walkthrough live in the appendix of `README_zh-CN.md`.

---

## License & links

- **License**: MIT (use/modify/redistribute freely, keep the notice)
- **Read these**: [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [NOTICE](NOTICE)

> Want "open-box, closed, vendor experience"? Plenty of others do that. Want to **run on your own models, keep the control, and be able to take it back when things go sideways**? The keys are right here.