"""設定の読み込み（環境変数・YAML・コンサルタントデータ）。"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ConsultantProfile

# Opus を使うときは Opus 5 を使う、という運用方針。GitHub の Variables などに
# 旧 Opus のモデル名が残っていても方針が守られるよう、Opus 系の指定は Opus 5 へ
# 読み替える（sonnet / haiku 等の指定はコスト選択なのでそのまま尊重する）。
OPUS_MODEL = "claude-opus-5"


def normalize_model(model: str) -> str:
    """Opus 系のモデル指定を Opus 5 に揃える。それ以外はそのまま返す。"""
    m = (model or "").strip()
    if m.startswith("claude-opus-") and m != OPUS_MODEL:
        return OPUS_MODEL
    return m


def project_root() -> Path:
    """BIZSCOUT_HOME が指定されていればそれを、なければリポジトリルートを返す。"""
    env_home = os.environ.get("BIZSCOUT_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    # このファイル: src/bizreach_scout/config.py → リポジトリルートは2つ上。
    return Path(__file__).resolve().parents[2]


# .env を最初に読み込む（プロセス全体で1回）。
load_dotenv(project_root() / ".env")


class Settings(BaseSettings):
    """環境変数ベースの実行設定。"""

    model_config = SettingsConfigDict(env_prefix="BIZSCOUT_", extra="ignore")

    # Anthropic（API キーは ANTHROPIC_API_KEY を直接参照）
    model: str = OPUS_MODEL
    max_tokens: int = 16000
    # 拡張思考(extended thinking)の有効化フラグ。>0 で有効、0 でオフ。
    # 注: 近年の Opus では {"type":"enabled","budget_tokens":N} は廃止され400になるため、
    #     adaptive thinking を使う。深さは thinking_effort(下記)で制御する。
    #     この値は「思考トークン数」ではなく単なる ON/OFF フラグとして扱う。
    thinking_budget_tokens: int = 8000
    # 思考の深さ/全体のトークン量: low | medium | high | xhigh | max（adaptive時に使用）。
    thinking_effort: str = "medium"

    # 送信制御
    dry_run: bool = True
    # 1実行で送る初回スカウトの上限。**保存検索ごとではなく全保存検索の合計**
    # （4つ登録していても合計でこの件数まで）。再送にも同じ上限が別枠で適用される。
    # ピックアップは無料枠のためこの上限を受けない。
    max_sends_per_run: int = 20
    send_delay_min: float = 20.0
    send_delay_max: float = 60.0
    kill_switch: str = "data/state/STOP"
    # true にすると、状態DB(重複防止)が空＝過去の送信履歴が消えている場合に
    # 実送信を中断する。GitHub Actions の actions/cache 失効でdedupe DBが消え、
    # 全候補者へ再送信してしまう事故を防ぐための安全弁（本番CIでのみ true 推奨）。
    expect_state: bool = False

    # --- 取り込みカーソル（同じ候補者を毎回取り直さないための設定）---
    # 保存検索の結果は上位から並ぶため、常に先頭 max_candidates 件を取ると
    # 「毎日まったく同じ顔ぶれ」を評価し続けることになる（2026-08-14 の本番で、
    # 7/28 に対象外判定した候補者を17日後にそのまま再取得していた）。
    # 直近この日数以内に評価済みの候補者は取り込み時に読み飛ばし、
    # 次ページへカーソルを進めて未評価の候補者を拾う。0 で無効。
    reevaluate_after_days: int = 30
    # 上記の読み飛ばしで検索結果を何ページまで辿るか（1ページ=100件）。
    # 未評価が枯渇したときに無限に巡回しないための上限。
    ingest_max_pages: int = 20

    # 再送までの日数は scout_rules.yaml resend.after_days が単一情報源
    # （config.resend_after_days() 経由で参照する）。

    @field_validator("model")
    @classmethod
    def _opus_means_opus_5(cls, v: str) -> str:
        """Opus を使う指定なら Opus 5 に揃える（旧 Opus の指定が残っていても方針を守る）。"""
        return normalize_model(v)

    # ブラウザ（bot検知対策で実ブラウザのUAに寄せる）
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # パス
    home: str = "."
    db_path: str = "data/bizscout.db"

    # --- スカウト分析（Google Sheets 連携・返信自動検知）---
    # 分析結果を書き込む Google スプレッドシートのID（URLの /d/ と /edit の間の文字列）。
    gsheet_spreadsheet_id: str = ""
    # サービスアカウント鍵JSONのパス（CIでは $RUNNER_TEMP に復号して渡す）。
    gsheet_credentials: str = ""
    # 返信自動チェック: 1回の実行でレジュメを再確認する最大人数。
    # 実データで、返信してもレジュメAPIには氏名が開示されない（=レジュメ経由の返信検知は
    # 効かない）ことを確認済み。返信の権威的な検知は受信箱スキャンで行うため、レジュメ側は
    # 将来仕様変更に備えた軽量なバックストップに留める（実行時間を短く保つ）。
    reply_check_max: int = 60
    # 返信自動チェック: 初回送信から何日以内の候補者を確認対象にするか。
    reply_recent_days: int = 45

    @property
    def anthropic_api_key(self) -> str:
        return os.environ.get("ANTHROPIC_API_KEY", "")

    def resolve(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else project_root() / p

    @property
    def kill_switch_path(self) -> Path:
        return self.resolve(self.kill_switch)

    @property
    def db_file(self) -> Path:
        return self.resolve(self.db_path)


class BizreachCredentials(BaseModel):
    email: str = ""
    password: str = ""
    storage_state: str = "data/sessions/bizreach_state.json"

    @classmethod
    def from_env(cls) -> BizreachCredentials:
        return cls(
            email=os.environ.get("BIZREACH_EMAIL", ""),
            password=os.environ.get("BIZREACH_PASSWORD", ""),
            storage_state=os.environ.get(
                "BIZREACH_STORAGE_STATE", "data/sessions/bizreach_state.json"
            ),
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def company_config() -> dict[str, Any]:
    return _load_yaml(project_root() / "config" / "company.yaml")


@lru_cache(maxsize=1)
def scout_rules() -> dict[str, Any]:
    """scout_rules.yaml を読み、型付きスキーマで検証して返す。

    未知キー（タイポ）や型不正があれば起動時に ValidationError を送出する。
    戻り値は従来どおり dict（呼び出し側の署名変更は不要）。
    """
    from .rules import validate_rules

    return validate_rules(_load_yaml(project_root() / "config" / "scout_rules.yaml"))


def resend_after_days() -> int:
    """再送までの日数の単一情報源（scout_rules.yaml resend.after_days）。"""
    return int(scout_rules().get("resend", {}).get("after_days", 5))


def scout_job_id() -> str | None:
    """スカウト送信に使う求人ID。

    優先順位: 環境変数 BIZSCOUT_SCOUT_JOB_ID > company.yaml の job.scout_job_id。
    保存検索に紐づく求人ではなく、会員種別を問わず送れる求人を指定する。
    """
    env = os.environ.get("BIZSCOUT_SCOUT_JOB_ID")
    if env:
        return env.strip()
    jid = (company_config().get("job", {}) or {}).get("scout_job_id")
    return str(jid).strip() if jid else None


@lru_cache(maxsize=1)
def prompt_template() -> str:
    path = project_root() / "config" / "prompt_template.md"
    return path.read_text(encoding="utf-8")


def consultants_path() -> Path:
    """実データ(consultants.json)があればそれを、なければサンプルを使う。"""
    real = project_root() / "config" / "consultants.json"
    if real.exists():
        return real
    return project_root() / "config" / "consultants.sample.json"


@lru_cache(maxsize=1)
def load_consultants() -> list[ConsultantProfile]:
    path = consultants_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ConsultantProfile(**c) for c in data.get("consultants", [])]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
