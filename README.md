# Bizreach Auto Scout

医院・病院経営コンサルティング（経営戦略研究所株式会社）向けの、**ビズリーチ自動スカウト文面生成・送信システム**です。

候補者プロフィールを取り込み、対象条件で絞り込み、Claude（Anthropic API）で**初回送信用と再送用の2通**をパーソナライズ生成し、ビズリーチ上へ自動送信、5日後の再送までスケジュールします。

---

## ⚠️ 重要な注意（必ずお読みください）

- **スカウト送信は取り消せない外向きの操作**です。まずは `BIZSCOUT_DRY_RUN=true`（既定）で文面を検証してから本番送信に切り替えてください。
- ビズリーチのブラウザ自動操作は、**利用規約・自動化ポリシーの確認**のうえ、ご自身のアカウント・責任で運用してください。過度な送信はアカウント制限の対象になり得ます。
- `config/bizreach_selectors.py` のセレクタ・URLは**想定値**です。実際の画面に合わせて `config/bizreach_selectors.yaml` で上書きしてください（後述）。
- `consultant_profiles_v2.docx`（在籍コンサルタント情報）は別途ご用意ください。未設定時はサンプル（`config/consultants.sample.json`）で動作します。

---

## セットアップ

```bash
# 1) 仮想環境と依存関係
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                 # もしくは: pip install -r requirements.txt
playwright install chromium      # ブラウザ自動操作を使う場合のみ

# 2) 環境変数
cp .env.example .env
#   ANTHROPIC_API_KEY / BIZREACH_EMAIL / BIZREACH_PASSWORD などを設定
#   まずは BIZSCOUT_DRY_RUN=true のまま検証することを推奨
```

主な環境変数（`.env.example` 参照）:

| 変数 | 意味 |
|------|------|
| `ANTHROPIC_API_KEY` | Claude APIキー |
| `BIZSCOUT_MODEL` | 生成モデル（既定 `claude-opus-4-8`。コスト重視なら sonnet 系） |
| `BIZREACH_EMAIL` / `BIZREACH_PASSWORD` | ビズリーチ採用企業アカウント |
| `BIZSCOUT_DRY_RUN` | `true` で実送信せず文面入力のみ（推奨） |
| `BIZSCOUT_MAX_SENDS_PER_RUN` | 1実行あたりの送信上限（暴走防止） |
| `BIZSCOUT_SEND_DELAY_MIN/MAX` | 送信間隔（秒）の下限/上限 |
| `BIZSCOUT_KILL_SWITCH` | このファイルが存在する間は送信を全停止 |
| `BIZSCOUT_RESEND_AFTER_DAYS` | 初回送信から再送までの日数（既定5） |

---

## 使い方

### 1. 文面だけ生成（送信しない・最も安全）

```bash
# 貼り付けテキストから
cat examples/sample_profile.txt | bizscout generate

# CSVから
bizscout generate --source csv --input examples/sample_candidates.csv --save
```

件名・本文がそれぞれコードブロックで「初回送信用 → 再送用」の順に出力されます。

### 2. 取り込み→生成→送信を一括実行

```bash
# ビズリーチを自動操作（保存検索の結果URLを指定）
bizscout run --source bizreach --search-url "https://cr-support.jp/search?saved=..." --max 30

# CSV/テキストの候補者に対して（profile_url があれば送信）
bizscout run --source csv --input candidates.csv --no-send   # 生成のみ
```

`BIZSCOUT_DRY_RUN=true` の場合、件名・本文は入力欄まで埋めますが**送信ボタンは押しません**。

### 3. 再送（初回送信から5日後）

```bash
bizscout run-resends        # 期限の到来した再送を送信
```

cron 例（毎朝9時）:

```
0 9 * * *  cd /path/to/repo && .venv/bin/bizscout run-resends >> logs/resend.log 2>&1
```

### 4. 完全自動運用（常駐・定期実行）

```bash
bizscout doctor    # 起動前チェック（APIキー/認証/セレクタ/DB/Playwright等）。失敗で終了コード1
bizscout serve --search-url "https://cr-support.jp/search?saved=..." --interval 86400
#   → 取り込み→生成→送信→再送 を interval 秒ごとに自動実行（kill switchで即停止可）
bizscout serve --search-url "..." --once   # 1サイクルだけ実行
```

定期実行は `bizscout serve`（常駐）のほか、cron / systemd / Docker でも運用できます。手順は **[docs/運用手順.md](docs/運用手順.md)** と **[deploy/](deploy/)** を参照してください。

### 5. その他

```bash
bizscout preview BU3765516           # 保存済みスカウトを表示
bizscout report                      # 送信状況・要確認候補者の一覧
bizscout import-consultants path/to/consultant_profiles_v2.docx
```

---

## 自動運用・デプロイ

完全自動運用（使い方B）のためのドキュメントと資材を用意しています。

| ファイル | 内容 |
|---|---|
| [docs/運用手順.md](docs/運用手順.md) | インストール→`.env`→セレクタ→`doctor`→ドライラン→本番送信→定期実行 までの手順 |
| [docs/セレクタ設定ガイド.md](docs/セレクタ設定ガイド.md) | ビズリーチ実画面のセレクタを開発者ツールで特定し `bizreach_selectors.yaml` に設定する方法 |
| [docs/トラブルシューティング.md](docs/トラブルシューティング.md) | ログイン失敗・送信されない・再送が走らない等の対処 |
| [docs/GitHub Actionsで運用.md](docs/GitHub%20Actions%E3%81%A7%E9%81%8B%E7%94%A8.md) | **サーバー不要**。GitHub Actions をスケジューラ兼実行環境にする手順（`.github/workflows/scout.yml`） |
| [Dockerfile](Dockerfile) / [docker-compose.yml](docker-compose.yml) | Playwright同梱イメージでの常駐運用 |
| [deploy/](deploy/) | cron 例・systemd unit/timer・Windowsタスクスケジューラの案内 |

> 実運用前に必ず `bizscout doctor` で環境を点検し、まず `BIZSCOUT_DRY_RUN=true` で文面を検証してください。

---

## コンサルタント共通点マッチング

`consultant_profiles_v2.docx` を取り込み、候補者と**共通点（前職・業界・大学・職種）のある在籍コンサルタント**を抽出します。

```bash
bizscout import-consultants consultant_profiles_v2.docx --out config/consultants.json
```

紹介する人数は `scout_rules.yaml` の `matching.max_intro_consultants`（既定3名。優先度順=リクルート/保険→共通点数の多い順で上位N名）で上限を設けています。共通点コンサルタントの紹介は、本文中で1人ずつ独立したブロック（紹介文＋▼氏名 プロフィール＋URL）としてシステムが組み立てます（文章を連結せず視認性を優先）。生成モデルへの指示（emit_scoutツールの`consultant_intros`）は必須項目とし、省略された場合は1回だけ修正リトライで再取得します。

特別ルール（仕様に準拠）:
- **リクルート出身**の候補者 → リクルート出身コンサルタントが7名在籍する旨を明記し、共通点のあるメンバー全員を紹介対象に含める。
- **保険業界出身**の候補者 → プルデンシャル生命出身者の在籍をアピールし、専用URLを紹介。
- 再送文でも共通点のあるメンバーに（`resend.max_consultant_mentions`名まで）同じ形式で言及。

> docx のレイアウトは固定でないため、取り込み後は `config/consultants.json` を必ず目視確認してください。

---

## ビズリーチのセレクタ設定（実運用前に必須）

`src/bizreach_scout/bizreach/selectors.py` のURL・セレクタは想定値です。実際のDOMに合わせて上書きします:

```bash
cp config/bizreach_selectors.example.yaml config/bizreach_selectors.yaml
# ブラウザの開発者ツールで実際の selector を確認して記入（空欄はデフォルトを使用）
```

2段階認証がある場合は、初回は `--no-headless` で起動し手動認証 → セッションが
`BIZREACH_STORAGE_STATE` に保存され、以降は再利用されます。

---

## 文面生成の仕組み（品質の担保）

仕様の本文構成 ①〜⑫ のうち、**固定要素はシステムが決定的に付与**し、文言の正確性を保証します。

- システム付与（誤りが起きない）: ①テンプレートではない旨／②会員番号様／③名前非表示の注記／⑨カジュアル面談定型文／⑩タップ案内／⑪署名（代表取締役社長 岩渕龍正）／⑫フッター（初回のみ）
- LLM生成（パーソナライズ）: 件名、④挨拶＋限定オファー、⑤スカウト理由、⑥会社紹介、⑦入社後キャリア、⑧ポジション魅力、再送本文

初回と再送はそれぞれ件名形式が異なります。初回は `【Premium Offer】〜`、再送は `【どうしても諦めきれず２度目のご連絡です。】〜` で始まり、再送は初回の約1/2の分量・熱意を前面に・フッターなしで生成されます。

生成後は `validators.py` で禁止表現（`――`・`「」`・「ビズリーチにて」）、件名形式（`【Premium Offer】`）、感嘆符数、絵文字をチェックし、違反時は1度だけ自動修正を試みます。

ルールの単一情報源:
- `config/company.yaml` … 企業・求人の事実値
- `config/scout_rules.yaml` … 対象条件・トーン・禁止表現
- `config/prompt_template.md` … 生成プロンプト本体

---

## 安全機構

- **dry-run**: `BIZSCOUT_DRY_RUN=true` で実送信を停止。
- **kill switch**: `BIZSCOUT_KILL_SWITCH` のファイルを作成すると即時に送信停止。
- **送信上限**: `BIZSCOUT_MAX_SENDS_PER_RUN`。
- **重複防止**: 同一会員番号への初回は二度生成・送信しない（SQLite管理）。
- **対象条件**: 27歳〜42歳／同一企業2.5年以上／男性／大学卒以上／国内の教育機関出身／日本語ネイティブ（の可能性が高い）を満たさない（または不明の）候補者は自動送信から除外し「要確認」として記録（`bizscout report`）。海外の教育機関出身・日本語検定(JLPT等)保有はレジュメに直接の判定フィールドが無いため、学歴名の日本語表記有無・検定資格の記載を代替シグナルとして判定する（`config/scout_rules.yaml` の `exclude_overseas_education` / `exclude_non_japanese_native` で無効化可能）。
- **人間的な間隔**: 送信間隔・操作間にランダム待機。

---

## テスト

```bash
pip install -r requirements-dev.txt
pytest -q
```

ネットワーク不要（Anthropic クライアントはモック）で全ケースが通ります。

---

## ディレクトリ構成

```
config/            企業情報・ルール・プロンプト・コンサルデータ・セレクタ
src/bizreach_scout/
  ├─ generation/   プロンプト構築・Claude生成・固定要素組み立て・検証
  ├─ ingest/       CSV / 貼り付けテキスト / ビズリーチ の取り込み
  ├─ bizreach/     Playwright（ログイン・検索・プロフィール・送信）
  ├─ storage/      SQLite（重複防止・再送スケジュール・監査）
  ├─ ops.py        起動前チェック（bizscout doctor）
  ├─ service.py    常駐・定期実行（bizscout serve）
  ├─ eligibility.py / consultants.py / pipeline.py / scheduler.py / cli.py
docs/              運用手順・セレクタ設定ガイド・トラブルシューティング
deploy/            cron / systemd の例
Dockerfile / docker-compose.yml   コンテナ運用
examples/          サンプルCSV・プロフィール
tests/             ユニットテスト
```

詳細は [ARCHITECTURE.md](ARCHITECTURE.md) を参照。
