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

**迷ったら除外しない側に倒す。** 役職欄は本人の働き方だけでなく、担当する顧客層
（「個人事業主セールス」「開業医MR」）・業界（「アカウントエグゼクティブ（製造）」）・
製品（「半導体製造装置 サービスエンジニア」）・管理する相手（「期間従業員の労務管理」）を
書く場所でもある。語が含まれるだけでは数えず、本人の働き方・所属として書かれた形
（括弧書きの付記・職種名そのもの・部署名）のときだけ数える。
誤検知・取りこぼしの実例は tests/test_career_types.py に固定してある。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .models import Candidate, Employment

# --- 共通 ---------------------------------------------------------------------
# 法人格。会社名の本体（括弧の外）にこれがあれば、本人はその法人の役員・社員。
_CORPORATE_RE = re.compile(
    r"株式会社|有限会社|合同会社|合資会社|合名会社|相互会社|法人|組合|機構|協会|公社|公団"
    r"|\binc\b|co\.?,?\s*ltd|\bltd\b|\bllc\b|\bcorp(?:oration)?\b|\bgmbh\b|\bk\.?k\.?\b|\bplc\b",
    re.IGNORECASE,
)
# 括弧の種類をそろえる（【業務委託】［契約］「工場」も丸括弧として扱う）。
_TRANS = str.maketrans(
    "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）【】［］〔〕「」",
    "0123456789abcdefghijklmnopqrstuvwxyz()()()()()",
)
# 「(株)」「㈱」などの略記。括弧として分割すると「株」だけが残り、社名（「山田工場」）が
# 法人格の無い工場名に見えてしまうため、先に法人格の語へ戻す。
_ABBR = {"株": "株式会社", "有": "有限会社", "同": "合同会社", "㈱": "株式会社", "㈲": "有限会社"}


def _norm(text: str | None) -> str:
    """判定用の正規化（小文字化・全角英数の半角化・括弧の統一・法人格の略記の展開）。"""
    s = (text or "").strip().lower().translate(_TRANS).replace("[", "(").replace("]", ")")
    return re.sub(r"\((株|有|同)\)|㈱|㈲", lambda m: _ABBR[m.group(1) or m.group(0)], s)


def _split_paren(text: str) -> tuple[str, list[str]]:
    """括弧の外（本体）と、括弧の中身のリストに分ける。"""
    inner = re.findall(r"\(([^()]*)\)", text)
    return re.sub(r"\([^()]*\)", " ", text), inner


_CORPORATE_ONLY_RE = re.compile(r"^(?:株式会社|有限会社|合同会社|合資会社|合名会社)$")


def _company_tokens(company: str) -> list[str]:
    """会社名欄を「社名」「拠点・付記」の語に分ける。

    「株式会社 ことば工場」のように法人格だけの語は次の語と合わせて社名とし、
    「トヨタ自動車株式会社元町工場」のように法人格の後ろに続く語は拠点名として分ける。
    """
    s = re.sub(r"(\S)(株式会社|有限会社|合同会社)(?=\S)", r"\1\2 ", _norm(company))
    tokens = [x for x in re.split(r"[\s/／・()]+", s) if x]
    while len(tokens) >= 2 and _CORPORATE_ONLY_RE.match(tokens[0]):
        tokens = [tokens[0] + tokens[1], *tokens[2:]]
    return tokens


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
    r"|自営業?|業務委託(?:契約)?|屋号|個人(?:経営|代理店)"
    r"|freelancer?|self[\s-]?employed|sole\s*proprietor|independent\s*contractor"
)
# 働き方の付記に添えられる勤務条件（「（業務委託・週3日）」「（フリーランス／常駐）」）。
_SE_NOTE_EXTRA = (
    r"副業|兼業|複業|常駐|リモート|フルリモート|スポット|非常勤|準委任|請負"
    r"|週\s*\d+\s*日?(?:勤務|稼働)?|月\s*\d+\s*(?:時間|日)|\d+\s*(?:年|か月|ヶ月|ヵ月)契約"
)
# 会社名欄の語・括弧書き・役職欄末尾の括弧書きが、本人の働き方だけを書いた付記か
# （「（業務委託）」「（フリーランス・業務委託）」「（業務委託として参画）」「（独立）」）。
# 「（個人事業主向け）」「（承継・開業支援部）」のように後ろへ別の語が続くものは付記ではない。
_SE_NOTE_RE = re.compile(
    rf"^\s*(?:(?:雇用|契約|就業)形態|働き方)?[:：]?\s*"
    rf"(?:{_SE_WORD}|独立・?開業|個人開業|独立|開業|一人親方)"
    rf"(?:\s*(?:として|にて|で)\s*(?:参画|稼働|従事|活動|就業|受託|勤務)?)?"
    rf"(?:\s*[・／/、,]\s*(?:{_SE_WORD}|{_SE_NOTE_EXTRA}))*\s*$"
)
# 会社名欄の語そのものが働き方（「個人」「フリー」「ITフリーランス」）。
_SE_TOKEN_RE = re.compile(r"^(?:個人|フリー|開業医|[a-z]{1,4}\s*フリーラン(?:ス|サー))$")
# 役職欄で本人の働き方として言い切っている形（「フリーランスとして参画」「業務委託にて」）。
_SE_AS_RE = re.compile(rf"(?:{_SE_WORD})(?:として|にて|で(?:活動|稼働|参画|従事|就業|受託))")
# 役職欄が働き方の語だけ（「フリーランス」「個人事業主」）。
_SE_TITLE_ONLY_RE = re.compile(rf"^(?:{_SE_WORD}|開業医)$")
# 役職欄の先頭に働き方の語があり、その後が職種名で終わる形（「フリーランス翻訳者」
# 「業務委託エンジニア」「フリーライター」）。職種名は決め打ちにする。顧客層・事業名・
# 担当を書いた「個人事業主セールス」「開業医MR」「フリーランスエージェント」
# 「フリーランスチーム リーダー」は本人の働き方ではない。
_SE_JOB = (
    r"翻訳者?|通訳(?:者|士)?|デザイナー|エンジニア|ライター|カメラマン|フォトグラファー|プログラマー?"
    r"|イラストレーター|編集者?|アナウンサー|コンサルタント|ディレクター|マーケター|クリエイター"
    r"|プロジェクトマネージャー|pm|開発者|講師|映像作家|記者"
    r"|designer|engineer|writer|translator|interpreter|photographer|developer|programmer"
    r"|consultant|director|marketer|editor"
)
_SE_LEADING_RE = re.compile(
    rf"^(?:フリーラン(?:ス|サー)|freelancer?|業務委託)\s*(?:の\s*)?(.{{0,12}}?)\s*(?:{_SE_JOB})$"
    rf"|^フリー(?:{_SE_JOB})$"
)
_SE_REST_GUARD_RE = re.compile(
    r"向け|担当|営業|事業|支援|管理|人材|サービス|活用|紹介|派遣|領域|チーム|エージェント"
    r"|platform|sales|business"
)
# 法人格の無い士業・医院・個人店（駐在員事務所・支社・営業所は会社の拠点なので除く）。
_PRACTICE_RE = re.compile(
    r"(?:税理士|会計|公認会計士|行政書士|司法書士|社会保険労務士|社労士|労務管理|労務|弁理士"
    r"|土地家屋調査士|中小企業診断士|[一二]級建築士|建築士|法務|法律|特許|設計|デザイン)事務所$"
    r"|クリニック$|医院$|歯科$|内科$|外科$|眼科$|皮膚科$|小児科$|耳鼻(?:咽喉)?科$|整形外科$"
    r"|治療院$|整骨院$|接骨院$|鍼灸院$|整体院$|動物病院$"
)
# 所長・院長などは役職全体がその語のときだけ（副院長・院長補佐・所長代理は勤務している職員）。
_LICENSE = (
    r"税理士|行政書士|司法書士|弁護士|公認会計士|社労士|社会保険労務士|弁理士|医師|歯科医師|獣医師"
    r"|中小企業診断士|[一二]級建築士|建築士"
)
_OWNER_TITLE_RE = re.compile(
    rf"^(?:代表者?|所長|院長|店主|オーナー|経営者|開業医)(?:{_LICENSE})?$"
    rf"|^(?:{_LICENSE})(?:・|\s)?(?:代表|所長|院長)$"
    rf"|^(?:{_LICENSE})\s*\((?:代表|所長|院長)\)$"
    rf"|^(?:代表|所長|院長)\s*\((?:{_LICENSE})\)$"
)
# 法人格の無い店の店主（「居酒屋 鈴の家」「スズキ商店」）。
_SHOP_OWNER_TITLE_RE = re.compile(r"^(?:店主|オーナー店主|オーナーシェフ)$")
# 副業・兼業として書かれた職歴（本業と並行している）。
_SIDE_JOB_RE = re.compile(r"副業|兼業|複業")


def _company_self_employed(c: str) -> bool:
    """会社名欄が本人の働き方（個人事業）を示しているか。c は正規化済み。"""
    _, parens = _split_paren(c)
    if any(_SE_NOTE_RE.match(p) for p in parens):
        return True   # 「株式会社X（業務委託・週3日）」「居酒屋 鈴の家（自営）」「ABCデザイン（独立）」
    body = _split_paren(c)[0]
    tokens = [x for x in re.split(r"[\s/／]+", body) if x]
    while len(tokens) >= 2 and _CORPORATE_ONLY_RE.match(tokens[0]):
        tokens = [tokens[0] + tokens[1], *tokens[2:]]
    # 法人の社名（「株式会社フリーランスエージェント」「レバテックフリーランス」）の中の語は
    # 数えず、社名とは別の語として書かれた働き方（「フリーランス」「株式会社X 業務委託」）を見る。
    return any(_SE_NOTE_RE.match(x) or _SE_TOKEN_RE.match(x) for x in tokens)


def is_self_employed(company: str, title: str = "", employment_type: str = "") -> bool:
    """その職歴が個人事業主（フリーランス・自営・業務委託・個人開業）か。"""
    c, t, et = _norm(company), _norm(title), _norm(employment_type)
    if et and re.search(r"freelanc|self.?employ|sole.?propri|individual|outsourc|業務委託|個人事業|フリーランス|自営", et):
        return True
    if _TRANSITION_RE.search(c) or _TRANSITION_RE.search(t):
        return False   # 「業務委託から正社員登用」「個人事業から法人化」は今の働き方ではない
    if _company_self_employed(c):
        return True
    # 役職欄の括弧書きは末尾にあるときだけ本人の働き方として読む（「マーケター（業務委託）」）。
    # 途中の括弧は前の語の補足（「BPO（業務委託）事業部 営業」の業務委託は事業名）。
    trailing = re.search(r"\(([^()]*)\)\s*$", t)
    if trailing and _SE_NOTE_RE.match(trailing.group(1)):
        return True
    t_body = _split_paren(t)[0].strip()
    if _SE_TITLE_ONLY_RE.match(t_body) or _SE_AS_RE.search(t):
        return True
    lead = _SE_LEADING_RE.match(t_body)
    if lead and not _SE_REST_GUARD_RE.search(lead.group(1) or ""):
        return True
    # 「山本税理士事務所 所長税理士」「居酒屋 鈴の家 店主」のような、法人格の無い士業・医院・個人店。
    if _CORPORATE_RE.search(c):
        return False
    head = _split_paren(c)[0].strip()
    if _PRACTICE_RE.search(head) and _OWNER_TITLE_RE.match(t):
        return True
    return bool(head) and bool(_SHOP_OWNER_TITLE_RE.match(t))


def current_employments(candidate: Candidate) -> list[Employment]:
    """在籍中の職歴（本業と副業など、同時に複数あることがある）。"""
    return [e for e in candidate.employments if e.is_current]


def is_current_self_employed(candidate: Candidate) -> bool:
    """現職が個人事業主か。

    在籍中の職歴がすべて個人事業のときだけ該当にする（正社員の本業と並行して副業で
    フリーランスをしている人は「現職が個人事業主」ではない）。職歴が取れない取り込み経路
    では現職の会社名・役職で判定する。離職中（在籍中の職歴が無い）は該当しない。
    """
    current = current_employments(candidate)
    if current:
        return all(is_self_employed(e.company, e.title, e.employment_type) for e in current)
    if not candidate.employments and (candidate.current_company or candidate.current_title):
        return is_self_employed(candidate.current_company, candidate.current_title)
    return False


def is_side_job(company: str, title: str) -> bool:
    """副業・兼業として書かれた職歴か（「Webデザイナー（副業）」）。"""
    return bool(_SIDE_JOB_RE.search(f"{_norm(company)} {_norm(title)}"))


# --- 2. 契約社員 ----------------------------------------------------------------
_CONTRACT_WORD = (
    r"契約(?:社員|職員|スタッフ|雇用)|嘱託(?:社員|職員)?"
    r"|有期(?:雇用|契約社員|契約職員|契約|社員)|期間(?:従業員|社員|工|契約|雇用(?:社員)?)"
    r"|会計年度任用|臨時(?:的任用)?(?:職員|社員)|臨時雇用|任期付き?|任期制|特任"
    r"|contract\s*(?:employee|staff(?!ing)|worker|basis)|fixed[\s-]?term"
)
# 「社内SE（契約）」「営業／契約」「（契約・1年更新）」「（有期）」「（1年契約）」のように
# 区切られた単独の語も拾う。「Contract Manager」「契約営業」「契約管理部」は職務なので、
# 後ろに語が続く形は拾わない。「（商談・契約）」「（提案・契約・導入支援）」のように
# 業務の並びの途中に出る「契約」は業務の工程なので拾わない（前が「・」の形は除く）。
_CONTRACT_SINGLE = r"契約|有期|contract|\d+\s*(?:年|か月|ヶ月|ヵ月|カ月)契約"
_CONTRACT_RE = re.compile(
    rf"{_CONTRACT_WORD}|(?:^|[(／/\s])(?:{_CONTRACT_SINGLE})(?=[)・／/、,]|\s*$)"
)
# 「契約社員の労務管理」「期間従業員採用担当」「派遣・契約社員コーディネーター」
# 「期間工専門キャリアアドバイザー」のように仕事の対象として書かれた形。
_CONTRACT_OBJECT_AFTER = re.compile(
    r"^(?:・[^\s・()]{1,8}?)*(?:の|を|向け|採用|管理|制度|労務|対応|担当|教育|研修|活用|領域|専門"
    r"|紹介|派遣|コーディネーター|アドバイザー|キャリア|事業|営業|ra(?![a-z])|ca(?![a-z])"
    r"|staffing|recruit)"
)
# 契約社員から正社員になった人（「契約社員から正社員へ」「入社当初は契約社員」「元契約社員」）。
_CONVERTED_RE = re.compile(
    r"正(?:社員|職員|規職員|規雇用|規社員)(?:に|へ|として)?(?:登用|転換|化|昇格|採用|入社)"
    r"|(?:契約|嘱託|有期)[^()]{0,16}?正(?:社員|職員|規)"
    r"|(?:契約|嘱託|有期)[^()]{0,16}?(?:から|より)(?:正社員)?(?:転換|登用|昇格)"
    r"|(?:→|⇒)\s*正(?:社員|職員)|無期転換|入社当初|当初は?契約|元(?:契約|嘱託|有期)"
)
# まだ登用されていない（「正社員登用あり」「正社員登用予定」「正社員登用制度あり」）。
_NOT_YET_CONVERTED_RE = re.compile(
    r"正(?:社員|職員)(?:への)?登用(?:制度|あり|有り|有|予定|見込み?|前提|可能|実績|の可能性)"
)


def is_contract(text: str, employment_type: str = "") -> bool:
    """その職歴・役職が契約社員（嘱託・有期雇用・期間従業員を含む）か。"""
    et = _norm(employment_type)
    if et and re.search(r"contract|fixed.?term|契約|嘱託|有期", et):
        return True
    s = _norm(text)
    if _CONVERTED_RE.search(s) and not _NOT_YET_CONVERTED_RE.search(s):
        return False
    return any(not _CONTRACT_OBJECT_AFTER.match(s[m.end():]) for m in _CONTRACT_RE.finditer(s))


# --- 3. 工場・生産部門 ----------------------------------------------------------
# 生産部門の部署・職種であることがはっきりしている語（開発・設計が付いても生産部門とみなす）。
# 「製造」「工場」単独は数えない（「アカウントエグゼクティブ（製造）」「製造・流通本部」
# 「工場・倉庫設計」のように業界・対象物として頻出するため）。
_FACTORY_STRONG_RE = re.compile(
    r"生産(?:管理|技術|計画|部(?![材品])|課|本部|ライン|現場|設備|職|スタッフ|オペレータ|工程"
    r"|グループ|g(?![a-z])|チーム|事業部|統括|センター|準備|部門|係)"
    r"|生産第?[0-9一二三四五六七八九十]+(?:課|部|係|グループ|g(?![a-z]))"
    r"|製造(?:部|課|技術|ライン|現場|オペ|職|スタッフ|グループ|g(?![a-z])|チーム|部門|本部|工程|係"
    r"|業務|作業|担当|員|リーダー|主任|班|マネージャー|マネジャー|責任者)"
    r"|製造[・/](?:検査|組立|加工|梱包|出荷|保全|品質管理)"
    r"|量産(?:技術|課|部|ライン|準備|試作)"
    r"|(?:塗装|溶接|プレス|熱処理|板金|鋳造|鍛造|研磨|切削|めっき|メッキ|組立|機械加工)"
    r"(?:課|係|班|工|職|作業|ライン|グループ)"
    r"|(?<![ァ-ヶー])ライン工|設備保全|期間(?:従業員|工)|生管|生技"
    r"|(?<!スマート)(?<!デジタル)工場(?:長|勤務|作業|スタッフ|従業員|ワーカー|内|配属|管理|部|課)"
    r"|manufacturing\s*(?:engineer|operator|technician|associate|staff|worker|supervisor|manager"
    r"|lead|leader|director|specialist|department|dept|division|line|plant|site|operations?)"
    r"|(?:head|director|vp|manager)\s+of\s+manufacturing|^manufacturing$"
    r"|plant\s*manager|(?<!smart\s)(?<!digital\s)\bfactory\b"
)
# 英語の production は映像・音楽などの「制作」でもあるため、制作会社では数えない。
_PRODUCTION_RE = re.compile(
    r"production\s*(?:control|planning|engineer|supervisor|line|operator|staff|worker|technician|associate)"
)
_MEDIA_RE = re.compile(
    r"プロダクション|production|映像|映画|放送|テレビ|出版|広告|スタジオ|studio|ゲーム|エンタ|音楽|music"
)
# 製造業の中なら生産部門だが、業種によってはそうでない語（病院の検査課、SaaSの品質保証）。
_FACTORY_CONTEXT_RE = re.compile(
    r"品質(?:管理|保証)|品管|品証|工程(?:管理|改善)|(?<![a-z])qc(?![a-z])"
    r"|quality\s*(?:control|assurance)"
    r"|(?:出荷|製品|外観|品質|受入|最終)?検査(?:員|課|係|工|部|グループ|g(?![a-z])|チーム|担当|室)?"
    r"|(?<!設備)保全(?:課|係|担当|部|グループ|g(?![a-z])|チーム|員|業務)?"
    r"|production\s*(?:manager|management|lead|leader)"
)
_MANUFACTURING_CUE_RE = re.compile(
    r"工場|製造|生産|製作所|工業|化学|電機|電子|電工|製薬|製鉄|鉄鋼|金属|精機|機械|機工|重工"
    r"|食品|製菓|飲料|自動車|部品|樹脂|ゴム|繊維|製紙|印刷|窯業|ガラス|セメント|メーカー"
    r"|manufactur|plant|factory|industr|chemical|motor|electric"
)
# 製造業らしい語があっても製造業ではない所属（「前田建設工業」「トヨタ記念病院」「自動車保険」）。
_NON_MANUFACTURING_RE = re.compile(
    r"病院|クリニック|医療法人|保険|銀行|証券|信用金庫|信金|建設(?!機)|土木|工務|不動産|商事|商社"
)
# 品質管理・検査・工程管理が生産部門のものではない文脈（臨床・ソフト・サービス・物流）。
_CONTEXT_GUARD_RE = re.compile(
    r"臨床|治験|信頼性|gcp|gvp|gpsp|薬事|研究|開発|設計|企画|データ|サービス|コール|物流"
    r"|業務品質|保険|ソフト|pmo|工事|土木|施工"
)
# 生産現場を示すがそれだけでは弱い語（研究・開発・設計・企画が付けば打ち消す）。
_FACTORY_WEAK_RE = re.compile(
    r"(?<![ァ-ヶー])ライン(?:作業|リーダー|長|工)?(?=$|\s|・|\)|課|班|担当|作業)"
    r"|組立|組み立て|製剤(?:課|部|工程|オペ|技術|室|係|グループ|g(?![a-z]))|操業|製鋼|製錬"
    r"|成形(?!機)|加工(?:課|部|オペ)|工員|技能(?:職|員)|機械加工"
    r"|(?<![a-z])c?nc(?=旋盤|フライス|加工|オペ)|旋盤|マシニング|溶接工|塗装工|プレス工|鋳造|鍛造"
    r"|(?:製造|プラント|印刷|機械|設備|ライン|マシン|装置|生産|成形|射出|プレス)\S{0,8}?\s?オペレータ"
    r"|班長"
)
# 役職の本体が「製造」の一語（「製造」「製造 リーダー」「製造（自動車部品）」）。
_FACTORY_BARE_RE = re.compile(r"(?:^|\s)製造(?=\s|$)")
# 生産側でない職能・業界/顧客/製品を担当する職種（括弧の外＝役職の本体にあれば打ち消す。
# 括弧の中の補足「（顧客対応も担当）」では打ち消さない）。
_NON_FACTORY_ROLE_RE = re.compile(
    r"営業|販売|セールス|経理|財務|会計|人事|採用|労務|広報|マーケ|コンサル"
    r"|カスタマー|サクセス|ソフト|(?<!fa)(?<!制御)(?<!設備)システム|アプリ|web|テスト"
    r"|(?<![a-z/])qa(?![a-z/])|向け|顧客|生産性|法務|知財|施工|建築|ディレクター|プロデューサー"
    r"|バイヤー|購買|調達|資材|本社(?!工場)|アドバイザー|コーディネーター"
    r"|プロダクトマネージャー|メディカル|学術|アカウント|エグゼクティブ|アナリスト|投資|融資|審査"
    r"|インダストリー|セクター|業界|領域|案件|分野|部材|委託|子会社|拠点|新設|跡地|不動産"
    r"|モジュール|基幹|組込|組み込み|ファームウェア|データ|物流|コールセンター|コンプライアンス"
    r"|サービス|サポート|フィールド|プロモーション|事業責任者|研修|講師|m&a"
    r"|(?<![a-z])(?:ra|ca|dx|it|mr|ae|bd|pmo|sap|erp|iot|hr)(?![a-z])"
    r"|saas|sales|marketing|consult|account|executive|analyst|practice|sector|invest|financ"
    r"|legal|counsel|human\s*resources|procure|purchas|customer|software|business\s*partner"
)
# 括弧の中でも打ち消す、IT を表す補足（「品質保証（ソフトウェア）」「品質保証部（ファームウェア）」）。
_IT_NOTE_RE = re.compile(
    r"ソフト|(?<!fa)(?<!制御)(?<!設備)システム|アプリ|web|saas|(?<![a-z])it(?![a-z])|テスト"
    r"|(?<![a-z/])qa(?![a-z/])|ファームウェア|組込|組み込み"
)
# 弱い語・品質管理などの文脈語だけのときに打ち消す職能（研究・開発・設計・企画は生産部門ではない）。
_NON_FACTORY_SOFT_RE = re.compile(r"研究|開発|設計|企画")
# 所属が工場そのもの（「株式会社X 豊橋工場」「株式会社X（元町工場）」「君津製鉄所」）。
_FACTORY_SITE_RE = re.compile(
    r"(?<!スマート)(?<!デジタル)工場$|製鉄所$|製作所\S*工場$|製造所$|製油所$|プラント$|セントラルキッチン$"
)
# 役職欄の異動の経緯（「生産技術部→経営企画部」「（生産管理部から異動）」）。今の部署だけを見る。
_MOVED_NOTE_RE = re.compile(r"から(?:の)?(?:異動|転籍|転属|配置転換)|より(?:異動|転籍)|異動前|出身")


def _current_part(s: str) -> str:
    """役職欄から異動の経緯を除き、今の役職・部署の部分だけにする。s は正規化済み。"""
    s = re.split(r"\s*[→⇒]\s*(?![^()]*\))", s)[-1]
    return re.sub(r"\(([^()]*)\)",
                  lambda m: " " if _MOVED_NOTE_RE.search(m.group(1)) else m.group(0), s)


def _factory_site(company: str) -> bool:
    """会社名欄に、社名とは別に工場などの所在地が書かれているか。"""
    tokens = _company_tokens(company)
    if len(tokens) >= 2:
        return any(_FACTORY_SITE_RE.search(tok) and not _CORPORATE_RE.search(tok)
                   for tok in tokens[1:])
    return bool(tokens) and not _CORPORATE_RE.search(tokens[0]) and bool(_FACTORY_SITE_RE.search(tokens[0]))


def is_factory_role(text: str, company: str = "") -> bool:
    """役職・部署名が工場・生産部門のものか（company は業種の手がかりとして使う）。"""
    s = _current_part(_norm(text))
    body, parens = _split_paren(s)
    if _NON_FACTORY_ROLE_RE.search(body) or any(_IT_NOTE_RE.search(p) for p in parens):
        return False
    c = _norm(company)
    context = f"{c} {s}"
    cue = bool(_MANUFACTURING_CUE_RE.search(context)) and not _NON_MANUFACTURING_RE.search(context)
    soft = bool(_NON_FACTORY_SOFT_RE.search(s))
    media = bool(_MEDIA_RE.search(c))
    for i, seg in enumerate([body, *parens]):
        # 括弧の中に出た語は、その括弧の中の文脈で判断する（「製品設計（工場向け）」の工場は対象）。
        if i > 0 and _NON_FACTORY_ROLE_RE.search(seg):
            continue
        if _FACTORY_STRONG_RE.search(seg) or (_PRODUCTION_RE.search(seg) and not media):
            return True
        # 役職欄の本体に工場名がある（「豊橋工場 総務課」）。
        if i == 0 and any(_FACTORY_SITE_RE.search(tok) for tok in seg.split()):
            return True
        if _FACTORY_CONTEXT_RE.search(seg) and cue and not soft and not _CONTEXT_GUARD_RE.search(s):
            return True
        if not soft and (_FACTORY_WEAK_RE.search(seg) or (i == 0 and _FACTORY_BARE_RE.search(seg))):
            return True
    return False


def _factory_site_job(company: str, title: str) -> bool:
    """所属が工場そのものか（本社勤務と書かれていれば除く）。"""
    t = _norm(title)
    if not _factory_site(company) or re.search(r"本社(?!工場)", t):
        return False
    # 会社名欄が「ことば工場」の一語だけのときは、社名か拠点名か分からないので、
    # 役職が生産側でない職能（営業・経理など）なら社名とみなす。
    return len(_company_tokens(company)) >= 2 or not _NON_FACTORY_ROLE_RE.search(t)


def is_factory_job(company: str, title: str) -> bool:
    """その職歴が工場・生産部門か（所属が工場、または役職が生産部門）。"""
    return _factory_site_job(company, title) or is_factory_role(title, company)


# --- 期間の割合 -----------------------------------------------------------------

def _months(months: int | None, years: float | None) -> int | None:
    """月数（DBに保存済みの古い職歴は月数が無いので年数から戻す）。"""
    if months is not None:
        return months
    return None if years is None else round(years * 12)


def _share(employments: Iterable[Employment],
           whole_flag: Callable[[Employment], bool],
           title_flag: Callable[[Employment], bool],
           segment_flag: Callable[[str, str], bool]) -> tuple[int, int]:
    """(該当の月数, 全体の月数)。期間が分からない職歴は分母・分子の両方から除く。

    whole_flag（所属が工場・雇用形態の欄など、職歴全体に効くもの）が立てば全期間を数える。
    そうでなければ、社内経歴（companyCareers）に該当する部署・役職が書かれていればその期間
    だけを数え（役職欄が入社時の「生産技術部」のままでも、経営企画部に移った期間は数えない）、
    社内経歴に該当が無ければ役職欄（title_flag）で職歴全体を判定する。
    """
    hit = total = 0
    for e in employments:
        m = _months(e.months, e.years)
        if m is None:
            continue
        total += m
        if whole_flag(e):
            hit += m
            continue
        seg_hit = sum(sm for s in e.segments
                      if (sm := _months(s.months, s.years)) is not None
                      and segment_flag(s.name, e.company))
        if seg_hit:
            hit += min(seg_hit, m)   # 社内で重なった期間を足しすぎない
        elif title_flag(e):
            hit += m
    return hit, total


def contract_ratio(candidate: Candidate) -> float | None:
    """キャリア（在籍期間の合計）のうち契約社員だった割合。判断材料が無ければ None。"""
    hit, total = _share(
        candidate.employments,
        lambda e: is_contract(e.company, e.employment_type),
        lambda e: is_contract(f"{e.company} {e.title}"),
        lambda name, _company: is_contract(name),
    )
    return hit / total if total > 0 else None


def factory_ratio(candidate: Candidate) -> float | None:
    """キャリア（在籍期間の合計）のうち工場・生産部門だった割合。判断材料が無ければ None。"""
    hit, total = _share(
        candidate.employments,
        lambda e: _factory_site_job(e.company, e.title),
        lambda e: is_factory_role(e.title, e.company),
        is_factory_role,
    )
    return hit / total if total > 0 else None
