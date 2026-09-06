# Qingxiaotuan Agent CLI (青小团)

> **« Model + Harness = Agent »** — on sépare « penser » de « tourner en sécurité », et on te remet les deux clés.
> Un harness d'agents IA en pur Python, sécurité d'abord, agnostique du modèle. `v0.2.014` · MIT · Python ≥ 3.10

**Langue/Language:** [English](README.md) · [简体中文](README_zh-CN.md) · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [Español](README_es.md) · [Português (Brasil)](README_pt-BR.md) · **Français** · [Deutsch](README_de.md) · [Русский](README_ru.md)

**À lire avant de plonger :** [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · `qxt models list-providers`

---

## En une phrase

La plupart des CLIs d'agents naissent *mariés à un fournisseur*. Ici, c'est l'inverse : un **châssis sans cerveau imposé**. Change de fournisseur à chaud parmi 48, tourne 100 % hors ligne, défais tes bêtises et vois le rayon d'impact **avant** que la commande ne parte. **Le modèle pense ; lui, il fait en sorte que rien n'explose.**

**Ce que ce n'est pas** : ni un emballage captif d'un modèle (hot-swap / auto-hébergement / offline, tu choisis) ; ni un sous-fifre d'IDE (c'est un outil de terminal, en plus pilotable via ACP par VSCode/Zed/JetBrains) ; ni une abstraction fragile sur une seule couche (architecture microkernel pour ceux qui bricolent + un `setup` d'un clic pour les autres).

---

## Pourquoi c'est différent

| Ce qu'il donne | Jusqu'où ça va |
|---|---|
| 🛡️ **La sécurité d'abord** | Quatre barrières : score de risque statique, blocage par rayon d'impact, plancher de lignes rouges en YOLO, et livre de comptes transactionnel avec `/undo` de précision |
| 🔌 **Agnostique modèle** | 48 fournisseurs + Ollama local + hot-swap + routage auto (`router.*`) |
| 🧠 **Trois boucles principales** | ReAct / Planner-Execute / DevLoop — enfichables : un noyau, plusieurs « rythmes de pensée » |
| 🔧 **Microkernel** | Un `@plugin` d'une ligne, registre de services, bus d'événements append-only, middleware de hooks |
| 🗂️ **Mémoire** | SQLite FTS5 + flux d'événements de session ; mémoire à 3 niveaux, `/undo`, checkpoint, replay, export Trajectory |
| 🧩 **Écosystème** | MCP + ACP — branche des outils ou laisse ton IDE te piloter |
| 🌍 **Dix langues** | simplifié par défaut, avec une vraie localisation de l'interface |
| 🐍 **Python pur** | ~414 `.py` / ~77 k lignes / 32 paquets / 15+ plugins, MIT |

---

## Sans détour

**Q : C'est vraiment « fait maison » ? Un cadavre dans le placard ?**
Le noyau et l'immense majorité des capacités (`kernel/`, `core/`, `acp/`, `tools/`, `ports/`) sont implémentés de zéro en Python, alignés sur des protocoles externes uniquement pour interopérer. Il existe exactement **une exception délibérée** : la peau du TUI `--tui` reprend volontairement le style et la palette signature de **Kimi Code** (`#4FA8FF`, le spinner des phases de lune, la barre d'état à deux lignes) —« si le ressenti est bon, on ne réinvente pas ». C'est la *seule* partie qui porte un autre look, et elle est créditée dans [NOTICE](NOTICE). Tout le reste est notre chair et notre sang.

**Q : Pourquoi il me bloque avant d'exécuter des commandes ?**
C'est une fonctionnalité, pas un bug. Les commandes dangereuses demandent d'abord ; YOLO respecte les lignes rouges. Trop bavard ? `qxt safe allow <cmd>` pour la liste blanche — ne désactive pas la sécurité.

**Q : Que peut vraiment défaire `/undo` ?**
Toute **écriture** qui passe par le livre de comptes : un fichier, un pas, un tour complet. Derrière : livre transactionnel + diff `reverse_transform` + snapshots de checkpoint. Pas miraculeux, mais ça transforme « j'ai merdé » d'une perte garantie en un sauvetage probable.

**Q : Il va lire mes fichiers privés ?**
Le périmètre des outils est borné par une allow-list de domaines, la sortie réseau est bridée contre l'exfiltration et les clés sont masquées dans la sortie.

**Q : Offline total ?**
`qxt models local` pour sonder, `/offline` pour gérer Ollama. Pas d'internet, pas de souci.

**Q : On peut écrire ses propres outils/plugins ?**
Oui — métadonnées via `@plugin`, `activate(kernel)` + `kernel.provide(...)` / `kernel.require(...)`. Trois étapes :

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

## Démarrage rapide

```bash
git clone <ce dépôt> && cd qingxiaotuan-agent-cli
python -m venv .venv && source .venv/bin/activate    # Windows : .venv\Scripts\activate
pip install -e ".[dev]"

qxt setup
# ou à la main : ~/.qingxiaotuan/config.yaml
#   model.provider: deepseek
#   model.model: deepseek-chat
#   model.api_key_env: DEEPSEEK_API_KEY      # jamais de clé en clair

qxt            # on discute
qxt --print run "Salut, présente-toi en une phrase."
```

---

## Surface de commandes : 25 sous-commandes + 36 commandes slash

| Commande | À quoi ça sert |
|---|---|
| `qxt` | TUI interactif (skin Kimi Code) |
| `qxt setup` / `qxt models` | Configurer le fournisseur / lister 48 modèles |
| `qxt agent` | Agents nommés (compatible `.claude/agents`, découverte en 3 niveaux) |
| `qxt acp` | Lance un serveur ACP pour que VSCode / Zed / JetBrains te pilote |
| `qxt cron` | Tâches planifiées en arrière-plan |
| `qxt doctor` / `qxt bench` | Check-up santé / benchmark |
| `qxt arch demo` | Vérifie l'architecture en 5 couches d'une commande |

Les slash au quotidien : `/plan`·`/model`·`/undo`·`/impact`·`/swarm`·`/log`·`/stats`·`/cost`·`/budget`·`/goal`·`/sandbox`·`/offline`·`/verify`·`/audit`·`/more`·`/help` — liste complète via `Ctrl-G` dans le TUI.

---

## Trois boucles, une main ferme

- **ReActLoop** — pense → agit → observe, le rythme par défaut.
- **PlannerExecuteLoop** — un modèle fort planifie, un modèle pas cher exécute (`router.*` permet de compartimenter plan/exécution). Le roi des économies de tokens.
- **DevLoop** — écris-vérifie-soigne : `/verify` détecte le projet (Python/Node/Rust/Go), infère les commandes de test et se soigne jusqu'à N tours.

## Le modèle de sécurité : quatre barrières + défaire à livre ouvert

1. **Score statique** — chaque commande shell notée `none→critical` par `safety_engine.score()`, avec dépliage des indirections (IFS, `$VAR`, substitution, échappements ANSI-C/octal/hex, PowerShell Base64, NFKC ; ≤32 de récursion).
2. **Blocage par rayon d'impact** — tu vois ce que ça touchera **avant** que ça parte (`--impact`).
3. **Plancher de lignes rouges en YOLO** — YOLO supprime les confirmations par étape mais **ne** touche **pas** aux lignes rouges (`rm` récursif, force-push, `chmod -R 000 /`… jamais en auto).
4. **Livre de comptes** — chaque écriture est auditée ; `/undo` restaure via diff `reverse_transform` + snapshots.

Les règles résolvent toujours `deny > ask > allow`. Utilise `qxt safe allow <cmd>` pour la liste blanche ; ne désactive pas la sécu pour gagner des clics.

---

## Là où tes idées tournent

- **Mémoire** — trois niveaux (utilisateur/projet/session) + SQLite FTS5 (trigram), dégrade en texte brut si indisponible.
- **Sous-agents** — délégation typée (general-purpose / explore / plan / coder), sous-agents isolés, workers concurrents et Swarm pour découper les longues tâches.
- **Cron et arrière-plan** — `qxt cron start --detach` ; autonomie en headless.
- **Hooks** — `PreToolUse` peut `block` ou réécrire `args` (`hooks.allow_edit_args`).
- **Observabilité** — `/stats`·`/audit`·`/impact`·`/bench`·`/cost` ; le coût sur la table.
- **crypto** — v2 solide : AES-GCM(AEAD) avec `cryptography`, sinon chiffrement de flux HMAC-SHA256 ; `open()` sans MAC / altéré fait toujours fail-closed.

---

## Dev & contrat

```bash
python -m pytest tests/ -q        # 2000+ tests
python -m mypy qingxiaotuan       # porte des types
qxt --print run "Salut, une ligne." # smoke
```

- **Discipline i18n** : README_zh-CN est le maître de référence ; chaque langue est une localisation vivante, zéro dérive — **aucune traduction mécanique.**
- **Échelle** : ~414 `.py` / ~77 k lignes / 32 paquets / 15+ plugins.
- **Version** : `v0.2.014` (0.x/Beta) ; les changements qui cassent préviennent en version mineure + notes de migration.
- **En profondeur** : signatures des neuf moteurs et un tutoriel de nouvelle outil vivent dans l'appendice de `README_zh-CN.md`.

---

## License & liens

- **License** : MIT (utilise, modifie, redistribue ; garde l'avis)
- **À lire** : [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [NOTICE](NOTICE)

> Tu veux « une expérience clé en main, fermée, mariée à un fournisseur » ? Ça ne manque pas. Tu veux **tourner sur tes propres modèles, garder le contrôle et pouvoir revenir en arrière** ? Les clés sont ici.