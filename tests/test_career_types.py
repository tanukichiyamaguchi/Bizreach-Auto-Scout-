"""職歴の種類による除外（career_types.py）のテスト。

運用者指示（2026-10）: 通常スカウト・ピックアップの両方で次を除外する。
- 現職が個人事業主
- これまでのキャリアの半分以上が契約社員
- これまでのキャリアの半分以上が工場・生産部門

*_CASES は、会社名・役職欄に実際に書かれる言い回しを「該当／紛らわしいが非該当」の両面で
集めた回帰用コーパス。判定は会社名・役職・社内の役職/部署名だけで行い、職務内容は見ない。
"""

from __future__ import annotations

import pytest

from bizreach_scout.career_types import (
    contract_ratio,
    factory_ratio,
    is_contract,
    is_current_self_employed,
    is_factory_job,
    is_factory_role,
    is_self_employed,
)
from bizreach_scout.eligibility import check_eligibility
from bizreach_scout.models import Employment, EmploymentSegment

from .factories import make_candidate

# --- 1. 個人事業主（1社分の職歴として該当するか）--------------------------------
SELF_EMPLOYED_CASES: list[tuple[str, str, bool, str]] = [
    ("個人事業主", "Webデザイナー", True,
     "会社名欄に「個人事業主」と明記されている。"),
    ("フリーランス", "ITコンサルタント", True,
     "フリーランスとして業務委託で就業しており、雇用関係がない。"),
    ("自営業", "代表", True,
     "「自営業」と明記されており、法人格の記載もない。"),
    ("田中デザイン事務所（個人事業）", "代表", True,
     "屋号に「（個人事業）」とあり、開業届の記載もあるので個人事業主。"),
    ("フリーランス（個人事業主）", "フロントエンドエンジニア", True,
     "会社名欄に「個人事業主」と明記。準委任契約で企業に雇用されていない。"),
    ("株式会社ノヴァテック（業務委託）", "マーケター（業務委託）", True,
     "境界例。会社名欄は委託先の法人だが、本人は雇用ではなく個人事業主として契約している。社名だけで「法人＝会社員」と判定すると漏れる。"),
    ("山本税理士事務所", "所長税理士", True,
     "税理士法人ではなく個人開業の事務所の所長なので本人が個人事業主。文中の「個人事業主」は顧客を指すが、本人も該当する。"),
    ("-", "フリーランス翻訳者", True,
     "会社名欄は空欄（-）だが、ポジション欄に「フリーランス」と明記されている。"),
    ("居酒屋 鈴の家（自営）", "店主", True,
     "「自営」「個人経営」とあり、法人格のない飲食店経営。"),
    ("個人", "営業代行", True,
     "会社名欄が「個人」で、独立後に業務委託で働く個人事業主。"),
    ("フリーランス", "人事・採用コンサルタント", True,
     "フリーランスとして複数社から業務委託で受注しており、個人事業主にあたる。"),
    ("佐藤行政書士事務所", "代表行政書士", True,
     "行政書士法人ではない個人事務所の代表なので個人事業主。"),
    ("Studio K（屋号）", "フォトグラファー", True,
     "「屋号」の明記と「フリーで活動」から、法人ではない個人事業。"),
    ("自営（農業）", "代表", True,
     "法人格のない個人の農業経営は個人事業主。「生産」の文脈だが工場・生産部門（factory）ではない点にも注意。"),
    ("個人事業主", "配達パートナー（ギグワーク）", True,
     "プラットフォーム経由でも雇用契約ではなく、個人事業主としての請負。"),
    ("フリーランス", "動画編集・映像クリエイター", True,
     "フリーランスとして複数の取引先から受託している。"),
    ("高橋保険サービス（個人代理店）", "代表", True,
     "「個人代理店」とあり、法人格のない個人事業。"),
    ("独立・開業", "キャリアコンサルタント", True,
     "会社名欄が「独立・開業」で、職務内容に「個人事業として開業」と明記されている。"),
    ("ITフリーランス", "インフラエンジニア", True,
     "境界例。エージェント経由・客先常駐でも本人はフリーランス（個人事業主）。SES企業の正社員として常駐する場合とは異なる。"),
    ("株式会社ミライデザイン", "代表取締役", False,
     "株式会社の代表取締役は法人の経営者で、個人事業主ではない。"),
    ("合同会社ブライトパス", "代表社員", False,
     "合同会社は法人であり、その代表社員は個人事業主ではない。"),
    ("一般社団法人日本キャリア支援協会", "代表理事", False,
     "一般社団法人は法人で、その理事は個人事業主ではない。"),
    ("株式会社ワークマッチ", "法人営業", False,
     "「フリーランス」はサービスの対象を指す言葉で、本人は株式会社の会社員。"),
    ("株式会社クラウド会計ラボ", "インサイドセールス", False,
     "「個人事業主」は顧客層を指す言葉で、本人の就業形態ではない。"),
    ("株式会社たなか屋", "店長", False,
     "屋号のような社名だが株式会社であり、本人はそこに雇用されている。"),
    ("株式会社スズキ商店", "営業課長", False,
     "「〇〇商店」は個人商店のような名前だが、株式会社の社員。"),
    ("株式会社アトリエ・ソラ", "代表取締役（一人社長）", False,
     "境界例。従業員のいない一人社長でも法人格がある。働き方がフリーランスに近くても、ルール上は個人事業主ではない。"),
    ("税理士法人みらい会計", "税理士", False,
     "税理士法人に勤務している。「個人事業主」は顧客を指す言葉（山本税理士事務所のケースと対比）。"),
    ("株式会社フリーランスエージェント", "キャリアアドバイザー", False,
     "社名と職務内容に「フリーランス」が入るが、本人は株式会社の社員。"),
    ("株式会社サンライズ", "調達部 業務委託管理担当", False,
     "業務委託・フリーランスは管理する相手を指す言葉で、本人は社員。"),
    ("株式会社ネクストスタート", "創業支援コンサルタント", False,
     "「独立・開業」は顧客側の文脈で、本人は株式会社の社員。"),
    ("有限会社佐藤工務店", "代表取締役", False,
     "有限会社は法人。家業でも法人化されているので個人事業主ではない。"),
    ("株式会社山田商店（家業）", "取締役", False,
     "「家業」とあるが株式会社であり、自営業ではない。"),
    ("株式会社ソロワークス", "Webディレクター", False,
     "境界例。本業は正社員。副業でのフリーランス活動があっても、この職歴の就業形態は個人事業主にならない。"),
    ("弁護士法人青葉総合法律事務所", "アソシエイト弁護士", False,
     "弁護士法人に所属しており、本人は個人事業主ではない。"),
    ("医療法人社団健生会 やまだクリニック", "事務長", False,
     "医療法人に勤務している。院長の個人経営クリニックではない。"),
    ("株式会社マネーラボ", "編集者", False,
     "「個人」は読者層を指す言葉で、本人は株式会社の社員。"),
    ("NPO法人こども未来ネット", "事務局長", False,
     "NPO法人は法人で、その職員は個人事業主ではない。"),
    ("一般社団法人フリーランス支援機構", "事務局スタッフ", False,
     "法人の職員で、「フリーランス」は会員を指す言葉。"),
    ("株式会社リクルート", "営業", False, "普通の会社員"),
    ("トヨタ自動車", "営業", False, "法人格を省略して書く人"),
    ("-", "営業", False, "社名を伏せているだけ"),
    ("非公開", "経営企画", False, "社名非公開"),
    ("某大手メーカー", "生産技術", False, "社名ぼかし"),
    ("株式会社チェーンストア", "新規開業店舗の店長", False, "店舗の開業"),
    ("ABC Partners", "代表取締役", False, "取締役＝株式会社"),
    ("山田事務所", "スタッフ", False, "事務所の従業員"),
    ("医療法人やまだクリニック", "院長", False, "医療法人の院長"),
    ("Freelance", "Designer", True, "英語"),
    ("Self-employed", "Consultant", True, "英語"),
    ("個人", "", True, "会社名が個人"),
    ("やまだクリニック", "院長", True, "個人開業医"),
    ("株式会社X（フリーランス）", "エンジニア", True, "括弧書きの働き方"),
    ("株式会社フリーランス協会", "事務局", False, "社名の一部"),
]

# --- 2. 契約社員 -----------------------------------------------------------------
CONTRACT_CASES: list[tuple[str, str, bool, str]] = [
    ("株式会社ABC商事", "営業事務（契約社員）", True,
     "ポジション欄に「契約社員」と明記されている。"),
    ("株式会社グローバルリンク", "契約社員", True,
     "ポジション欄がそのまま「契約社員」。"),
    ("株式会社東都銀行", "窓口テラー（契約社員）", True,
     "ポジション欄に「契約社員」と明記されている。"),
    ("株式会社ミナト物流", "物流センター事務（契約社員・有期雇用）", True,
     "「契約社員・有期雇用」と明記されている。"),
    ("株式会社テックス", "社内SE（契約）", True,
     "「（契約）」の略記と「1年更新」から、有期の契約社員と判断できる。"),
    ("株式会社ホテルグランデ", "フロントスタッフ（契約社員）", True,
     "ポジション欄に「契約社員」と明記されている。"),
    ("株式会社サクラ通信 カスタマーセンター", "スーパーバイザー（契約社員）", True,
     "管理的な役割でも、雇用形態は「契約社員」と明記されている。"),
    ("国立大学法人北陸総合大学", "研究支援 契約職員", True,
     "大学や公的機関の「契約職員」は契約社員と同じ有期雇用。任期もある。"),
    ("株式会社ライフケア", "専門契約社員（年俸制）", True,
     "「専門契約社員」で、年俸制でも雇用形態は契約社員。"),
    ("Vertex Japan合同会社", "Contract Employee / マーケティングスペシャリスト", True,
     "Contract Employee は契約社員を意味し、1年契約とも書かれている。合同会社だが本人は雇用されており、self_employed には当たらない。"),
    ("株式会社東洋電子", "嘱託社員", True,
     "境界例。嘱託社員は有期の契約社員の一種（定年後再雇用）として該当にする。"),
    ("東海オートモーティブ株式会社", "期間従業員", True,
     "境界例。期間従業員（期間工）は有期雇用の契約社員にあたる。factory ルールにも同時に該当する。"),
    ("株式会社フロンティアテレビ", "番組制作AD（契約社員）", True,
     "ポジション欄に「契約社員」と明記されている。"),
    ("株式会社ブルームアパレル", "販売スタッフ（契約社員）", True,
     "ポジション欄に「契約社員」と明記されている。"),
    ("株式会社ネクスタ", "インサイドセールス（契約社員／正社員登用制度あり）", True,
     "「正社員登用制度あり」は制度の説明で、実際には登用されておらず契約社員のまま。"),
    ("独立行政法人国際交流推進機構", "契約職員（事業推進課）", True,
     "独立行政法人の「契約職員」は有期の契約雇用。"),
    ("株式会社データワン", "データ入力・集計（契約社員）", True,
     "ポジション欄に「契約社員」と明記されている。"),
    ("株式会社シティ百貨店", "外商アシスタント（有期契約社員）", True,
     "「有期契約社員」と明記されている。"),
    ("株式会社リーガルテック", "法務アシスタント（契約社員）", True,
     "職務内容に「契約書」が出てくるが、それとは別に雇用形態も「契約社員」と明記されている。キーワードの混在に注意。"),
    ("株式会社大和システムズ", "契約管理部 主任", False,
     "「契約」は部署名で、雇用形態ではない。"),
    ("株式会社グリーンホールディングス", "法務部 契約書レビュー担当", False,
     "「契約書」は担当業務を指す言葉で、雇用形態ではない。"),
    ("株式会社アークメディア", "総務部", False,
     "「業務委託契約」は担当業務で、本人の雇用形態ではない。"),
    ("株式会社ネクスタ", "インサイドセールス（2021年4月 正社員登用）", False,
     "境界例。契約社員の期間を含むが、現在は正社員に登用済み。この職歴は正社員として扱う（契約社員期間が在籍の大半を占める場合に期間で按分するかは別途検討）。"),
    ("株式会社キャリアブリッジスタッフ（人材派遣）", "派遣営業（正社員）", False,
     "派遣会社の正社員として営業をしている。「契約更新」は業務の内容。"),
    ("株式会社プライムホーム", "賃貸仲介営業", False,
     "「契約」は業務上の成果を指す言葉で、雇用形態ではない。"),
    ("株式会社ジェイ・リテール", "人事部 労務担当（正社員）", False,
     "「契約社員」は管理対象の従業員で、本人は正社員と明記されている。"),
    ("株式会社コードワークス", "SESエンジニア（客先常駐）", False,
     "境界例。準委任契約は自社と客先の契約で、本人は自社の正社員（雇用形態の明記がない場合は正社員とみなす前提）。"),
    ("株式会社ABCスタッフ（派遣先：株式会社XYZ）", "派遣社員（一般事務）", False,
     "境界例。派遣社員は派遣元に雇用される形態で、契約社員とは区別する。非正規としてまとめて除外したいなら別ルールが必要。"),
    ("株式会社カフェ・ド・ミル", "アルバイト（ホールスタッフ）", False,
     "境界例。アルバイト・パートは契約社員とは別の雇用形態として扱う。"),
    ("株式会社メディカルパートナーズ", "コントラクトMR", False,
     "「コントラクト」はCSOという業態の呼び名で、雇用形態は正社員。"),
    ("株式会社日本エンジニアリング建設", "コントラクトマネージャー", False,
     "コントラクトマネージャーは契約管理の職種名で、雇用形態ではない。"),
    ("株式会社エナジープラス", "法人営業", False,
     "「契約」は営業の対象を指す言葉で、雇用形態ではない。"),
    ("株式会社アドバンスクラウド", "カスタマーサクセス", False,
     "「年間契約」は商材の契約形態で、本人の雇用形態ではない。"),
    ("株式会社リンク商事", "営業アシスタント（無期転換社員）", False,
     "境界例。無期転換の後は有期の契約社員ではない。ただし処遇が非正規のままの場合もあり、運用で判断が分かれうる。"),
    ("東日本損害保険株式会社", "保険契約保全担当", False,
     "「契約」は担当業務を指す言葉で、雇用形態ではない。"),
    ("株式会社グローイング", "営業マネージャー", False,
     "「契約社員」は部下を指す言葉で、本人の雇用形態ではない。"),
    ("フリーランス", "業務委託エンジニア", False,
     "業務委託は雇用ではないので契約社員には当たらない。self_employed 側で該当する。"),
    ("株式会社ジョブライン", "キャリアアドバイザー", False,
     "「契約社員」は支援対象の求職者を指す言葉で、本人の雇用形態ではない。"),
    ("株式会社X", "人事部 契約社員の採用担当", False, "仕事の対象"),
    ("株式会社X", "契約営業", False, "契約は業務"),
    ("株式会社X", "Contract Manager", False, "契約管理職"),
    ("株式会社X", "派遣社員", False, "派遣は契約社員とは別"),
    ("株式会社X", "アルバイト", False, "アルバイトは別"),
    ("株式会社X（契約社員）", "営業", True, "社名側の括弧"),
    ("株式会社X", "嘱託（再雇用）", True, "嘱託"),
]

# --- 3. 工場・生産部門 -----------------------------------------------------------
FACTORY_CASES: list[tuple[str, str, bool, str]] = [
    ("株式会社中部精機製作所 豊橋工場", "製造課 班長", True,
     "工場の製造課で、生産ラインの現場監督をしている。"),
    ("みなみ食品株式会社", "生産管理課", True,
     "生産管理は製造業の生産側の機能。"),
    ("株式会社北斗電機", "生産技術エンジニア", True,
     "生産技術は量産ラインを担う生産部門。"),
    ("株式会社北斗電機 長野工場", "品質管理課", True,
     "工場の品質管理で、製造の現場側。"),
    ("太平洋化学株式会社 鹿島事業所", "製造オペレーター（4組3交替）", True,
     "プラントで交替勤務の製造オペレーターをしており、生産現場そのもの。"),
    ("株式会社ヤマト金属工業", "工場長", True,
     "工場長は工場・生産部門のトップ。"),
    ("東海オートモーティブ株式会社", "期間従業員", True,
     "組立ラインの作業者で生産現場。雇用形態は有期なので contract ルールにも同時に該当する。"),
    ("日本ファーマ製薬株式会社 静岡工場", "製剤課", True,
     "医薬品工場の製造工程を担当している。"),
    ("株式会社シリコンテック", "製造部 主任（半導体前工程）", True,
     "半導体の製造部で、生産現場にあたる。"),
    ("株式会社大東製紙", "設備保全課", True,
     "工場の生産設備を保全する部署で、生産側の部門。"),
    ("株式会社北斗電機", "品質保証部 主任", True,
     "境界例。品質保証は判断が分かれうるが、工程監査や是正処置など製造現場に近い業務なので製造業の生産側とみなす。"),
    ("株式会社中部精機製作所 豊橋工場", "総務課", True,
     "境界例。職種は総務だが所属は工場（事業所）なので、「工場」に該当とした。所属でなく職種で判定する運用なら非該当になりうる。"),
    ("株式会社大東製紙 富士工場", "資材課（購買）", True,
     "境界例。工場に所属する購買で、生産に直結している。本社調達部門のバイヤーとは区別する。"),
    ("株式会社ミヤビ印刷 川口工場", "印刷オペレーター", True,
     "印刷工場で機械を操作するオペレーターで、製造現場にあたる。"),
    ("株式会社オオサワ化成", "SCM部 生産計画担当", True,
     "境界例。本社勤務だが、生産計画は生産管理の機能なので生産部門に該当。"),
    ("株式会社ニッポンベーカリー", "パート（ライン作業）", True,
     "食品工場の製造ライン作業。雇用形態がパートでも factory の判定には影響しない。"),
    ("株式会社グルメキッチン セントラルキッチン", "調理スタッフ", True,
     "境界例。セントラルキッチンは実質的に食品製造工場。店舗での調理とは区別する。"),
    ("東日本製鉄株式会社 君津製鉄所", "製鋼部 操業スタッフ", True,
     "製鉄所の操業部門で、生産現場にあたる。"),
    ("株式会社北斗電機 長野工場", "IE・工程改善担当", True,
     "工場に所属して生産ラインを改善しており、生産側の機能。"),
    ("株式会社サンエイ電子", "出荷検査員", True,
     "製品の検査工程で、製造業の生産側（品質管理）。"),
    ("株式会社東洋機械商事", "法人営業", False,
     "製造業は顧客で、本人は商社の営業。"),
    ("株式会社中部精機製作所", "本社 経理部", False,
     "社名は製作所だが、本社の経理で生産部門ではない。"),
    ("株式会社ファクトリーOS", "カスタマーサクセス", False,
     "工場はSaaSの顧客。社名や職務内容に「工場」「生産管理」が入るが、本人はIT企業の社員。"),
    ("株式会社ワークスマート", "生産性向上コンサルタント", False,
     "「生産性」は業務効率の意味で、生産部門とは関係ない。"),
    ("株式会社マネジメントソリューションズ", "シニアコンサルタント", False,
     "境界例。工場はクライアントで、本人はコンサルティングファームに所属している。"),
    ("みなみ食品株式会社", "商品企画・マーケティング", False,
     "食品メーカーの企画・マーケティング部門で、生産側ではない。"),
    ("株式会社ヤマト金属工業", "本社人事部 採用担当", False,
     "工場の人材を採用しているが、所属は本社の人事部。"),
    ("株式会社北斗電機", "研究開発センター 研究員", False,
     "境界例。メーカー勤務でも、研究開発は生産側ではない。"),
    ("株式会社北斗電機", "機械設計エンジニア", False,
     "境界例。設計開発は製品開発部門で、生産現場ではない（生産技術とは区別する）。"),
    ("株式会社オートメーションジャパン", "技術営業", False,
     "工場は提案先で、本人の職種は営業。"),
    ("株式会社ものづくりキャリア", "リクルーティングアドバイザー", False,
     "人材紹介会社の営業で、製造業は顧客。"),
    ("株式会社イーストロジ", "物流センター 庫内作業リーダー", False,
     "境界例。現場作業ではあるが、物流倉庫は工場・生産部門ではない。"),
    ("株式会社オオサワ化成", "本社 調達本部 バイヤー", False,
     "境界例。製造業の購買だが、本社の調達機能で工場には所属していない（工場資材課のケースと対比）。"),
    ("株式会社クラウドナイン", "QAエンジニア", False,
     "品質保証という名前だがソフトウェアのQAで、製造業の生産側ではない。"),
    ("大成建工株式会社", "施工管理", False,
     "工場は施工の対象物で、本人は建設会社の施工管理。"),
    ("株式会社DXパートナーズ", "ITコンサルタント", False,
     "製造業DXのコンサルで、工場は顧客。"),
    ("株式会社グリーンフレッシュ", "青果バイヤー", False,
     "「生産者」は仕入れ先を指す言葉で、本人は卸売のバイヤー。"),
    ("株式会社ピクセルプロダクション", "映像制作プロデューサー", False,
     "「制作」「プロダクション」は製造・生産部門ではない。"),
    ("日本ファーマ製薬株式会社", "広報部", False,
     "製薬メーカーの広報部門で、「工場」は広報の対象。"),
    ("株式会社クオリティシステムズ", "インサイドセールス", False,
     "「品質管理」は商材の名前で、本人の職種は営業。"),
    ("株式会社グルメキッチン", "店舗 調理スタッフ", False,
     "境界例。飲食店の店舗での調理は工場ではない（セントラルキッチンのケースと対比）。"),
    ("トヨタ自動車", "生産技術開発", True, "強い語＋開発"),
    ("トヨタ自動車", "生産技術（設備設計）", True, "強い語＋設計"),
    ("株式会社X", "ラインマネージャー", False, "人事用語のライン"),
    ("株式会社X", "コールセンターオペレーター", False, "コールセンター"),
    ("株式会社X", "臨床検査技師", False, "医療の検査"),
    ("株式会社Web工場", "デザイナー", False, "社名に工場"),
    ("豊橋工場", "総務", True, "所在地だけ"),
    ("株式会社X", "製造小売（SPA）の店長", False, "SPA"),
    ("株式会社X", "品質保証（ソフトウェア）", False, "ソフトのQA"),
    ("株式会社X", "製造業向けコンサルタント", False, "顧客が製造業"),
    ("株式会社X", "生産管理システム導入PM", False, "ITプロジェクト"),
    ("株式会社X", "印刷オペレーター", True, "印刷工"),
    ("株式会社X", "製品設計（工場向け）", False, "向け"),
    ("株式会社X 本社", "生産管理部", True, "本社でも生産管理"),
    ("株式会社X", "製造業務", True, "製造の職種名"),
    ("株式会社X", "製造作業員", True, "製造の職種名"),
    ("株式会社X", "製造担当", True, "製造の職種名"),
    ("株式会社X", "製造リーダー", True, "製造の職種名"),
    ("株式会社X", "製造マネージャー", True, "製造の職種名"),
    ("株式会社X", "製造業向け営業", False, "顧客が製造業"),
    ("株式会社X", "製造業界担当 RA", False, "人材紹介の担当業界"),
]


def _ids(cases):
    return [f"{c[:10]}/{t[:10]}" for c, t, _, _ in cases]


@pytest.mark.parametrize("company,title,expected,why", SELF_EMPLOYED_CASES,
                         ids=_ids(SELF_EMPLOYED_CASES))
def test_self_employed_cases(company, title, expected, why):
    assert is_self_employed(company, title) is expected, why


@pytest.mark.parametrize("company,title,expected,why", CONTRACT_CASES, ids=_ids(CONTRACT_CASES))
def test_contract_cases(company, title, expected, why):
    assert is_contract(f"{company} {title}") is expected, why


@pytest.mark.parametrize("company,title,expected,why", FACTORY_CASES, ids=_ids(FACTORY_CASES))
def test_factory_cases(company, title, expected, why):
    assert is_factory_job(company, title) is expected, why


def test_employment_type_field_is_used_when_present():
    """レジュメに雇用形態の欄があればその値も使う（本番では未確認のため候補キーで受ける）。"""
    assert is_self_employed("株式会社X", "エンジニア", "Freelance")
    assert is_self_employed("株式会社X", "エンジニア", "業務委託")
    assert not is_self_employed("株式会社X", "エンジニア", "FullTime")
    assert is_contract("株式会社X 営業", "Contract")
    assert is_contract("株式会社X 営業", "契約社員")
    assert not is_contract("株式会社X 営業", "正社員")


# --- 期間の割合（キャリアの半分以上）---------------------------------------------

def _job(company, title, years, *, current=False, segments=()):
    return Employment(company=company, title=title, years=years, is_current=current,
                      segments=[EmploymentSegment(name=n, years=y) for n, y in segments])


def test_contract_ratio_is_weighted_by_tenure_not_job_count():
    """社数ではなく在籍期間で数える: 契約社員3社(各1年) vs 正社員1社(5年) → 3/8。"""
    c = make_candidate(employments=[
        _job("株式会社A", "営業", 5.0, current=True),
        _job("株式会社B", "営業（契約社員）", 1.0),
        _job("株式会社C", "営業（契約社員）", 1.0),
        _job("株式会社D", "営業（契約社員）", 1.0),
    ])
    assert contract_ratio(c) == pytest.approx(3 / 8)
    # 4社なので転職回数ルールには当たりうるが、契約社員を理由には除外しない。
    assert not any("契約社員" in r for r in check_eligibility(c).failed)


def test_contract_half_or_more_is_excluded():
    """「半分以上」= ちょうど50%も除外する。"""
    c = make_candidate(employments=[
        _job("株式会社A", "営業", 3.0, current=True),
        _job("株式会社B", "事務（契約社員）", 3.0),
    ])
    assert contract_ratio(c) == pytest.approx(0.5)
    failed = check_eligibility(c).failed
    assert "キャリアの50%が契約社員のため対象外（50%以上）" in failed


def test_contract_just_under_half_is_not_excluded():
    c = make_candidate(employments=[
        _job("株式会社A", "営業", 3.1, current=True),
        _job("株式会社B", "事務（契約社員）", 3.0),
    ])
    assert not any("契約社員" in r for r in check_eligibility(c).failed)


def test_factory_ratio_counts_only_factory_segments_within_a_company():
    """社内で部署が変わった人は、その期間（companyCareers）単位で数える。

    トヨタで生産技術3年→経営企画7年: 工場・生産部門は3/10で対象のまま。
    """
    c = make_candidate(employments=[
        _job("トヨタ自動車株式会社", "経営企画部 課長", 10.0, current=True,
             segments=[("生産技術部", 3.0), ("経営企画部", 7.0)]),
    ])
    assert factory_ratio(c) == pytest.approx(0.3)
    assert not any("工場" in r for r in check_eligibility(c).failed)


def test_factory_majority_is_excluded():
    c = make_candidate(employments=[
        _job("株式会社A", "法人営業", 4.0, current=True),
        _job("株式会社北斗電機 長野工場", "品質管理課", 6.0),
    ])
    assert factory_ratio(c) == pytest.approx(0.6)
    assert "キャリアの60%が工場・生産部門のため対象外（50%以上）" in check_eligibility(c).failed


def test_overlapping_segments_do_not_exceed_the_job():
    c = make_candidate(employments=[
        _job("株式会社A", "", 2.0, current=True,
             segments=[("製造課", 2.0), ("製造課 班長（兼務）", 2.0)]),
        _job("株式会社B", "営業", 2.0),
    ])
    assert factory_ratio(c) == pytest.approx(0.5)


def test_unknown_durations_do_not_trigger_exclusion():
    """期間が分からない職歴は分母・分子の両方から外し、全部不明なら判定しない。"""
    c = make_candidate(employments=[_job("株式会社A", "事務（契約社員）", None, current=True)])
    assert contract_ratio(c) is None
    assert not any("契約社員" in r for r in check_eligibility(c).failed)


# --- 現職が個人事業主 -------------------------------------------------------------

def test_current_self_employed_is_excluded():
    c = make_candidate(employments=[
        _job("フリーランス", "Webディレクター", 2.0, current=True),
        _job("株式会社A", "Webディレクター", 5.0),
    ])
    assert is_current_self_employed(c)
    assert "現職が個人事業主（フリーランス・自営・業務委託）のため対象外" in check_eligibility(c).failed


def test_past_self_employment_is_not_excluded():
    """過去にフリーランスでも、現職が会社員なら対象（「現職が」の条件）。"""
    c = make_candidate(employments=[
        _job("株式会社A", "Webディレクター", 3.0, current=True),
        _job("フリーランス", "Webディレクター", 2.0),
    ])
    assert not is_current_self_employed(c)
    assert check_eligibility(c).eligible


def test_no_current_job_is_not_treated_as_self_employed():
    """離職中（在籍中の職歴が無い）は「現職が個人事業主」に当たらない。"""
    c = make_candidate(employments=[_job("フリーランス", "デザイナー", 2.0, current=False)])
    assert not is_current_self_employed(c)


def test_falls_back_to_current_company_when_no_employment_history():
    """職歴が取れない取り込み経路（CSV等）は現職の会社名・役職で判定する。"""
    assert is_current_self_employed(make_candidate(current_company="個人事業主", employments=[]))
    assert not is_current_self_employed(make_candidate(employments=[]))


# --- 設定と、通常スカウト／ピックアップの両方に効くこと -----------------------------

def test_rules_apply_to_pickup_too():
    """ピックアップ（apply_status_filter=False）でも同じく除外される。"""
    c = make_candidate(employments=[_job("フリーランス", "PM", 2.0, current=True)])
    assert not check_eligibility(c, apply_status_filter=False).eligible
    assert not check_eligibility(c, apply_status_filter=True).eligible


@pytest.mark.parametrize("key,value,employments,label", [
    ("exclude_current_self_employed", False,
     [("フリーランス", "PM", 2.0, True)], "個人事業主"),
    ("contract_career_ratio_exclude", None,
     [("株式会社A", "事務（契約社員）", 5.0, True)], "契約社員"),
    ("factory_career_ratio_exclude", None,
     [("株式会社A", "製造課", 5.0, True)], "工場・生産部門"),
])
def test_each_rule_can_be_disabled(key, value, employments, label):
    from bizreach_scout.config import scout_rules

    rules = scout_rules()
    elig = {**rules["eligibility"]}
    if value is None:
        elig.pop(key, None)
    else:
        elig[key] = value
    c = make_candidate(employments=[_job(n, t, y, current=cur) for n, t, y, cur in employments])
    assert any(label in r for r in check_eligibility(c).failed)
    assert not any(label in r for r in check_eligibility(c, rules={**rules, "eligibility": elig}).failed)


def test_ratio_threshold_is_validated():
    from pydantic import ValidationError

    from bizreach_scout.rules import validate_rules

    with pytest.raises(ValidationError, match="contract_career_ratio_exclude"):
        validate_rules({"eligibility": {"contract_career_ratio_exclude": 1.5}})
    with pytest.raises(ValidationError, match="factory_career_ratio_exclude"):
        validate_rules({"eligibility": {"factory_career_ratio_exclude": 0}})


def test_role_only_helper():
    assert is_factory_role("生産技術部")
    assert not is_factory_role("経営企画部")


# --- レビューで見つかった誤除外・取りこぼしの実例（2026-10）-----------------------
# 役職欄には顧客層・事業名・担当業務として同じ語が頻出する。自社が欲しい医療系コンサル・
# 金融/SaaS の営業・人材業界の人を「現職が個人事業主」で落とさないことを固定する。
NOT_SELF_EMPLOYED_REVIEW = [
    ("株式会社日本経営", "医業コンサルタント（承継・開業支援）"),
    ("株式会社メディカルサポート", "医療機関向けコンサルタント（M&A・開業支援）"),
    ("オリンパスメディカル株式会社", "個人開業医担当 営業"),
    ("株式会社ABC", "個人開業医向けコンサルティング営業"),
    ("株式会社X", "独立開業支援コンサルタント"),
    ("株式会社X", "フランチャイズ開発（独立・開業支援）"),
    ("株式会社マネーフォワード", "個人事業主・中小企業向けセールス"),
    ("株式会社みずほ銀行", "個人事業主担当 融資営業"),
    ("株式会社freee", "個人事業主の確定申告サポート リーダー"),
    ("株式会社X", "個人事業本部 営業企画部"),
    ("東京海上日動火災保険株式会社", "代理店営業（個人代理店担当）"),
    ("株式会社X", "個人経営店向けPOS営業"),
    ("株式会社X信用金庫", "自営業者・中小企業担当 融資課"),
    ("株式会社X", "マーケティングマネージャー（業務委託から正社員登用）"),
    ("株式会社X", "エンジニアリングマネージャー（フリーランスを経て正社員入社）"),
    ("株式会社ABC（2019年に個人事業から法人化）", "代表取締役"),
    ("合同会社X（フリーランスから法人成り）", "代表社員"),
    ("株式会社X", "代表取締役（個人事業として創業後、法人化）"),
    ("レバテック株式会社", "レバテックフリーランス 法人営業"),
    ("株式会社X", "フリーランスエンジニアのキャリアアドバイザー"),
    ("株式会社X", "人事部 フリーランス・副業人材活用担当"),
    ("株式会社X", "購買部 業務委託・派遣管理"),
    ("株式会社X", "BPO（業務委託）事業部 営業"),
    ("Lancers Inc.", "Business Development, Freelance Platform"),
    ("鈴木税理士事務所", "副所長"),
    ("鈴木税理士事務所", "所長代理"),
    ("鈴木税理士事務所", "経営者支援コンサルタント"),
    ("山田会計事務所", "医業コンサルタント（開業支援担当）"),
    ("やまだクリニック", "副院長"),
    ("やまだクリニック", "院長秘書"),
    ("さくら内科クリニック", "院長補佐（経営企画）"),
    ("三菱商事 ジャカルタ駐在員事務所", "所長"),
    ("トヨタ自動車 ワシントン事務所", "所長"),
]
SELF_EMPLOYED_REVIEW = [
    ("フリーランス（主要取引先：株式会社A）", "Webデザイナー"),
    ("フリーランス（法人向けWeb制作）", "デザイナー"),
    ("個人事業主（日本デザイン協会 会員）", "デザイナー"),
    ("フリーランサー", "エンジニア"),
    ("フリーライター", "ライター"),
    ("株式会社X", "フリーライター"),
    ("株式会社X（業務委託契約）", "ディレクター"),
    ("株式会社X", "Webディレクター（業務委託契約にて）"),
    ("株式会社X", "業務委託にてWebディレクション"),
    ("株式会社X【業務委託】", "PM"),
    ("株式会社X / 業務委託", "PM"),
    ("たなか歯科", "院長"),
    ("すずき内科", "院長"),
    ("株式会社X", "フリーランスとして参画"),
]
NOT_CONTRACT_REVIEW = [
    "株式会社X 期間従業員採用担当",
    "株式会社X 契約社員採用担当",
    "株式会社X 人事 契約社員・パートの労務管理",
    "株式会社X 営業（契約社員から正社員登用）",
    "株式会社X 契約社員→正社員",
]
CONTRACT_REVIEW = [
    "株式会社X 事務（契約スタッフ）", "株式会社X 契約雇用", "株式会社X 有期社員",
    "株式会社X 期間契約", "株式会社X 社内SE（契約・1年更新）", "株式会社X 営業【契約】",
    "株式会社X 営業／契約", "Acme Inc. Analyst (Contract)", "Acme Inc. Contract Staff",
    "Acme Inc. Contract Worker", "X市役所 会計年度任用職員", "X県庁 臨時職員",
    "X大学 任期付き研究員",
]
NOT_FACTORY_REVIEW = [
    ("株式会社X", "キャリアアドバイザー（製造領域担当）"),
    ("株式会社X", "製造IT コンサルタント"),
    ("株式会社X", "製造DX推進"),
    ("株式会社X", "スマート工場推進リーダー"),
    ("株式会社 ことば工場", "デザイナー"),
    ("医療法人さくら会 さくら病院", "検査課"),       # 病院の臨床検査（自社が欲しい医療系人材）
    ("株式会社クラウドX", "品質保証部"),              # SaaS の品質保証
]
FACTORY_REVIEW = [
    ("株式会社X", "本社工場 製造課"), ("株式会社X", "工場長（本社工場）"),
    ("株式会社X電機", "生産職"), ("株式会社X", "生産スタッフ"), ("株式会社X", "生産オペレーター"),
    ("株式会社X", "生産G リーダー"), ("株式会社X", "生産グループ"), ("株式会社X", "生産チーム"),
    ("株式会社X", "生産事業部"), ("株式会社X", "生管"), ("株式会社X", "生技"),
    ("株式会社X工業", "品管"), ("株式会社X工業", "品証"),
    ("株式会社X", "製造課 リーダー（顧客対応も担当）"),
    ("株式会社X（豊橋工場）", "総務"), ("トヨタ自動車 (元町工場)", "総務"),
    ("株式会社X", "工員"), ("株式会社X", "技能職"), ("株式会社X", "機械加工"),
    ("株式会社X", "NC旋盤オペレーター"), ("株式会社X", "溶接工"), ("株式会社X", "塗装工"),
    ("株式会社X", "プレス工"), ("株式会社X", "マシンオペレーター"),
    ("Acme Inc.", "Manufacturing Engineer"), ("Acme Inc.", "Production Engineer"),
    ("Acme Inc.", "Plant Manager"), ("Toyota Motor", "Quality Control"),
]


@pytest.mark.parametrize("company,title", NOT_SELF_EMPLOYED_REVIEW)
def test_review_not_self_employed(company, title):
    assert not is_self_employed(company, title)


@pytest.mark.parametrize("company,title", SELF_EMPLOYED_REVIEW)
def test_review_self_employed(company, title):
    assert is_self_employed(company, title)


@pytest.mark.parametrize("text", NOT_CONTRACT_REVIEW)
def test_review_not_contract(text):
    assert not is_contract(text)


@pytest.mark.parametrize("text", CONTRACT_REVIEW)
def test_review_contract(text):
    assert is_contract(text)


@pytest.mark.parametrize("company,title", NOT_FACTORY_REVIEW)
def test_review_not_factory(company, title):
    assert not is_factory_job(company, title)


@pytest.mark.parametrize("company,title", FACTORY_REVIEW)
def test_review_factory(company, title):
    assert is_factory_job(company, title)


def test_exact_half_is_excluded_without_rounding_drift():
    """月数で割合を出すので、0.1年に丸めた年数の足し算で半分を割り込まない。"""
    c = make_candidate(employments=[
        Employment(company="株式会社A", title="営業", years=1.4, months=17, is_current=True),
        Employment(company="株式会社B", title="事務（契約社員）", years=0.6, months=7),
        Employment(company="株式会社C", title="事務（契約社員）", years=0.8, months=10),
    ])
    assert contract_ratio(c) == pytest.approx(17 / 34)
    assert any("契約社員" in r for r in check_eligibility(c).failed)


def test_boolean_ratio_setting_is_rejected():
    from pydantic import ValidationError

    from bizreach_scout.rules import validate_rules

    with pytest.raises(ValidationError, match="contract_career_ratio_exclude"):
        validate_rules({"eligibility": {"contract_career_ratio_exclude": True}})


# --- 2回目のレビュー（2026-10）で見つかった誤除外・取りこぼしの実例 ----------------
# 役職欄の先頭の「開業医」「個人事業主」「フリーランス」は担当する顧客層・事業名（自社が欲しい MR・金融/SaaS 営業・人材業界の人）。
NOT_SELF_EMPLOYED_REVIEW2 = [
    ("中外製薬株式会社", "開業医MR"),
    ("第一三共株式会社", "開業医領域 MR"),
    ("株式会社X", "開業医MR（GP担当）"),
    ("株式会社X", "開業医市場 MR"),
    ("株式会社日本経営", "開業医コンサルタント"),
    ("株式会社X", "開業医専門コンサルタント"),
    ("株式会社マネーフォワード", "個人事業主セールス"),
    ("株式会社freee", "個人事業主セグメント マネージャー"),
    ("株式会社X", "個人事業主領域 インサイドセールス"),
    ("株式会社X銀行", "個人事業主ローン推進"),
    ("株式会社X", "個人事業本部 部長"),
    ("株式会社X", "個人事業部 課長"),
    ("株式会社ギークス", "フリーランスエージェント"),
    ("株式会社X", "フリーランスコーディネーター"),
    ("株式会社X", "フリーランス領域 RA"),
    ("株式会社X", "フリーランスチーム リーダー"),
    ("株式会社X信用金庫", "自営業者融資"),
    ("レバテックフリーランス", "法人営業"),
    ("日本経営（承継・開業支援部）", "コンサルタント"),
    ("X信用金庫 個人事業主ローン推進", "課長"),
    ("freee（個人事業主セグメント）", "PdM"),
]
# 勤務条件を添えた付記・法人格の無い士業/医院/個人店・会社名欄の「フリー」「一人親方」「（独立）」。
SELF_EMPLOYED_REVIEW2 = [
    ("株式会社X（業務委託・週3日）", "PM"),
    ("株式会社X（業務委託／週3日）", "PM"),
    ("株式会社X（フリーランス・業務委託）", "エンジニア"),
    ("株式会社X（業務委託として参画）", "PM"),
    ("株式会社X", "PM（業務委託・週3日）"),
    ("株式会社X", "PM（フリーランス／業務委託）"),
    ("株式会社X 業務委託", "PM"),
    ("居酒屋 鈴の家", "店主"),
    ("スズキ商店", "店主"),
    ("カフェ ひだまり", "店主"),
    ("鈴木中小企業診断士事務所", "代表"),
    ("山田一級建築士事務所", "代表"),
    ("山田法務事務所", "代表"),
    ("山田労務管理事務所", "代表"),
    ("やまだ整体院", "院長"),
    ("やまだ動物病院", "院長"),
    ("山本税理士事務所", "税理士（代表）"),
    ("山本税理士事務所", "所長（税理士）"),
    ("フリー", "Webデザイナー"),
    ("一人親方", "大工"),
    ("ABCデザイン（独立）", "代表"),
    ("ABCデザイン（開業）", "代表"),
]
# 正社員になった経緯の付記・人材紹介や労務で扱う相手・営業の工程としての「契約」。
NOT_CONTRACT_REVIEW2 = [
    ("医療法人X会 X病院", "事務長（契約職員から正職員へ）"),
    ("医療法人X会 X病院", "医事課（契約職員から正職員登用）"),
    ("株式会社X", "MR（契約社員から正社員）"),
    ("株式会社X", "営業（契約社員から正社員へ）"),
    ("株式会社X", "営業（契約社員から正社員に）"),
    ("株式会社X", "営業（契約社員入社後、正社員昇格）"),
    ("株式会社X", "営業（契約社員→2年後正社員化）"),
    ("株式会社X", "法人営業（契約社員として入社、翌年正社員）"),
    ("株式会社X", "法人営業（入社当初は契約社員）"),
    ("株式会社X", "営業（元契約社員）"),
    ("ロバート・ウォルターズ・ジャパン株式会社", "Contract Staffing Manager"),
    ("ヘイズ・スペシャリスト・リクルートメント・ジャパン株式会社", "Senior Consultant, Contract Staffing"),
    ("株式会社X", "派遣・契約社員コーディネーター"),
    ("株式会社X", "契約社員領域 キャリアアドバイザー"),
    ("株式会社X", "期間工・製造派遣 キャリアアドバイザー"),
    ("株式会社X", "期間工専門キャリアアドバイザー"),
    ("株式会社X", "期間従業員領域 RA"),
    ("株式会社X", "人事部 有期契約社員の労務管理担当"),
    ("株式会社X", "人事 契約社員・嘱託社員の労務"),
    ("株式会社X", "フィールドセールス（商談・契約）"),
    ("株式会社X", "インサイドセールス（アポ獲得・商談・契約）"),
    ("株式会社X", "法人営業（提案・契約・導入支援）"),
    ("株式会社X", "保険営業（新規・契約・保全）"),
]
# 期間雇用・有期・特任・任期制・臨時社員・年単位の契約。「正社員登用あり/予定」はまだ契約社員。
CONTRACT_REVIEW2 = [
    ("日本郵便株式会社", "期間雇用社員"),
    ("株式会社X", "期間雇用社員（1年更新）"),
    ("アマゾンジャパン合同会社", "期間雇用"),
    ("株式会社X", "営業（契約社員・正社員登用あり）"),
    ("株式会社X", "営業事務（契約社員／正社員登用予定）"),
    ("株式会社X", "営業（契約社員 ※正社員登用予定）"),
    ("株式会社X", "営業（有期）"),
    ("X大学", "特任研究員"),
    ("X大学", "特任助教"),
    ("X大学", "研究員（任期制）"),
    ("X市役所", "任期制職員"),
    ("株式会社X", "臨時社員"),
    ("株式会社X", "営業（1年契約）"),
    ("Acme Inc.", "Analyst (Fixed-term)"),
]
# 業界・顧客・製品として書かれた「製造」「工場」「製剤」、オンライン等の「ライン」、法人格の略記の社名、
# 生産部門以外の品質管理・工程管理（臨床・ソフト・サービス・建設）、異動前の部署。
NOT_FACTORY_REVIEW2 = [
    ("(株)山田工場", "経理"),
    ("（株）山田工場", "経理部"),
    ("(有)山田工場", "人事部"),
    ("(株)大川製造所", "人事部 採用担当"),
    ("(株)東邦プラント", "経理"),
    ("(株)ことば工場", "デザイナー"),
    ("株式会社北斗化学", "コンプライアンス推進室 ホットライン担当"),
    ("株式会社北斗化学", "内部通報ヘルプライン担当"),
    ("株式会社北斗化学", "コンプライアンス推進（ガイドライン・規程整備）"),
    ("日本ファーマ製薬株式会社", "パイプライン担当"),
    ("株式会社北斗食品", "ECサイト運営（オンライン）"),
    ("株式会社北斗食品", "オンライン 研修講師"),
    ("株式会社北斗工業", "総務部 研修（オンライン）"),
    ("株式会社北斗工業", "購買部 生産部材調達グループ"),
    ("株式会社北斗工業", "生産部材の購買"),
    ("株式会社北斗工業", "資材部（生産部材購買）"),
    ("株式会社北斗工業", "生産材購買"),
    ("株式会社荒川製作所", "半導体製造装置 フィールドサービスエンジニア"),
    ("東京エレクトロ株式会社", "フィールドエンジニア（半導体製造装置）"),
    ("株式会社日本製鋼工業", "射出成形機 サービスエンジニア"),
    ("株式会社牧田工機製作所", "NC工作機械 技術サポート"),
    ("太平洋化学株式会社", "調達部 製造委託先管理"),
    ("株式会社中部精機製作所", "購買課 製造設備の調達"),
    ("株式会社ヤマト金属工業", "経営管理部 製造子会社管理"),
    ("株式会社ヤマト金属工業", "経営戦略室 製造拠点戦略"),
    ("株式会社北斗工業", "海外事業部 製造拠点管理"),
    ("株式会社北斗電機", "ソフト品質保証課"),
    ("株式会社北斗電機", "組込みソフト 品質保証"),
    ("株式会社北斗電機", "品質保証部（ファームウェア）"),
    ("株式会社北斗電機", "ソフトウェア品質保証部"),
    ("株式会社北斗工業", "SAP導入（生産管理）"),
    ("株式会社北斗工業", "ERP 生産管理モジュール担当"),
    ("株式会社北斗工業", "情報企画部 SAP PP担当（生産管理モジュール）"),
    ("株式会社北斗工業", "基幹システム（生産管理）"),
    ("株式会社北斗電機", "コールセンター品質管理"),
    ("株式会社北斗電機", "サービス品質管理"),
    ("みなみ食品株式会社", "物流部 物流品質管理"),
    ("株式会社北斗電機", "データ品質管理担当"),
    ("株式会社北斗電機", "開発工程管理（PMO）"),
    ("株式会社北斗重工業", "設計部 設計工程管理"),
    ("東海オートモーティブ株式会社", "労務課 期間従業員管理"),
    ("東海オートモーティブ株式会社", "期間従業員の労務管理"),
    ("東海オートモーティブ株式会社", "労務 期間従業員"),
    ("東海オートモーティブ株式会社", "人事部 期間従業員管理"),
    ("大和ハウス工業株式会社", "建築設計（物流施設・工場）"),
    ("大和ハウス工業株式会社", "設計部 工場・倉庫設計"),
    ("株式会社ヤマト金属工業", "経営企画部 工場新設プロジェクト"),
    ("株式会社北斗工業", "不動産開発部 工場跡地活用"),
    ("株式会社北斗電機", "工場IoTソリューション開発"),
    ("株式会社北斗電機", "IoT推進室（工場IoT）"),
    ("大和ハウス工業株式会社", "建築営業（工場）"),
    ("Hokuto Industries Inc.", "Finance Manager, Manufacturing"),
    ("Hokuto Industries Inc.", "HR Business Partner - Manufacturing"),
    ("Hokuto Industries Inc.", "Procurement Specialist (Manufacturing)"),
    ("Hokuto Industries Inc.", "Legal Counsel, Manufacturing"),
    ("Hokuto Industries Inc.", "Account Executive - Manufacturing"),
    ("Hokuto Industries Inc.", "Customer Success Manager (Manufacturing)"),
    ("Hokuto Industries Inc.", "カスタマーサクセス (Manufacturing)"),
    ("Hokuto Industries Inc.", "Software Engineer, Manufacturing Systems"),
    ("株式会社北斗プロダクション", "Production Manager"),
    ("前田建設工業株式会社", "工事部 工程管理"),
    ("東亜建設工業株式会社", "土木 工程管理"),
    ("前田建設工業株式会社", "品質管理課"),
    ("前田建設工業株式会社", "施工管理"),
    ("アクセンチュア株式会社", "製造・流通本部 マネジャー"),
    ("アクセンチュア株式会社", "マネジャー（製造・流通本部）"),
    ("株式会社X", "シニアマネージャー（製造・ハイテク）"),
    ("株式会社X", "マネージャー（製造業・生産管理領域）"),
    ("株式会社X", "製造インダストリー マネージャー"),
    ("株式会社セールスフォース・ジャパン", "アカウントエグゼクティブ（製造）"),
    ("株式会社X", "エンタープライズAE（製造・流通）"),
    ("株式会社X", "Account Executive, Manufacturing"),
    ("株式会社X", "Senior Manager, Manufacturing Practice"),
    ("株式会社X証券", "アナリスト（製造セクター）"),
    ("株式会社X証券", "Manufacturing Sector Analyst"),
    ("株式会社X", "投資担当（製造・ヘルスケア）"),
    ("株式会社X", "M&Aアソシエイト（製造案件）"),
    ("株式会社X銀行", "法人融資（製造企業担当）"),
    ("株式会社X銀行", "審査役（製造・建設）"),
    ("中外製薬株式会社", "MR（生物学的製剤）"),
    ("株式会社X", "MR（バイオ製剤担当）"),
    ("株式会社X", "MR（血液製剤）"),
    ("株式会社X", "プロダクトマネージャー（注射製剤）"),
    ("株式会社X", "MR（オンライン担当）"),
    ("株式会社X", "MR（オンライン）"),
    ("株式会社X", "マネージャー（オンライン）"),
    ("株式会社X", "オンライン 事業責任者"),
    ("株式会社X", "プロモーション担当（オンライン・オフライン）"),
    ("株式会社X", "コンプライアンス ホットライン担当"),
    ("株式会社X", "BD（パイプライン担当）"),
    ("株式会社日立製作所 日立総合病院", "臨床検査課"),
    ("トヨタ自動車株式会社 トヨタ記念病院", "検査課"),
    ("トヨタ自動車株式会社 トヨタ記念病院", "品質管理室"),
    ("X製薬株式会社", "臨床開発部 品質管理"),
    ("X製薬株式会社", "信頼性保証部 GCP品質保証"),
    ("東京海上日動火災保険株式会社", "自動車保険 業務品質管理"),
    ("トヨタ自動車株式会社", "生産技術部→経営企画部 課長"),
    ("株式会社X", "経営企画部 課長（生産管理部から異動）"),
    ("株式会社X", "MR（生産本部から異動）"),
    ("(株)ことば工場", "法人営業"),
    ("（株）アイデア工場", "マーケティング"),
    ("(有)アイデア工場", "営業"),
    ("ことば工場", "法人営業"),
]
# 番号付きの生産課・量産技術・加工の課名・オペレータ（長音なし）・製造業の保全/検査・社名に続く工場名・英語の職種名。
FACTORY_REVIEW2 = [
    ("株式会社X", "生産オペレータ"),
    ("株式会社X", "印刷オペレータ"),
    ("株式会社X", "マシンオペレータ"),
    ("株式会社X", "機械オペレータ"),
    ("株式会社X", "プラントオペレータ"),
    ("株式会社X", "装置オペレータ"),
    ("株式会社X", "生産1課"),
    ("株式会社X", "生産第2部"),
    ("株式会社X", "生産二課"),
    ("株式会社X", "生産第一課"),
    ("株式会社X", "生産1部"),
    ("株式会社X電機", "量産技術課"),
    ("株式会社X電機", "量産技術"),
    ("株式会社X", "量産技術部"),
    ("株式会社X", "量産技術課 主任"),
    ("株式会社X", "塗装課"),
    ("株式会社X", "溶接課"),
    ("株式会社X", "プレス課"),
    ("株式会社X", "熱処理課"),
    ("株式会社X", "板金課"),
    ("株式会社X", "ライン工"),
    ("株式会社X", "ライン工（自動車部品）"),
    ("株式会社X", "マシニングオペレーター"),
    ("株式会社X", "マシニングセンタ オペレーター"),
    ("株式会社X製作所", "保全課"),
    ("株式会社X工業", "保全係"),
    ("株式会社X工業", "保全担当"),
    ("株式会社X工業", "検査部"),
    ("株式会社X工業", "検査グループ"),
    ("株式会社X工業", "外観検査"),
    ("株式会社X工業", "製品検査"),
    ("株式会社X", "生産技術部 FAシステム課"),
    ("株式会社X", "生産技術（制御システム設計）"),
    ("日本ファーマ製薬株式会社", "品質管理（QA/QC）"),
    ("日本ファーマ製薬株式会社", "品質保証部（QA/QC）"),
    ("トヨタ自動車株式会社元町工場", "総務"),
    ("Acme Inc.", "Factory Manager"),
    ("Acme Inc.", "Factory Worker"),
    ("Acme Inc.", "Production Staff"),
    ("Acme Inc.", "Production Worker"),
]
# 上の誤除外のうち、eligibility の結論まで確かめるもの（現職5年・1社のみ）。
ELIGIBLE_REVIEW2 = [
    ("(株)山田工場", "経理部"),
    ("株式会社北斗化学", "コンプライアンス推進室 ホットライン担当"),
    ("株式会社北斗工業", "購買部 生産部材調達グループ"),
    ("株式会社北斗工業", "管理本部（社内経歴: 購買部 生産部材グループ 60か月／経営企画部 36か月）"),
    ("株式会社荒川製作所", "半導体製造装置 フィールドサービスエンジニア"),
    ("株式会社北斗電機", "ソフト品質保証課"),
    ("Hokuto Industries Inc.", "Finance Manager, Manufacturing"),
    ("中外製薬株式会社", "開業医MR"),
    ("株式会社マネーフォワード", "個人事業主セールス"),
    ("医療法人X会 X病院", "事務長（契約職員から正職員へ）"),
    ("株式会社X", "MR（契約社員から正社員）"),
    ("アクセンチュア株式会社", "製造・流通本部 マネジャー"),
    ("株式会社セールスフォース・ジャパン", "アカウントエグゼクティブ（製造）"),
    ("中外製薬株式会社", "MR（生物学的製剤）"),
    ("株式会社X", "経営企画部 課長（生産管理部から異動）"),
    ("ロバート・ウォルターズ・ジャパン株式会社", "Contract Staffing Manager"),
]
# 上の取りこぼしのうち、eligibility の結論まで確かめるもの（現職5年・1社のみ）。
EXCLUDED_REVIEW2 = [
    ("株式会社X（業務委託・週3日）", "Webディレクター"),
    ("日本郵便株式会社", "期間雇用社員"),
    ("株式会社B", "営業事務（契約社員・正社員登用あり）"),
    ("株式会社B", "生産オペレータ"),
    ("株式会社B", "生産1課"),
    ("株式会社B電機", "量産技術部"),
    ("居酒屋 鈴の家", "店主"),
]


@pytest.mark.parametrize("company,title", NOT_SELF_EMPLOYED_REVIEW2)
def test_review2_not_self_employed(company, title):
    assert not is_self_employed(company, title)


@pytest.mark.parametrize("company,title", SELF_EMPLOYED_REVIEW2)
def test_review2_self_employed(company, title):
    assert is_self_employed(company, title)


@pytest.mark.parametrize("company,title", NOT_CONTRACT_REVIEW2)
def test_review2_not_contract(company, title):
    assert not is_contract(f"{company} {title}")


@pytest.mark.parametrize("company,title", CONTRACT_REVIEW2)
def test_review2_contract(company, title):
    assert is_contract(f"{company} {title}")


@pytest.mark.parametrize("company,title", NOT_FACTORY_REVIEW2)
def test_review2_not_factory(company, title):
    assert not is_factory_job(company, title)


@pytest.mark.parametrize("company,title", FACTORY_REVIEW2)
def test_review2_factory(company, title):
    assert is_factory_job(company, title)


def _career_failures(company, title):
    c = make_candidate(current_company=company, current_title=title, employments=[
        Employment(company=company, title=title, years=5.0, months=60, is_current=True)])
    return [r for r in check_eligibility(c).failed
            if any(k in r for k in ("個人事業主", "契約社員", "工場・生産部門"))]


@pytest.mark.parametrize("company,title", ELIGIBLE_REVIEW2)
def test_review2_not_excluded_by_career_rules(company, title):
    assert _career_failures(company, title) == []


@pytest.mark.parametrize("company,title", EXCLUDED_REVIEW2)
def test_review2_excluded_by_career_rules(company, title):
    assert _career_failures(company, title)


def test_side_freelance_with_full_time_job_is_not_current_self_employed():
    """正社員の本業と並行した副業のフリーランスは「現職が個人事業主」ではない。"""
    c = make_candidate(employments=[
        _job("フリーランス", "Webデザイナー（副業）", 1.5, current=True),
        _job("株式会社B", "法人営業", 10.5, current=True),
    ])
    assert not is_current_self_employed(c)


def test_segments_take_precedence_over_title_written_at_hire():
    """役職欄が入社時の「生産技術部」のままでも、社内経歴で移った期間は数えない。"""
    c = make_candidate(employments=[
        _job("トヨタ自動車株式会社", "生産技術部", 10.0, current=True,
             segments=[("生産技術部", 3.0), ("経営企画部", 7.0)]),
    ])
    assert factory_ratio(c) == pytest.approx(0.3)


def test_factory_site_counts_whole_job_regardless_of_segments():
    c = make_candidate(employments=[
        _job("株式会社X 豊橋工場", "総務課", 10.0, current=True, segments=[("総務課", 4.0)]),
    ])
    assert factory_ratio(c) == pytest.approx(1.0)

