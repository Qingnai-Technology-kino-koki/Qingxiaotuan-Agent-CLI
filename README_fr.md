# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

**Français** | [English](README.md) | [简体中文](README_zh-CN.md) | [繁體中文](README_zh-TW.md) | [日本語](README_ja.md) | [한국어](README_ko.md) | [Español](README_es.md) | [Português (BR)](README_pt-BR.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> Un agent CLI bâti autour d'une seule idée : **connaître votre rayon d'impact avant que l'agent ne touche à quoi que ce soit.**
> Chaque commande shell fait l'objet d'une analyse et d'une notation de risque *avant* son exécution — les opérations critiques sont bloquées par défaut, et une mascotte vivante vous montre exactement dans quel état se trouve l'agent.

Pur Python, zéro compilation, neutre vis-à-vis des modèles. 119 modules, ~21k lignes de code source épaulées par **591 tests hors ligne** (parcours end-to-end sur serveur mock inclus), sur une matrice CI Python 3.11–3.13.

<!-- 📹 TODO(demo): insérer ici un enregistrement de terminal de 30 à 60 s montrant :
     qxt chat → tâche → transitions de la mascotte → instant d'interception par le garde-fou.
     D'ici là, la machine à états ASCII ci-dessous est la vraie chose. -->

## La mascotte est la barre d'état

Qingxiaotuan (« petite boulette verte ») n'est pas un logo statique — c'est une **machine à états dotée de signes vitaux**, rendue en direct dans votre terminal afin que vous sachiez toujours ce que fait l'agent :

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| État | Quand | Visuel |
| --- | --- | --- |
| `idle` | en attente d'une saisie | respiration calme |
| `thinking` | le modèle raisonne | balancement + spinner |
| `working` | exécution d'un appel d'outil | lévitation + anneau de progression |
| `alert` | **le garde-fou de sécurité a bloqué** une commande dangereuse | devient orange et tremble — un contrôle du rayon d'impact que vous pouvez *voir* |
| `done` | tâche terminée | yeux en croissant + étincelle |

## Pourquoi celui-ci ?

- **Rayon d'impact minimal par défaut** — avant chaque commande shell, le moteur `safety` en pur Python réalise une analyse statique des risques ; les commandes `critical` (`rm -rf /`, `git push --force`, `DROP TABLE`) sont **bloquées avant exécution**, et pas simplement consignées après coup.
- **Opérations d'espace de travail réversibles** — `/diff` pour examiner les modifications, `/undo` pour revenir en arrière (mode `--safe` inclus), sessions rejouables grâce au flux d'événements append-only.
- **10 moteurs de capacités, tous en pur Python, zéro IPC** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market, câblés via un registre avec contrôles de santé (`qxt ext selftest`).
- **Neutre vis-à-vis des modèles** — DeepSeek, Claude, Gemini ou modèles locaux, derrière des adaptateurs compatibles OpenAI / Anthropic ; basculez à tout moment avec `/model`. `/route` estime la difficulté de la tâche et suggère un modèle (à titre indicatif).
- **Essaim multi-agents** — `/swarm` planifie avec un modèle puissant, exécute les sous-tâches en parallèle avec des modèles moins chers, puis fait accepter ou rejeter le résultat par le modèle puissant.
- **Boucle d'auto-amélioration** — après chaque exécution, le réflecteur diagnostique les échecs (en prenant en compte pytest/npm/git/cargo/dotnet) et distille des garde-fous, afin que la même erreur soit interceptée plus tôt la prochaine fois.
- **Une mémoire qui survit aux redémarrages** — trois couches (session / faits / compétences) avec rappel en texte intégral SQLite FTS5 à travers les sessions.

## Démarrage rapide

> Nécessite Python ≥ 3.11. Pur Python — rien à compiler.

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

Les tâches headless ponctuelles fonctionnent aussi :

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **Note crypto :** le moteur `crypto` s'appuie exclusivement sur la bibliothèque standard (dérivation de clé PBKDF2-HMAC-SHA256 + chiffrement de flux à keystream SHA256 + empreintes SHA-256). Le chiffrement de flux est une conception légère, sans tags d'authentification — adapté à un usage local avec détection des altérations, mais pas une primitive de haute sécurité.

## Commandes slash

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

À retenir :

| Commande | Effet |
| --- | --- |
| `/plan` | mode d'analyse en lecture seule — les outils de modification sont interceptés |
| `/swarm` | collaboration multi-agents (planification par le modèle puissant → exécution parallèle économique → acceptation par le modèle puissant) |
| `/route` | estimation de la difficulté + suggestion de modèle (indicatif, aucune bascule automatique) |
| `/compact` | compacte l'historique ancien pour récupérer du budget de contexte |
| `/cost` | consommation de tokens, taux de réussite du cache, dépense estimée |
| `/undo` | retour arrière rapide de l'espace de travail (`all` / `--safe` / fichier par fichier) |

## Moteurs de capacités

| Moteur | Ce qu'il fournit |
| --- | --- |
| `diff` | diff au niveau ligne/mot + patch + fusion à trois voies |
| `crypto` | dérivation PBKDF2-HMAC-SHA256 + chiffrement de flux à keystream SHA256 + empreintes |
| `index` | index incrémental de symboles FNV-1a |
| `ansi` | analyse / retrait / rendu des séquences d'échappement de terminal |
| `safety` | garde-fou « rayon d'impact minimal » : notation du risque + rayon d'impact + blocage |
| `json` | pointeurs RFC 6901 / diff par chemin / fusion profonde |
| `search` | recherche par regex récursive (ignore node_modules/.git) |
| `notify` | notifications de bureau multiplateformes |
| `rules` | validation de politiques YAML (expressions sûres, sans eval) |
| `skill-market` | registre de paquets de compétences : pull / publish / search |

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
├── tools/        base / shell / filesystem / web / external / code / audit
├── context/      indexer / manager
├── cron/         scheduled jobs
├── ui/           repl + fullscreen TUI (shared theme)
└── resources/    bundled skill templates + SOUL.md identity
```

## Qualité

- **591 tests hors ligne** — aucun réseau requis, y compris des exécutions de bout en bout contre un serveur mock compatible OpenAI local (boucle d'outils de l'agent, streaming, worker en arrière-plan, véritable sous-processus `qxt`).
- **Matrice CI** : Python 3.11 / 3.12 / 3.13.
- Seulement cinq dépendances à l'exécution : `openai`, `pyyaml`, `rich`, `prompt_toolkit`, `httpx`.

## Feuille de route

- [ ] Routage automatique des modèles (brancher `router.enabled` dans la boucle de l'agent ; aujourd'hui `/route` reste indicatif)
- [ ] Publication sur PyPI (`pip install qingxiaotuan`)
- [ ] Registre public pour le skill-market

## Contribuer

Issues et PRs bienvenues — voir [CONTRIBUTING.md](./CONTRIBUTING.md). Les traductions de ce README sont coordonnées au sein même du fichier ; corrigez n'importe quelle langue via une PR.

## Licence & crédits

MIT — voir [LICENSE](./LICENSE).

Ce projet se tient sur les épaules de l'open source ; idées absorbées puis réimplémentées de zéro en Python :

- **DeepSeek Harness (dsh)** — micro-noyau (à la Cordis), tout-est-plugin, configuration par profils, adaptation neutre vis-à-vis des modèles, tâches headless, flux d'événements de session append-only
- **Hermes Agent** — mémoire à trois couches, boucle d'auto-évolution des compétences, identité SOUL.md, rappel inter-sessions SQLite FTS5, tâches cron
- **Claude Code** — interactions CLI fluides, affichage en direct de la réflexion et des outils, gestion des grands contextes, commandes slash interactives
