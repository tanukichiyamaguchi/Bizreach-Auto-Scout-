"""職歴の種類による除外判定（現職が個人事業主・契約社員中心・工場/生産部門中心）。

運用方針（2026-10 運用者指示）。通常スカウトとピックアップの両方で除外する
（どちらも pipeline → eligibility.check_eligibility を通るため、ここに足せば両方に効く）。

1. **現職が個人事業主**（フリーランス・自営・業務委託・屋号での開業など）
2. **これまでのキャリアの半分以上が契約社員**（嘱託・有期雇用・期間従業員を含む）
3. **これまでのキャリアの半分以上が工場・生産部門**（製造・生産管理・生産技術・品質管理など）

「キャリアの半分以上」は**在籍期間の合計に占める割合**で判定する（社数ではない）。
社内で役職・部署が変わった人は、その期間（companyCareers）単位で数える。

判定に使うのは**会社名・役職・社内の役職/部署名**だけで、職務内容の自由記述は見ない。
ビズリーチの職務経歴には雇用形態の入力欄が無いとみられ（公開情報の入力項目28件に無い）、
本人が会社名・役職欄に「（契約社員）」「フリーランス」等と書いたものから拾うしかない。
一方、職務内容には「工場向けSaaSの営業」「契約社員の労務管理」「フリーランス向け
サービスの企画」のように同じ語が**仕事の対象**として頻出し、誤除外が避けられないため。
誤検知・取りこぼしの実例は tests/test_career_types.py に固定してある。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Candidate, Employment

# --- 共通 ---------------------------------------------------------------------
# 法人格。会社名にこれがあれば、本人はその法人の役員・社員（個人事業主ではない）。
_CORPORATE_RE = re.compile(
    r"株式会社|有限会社|合同会社|合資会社|合名会社|相互会社|法人|組合|機構|協会|公社|公団"
    r"|（株）|\(株\)|㈱|（有）|\(有\)|㈲|（同）|\(同\)"
    r"|\binc\b|co\.?,?\s*ltd|\bltd\b|\bllc\b|\bcorp(?:oration)?\b|\bgmbh\b|\bk\.?k\.?\b|\bplc\b",
    re.IGNORECASE,
)


def _norm(text: str | None) -> str:
    """判定用の正規化（小文字化・全角英数の半角化）。空白は区切りとして残す。"""
    s = (text or "").strip().lower()
    return s.translate(str.maketrans(
        "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）",
        "0123456789abcdefghijklmnopqrstuvwxyz()",
    ))


def _has_corporate_form(company: str) -> bool:
    return bool(_CORPORATE_RE.search(company))


# --- 1. 個人事業主 --------------------------------------------------------------
# 本人の働き方を表す語。直後が「向け／支援／管理…」なら仕事の対象なので数えない
# （「フリーランス向けサービスの営業」「業務委託管理担当」「個人事業主向け会計ソフト」）。
_SELF_EMPLOYED_RE = re.compile(
    r"個人事業主?(?!向け|様|の(?:顧客|お客様|支援)|支援|対象|専門)"
    r"|フリーランス(?!向け|支援|エージェント|紹介|人材|専門|協会|機構|サービス|の(?:支援|活用))"
    r"|フリー(?:で|として)"
    r"|自営(?!業者向け)"
    r"|屋号"
    r"|個人(?:経営|代理店|商店|で(?:開業|活動|受託))"
    r"|(?:として|独立|個人|・)開業|開業(?:届|$)"
    r"|業務委託(?!管理|契約|先|元|費|料|の|を|に|担当|業務|案件の(?:管理|発注))"
    r"|freelanc|self[\s-]?employed|sole\s*proprietor|independent\s*contractor"
)
# 会社名欄が「個人」「-」だけ、など法人が書かれていない形。
# 「-」「なし」「非公開」は社名を伏せているだけの人もいるので含めない（役職欄の記載で判定する）。
_INDIVIDUAL_COMPANY_RE = re.compile(r"^(?:個人|独立|自営|自営業|個人事業|個人事業主)$")
# 士業・個人店などの屋号。法人格なしで所長・代表・店主なら本人の個人事業。
_PRACTICE_NAME_RE = re.compile(
    r"(?:税理士|会計|公認会計士|行政書士|司法書士|社会保険労務士|社労士|弁理士|土地家屋調査士"
    r"|法律|設計|デザイン)事務所|事務所$|クリニック|医院|治療院|整骨院|接骨院|鍼灸院"
)
_OWNER_TITLE_RE = re.compile(r"所長|代表(?!取締役)|院長|店主|オーナー|経営者|開業")


def _parenthetical(text: str) -> str:
    """括弧書きの中身だけを連結する（「株式会社X（業務委託）」の（業務委託）など）。"""
    return " ".join(re.findall(r"\(([^)]*)\)", text))


def is_self_employed(company: str, title: str = "", employment_type: str = "") -> bool:
    """その職歴が個人事業主（フリーランス・自営・業務委託・個人開業）か。"""
    c, t, et = _norm(company), _norm(title), _norm(employment_type)
    if et and re.search(r"freelanc|self.?employ|sole.?propri|individual|outsourc|業務委託|個人事業|フリーランス|自営", et):
        return True
    corporate = _has_corporate_form(c)
    # 会社名に法人格があるときは、社名の中の語（株式会社フリーランスエージェント等）は数えず、
    # 本人の働き方を書いた括弧書き（株式会社X（業務委託））だけを見る。
    company_part = _parenthetical(c) if corporate else c
    if _SELF_EMPLOYED_RE.search(company_part) or _SELF_EMPLOYED_RE.search(t):
        return True
    if _INDIVIDUAL_COMPANY_RE.match(c.strip()):
        return True    # 会社名が「個人」「自営業」だけ
    # 「山本税理士事務所 所長税理士」のような、法人格の無い士業・個人店の所長・代表・店主。
    return (not corporate and bool(_PRACTICE_NAME_RE.search(c))
            and bool(_OWNER_TITLE_RE.search(t)))


def current_employment(candidate: Candidate) -> Employment | None:
    """現職（在籍中の職歴）。在籍中が無ければ None（離職中）。"""
    for e in candidate.employments:
        if e.is_current:
            return e
    return None


def is_current_self_employed(candidate: Candidate) -> bool:
    """現職が個人事業主か。職歴が取れない取り込み経路では現職の会社名・役職で判定する。"""
    cur = current_employment(candidate)
    if cur is not None:
        return is_self_employed(cur.company, cur.title, cur.employment_type)
    if not candidate.employments and (candidate.current_company or candidate.current_title):
        return is_self_employed(candidate.current_company, candidate.current_title)
    return False


# --- 2. 契約社員 ----------------------------------------------------------------
_CONTRACT_RE = re.compile(
    r"契約社員|契約職員|嘱託|有期(?:雇用|契約)|期間(?:従業員|社員|工)"
    r"|\(契約\)|contract\s*employee|fixed[\s-]?term"
)
# 「契約社員の採用・労務管理」のように仕事の対象として書かれた形。
_CONTRACT_OBJECT_RE = re.compile(r"契約社員(?:の|を|向け)|契約職員(?:の|を)")


def is_contract(text: str, employment_type: str = "") -> bool:
    """その職歴・役職が契約社員（嘱託・有期雇用・期間従業員を含む）か。"""
    et = _norm(employment_type)
    if et and re.search(r"contract|fixed.?term|契約|嘱託|有期", et):
        return True
    s = _norm(text)
    return bool(_CONTRACT_RE.search(s)) and not _CONTRACT_OBJECT_RE.search(s)


# --- 3. 工場・生産部門 ----------------------------------------------------------
# 生産部門であることがはっきりしている語。「生産技術開発」「生産技術（設備設計）」のように
# 開発・設計が付いても生産部門とみなす。
_FACTORY_STRONG_RE = re.compile(
    r"生産(?:管理|技術|計画|部|課|本部|ライン|現場|設備)|製造(?:部|課|技術|ライン|現場|オペ)"
    r"|工場|品質(?:管理|保証)|設備保全|工程(?:管理|改善)|期間(?:従業員|工)"
)
# 生産現場を示すがそれだけでは弱い語。
_FACTORY_WEAK_RE = re.compile(
    r"製造(?!業|小売|原価|販売|物責任)|ライン(?:作業|リーダー|長)?(?=$|\s|・|\)|課|班|担当|作業)"
    r"|組立|組み立て|(?:出荷|製品|外観)?検査(?:員|課|係|工)|製剤|操業|製鋼|製錬|成形|加工(?:課|部|オペ)"
    r"|(?:製造|プラント|印刷|機械|設備|ライン)\S*オペレーター|班長"
)
# 生産側でない職能。これがあれば工場系の語があっても生産部門とはみなさない
# （「製造業向け法人営業」「生産管理システムのコンサル」「品質保証（ソフトウェア）」）。
_NON_FACTORY_ROLE_RE = re.compile(
    r"営業|販売|セールス|経理|財務|会計|人事|採用|広報|マーケ|コンサル"
    r"|カスタマー|サクセス|ソフトウェア|システム|アプリ|web|qa|テスト|向け|顧客|生産性"
    r"|法務|知財|施工|ディレクター|プロデューサー|バイヤー|調達本部|本社"
)
# 弱い語だけのときに打ち消す職能（研究・開発・設計・企画は生産部門ではない）。
_NON_FACTORY_SOFT_RE = re.compile(r"研究|開発|設計|企画")
# 所属が工場そのもの（「株式会社X 豊橋工場」「君津製鉄所」）。社名と空白で区切られた所在地の
# 語として書かれたときだけ数える（「株式会社ことば工場」のような社名の一部は数えない）。
_FACTORY_SITE_RE = re.compile(r"工場$|製鉄所$|製作所\S*工場|製造所$|製油所$|プラント$|セントラルキッチン$")


def _factory_site(company: str) -> bool:
    tokens = [x for x in re.split(r"[\s　/／・]+", _norm(company)) if x]
    if len(tokens) >= 2:
        return any(_FACTORY_SITE_RE.search(tok) and not _CORPORATE_RE.search(tok)
                   for tok in tokens[1:])
    return bool(tokens) and not _has_corporate_form(tokens[0]) and bool(_FACTORY_SITE_RE.search(tokens[0]))


def is_factory_role(text: str) -> bool:
    """役職・部署名が工場・生産部門のものか。"""
    s = _norm(text)
    if _NON_FACTORY_ROLE_RE.search(s):
        return False
    if _FACTORY_STRONG_RE.search(s):
        return True
    return bool(_FACTORY_WEAK_RE.search(s)) and not _NON_FACTORY_SOFT_RE.search(s)


def is_factory_job(company: str, title: str) -> bool:
    """その職歴が工場・生産部門か（所属が工場、または役職が生産部門）。"""
    if _factory_site(company) and "本社" not in _norm(title):
        return True
    return is_factory_role(title)


# --- 期間の割合 -----------------------------------------------------------------

def _share(employments: Iterable[Employment], job_flag, segment_flag) -> tuple[float, float]:
    """(該当期間, 全期間) を年で返す。期間が分からない職歴は分母・分子の両方から除く。"""
    hit = total = 0.0
    for e in employments:
        if e.years is None:
            continue
        total += e.years
        if job_flag(e):
            hit += e.years
            continue
        seg = sum(s.years for s in e.segments if s.years is not None and segment_flag(s.name))
        hit += min(seg, e.years)   # 社内で重なった期間を足しすぎない
    return hit, total


def contract_ratio(candidate: Candidate) -> float | None:
    """キャリア（在籍期間の合計）のうち契約社員だった割合。判断材料が無ければ None。"""
    hit, total = _share(
        candidate.employments,
        lambda e: is_contract(f"{e.company} {e.title}", e.employment_type),
        is_contract,
    )
    return hit / total if total > 0 else None


def factory_ratio(candidate: Candidate) -> float | None:
    """キャリア（在籍期間の合計）のうち工場・生産部門だった割合。判断材料が無ければ None。"""
    hit, total = _share(
        candidate.employments,
        lambda e: is_factory_job(e.company, e.title),
        is_factory_role,
    )
    return hit / total if total > 0 else None
