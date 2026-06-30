"""固定要素（ヘッダー・定型文・署名・フッター・罫線）と本文組み立て。

仕様の本文構成 ①〜⑫ のうち、モデルが生成するのは ④〜⑧ と件名のみ。
①②③（ヘッダー）と ⑨⑩⑪⑫（定型文・署名・フッター）はここで決定的に付与し、
文言の正確性を保証する。
"""

from __future__ import annotations

from pydantic import BaseModel

RULE = "──────────────────"  # 罫線（セクションタイトル・弊社について）
POINT_RULE = "================"  # このスカウトのポイント枠

# ⑨ カジュアル面談の定型文（オンライン実施を明記）
CASUAL_MEETING = (
    "現時点での応募意思は問いません。"
    "まずはカジュアル面談(オンラインで実施)という形で、"
    "ざっくばらんなお話ができれば幸いです。"
    "ぜひ一度お話する機会をいただけませんでしょうか？"
)

# ⑩ タップ案内
TAP_GUIDE = (
    "少しでもご興味頂けましたら、"
    "【まずは話を聞いてみる】をタップした後、送信ボタンを押してください。"
)

# ③ 名前非表示の注記
NAME_DISCLAIMER = (
    "※ビズリーチのシステム上、お名前が表示されないため会員名での表記となっております。"
)

# ① テンプレートではない旨
NOT_TEMPLATE = "【このメッセージはテンプレートではありません】"


class FirstSections(BaseModel):
    """モデルが生成する初回本文の各セクション(④〜⑧)。"""

    greeting_offer: str  # ④
    scout_reason: str  # ⑤
    company_intro: str  # ⑥
    career_title: str  # ⑦タイトル
    career_body: str  # ⑦本文
    position_title: str  # ⑧タイトル
    position_body: str  # ⑧本文


def build_signature(company_cfg: dict) -> str:
    c = company_cfg.get("company", {})
    name = c.get("name", "経営戦略研究所株式会社")
    title = c.get("representative_title", "代表取締役社長")
    rep = c.get("representative", "岩渕")
    return f"{name} {title} {rep}"


def build_footer(company_cfg: dict) -> str:
    """⑫ フッター（初回・再送で同一）。URLは company.yaml の appeals から。"""
    appeals = company_cfg.get("appeals", {})
    atmosphere = appeals.get("atmosphere_url", "https://www.consuldent.jp/recruitment/")
    members = appeals.get("members_url", "https://www.consuldent.jp/members.html")
    return "\n".join(
        [
            POINT_RULE,
            "このスカウトのポイント",
            POINT_RULE,
            "◎1000万円以上の年収を望んでいる方",
            "◎入社後教育研修充実/未経験でもプロのコンサルタントになれる",
            "◎経営にかかわるすべてのコンサルティングを経験できる",
            "",
            RULE,
            "弊社について",
            RULE,
            "私たちは1988年に設立した、医院・病院経営に特化したコンサルティングファームです。",
            "徹底したハンズオン型のコンサルティングにより現場主義と医院に合わせたオーダーメイドの"
            "コンサルティング、半年で医業収入1.5倍を実現。クライアントから高い評価を得ています。",
            "",
            "↓当社の雰囲気について確認する↓",
            atmosphere,
            "",
            "↓当社のメンバー紹介を確認する↓",
            members,
        ]
    )


def _header(member_no: str) -> str:
    # ①から改行なしで②会員番号様を続ける。続けて③の注記。
    return f"{NOT_TEMPLATE}{member_no}様\n\n{NAME_DISCLAIMER}"


def _titled_section(title: str, body: str) -> str:
    return f"{RULE}\n{title.strip()}\n{RULE}\n{body.strip()}"


def assemble_first_body(member_no: str, sec: FirstSections, company_cfg: dict) -> str:
    """初回本文を ①〜⑫ の順で組み立てる。段落間は1行空け。"""
    parts = [
        _header(member_no),
        sec.greeting_offer.strip(),
        sec.scout_reason.strip(),
        sec.company_intro.strip(),
        _titled_section(sec.career_title, sec.career_body),
        _titled_section(sec.position_title, sec.position_body),
        CASUAL_MEETING,
        TAP_GUIDE,
        build_signature(company_cfg),
        build_footer(company_cfg),
    ]
    return "\n\n".join(p for p in parts if p)


def assemble_resend_body(member_no: str, resend_core: str, company_cfg: dict) -> str:
    """再送本文を組み立てる。モデル生成の本文(冒頭で再送に触れる)＋定型尾部。"""
    parts = [
        _header(member_no),
        resend_core.strip(),
        CASUAL_MEETING,
        TAP_GUIDE,
        build_signature(company_cfg),
        build_footer(company_cfg),
    ]
    return "\n\n".join(p for p in parts if p)
