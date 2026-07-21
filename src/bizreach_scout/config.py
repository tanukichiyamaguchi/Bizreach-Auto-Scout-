"""設定の読み込み（環境変数・YAML・コンサルタントデータ）。"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ConsultantProfile


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
    model: str = "claude-opus-4-8"
    max_tokens: int = 16000
    # 拡張思考(extended thinking)の有効化フラグ。>0 で有効、0 でオフ。
    # 注: Opus 4.8/4.7 では {"type":"enabled","budget_tokens":N} は廃止され400になるため、
    #     adaptive thinking を使う。深さは thinking_effort(下記)で制御する。
    #     この値は「思考トークン数」ではなく単なる ON/OFF フラグとして扱う。
    thinking_budget_tokens: int = 8000
    # 思考の深さ/全体のトークン量: low | medium | high | xhigh | max（adaptive時に使用）。
    thinking_effort: str = "medium"

    # 送信制御
    dry_run: bool = True
    max_sends_per_run: int = 20
    send_delay_min: float = 20.0
    send_delay_max: float = 60.0
    kill_switch: str = "data/state/STOP"
    # true にすると、状態DB(重複防止)が空＝過去の送信履歴が消えている場合に
    # 実送信を中断する。GitHub Actions の actions/cache 失効でdedupe DBが消え、
    # 全候補者へ再送信してしまう事故を防ぐための安全弁（本番CIでのみ true 推奨）。
    expect_state: bool = False

    # 再送までの日数は scout_rules.yaml resend.after_days が単一情報源
    # （config.resend_after_days() 経由で参照する）。

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
    # 返信自動チェック: 1回の実行で確認する最大人数（レジュメ再取得の負荷制御）。
    # recent_days の窓内の送信済み全員を1回で確認できるよう十分大きく取る
    # （氏名開示＝返信のシグナルは、その候補者を確認した時だけ拾えるため）。
    reply_check_max: int = 400
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
