# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

[English](README.md) | [简体中文](README_zh-CN.md) | [繁體中文](README_zh-TW.md) | [日本語](README_ja.md) | [한국어](README_ko.md) | [Español](README_es.md) | [Português (BR)](README_pt-BR.md) | [Français](README_fr.md) | **Deutsch** | [Русский](README_ru.md)

> Ein Agent-CLI, gebaut um eine einzige Idee: **Kenne deinen Wirkungsradius (Blast Radius), bevor der Agent irgendetwas anfasst.**
> Jeder Shell-Befehl wird analysiert und eingestuft, *bevor* er ausgeführt wird — kritische Operationen werden standardmäßig blockiert, und ein lebendiges Maskottchen zeigt dir jederzeit genau, in welchem Zustand sich der Agent befindet.

Reines Python, keine Kompilierung, modellneutral. 119 Module, ~21k Zeilen Quellcode, abgesichert durch **591 Offline-Tests** (End-to-End gegen einen Mock-Server inklusive) auf einer CI-Matrix für Python 3.11–3.13.

<!-- 📹 TODO(demo): hier eine 30–60 Sekunden lange Terminal-Aufnahme einfügen, die zeigt:
     qxt chat → Aufgabe → Maskottchen-Zustandswechsel → Moment des Sicherheits-Eingriffs.
     Bis dahin ist die untenstehende ASCII-Zustandsmaschine das Original. -->

## Das Maskottchen ist die Statusleiste

Qingxiaotuan („kleiner grüner Dumpling“) ist kein statisches Logo — es ist eine **Zustandsmaschine mit Vitalzeichen**, live in deinem Terminal gerendert, damit du immer weißt, was der Agent gerade tut:

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| Zustand | Wann | Darstellung |
| --- | --- | --- |
| `idle` | wartet auf Eingabe | ruhiges Atmen |
| `thinking` | das Modell denkt | Schwingen + Spinner |
| `working` | führt einen Tool-Aufruf aus | Schweben + Fortschrittsring |
| `alert` | **Safety-Guardrail hat einen gefährlichen Befehl blockiert** | färbt sich orange und zittert — Kontrolle des Wirkungsradius, die man *sehen* kann |
| `done` | Aufgabe abgeschlossen | Halbmond-Augen + Funkeln |

## Warum dieses Projekt

- **Standardmäßig minimaler Wirkungsradius** — bevor jeder Shell-Befehl ausgeführt wird, führt die Pure-Python-Engine `safety` eine statische Risikoanalyse durch; `critical`-Befehle (`rm -rf /`, `git push --force`, `DROP TABLE`) werden **vor der Ausführung blockiert**, statt sie lediglich nachträglich zu protokollieren.
- **Umkehrbare Workspace-Änderungen** — mit `/diff` Änderungen prüfen, mit `/undo` zurückrollen (`--safe`-Modus inklusive); Sessions lassen sich über einen Append-only-Event-Stream wiedergeben.
- **10 Capability-Engines, alle in reinem Python, null IPC** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market, angebunden über eine Registry mit Health Checks (`qxt ext selftest`).
- **Modellneutral** — DeepSeek, Claude, Gemini oder lokale Modelle hinter OpenAI-kompatiblen bzw. Anthropic-Adaptern; jederzeit wechselbar mit `/model`. `/route` schätzt die Schwierigkeit einer Aufgabe und schlägt ein Modell vor (rein beratend).
- **Multi-Agenten-Schwarm** — `/swarm` plant mit einem starken Modell, führt Teilaufgaben parallel mit günstigeren Modellen aus und lässt anschließend das starke Modell das Ergebnis annehmen oder verwerfen.
- **Selbstverbesserungs-Loop** — nach jedem Lauf diagnostiziert der Reflector Fehlschläge (erkennt pytest-, npm-, git-, cargo- und dotnet-Fehler) und destilliert daraus Guardrails, sodass derselbe Fehler beim nächsten Mal früher erkannt wird.
- **Gedächtnis, das Neustarts überlebt** — drei Ebenen (session / facts / skills) mit SQLite-FTS5-Volltextabruf über Sessions hinweg.

## Schnellstart

> Erfordert Python ≥ 3.11. Reines Python — nichts zu kompilieren.

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

Headless-One-Shot-Aufgaben funktionieren ebenfalls:

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **Hinweis zu Crypto:** Die Engine `crypto` nutzt ausschließlich die Standardbibliothek (PBKDF2-HMAC-SHA256-Schlüsselableitung + SHA256-Keystream-Stromchiffre + SHA-256-Fingerabdrücke). Die Stromchiffre ist ein leichtgewichtiger Entwurf ohne Authentifizierungs-Tags — gut geeignet für manipulationserkennende lokale Nutzung, kein kryptografisches Primitiv für Hochsicherheitsanforderungen.

## Slash-Befehle

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

Highlights:

| Befehl | Was er tut |
| --- | --- |
| `/plan` | Nur-Lese-Analysemodus — Mutationstools werden abgefangen |
| `/swarm` | Multi-Agenten-Zusammenarbeit (starker Plan → parallele günstige Ausführung → starke Abnahme) |
| `/route` | Schwierigkeitseinschätzung + Modellvorschlag (rein beratend, kein automatischer Wechsel) |
| `/compact` | faltet alte Historie zusammen, um Kontextbudget zurückzugewinnen |
| `/cost` | Tokenverbrauch, Cache-Hit-Rate, geschätzte Kosten |
| `/undo` | schnelles Workspace-Rollback (`all` / `--safe` / pro Datei) |

## Capability-Engines

| Engine | Was sie bietet |
| --- | --- |
| `diff` | Diff auf Zeilen-/Wortebene + Patch + 3-Wege-Merge |
| `crypto` | PBKDF2-HMAC-SHA256-Schlüsselableitung + SHA256-Keystream-Stromchiffre + Fingerabdrücke |
| `index` | inkrementeller Symbolindex mit FNV-1a |
| `ansi` | Terminal-Escape-Sequenzen parsen / entfernen / rendern |
| `safety` | Guardrail für minimalen Wirkungsradius: Risikobewertung + Wirkungsradius + Blockierung |
| `json` | RFC-6901-Pointer / Diff pro Pfad / Deep Merge |
| `search` | rekursive Regex-Suche (ignoriert node_modules/.git) |
| `notify` | plattformübergreifende Desktop-Benachrichtigungen |
| `rules` | Validierung von YAML-Richtlinien (sichere Ausdrücke ohne eval) |
| `skill-market` | Skill-Paket-Registry: pull / publish / search |

```bash
qxt ext engines     # list engines actually available
qxt ext selftest    # launch each engine, report health
```

## Architektur

```
qingxiaotuan/
├── cli/          command layer (+ fullscreen TUI)
├── core/         kernel & orchestration: kernel / agent / devloop / background / swarm
├── config/       defaults / loader / plugin / validate
├── ext/          10 pure-Python engines + registry
├── models/       adapters: openai_compat / anthropic / provider_catalog / router
├── memory/       store (SQLite FTS5) / sessions (append-only events)
├── skills/       manager / plugin
├── tools/        base / shell / filesystem / web / external / code / audit
├── context/      indexer / manager
├── cron/         scheduled jobs
├── ui/           repl + fullscreen TUI (shared theme)
└── resources/    bundled skill templates + SOUL.md identity
```

## Qualität

- **591 Offline-Tests** — kein Netzwerk erforderlich, darunter End-to-End-Läufe gegen einen lokalen OpenAI-kompatiblen Mock-Server (Agent-Tool-Loop, Streaming, Background-Worker, echter `qxt`-Subprozess).
- **CI-Matrix**: Python 3.11 / 3.12 / 3.13.
- Nur fünf Laufzeitabhängigkeiten: `openai`, `pyyaml`, `rich`, `prompt_toolkit`, `httpx`.

## Roadmap

- [ ] Automatisches Modell-Routing (`router.enabled` in den Agent-Loop einbinden; derzeit ist `/route` nur beratend)
- [ ] Veröffentlichung auf PyPI (`pip install qingxiaotuan`)
- [ ] Öffentliche Registry für den Skill-Market

## Mitwirken

Issues und PRs sind willkommen — siehe [CONTRIBUTING.md](./CONTRIBUTING.md). Die Übersetzungen dieses READMEs werden direkt in den Dateien koordiniert; Korrekturen für jede Sprache sind jederzeit per PR willkommen.

## Lizenz & Danksagungen

MIT — siehe [LICENSE](./LICENSE).

Wir stehen auf den Schultern von Open Source; die folgenden Ideen haben wir aufgegriffen und in Python von Grund auf neu implementiert:

- **DeepSeek Harness (dsh)** — Mikrokernel im Cordis-Stil, Alles-ist-ein-Plugin, profibasierte Konfiguration, modellneutrale Anpassung, Headless-Aufgaben, Append-only-Session-Event-Stream
- **Hermes Agent** — dreischichtiges Gedächtnis, Self-Evolution-Loop für Skills, SOUL.md-Identität, SQLite-FTS5-Recall über Sessions hinweg, Cron-Jobs
- **Claude Code** — flüssige CLI-Interaktionen, Live-Anzeige von Thinking-/Tool-Status, Verwaltung großer Kontexte, interaktive Slash-Befehle
