# -*- coding: utf-8 -*-
"""労働の規則。ゲームには触らない（ゲーム抜きの検査で全部通せる）。

職種の表・賃金の式・回ごとの出来事の表・勤続の段・裏の仕事の危険の式・控えの形を持つ。
設定は読まない（必要な値は引数で受け取る。TECH.md §3.1.1.1）。
遊び方は DOC.md。
"""

import random
import zlib

ABILITY_NAMES = {"strength": "筋力", "dexterity": "器用", "constitution": "耐久",
                 "intelligence": "知力", "wisdom": "賢さ", "charisma": "魅力"}

#: ゲームの労働の1回の長さ（日。29歳まで）。賃金の式 `get_labor_reward` は1回ぶんを返し、年齢で長さが伸びても額は変わらない。
GAME_TERM_DAYS = 90
#: 29歳までの1回の長さ（月）。30歳から5歳ごとに1ヵ月伸びる（役場も裏の事務所も同じ。GAME.md「役場の労働」）。
AGE_BASE_MONTHS = 3
#: ゲームの闇労働の1回の長さ（日）。危険の確率はこの長さの1回ぶんで決めてある。
GAME_ILLEGAL_TERM_DAYS = 90
DAYS_PER_MONTH = 30

#: 能力値の基準（作成時の既定）と、倍率が上限に届く値。
BASE_SCORE = 11
TOP_SCORE = 30
#: 基準より低い能力値で下がる賃金の下限（%）。
LOW_RATE_PERCENT = 80
#: 賃金の基準に使う、同じ難易度の依頼1件の報酬の割合（%）。ゲームの労働の式の値より低ければ式の値。0 で式の値のまま。
QUEST_PAY_PERCENT = 20

# ---------------------------------------------------------------- 役場の仕事
#: 雇い主になる施設の種類 → 効く能力値2つと、一覧に出す仕事の中身。
#: 並びは一覧に出す順。
OFFICE_JOBS = (
    ("blacksmith", ("strength", "constitution"), "重い物を運び、作業場の力仕事をする。"),
    ("inn", ("charisma", "constitution"), "客の世話と部屋の支度をする。"),
    ("general_store", ("charisma", "wisdom"), "店番と品出しをする。"),
    ("specialty_shop", ("intelligence", "dexterity"), "扱いの難しい品の手入れと客の応対をする。"),
    ("medical_facility", ("wisdom", "dexterity"), "手当ての手伝いと病人の世話をする。"),
    ("guild", ("intelligence", "charisma"), "受付と依頼の書類を受け持つ。"),
    ("administrative_office", ("intelligence", "wisdom"), "書類の整理と窓口の応対をする。"),
    ("training_facility", ("strength", "dexterity"), "稽古の相手と道具の手入れをする。"),
    ("colosseum", ("constitution", "strength"), "催しの設営と雑用をする。"),
)
OFFICE_JOB_BY_TYPE = {row[0]: row for row in OFFICE_JOBS}

#: 一覧に並べる施設の仕事の数と、顔ぶれが入れ替わる間隔（日）。
LIST_JOBS = 3
ROTATE_DAYS = 30

#: 雇い主の居ない仕事（ゲームの労働そのまま。能力値も勤続も効かない）。
PORTER = "porter"
#: 街の困り事の人手。
RELIEF = "relief"
RELIEF_ABILITIES = ("constitution", "wisdom")
RELIEF_PAY_PERCENT = 150
#: 1回の長さの設定がこれより長いときの、人手の仕事だけの長さと回数。
RELIEF_TERM_DAYS = 30
RELIEF_TERMS = 3

# ---------------------------------------------------------------- 裏の仕事
CRACKDOWN = "crackdown"
RETALIATION = "retaliation"
BETRAYAL = "betrayal"
RISK_NAMES = {CRACKDOWN: "取り締まり", RETALIATION: "仕返し", BETRAYAL: "裏切り"}
RISKS = (CRACKDOWN, RETALIATION, BETRAYAL)

#: 裏の仕事。`danger` は 1〜3（賃金の倍率と、危険が起きる確率を決める）。`risks` は起きたときの種類の重み。
ILLEGAL_JOBS = (
    {"key": "courier", "name": "荷運び", "danger": 1,
     "abilities": ("dexterity", "constitution"),
     "risks": {CRACKDOWN: 3, RETALIATION: 1, BETRAYAL: 1},
     "detail": "中身を問わない荷を、決められた先へ届ける。"},
    {"key": "lookout", "name": "見張り", "danger": 1,
     "abilities": ("wisdom", "constitution"),
     "risks": {CRACKDOWN: 1, RETALIATION: 2, BETRAYAL: 1},
     "detail": "誰かの仕事が済むまで、近づく者を知らせる。"},
    {"key": "collector", "name": "取り立て", "danger": 2,
     "abilities": ("strength", "charisma"),
     "risks": {CRACKDOWN: 1, RETALIATION: 3, BETRAYAL: 1},
     "detail": "払いの滞った相手から、貸しを取り立てる。"},
    {"key": "insider", "name": "内通", "danger": 2,
     "abilities": ("intelligence", "charisma"),
     "risks": {CRACKDOWN: 2, RETALIATION: 1, BETRAYAL: 3},
     "detail": "表の店や役所に潜り込み、内情を流し続ける。"},
    {"key": "breakin", "name": "侵入の手引き", "danger": 3,
     "abilities": ("dexterity", "wisdom"),
     "risks": {CRACKDOWN: 3, RETALIATION: 2, BETRAYAL: 2},
     "detail": "盗みに入る先を下見し、出入りを手引きする。"},
)
ILLEGAL_JOB_BY_KEY = {job["key"]: job for job in ILLEGAL_JOBS}
#: 前の版の仕事のキー（控えに残った契約を、替えた仕事として続けさせる）。帳簿の改ざんは長い契約に合わず内通に替えた。
ILLEGAL_JOB_BY_KEY["ledger"] = ILLEGAL_JOB_BY_KEY["insider"]
MAX_DANGER = 3
#: この危険度の仕事は、勤続の段がこの番号以上で並ぶ。
DANGER_NEEDS_RANK = {3: 1}
#: 危険が起きる確率（%。ゲームの闇労働の1回＝90日ぶん。能力値が基準のとき）。
RISK_BASE_PERCENT = {1: 10, 2: 18, 3: 28}
#: 能力値で確率に掛かる率の幅（基準より低いと上がり、高いと下がる）。
RISK_FACTOR_MIN = 0.4
RISK_FACTOR_MAX = 1.3
RISK_PERCENT_CAP = 90
#: 取り締まりで下がる手配度（危険度1つにつき）と、裏切りで下がる手配度。
CRACKDOWN_LAW_PER_DANGER = 2
BETRAYAL_LAW = 1
#: 仕返しで失う額（その回の賃金の倍数）。
RETALIATION_LOSS_TERMS = 2
#: 街に困り事が起きている間の、裏の仕事の賃金（%）。
ILLEGAL_TROUBLE_PERCENT = 130

# ---------------------------------------------------------------- 勤続
#: 段の (名前, 届く日数, 賃金 %, その店での値引き %, 裏の仕事の危険に掛ける %)。
RANKS = (
    ("見習い", 0, 100, 0, 100),
    ("一人前", 480, 125, 5, 90),
    ("任される", 1440, 150, 10, 80),
    ("右腕", 2880, 200, 15, 70),
)
#: 途中でやめたときに減る勤続（日）と、その雇い主が一覧に出なくなる日数。
QUIT_TENURE_DAYS = 240
QUIT_BARRED_DAYS = 180
#: 満了で上がる雇い主の好感度（短い契約・長い契約）。
FINISH_AFFINITY = (3, 6)
#: 満了の割増（%。短い契約・長い契約）。ゲームの 16ヵ月・32ヵ月の額から逆算した値。
FINISH_BONUS_PERCENT = (12.5, 25.0)

# ---------------------------------------------------------------- 回ごとの出来事
# 1件 = {"key", "jobs"（施設の種類の並び。None はどの仕事でも）, "text", "options"}
# 選択肢 = {"label", "ability"（判定に使う能力値。None は判定なし）, "base"（能力値が基準のときの成功 %）,
#           "win" / "lose"（判定ありの結果）, "result"（判定なしの結果）}
# 結果 = {"text", "pay"（その回の賃金の増減 %）, "tenure"（勤続の増減・日）, "affinity"（雇い主の好感度の増減）,
#         "law"（手配度の増減）, "fired"（その場で契約が終わる）}
# 文は {owner}（雇い主の名前）・{place}（施設の名前）を差し込む。舞台の物は書かず、関係と出来事だけを書く。
#: 能力値が基準から1上がるごとに成功の % に足す数と、成功の % の範囲。
CHECK_PER_POINT = 3
CHECK_MIN = 5
CHECK_MAX = 95


def _check(label, ability, base, win, lose):
    return {"label": label, "ability": ability, "base": base, "win": win, "lose": lose}


def _plain(label, result):
    return {"label": label, "ability": None, "base": None, "result": result}


EVENTS = (
    {"key": "rush", "jobs": None,
     "text": "{owner}から、今日のうちに片付けたい仕事を追加で頼まれた。",
     "options": (
         _check("引き受ける", "constitution", 60,
                {"text": "最後までやり遂げた。{owner}は上乗せを弾んでくれた。", "pay": 30, "tenure": 15},
                {"text": "無理がたたり、翌日は使い物にならなかった。", "pay": -10}),
         _plain("断る", {"text": "手一杯だと断った。{owner}は何も言わなかった。", "tenure": -5}))},
    {"key": "accident", "jobs": None,
     "text": "作業の最中に、積んであった物が崩れかけた。",
     "options": (
         _check("とっさに支える", "strength", 50,
                {"text": "体を入れて支え切った。居合わせた者が口々に礼を言う。", "affinity": 2, "tenure": 20},
                {"text": "支え切れず、物が駄目になった。弁償が賃金から引かれた。", "pay": -30}),
         _plain("身を引く", {"text": "怪我は無かったが、片付けに半日かかった。", "pay": -10}))},
    {"key": "quarrel", "jobs": None,
     "text": "同じ持ち場の者が、仕事の分担に文句を付けてきた。",
     "options": (
         _check("話をつける", "charisma", 55,
                {"text": "言い分を聞いたうえで分担を決め直した。以後は揉めなくなった。", "tenure": 10},
                {"text": "話はこじれ、持ち場の空気が悪くなった。", "tenure": -15}),
         _check("黙って多めに引き受ける", "constitution", 65,
                {"text": "文句の出ない量をこなしてみせた。相手は黙った。", "tenure": 15},
                {"text": "抱え込みすぎて、自分の仕事が遅れた。", "pay": -15}))},
    {"key": "customer", "jobs": None,
     "text": "{place}に来た者が、言いがかりを付けて騒ぎ始めた。",
     "options": (
         _check("なだめる", "charisma", 50,
                {"text": "相手の言い分を聞いて落ち着かせた。{owner}が礼を言った。", "affinity": 2, "pay": 10},
                {"text": "火に油を注いだ。{owner}が出てきて場を収めた。", "affinity": -1}),
         _plain("{owner}を呼ぶ", {"text": "{owner}に任せた。騒ぎはじきに収まった。"}))},
    {"key": "shortfall", "jobs": None,
     "text": "預かった金を数え直すと、わずかに足りない。",
     "options": (
         _check("原因を探す", "intelligence", 50,
                {"text": "書き付けの写し間違いを見つけた。{owner}は感心していた。", "affinity": 2, "tenure": 15},
                {"text": "原因は分からず、足りない分は自腹で埋めた。", "pay": -20}),
         _plain("正直に申し出る", {"text": "{owner}に申し出て、足りない分を賃金から埋めた。",
                                  "affinity": 1, "pay": -10}))},
    {"key": "raise", "jobs": None,
     "text": "{owner}が、働きぶりを見て賃金の話を切り出した。",
     "options": (
         _check("条件を掛け合う", "charisma", 45,
                {"text": "言い分が通り、今回の賃金が大きく上がった。", "pay": 40},
                {"text": "欲をかいたと思われた。話は流れた。", "affinity": -1}),
         _plain("言い値で受ける", {"text": "{owner}の言い値で受けた。", "pay": 15}))},
    {"key": "theft", "jobs": None,
     "text": "同じ持ち場の者が、預かり物をくすねて山分けしようと持ちかけてきた。",
     "options": (
         _check("乗る", "dexterity", 45,
                {"text": "気づかれずに済んだ。分け前が懐に入った。", "pay": 80},
                {"text": "{owner}に見つかった。その場で暇を出され、役人に届けられた。",
                 "fired": True, "law": -3, "affinity": -10}),
         _plain("断る", {"text": "関わらないと断った。"}),
         _plain("{owner}に知らせる", {"text": "{owner}に知らせた。持ちかけた者は翌日から来なくなった。",
                                     "affinity": 3, "tenure": 10}))},
    {"key": "teach", "jobs": None,
     "text": "古株が、手の空いた時間に仕事のこつを教えてくれるという。",
     "options": (
         _check("教わる", "wisdom", 55,
                {"text": "こつを呑み込んだ。任される仕事が増えた。", "tenure": 30},
                {"text": "聞いただけでは身に付かなかった。", "tenure": 5}),
         _plain("休む", {"text": "休めるときに休んだ。"}))},
    {"key": "delivery", "jobs": None,
     "text": "届け先のはっきりしない預かり物が、持ち場に残っている。",
     "options": (
         _check("聞いて回る", "wisdom", 50,
                {"text": "受け取る相手を突き止めて届けた。礼の金を渡された。", "pay": 20, "affinity": 1},
                {"text": "一日歩いても分からず、自分の仕事が溜まった。", "pay": -10}),
         _plain("後回しにする", {"text": "後回しにした。{owner}に小言を言われた。", "tenure": -5}))},
    {"key": "absence", "jobs": None,
     "text": "持ち場の者が倒れ、穴を埋める者が要る。",
     "options": (
         _check("代わりに入る", "constitution", 55,
                {"text": "二人ぶんを回し切った。そのぶんの賃金が付いた。", "pay": 25, "tenure": 15},
                {"text": "二人ぶんは回らず、どちらも半端になった。", "pay": -10}),
         _plain("断る", {"text": "自分の持ち場で手一杯だと断った。", "tenure": -5}))},
    {"key": "idea", "jobs": None,
     "text": "手順を変えれば、仕事がずっと早く済みそうだと気づいた。",
     "options": (
         _check("{owner}に持ちかける", "intelligence", 55,
                {"text": "試してみると確かに早かった。{owner}はその手順を採った。", "pay": 20, "tenure": 20},
                {"text": "試すと不具合が出た。元の手順に戻された。", "tenure": -5}),
         _plain("黙っておく", {"text": "言われたとおりに続けた。"}))},
    {"key": "night", "jobs": None,
     "text": "夜の見回りを頼まれた晩、奥で物音がした。",
     "options": (
         _check("確かめに行く", "dexterity", 50,
                {"text": "忍び込んだ者を見つけ、追い払った。{owner}から礼が出た。", "pay": 20, "affinity": 2},
                {"text": "暗がりで物を倒し、壊してしまった。", "pay": -20}),
         _plain("朝まで待つ", {"text": "朝になって確かめたが、何も無くなっていなかった。"}))},
    # ---- 職種ごと
    {"key": "smith_order", "jobs": ("blacksmith",),
     "text": "{place}に大口の注文が入り、数日のうちに数を揃えなければならない。",
     "options": (
         _check("力の要る工程を引き受ける", "strength", 55,
                {"text": "いちばん重い工程を一人で回した。注文は間に合った。", "pay": 30, "tenure": 15},
                {"text": "途中で腕が上がらなくなり、納めるのが遅れた。", "pay": -15}),
         _plain("運びと片付けに回る", {"text": "裏方に回った。注文はどうにか間に合った。", "tenure": 5}))},
    {"key": "smith_finish", "jobs": ("blacksmith",),
     "text": "{owner}が、仕上げの工程を一度やってみろと言う。",
     "options": (
         _check("やってみる", "dexterity", 50,
                {"text": "仕上がりを見た{owner}がうなずいた。", "affinity": 2, "tenure": 25},
                {"text": "仕上げをしくじり、一つ駄目にした。", "pay": -20}),
         _plain("まだ早いと断る", {"text": "まだ早いと断った。{owner}は肩をすくめた。"}))},
    {"key": "smith_watch", "jobs": ("blacksmith",),
     "text": "夜通し目を離せない工程の番を頼まれた。",
     "options": (
         _check("朝まで番をする", "constitution", 55,
                {"text": "一睡もせずに番をし通した。", "pay": 20, "tenure": 10},
                {"text": "明け方に居眠りをして、やり直しになった。", "pay": -15, "affinity": -1}),
         _plain("ほかの者に譲る", {"text": "番はほかの者に回った。"}))},
    {"key": "inn_rowdy", "jobs": ("inn",),
     "text": "{place}の客が夜更けに騒ぎ、ほかの客から苦情が出ている。",
     "options": (
         _check("静かにしてもらう", "charisma", 50,
                {"text": "角を立てずに収めた。翌朝、苦情を言った客から心付けを渡された。", "pay": 15, "affinity": 1},
                {"text": "言い合いになり、騒ぎが大きくなった。", "affinity": -1}),
         _plain("{owner}を起こす", {"text": "{owner}を起こして任せた。"}))},
    {"key": "inn_full", "jobs": ("inn",),
     "text": "{place}が満室になり、手が足りない日が続いている。",
     "options": (
         _check("休みなく回す", "constitution", 55,
                {"text": "満室の日々を倒れずに乗り切った。", "pay": 25, "tenure": 15},
                {"text": "疲れから手落ちが続いた。", "pay": -10}),
         _plain("自分の持ち場だけ守る", {"text": "自分の持ち場だけは滞らせなかった。"}))},
    {"key": "inn_left", "jobs": ("inn",),
     "text": "発った客の部屋に、忘れ物が残っていた。",
     "options": (
         _check("追いかけて届ける", "constitution", 50,
                {"text": "街の出口で追いついて渡した。客は{place}の名を覚えて帰った。", "affinity": 3},
                {"text": "追いつけなかった。持ち場を空けたぶんを叱られた。", "tenure": -5}),
         _plain("{owner}に預ける", {"text": "{owner}に預けた。", "affinity": 1}))},
    {"key": "store_haggle", "jobs": ("general_store",),
     "text": "店番の最中に、しつこく値引きを迫る客が来た。",
     "options": (
         _check("値を守って売る", "charisma", 50,
                {"text": "値を崩さずに気持ちよく買わせた。", "pay": 20, "affinity": 1},
                {"text": "押し切られて、損な値で売ってしまった。", "pay": -15}),
         _plain("{owner}の帰りを待たせる", {"text": "客は待たずに帰った。"}))},
    {"key": "store_stock", "jobs": ("general_store",),
     "text": "仕入れの品に、質の怪しい物が混じっている気がする。",
     "options": (
         _check("選り分ける", "wisdom", 50,
                {"text": "悪い品を見分けて突き返した。{owner}は損をせずに済んだ。", "affinity": 2, "tenure": 15},
                {"text": "良い品まで突き返し、仕入れ先を怒らせた。", "affinity": -1}),
         _plain("そのまま並べる", {"text": "そのまま並べた。後で客から苦情が来た。", "tenure": -5}))},
    {"key": "store_count", "jobs": ("general_store",),
     "text": "{owner}から、棚の品を全部数え直すよう頼まれた。",
     "options": (
         _check("一晩で数え上げる", "wisdom", 55,
                {"text": "帳面と突き合わせ、食い違いまで洗い出した。", "pay": 15, "tenure": 15},
                {"text": "数え間違いが多く、やり直しになった。", "pay": -10}),
         _plain("数日かけて数える", {"text": "数日かけて数え終えた。"}))},
    {"key": "shop_origin", "jobs": ("specialty_shop",),
     "text": "客に、棚の品の由来を詳しく尋ねられた。",
     "options": (
         _check("知る限りを話す", "intelligence", 50,
                {"text": "話に納得した客は、値切らずに買っていった。", "pay": 25},
                {"text": "でたらめを見抜かれ、客は呆れて帰った。", "affinity": -1}),
         _plain("{owner}に取り次ぐ", {"text": "{owner}に取り次いだ。"}))},
    {"key": "shop_fragile", "jobs": ("specialty_shop",),
     "text": "扱いの難しい品を、奥から売り場へ移すことになった。",
     "options": (
         _check("一人で運ぶ", "dexterity", 55,
                {"text": "傷一つ付けずに移し終えた。", "tenure": 20, "affinity": 1},
                {"text": "手を滑らせ、品に傷を付けた。弁償が賃金から引かれた。", "pay": -30}),
         _plain("{owner}の手を借りる", {"text": "{owner}と二人で運んだ。"}))},
    {"key": "shop_patron", "jobs": ("specialty_shop",),
     "text": "{place}の得意客が、{owner}の留守に訪ねてきた。",
     "options": (
         _check("相手を務める", "intelligence", 50,
                {"text": "話し相手を務め、次の注文まで取り付けた。", "pay": 20, "affinity": 2},
                {"text": "受け答えを誤り、客の機嫌を損ねた。", "affinity": -2}),
         _plain("出直してもらう", {"text": "出直してもらった。"}))},
    {"key": "clinic_urgent", "jobs": ("medical_facility",),
     "text": "{place}に、急を要する者が担ぎ込まれた。{owner}は手が離せない。",
     "options": (
         _check("容体を見て手順を決める", "wisdom", 50,
                {"text": "見立ては合っていた。{owner}が来るまで持たせた。", "affinity": 3, "tenure": 20},
                {"text": "見立てを誤り、{owner}に厳しく叱られた。", "affinity": -2}),
         _plain("{owner}を呼びに走る", {"text": "{owner}を呼んできた。間に合った。"}))},
    {"key": "clinic_hands", "jobs": ("medical_facility",),
     "text": "{owner}の手当てを、すぐ横で手伝うことになった。",
     "options": (
         _check("言われる前に手を出す", "dexterity", 55,
                {"text": "要る物を要る時に渡し続けた。{owner}の手が止まらなかった。", "tenure": 25, "pay": 10},
                {"text": "渡す物を間違え、手当てが長引いた。", "tenure": -10}),
         _plain("言われた事だけをする", {"text": "言われた事だけをこなした。"}))},
    {"key": "clinic_night", "jobs": ("medical_facility",),
     "text": "容体の重い者が居て、夜通し付き添う者が要る。",
     "options": (
         _check("付き添う", "wisdom", 55,
                {"text": "夜中の変わり目に気づいて{owner}を起こした。朝には峠を越えていた。", "pay": 20, "affinity": 2},
                {"text": "変わり目を見落とした。大事には至らなかったが、叱られた。", "affinity": -1}),
         _plain("交代で付き添う", {"text": "ほかの者と交代で付き添った。"}))},
    {"key": "guild_papers", "jobs": ("guild",),
     "text": "片付いた依頼の報告が溜まり、書類が山になっている。",
     "options": (
         _check("まとめて片付ける", "intelligence", 55,
                {"text": "山を一日で崩した。{owner}が目を丸くした。", "pay": 20, "tenure": 15},
                {"text": "写し間違いが見つかり、最初からやり直した。", "pay": -10}),
         _plain("少しずつ進める", {"text": "少しずつ進めた。"}))},
    {"key": "guild_dispute", "jobs": ("guild",),
     "text": "報酬の分け前を巡って、受付の前で冒険者どうしが揉めている。",
     "options": (
         _check("間に入る", "charisma", 50,
                {"text": "双方の言い分を聞いて折り合わせた。", "affinity": 2, "tenure": 15},
                {"text": "どちらからも恨まれた。", "tenure": -10}),
         _plain("{owner}に任せる", {"text": "{owner}が出てきて収めた。"}))},
    {"key": "guild_client", "jobs": ("guild",),
     "text": "依頼を出したいが、どう書けばよいか分からないという者が来た。",
     "options": (
         _check("話を聞いて書き起こす", "intelligence", 50,
                {"text": "要点をまとめた依頼書は、その日のうちに引き受け手が付いた。", "pay": 15, "affinity": 1},
                {"text": "要領を得ない依頼書になり、書き直しを命じられた。", "tenure": -5}),
         _plain("{owner}に回す", {"text": "{owner}に回した。"}))},
    {"key": "office_record", "jobs": ("administrative_office",),
     "text": "古い記録と新しい記録に、食い違いを見つけた。",
     "options": (
         _check("突き合わせて正す", "intelligence", 50,
                {"text": "どこで違えたかまで突き止めた。{owner}の覚えがよくなった。", "affinity": 2, "tenure": 20},
                {"text": "正したつもりが、別の食い違いを作った。", "tenure": -10}),
         _plain("{owner}に報告だけする", {"text": "報告だけして、判断は{owner}に任せた。", "tenure": 5}))},
    {"key": "office_petition", "jobs": ("administrative_office",),
     "text": "窓口に、順番を待ち切れない者が怒鳴り込んできた。",
     "options": (
         _check("用件を聞き分ける", "wisdom", 50,
                {"text": "急ぐべき用件だと見て先に通した。後で{owner}がその判断を褒めた。", "affinity": 2, "pay": 10},
                {"text": "ただの割り込みを通してしまい、待っていた者たちが騒いだ。", "affinity": -1}),
         _plain("順番を守らせる", {"text": "順番を守らせた。"}))},
    {"key": "office_deadline", "jobs": ("administrative_office",),
     "text": "期限の迫った書類が、手つかずで見つかった。",
     "options": (
         _check("引き取って仕上げる", "intelligence", 55,
                {"text": "期限の前日に仕上げて出した。", "pay": 25, "tenure": 10},
                {"text": "間に合わず、{owner}が方々に頭を下げた。", "pay": -10, "affinity": -1}),
         _plain("見つけたと知らせる", {"text": "{owner}に知らせた。{owner}が手配した。"}))},
    {"key": "train_spar", "jobs": ("training_facility",),
     "text": "腕に覚えのある者が、手加減の要らない相手を求めている。",
     "options": (
         _check("相手を務める", "strength", 50,
                {"text": "最後まで打ち合った。相手は満足して割増を払った。", "pay": 30, "tenure": 15},
                {"text": "早々に打ち負かされ、相手は不満そうに帰った。", "pay": -10}),
         _plain("ほかの者に譲る", {"text": "ほかの者が相手をした。"}))},
    {"key": "train_novice", "jobs": ("training_facility",),
     "text": "入ったばかりの者に、基本の動きを見せてやれと言われた。",
     "options": (
         _check("手本を見せる", "dexterity", 50,
                {"text": "分かりやすい手本だと{owner}が言った。", "affinity": 2, "tenure": 20},
                {"text": "手本の途中でしくじり、笑われた。", "tenure": -5}),
         _plain("口で説明する", {"text": "口で説明した。"}))},
    {"key": "train_gear", "jobs": ("training_facility",),
     "text": "稽古に使う道具が傷んでいる。",
     "options": (
         _check("手入れして直す", "dexterity", 55,
                {"text": "一通り直して使えるようにした。", "pay": 15, "tenure": 10},
                {"text": "直すつもりで壊してしまった。", "pay": -15}),
         _plain("{owner}に知らせる", {"text": "{owner}に知らせた。"}))},
    {"key": "arena_setup", "jobs": ("colosseum",),
     "text": "次の催しまでに、場の設えを組み直さなければならない。",
     "options": (
         _check("重い物を引き受ける", "strength", 55,
                {"text": "いちばん重い物を運び切った。設えは間に合った。", "pay": 25, "tenure": 15},
                {"text": "腰を痛めかけ、途中で外された。", "pay": -10}),
         _plain("細かい作業に回る", {"text": "細かい作業に回った。"}))},
    {"key": "arena_crowd", "jobs": ("colosseum",),
     "text": "熱くなった見物人が、柵を越えようとしている。",
     "options": (
         _check("体を張って止める", "constitution", 50,
                {"text": "押されても退かず、柵の内へは入れなかった。", "affinity": 2, "pay": 15},
                {"text": "押し倒され、見物人が何人か中へ入った。", "affinity": -1}),
         _plain("人を呼ぶ", {"text": "人を呼んだ。数で押し返した。"}))},
    {"key": "arena_fighter", "jobs": ("colosseum",),
     "text": "出番を控えた者の世話を任された。相手は気が立っている。",
     "options": (
         _check("黙って要る物を揃える", "constitution", 50,
                {"text": "当たり散らされても動じずに支度を整えた。勝った後、祝儀が回ってきた。", "pay": 30},
                {"text": "手際が悪いと怒鳴られ、世話役を替えられた。", "tenure": -10}),
         _plain("ほかの者に替わってもらう", {"text": "ほかの者に替わってもらった。"}))},
    # ---- 品を受け取る出来事（施設ごとに1件。成功すると雇い主の持ち物から1品を受け取る）
    {"key": "smith_test", "jobs": ("blacksmith",),
     "text": "{owner}が、打ち上がったばかりの品の具合を見てほしいと言う。",
     "options": (
         _check("使い込んで確かめる", "strength", 55,
                {"text": "使い込んで癖まで伝えた。{owner}が礼をしたいと言う。", "item": True, "affinity": 1},
                {"text": "試すうちに傷を付けてしまった。", "pay": -15}),
         _plain("ざっと見て返す", {"text": "ざっと見て返した。"}))},
    {"key": "inn_supply", "jobs": ("inn",),
     "text": "{owner}が、仕入れの目利きを頼んできた。",
     "options": (
         _check("質の良い物を選び抜く", "wisdom", 50,
                {"text": "良い物だけを選び抜いた。{owner}が余った分を持たせてくれると言う。", "item": True},
                {"text": "傷んだ物を混ぜてしまった。", "affinity": -1}),
         _plain("言われた数だけ揃える", {"text": "言われた数だけ揃えた。"}))},
    {"key": "store_shelf", "jobs": ("general_store",),
     "text": "{owner}が、売れ残った品の並べ方を変えてみろと言う。",
     "options": (
         _check("客の目を引くように並べる", "charisma", 50,
                {"text": "並べ替えた棚から品がよく売れた。{owner}が好きな物を持っていけと言う。", "item": True},
                {"text": "かえって客の足が遠のいた。", "pay": -10}),
         _plain("元のままにしておく", {"text": "元のままにしておいた。"}))},
    {"key": "shop_appraise", "jobs": ("specialty_shop",),
     "text": "{owner}が、持ち込まれた品の目利きを任せてきた。",
     "options": (
         _check("見立てる", "intelligence", 55,
                {"text": "見立ては{owner}と同じだった。{owner}が棚の物を分けてくれると言う。", "item": True},
                {"text": "見立てを外し、{owner}に損をさせかけた。", "affinity": -1}),
         _plain("{owner}に任せる", {"text": "{owner}に任せた。"}))},
    {"key": "clinic_mix", "jobs": ("medical_facility",),
     "text": "{owner}が、手当てに使う物の支度を教えてくれることになった。",
     "options": (
         _check("手順を覚えて支度する", "dexterity", 50,
                {"text": "手順を一度で覚えた。{owner}が支度した物を持たせてくれると言う。", "item": True},
                {"text": "分量を誤り、作り直しになった。", "pay": -10}),
         _plain("見ているだけにする", {"text": "見ているだけにした。"}))},
    {"key": "guild_lost", "jobs": ("guild",),
     "text": "報告に来た冒険者が、受け取り手のいない品を置いていった。",
     "options": (
         _check("持ち主を辿る", "wisdom", 50,
                {"text": "持ち主は見つからなかった。{owner}がそれをお前の取り分にしようと言う。", "item": True},
                {"text": "持ち主を取り違えて騒ぎになった。", "tenure": -10}),
         _plain("{owner}に預ける", {"text": "{owner}に預けた。", "affinity": 1}))},
    {"key": "office_seized", "jobs": ("administrative_office",),
     "text": "差し押さえた品の目録づくりを手伝うことになった。",
     "options": (
         _check("一晩で目録を仕上げる", "intelligence", 50,
                {"text": "目録を一晩で仕上げた。{owner}が払い下げの品を回してくれると言う。", "item": True},
                {"text": "数え漏れが見つかり、やり直しになった。", "pay": -10}),
         _plain("数えるだけ数える", {"text": "数えるだけ数えた。"}))},
    {"key": "train_bout", "jobs": ("training_facility",),
     "text": "{owner}が、稽古の締めに一本勝負を挑んできた。",
     "options": (
         _check("受けて立つ", "strength", 50,
                {"text": "一本取った。{owner}が笑って、稽古場の物を寄越すと言う。", "item": True, "affinity": 1},
                {"text": "あっさり転がされた。", "tenure": -5}),
         _plain("辞退する", {"text": "辞退した。"}))},
    {"key": "arena_prize", "jobs": ("colosseum",),
     "text": "催しの裏で、余った景品の配り役を任された。",
     "options": (
         _check("揉めないように配る", "charisma", 50,
                {"text": "揉めずに配り終えた。{owner}が残りの一つを回してくれると言う。", "item": True},
                {"text": "配り方で揉め事が起きた。", "affinity": -1}),
         _plain("ほかの者に任せる", {"text": "ほかの者に任せた。"}))},
)
EVENT_BY_KEY = {event["key"]: event for event in EVENTS}

# ---------------------------------------------------------------- 闇労働の出来事
# 危険が起きなかった回に、役場の出来事と同じ確率で起きる。形は役場の出来事と同じ（"affinity" は事務所の主人）。
ILLEGAL_EVENTS = (
    {"key": "skim", "jobs": None,
     "text": "預かった物から少し抜いても、誰も気づかなそうだ。",
     "options": (
         _check("抜く", "dexterity", 45,
                {"text": "誰にも気づかれなかった。", "pay": 40},
                {"text": "気づかれた。{owner}の信用を失った。", "tenure": -60, "affinity": -3}),
         _plain("手を付けない", {"text": "手を付けなかった。", "tenure": 5}))},
    {"key": "extra", "jobs": None,
     "text": "{owner}から、急ぎの追加の仕事を頼まれた。",
     "options": (
         _check("引き受ける", "constitution", 55,
                {"text": "急ぎの仕事も片付けた。{owner}は上乗せを払った。", "pay": 35, "tenure": 10},
                {"text": "手が回らず、どちらも中途半端になった。", "pay": -10}),
         _plain("断る", {"text": "手一杯だと断った。", "tenure": -5}))},
    {"key": "mess", "jobs": None,
     "text": "仲間の一人がしくじり、後始末が要る。",
     "options": (
         _check("跡を消して回る", "wisdom", 50,
                {"text": "跡を残さず片付けた。{owner}の覚えがよくなった。", "tenure": 20, "affinity": 1},
                {"text": "片付けの最中に顔を見られた。", "law": -1}),
         _plain("放っておく", {"text": "放っておいた。{owner}は不満げだった。", "affinity": -1}))},
    {"key": "hush", "jobs": None,
     "text": "仕事を見ていた者が、口止め料を求めてきた。",
     "options": (
         _check("凄んで黙らせる", "strength", 50,
                {"text": "相手は青い顔で引き下がった。", "tenure": 10},
                {"text": "相手は逃げて、役人に話を持ち込んだ。", "law": -2}),
         _plain("払って黙らせる", {"text": "稼ぎの一部を渡して黙らせた。", "pay": -30}))},
    {"key": "stranger", "jobs": None,
     "text": "見知らぬ者が、割のいい頼み事を持ちかけてきた。",
     "options": (
         _check("素性を探ってから受ける", "intelligence", 50,
                {"text": "怪しい所は無かった。頼み事は割のいい稼ぎになった。", "pay": 30},
                {"text": "罠だった。逃げ帰る羽目になった。", "pay": -20, "law": -1}),
         _plain("断る", {"text": "断った。"}))},
    {"key": "rival", "jobs": None,
     "text": "別の筋の者が、縄張りを荒らすなと脅してきた。",
     "options": (
         _check("話をつける", "charisma", 50,
                {"text": "互いの縄張りを決めて手打ちにした。{owner}が感心した。", "affinity": 2, "tenure": 10},
                {"text": "話はこじれ、稼ぎの一部を譲る羽目になった。", "pay": -20}),
         _plain("引き下がる", {"text": "引き下がった。", "tenure": -5}))},
    {"key": "informant", "jobs": None,
     "text": "役人の動きを知る者が、話を売りたいと言ってきた。",
     "options": (
         _check("値を叩いて買う", "charisma", 50,
                {"text": "安く聞き出した。次の手入れの日取りが分かった。", "pay": -10, "tenure": 15},
                {"text": "高値で掴まされたうえ、中身は古い話だった。", "pay": -25}),
         _plain("断る", {"text": "断った。"}))},
    {"key": "bonus", "jobs": None,
     "text": "{owner}が、今回の働きに上乗せを考えていると言う。",
     "options": (
         _check("吹っかける", "charisma", 45,
                {"text": "思った以上の額を引き出した。", "pay": 40},
                {"text": "欲をかいたと思われ、{owner}の機嫌を損ねた。", "affinity": -2}),
         _plain("素直に受け取る", {"text": "素直に受け取った。", "pay": 15}))},
)
ILLEGAL_EVENT_BY_KEY = {event["key"]: event for event in ILLEGAL_EVENTS}


def pick_illegal_event(rng, last_key=None):
    """闇労働の出来事を1つ選ぶ。直前と同じものは続けない。"""
    pool = [event for event in ILLEGAL_EVENTS if event["key"] != last_key] or list(ILLEGAL_EVENTS)
    return pool[int(rng.random() * len(pool)) % len(pool)]


# ---------------------------------------------------------------- 危険を避ける選択肢
# 危険が起きたら、すぐに損をさせず選択肢を出す。成功すれば避け、失敗すれば危険のとおりの損。
# 選択肢 = {"label", "how", "ability", "shift"（危険度ごとの基準に足す %）, "win"（成功の結果）, "lose"（失敗の頭の文）}
#   how: "check"（能力値の判定）/ "bribe"（1回の賃金を所持金から払い、能力値で判定。失敗しても払った額は戻らない）
#        / "sure"（判定なしで決まる結果）
# 結果の "pay" は、その回の賃金の増減 %（-100 で賃金なし）。"quit" は手を引いて契約を終える（稼ぎは守る）。
#: 危険度ごとの、避ける判定の基準の成功 %（能力値 11 のとき）。危険度が高い仕事ほど避けにくい。
RISK_CHECK_BASE = {1: 60, 2: 50, 3: 40}


def _avoid(label, ability, shift, win, lose, how="check"):
    return {"label": label, "how": how, "ability": ability, "shift": shift, "win": win, "lose": lose}


def _sure(label, result):
    return {"label": label, "how": "sure", "ability": None, "shift": 0, "win": result, "lose": None}


RISK_EVENTS = {
    CRACKDOWN: {
        "text": "仕事の最中に、取り締まりの手が回ってきた。",
        "options": (
            _avoid("裏口から逃げる", "dexterity", 0,
                   {"text": "裏口から抜け出し、追っ手を撒いた。", "tenure": 10}, "逃げ切れなかった。"),
            _avoid("荷を捨てて人混みに紛れる", "wisdom", 10,
                   {"text": "荷を捨てて紛れ込み、顔を見られずに済んだ。今回の稼ぎは半分になった。", "pay": -50},
                   "紛れ込む前に見咎められた。"),
            _avoid("見張りに金を握らせる", "charisma", 0,
                   {"text": "金を受け取った見張りは、黙って目を逸らした。"}, "金は受け取られたが、見逃してはもらえなかった。",
                   how="bribe"),
        )},
    RETALIATION: {
        "text": "仕事の相手方が、仕返しに人を差し向けてきた。",
        "options": (
            _avoid("迎え撃つ", "strength", 0,
                   {"text": "差し向けられた者たちを追い返した。", "tenure": 15}, "多勢に押し切られた。"),
            _sure("今回の稼ぎを渡して収める", {"text": "今回の稼ぎを差し出して、相手を収めた。", "pay": -100}),
            _avoid("身を隠してやり過ごす", "dexterity", 5,
                   {"text": "身を隠し、相手が諦めるまでやり過ごした。"}, "隠れ家を嗅ぎつけられた。"),
        )},
    BETRAYAL: {
        "text": "組んでいる者の様子がおかしい。役人と通じているのかもしれない。",
        "options": (
            _avoid("先回りして段取りを変える", "wisdom", 0,
                   {"text": "相手が動く前に段取りを変え、売られずに済んだ。", "tenure": 30}, "先回りは間に合わなかった。"),
            _avoid("問い詰める", "charisma", -5,
                   {"text": "問い詰めると、相手は白状して手を引いた。", "tenure": 20}, "相手はしらを切り、その夜のうちに動いた。"),
            _sure("ここで手を引く", {"text": "嫌な予感に従って手を引いた。ここまでの稼ぎは守ったが、仕事はここまでになった。",
                                    "pay": -100, "quit": True}),
        )},
}


def tidy_item(raw, value, item_types, rarities):
    """LLM が書いた品1つを、ゲームの `generate_item_from_item_data` の引数の並びにする。読めなければ None。

    `(item_name, description, item_type, item_sub_type, value, item_appearance, rarity)`。
    種類が語彙（`items.ITEM_TYPES`）から外れたら `utility`、細分が外れたらその種類の先頭、レア度が外れたら先頭に寄せる。
    価値は呼び側が決める（その土地の平均難易度）。
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("item_name") or "").strip()[:40]
    if not name:
        return None
    item_type = str(raw.get("item_type") or "")
    if item_type not in item_types:
        item_type = "utility"
    sub = str(raw.get("item_sub_type") or "")
    if sub not in item_types[item_type]:
        sub = item_types[item_type][0]
    rarity = str(raw.get("rarity") or "")
    if rarity not in rarities:
        rarity = rarities[0]
    appearance = str(raw.get("item_appearance") or "").strip() or name
    return (name, str(raw.get("description") or "").strip(), item_type, sub, int(value), appearance, rarity)


def avoid_percent(option, danger, score):
    """危険を避ける選択肢の成功 %。判定の無い選択肢は None。"""
    if option.get("how") == "sure":
        return None
    danger = min(MAX_DANGER, max(1, int(_number(danger, 1))))
    return check_percent(RISK_CHECK_BASE[danger] + int(_number(option.get("shift"))), score)


#: 良い出来事の賃金（段階ごとに足す %・上限・崩れた段階の %）。
INCIDENT_STEP_PERCENT = 10
INCIDENT_MAX_PERCENT = 130
INCIDENT_BAD_PERCENT = 70

#: `configure` がそのまま差し替える数の名前。
PLAIN_VALUES = ("LIST_JOBS", "ROTATE_DAYS", "QUIT_TENURE_DAYS", "QUIT_BARRED_DAYS", "LOW_RATE_PERCENT",
                "QUEST_PAY_PERCENT",
                "CHECK_PER_POINT", "INCIDENT_STEP_PERCENT", "INCIDENT_MAX_PERCENT", "INCIDENT_BAD_PERCENT",
                "RELIEF_PAY_PERCENT", "RELIEF_TERM_DAYS", "RELIEF_TERMS", "ILLEGAL_TROUBLE_PERCENT",
                "CRACKDOWN_LAW_PER_DANGER", "BETRAYAL_LAW", "RETALIATION_LOSS_TERMS")
#: 既定の段の表（`configure` が日数・賃金・値引きを差し替える元）。
DEFAULT_RANKS = RANKS


def values_from_settings(get):
    """設定の名前（mod.json の "settings"）から、`configure` に渡す形を作る。`get(名前)` が値を返す。

    入口（ゲームの中）と設定画面の試算（ゲームの外）が、同じ対応で規則に値を渡すためにここへ置く。
    """
    return {
        "LIST_JOBS": get("LIST_JOBS"), "ROTATE_DAYS": get("ROTATE_DAYS"),
        "FINISH_BONUS_PERCENT": (get("SHORT_BONUS_PERCENT"), get("LONG_BONUS_PERCENT")),
        "FINISH_AFFINITY": (get("SHORT_AFFINITY"), get("LONG_AFFINITY")),
        "QUIT_TENURE_DAYS": get("QUIT_TENURE_DAYS"), "QUIT_BARRED_DAYS": get("QUIT_BARRED_DAYS"),
        "RANK_DAYS": (get("RANK2_DAYS"), get("RANK3_DAYS"), get("RANK4_DAYS")),
        "RANK_PAY_PERCENT": (get("RANK2_PAY_PERCENT"), get("RANK3_PAY_PERCENT"), get("RANK4_PAY_PERCENT")),
        "RANK_DISCOUNT_PERCENT": (get("RANK2_DISCOUNT_PERCENT"), get("RANK3_DISCOUNT_PERCENT"),
                                  get("RANK4_DISCOUNT_PERCENT")),
        "LOW_RATE_PERCENT": get("ABILITY_LOW_PERCENT"), "CHECK_PER_POINT": get("CHECK_PER_POINT"),
        "QUEST_PAY_PERCENT": get("QUEST_PAY_PERCENT"),
        "INCIDENT_STEP_PERCENT": get("GOOD_EVENT_STEP_PERCENT"),
        "INCIDENT_MAX_PERCENT": get("GOOD_EVENT_MAX_PERCENT"),
        "INCIDENT_BAD_PERCENT": get("BAD_STAGE_PERCENT"),
        "RELIEF_PAY_PERCENT": get("RELIEF_PAY_PERCENT"), "RELIEF_TERM_DAYS": get("RELIEF_TERM_DAYS"),
        "RELIEF_TERMS": get("RELIEF_TERMS"), "ILLEGAL_TROUBLE_PERCENT": get("ILLEGAL_TROUBLE_PERCENT"),
        "RISK_BASE_PERCENT": (get("RISK_PERCENT_1"), get("RISK_PERCENT_2"), get("RISK_PERCENT_3")),
        "CRACKDOWN_LAW_PER_DANGER": get("CRACKDOWN_LAW"), "BETRAYAL_LAW": get("BETRAYAL_LAW"),
        "RETALIATION_LOSS_TERMS": get("RETALIATION_TERMS"),
        "RISK_CHECK_BASE": (get("AVOID_PERCENT_1"), get("AVOID_PERCENT_2"), get("AVOID_PERCENT_3")),
    }


def configure(**values):
    """設定の値を当てる（入口が `apply()` のたびに呼ぶ）。読めない値・知らない名前はそのままにする。

    `RANK_DAYS` / `RANK_PAY_PERCENT` / `RANK_DISCOUNT_PERCENT` は2段め以降の3つの並び、
    `FINISH_BONUS_PERCENT` / `FINISH_AFFINITY` は（短い契約, 長い契約）、`RISK_BASE_PERCENT` は危険度 1〜3 の並び。
    段の日数は、前の段より小さい値を前の段の日数まで引き上げる（段の順が入れ替わらないように）。
    """
    scope = globals()

    def number(value):
        return not isinstance(value, bool) and isinstance(value, (int, float))

    def several(name, count=3):
        found = values.get(name)
        if isinstance(found, (list, tuple)) and len(found) == count and all(number(v) for v in found):
            return list(found)
        return None

    for name in PLAIN_VALUES:
        if number(values.get(name)):
            scope[name] = values[name]
    pair = several("FINISH_BONUS_PERCENT", 2)
    if pair is not None:
        scope["FINISH_BONUS_PERCENT"] = (float(pair[0]), float(pair[1]))
    pair = several("FINISH_AFFINITY", 2)
    if pair is not None:
        scope["FINISH_AFFINITY"] = (int(pair[0]), int(pair[1]))
    risks = several("RISK_BASE_PERCENT")
    if risks is not None:
        scope["RISK_BASE_PERCENT"] = {1: risks[0], 2: risks[1], 3: risks[2]}
    avoids = several("RISK_CHECK_BASE")
    if avoids is not None:
        scope["RISK_CHECK_BASE"] = {1: avoids[0], 2: avoids[1], 3: avoids[2]}
    days, pays, cuts = several("RANK_DAYS"), several("RANK_PAY_PERCENT"), several("RANK_DISCOUNT_PERCENT")
    ranks = [DEFAULT_RANKS[0]]
    for index in range(1, len(DEFAULT_RANKS)):
        name, base_days, base_pay, base_cut, risk = DEFAULT_RANKS[index]
        reach = int(days[index - 1]) if days is not None else base_days
        reach = max(reach, ranks[-1][1])
        ranks.append((name, reach,
                      pays[index - 1] if pays is not None else base_pay,
                      cuts[index - 1] if cuts is not None else base_cut, risk))
    scope["RANKS"] = tuple(ranks)


# ---------------------------------------------------------------- 数え方
def _number(value, default=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def age_months(age):
    """その年齢でのゲームの労働の1回の長さ（月）。29歳まで 3、30〜34 で 4、…、50〜54 で 8。年齢が読めなければ 3。"""
    if isinstance(age, bool):
        return AGE_BASE_MONTHS
    try:
        age = int(age)
    except (TypeError, ValueError):
        return AGE_BASE_MONTHS
    return AGE_BASE_MONTHS + max(0, (age - 25) // 5)


def age_days(days, age):
    """設定の長さ（29歳までの長さ）を、その年齢の長さにする。ゲームと同じ割合で伸ばす。"""
    days = max(1, int(_number(days, GAME_TERM_DAYS)))
    return max(1, int(round(days * age_months(age) / float(AGE_BASE_MONTHS))))


def days_in_words(days):
    """日数の言い方。30 日で割り切れれば「nヵ月」、ほかは「n日」。"""
    days = int(_number(days))
    if days > 0 and days % DAYS_PER_MONTH == 0:
        return "{}ヵ月".format(days // DAYS_PER_MONTH)
    return "{}日".format(days)


def ability_average(scores, abilities):
    """効く能力値の平均。能力値が読めなければ基準の値。"""
    if not isinstance(scores, dict) or not abilities:
        return float(BASE_SCORE)
    values = [_number(scores.get(name), BASE_SCORE) for name in abilities]
    return sum(values) / float(len(values))


def ability_percent(average, max_percent):
    """能力値の平均から賃金の倍率（%）。基準で 100、`TOP_SCORE` で `max_percent`、基準より下は `LOW_RATE_PERCENT` まで。"""
    average = _number(average, BASE_SCORE)
    top = max(100.0, float(_number(max_percent, 100)))
    if average >= BASE_SCORE:
        share = min(1.0, (average - BASE_SCORE) / float(TOP_SCORE - BASE_SCORE))
        return 100.0 + (top - 100.0) * share
    share = min(1.0, (BASE_SCORE - average) / float(BASE_SCORE - 1))
    return 100.0 - (100.0 - LOW_RATE_PERCENT) * share


def ability_words(abilities):
    """「筋力・耐久」。効く能力値が無ければ空。"""
    return "・".join(ABILITY_NAMES.get(name, name) for name in abilities or ())


def rank_index(days):
    """勤続の日数から段の番号（0 から）。"""
    days = _number(days)
    index = 0
    for number, row in enumerate(RANKS):
        if days >= row[1]:
            index = number
    return index


def rank_name(days):
    return RANKS[rank_index(days)][0]


def rank_pay_percent(days):
    return RANKS[rank_index(days)][2]


def rank_discount_percent(days):
    return RANKS[rank_index(days)][3]


def rank_risk_percent(days):
    return RANKS[rank_index(days)][4]


def days_to_next_rank(days):
    """次の段までの日数。最後の段なら None。"""
    index = rank_index(days)
    if index + 1 >= len(RANKS):
        return None
    return max(0, RANKS[index + 1][1] - int(_number(days)))


def incident_percent(event):
    """街の出来事から、役場の仕事の賃金の倍率（%）。出来事が無い・困り事なら 100。

    良い出来事は段が進むほど上がり（110・120・130 で頭打ち）、調子が悪い段（景気の崩れ）は 70、落ち着いた段は 100。
    """
    if not isinstance(event, dict) or not event.get("good"):
        return 100
    tone = event.get("tone")
    if tone == "bad":
        return INCIDENT_BAD_PERCENT
    if tone == "calm":
        return 100
    return min(max(100, INCIDENT_MAX_PERCENT),
               100 + INCIDENT_STEP_PERCENT * max(1, int(_number(event.get("stage"), 1))))


def is_trouble(event):
    """人手を求める困り事か（良い出来事でない）。"""
    return isinstance(event, dict) and not event.get("good")


def base_pay(labor, quest):
    """賃金の基準（29歳までの1回＝90日ぶん）。ゲームの労働の式の値 `labor` と、同じ難易度の依頼1件の報酬 `quest` の
    `QUEST_PAY_PERCENT` % の高い方。`quest` が読めなければ `labor` のまま。

    ゲームの式は依頼の報酬ほど難易度で伸びず（難易度 0→76 で依頼は約3,400倍、労働は75倍）、
    終盤は長い契約でも依頼1件に届かないため、依頼の報酬につなぐ。
    """
    value = float(_number(labor))
    if not isinstance(quest, bool) and isinstance(quest, (int, float)) and quest > 0:
        value = max(value, float(quest) * max(0.0, float(_number(QUEST_PAY_PERCENT))) / 100.0)
    return value


def term_pay(base, term_days, percents=()):
    """1回ぶんの賃金。`base` はゲームの式の値（29歳までの1回＝90日ぶん）。`percents` は掛ける倍率（%）の並び。1 未満にはしない。

    `term_days` には設定の長さ（年齢で伸ばす前）を渡す。ゲームと同じく、年齢で1回が長くなっても額は増やさない。
    """
    pay = float(_number(base)) * max(1, int(_number(term_days, GAME_TERM_DAYS))) / GAME_TERM_DAYS
    for percent in percents:
        pay *= float(_number(percent, 100)) / 100.0
    return max(1, int(round(pay)))


def illegal_pay_percent(danger, min_percent, max_percent):
    """危険度から裏の仕事の賃金の倍率（%）。危険度 1 で下限、`MAX_DANGER` で上限。"""
    low = float(_number(min_percent, 200))
    high = max(low, float(_number(max_percent, 400)))
    danger = min(MAX_DANGER, max(1, int(_number(danger, 1))))
    return low + (high - low) * (danger - 1) / float(MAX_DANGER - 1)


def finish_bonus(earned, long_contract, relief=False):
    """満了の割増の額。人手の仕事には付かない。"""
    if relief:
        return 0
    percent = FINISH_BONUS_PERCENT[1 if long_contract else 0]
    return int(_number(earned) * percent / 100.0)


def event_percent(percent_per_term, term_days=None):
    """1回（「働く」1回）で出来事が起きる確率（%）。1回の長さによらず同じ（`term_days` は使わない）。

    前の版は「90日あたり」で長さに比例させていたので、1回を短くするほど出来事が起きなくなった。
    """
    return max(0.0, min(100.0, float(_number(percent_per_term))))


def events_for(job_type):
    """その仕事で起きうる出来事。"""
    return [event for event in EVENTS if event["jobs"] is None or job_type in event["jobs"]]


def pick_event(rng, job_type, last_key=None):
    """出来事を1つ選ぶ。直前と同じものは続けない（ほかに候補があれば）。"""
    pool = events_for(job_type)
    if last_key is not None and len(pool) > 1:
        pool = [event for event in pool if event["key"] != last_key]
    if not pool:
        return None
    return pool[int(rng.random() * len(pool)) % len(pool)]


def check_percent(base, score):
    """判定の成功の確率（%）。能力値が基準から1上がるごとに `CHECK_PER_POINT` 足す。"""
    percent = _number(base, 50) + (_number(score, BASE_SCORE) - BASE_SCORE) * CHECK_PER_POINT
    return int(max(CHECK_MIN, min(CHECK_MAX, round(percent))))


def risk_percent(danger, average, term_days, rank_days=0):
    """裏の仕事の1回で危険が起きる確率（%）。能力値が高いほど・勤続が長いほど下がる。"""
    danger = min(MAX_DANGER, max(1, int(_number(danger, 1))))
    base = RISK_BASE_PERCENT[danger] * max(1, int(_number(term_days, GAME_ILLEGAL_TERM_DAYS))) \
        / float(GAME_ILLEGAL_TERM_DAYS)
    spread = (_number(average, BASE_SCORE) - BASE_SCORE) / float(TOP_SCORE - BASE_SCORE)
    factor = max(RISK_FACTOR_MIN, min(RISK_FACTOR_MAX, 1.0 - 0.6 * spread))
    percent = base * factor * rank_risk_percent(rank_days) / 100.0
    return max(0.0, min(float(RISK_PERCENT_CAP), percent))


def pick_risk(rng, job):
    """起きた危険の種類を、仕事ごとの重みで選ぶ。"""
    weights = [(name, max(0, int(_number(job["risks"].get(name))))) for name in RISKS]
    total = sum(weight for _name, weight in weights)
    if total <= 0:
        return CRACKDOWN
    point = rng.random() * total
    for name, weight in weights:
        point -= weight
        if point < 0:
            return name
    return weights[-1][0]


def risk_words(job):
    """「取り締まり・仕返し」。重みの大きい順に2つ。"""
    ordered = sorted(RISKS, key=lambda name: (-job["risks"].get(name, 0), RISKS.index(name)))
    return "・".join(RISK_NAMES[name] for name in ordered[:2])


# ---------------------------------------------------------------- 控え
# 周回（世界×主人公）ごとに1つ。
#   employers  {"<土地id>/<施設id>": {"days": 勤続の日数, "owner": 雇い主の人物 id, "barred_until": 一覧に戻る日}}
#   contract   進行中の契約（無ければ None）
#   last_event 直前に起きた出来事のキー（役場と闇労働の出来事で同じ欄。キーは重ならない）
#   last_done  {"<土地id>": 最後に務め終えた雇い主}（一覧の「前回の仕事」の枠）
#   towns      {"<土地id>": {"growth": その街の労働の実績, "done": 務め終えた契約の数}}
BUCKET_KEYS = ("employers", "contract", "last_event", "last_done", "towns")
TOWN_KEYS = ("growth", "done")
EMPLOYER_KEYS = ("days", "owner", "barred_until")
#   log        回ごとの結果 [{"pay", "kind", ...}]（契約の帯のマスと、雇い主の一言の頼みに使う）
#   rank_start 契約を始めたときの勤続の段（務め終えた帯に「勤続が上がった」を出すか決める）
#   icon       闇労働の仕事の様子のアイコンのパス（一覧で選んだ1枚を、契約の間ずっと帯に出す）
CONTRACT_KEYS = ("kind", "job", "employer", "name", "owner", "owner_name", "place", "abilities", "term_days",
                 "terms", "done", "pay", "earned", "long", "started", "area", "danger", "pending", "log",
                 "rank_start", "icon")
OFFICE = "office"
ILLEGAL = "illegal"


def new_bucket():
    return {"employers": {}, "contract": None, "last_event": None, "last_done": {}, "towns": {}}


def order_bucket(bucket):
    """書く直前に並びを固定する。"""
    fresh = new_bucket()
    source = bucket if isinstance(bucket, dict) else fresh
    employers = source.get("employers") if isinstance(source.get("employers"), dict) else {}
    out = {"employers": {}}
    for key in sorted(employers):
        row = employers[key] if isinstance(employers[key], dict) else {}
        out["employers"][str(key)] = {name: row.get(name) for name in EMPLOYER_KEYS}
    contract = source.get("contract")
    out["contract"] = ({name: contract.get(name) for name in CONTRACT_KEYS}
                       if isinstance(contract, dict) else None)
    out["last_event"] = source.get("last_event") if isinstance(source.get("last_event"), str) else None
    done = source.get("last_done") if isinstance(source.get("last_done"), dict) else {}
    out["last_done"] = {str(area): str(done[area]) for area in sorted(done, key=str)
                        if isinstance(done[area], str) and done[area]}
    towns = source.get("towns") if isinstance(source.get("towns"), dict) else {}
    out["towns"] = {str(area): {name: max(0, int(_number(towns[area].get(name)))) for name in TOWN_KEYS}
                    for area in sorted(towns, key=str) if isinstance(towns[area], dict)}
    return out


def rotation_of(day):
    """その日が、一覧の入れ替えの何回目に当たるか。"""
    return int(_number(day)) // max(1, int(ROTATE_DAYS))


def pick_listed(keys, seed_text, count=None):
    """一覧に並べる雇い主を選ぶ。同じ種（周回・土地・入れ替えの回）なら、開き直しても同じ顔ぶれ。

    渡された並びの順を保って返す。候補が `count` 以下なら全部。
    """
    keys = list(keys)
    count = max(0, int(LIST_JOBS if count is None else count))
    if len(keys) <= count:
        return keys
    picker = random.Random(zlib.crc32(str(seed_text).encode("utf-8")))
    chosen = set(picker.sample(sorted(keys), max(0, int(count))))
    return [key for key in keys if key in chosen]


def last_done(bucket, area_id):
    """その土地で最後に務め終えた雇い主。無ければ None。"""
    table = bucket.get("last_done") if isinstance(bucket, dict) else None
    value = table.get(str(area_id)) if isinstance(table, dict) else None
    return value if isinstance(value, str) and value else None


def set_last_done(bucket, area_id, employer):
    """その土地の「前回の仕事」を書く。務め終えた雇い主か、None（やめた・雇い主の無い仕事だった）。"""
    table = bucket.get("last_done")
    if not isinstance(table, dict):
        table = {}
        bucket["last_done"] = table
    if employer:
        table[str(area_id)] = str(employer)
    else:
        table.pop(str(area_id), None)


def _town(bucket, area_id, make=False):
    towns = bucket.get("towns") if isinstance(bucket, dict) else None
    if not isinstance(towns, dict):
        if not make:
            return None
        towns = {}
        bucket["towns"] = towns
    row = towns.get(str(area_id))
    if not isinstance(row, dict):
        if not make:
            return None
        row = {"growth": 0, "done": 0}
        towns[str(area_id)] = row
    return row


def town_growth(bucket, area_id):
    """その街の労働の実績（賃金の基準の難易度に足す数）。無ければ 0。"""
    row = _town(bucket, area_id)
    return max(0, int(_number(row.get("growth")))) if row is not None else 0


def town_done(bucket, area_id):
    """その街で務め終えた契約の数。"""
    row = _town(bucket, area_id)
    return max(0, int(_number(row.get("done")))) if row is not None else 0


def add_growth(bucket, area_id, step, cap):
    """契約を1つ務め終えた。その街の実績を `step` 足す（`cap` まで）。`(前の実績, 後の実績)`。

    上限を後から下げても、積んだ実績は減らさない（上限より上にあれば、そのまま増えないだけ）。
    """
    row = _town(bucket, area_id, make=True)
    before = max(0, int(_number(row.get("growth"))))
    after = max(before, min(max(0, int(_number(cap))), before + max(0, int(_number(step)))))
    row["growth"] = after
    row["done"] = max(0, int(_number(row.get("done")))) + 1
    return before, after


def employer_key(area_id, facility_id):
    return "{}/{}".format(area_id, facility_id)


def employer_row(bucket, key, owner_id=None):
    """その雇い主の控え。無ければ作る。雇い主の人物が替わっていたら勤続を消す。"""
    employers = bucket.setdefault("employers", {})
    row = employers.get(key)
    if not isinstance(row, dict):
        row = {"days": 0, "owner": None, "barred_until": None}
        employers[key] = row
    if owner_id is not None:
        owner_id = str(owner_id)
        if row.get("owner") not in (None, owner_id):
            row["days"] = 0
            row["barred_until"] = None
        row["owner"] = owner_id
    return row


def tenure_days(bucket, key, owner_id=None):
    """その雇い主での勤続の日数（読むだけ）。

    `owner_id` を渡すと、控えの雇い主の人物と違うときは 0（`employer_row` が次に消す勤続を、
    消す前に読んで使わない。値段のフックは控えを書かないのでこちらで見分ける）。
    """
    employers = bucket.get("employers") if isinstance(bucket, dict) else None
    row = employers.get(key) if isinstance(employers, dict) else None
    if not isinstance(row, dict):
        return 0
    if owner_id is not None and row.get("owner") not in (None, str(owner_id)):
        return 0
    return max(0, int(_number(row.get("days"))))


def add_tenure(bucket, key, days):
    """勤続を足す（負なら減らす）。0 より下にはしない。足した後の日数を返す。"""
    row = employer_row(bucket, key)
    row["days"] = max(0, int(_number(row.get("days"))) + int(_number(days)))
    return row["days"]


def barred(bucket, key, day):
    """途中でやめた雇い主が、まだ一覧に出ない間か。"""
    employers = bucket.get("employers") if isinstance(bucket, dict) else None
    row = employers.get(key) if isinstance(employers, dict) else None
    until = row.get("barred_until") if isinstance(row, dict) else None
    return isinstance(until, int) and not isinstance(until, bool) and day is not None and day < until
