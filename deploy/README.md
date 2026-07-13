# デプロイ・定期実行ガイド

> **📌 現在の本番運用は GitHub Actions です**（`.github/workflows/scout.yml` /
> 手順は `docs/GitHub Actionsで運用.md`）。定期実行は 16:09 / 18:39 JST の2枠。
> **本ディレクトリは自前サーバ（Docker / cron / systemd）で運用する場合の参考資料**であり、
> 以下に登場する `09:00` / `10:00` などの時刻は一例です。現行の本番スケジュールとは異なります。

Bizreach Auto Scout を **Docker / cron / systemd / Windows タスクスケジューラ** で
運用するための手順をまとめます。

> ## ⚠️ 最初に必ず読む — まずは DRY-RUN で検証
>
> スカウト送信は**取り消せない外向き操作**です。本番送信を有効にする前に、必ず
> `.env` で **`BIZSCOUT_DRY_RUN=true`**（実送信なし・生成と保存のみ）のまま運用し、
> 生成文面・動作・ログを十分に確認してください。問題がなければ `false` に切り替えます。
>
> **緊急停止（kill switch）**: 送信を即座に止めたいときは kill switch ファイルを作成します。
> ```bash
> touch data/state/STOP        # 既定パス（.env の BIZSCOUT_KILL_SWITCH で変更可）
> ```
> ファイルが存在する間、すべての送信・再送はスキップされます。再開するには削除します。
> ```bash
> rm data/state/STOP
> ```

---

## 事前準備（共通）

```bash
cp .env.example .env
# ANTHROPIC_API_KEY / BIZREACH_EMAIL / BIZREACH_PASSWORD などを設定
# まずは BIZSCOUT_DRY_RUN=true のまま
```

主な環境変数（詳細は `.env.example`）:

| 変数 | 役割 |
| --- | --- |
| `ANTHROPIC_API_KEY` | 文面生成に使う Anthropic API キー |
| `BIZREACH_EMAIL` / `BIZREACH_PASSWORD` | ビズリーチのログイン情報 |
| `BIZSCOUT_DRY_RUN` | `true` で実送信せず生成・保存のみ（**まずはこれ**） |
| `BIZSCOUT_MAX_SENDS_PER_RUN` | 1回の実行あたりの送信上限（暴走防止） |
| `BIZSCOUT_KILL_SWITCH` | 緊急停止スイッチのパス（既定 `data/state/STOP`） |
| `BIZSCOUT_RESEND_AFTER_DAYS` | 初回送信から再送までの日数 |

`config/bizreach_selectors.yaml`（任意）でビズリーチ画面のセレクタを上書きできます。

---

## 1. Docker / docker compose

Chromium 同梱の Playwright 公式イメージをベースにしているため、ブラウザの追加導入は不要です。

```bash
# ビルド
docker compose build

# 単発で状況確認（送信しない）
docker compose run --rm bizscout report

# 常駐運用（compose の command: serve ... を実行）
docker compose up -d

# ログ確認 / 停止
docker compose logs -f bizscout
docker compose down
```

- `data/` と `config/` はホスト側ディレクトリをボリュームとしてマウントします
  （DB・ログインセッション・kill switch・セレクタ上書きを永続化・反映）。
- `docker-compose.yml` の `command` に `serve --search-url ... --interval 86400` の例を記載。
  単発バッチにしたい場合は `command: ["run", "--source", "bizreach", "--search-url", "..."]`
  や `["run-resends"]` に変更します。
- kill switch はホスト側で `touch data/state/STOP` を作成すればコンテナ内にも反映されます。

---

## 2. cron（Linux / macOS）

`deploy/crontab.example` を環境に合わせて編集し、`crontab -e` に貼り付けます。

```bash
crontab -e
# deploy/crontab.example の内容を貼り付け、
#   BIZSCOUT_HOME と .venv のフルパスを自分の配置に書き換える
```

- 毎日 09:00 に新規候補の取り込み・初回送信（`bizscout run`）、10:00 に再送（`bizscout run-resends`）を実行する例。
- `.venv/bin/bizscout` をフルパスで呼ぶため PATH 設定に依存しません。
- 標準出力・エラーは `logs/cron-*.log` に追記します。

---

## 3. systemd（Linux・推奨）

タイマーで毎日実行します。ユーザー単位（`--user`）の例:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/bizscout-*.service deploy/systemd/bizscout-*.timer ~/.config/systemd/user/
#   各ユニットの WorkingDirectory / EnvironmentFile / ExecStart のパスを書き換える

systemctl --user daemon-reload
systemctl --user enable --now bizscout-run.timer
systemctl --user enable --now bizscout-resends.timer

# 手動テスト（タイマーを待たずに1回実行）
systemctl --user start bizscout-run.service
# 状態・ログ
systemctl --user list-timers
journalctl --user -u bizscout-run.service -f
```

- `bizscout-run.timer` … 毎日 09:00、`bizscout-resends.timer` … 毎日 10:00（`OnCalendar` で変更可）。
- `Persistent=true` によりマシン停止で逃した実行を起動後に取り戻します。
- システム全体で動かす場合は `/etc/systemd/system/` に置き、`--user` を外して
  ユニットに `User=`/`Group=` を追加してください。

---

## 4. Windows タスクスケジューラ（注意）

Windows では cron / systemd の代わりにタスクスケジューラを使います。

- **プログラム/スクリプト**: 仮想環境内の実行ファイルをフルパスで指定します。
  ```
  C:\path\to\Bizreach-Auto-Scout-\.venv\Scripts\bizscout.exe
  ```
- **引数**: 例 `run --source bizreach --search-url "https://..."`、再送は `run-resends`。
- **開始（作業フォルダー）**: プロジェクトのルート（`.env` を読み込むため必須）。
  ```
  C:\path\to\Bizreach-Auto-Scout-
  ```
- 環境変数は `.env` から読み込まれます（タスク側で別途設定する必要はありません）。
- ブラウザ自動操作を使うため、初回に `playwright install chromium` を済ませておきます。
- kill switch は同様に `data\state\STOP` ファイルの作成/削除で制御します。
- ログを残すには、引数末尾でリダイレクトせず（タスクスケジューラは `>>` を解釈しないため）、
  `cmd /c "...\bizscout.exe run ... >> logs\run.log 2>&1"` の形で `cmd /c` 経由にするのが簡便です。

---

## トラブルシュート

- 送信が一切行われない → `BIZSCOUT_DRY_RUN=true` か、kill switch（`data/state/STOP`）の存在を確認。
- ログインできない → `BIZREACH_EMAIL` / `BIZREACH_PASSWORD` と
  `data/sessions/` の書き込み権限を確認。
- 画面操作が失敗する → `config/bizreach_selectors.yaml` で実際のセレクタに上書き。
