# 青小团 / Qingxiaotuan Agent CLI

> **「Model + Harness = Agent」** —— 「考えること」と「安全に走らせること」を分離して、両方の鍵をあなたに。
> 安全第一・モデル非依存・純 Python の AI Agent Harness。`v0.2.014` · MIT · Python ≥ 3.10

**言語/Language:** [English](README.md) · [简体中文](README_zh-CN.md) · [繁體中文](README_zh-TW.md) · **日本語** · [한국어](README_ko.md) · [Español](README_es.md) · [Português (Brasil)](README_pt-BR.md) · [Français](README_fr.md) · [Deutsch](README_de.md) · [Русский](README_ru.md)

**手に取る前に：** [SECURITY.md](SECURITY.md)（脅威モデル）· [CHANGELOG.md](CHANGELOG.md)（バージョン規律）· [ARCHITECTURE.md](ARCHITECTURE.md)（アーキテクチャ）· `qxt models list-providers`

---

## ひとことで

他社は「モデルに殻を被せる」。青小团は違う——**脳みそ換え放題のボディ**を渡します。48 社のプロバイダをホットスワップ、完全オフライン動作、やらかした操作は巻き戻せて、実行する**前**に影響範囲を見せてくれます。**考えるのがモデル、それを支えるのがこいつ。**

**これは何でもない**：特定モデルの囲い込みではない（ホットスワップ・自前ホスト・オフライン、自由自在）；IDE の付属物ではない（標準ターミナルツール、ACP 経由で VSCode/Zed/JetBrains に駆動される）；一重の頼りない抽象でもない（開発者向けマイクロカーネル + 一般ユーザー向け一発 `setup`）。

---

## なにが違うのか

| こいつ | どこまでやる |
|---|---|
| 🛡️ **安全第一** | 四重ゲート：静的リスク判定、影響範囲の事前ブロック、YOLO レッドライン、トランザクション台帳の精密 `/undo` |
| 🔌 **モデル非依存** | 48 プロバイダ + ローカル Ollama + ホットスワップ + 自動ルーティング（`router.*`） |
| 🧠 **三つのメインループ** | ReAct / Planner-Execute / DevLoop を差し替え可能。一つのカーネル、複数の「思考リズム」 |
| 🔧 **マイクロカーネル** | 一行 `@plugin`、サービスレジストリ、追記専用イベントバス、hook ミドルウェア |
| 🗂️ **メモリ** | SQLite FTS5 + セッションイベントストリーム；3 層メモリ、`/undo`、checkpoint、replay、Trajectory 出力 |
| 🧩 **エコシステム** | MCP + ACP — ツールを刺すも良し、IDE に駆動されるも良し |
| 🌍 **10 言語** | デフォルトは翻訳品質込みの日本語UIにも対応、機能ごとローカライズ |
| 🐍 **純 Python** | 約 414 `.py` / 約 7.7 万行 / 32 パッケージ / 15+ プラグイン、MIT |

---

## ぶっちゃけ

**Q: これって本当に「独立自研」？隠し事ない？**
カーネルと大半の機能（`kernel/`、`core/`、`acp/`、`tools/`、`ports/`）は Python で**アーキテクチャから一行ずつ自前実装**で、外部プロトコルとの相互運用のためだけにインターフェースを合わせています。唯一わざと残した例外が一つ：`--tui` のターミナル UI は**Kimi Code の看板スタイル**（`#4FA8FF` 基調、ムーンフェイズのスピナー、二行ステータスバー）を意図的に踏襲——「手触りがいいなら再発明しない」。この部分だけ他人の皮をかぶっていて、クレジットは [NOTICE](NOTICE)。それ以外は全部、青小团の血肉です。

**Q: なんでコマンドの前に止まるの？**
仕様、バグじゃない。危険なコマンドは確認を求める；YOLO でもレッドラインは硬く守る。煩わしければ `qxt safe allow <cmd>` で許可リスト化——安全を無効化するんじゃなく。

**Q: `/undo` で実際どこまで戻せる？**
台帳に載る**書き込み**全部：単一ファイル、単一 step、ターン全体。中身はトランザクション台帳 + diff `reverse_transform` + checkpoint スナップショット。万能じゃないけど、「やらかした → 確実に損」を「多分取り返せる」に変える。

**Q: プライベートファイル読まれない？**
権限ポリシーは domain allow-list でツール範囲を制限；外部送信は防がれ、出力の秘密鍵はマスク。

**Q: 完全オフラインは？**
`qxt models local` で検出、`/offline` で Ollama 管理。ネットなしでも動く。

**Q: 自前のツール / プラグイン書ける？**
もちろん。`@plugin` でメタデータ宣言、`activate(kernel)` で `kernel.provide(...)` / `kernel.require(...)`。3 ステップ：

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

## クイックスタート

```bash
git clone <このリポジトリ> && cd qingxiaotuan-agent-cli
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

qxt setup
# 手動でも：~/.qingxiaotuan/config.yaml
#   model.provider: deepseek
#   model.model: deepseek-chat
#   model.api_key_env: DEEPSEEK_API_KEY      # 鍵を平文で書かない

qxt            # 会話開始
qxt --print run "こんにちは、あなたを一言で自己紹介して"
```

---

## コマンド面：25 サブコマンド + 36 スラッシュコマンド

| コマンド | 用途 |
|---|---|
| `qxt` | 対話型 TUI（Kimi Code スキン） |
| `qxt setup` / `qxt models` | プロバイダ設定 / 48 モデル一覧 |
| `qxt agent` | 名前付きエージェント（`.claude/agents` 互換、3 層ディスカバリ） |
| `qxt acp` | ACP server 起動、VSCode / Zed / JetBrains に駆動される |
| `qxt cron` | 定期バックグラウンドタスク |
| `qxt doctor` / `qxt bench` | 健康診断 / ベンチ |
| `qxt arch demo` | 5 層アーキテクチャを一発検証 |

よく使うスラッシュコマンド：`/plan` · `/model` · `/undo`·`/impact` · `/swarm` · `/log`·`/stats`·`/cost`·`/budget`·`/goal`·`/sandbox`·`/offline`·`/verify`·`/audit`·`/more`·`/help` — 全一覧は TUI で `Ctrl-G`。

---

## 三つのメインループ、変わらぬ安定

- **ReActLoop**：考え→動く→確認、デフォルトのリズム。
- **PlannerExecuteLoop**：強いモデルが計画、安いモデルが実行（`router.*` で compartmentalize 対応）。トークン節約家。
- **DevLoop**：書いて→検証して→自癒。`/verify` がプロジェクト種別（Python/Node/Rust/Go）を自動判定し、テストコマンドを推測して最大 N ラウンド自修。

## 安全モデル：四重ゲート + 台帳式取り消し

1. **静的スコアリング** — 全シェルコマンドを `safety_engine.score()` で `none→critical` 判定、間接入力展開に対応（IFS、`$VAR`、コマンド置換、ANSI-C/8進/16進エスケープ、PowerShell Base64、NFKC；再帰 ≤32）。
2. **影響範囲の事前ブロック** — 実行**前**に触る範囲を見せる（`--impact`）。
3. **YOLO レッドラインの床** — YOLO は逐次確認を消せるが、ハードレッドライン（再帰 `rm`、force-push、`chmod -R 000 /`…）は**絶対に自動実行できない**。
4. **トランザクション台帳** — 書き込みはすべて記録、`/undo` は diff `reverse_transform` + スナップショットで精密復元。

ルールは常に `deny > ask > allow`。`qxt safe allow <cmd>` で許可リスト化を、安全の無効化でクリックを節約しないで。

---

## アイデアを走らせる場所

- **メモリ** — 3 層（ユーザー/プロジェクト/セッション）+ SQLite FTS5（trigram）、使えなければ平文に自動ダウングレード。
- **サブエージェント** — 型付き委譲（general-purpose / explore / plan / coder）、隔離サブエージェント、並行ワーカー、Swarm で長タスクを分割。
- **Cron & バックグラウンド** — `qxt cron start --detach`；ヘッドレスで自律稼働。
- **Hooks** — `PreToolUse` が `block` や `args` 書き換え（`hooks.allow_edit_args`）。
- **可観測性** — `/stats`·`/audit`·`/impact`·`/bench`·`/cost`、コストを机の上へ。
- **crypto** — v2 は堅牢：`cryptography` があれば AES-GCM(AEAD)、無ければ HMAC-SHA256 ストリーム暗号、`open()` で MAC 欠落・改変は常に fail-closed。

---

## 開発 & 規約

```bash
python -m pytest tests/ -q        # 2000+ テスト
python -m mypy qingxiaotuan       # 型ゲート
qxt --print run "こんにちは。"     # スモーク
```

- **多言語文書規律**：`README_zh-CN.md` が権威あるマスター。各言語版は「生き生き、ドリフトなし」のローカライズで、**機械翻訳禁止**。
- **規模**：約 414 `.py` / 約 7.7 万行 / 32 パッケージ / プラグイン 15+。
- **バージョン**：`v0.2.014`（0.x/Beta）；破壊的変更はマイナーバージョンで事前告知 + 移行ヒント。
- **深掘り**：九大エンジンのシグネチャと新規ツールのチュートリアルは `README_zh-CN.md` 末尾の付録。

---

## License & 関連

- **License**：MIT（自由な使用・変更・再配布、著作権表示を保持）
- **必読**：[SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [NOTICE](NOTICE)

> 「箱出しでクローズド、特定ベンダーに縛られたい」なら他にもある。**自分のモデルで走らせ、コントロールを握り、トラブル時は取り戻せる**——鍵はここに。