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

### 手順2: ログインセッションを取得して secret 化（2段階認証がある場合のみ・推奨）

> **2段階認証を使っていない場合、この手順2は不要です。** ワークフローが手順3の
> メール/パスワードで自動ログインします。手順3へ進んでください。

2段階認証がある場合は、お手元のPCで一度だけログインし、そのセッションを GitHub に登録します。

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
| `BIZREACH_STORAGE_STATE_B64` | 手順2 のセッション（**2段階認証がある場合のみ**。無ければ不要） |

### 手順4: Variables（非機密の設定）を登録
**Variables** タブで以下を登録します（定期実行で使われます）。

| Variable 名 | 例 | 意味 |
|---|---|---|
| `BIZSCOUT_DRY_RUN` | `true` | **まず true。** 本番送信する時だけ `false` |
| `BIZSCOUT_SEARCH_URL` | `https://cr-support.jp/search?saved=...` | 対象候補者の保存検索URL（空なら再送のみ）。**複数指定する場合は半角スペースか `\|` で区切る**（例: `URL1 URL2`）。同じ候補者が複数の検索に出ても重複送信はされません |
| `BIZSCOUT_MAX` | `30` | 1回の最大処理件数（検索スカウト用） |
| `BIZSCOUT_PICKUP_MAX` | `50` | ピックアップの最大処理件数（未設定なら50）。**本日の全ピックアップを開封（既読化）するため、日次の対象件数（通常10〜15件）より十分大きくしておく**。ピックアップ送信は無料枠のため `BIZSCOUT_MAX_SENDS_PER_RUN` の制限を受けず、対象条件を満たす全員へ送信される |
| `BIZSCOUT_MAX_SENDS_PER_RUN` | `20` | 1回の送信上限（検索スカウト・再送用。ピックアップには適用されない） |
| `BIZSCOUT_MODEL` | `claude-opus-4-8` | 生成モデル（任意） |
| `BIZSCOUT_EXPECT_STATE` | `true` | **状態DB消失ガード（推奨）。** 送信履歴DBが空のまま実送信しようとしたら中断する（下記「状態DBが消えたときの復旧」参照）。**初回運用のときだけ `false`（またはVariable未設定）**にし、一度でも送信されたら `true` にする |

---

## 動かす

### 手動で試す（推奨の最初の一歩）
1. GitHub の **Actions** タブ → 左の **scout** → **Run workflow**
2. `dry_run` = `true`、`search_url` に保存検索URL、`max` に件数を入れて実行
3. 実行ログと、末尾の `Status report`（`bizscout report` 相当）で生成内容・状況を確認
4. `Upload logs` の成果物（artifact）で `data/exports/` の文面を確認できます

### 定期実行
`.github/workflows/scout.yml` の `schedule` は毎日 **16:09 JST（主）** と **18:39 JST（予備）** の2回起動します。
定期実行は Variables の値を使うため、`BIZSCOUT_DRY_RUN=true` の間は**送信されません**。

> GitHub の `schedule` は毎時00分が最も混雑し、数時間の遅延やまれにドロップが起こり得るベストエフォート仕様です。
> そのため実行時刻を00分から外し、さらに予備の時刻を用意して「その日ぜんぶ未実行」を防いでいます。
> 実測では設定時刻から1〜2.5時間ほど遅れて発火する傾向があったため、遅くなりすぎないよう
> 当初の19:09/21:39 JSTから3時間前倒しし、体感の到着時刻を17時台後半〜18時台に寄せています。
> 主・予備の両方が走っても、重複防止（SQLite）により二重送信にはなりません（予備は既送信分をスキップします）。
> 秒単位の時刻厳守が必要な場合は、外部スケジューラから `workflow_dispatch` API を叩く方式を検討してください。

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

## 状態DBが消えたときの復旧

重複防止・再送予定は `data/bizscout.db`（SQLite）に保存され、`actions/cache` で実行間に引き継がれます。
キャッシュはベストエフォートのため、**約7日アクセスが無い・容量超過などで消える**ことがあります。
消えたまま実送信すると、全候補者が「初回」と誤判定され**全員へ再送信**してしまいます。

これを防ぐ仕組みと、消えた場合の復旧手順は次のとおりです。

### 自動ガード（`BIZSCOUT_EXPECT_STATE=true`）
Variables に `BIZSCOUT_EXPECT_STATE=true` を設定しておくと、**送信履歴DBが空のまま実送信しようとした瞬間に実行を中断**します（`RuntimeError` で job が赤くなる）。
これにより「キャッシュ消失 → 気づかず全員へ再送信」という事故を未然に止めます。
`dry_run=true` の実行はそもそも送信しないためガード対象外です。**一度でも本番送信を行った後は必ず `true` にしておいてください**（初回運用時のみ `false`）。

### DBスナップショットからの復旧
本ワークフローは毎回の実行で `data/bizscout.db` を `Upload logs` の成果物（artifact, 14日保持）に含めています。
状態が消えた場合は、**直近の正常だった実行の artifact から DB を復元**できます。

1. GitHub の **Actions** タブ →（消失前の）成功した run を開く
2. 下部の成果物（`bizscout-logs-...`）をダウンロードし、中の `data/bizscout.db` を取り出す
3. 復元したDBを状態保存先へ戻す（下記のいずれか）
   - 手早く戻すなら、ローカルにクローンして `data/bizscout.db` を配置し、`bizscout report` で件数が妥当か確認したうえで、キャッシュに載る形（次回実行時に `actions/cache` が拾える状態）で用意する
   - より確実にするなら、次項「より確実な状態保存」への移行を検討する
4. 復元後、`BIZSCOUT_DRY_RUN=true` で1回手動実行し、`Status report` の件数分布が消失前と同傾向であることを確認してから本番送信に戻す

> 復旧に自信が持てないときは、`BIZSCOUT_DRY_RUN=true`（または `BIZSCOUT_EXPECT_STATE=true` のまま）にしておけば実送信は止まります。慌てて `false` に戻さないでください。

## より確実な状態保存（任意）
`actions/cache` ではなく、より確実な状態保存（例: 専用ブランチや外部DBへの保存、暗号化したうえでのコミット）に切り替えられます。ご希望があれば、運用に合わせて実装します。
