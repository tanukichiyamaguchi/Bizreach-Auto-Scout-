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
    "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）【】［］〔〕「」｜",
    "0123456789abcdefghijklmnopqrstuvwxyz()()()()()|",
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


# --- 4. 公務員 ------------------------------------------------------------------
# 運用者指示（2026-11）: キャリアの中に公務員がある人は対象外（期間・現職か過去かを問わない）。
# 公務員 = 国・地方公共団体に任用される職員（一般職・特別職、正規・非正規を問わない）。
# 中央省庁・出先機関・裁判所・国会・在外公館、都道府県庁・市区町村役所/役場・議会・
# 教育委員会・公営企業、警察・消防・自衛隊、公立の学校/病院/保育所、一部事務組合。
# 独立行政法人・国立大学法人・公立大学法人・地方独立行政法人・特殊法人・公益法人・社会福祉協議会・
# 民営化した会社（日本郵便・JR・NTT など）は法人で、職員は公務員ではないため数えない。
# 民間から官公庁への出向・派遣・常駐・業務委託・指定管理の職歴も、雇い主は民間なので数えない
# （公務員が別の官公庁へ出向・派遣された職歴は公務員）。
#
# 判定の順序（誤除外を避けるため、役職欄の機関名は「勤務先を伏せた人」のときだけ数える）:
# 1. 役職欄の本人の経歴の付記（「元自衛官」「前職：横浜市役所」「自衛隊出身」「市役所を経て入社」）
# 2. 出向・派遣・委託などの付記（出向元が民間なら民間、公的機関なら公務員）
# 3. 役職欄だけで公務員と分かる職（議員・公設秘書・会計年度任用職員）
# 4. 会社名欄が公的機関（先頭の語。法人格・民間の施設を示す語が無い）
# 5. 会社名を伏せた人（「-」「非公開」）の役職欄の機関名・公務員の職名
# 民間の会社名（「富士通」「千葉銀行」のように法人格を省いて書く人が多い）の役職欄に出る
# 「防衛省向け」「国土交通省発注工事」「県庁支店」「公務員の転職支援」は仕事の対象なので数えない。

# 中央省庁（略称・再編前の旧省庁名・内閣官房/各省の機関を含む）。「環境省エネ」は数えない。
_MINISTRY = (
    r"(?:内閣府|内閣官房|内閣法制局|内閣サイバーセキュリティセンター|内閣情報調査室|国家安全保障局"
    r"|総務省|法務省|外務省|財務省|文部科学省|厚生労働省|農林水産省|経済産業省|国土交通省|環境省|防衛省"
    r"|復興庁|デジタル庁|宮内庁|警察庁|金融庁|消費者庁|こども家庭庁|国税庁|特許庁|気象庁|海上保安庁"
    r"|観光庁|林野庁|水産庁|資源エネルギー庁|中小企業庁|文化庁|スポーツ庁|消防庁|公安調査庁"
    r"|出入国在留管理庁|防衛装備庁|原子力規制庁|会計検査院|人事院|国土地理院|証券取引等監視委員会"
    r"|(?:公正取引|個人情報保護|原子力規制|国家公安|運輸安全|中央労働)委員会|公取委"
    r"|経産省|厚労省|国交省|文科省|農水省|通産省"
    r"|大蔵省|通商産業省|郵政省|建設省|運輸省|厚生省|労働省|文部省|自治省|農林省|総理府|総務庁"
    r"|防衛庁|環境庁|経済企画庁|科学技術庁|国土庁|北海道開発庁|沖縄開発庁|社会保険庁|防衛施設庁"
    r"|郵政事業庁)(?!エネ)"
)
# 公的機関の名前。
_PUBLIC_BODY = (
    rf"{_MINISTRY}"
    r"|(?:都|道|府|県)庁|(?:市|区)役所|(?<!公証)役場|議会事務局|(?:都|道|府|県|市|区|町|村)議会(?!議員)"
    r"|教育委員会|教育庁|(?:都|道|府|県)教育局|選挙管理委員会|人事委員会|企業庁|企業局"
    r"|水道局|下水道局|交通局|病院局|港務局|(?:都|市|県|町|村)営(?:バス|地下鉄|交通|電車|水道|病院|渡船)"
    r"|(?:都|道|府|県)税事務所|国税事務所|土木事務所|農林(?:水産)?事務所|児童相談所|福祉事務所"
    r"|保健福祉事務所|家畜保健衛生所|保健所|(?<!産業)保健センター|消防(?:局|本部|署|学校|大学校)"
    r"|一部事務組合|広域連合|(?:総合|地域)?振興局"
    r"|警察(?:署|本部|学校|大学校)|(?:都|道|府|県)警察|(?:県|府)警(?!備)|(?<![鉄水])道警(?!備)|警視庁"
    r"|皇宮警察|管区警察局|交番|駐在所|(?:衆|参)議院(?!議員)|国立国会図書館|国会図書館|裁判所"
    r"|(?:地|高|家|簡)裁|(?:最高|高|区)検|(?<!生)地検(?!品|査)|検察庁|検察審査会|法務局"
    r"|(?<![人費関得与])税関(?![連係])|国税局|税務署|国税不服審判所|労働局|労働基準監督署|労基署"
    r"|ハローワーク(?!ス)|公共職業安定所|地方整備局|運輸(?:支)?局|財務(?:支)?局|財務事務所|経済産業局"
    r"|農政局|森林管理(?:局|署)|地方環境事務所|航空局|航空交通管制部|北海道開発局|開発建設部"
    r"|(?:地方)?厚生(?:支)?局|総合通信局|(?:河川国道|国道|河川|港湾|空港|港湾空港整備|砂防|ダム|営繕)事務所"
    r"|社会保険事務所|検疫所|植物防疫所|動物検疫所|刑務所|拘置所|少年院|少年鑑別所|矯正管区|保護観察所"
    r"|公安調査局|入国管理局|入国管理センター|出入国在留管理局|入管|海上保安(?:部|署|本部|学校|大学校)"
    r"|海保|気象台|(?:税務|自治|気象|国土交通|農業|水産)大学校|国立感染症研究所|国土技術政策総合研究所"
    r"|自衛隊|(?:陸|海|空)自(?![動治然由己立律])|(?:統合|陸上|海上|航空)幕僚監部|(?:陸|海|空|統)幕"
    r"|地方総監部|駐屯地|分屯地|防衛局|防衛施設局|防衛大学校|防衛医科大学校|防衛医大|防衛研究所"
    r"|地方協力本部|在外公館|(?<!駐)日本(?:国)?(?:大使館|総領事館|領事館|政府代表部)"
    r"|^在(?!日)[^\s()]{1,12}?(?:大使館|総領事館|領事館)"
    r"|(?:都|道|府|県|市|区|町|村)立(?![^\s()]*(?:大学(?!校)|医大|市大|府大|県大|大附属|大病院"
    r"|高等専門学校|高専))[^\s()]{0,12}?"
    r"(?:学校|小学校|中学校?|高等学校|高校|中等教育学校|特別支援学校|養護学校|大学校|技術校|専門校"
    r"|病院|医療センター|センター|診療所|図書館|博物館|美術館|公民館|児童館|保育所|保育園|幼稚園"
    r"|こども園|(?<=[^\s()])(?:小|中|高)(?=$|\s|\())"
    r"|公立(?!大学)[^\s()]{0,10}?(?:学校|中学|高校|幼稚園|保育所|保育園|こども園|図書館|病院|医療センター)"
    r"|(?:市|町|村)民病院|国民健康保険[^\s()]{0,6}?(?:病院|診療所)|国保[^\s()]{0,6}?(?:病院|診療所)"
)
_PUBLIC_BODY_RE = re.compile(_PUBLIC_BODY)
# 会社名欄の先頭にあれば、後ろの語（「大学振興課」「国立大学法人支援課」「総合研究所」）に関係なく公的機関。
_PUBLIC_START_RE = re.compile(
    rf"^(?:{_MINISTRY}|国立感染症研究所|国土技術政策総合研究所|防衛研究所|防衛医大|防衛医科大学校"
    r"|防衛大学校|(?:税務|自治|気象|国土交通|消防|海上保安|警察)大学校)"
)
# 英語表記の公的機関（会社名欄だけで見る）。
_PUBLIC_EN_RE = re.compile(
    r"ministry of (?!supply)|embassy of japan|consulate[- ]general of japan|permanent mission of japan"
    r"|self[- ]defen[cs]e forces?|(?<![a-z])(?:jgsdf|jmsdf|jasdf|meti|mofa|mhlw|mext|mlit|maff)(?![a-z])"
    r"|metropolitan government|prefectural (?:government|office|police|board of education)"
    r"|(?<![a-z])prefecture$|^[a-z]+ (?:city|prefectural|metropolitan) (?:government|hall|office)"
    r"|city (?:hall|government|office)|ward office|town office|village office|^city of [a-z]"
    r"|^[a-z]+ city$|^(?:hokkaido|tokyo|osaka|kyoto) (?:government|metropolitan)"
    r"|board of education|fire department|police department|^(?:tokyo|osaka|yokohama|kobe|nagoya) customs"
    r"|national police agency|metropolitan police|japan coast guard|cabinet (?:office|secretariat)"
    r"|national tax agency|regional taxation bureau|(?:district|high|supreme|family) court"
    r"|public prosecutors? office|japan patent office|financial services agency|^(?:japan )?digital agency$"
)
# 公務員の職名（会社名を伏せた人の役職欄・経歴の付記で見る）。「準公務員」「みなし公務員」は数えない。
_PUBLIC_ROLE = (
    r"(?<!準)(?<!みなし)(?:地方|国家)?公務員|自衛官|官僚|警察官|警察職員|刑事(?!事件|弁護|訴訟|法|責任)"
    r"|巡査(?:部長|長)?|警部(?:補)?|警視(?!庁)(?:正|長|監)?|消防(?:士|吏員|官|職員|隊員)|刑務官"
    r"|入国(?:審査|警備)官|保護観察官|航空管制官|国税(?:専門|調査)官|税務(?:署)?職員|食品衛生監視員"
    r"|麻薬取締官|労働基準監督官|海上保安官|裁判官|判事|検事|検察官|書記官|事務官|技官|外交官"
    r"|公設(?:第[一二12])?秘書|政策(?:担当)?秘書"
    r"|(?:衆議院|参議院|国会|(?:都|道|府|県|市|区|町|村)議会|地方)\s?議員|(?:都|道|府|県|市|区|町|村)議(?!会)"
    r"|(?:自治体|(?:市|区)役所|役場|(?:都|道|府|県)庁|(?:市|区|町|村|県|都|府|(?<![鉄水])道)(?:の)?)職員"
    r"|(?:都|道|府|県|市|区|町|村)教職員|行政職員?|会計年度任用(?:職員)?|臨時的任用(?:職員|教員|講師)?"
    r"|任期付職員|期間業務職員|[0-9一二三]等(?:陸|海|空)(?:佐|尉|曹|士)|(?:陸|海|空)(?:将補?|曹長|士長)"
)
_PUBLIC_ROLE_RE = re.compile(_PUBLIC_ROLE)
# 役職欄だけで公務員と分かる職（議員・公設秘書・自治体の非正規の任用）。会社名欄に議員事務所や
# 政党名を書く人がいるため、勤務先が法人でなければ数える。
_PUBLIC_OFFICE_RE = re.compile(
    r"公設(?:第[一二12])?秘書|政策(?:担当)?秘書"
    r"|(?:衆議院|参議院|国会|(?:都|道|府|県|市|区|町|村)議会|地方)\s?議員"
    r"|会計年度任用(?:職員)?|臨時的任用(?:職員|教員|講師)?|任期付職員|期間業務職員"
)
# 伏せた書き方。単独の「県」「市」は「某県」「某市」のときだけ。
_PUBLIC_GENERIC = (
    r"官公庁|中央官庁|官庁|中央省庁|省庁|地方自治体|自治体|地方公共団体|政令(?:指定)?都市|政令市"
    r"|政府機関|国家機関|国の(?:行政)?機関|行政機関|警察|消防(?!設備|用品|機器|点検|器具)|自衛隊"
    r"|公立(?!大学)|(?:都|道|府|県|市|区|町|村)立(?=$|\))"
)
_PUBLIC_GENERIC_RE = re.compile(
    rf"^(?:某|都内の?|県内の?|市内の?|関東の?|関西の?|地方の?|首都圏の?)?(?:{_PUBLIC_GENERIC})$"
    r"|^某(?:県|市|区|町|村|省|官庁)$"
    r"|^(?:地方|国家)?公務員(?:\s*[^\s()]{0,8})?$|^(?:衆議院|参議院|国会|地方)議員$"
)
# 会社名欄が自治体名そのもの、または自治体名に部局名を続けたもの
# （「横浜市」「神奈川県横浜市」「東京都総務局人事部人事課」「横浜市職員」「岩手郡雫石町」）。
# 区・町・村だけで始まるものは、民間の名前と紛れやすいので部局・役所の語が続く形に限る。
_PREF = r"(?:北海道|東京都|京都府|大阪府|[一-龥]{2,3}県)"
_CITY = r"(?!都市)[一-龥ぁ-ゖァ-ヶ々]{1,7}?市(?!場)"
_WARD_TOWN = r"[一-龥ぁ-ゖァ-ヶ々]{1,6}?(?:区|町|村)"
# 「中村」「西村」のような名字や「ドイツ村」「映画村」のような施設は町村名とみなさない。
_NOT_VILLAGE = (
    r"(?![中田木北西野松今岡川吉上下河藤奥島三植大小山杉森沢澤梅竹宮]村(?:$|[^\s]))(?!大手町$)"
    r"(?![^\s]*(?:江戸|映画|明治|民俗|ドイツ|スペイン|オランダ|カナダ|ムーミン|フルーツ|ワイン|リゾート"
    r"|選手|ハーブ|琉球|時代)村)"
)
_GUN = r"[一-龥]{1,4}郡"
_UNIT_CHARS = r"[一-龥ぁ-ゖァ-ヶ々ー・a-z0-9]{0,30}?"
_BROAD_UNIT = (rf"{_UNIT_CHARS}(?:局|部|課|室|係|班|担当|グループ|庁|事務所|出張所|支所|事務局|議会"
               r"|委員会|本部|署|役所|役場|庁舎|図書館|公民館|児童館|市場|保育所|保育園|診療所|職員|教職員)")
_STRONG_UNIT = (rf"{_UNIT_CHARS}(?:局|部|課|室|役所|役場|議会|委員会|庁舎|出張所|支所|図書館|公民館"
                r"|児童館|保育所|保育園|診療所|職員)")
_PUBLIC_PLACE_RE = re.compile(
    rf"^(?:{_PREF}(?:{_GUN})?(?:{_CITY}|{_WARD_TOWN})?(?:{_WARD_TOWN})?(?:{_BROAD_UNIT})?"
    rf"|{_CITY}(?:{_WARD_TOWN})?(?:{_BROAD_UNIT})?"
    rf"|(?:{_GUN})?{_NOT_VILLAGE}{_WARD_TOWN}(?:{_STRONG_UNIT})?)$"
)
# 法人格（公的機関でない）。「法人課税部門」のような部署名の「法人」は数えない。
_LEGAL_ENTITY_RE = re.compile(
    r"株式会社|有限会社|合同会社|合資会社|合名会社|相互会社"
    r"|(?:社団|財団|医療|学校|社会福祉|宗教|独立行政|国立大学|公立大学|地方独立行政|国立研究開発"
    r"|特定非営利活動|特殊|認可|npo|税理士|弁護士|監査|司法書士|行政書士|社会保険労務士|弁理士"
    r"|土地家屋調査士|農事組合|一般社団|一般財団|公益社団|公益財団|技術研究組合)法人"
    r"|協同組合|共済組合|健康保険組合|労働組合|信用組合|機構|協会|協議会|財団|公社|公団|事業団|基金"
    r"|振興会|連合会|中央会|商工会|医師会|薬剤師会|社協|シルバー人材センター"
    r"|(?<![a-z])(?:inc|ltd|llc|corp|corporation|gmbh|plc)(?![a-z])"
    r"|co\.?,?\s*ltd|(?<![a-z])k\.?k\.?(?![a-z])|コーポレーション|ホールディングス"
)
# 法人格はあるが公的機関（一部事務組合・広域連合・企業団の職員は地方公務員）。
_PUBLIC_UNION_RE = re.compile(
    r"(?:消防|(?<!保険)事務|広域行政|広域市町村圏|衛生|環境衛生|衛生施設|清掃施設|清掃|環境施設|病院"
    r"|学校|資源環境|競馬|退職手当)組合|港管理組合|広域連合|(?<!中小)企業団(?!体|地)"
)
# 会社名欄の先頭の語が民間の施設・団体であることを示す語（公立大学法人の病院の略称、済生会・医師会の
# 病院、庁舎内や庁舎前の店・郵便局、銀行の県庁支店）。
_PRIVATE_HEAD_RE = re.compile(
    r"銀行|証券|信用金庫|信金|信用組合|労働金庫|bank|大学(?!校)|医大|市大|府大|県大|高等専門学校|高専"
    r"|郵便局|クリニック|医院|歯科|薬局|ホテル|売店|済生会|医師会|厚生連|赤十字|日赤|労災"
    r"|総合研究所|総研|(?:庁|役所|役場|署)(?:前|内)|(?:庁|役所)店|前店"
    r"|(?:法律|会計|税理士|特許|設計|司法書士|行政書士|社会保険労務士|社労士|建築|デザイン|監査|経営)事務所"
    r"|向け|関連会社|ベンチャー|ファーム|ベンダー|(?<![a-z])(?:sier|ses)(?![a-z])"
)
# 民間の立場で公的機関に勤めたことを示す付記（会社名欄・役職欄。区切られた語として出るもの）。
_PRIVATE_STATUS_RE = re.compile(
    r"(?:^|(?<=[\s(、,・/／|]))(?:派遣|人材派遣|派遣社員|派遣スタッフ|派遣職員|派遣契約|業務委託|委託"
    r"|外部委託|受託|受託会社|委託会社|委託事業|請負|常駐|出向|民間出向|アウトソーシング|bpo)"
    r"(?=$|[\s)、,・/／|(]|として|にて)"
    r"|業務委託(?:先|会社|契約社員)|指定管理(?!者制度)|公設民営|として参画|私設|派遣会社|人材派遣"
    r"|派遣員|派遣先|常駐先"
    r"|パソナ|リクルートスタッフィング|テンプスタッフ|パーソル|アデコ|マンパワー|ランスタッド"
    r"|スタッフサービス|ヒューマンリソシア|インタラック"
)
# 出向・派遣の向き。「〇〇から/より出向」「出向元：〇〇」は出向元が公的機関なら公務員、
# 「〇〇へ出向」「被災地へ派遣」「PKO派遣」は元の組織（会社名欄）の職員。
_FROM_SECONDMENT_RE = re.compile(
    r"([^\s()、,：:・/／]{1,30}?)(?:より|から)(?:の)?(?:出向|派遣)|(?:出向|派遣)元\s*[:：]?\s*([^\s()、,]+)"
)
_TO_SECONDMENT_RE = re.compile(
    r"へ(?:の)?(?:出向|派遣)|派遣(?:施設)?隊|(?:pko|災害|被災地|国際緊急援助)\S{0,4}派遣|兼務|兼任"
)
# 勤務先を伏せた書き方。
_HIDDEN_COMPANY_RE = re.compile(
    r"^(?:|[-ー―‐—－−/]+|n/?a|none|なし|[*＊×○〇]+"
    r"|(?:社名|企業名|会社名|勤務先|所属)?(?:は)?(?:非公開|非公表|非開示|匿名|秘匿)(?:企業|会社)?"
    r"|[^\s()]{0,12}のため非公開)$"
)
# 伏せた勤務先の括弧書きが民間を示す語（「非公開（IT企業）」）。
_PRIVATE_NOTE_RE = re.compile(
    r"企業|会社|メーカー|商社|銀行|ベンダー|sier|ses|ベンチャー|コンサル|(?<![a-z])it(?![a-z])|メディア"
    r"|派遣|委託|請負|受託|常駐"
)
# 公的機関の下の組織（会社名欄の先頭がこれで、括弧に機関名を書く形「第1空挺団（陸上自衛隊）」
# 「西谷浄水場（横浜市水道局）」「みなと中学校（横浜市立）」）。
_ORG_UNIT_HEAD_RE = re.compile(
    r"(?:部|局|課|室|係|班|所|署|団|隊|群|基地|駐屯地|分屯地|学校|校|高|中|小|院|館|園|場|センター"
    r"|本部|艦|事務所|庁舎|交番)$|^護衛艦|^第[0-9一二三四五六七八九十]+"
)
_FACILITY_HEAD_RE = re.compile(
    r"(?:学校|高校|中学校|小学校|中|高|小|病院|医療センター|診療所|保育園|保育所|図書館|幼稚園|こども園"
    r"|公民館|児童館|博物館|美術館|技術校|専門校|大学校)$"
)
# 民間の職歴の役職欄に書かれた本人の経歴（「元自衛官」「前職：横浜市役所」「自衛隊出身」
# 「国税OB」「市役所勤務を経て入社」「警察官として10年勤務後」「横浜市役所より出向」）。
# 地名だけの「前職：京都銀行」「元都市銀行」「前職：埼玉県の商社」は経歴とみなさない。
# 公立学校・公立病院の職（「元教員（公立中学校）」「前職：教員（公立）」）。
_PUBLIC_JOB_NOTE = (
    r"(?:教員|教諭|講師|保育士|看護師|職員)\((?:[^()]{0,8}?(?:都|道|府|県|市|区|町|村)立|公立)[^()]{0,8}\)"
)
_PAST_GENERIC = (
    r"官公庁|中央官庁|官庁|中央省庁|省庁|地方自治体|自治体|地方公共団体|国家機関|政府機関|行政機関"
    r"|自衛隊|警察(?!犬)|消防(?!設備|用品|機器|点検|器具)|国税|検察"
)
_PUBLIC_PAST_NAME = (
    rf"(?:{_PUBLIC_JOB_NOTE}|{_PUBLIC_BODY}|{_PUBLIC_ROLE}|{_PAST_GENERIC}"
    rf"|{_PREF}(?=$|[\s)、,・/／|]|を経|出身|勤務|職員)|{_CITY}(?=$|[\s)、,・/／|]|を経|勤務|職員))"
)
_PAST_PREFIX_RE = re.compile(
    r"(?:^|(?<=[\s(、,・/／|※:：]))(?:元(?!請|受|締|本|祖|気|来|旦|日|号|年|々|の)\s*[・:：]?\s*"
    r"|前(?:々|前)?職\s*\)?\s*[:：/／]?\s*)"
    rf"[一-龥ぁ-ゖァ-ヶ々a-z0-9]{{0,8}}?{_PUBLIC_PAST_NAME}(?!在住|出身)"
)
_PAST_SUFFIX_RE = re.compile(
    rf"{_PUBLIC_PAST_NAME}(?:\([^()]{{0,16}}\))?"
    r"(?:勤務|に[^\s()]{0,6}?在籍|として[^\s()]{0,8}?勤務|にて[^\s()]{0,8}?勤務)?"
    r"(?:後|を経て|出身|ob|og|より転職|から転職|を(?:任期満了)?退職|退職後|を?退官|を?除隊"
    r"|経験あり|経験[0-9一二三四五六七八九十]+年|に入庁|入庁)(?!者)"
    rf"|{_PUBLIC_PAST_NAME}(?:より|から)(?:の)?出向"
)
# 機関名・職名のすぐ前後に、仕事の対象であることを示す語がある形。
_PUBLIC_OBJECT_AFTER = re.compile(
    r"^[^\s()、,/／]{0,12}?(?:向け|向|担当|営業|顧客|案件|支援|領域|対応|提案|コンサル|入札|セールス"
    r"|納入|納品|プロジェクト|pj|pm|業界|市場|マーケット|分野|試験|予備校|講座|講師|対策|受験|専門|研修"
    r"|教育|募集|広告|saas|sales|account|出向|派遣|常駐|受託|委託|請負|共済|保険|ローン|との|連携|協働"
    r"|共同|折衝|渉外|窓口|関連|取引|システム|開発|更改|調達|発注|工事|新築|申請|申告|届出|出願|手続"
    r"|登記|提出|求人|掲載|転職|宿舎|サポート|製品|艦艇|車両|機の|事業部|事業本部|本部|ソリューション"
    r"|ビジネス|推進|dx|部門|ユニット|セクター|公共|労務|管理|代行|計算|運用|秘書)"
)
_PUBLIC_OBJECT_PAREN = re.compile(r"^\s*\([^()]*(?:派遣|委託|常駐|請負|受託|出向|向け|担当)")
_PUBLIC_OBJECT_BEFORE = re.compile(r"(?:顧客|客先|発注者?|委託元|取引先|担当|派遣先|出向先|常駐先|派遣)\s*[:：]?\s*$")
_PUBLIC_MENTION_RE = re.compile(rf"{_PUBLIC_BODY}|{_PUBLIC_ROLE}|{_PUBLIC_GENERIC}")


def _is_object(text: str, start: int, end: int) -> bool:
    """text[start:end] の機関名・職名が、仕事の対象（顧客・担当先・派遣先）として書かれているか。"""
    after = text[end:]
    return bool(_PUBLIC_OBJECT_AFTER.match(after) or _PUBLIC_OBJECT_PAREN.match(after)
                or _PUBLIC_OBJECT_BEFORE.search(text[:start]))


def _public_past(t: str) -> bool:
    """役職欄に本人の公務員の経歴が書かれているか。"""
    if any(not _is_object(t, m.start(), m.end()) for m in _PAST_PREFIX_RE.finditer(t)):
        return True
    return bool(_PAST_SUFFIX_RE.search(t))


def _public_mention(text: str) -> bool:
    """text に公的機関名・公務員の職名が、仕事の対象としてではなく出ているか。"""
    head = text.split()[0] if text.split() else ""
    if _PUBLIC_PLACE_RE.match(head):
        return True   # 「横浜市 健康福祉局 主事」
    return any(not _is_object(text, m.start(), m.end()) for m in _PUBLIC_MENTION_RE.finditer(text))


def _name_is_public(name: str) -> bool:
    """出向元・派遣元として書かれた名前が公的機関か。"""
    return bool(_PUBLIC_START_RE.match(name) or _PUBLIC_BODY_RE.search(name)
                or _PUBLIC_PLACE_RE.match(name) or _PUBLIC_GENERIC_RE.match(name)) \
        and not _LEGAL_ENTITY_RE.search(name)


def _secondment(text: str) -> str | None:
    """出向・派遣・委託などの付記から雇い主を読む。'public' / 'private' / None（付記なし・元の組織のまま）。"""
    for m in _FROM_SECONDMENT_RE.finditer(text):
        return "public" if _name_is_public(m.group(1) or m.group(2)) else "private"
    if _TO_SECONDMENT_RE.search(text):
        return None
    return "private" if _PRIVATE_STATUS_RE.search(text) else None


def _company_is_public(c: str) -> bool:
    """会社名欄が公的機関か。c は正規化済み。"""
    body, parens = _split_paren(c)
    body = body.strip()
    head = next(iter(re.split(r"[\s/／、,・]+", body)), "")
    if _PUBLIC_UNION_RE.search(head) or _PUBLIC_START_RE.match(head):
        return True   # 「文部科学省 国立大学法人支援課」「金融庁 監督局銀行第一課」
    # 「社会保険庁（現 日本年金機構）」の今の名前や、兼務・派遣先の付記は法人格の判定に使わない。
    notes = [p for p in parens
             if not re.match(r"\s*(?:現|旧|現在の)", p) and not _TO_SECONDMENT_RE.search(p)]
    if _LEGAL_ENTITY_RE.search(body) or any(_LEGAL_ENTITY_RE.search(p) for p in notes):
        return False
    if _PRIVATE_HEAD_RE.search(head):
        return False
    hidden = bool(_HIDDEN_COMPANY_RE.match(body)) and not any(_PRIVATE_NOTE_RE.search(p) for p in parens)
    if _PUBLIC_GENERIC_RE.match(body) or (hidden and any(_PUBLIC_GENERIC_RE.match(p.strip()) for p in parens)):
        return True
    if (_PUBLIC_PLACE_RE.match(head) or _PUBLIC_BODY_RE.search(head)
            or re.fullmatch(r"(?:[^\s()]{0,8}?(?:都|道|府|県|市|区|町|村))?立", head)):
        return True
    for p in parens:
        p = p.strip()
        if (_FACILITY_HEAD_RE.search(head) and re.fullmatch(
                r"[^\s()]{0,8}?(?:都|道|府|県|市|区|町|村)立|公立", p)):
            return True   # 「希望ヶ丘高等学校（県立）」「みなと中学校（横浜市立）」
        if _PUBLIC_BODY_RE.search(p) and (hidden or (_ORG_UNIT_HEAD_RE.search(head)
                                                      and not _PRIVATE_HEAD_RE.search(head))):
            return True   # 「第1空挺団（陸上自衛隊）」「非公開（市役所）」
    return bool(_PUBLIC_EN_RE.search(c))


def is_public_sector(company: str, title: str = "") -> bool:
    """その職歴が公務員（官公庁・自治体・警察・消防・自衛隊・公立学校/病院など）か。

    役職欄（社内経歴の部署名を含む）は、本人の経歴の付記・公務員にしか無い職と、勤務先を伏せた人の
    機関名・職名だけを見る。民間の会社名に続く役職欄の機関名は顧客・発注者・担当先として頻出するため数えない。
    """
    c, t = _norm(company), _norm(title)
    if _public_past(t):
        return True
    status = _secondment(t) or _secondment(c)
    if status is not None:
        return status == "public" and (_company_is_public(c) or _name_is_public(c))
    legal = bool(_LEGAL_ENTITY_RE.search(_split_paren(c)[0]))
    if not legal and any(not _is_object(t, m.start(), m.end()) for m in _PUBLIC_OFFICE_RE.finditer(t)):
        return True
    if not legal and re.search(r"(?:衆議院|参議院|国会)議員", c) and re.search(r"第[一二12]秘書", t):
        return True   # 国会議員事務所の第一・第二秘書は公設秘書
    if _company_is_public(c):
        return True
    body, parens = _split_paren(c)
    if _HIDDEN_COMPANY_RE.match(body.strip()) and not any(_PRIVATE_NOTE_RE.search(p) for p in parens):
        return _public_mention(t)
    return False


def has_public_sector_career(candidate: Candidate) -> bool:
    """キャリアの中に公務員の職歴があるか（社内経歴の役職・部署名も見る）。"""
    for e in candidate.employments:
        if is_public_sector(e.company, e.title):
            return True
        if any(is_public_sector(e.company, s.name) for s in e.segments):
            return True
    if candidate.employments:
        return False
    # 職歴が取れない取り込み経路では、現職と過去の会社名で判定する。
    jobs = [(candidate.current_company, candidate.current_title),
            *[(n, "") for n in candidate.prior_companies]]
    return any(is_public_sector(c, t) for c, t in jobs if c or t)
