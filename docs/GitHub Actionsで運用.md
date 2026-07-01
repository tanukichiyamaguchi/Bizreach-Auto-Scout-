# GitHub Actions で自動運用する

サーバーを用意せず、**GitHub Actions** をスケジューラ兼実行環境として完全自動運用する手順です。
ワークフローは `.github/workflows/scout.yml` です。

> まず必ず `dry_run=true`（実送信しない）で動作を確認してから本番に切り替えてください。

---

## ⚠️ 先に知っておくべき2つの注意点

GitHub Actions のランナーは**毎回まっさらな使い捨て環境**です。そのため次の2点に注意します。

1. **状態（送信履歴・再送予定・ログインセッション）の保持**
   重複送信の防止と再送スケジュールは `data/` 配下の SQLite DB に保存されます。本ワークフローは `actions/cache` で `data/` を実行間に引き継ぎますが、**キャッシュは最善努力**（約7日アクセスが無い／容量超過で消える）です。万一消えると重複判定がリセットされ、**同じ候補者へ再送信するリスク**があります。毎日実行していれば消えにくいですが、心配な場合はより確実な保存先（後述）をご相談ください。

2. **ログイン（2FA・bot検知）**
   GitHub のデータセンターIPからの自動ログインは、ビズリーチの2段階認証やbot検知で失敗しやすいです。これを避けるため、**ローカルで一度ログインしたセッションを secret として持ち込む**方式を推奨します（下記 手順2）。それでも新しいIPからのアクセスは制限される場合があります。

---

## セットアップ手順

### 手順1: セレクタを設定してコミット
ビズリーチの実画面に合わせたセレクタを用意します（[セレクタ設定ガイド.md](セレクタ設定ガイド.md) 参照）。

```bash
cp config/bizreach_selectors.example.yaml config/bizreach_selectors.yaml
# 実画面に合わせて編集し、コミット（セレクタは機密ではないのでコミットしてOK）
git add config/bizreach_selectors.yaml && git commit -m "ビズリーチ セレクタ設定" && git push
```

### 手順2: ログインセッションを取得して secret 化（推奨）
お手元のPCで一度だけログインし、そのセッションを GitHub に登録します。

```bash
pip install -e . && playwright install chromium
bizscout login            # ブラウザが開くのでログイン（2FAも手動入力）
# → data/sessions/bizreach_state.json が保存される
base64 -w0 data/sessions/bizreach_state.json   # 出力された文字列をコピー
```
コピーした文字列を、GitHub の **Settings → Secrets and variables → Actions → New repository secret** で
`BIZREACH_STORAGE_STATE_B64` という名前で登録します。

### 手順3: Secrets（機密情報）を登録
同じ画面で以下を登録します。

| Secret 名 | 内容 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude APIキー |
| `BIZREACH_EMAIL` | ビズリーチ ログインメール（セッション失効時の再ログイン用） |
| `BIZREACH_PASSWORD` | ビズリーチ パスワード |
| `BIZREACH_STORAGE_STATE_B64` | 手順2 のセッション（推奨） |

### 手順4: Variables（非機密の設定）を登録
**Variables** タブで以下を登録します（定期実行で使われます）。

| Variable 名 | 例 | 意味 |
|---|---|---|
| `BIZSCOUT_DRY_RUN` | `true` | **まず true。** 本番送信する時だけ `false` |
| `BIZSCOUT_SEARCH_URL` | `https://cr-support.jp/search?saved=...` | 対象候補者の保存検索URL（空なら再送のみ）。**複数指定する場合は半角スペースか `\|` で区切る**（例: `URL1 URL2`）。同じ候補者が複数の検索に出ても重複送信はされません |
| `BIZSCOUT_MAX` | `30` | 1回の最大処理件数 |
| `BIZSCOUT_MAX_SENDS_PER_RUN` | `20` | 1回の送信上限 |
| `BIZSCOUT_MODEL` | `claude-opus-4-8` | 生成モデル（任意） |

---

## 動かす

### 手動で試す（推奨の最初の一歩）
1. GitHub の **Actions** タブ → 左の **scout** → **Run workflow**
2. `dry_run` = `true`、`search_url` に保存検索URL、`max` に件数を入れて実行
3. 実行ログと、末尾の `Status report`（`bizscout report` 相当）で生成内容・状況を確認
4. `Upload logs` の成果物（artifact）で `data/exports/` の文面を確認できます

### 定期実行
`.github/workflows/scout.yml` の `schedule` は毎日 09:00 JST に動きます。
定期実行は Variables の値を使うため、`BIZSCOUT_DRY_RUN=true` の間は**送信されません**。

### 本番送信に切り替える
1. 手動実行＋`dry_run=true` で文面と挙動に納得できたら
2. Variables の `BIZSCOUT_DRY_RUN` を `false` に変更
3. 以降の定期実行（または `dry_run=false` の手動実行）で実送信されます

---

## 止め方（緊急停止）
- いますぐ止める: 実行中なら Actions 画面で該当 run を **Cancel**
- 当面止める: Variables の `BIZSCOUT_DRY_RUN` を `true` に戻す（送信されなくなる）
- 完全に止める: Actions タブで **scout** ワークフローを **Disable**

---

## 二重送信を確実に防ぎたい場合（任意）
`actions/cache` ではなく、より確実な状態保存（例: 専用ブランチや外部DBへの保存、暗号化したうえでのコミット）に切り替えられます。ご希望があれば、運用に合わせて実装します。
