# アーキテクチャ

## 全体フロー

```
            ┌─────────────┐
 取り込み →  │ CandidateSource │  CSV / Text / Bizreach(Playwright)
            └──────┬──────┘
                   ▼
            ┌─────────────┐
 条件判定 →  │ eligibility   │  27歳以上 / 同一企業3年+ / 男性 / 大学卒+
            └──────┬──────┘
                   ▼  （満たさない→要確認として除外）
            ┌─────────────┐
 重複判定 →  │ Repository    │  SQLite（会員番号で初回処理済みか）
            └──────┬──────┘
                   ▼
            ┌─────────────┐
 共通点   →  │ consultants   │  在籍コンサルとの共通点・特別ルール（リクルート/保険）
            └──────┬──────┘
                   ▼
            ┌─────────────┐
 文面生成 →  │ generation    │  prompt→Claude(tool_use)→固定要素組み立て→検証
            └──────┬──────┘
                   ▼
            ┌─────────────┐
 保存     →  │ Repository    │  初回/再送を generated で保存
            └──────┬──────┘
                   ▼
            ┌─────────────┐
 初回送信 →  │ bizreach.sender │  dry_run / kill switch / 上限 / 間隔
            └──────┬──────┘
                   ▼  mark_sent → 再送 scheduled_at = +5日
            ┌─────────────┐
 再送     →  │ scheduler     │  期限到来分を送信（run-resends）
            └─────────────┘
```

オーケストレーションは `pipeline.ScoutPipeline`（初回）と `scheduler.run_due_resends`（再送）。

## モジュール責務

| モジュール | 責務 |
|-----------|------|
| `models.py` | ドメインモデル（Candidate / ConsultantProfile / GeneratedScout 等） |
| `config.py` | `.env`・YAML・コンサルJSON のロード、実行設定 |
| `eligibility.py` | 必須条件の判定（不明は「要確認」に倒す） |
| `consultants.py` | 共通点マッチングとリクルート/保険の特別ルール |
| `generation/prompt.py` | システムプロンプト構築・トーン選択・`emit_scout` ツール定義 |
| `generation/templates.py` | 固定要素（①②③⑨⑩⑪⑫）の決定的組み立て |
| `generation/generator.py` | Claude 呼び出し・構造化出力の組み立て・検証・再試行 |
| `generation/validators.py` | 禁止表現・件名形式・感嘆符・絵文字の検査 |
| `ingest/*` | CSV / 貼り付けテキスト / ビズリーチ からの取り込み |
| `bizreach/*` | Playwright によるログイン・検索・プロフィール抽出・送信 |
| `storage/repository.py` | SQLite による重複防止・再送スケジュール・監査ログ |
| `pipeline.py` / `scheduler.py` | 一括処理・再送実行 |
| `cli.py` | `bizscout` コマンド |

## 文面の構造保証

仕様の本文 ①〜⑫ のうち、固定文言（ヘッダー・定型文・署名・フッター）は
`templates.py` がコードで付与する。LLM が生成するのは件名と ④〜⑧・再送本文のみ。
初回は ⑫フッターまで付与、再送はフッターなし（署名まで）で件名接頭辞も別（`【どうしても諦めきれず２度目のご連絡です。】`）。
これにより「会員番号様の表記」「定型文の一字一句」「署名(岩渕龍正)」を
モデルのゆらぎから守る。`generator.py` は `emit_scout` ツールで構造化出力を強制し、
JSON パースの失敗やフォーマット崩れを排除する。

## 設定の単一情報源

- 事実値（年収・クライアント数・URL・人数）→ `config/company.yaml`
- ルール（対象条件・トーン・禁止表現・マッチング語彙）→ `config/scout_rules.yaml`
- 生成プロンプト本文 → `config/prompt_template.md`

コードはこれらを参照するため、文面ポリシーの調整は基本的に設定編集だけで完結する。

## 拡張ポイント

- 新しい取り込み元 → `ingest/base.CandidateSource` を実装。
- ビズリーチUI変更 → `config/bizreach_selectors.yaml` で吸収（コード変更不要）。
- 他媒体への展開 → `bizreach/` 相当のドライバを追加し `pipeline` に注入。
