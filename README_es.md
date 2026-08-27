# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

[English](README.md) | [简体中文](README_zh-CN.md) | [繁體中文](README_zh-TW.md) | [日本語](README_ja.md) | [한국어](README_ko.md) | **Español** | [Português (BR)](README_pt-BR.md) | [Français](README_fr.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> Un CLI de agentes construido alrededor de una sola idea: **conoce tu radio de impacto antes de que el agente toque nada.**
> Cada comando de shell se analiza y se clasifica por riesgo *antes* de ejecutarse: las operaciones críticas se bloquean por defecto, y una mascota viva te muestra exactamente en qué estado se encuentra el agente.

Python puro, sin compilación y neutral respecto al modelo. 119 módulos y ~21k líneas de código fuente respaldadas por **591 pruebas offline** (incluidas pruebas end-to-end contra un mock server) sobre una matriz de CI con Python 3.11–3.13.

<!-- 📹 TODO(demo): insertar aquí una grabación de terminal de 30–60 s que muestre:
     qxt chat → tarea → transiciones de la mascota → momento de interceptación de seguridad.
     Hasta entonces, la máquina de estados ASCII de abajo es lo auténtico. -->

## La mascota es la barra de estado

Qingxiaotuan («pequeña bolita de masa verde») no es un logotipo estático: es una **máquina de estados con signos vitales**, dibujada en vivo en tu terminal para que siempre sepas qué está haciendo el agente:

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| Estado | Cuándo | Visual |
| --- | --- | --- |
| `idle` | esperando entrada | respiración tranquila |
| `thinking` | el modelo razonando | se balancea + spinner |
| `working` | ejecutando una llamada a herramientas | flota + anillo de progreso |
| `alert` | **la barrera de seguridad bloqueó** un comando peligroso | se pone naranja, tiembla — control del radio de impacto que puedes *ver* |
| `done` | tarea terminada | ojos de media luna + destello |

## ¿Por qué este CLI?

- **Radio de impacto mínimo por defecto** — antes de ejecutar cada comando de shell, el motor `safety`, escrito en Python puro, realiza un análisis estático de riesgo; los comandos `critical` (`rm -rf /`, `git push --force`, `DROP TABLE`) se **bloquean antes de ejecutarse**, no solo se registran después.
- **Cambios reversibles en el espacio de trabajo** — `/diff` para revisar cambios, `/undo` para revertirlos (incluido el modo `--safe`), y sesiones reproducibles mediante un flujo de eventos append-only.
- **10 motores de capacidades, todos en Python puro, sin IPC** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market, conectados mediante un registro con verificaciones de estado (`qxt ext selftest`).
- **Neutral respecto al modelo** — DeepSeek, Claude, Gemini o modelos locales detrás de adaptadores compatibles con OpenAI / Anthropic; cambia de modelo cuando quieras con `/model`. `/route` estima la dificultad de la tarea y sugiere un modelo (solo orientativo).
- **Enjambre multiagente** — `/swarm` planifica con un modelo potente, ejecuta subtareas en paralelo con modelos más económicos y luego deja que el modelo potente acepte o rechace el resultado.
- **Ciclo de auto-mejora** — tras cada ejecución, el reflector diagnostica los fallos (reconoce pytest/npm/git/cargo/dotnet) y destila salvaguardas, de modo que el mismo error se detecte antes la próxima vez.
- **Memoria que sobrevive a los reinicios** — tres capas (session / facts / skills) con recuperación de texto completo entre sesiones vía SQLite FTS5.

## Inicio rápido

> Requiere Python ≥ 3.11. Python puro: no hay nada que compilar.

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

Las tareas headless de una sola ejecución también funcionan:

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **Nota sobre `crypto`:** el motor `crypto` usa únicamente la biblioteca estándar (derivación de claves PBKDF2-HMAC-SHA256 + cifrado en flujo con keystream SHA256 + huellas SHA-256). El cifrado en flujo es un diseño ligero sin etiquetas de autenticación: sirve para uso local a prueba de alteraciones, pero no es una primitiva de alta seguridad.

## Comandos slash

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

Lo más destacado:

| Comando | Qué hace |
| --- | --- |
| `/plan` | modo de análisis de solo lectura — las herramientas de mutación son interceptadas |
| `/swarm` | colaboración multiagente (plan potente → ejecución económica en paralelo → aceptación potente) |
| `/route` | estimación de dificultad + sugerencia de modelo (orientativo, sin cambio automático) |
| `/compact` | comprime el historial antiguo para recuperar presupuesto de contexto |
| `/cost` | uso de tokens, tasa de aciertos de caché y gasto estimado |
| `/undo` | reversión rápida del espacio de trabajo (`all` / `--safe` / por archivo) |

## Motores de capacidades

| Motor | Qué aporta |
| --- | --- |
| `diff` | diff a nivel de línea/palabra + patch + fusión de 3 vías |
| `crypto` | derivación PBKDF2-HMAC-SHA256 + cifrado en flujo con keystream SHA256 + huellas |
| `index` | índice incremental de símbolos FNV-1a |
| `ansi` | análisis / eliminación / renderizado de secuencias de escape de terminal |
| `safety` | salvaguarda de radio de impacto mínimo: puntuación de riesgo + radio de impacto + bloqueo |
| `json` | punteros RFC 6901 / diff por ruta / fusión profunda (deep merge) |
| `search` | búsqueda recursiva con expresiones regulares (ignora node_modules/.git) |
| `notify` | notificaciones de escritorio multiplataforma |
| `rules` | validación de políticas YAML (expresiones seguras sin eval) |
| `skill-market` | registro de paquetes de skills: pull / publish / search |

```bash
qxt ext engines     # list engines actually available
qxt ext selftest    # launch each engine, report health
```

## Arquitectura

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

## Calidad

- **591 pruebas offline** — sin necesidad de red, incluidas ejecuciones end-to-end contra un mock server local compatible con OpenAI (bucle de herramientas del agente, streaming, worker en segundo plano y un subproceso real de `qxt`).
- **Matriz de CI**: Python 3.11 / 3.12 / 3.13.
- Solo cinco dependencias en tiempo de ejecución: `openai`, `pyyaml`, `rich`, `prompt_toolkit`, `httpx`.

## Hoja de ruta

- [ ] Enrutamiento automático de modelos (conectar `router.enabled` al bucle del agente; hoy `/route` es solo orientativo)
- [ ] Publicar en PyPI (`pip install qingxiaotuan`)
- [ ] Registro público del skill market

## Contribuir

Issues y PRs son bienvenidos — consulta [CONTRIBUTING.md](./CONTRIBUTING.md). Las traducciones de este README se coordinan dentro del propio archivo; corrige cualquier idioma mediante un PR.

## Licencia y créditos

MIT — consulta [LICENSE](./LICENSE).

Construido a hombros del open source; ideas absorbidas y reimplementadas desde cero en Python:

- **DeepSeek Harness (dsh)** — microkernel (estilo Cordis), todo-es-un-plugin, configuración basada en perfiles, adaptación neutral al modelo, tareas headless, flujo de eventos de sesión append-only
- **Hermes Agent** — memoria de tres capas, ciclo de autoevolución de skills, identidad SOUL.md, recall entre sesiones con SQLite FTS5, trabajos cron
- **Claude Code** — interacciones CLI fluidas, visualización en vivo del razonamiento y del estado de las herramientas, gestión de contextos grandes, comandos slash interactivos
