# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

[English](README.md) | [简体中文](README_zh-CN.md) | **繁體中文** | [日本語](README_ja.md) | [한국어](README_ko.md) | [Español](README_es.md) | [Português (BR)](README_pt-BR.md) | [Français](README_fr.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> 一個圍繞單一理念打造的 agent CLI:**agent 動手之前,先掌握爆炸半徑(blast radius)。**
> 每條 shell 指令都在*執行前*完成風險分析與評級——關鍵操作預設直接阻擋;還有一隻活生生的吉祥物,清楚顯示 agent 此刻所處的狀態。

純 Python、零編譯、模型中立。119 個模組、約 21k 行原始碼,背後有 **591 項離線測試**(含 mock server 端到端測試)在 Python 3.11–3.13 CI 矩陣上把關。

<!-- 📹 TODO(demo): 在此放置一段 30–60 秒的終端機錄影,呈現:
     qxt chat → 下達任務 → 吉祥物狀態流轉 → safety 攔截瞬間。
     在那之前,下方的 ASCII 狀態機就是實際樣貌。 -->

## 吉祥物就是狀態列

Qingxiaotuan(意為「小綠糰子」)不是靜態的 logo——它是一台**具備生命徵象的狀態機**,在你的終端機中即時呈現,讓你隨時掌握 agent 正在做什麼:

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| 狀態 | 觸發時機 | 視覺呈現 |
| --- | --- | --- |
| `idle` | 等待輸入 | 平緩呼吸 |
| `thinking` | 模型推理中 | 左右搖擺 + spinner 轉動 |
| `working` | 正在執行工具呼叫 | 漂浮 + 進度環 |
| `alert` | 危險指令遭 **safety 護欄阻擋** | 轉為橘色、微微顫抖——看得*見*的爆炸半徑控制 |
| `done` | 任務完成 | 彎月眼 + 星光閃爍 |

## 為什麼選擇它

- **預設最小爆炸半徑(Minimum Blast Radius)** — 每條 shell 指令執行前,純 Python 的 `safety` 引擎都會先做靜態風險分析;`critical` 級指令(`rm -rf /`、`git push --force`、`DROP TABLE`)在**執行前就遭到阻擋**,而不是事後才留下記錄。
- **工作區異動皆可回復** — 以 `/diff` 檢視變更、以 `/undo` 復原(內建 `--safe` 模式);各工作階段採用 append-only 事件流,可完整重播。
- **10 個能力引擎,全為純 Python、零 IPC** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market,全部經由內建健康檢查的註冊表串接(`qxt ext selftest`)。
- **模型中立** — 透過 OpenAI 相容 / Anthropic 轉接器,可接 DeepSeek、Claude、Gemini 或本地模型;隨時以 `/model` 切換。`/route` 會估算任務難度並建議模型(僅供參考)。
- **多 agent 群集(swarm)** — `/swarm` 先由強模型規劃,再由多個較便宜的模型並行執行子任務,最後交由強模型決定接受或退回結果。
- **自我改進迴圈** — 每次執行結束後,reflector 會診斷失敗原因(能辨識 pytest/npm/git/cargo/dotnet)並淬煉出護欄,讓同樣的錯誤下次更早被攔下。
- **重新啟動也不消失的記憶** — 三層結構(工作階段 / 事實 / 技能),並以 SQLite FTS5 全文檢索跨工作階段召回。

## 快速開始

> 需要 Python ≥ 3.11。純 Python——沒有任何東西需要編譯。

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

也支援無頭(headless)一次性任務:

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **加密說明:** `crypto` 引擎僅使用標準函式庫(PBKDF2-HMAC-SHA256 金鑰衍生 + SHA256-keystream 串流密碼器 + SHA-256 指紋)。此串流密碼器屬輕量設計、未附驗證標籤(authentication tag)——足以應付需要防篡改的本機用途,並非高安全場景適用的密碼學原件。

## 斜線指令

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

重點功能:

| 指令 | 說明 |
| --- | --- |
| `/plan` | 唯讀分析模式——所有具修改性的工具一律攔截 |
| `/swarm` | 多 agent 協作(強模型規劃 → 低成本模型並行執行 → 強模型驗收) |
| `/route` | 難度評估 + 模型建議(僅供參考,不自動切換) |
| `/compact` | 摺疊較舊的歷史紀錄,收回 context 可用額度 |
| `/cost` | token 用量、快取命中率、預估花費 |
| `/undo` | 快速回復工作區(`all` / `--safe` / 單一檔案) |

## 能力引擎

| 引擎 | 提供能力 |
| --- | --- |
| `diff` | 行 / 字詞層級 diff + patch + 三方合併 |
| `crypto` | PBKDF2-HMAC-SHA256 金鑰衍生 + SHA256-keystream 串流密碼器 + 指紋 |
| `index` | FNV-1a 增量符號索引 |
| `ansi` | 終端機 escape 序列的解析 / 剝除 / 渲染 |
| `safety` | 最小爆炸半徑護欄:風險評分 + 爆炸半徑 + 阻擋 |
| `json` | RFC 6901 pointer / 逐路徑 diff / 深度合併 |
| `search` | 遞迴 regex 搜尋(忽略 node_modules/.git) |
| `notify` | 跨平台桌面通知 |
| `rules` | YAML 政策驗證(no-eval 的安全運算式) |
| `skill-market` | 技能套件註冊表:pull / publish / search |

```bash
qxt ext engines     # list engines actually available
qxt ext selftest    # launch each engine, report health
```

## 架構

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

## 品質

- **591 項離線測試** — 完全不需要網路,包含針對本機 OpenAI 相容 mock server 的端到端測試(agent 工具迴圈、串流、背景 worker、真實 `qxt` 子程序)。
- **CI 矩陣**:Python 3.11 / 3.12 / 3.13。
- 執行期相依套件只有五個:`openai`、`pyyaml`、`rich`、`prompt_toolkit`、`httpx`。

## 路線圖

- [ ] 自動模型路由(將 `router.enabled` 接入 agent 迴圈;目前 `/route` 僅供參考)
- [ ] 發布至 PyPI(`pip install qingxiaotuan`)
- [ ] skill-market 公開註冊表

## 參與貢獻

歡迎提出 Issues 與 PR——請見 [CONTRIBUTING.md](./CONTRIBUTING.md)。本 README 各語言版本的翻譯直接在各檔案內協調維護;修正任何語言都歡迎透過 PR 進行。

## 授權與致謝

MIT——詳見 [LICENSE](./LICENSE)。

站在開源巨人的肩膀上;以下構想經吸收理解後,均以 Python 從零重新實作:

- **DeepSeek Harness (dsh)** — 微核心(Cordis 風格)、一切皆外掛、以 profile 為基礎的設定、模型中立轉接、無頭任務、append-only 的工作階段事件流
- **Hermes Agent** — 三層記憶、技能自我演化迴圈、SOUL.md 身分識別、SQLite FTS5 跨工作階段召回、cron 定時任務
- **Claude Code** — 流暢的 CLI 互動、thinking 與工具狀態即時顯示、大型 context 管理、互動式斜線指令
