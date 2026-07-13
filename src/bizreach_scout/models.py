"""ドメインモデル（候補者・コンサルタント・スカウト文面など）。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Gender(str, Enum):
    male = "male"
    female = "female"
    unknown = "unknown"


class Education(str, Enum):
    """学歴レベル。大小比較のため順序を持つ。"""

    unknown = "unknown"
    high_school = "high_school"
    vocational = "vocational"
    associate = "associate"
    bachelor = "bachelor"
    master = "master"
    doctor = "doctor"

    @property
    def rank(self) -> int:
        order = {
            Education.unknown: -1,
            Education.high_school: 0,
            Education.vocational: 1,
            Education.associate: 2,
            Education.bachelor: 3,
            Education.master: 4,
            Education.doctor: 5,
        }
        return order[self]

    def meets(self, minimum: Education) -> bool:
        """unknown は判定不能として常に False（要確認に倒す）。"""
        if self is Education.unknown:
            return False
        return self.rank >= minimum.rank


class Employment(BaseModel):
    """1社分の在籍履歴。"""

    company: str = ""
    title: str = ""
    years: float | None = None  # 在籍年数
    industry: str = ""


class Candidate(BaseModel):
    """ビズリーチ候補者。名前は非表示のため会員番号で識別する。"""

    member_no: str = Field(..., description="会員番号（例: BU3765516）")
    age: int | None = None
    gender: Gender = Gender.unknown
    education: Education = Education.unknown
    university: str = ""
    # 最終学歴の学校名に日本語表記(ja)が無く英語表記(en)のみの場合 True
    # （海外の教育機関出身の可能性。Bizreach APIのレジュメには学校の所在国フィールドが
    # 無いための代替シグナル。CSV/テキスト取り込みでは判定材料が無いため常に False）。
    overseas_education: bool = False

    current_company: str = ""
    current_title: str = ""
    current_tenure_years: float | None = None  # 現職の在籍年数
    total_experience_years: float | None = None

    industry: str = ""
    job_function: str = ""  # 営業 / コンサル / マーケ など
    prior_companies: list[str] = Field(default_factory=list)
    employments: list[Employment] = Field(default_factory=list)

    salary_current: str = ""
    salary_desired: str = ""
    languages: str = ""
    desired_jobs: str = ""
    desired_industries: str = ""
    work_style: str = ""

    summary: str = ""  # 自己PR・職務要約
    raw_profile: str = ""  # 取得した生テキスト（プロンプトのフォールバックに使用）
    # 外国人判定専用に収集した英語プロフィール・語学欄テキスト（文面生成には使わない）。
    # ja が空で en のみのレジュメでも「英語優勢／外国語ネイティブ」判定が効くようにする。
    foreign_text: str = ""

    source: str = "manual"  # bizreach / csv / text / manual
    profile_url: str = ""
    mrccid: str = ""  # ビズリーチ内部の候補者ID（API・スカウト送信に使用）

    # --- ステータス（ターゲティング用）---
    intention: list[str] = Field(default_factory=list)  # Hot / Will
    resume_updated_status: str = ""  # New(新着) / Updated(更新) / None
    contract_plan: str = ""  # Premium / Free
    candidate_class: str = ""  # HighClass / Talent

    def status_flags(self) -> set[str]:
        """該当するターゲティング区分を返す（new/updated/hot/will/premium）。"""
        flags: set[str] = set()
        if self.resume_updated_status == "New":
            flags.add("new")
        if self.resume_updated_status == "Updated":
            flags.add("updated")
        if "Hot" in self.intention:
            flags.add("hot")
        if "Will" in self.intention:
            flags.add("will")
        if self.contract_plan == "Premium":
            flags.add("premium")
        return flags

    def all_companies(self) -> list[str]:
        names = [self.current_company, *self.prior_companies]
        names += [e.company for e in self.employments]
        return [n for n in names if n]

    def max_single_tenure_years(self) -> float | None:
        values = [
            v
            for v in (
                self.current_tenure_years,
                *[e.years for e in self.employments],
            )
            if v is not None
        ]
        return max(values) if values else None

    def job_change_count(self) -> int:
        """転職回数の推定 = 勤務先数 - 1（0未満は0）。

        current_company / prior_companies / employments を横断して社名を重複排除し
        勤務先数を数える。取り込み経路（API/CSV/テキスト）の差で現職が
        prior_companies に含まれても二重計上しない。
        ※現職が複数（同時在籍）の場合は多めに出る可能性がある（保守的＝除外側に倒れる）。
        """
        names: set[str] = set()
        if self.current_company:
            names.add(self.current_company)
        names.update(n for n in self.prior_companies if n)
        names.update(e.company for e in self.employments if e.company)
        return max(0, len(names) - 1)


class ConsultantProfile(BaseModel):
    """在籍コンサルタント（共通点マッチングの対象）。"""

    id: str
    display_name: str
    former_companies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    universities: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    specialties: list[str] = Field(default_factory=list)
    profile_url: str = ""
    tags: list[str] = Field(default_factory=list)


class ConsultantMatch(BaseModel):
    """候補者と共通点のあるコンサルタント。"""

    consultant: ConsultantProfile
    common_points: list[str] = Field(default_factory=list)
    category: str = "general"  # recruit / insurance / general


class EligibilityResult(BaseModel):
    """対象条件の判定結果。"""

    eligible: bool
    failed: list[str] = Field(default_factory=list)  # 満たさなかった条件の説明

    @property
    def needs_confirmation(self) -> bool:
        return not self.eligible


class ScoutContent(BaseModel):
    """1通分の件名と本文（レンダリング済み）。"""

    subject: str
    body: str


class GeneratedScout(BaseModel):
    """初回・再送の2通セットと内部分析。"""

    member_no: str
    first: ScoutContent
    resend: ScoutContent
    tone_key: str = ""
    matched_consultant_ids: list[str] = Field(default_factory=list)
    analysis: str = ""  # 内部ログ用。メール本文には含めない。
    model: str = ""
    generated_at: datetime | None = None
