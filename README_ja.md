# Qingxiaotuan CLI (青小团)

[![CI](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml/badge.svg)](https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](./CONTRIBUTING.md)

[English](README.md) | [简体中文](README_zh-CN.md) | [繁體中文](README_zh-TW.md) | **日本語** | [한국어](README_ko.md) | [Español](README_es.md) | [Português (BR)](README_pt-BR.md) | [Français](README_fr.md) | [Deutsch](README_de.md) | [Русский](README_ru.md)

> 一つの考え方を軸に構築されたエージェント CLI:**エージェントが何かに触れる前に、その影響範囲（blast radius）を把握する。**
> すべてのシェルコマンドは実行*前*にリスク分析と重要度評価が行われます。重大な操作はデフォルトでブロックされ、生きているマスコットがエージェントの現在状態を正確に表示します。

Pure Python・コンパイル不要・モデルニュートラル。119 モジュール、約 21k 行のソースコードを、Python 3.11–3.13 の CI マトリクス上で動く **591 件のオフラインテスト**（モックサーバーによる E2E テストを含む）が支えています。

<!-- 📹 TODO(demo): ここに 30〜60 秒のターミナル録画を配置してください。
     内容: qxt chat → タスク実行 → マスコットの状態遷移 → safety が介入する瞬間。
     それまでの間、下の ASCII 状態マシンこそが本物です。 -->

## マスコットはステータスバー

Qingxiaotuan（「小さな緑のお団子」の意）は静的なロゴではありません。**バイタルサインを持つステートマシン**であり、ターミナル内にリアルタイムで描画されるため、エージェントが今何をしているのかを常に把握できます：

```
  idle      thinking      working       alert        done
  ( ◡ )     ? ⠋          ( • • )      ( @ • )      ✨
 ╭─────╮   ( ◠ ◠ )      ╭─────╮     ╭─────╮      ( ^ ^ )
 ╰─────╯   ╭─────╮      ╰─────╯     ╰─────╯      ╭─────╮
           ╰─────╯       ▔▔▔▔▔        ⚠ BLOCKED    ╰─────╯
  waiting  reasoning    tool call    intercepted   finished
```

| 状態 | 状況 | 見た目 |
| --- | --- | --- |
| `idle` | 入力待ち | 落ち着いた呼吸 |
| `thinking` | モデルが推論中 | 揺れ＋スピナー |
| `working` | ツール呼び出しを実行中 | 浮遊＋プログレスリング |
| `alert` | 危険なコマンドを **safety ガードレールがブロック** | オレンジ色に変わって震える — *見てわかる*影響範囲（blast radius）制御 |
| `done` | タスク完了 | 三日月型の目＋キラキラ |

## なぜこの CLI なのか

- **デフォルトで最小の影響範囲（Minimum Blast Radius）** — すべてのシェルコマンドは実行前に、純 Python 製の `safety` エンジンが静的リスク分析を行います。`critical` に分類されたコマンド（`rm -rf /`、`git push --force`、`DROP TABLE`）は、実行後に記録されるだけでなく**実行前にブロック**されます。
- **可逆的なワークスペース操作** — `/diff` で変更内容を確認、`/undo` でロールバック（`--safe` モード付き）。セッションは追記専用（append-only）のイベントストリームから再生できます。
- **10 のケイパビリティエンジン、すべて純 Python、IPC ゼロ** — diff / crypto / index / ansi / safety / json / search / notify / rules / skill-market を、ヘルスチェック付きのレジストリで接続（`qxt ext selftest`）。
- **モデルニュートラル** — OpenAI 互換 / Anthropic アダプター経由で DeepSeek、Claude、Gemini、ローカルモデルを利用可能。`/model` でいつでも切り替えられます。`/route` はタスク難易度を見積もり、モデルを提案します（アドバイザリ）。
- **マルチエージェントスウォーム** — `/swarm` は強力なモデルで計画し、安価なモデルでサブタスクを並列実行した後、強力なモデルに結果の承認／却下を判定させます。
- **自己改善ループ** — 各実行後、リフレクターが失敗を診断し（pytest/npm/git/cargo/dotnet を認識）、ガードレールとして蒸留します。同じ失敗は次回、より早い段階で捕捉されます。
- **再起動を超えて残るメモリ** — session / facts / skills の 3 層構成。SQLite FTS5 による全文検索で、セッションをまたいで想起できます。

## クイックスタート

> Python 3.11 以上が必要です。純 Python 製 — コンパイルするものは何もありません。

```bash
git clone https://github.com/Qingnai-Technology-kino-koki/Qingxiaotuan-Agent-CLI.git
cd qingxiaotuan
pip install -e .

qxt setup     # 30-second wizard: pick a provider, paste your API key
qxt chat      # start talking
```

ヘッドレスでのワンショットタスクにも対応しています:

```bash
qxt run "refactor utils.py into two modules and keep tests green"
```

> **暗号化に関する注記:** `crypto` エンジンは標準ライブラリのみで構築されています（PBKDF2-HMAC-SHA256 による鍵導出 + SHA256 キーストリーム方式のストリーム暗号 + SHA-256 フィンガープリント）。このストリーム暗号は認証タグを持たない軽量設計であり、改ざん検知を伴うローカル用途には十分ですが、高セキュリティ向けのプリミティブではありません。

## スラッシュコマンド

`/help` `/tools` `/skills` `/memory` `/usage` `/cost` `/context` `/compact` `/diff` `/undo` `/model` `/effort` `/mode` `/plan` `/resume` `/swarm` `/route` `/clear` `/more` `/exit`

主なコマンド:

| コマンド | 機能 |
| --- | --- |
| `/plan` | 読み取り専用の分析モード — 変更を加えるツールは介入されます |
| `/swarm` | マルチエージェント協調（強力なモデルで計画 → 安価なモデルで並列実行 → 強力なモデルで受領判断） |
| `/route` | 難易度の見積もり + モデル提案（アドバイザリ、自動切り替えなし） |
| `/compact` | 古い履歴を折りたたんでコンテキスト予算を回収 |
| `/cost` | トークン使用量、キャッシュヒット率、推定支出額 |
| `/undo` | ワークスペースを素早くロールバック（`all` / `--safe` / ファイル単位） |

## ケイパビリティエンジン

| エンジン | 提供機能 |
| --- | --- |
| `diff` | 行／単語レベルの差分 + パッチ適用 + 3-way マージ |
| `crypto` | PBKDF2-HMAC-SHA256 による鍵導出 + SHA256 キーストリーム方式のストリーム暗号 + フィンガープリント |
| `index` | FNV-1a によるインクリメンタルなシンボルインデックス |
| `ansi` | ターミナルエスケープシーケンスの解析 / 除去 / 描画 |
| `safety` | 最小影響範囲（minimum-blast-radius）ガードレール: リスクスコアリング + 影響範囲算出 + ブロック |
| `json` | RFC 6901 ポインター / パス単位の差分 / ディープマージ |
| `search` | 再帰的な正規表現検索（node_modules/.git を除外） |
| `notify` | クロスプラットフォームのデスクトップ通知 |
| `rules` | YAML ポリシー検証（eval を使わない安全な式） |
| `skill-market` | スキルパッケージレジストリ: pull / publish / search |

```bash
qxt ext engines     # list engines actually available
qxt ext selftest    # launch each engine, report health
```

## アーキテクチャ

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

- **591 件のオフラインテスト** — ネットワーク不要。ローカルの OpenAI 互換モックサーバーに対する E2E 実行（エージェントのツールループ、ストリーミング、バックグラウンドワーカー、実際の `qxt` サブプロセス）を含みます。
- **CI マトリクス**: Python 3.11 / 3.12 / 3.13。
- ランタイム依存はわずか 5 つ: `openai`、`pyyaml`、`rich`、`prompt_toolkit`、`httpx`。

## ロードマップ

- [ ] 自動モデルルーティング（`router.enabled` をエージェントループに接続。現時点で `/route` はアドバイザリ）
- [ ] PyPI への公開（`pip install qingxiaotuan`）
- [ ] skill-market の公開レジストリ

## コントリビューション

Issue や PR を歓迎します — [CONTRIBUTING.md](./CONTRIBUTING.md) を参照してください。本 README の翻訳はこのファイル内で一元管理しており、どの言語の修正も PR で受け付けます。

## ライセンス & クレジット

MIT — [LICENSE](./LICENSE) を参照してください。

オープンソースの巨人の肩の上に立つプロジェクトです。以下から着想を得て、Python でゼロから再実装しました:

- **DeepSeek Harness (dsh)** — マイクロカーネル（Cordis スタイル）、すべてをプラグインとして扱う設計、プロファイルベースの設定、モデルニュートラルなアダプテーション、ヘッドレスタスク、追記専用のセッションイベントストリーム
- **Hermes Agent** — 3 層メモリ、スキル自己進化ループ、SOUL.md アイデンティティ、SQLite FTS5 によるセッション横断の想起、cron ジョブ
- **Claude Code** — 流れるような CLI インタラクション、思考中／ツール実行状況のライブ表示、大規模コンテキスト管理、インタラクティブなスラッシュコマンド
