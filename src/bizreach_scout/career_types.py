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
# 法人格。会社名の本体（括弧の外）にこれがあれば、本人はその法人の役員・社員。
_CORPORATE_RE = re.compile(
    r"株式会社|有限会社|合同会社|合資会社|合名会社|相互会社|法人|組合|機構|協会|公社|公団"
    r"|\(株\)|㈱|\(有\)|㈲|\(同\)"
    r"|\binc\b|co\.?,?\s*ltd|\bltd\b|\bllc\b|\bcorp(?:oration)?\b|\bgmbh\b|\bk\.?k\.?\b|\bplc\b",
    re.IGNORECASE,
)
# 括弧の種類をそろえる（【業務委託】［契約］「工場」も丸括弧として扱う）。
_TRANS = str.maketrans(
    "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）【】［］〔〕「」",
    "0123456789abcdefghijklmnopqrstuvwxyz()()()()()",
)


def _norm(text: str | None) -> str:
    """判定用の正規化（小文字化・全角英数の半角化・括弧の統一）。空白は区切りとして残す。"""
    return (text or "").strip().lower().translate(_TRANS).replace("[", "(").replace("]", ")")


def _split_paren(text: str) -> tuple[str, list[str]]:
    """括弧の外（本体）と、括弧の中身のリストに分ける。"""
    inner = re.findall(r"\(([^()]*)\)", text)
    return re.sub(r"\([^()]*\)", " ", text), inner


def _has_corporate_form(company: str) -> bool:
    body, _ = _split_paren(_norm(company))
    return bool(_CORPORATE_RE.search(body))


# 過去の働き方を書いただけの付記（「業務委託から正社員登用」「個人事業から法人化」）。
_TRANSITION_RE = re.compile(
    r"(?:から|を経て|より|→|⇒).{0,12}?(?:正社員|入社|登用|転換|法人化|法人成り|創業|設立)"
    r"|法人化|法人成り|法人設立|会社設立"   # 個人事業から法人にした人は、今は法人の経営者
)


# --- 1. 個人事業主 --------------------------------------------------------------
# 本人の働き方を表す語。
_SE_WORD = (
    r"個人事業主?|フリーラン(?:ス|サー)|フリー(?:ライター|カメラマン|アナウンサー|エンジニア"
    r"|デザイナー|編集者|翻訳者|ランサー|プログラマー|ディレクター|コンサルタント)"
    r"|自営業?|業務委託(?:契約)?|屋号|個人(?:経営|代理店)|開業医"
    r"|freelancer?|self[\s-]?employed|sole\s*proprietor|independent\s*contractor"
)
# 働き方の語のすぐ後に仕事の対象・組織を表す語が続く形（顧客層・事業名・担当業務）。
_SE_OBJECT_AFTER = (
    r"(?:向け|様|担当|支援|対象|専門|協会|機構|エージェント|紹介|人材|サービス|活用|管理"
    r"|派遣|本部|事業部|事業|部門|グループ|チーム|領域|市場|案件|店|・|の|を|に(?!て)|へ|との|等"
    r"|platform|向|sales|営業)"
)
_SE_RE = re.compile(rf"(?:{_SE_WORD})(?!{_SE_OBJECT_AFTER})")
# 会社名欄・括弧書きが働き方の語だけでできている形（「（業務委託）」「（フリーランス）」）。
_SE_ONLY_RE = re.compile(
    rf"^\s*(?:雇用形態|契約形態|就業形態|働き方)?[:：]?\s*(?:{_SE_WORD})\s*(?:として|にて|で)?\s*$"
)
# 役職欄で本人の働き方として言い切っている形（「フリーランスとして参画」「業務委託にて」）。
_SE_AS_RE = re.compile(rf"(?:{_SE_WORD})(?:として|にて|で(?:活動|稼働|参画|従事|就業|受託))")
# 役職欄の先頭に働き方の語があり、その後が職種名だけの形（「フリーランス翻訳者」
# 「Freelance Designer」）。後ろに顧客・事業・担当を表す語があれば職種ではない
# （「個人経営店向けPOS営業」「フリーランスエンジニアのキャリアアドバイザー」）。
_SE_LEADING_RE = re.compile(rf"^\s*({_SE_WORD})\s*(.{{0,16}})$")
_SE_REST_GUARD_RE = re.compile(
    r"の|・|向け|担当|営業|事業|支援|管理|人材|サービス|活用|紹介|派遣|platform|sales|business"
)
# 会社名欄に法人が書かれていない形（「独立・開業」「個人」）。
_SE_COMPANY_ONLY_RE = re.compile(r"^(?:個人|独立|独立・?開業|開業|自営|自営業|個人事業主?)$")
_OPENING_RE = re.compile(r"(?:として|独立|個人|・)開業|^開業(?:届)?$|開業届")
# 法人格の無い士業・医院・個人店（駐在員事務所・支社・営業所は会社の拠点なので除く）。
_PRACTICE_RE = re.compile(
    r"(?:税理士|会計|公認会計士|行政書士|司法書士|社会保険労務士|社労士|弁理士|土地家屋調査士"
    r"|法律|特許|設計|デザイン)事務所$"
    r"|クリニック$|医院$|歯科$|内科$|外科$|眼科$|皮膚科$|小児科$|耳鼻(?:咽喉)?科$|整形外科$"
    r"|治療院$|整骨院$|接骨院$|鍼灸院$"
)
# 所長・院長などは役職全体がその語のときだけ（副院長・院長補佐・所長代理は勤務している職員）。
_OWNER_TITLE_RE = re.compile(
    r"^(?:代表者?|所長|院長|店主|オーナー|経営者|開業医)"
    r"(?:税理士|行政書士|司法書士|弁護士|公認会計士|社労士|社会保険労務士|弁理士|医師|歯科医師|獣医師)?$"
    r"|^(?:税理士|行政書士|司法書士|弁護士|公認会計士|社労士|社会保険労務士|弁理士|医師|歯科医師)"
    r"(?:・|\s)?(?:代表|所長|院長)$"
)


def _self_employed_text(text: str) -> bool:
    """本人の働き方として個人事業主の語が出ているか（過去の経緯の付記は数えない）。"""
    return bool(_SE_RE.search(text)) and not _TRANSITION_RE.search(text)


def is_self_employed(company: str, title: str = "", employment_type: str = "") -> bool:
    """その職歴が個人事業主（フリーランス・自営・業務委託・個人開業）か。"""
    c, t, et = _norm(company), _norm(title), _norm(employment_type)
    if et and re.search(r"freelanc|self.?employ|sole.?propri|individual|outsourc|業務委託|個人事業|フリーランス|自営", et):
        return True
    if _TRANSITION_RE.search(c) or _TRANSITION_RE.search(t):
        return False   # 「業務委託から正社員登用」「個人事業から法人化」は今の働き方ではない
    body, parens = _split_paren(c)
    # 会社名欄のうち「/」「／」で区切った後ろも、括弧書きと同じく働き方の付記として扱う。
    head, *tails = re.split(r"\s*[/／]\s*", body.strip())
    notes = parens + tails
    if _CORPORATE_RE.search(head):
        # 法人の会社名（「株式会社フリーランスエージェント」）の中の語は数えず、
        # 本人の働き方を書いた付記（「株式会社X（業務委託）」）だけを見る。
        company_hit = any(_SE_ONLY_RE.match(n) for n in notes)
    else:
        company_hit = (bool(_SE_COMPANY_ONLY_RE.match(head.strip()))
                       or _self_employed_text(c) or bool(_OPENING_RE.search(c)))
    if company_hit:
        return True
    # 役職欄の括弧書きは末尾にあるときだけ本人の働き方として読む（「マーケター（業務委託）」）。
    # 途中の括弧は前の語の補足（「BPO（業務委託）事業部 営業」の業務委託は事業名）。
    trailing = re.search(r"\(([^()]*)\)\s*$", t)
    if trailing and _SE_ONLY_RE.match(trailing.group(1)):
        return True
    t_body, _ = _split_paren(t)
    if _SE_AS_RE.search(t) and not _TRANSITION_RE.search(t):
        return True
    lead = _SE_LEADING_RE.match(t_body.strip())
    if lead and not _SE_REST_GUARD_RE.search(lead.group(2)):
        return True
    # 「山本税理士事務所 所長税理士」のような、法人格の無い士業・医院・個人店の所長・院長・店主。
    return (not _CORPORATE_RE.search(c) and bool(_PRACTICE_RE.search(head.strip()))
            and bool(_OWNER_TITLE_RE.match(t.strip())))


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
_CONTRACT_WORD = (
    r"契約(?:社員|職員|スタッフ|雇用)|嘱託|有期(?:雇用|契約|社員)|期間(?:従業員|社員|工|契約)"
    r"|会計年度任用|臨時(?:的任用)?職員|任期付き?|contract\s*(?:employee|staff|worker|basis)"
)
# 「社内SE（契約）」「営業／契約」「（契約・1年更新）」のように区切られた単独の「契約」も拾う。
# 「Contract Manager」「契約営業」「契約管理部」は職務なので、後ろに語が続く形は拾わない。
_CONTRACT_RE = re.compile(
    rf"{_CONTRACT_WORD}|(?:^|[(／/・\s])(?:契約|contract)(?=[)・／/、,]|\s*$)"
)
# 「契約社員の労務管理」「期間従業員採用担当」のように仕事の対象として書かれた形。
_CONTRACT_OBJECT_AFTER = re.compile(
    r"^(?:・(?:パート|アルバイト|派遣)(?:社員)?)*(?:の|を|向け|採用|管理|制度|労務|対応|担当|教育|研修|活用)"
)
# 契約社員から正社員になった人（「正社員登用制度あり」はまだ登用されていないので含めない）。
_CONVERTED_RE = re.compile(
    r"正社員(?:に|へ)?(?:登用(?!制度)|転換)|(?:→|⇒)\s*正社員|無期転換|正社員(?:として|に)(?:入社|採用)"
)


def is_contract(text: str, employment_type: str = "") -> bool:
    """その職歴・役職が契約社員（嘱託・有期雇用・期間従業員を含む）か。"""
    et = _norm(employment_type)
    if et and re.search(r"contract|fixed.?term|契約|嘱託|有期", et):
        return True
    s = _norm(text)
    if _CONVERTED_RE.search(s):
        return False
    return any(not _CONTRACT_OBJECT_AFTER.match(s[m.end():]) for m in _CONTRACT_RE.finditer(s))


# --- 3. 工場・生産部門 ----------------------------------------------------------
# 生産部門であることがはっきりしている語（開発・設計が付いても生産部門とみなす）。
_FACTORY_STRONG_RE = re.compile(
    r"生産(?:管理|技術|計画|部|課|本部|ライン|現場|設備|職|スタッフ|オペレーター|工程|グループ"
    r"|g(?![a-z])|チーム|事業部|統括|センター|準備|部門)"
    r"|製造(?:部|課|技術|ライン|現場|オペ|職|スタッフ|グループ|g(?![a-z])|チーム|部門|本部|工程)"
    r"|(?<!スマート)(?<!デジタル)工場|設備保全|期間(?:従業員|工)|生管|生技"
    r"|manufactur|plant\s*manager|production\s*(?:control|planning|engineer|manager|supervisor|line|operator)"
)
# 製造業の中なら生産部門だが、業種によってはそうでない語（病院の検査課、SaaSの品質保証）。
_FACTORY_CONTEXT_RE = re.compile(
    r"品質(?:管理|保証)|品管|品証|工程(?:管理|改善)|(?:出荷|製品|外観|品質)?検査(?:員|課|係|工)"
    r"|quality\s*control"
)
_MANUFACTURING_CUE_RE = re.compile(
    r"工場|製造|生産|製作所|工業|化学|電機|電子|電工|製薬|製鉄|鉄鋼|金属|精機|機械|機工|重工"
    r"|食品|製菓|飲料|自動車|部品|樹脂|ゴム|繊維|製紙|印刷|窯業|ガラス|セメント|メーカー"
    r"|manufactur|plant|factory|industr|chemical|motor|electric"
)
# 生産現場を示すがそれだけでは弱い語。
_FACTORY_WEAK_RE = re.compile(
    r"製造(?!業|小売|原価|販売|物責任|領域|メーカー|業界|派遣|it|dx|向け)"
    r"|ライン(?:作業|リーダー|長)?(?=$|\s|・|\)|課|班|担当|作業)"
    r"|組立|組み立て|製剤|操業|製鋼|製錬|成形|加工(?:課|部|オペ)|工員|技能(?:職|員)|機械加工"
    r"|(?<![a-z])c?nc(?![a-z])|旋盤|溶接工|塗装工|プレス工|鋳造|鍛造"
    r"|(?:製造|プラント|印刷|機械|設備|ライン|マシン|装置|生産)\S*オペレーター|班長"
)
# 生産側でない職能（括弧の外＝役職の本体にあるときだけ打ち消す。
# 括弧の中の補足「（顧客対応も担当）」では打ち消さない）。
_NON_FACTORY_ROLE_RE = re.compile(
    r"営業|販売|セールス|経理|財務|会計|人事|採用|広報|マーケ|コンサル"
    r"|カスタマー|サクセス|ソフトウェア|システム|アプリ|web|qa|テスト|向け|顧客|生産性"
    r"|法務|知財|施工|ディレクター|プロデューサー|バイヤー|調達本部|本社(?!工場)"
    r"|アドバイザー|コーディネーター|(?<![a-z])(?:ra|ca|dx|it)(?![a-z])|saas|sales|marketing|consult"
)
# 括弧の中でも打ち消す、IT を表す補足（「品質保証（ソフトウェア）」）。
_IT_NOTE_RE = re.compile(r"ソフトウェア|システム|アプリ|web|saas|(?<![a-z])it(?![a-z])|テスト|qa")
# 弱い語だけのときに打ち消す職能（研究・開発・設計・企画は生産部門ではない）。
_NON_FACTORY_SOFT_RE = re.compile(r"研究|開発|設計|企画")
# 所属が工場そのもの（「株式会社X 豊橋工場」「株式会社X（元町工場）」「君津製鉄所」）。
_FACTORY_SITE_RE = re.compile(
    r"(?<!スマート)(?<!デジタル)工場$|製鉄所$|製作所\S*工場$|製造所$|製油所$|プラント$|セントラルキッチン$"
)
_CORPORATE_ONLY_RE = re.compile(r"^(?:株式会社|有限会社|合同会社|合資会社|合名会社|\(株\)|㈱|\(有\)|㈲)$")


def _factory_site(company: str) -> bool:
    """会社名欄に、社名とは別に工場などの所在地が書かれているか。"""
    tokens = [x for x in re.split(r"[\s　/／・()]+", _norm(company)) if x]
    # 「株式会社 ことば工場」のように法人格だけの語は次の語と合わせて社名とする。
    while len(tokens) >= 2 and _CORPORATE_ONLY_RE.match(tokens[0]):
        tokens = [tokens[0] + tokens[1], *tokens[2:]]
    if len(tokens) >= 2:
        return any(_FACTORY_SITE_RE.search(tok) and not _CORPORATE_RE.search(tok)
                   for tok in tokens[1:])
    return bool(tokens) and not _CORPORATE_RE.search(tokens[0]) and bool(_FACTORY_SITE_RE.search(tokens[0]))


def is_factory_role(text: str, company: str = "") -> bool:
    """役職・部署名が工場・生産部門のものか（company は業種の手がかりとして使う）。"""
    s = _norm(text)
    body, parens = _split_paren(s)
    if _NON_FACTORY_ROLE_RE.search(body) or any(_IT_NOTE_RE.search(p) for p in parens):
        return False
    cue = _MANUFACTURING_CUE_RE.search(f"{_norm(company)} {s}")
    soft = _NON_FACTORY_SOFT_RE.search(s)
    for i, seg in enumerate([body, *parens]):
        # 括弧の中に出た語は、その括弧の中の文脈で判断する（「製品設計（工場向け）」の工場は対象）。
        if i > 0 and _NON_FACTORY_ROLE_RE.search(seg):
            continue
        if _FACTORY_STRONG_RE.search(seg):
            return True
        if _FACTORY_CONTEXT_RE.search(seg) and cue:
            return True
        if _FACTORY_WEAK_RE.search(seg) and not soft:
            return True
    return False


def is_factory_job(company: str, title: str) -> bool:
    """その職歴が工場・生産部門か（所属が工場、または役職が生産部門）。"""
    t = _norm(title)
    if _factory_site(company) and not re.search(r"本社(?!工場)", t):
        return True
    return is_factory_role(title, company)


# --- 期間の割合 -----------------------------------------------------------------

def _months(months: int | None, years: float | None) -> int | None:
    """月数（DBに保存済みの古い職歴は月数が無いので年数から戻す）。"""
    if months is not None:
        return months
    return None if years is None else round(years * 12)


def _share(employments: Iterable[Employment], job_flag, segment_flag) -> tuple[int, int]:
    """(該当の月数, 全体の月数)。期間が分からない職歴は分母・分子の両方から除く。"""
    hit = total = 0
    for e in employments:
        m = _months(e.months, e.years)
        if m is None:
            continue
        total += m
        if job_flag(e):
            hit += m
            continue
        seg = sum(sm for s in e.segments
                  if (sm := _months(s.months, s.years)) is not None and segment_flag(e, s.name))
        hit += min(seg, m)   # 社内で重なった期間を足しすぎない
    return hit, total


def contract_ratio(candidate: Candidate) -> float | None:
    """キャリア（在籍期間の合計）のうち契約社員だった割合。判断材料が無ければ None。"""
    hit, total = _share(
        candidate.employments,
        lambda e: is_contract(f"{e.company} {e.title}", e.employment_type),
        lambda e, name: is_contract(name),
    )
    return hit / total if total > 0 else None


def factory_ratio(candidate: Candidate) -> float | None:
    """キャリア（在籍期間の合計）のうち工場・生産部門だった割合。判断材料が無ければ None。"""
    hit, total = _share(
        candidate.employments,
        lambda e: is_factory_job(e.company, e.title),
        lambda e, name: is_factory_role(name, e.company),
    )
    return hit / total if total > 0 else None
