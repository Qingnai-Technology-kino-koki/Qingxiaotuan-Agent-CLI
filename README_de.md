# Qingxiaotuan Agent CLI (青小团)

> **»Model + Harness = Agent«** — »Denken« und »Sicher laufen« getrennt, und du bekommst beide Schlüssel.
> Ein sicherheitsorientiertes, modellneutrales, reines-Python-Agenten-Harness. `v0.2.014` · MIT · Python ≥ 3.10

**Sprache/Language:** [English](README.md) · [简体中文](README_zh-CN.md) · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [Español](README_es.md) · [Português (Brasil)](README_pt-BR.md) · [Français](README_fr.md) · **Deutsch** · [Русский](README_ru.md)

**Vor dem Handanlegen:** [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · `qxt models list-providers`

---

## In einem Satz

Die meisten Agent-CLIs werden *an einen Anbieter verkauft* und damit oft geboren. Dieses hier ist das Gegenteil: ein **Chassis ohne festgeschriebenes Hirn**. Wechsle zu heiß zwischen 48 Anbietern, laufe komplett offline, mach Fehler rückgängig und sieh den Wirkradius **bevor** der Befehl abschießt. **Das Modell denkt; er sorgt dafür, dass nichts hochgeht.**

**Was es NICHT ist**: kein gefangener Wrapper um ein Modell (Hot-Swap / Self-Host / Offline, ganz wie du willst); kein Handlanger eines IDEs (es ist ein Terminal-Tool, per ACP sogar von VSCode/Zed/JetBrains steuerbar); keine fragile Einzelschicht-Abstraktion (Microkernel-Architektur für Bastler + ein Setup mit einem Klick für alle anderen).

---

## Warum es anders ist

| Was es bringt | Wie weit das geht |
|---|---|
| 🛡️ **Sicherheit zuerst** | Vier Schranken: statisches Risikoscoring, Wirkradius-Blockade, YOLO-Redline-Boden, transaktionales Hauptbuch mit präzisem `/undo` |
| 🔌 **Modellneutral** | 48 Anbieter + lokales Ollama + Hot-Swap + Auto-Routing (`router.*`) |
| 🧠 **Drei Hauptschleifen** | ReAct / Planner-Execute / DevLoop — steckbar: ein Kern, mehrere »Denkrhythmen« |
| 🔧 **Microkernel** | `@plugin` in einer Zeile, Service-Registry, Append-only-Eventbus, Hook-Middleware |
| 🗂️ **Gedächtnis** | SQLite FTS5 + Session-Event-Stream; 3-Ebenen-Speicher, `/undo`, Checkpoint, Replay, Trajectory-Export |
| 🧩 **Ökosystem** | MCP + ACP — Tools einstecken oder dein IDE dich steuern lassen |
| 🌍 **Zehn Sprachen** | standardmäßig vereinfachtes Chinesisch, echte Lokalisierung der Oberfläche |
| 🐍 **Reines Python** | ~414 `.py` / ~77 k Zeilen / 32 Pakete / 15+ Plugins, MIT |

---

## Ohne Umschweife

**F: Ist das wirklich »selbst entwickelt«? Keine Leiche im Keller?**
Der Kern und die allermeisten Fähigkeiten (`kernel/`, `core/`, `acp/`, `tools/`, `ports/`) sind von Grund auf in Python implementiert, nur für Interoperabilität an externe Protokolle angeglichen. Es gibt genau **eine bewusste Ausnahme**: Die Haut des TUI `--tui` übernimmt absichtlich den unverwechselbaren Stil und die Farbpalette von **Kimi Code** (`#4FA8FF`, den Mondphasen-Spinner, die zweizeilige Statusleiste) —»wenn sich's gut anfühlt, erfindet man's nicht neu.« Das ist der *einzige* Teil, der fremdes Aussehen trägt, und er ist in [NOTICE](NOTICE) gutgeschrieben. Alles andere ist eigenes Fleisch und Blut.

**F: Warum blockiert es mich, bevor ich Kommandos ausführe?**
Feature, kein Bug. Gefährliche Kommandos fragen zuerst; YOLO respektiert die Redlines. Zu redselig? `qxt safe allow <cmd>` für die Whitelist — Sicherheit nicht ausschalten.

**F: Was kann `/undo` wirklich zurückrollen?**
Jeden **Schreibzugriff**, der durchs Hauptbuch geht: eine Datei, einen Schritt, eine ganze Runde. Dahinter: transaktionales Hauptbuch + diff `reverse_transform` + Checkpoint-Snapshots. Kein Wundermittel, aber es macht aus »ich hab's versaut« einen wahrscheinlichen Save statt eines sicheren Verlusts.

**F: Liest es meine privaten Dateien?**
Der Tool-Umfang ist per Domain-Allowlist begrenzt, der Netzausgang gegen Exfiltration gesperrt und Schlüssel werden in der Ausgabe maskiert.

**F: Komplett offline?**
`qxt models local` zum Sondieren, `/offline` zum Verwalten von Ollama. Kein Internet, kein Problem.

**F: Kann ich eigene Tools/Plugins schreiben?**
Ja — Metadaten per `@plugin`, `activate(kernel)` + `kernel.provide(...)` / `kernel.require(...)`. Drei Schritte:

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

## Schnellstart

```bash
git clone <dieses Repo> && cd qingxiaotuan-agent-cli
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

qxt setup
# oder manuell: ~/.qingxiaotuan/config.yaml
#   model.provider: deepseek
#   model.model: deepseek-chat
#   model.api_key_env: DEEPSEEK_API_KEY      # Schlüssel nie im Klartext

qxt            # losplaudern
qxt --print run "Hallo, stell dich in einem Satz vor."
```

---

## Kommandofläche: 25 Subcommands + 36 Slash-Commands

| Befehl | Wofür |
|---|---|
| `qxt` | Interaktives TUI (Kimi-Code-Skin) |
| `qxt setup` / `qxt models` | Anbieter konfigurieren / 48 Modelle listen |
| `qxt agent` | Benannte Agents (`.claude/agents`-kompatibel, 3-Ebenen-Discovery) |
| `qxt acp` | ACP-Server starten, damit VSCode / Zed / JetBrains dich steuern |
| `qxt cron` | Geplante Hintergrundaufgaben |
| `qxt doctor` / `qxt bench` | Gesundheitscheck / Benchmark |
| `qxt arch demo` | 5-Ebenen-Architektur mit einem Befehl verifizieren |

Slash-Commands im Alltag: `/plan`·`/model`·`/undo`·`/impact`·`/swarm`·`/log`·`/stats`·`/cost`·`/budget`·`/goal`·`/sandbox`·`/offline`·`/verify`·`/audit`·`/more`·`/help` — volle Liste via `Ctrl-G` im TUI.

---

## Drei Schleifen, eine ruhige Hand

- **ReActLoop** — denken → handeln → beobachten, der Default-Rhythmus.
- **PlannerExecuteLoop** — ein starkes Modell plant, ein billiges führt aus (`router.*` erlaubt Plan/Ausführung zu trennen). Der Tokensparer.
- **DevLoop** — schreib-verifiziere-heile: `/verify` erkennt das Projekt (Python/Node/Rust/Go), leitet Testbefehle ab und heilt sich bis zu N Runden.

## Das Sicherheitsmodell: vier Schranken + abbrechen mit Buchführung

1. **Statisches Scoring** — jeder Shell-Befehl von `safety_engine.score()` mit `none→critical` bewertet, mit Entfaltung von Indirektionen (IFS, `$VAR`, Befehlsersetzung, ANSI-C/Oktal/Hex-Escapes, PowerShell Base64, NFKC; ≤32 Rekursion).
2. **Wirkradius-Blockade** — du siehst, was es anfasst, **bevor** es abgeht (`--impact`).
3. **YOLO-Redline-Boden** — YOLO nimmt die Schritt-Bestätigungen, aber **nicht** die harten Redlines (`rm` rekursiv, force-push, `chmod -R 000 /`… nie automatisch).
4. **Transaktionales Hauptbuch** — jeder Write ist geprüft; `/undo` stellt via diff `reverse_transform` + Snapshots wieder her.

Regeln lösen immer `deny > ask > allow`. Whitelist mit `qxt safe allow <cmd>`; schalte Sicherheit nicht ab, um Klicks zu sparen.

---

## Wo deine Ideen laufen

- **Gedächtnis** — drei Ebenen (User/Projekt/Session) + SQLite FTS5 (Trigram), fällt notfalls auf Klartext zurück.
- **Subagents** — typisierte Delegation (general-purpose / explore / plan / coder), isolierte Subagents, parallele Worker und Swarm zum Zerlegen langer Aufgaben.
- **Cron & Hintergrund** — `qxt cron start --detach`; headless autonome Läufe.
- **Hooks** — `PreToolUse` kann `block` oder `args` umschreiben (`hooks.allow_edit_args`).
- **Observability** — `/stats`·`/audit`·`/impact`·`/bench`·`/cost`; die Kosten auf den Tisch.
- **crypto** — v2 solide: AES-GCM(AEAD) mit `cryptography`, sonst HMAC-SHA256-Stream-Cipher; `open()` ohne MAC / manipuliert ist immer fail-closed.

---

## Dev & Vertrag

```bash
python -m pytest tests/ -q        # 2000+ Tests
python -m mypy qingxiaotuan       # Typ-Gate
qxt --print run "Hallo. Eine Zeile." # Smoke
```

- **i18n-Disziplin**: README_zh-CN ist die maßgebliche Master-Version; jede Sprache ist eine lebendige, driftfreie Lokalisierung — **keine maschinelle Übersetzung.**
- **Umfang**: ~414 `.py` / ~77 k Zeilen / 32 Pakete / 15+ Plugins.
- **Version**: `v0.2.014` (0.x/Beta); breaking Changes kündigen sich in der Minor an + Migrationshinweise.
- **Tiefgang**: Signaturen der neun Engines und ein Tool-Tutorial stehen im Anhang von `README_zh-CN.md`.

---

## Lizenz & Links

- **Lizenz**: MIT (nutzen, ändern, weitergeben; Hinweis behalten)
- **Lies das**: [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [NOTICE](NOTICE)

> Du willst »Haecke auf, closed, an einen Anbieter gebunden«? Davon gibt's genug. Du willst **auf deinen eigenen Modellen laufen, die Kontrolle behalten und im Notfall zurückrollen**? Hier liegen die Schlüssel.