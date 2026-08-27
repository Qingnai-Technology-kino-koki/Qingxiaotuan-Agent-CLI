# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

[English](README.md) | [简体中文](README_zh-CN.md) | [繁體中文](README_zh-TW.md) | [日本語](README_ja.md) | [한국어](README_ko.md) | [Español](README_es.md) | **Português (BR)** | [Français](README_fr.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> Um CLI de agente construído em torno de uma única ideia: **conheça o raio de impacto antes que o agente toque em qualquer coisa.**
> Todo comando de shell é analisado quanto a risco e classificado *antes* de ser executado — operações críticas são bloqueadas por padrão, e um mascote vivo mostra exatamente o estado em que o agente está.

Python puro, zero compilação, neutro em relação a modelos. São 119 módulos e ~21 mil linhas de código-fonte respaldados por **591 testes offline** (incluindo testes ponta a ponta com mock server) em uma matriz de CI Python 3.11–3.13.

<!-- 📹 TODO(demo): inserir aqui uma gravação de terminal de 30 a 60 segundos mostrando:
     qxt chat → task → transições do mascote → momento de interceptação pelo safety.
     Até lá, a máquina de estados ASCII abaixo é a coisa real. -->

## O Mascote É a Barra de Status

Qingxiaotuan ("pequeno bolinho verde") não é um logotipo estático — é uma **máquina de estados com sinais vitais**, renderizada ao vivo no seu terminal para que você sempre saiba o que o agente está fazendo:

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| Estado | Quando | Visual |
| --- | --- | --- |
| `idle` | aguardando entrada | respiração calma |
| `thinking` | raciocínio do modelo | balança + spinner |
| `working` | executa uma chamada de ferramenta | flutua + anel de progresso |
| `alert` | **guardrail do safety bloqueou** um comando perigoso | fica laranja e treme — controle de raio de impacto que você pode *ver* |
| `done` | tarefa concluída | olhos em crescente + brilho |

## Por Que Este Projeto

- **Raio de impacto mínimo por padrão** — antes de cada comando de shell ser executado, o motor `safety`, em Python puro, faz análise estática de risco; comandos `critical` (`rm -rf /`, `git push --force`, `DROP TABLE`) são **bloqueados antes da execução**, não apenas registrados depois.
- **Movimentações de workspace reversíveis** — `/diff` para revisar alterações, `/undo` para reverter (com modo `--safe` incluído), sessões reproduzíveis graças a um fluxo de eventos append-only.
- **10 motores de capacidade, todos em Python puro, zero IPC** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market, conectados por meio de um registro com verificações de integridade (`qxt ext selftest`).
- **Neutro em relação a modelos** — DeepSeek, Claude, Gemini ou modelos locais por trás de adaptadores compatíveis com OpenAI / Anthropic; troque a qualquer momento com `/model`. `/route` estima a dificuldade da tarefa e sugere um modelo (apenas consultivo).
- **Enxame multiagente** — `/swarm` planeja com um modelo forte, executa subtarefas com modelos mais baratos em paralelo e, no fim, faz o modelo forte aceitar ou rejeitar o resultado.
- **Ciclo de autoaprimoramento** — após cada execução, o refletidor diagnostica falhas (reconhece pytest/npm/git/cargo/dotnet) e destila guardrails, para que o mesmo erro seja detectado mais cedo da próxima vez.
- **Memória que sobrevive a reinicializações** — três camadas (session / facts / skills) com recuperação full-text entre sessões via SQLite FTS5.

## Início Rápido

> Requer Python ≥ 3.11. Python puro — nada a compilar.

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

Tarefas headless de execução única também funcionam:

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **Nota sobre o crypto:** o motor `crypto` usa apenas a biblioteca padrão (derivação de chave PBKDF2-HMAC-SHA256 + cifra de fluxo SHA256-keystream + fingerprints SHA-256). A cifra de fluxo é um design leve, sem tags de autenticação — adequada para uso local à prova de violação, mas não é um primitivo de alta segurança.

## Comandos Slash

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

Destaques:

| Comando | O que faz |
| --- | --- |
| `/plan` | modo de análise somente leitura — ferramentas de mutação são interceptadas |
| `/swarm` | colaboração multiagente (plano forte → execução barata em paralelo → aceite forte) |
| `/route` | estimativa de dificuldade + sugestão de modelo (consultivo, sem troca automática) |
| `/compact` | comprime o histórico antigo para recuperar o orçamento de contexto |
| `/cost` | uso de tokens, taxa de acerto do cache, gasto estimado |
| `/undo` | reversão rápida do workspace (`all` / `--safe` / por arquivo) |

## Motores de Capacidade

| Motor | O que oferece |
| --- | --- |
| `diff` | diff por linha/palavra + patch + mesclagem de 3 vias |
| `crypto` | derivação PBKDF2-HMAC-SHA256 + cifra de fluxo SHA256-keystream + fingerprints |
| `index` | índice incremental de símbolos FNV-1a |
| `ansi` | análise / remoção / renderização de sequências de escape de terminal |
| `safety` | guardrail de raio de impacto mínimo: pontuação de risco + raio de impacto + bloqueio |
| `json` | RFC 6901 pointer / diff por caminho / deep merge |
| `search` | busca recursiva por regex (ignora node_modules/.git) |
| `notify` | notificações de desktop multiplataforma |
| `rules` | validação de políticas YAML (expressões seguras sem eval) |
| `skill-market` | registro de pacotes de skills: pull / publish / search |

```bash
qxt ext engines     # list engines actually available
qxt ext selftest    # launch each engine, report health
```

## Arquitetura

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

## Qualidade

- **591 testes offline** — sem necessidade de rede, incluindo execuções ponta a ponta contra um mock server local compatível com OpenAI (loop de ferramentas do agente, streaming, worker em segundo plano, subprocesso `qxt` real).
- **Matriz de CI**: Python 3.11 / 3.12 / 3.13.
- Apenas cinco dependências de runtime: `openai`, `pyyaml`, `rich`, `prompt_toolkit`, `httpx`.

## Roadmap

- [ ] Roteamento automático de modelos (integrar `router.enabled` ao loop do agente; hoje `/route` é apenas consultivo)
- [ ] Publicar no PyPI (`pip install qingxiaotuan`)
- [ ] Registro público do skill-market

## Contribuindo

Issues e PRs são bem-vindos — veja [CONTRIBUTING.md](./CONTRIBUTING.md). As traduções deste README são coordenadas no próprio arquivo; corrija qualquer idioma via PR.

## Licença e Créditos

MIT — veja [LICENSE](./LICENSE).

Apoiado sobre os ombros do open source; ideias absorvidas e reimplementadas do zero em Python:

- **DeepSeek Harness (dsh)** — microkernel (estilo Cordis), tudo é plugin, configuração baseada em perfis, adaptação neutra a modelos, tarefas headless, fluxo de eventos de sessão append-only
- **Hermes Agent** — memória em três camadas, ciclo de autoevolução de skills, identidade SOUL.md, recuperação entre sessões com SQLite FTS5, tarefas cron
- **Claude Code** — interações fluidas de CLI, exibição ao vivo do status de pensamento/ferramentas, gerenciamento de contexto grande, slash commands interativos
