# =============================================================
#  Bizreach Auto Scout — 本番コンテナイメージ
# -------------------------------------------------------------
#  ベースは Playwright 公式の Python イメージ。
#  Chromium 等のブラウザ本体と OS 依存ライブラリが同梱されており、
#  `playwright install` を別途実行しなくてもブラウザ自動操作が動く。
#  バージョンは pyproject の playwright>=1.40.0 に合わせて 1.40.0 を採用。
# =============================================================
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Python のバッファリングを無効化し、ログを即時にコンテナ標準出力へ流す。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # 既定はドライラン（実送信しない）。本番送信は実行時に上書きすること。
    BIZSCOUT_DRY_RUN=true

# 作業ディレクトリ。data/ と config/ は実行時にボリュームでマウントする想定。
WORKDIR /app

# 依存解決の安定化のためにメタデータ類を先にコピーしてから本体をコピーする。
# （依存だけが変わったときにレイヤキャッシュを活かしやすくする）
COPY pyproject.toml README.md ./

# リポジトリ全体をコピー（.dockerignore で .venv / .git / data の DB 等は除外済み）。
COPY . .

# 編集可能インストール。pyproject の [project.scripts] により `bizscout` が PATH に入る。
RUN pip install --no-cache-dir -e .

# data/ は実行時にボリュームとしてマウントする（DB・セッション・kill switch の永続化先）。
# config/ も上書き用セレクタ等を反映するためマウント想定（compose 側で定義）。
VOLUME ["/app/data"]

# `bizscout` を起点にする。サブコマンド（run / run-resends / serve など）は
# docker run / compose の command 側で渡す。
ENTRYPOINT ["bizscout"]

# 既定の引数。compose 側で上書きする想定。
# まずは安全側に倒し、起動前チェックではなく状況レポートを既定にしておく。
CMD ["report"]
