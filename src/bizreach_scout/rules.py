"""scout_rules.yaml の型付きスキーマ（バリデーション用）。

目的:
- **タイポの即死化**: すべてのモデルに ``extra="forbid"`` を付け、YAML に未知の
  キー（例: ``min_ages:`` のような綴り間違い）があれば起動時に即エラーにする。
  従来は ``cfg.get(key, default)`` が黙ってデフォルトに戻り、条件が無言で消えていた。
- **型の検証**: 数値であるべき所に文字列が入っていれば起動時に検出する。
- **デフォルトの単一情報源**: 各値の既定をここに集約する。

``config.scout_rules()`` は raw YAML をこのモデルで検証し、``model_dump(exclude_none=True)``
を返す。既存の呼び出し側（eligibility.py など）は従来どおり dict を受け取るため署名変更は不要。
現行 scout_rules.yaml に対して dump 結果が raw と完全一致することをテストで保証している
（振る舞い不変）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_Forbid = ConfigDict(extra="forbid")


class JobChangeBracket(BaseModel):
    """年代別の転職回数上限（この回数「以上」で対象外）。"""

    model_config = _Forbid
    age_min: int | None = None
    age_max: int | None = None
    count: int


class EligibilityRules(BaseModel):
    model_config = _Forbid
    min_age: int = 27
    max_age: int | None = 42
    min_same_company_years: float = 2.5
    required_gender: str = "male"
    required_education: str = "bachelor"
    exclude_overseas_education: bool = True
    exclude_japanese_language_school: bool = True
    exclude_non_japanese_native: bool = True
    exclude_english_resume: bool = True
    exclude_residency_status_mention: bool = True
    exclude_overseas_residence: bool = True
    # None/未設定なら直近転職チェック無効（"絶対値が入っていれば有効"の従来意味を保つ）。
    min_current_tenure_years: int | None = 1
    job_changes_exclude: list[JobChangeBracket] = Field(default_factory=list)
    require_any_status: list[str] = Field(default_factory=list)
    on_ineligible: str = "skip"


class ResendRules(BaseModel):
    model_config = _Forbid
    after_days: int = 5
    length_ratio: float = 0.5
    max_consultant_mentions: int = 1
    use_native_reminder: bool = True


class Constraints(BaseModel):
    model_config = _Forbid
    subject_prefix: str = "【Premium Offer】"
    resend_subject_prefix: str = "【どうしても諦めきれず２度目のご連絡です。】"
    forbid_strings: list[str] = Field(default_factory=list)
    max_exclamations: int = 2
    forbid_emoji: bool = True
    forbid_phrase: list[str] = Field(default_factory=list)
    allowed_url_domains: list[str] = Field(default_factory=list)
    # 本文の最大文字数（媒体側の制限。超過すると送信APIが400を返す）。0で無効。
    max_body_chars: int = 3000


class MatchingRules(BaseModel):
    model_config = _Forbid
    recruit_keywords: list[str] = Field(default_factory=list)
    insurance_keywords: list[str] = Field(default_factory=list)
    match_fields: list[str] = Field(default_factory=list)
    max_intro_consultants: int = 3
    # 全メールに載せるコンサルタント紹介の下限（保証人数）。共通点が無くても近い経歴/
    # フォールバックで最低この人数を必ず紹介する。max_intro_consultants=0 のときのみ無効。
    min_intro_consultants: int = 1
    # 紹介対象から除外するコンサルタントID（署名者本人など）。
    exclude_consultant_ids: list[str] = Field(default_factory=list)
    # 同上を氏名（部分一致）でも指定できる保険（import で id が振り直されても効く）。
    exclude_consultant_names: list[str] = Field(default_factory=list)
    # 「前職企業の共通点」の根拠にしない一般名（業種の総称）。誤マッチ由来の嘘を防ぐ。
    generic_company_terms: list[str] = Field(default_factory=list)
    # 「職種・役割の共通点」の根拠にしない語（役職名など）。
    role_stopwords: list[str] = Field(default_factory=list)


class ToneMatch(BaseModel):
    model_config = _Forbid
    age_min: int | None = None
    age_max: int | None = None
    experience_max: int | None = None
    job_functions: list[str] | None = None


class ToneProfile(BaseModel):
    model_config = _Forbid
    key: str
    label: str = ""
    match: ToneMatch = Field(default_factory=ToneMatch)
    tone: str = ""
    length: str = ""
    focus: str = ""


class ScheduleRules(BaseModel):
    """送信曜日の運用ルール（通常スカウトのみ対象。ピックアップは毎日送る）。"""

    model_config = _Forbid
    # 通常スカウトを送らない曜日（1=月 … 6=土, 7=日）。
    search_skip_weekdays: list[int] = Field(default_factory=list)
    # 日本の祝日も通常スカウトを送らない。
    search_skip_holidays: bool = False
    # 祝日の前日も通常スカウトを送らない（翌日が休みだと読まれずに埋もれるため）。
    search_skip_holiday_eve: bool = False
    # 追加の休止日（YYYY-MM-DD）。
    search_skip_dates: list[str] = Field(default_factory=list)


class ScoutRules(BaseModel):
    model_config = _Forbid
    eligibility: EligibilityRules = Field(default_factory=EligibilityRules)
    tone_profiles: list[ToneProfile] = Field(default_factory=list)
    resend: ResendRules = Field(default_factory=ResendRules)
    constraints: Constraints = Field(default_factory=Constraints)
    matching: MatchingRules = Field(default_factory=MatchingRules)
    schedule: ScheduleRules = Field(default_factory=ScheduleRules)


def validate_rules(raw: dict) -> dict:
    """raw dict を検証し、正規化した dict を返す（未知キー/型不正は ValidationError）。

    exclude_none=True で「未設定の任意フィールド」を落とし、現行の
    「キーが無ければ cfg.get のデフォルトに委ねる」挙動と一致させる。
    """
    return ScoutRules.model_validate(raw).model_dump(exclude_none=True)
