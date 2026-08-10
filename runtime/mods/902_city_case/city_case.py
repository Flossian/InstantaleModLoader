# -*- coding: utf-8 -*-
"""街を回って調べる事件（最小の1件）。

ギルドで噂を聞くと事件が1件始まる。町の施設を回って聞き込み、手がかりが
揃ったら犯人を告発する。当たれば報酬が出て、犯人は退場する。

##### 設計の柱（実測に基づく。GAME.md §2.21 / §2.22）

**1. 判定は AI から取り上げる。**
選択肢を押すとシーンのマネージャに入り直し、**押した選択肢の飛び先ラベル**が
`resume={'kind':'goto','label':...}` で渡ってくる。手がかりごとにラベルを
分けておけば、それを見るだけで確実に判定できる。AI は描写しかしない。

`305_` は「AI に判定させて4周外した」記録が残っている（VERIFICATION.md
§2.19〜§2.22）。同じ轍を踏まないための一番大事な線引き。

**2. 真相は最初に決めきる。**
犯人も手がかりの連鎖も事件を組む時点で確定させ、`state/city_case.json` に持つ。
後から決めると AI の出力次第で真相が変わる。

**3. 開示していない情報は AI に渡さない。**
「言うな」と指示しても押し切られる。**知らせなければ喋れない。**
プロンプトに載せるのは、その時点で開示してよい分だけ。

**4. セーブを汚さない。**
プログラムは `_lookup_program` を包んで渡す（`free_facility_programs` には
書かない）。`flag_set` も使わない（施設の `config` に残るため）。
報酬は `309_` が実証した経路で直接渡す。事件の控えは `state/` だけ。

##### 犯人の退場

`Character.config['is_dead'] = True`。名簿からは外れないので参照は切れず、
それでいてゲーム内では会話にも呼び出しにも出てこない（実測で2例確認）。
移送も削除も要らず、戻すこともできる。

##### キャストは事件のために作る

**既存の NPC を犯人に仕立ててはいけない。**機能としては動くが、プレイヤーが
愛着を持っている相手が犯人にされうる。そうなった時点で、この遊びは受け入れ
られなくなる（利用者の指摘）。実際、最初の実機では
`混乱する村人たち` という群衆が犯人に選ばれていて、既存 NPC から選ぶ設計の
筋の悪さが2つ同時に出ていた。

だからキャストは事件のために生成し、**作れなければ事件を始めない**。
既存 NPC に落とすと問題がそのまま戻る。始まらないほうがましだと判断している。

生成した NPC は HP もスキルも立ち絵も空でよい ― ゲームが会話の直前に
`ensure_npc_detail_generated` で埋める（実測。VERIFICATION.md §2.29）。
事件が終われば犯人は `is_dead` で退場し、無実の登場人物は町に残る
（世界の住人が少し増えるだけで、誰の思い入れも壊さない）。

既存 NPC を使う経路は持たない。愛着の問題に加えて、**推理が成立しない**ため
（手がかりは容疑者の特徴との突き合わせなので、特徴の分からない相手は
容疑者にできない）。
"""

import datetime
import itertools
import random
import sys
import threading
import time

from instantale_modloader import frames, ui

from . import case as case_mod
from . import ledger
from . import patterns
from . import world as game
from . import writer

# **この MOD 専用の乱数器。** グローバルの `random` を引くとゲーム自身の
# 乱数列がずれる（TECH.md §6.1。`104_balance_area_bgm` と同じ方針）。事件は
# 1回組むたびに何十回も引くので、混ぜると影響が積み上がる。
_RNG = random.Random()

LOG_BASENAME = "city_case.log"

#: 進行中の事件の控え。置き場は `state/`（`ctx.state_path`）。
RECORD_BASENAME = "city_case.json"

#: この MOD が作った NPC の台帳。**セーブの外に持つ**（`ledger` の冒頭）。
#: NPC 自身に印を持たせられないため（項目を足すと33項目の並びが壊れる。
#: GAME.md §2.23）、掃除の手がかりはここにしか無い。**消すと片付けられなく
#: なる**ので、ログと同じ `out/` ではなく `state/` に置く。
LEDGER_BASENAME = "city_case_cast.json"

#: 描写を書かせる相手。**ゲームのプロンプトは流用しない** ―
#: `send_request(manager_name, message, structure)` は名前・頼み文・
#: 応答の型を全部こちらで決められる汎用の入口（リコンの署名より）。
LLM_MODULE = "scripts.llm.llm_manager"

#: 押下を横取りするための印。**他の MOD と別のキーにすること**
#: （`301_` mod_action / `302_` mod_party_action / `305_` mod_mini_action /
#: `309_` mod_pardon_action）。
MARK = "mod_city_case_action"

# ---------------------------------------------------------------- 設定（mod.json）

#: 事件を受けられる施設。
START_FACILITY_TYPE = "guild"

#: 解決したときの報酬。
REWARD_GOLD = 500

#: 犯人を退場させるか。切ると真相が分かるだけで、犯人は町に残る。
ARREST_CULPRIT = True

#: 容疑者の人数（犯人を含む）。
#:
#: 多いほど手がかりが増え、聞き込みも長くなる。特徴の軸は人数から決まる
#: （`patterns.axes_wanted`）ので、増やすと一覧の語も増える。
SUSPECT_COUNT = 4

# ---------------------------------------------------------------- 事件の型
#: 手がかりを置ける施設の種類。**町にある施設からしか選ばない。**
#:
#: 町の構成は世界ごとに違う（闇市や診療所が無い町がある。利用者の報告）。
#: 無い施設に手がかりを置くとその事件は永久に解けないので、
#: 事件を組むときに `world.facility_types_in` と突き合わせて決める。
#:
#: 並び順は優先順位。**上から、その町に在るものを使う。**
#: 証言者はその施設の主で、**話しかければ話す**（専用のボタンは要らない）。
CLUE_POOL = (
    "inn", "underworld_office", "general_store", "blacksmith",
    "medical_facility", "administrative_office", "specialty_shop", "guild",
)

# ---------------------------------------------------------------- 画面に出す文言
START_LABEL = "気になる噂を聞く"
ACCUSE_LABEL = "犯人を告発する"
STATUS_LABEL = "調べの進み具合を確かめる"
#: 告発をやめて調べに戻る。**事件は続く。**
LEAVE_LABEL = "やめる（もう少し考える）"
#: 事件そのものを打ち切る。**戻れない。**だから何が起きるかを文言に入れる。
GIVE_UP_LABEL = "諦める（調べを打ち切る）"
FAREWELL = "（その場を離れた）"

START_TEXT = "掲示板の隅に、まだ依頼になっていない噂が貼られている。"
#: 題材が引けなかったときの最後の受け皿（普通は `patterns/incidents.json`）。
OPENED_TEXT = "「この町で盗みが続いている。誰がやっているのか、調べてほしい」"
HINT_TEXT = "（分かったこと {found}/{total}）"
GAINED_TEXT = "【分かったこと】{text}"

#: 容疑者の一覧。**特徴を必ず添える** ― これが無いと手がかりと
#: 突き合わせられず、推理が成立しない。
ROSTER_HEAD_TEXT = "疑わしいのはこの者たちだ。"
ROSTER_LINE_TEXT = "  ・{name}（{tell}）"
#: 居場所が分かるときの行。**どこに居るかを必ず添える。**
#: 名前と特徴だけだと、聞き込みに回る前に町を端から探すことになる
#: （利用者の指摘）。絞り込みは推理でも、**人を見つけるのは
#: 作業**なので、そこは省いてよい。
ROSTER_LINE_AT_TEXT = "  ・{name}（{tell}）― {place}に居る"
ROSTER_HINT_TEXT = "（誰が当てはまるかは、集めた話と見比べて決めること）"

#: **行き先の案内。**これが無いと、プレイヤーは町を無策で歩き回ることになる
#: （利用者の指摘）。施設は種類ではなく**名前**で示す ―
#: 移動先の選択肢に出ている文字列とそのまま突き合わせられるように。
#: **行き先は示すが、順番は決めない。**まだ話を聞いていない場所を並べるだけで、
#: どこから当たるかはプレイヤーが決める。道順を1つに絞ると、また作業に戻る。
LEAD_TEXT = "話を聞けそうなのは{places}。"
LEAD_FALLBACK_TEXT = "この町のどこかに、話を知っている者がいるはずだ。"
GO_ACCUSE_TEXT = "誰の仕業か分かったら、{place}で告げればいい。"

#: 進み具合の確認。**忘れても取り戻せるようにする。**
#: 事件は町をまたいで続くし、ゲームを閉じて再開することもある。
STATUS_HEAD_TEXT = "いま分かっていること:"
STATUS_FOUND_TEXT = "  ・{text}"
STATUS_NONE_TEXT = "  （まだ何も）"
#: 聞いた言い分。**覚えていられないので必ず控える。**
STATUS_CLAIM_HEAD_TEXT = "聞いた言い分:"
STATUS_CLAIM_TEXT = "  ・{name}「{claim}」"
CLAIM_HEARD_TEXT = "【言い分】{name}は{claim}と言った。"
ACCUSE_TEXT = "誰の仕業だったのかを告げる。"
#: 告発の相手を選ぶボタン。**特徴を必ず添える** ― 集めた事実と突き合わせるのが
#: プレイヤーの仕事なので、一覧と同じ書き方にする。
#:
#: **決まった接頭辞を付けること**（`ACCUSE_ONE_PREFIX`）。ボタンはセーブに
#: 焼かれることがあり、そのとき印は落ちる（`ui.Screen.prune_stale`）。残骸を
#: 見分ける手段は文言しかないが、容疑者の名前は事件ごとに変わるので
#: 名前だけでは照合できない。固定の頭を付けておけば前方一致で拾える。
ACCUSE_ONE_PREFIX = "告発する ― "
ACCUSE_ONE_LABEL = ACCUSE_ONE_PREFIX + "{name}（{tell}）"
RIGHT_TEXT = "{name}は言い逃れをやめた。町の者に引き渡される。"
WRONG_TEXT = "{name}は身に覚えが無いと言い、周りもそれを認めた。"
#: **一度外したら終わり。**やり直せると総当たりで解けてしまう。
FAILED_TEXT = "騒ぎの間に、本当の下手人 ―― {name}は町を出ていった。"
REWARD_TEXT = "報酬として{gold}ゴールドを受け取った。"
NO_CASE_TEXT = "（いまは調べていることは無い）"

#: 集めた話が食い違ったときに出す一言。**どれが嘘かは言わない。**
#: 何も言わないと「詰んだ」「壊れている」と受け取られるので、気づく
#: きっかけだけは渡す。外して数えるのはプレイヤーの仕事。
CONTRADICTION_TEXT = ("……おかしい。聞いた話を全部そのまま信じると、"
                      "誰ひとり当てはまらない。**どれかが思い違いだ。**")

#: 決着したあと、キャストが町を去る文。**消えたことを黙って済ませない。**
#: 事件のために現れた者が居なくなるのは、プレイヤーから見れば世界の変化。
#: 何も言わずに消すと「居たはずの人が消えた」という不具合に見える。
DEPARTED_TEXT = "騒ぎが収まると、事件で名の挙がった者たちは町を離れていった。"

#: 調べを打ち切ったときの文。**真相は明かさない。**
#: 告発すれば当たっても外れても真相が分かるので、そこまで踏み込まずに
#: 降りたのなら、答えは持ち帰れないほうが筋が通る。
ABANDON_TEXT = "「すまない、この件からは手を引く」――調べはそこで途切れた。"

#: 描写を書かせているあいだの一言。**待たせるなら黙らない。**
#: 数秒〜数十秒かかるので、何も出ないと固まったように見える。
WRITING_TEXT = "（噂の出どころを当たっている……）"

#: 待ち時間を過ぎても返ってこなかったときの一言。**黙って解かない** ―
#: 押せるようになった理由が分からないと、何が起きたのか掴めない。
WRITING_SLOW_TEXT = "（今日は誰も口を割らなかった。出直すことにする）"

#: 断る理由ごとに文を分ける。**同じ文が別々の理由で出ると切り分けられない**
#: （実機で「町に調べようがない」と出たが、実際は町ではなくキャスト生成の
#: 失敗だった）。画面の文だけで、ログを開くべきかが分かるようにする。
NO_PLACE_TEXT = "「この町には、聞いて回れる場所が無い」"
NO_CAST_TEXT = "（噂は立ち消えになった ― out/city_case.log を見ること）"

#: ここが真の間はボタンを出さない（`309_` と同じ）。
# ここが真の間は選択肢を足さない。表はローダが持つ（`309_` と共有）。
BUSY_FLAGS = ui.BUSY_FLAGS

#: 施設の選択肢だと見なす目印。**文字列ではなく spec のクラス名で見る。**
FACILITY_MARK = "MovePhaseManager"

#: 容疑者に話しかけたときに差し込む言い分。
#:
#: **容疑者は自分の立場を語る。**事件のために作った人物が町に立っているのに
#: 話しかけても何も起きないのでは、探偵ものにならない（利用者の指摘）。
#: **嘘はつかせない** ― 言い分そのものは真実で、裏が取れるか
#: どうかだけが違う。AI に嘘を任せると真相が揺れる。
#: 容疑者が語る自分の立場。**証言者と同じく「必ず話す」にする。**
#:
#: 「問われたら答える」だと、控えに `heard` が立つ条件（会話した）と
#: 噛み合わない ― 尋ねなければ何も言われないのに言い分だけ控えに残る。
#: 印を立てるのは実際に話しかけて何か尋ねたときだけになったので
#: （`brief`）、その1回で必ず言わせる。
SUSPECT_TEXT = ("【この人物の立場】この者は町で起きた{crime}の件で疑われている。"
                "身に覚えは無いと思っており、この会話の中でそう述べる。"
                "そして**自分から**その晩の居場所として「{claim}」と語る。"
                "**この一文の内容は変えないこと。**"
                "他人を名指しで疑うことはしない。")

#: 会話に差し込む知識。**開示してよい事実だけ**を渡す（設計の柱3）。
#:
#: 差し込む先は2つ。開始のひとこと（`conversation_starter` の messages）と、
#: **受け答えを組むときの `retrieved_knowledge`** ― 後者はゲーム自身が持って
#: いる「この人物が引き出した知識」の枠で、プレイヤーが直接尋ねたときにも効く。
#: 証言者に喋らせる事実。**「尋ねられたら」にしない。**
#:
#: 以前は「尋ねられたら話す」だった。ところが手がかりが立つ条件は
#: 「その人物と会話した」という行動なので、**プレイヤーが尋ねなければ
#: 相手は何も言わないのに、会話を閉じた瞬間に手がかりだけが出る。**
#: 実機で「会話内容と得られたヒントが全くかみ合わない」ことになった
#: （利用者の指摘）。
#:
#: 判定を会話の中身に寄せる道もあるが、それは `305_` が4周外した路
#: （AI に判定させる）に戻ることになる。**判定はこちらが持ったまま、
#: AI の側を確実にする** ― 必ず自分から言わせる。
#: 第一声に差し込む匂わせ。**事実そのものは言わせない。**
#:
#: 会話に入って即抜けるだけで手がかりが手に入るのを止めるため、中身は
#: 尋ねられてから話させる（`conversation_starter` の註）。**代わりに
#: 「尋ねれば話す」ことは分かるようにする** ― 匂わせも無いと、
#: 誰に聞けばよいのか分からないまま町を歩くことになる。
TEASE_TEXT = ("【この人物の様子】この人物は町で続いている{crime}について"
              "何か見聞きしているが、**尋ねられるまでは自分から詳しく話さない**。"
              "尋ねられていない段階では、何か言いたげな素振りを見せるにとどめ、"
              "見たことの中身は言わないこと。")

KNOWS_TEXT = ("【この人物が必ず話すこと】この人物は、この会話の中で"
              "**自分から**次のことを話す: {fact}。"
              "尋ねられていなくても、世間話のついでに自分から切り出す。"
              "自分が見た・聞いたこととして具体的に語ること。"
              "**この内容は変えないこと。**"
              "ただし犯人が誰かは知らないので、名指しはしない。")

#: 事件の描写を LLM に書かせるか。
#:
#: 切ると `patterns/cast.json` の顔ぶれから選び、文言も定型になる。
#: 入れると事件ごとにその世界に合った人物と
#: 文章が作られる。**論理は入れても切っても同じ**（コードが決めている）。
#:
#: 呼べなかった・返事が壊れていた・検算に落ちたときは**黙って下地に戻る**。
USE_LLM = True

#: LLM の返事を待つ秒数。**待ちすぎない。**
#: 超えたら下地で事件を始める ― 始まらないより定型で始まるほうがよい。
LLM_TIMEOUT = 120

#: 待機表示を強制的に解くまでの上乗せ（秒）。
#:
#: **押せない画面のまま放置しないための最後の網。**`send_request` が
#: `timeout` を守る保証はこちらには無いし、スレッドが固まる可能性もある。
#: `LLM_TIMEOUT` を過ぎてこれだけ待っても返らなければ、操作だけ返す。
BUSY_GRACE = 15

#: 思い違いの証言が混じる割合（％）。
#:
#: 混じると、**集めた話をすべて信じたときに誰も当てはまらなくなる**。
#: そこで「どれが嘘か」を探すことになり、消し込みが推論に変わる。
#:
#: **毎回混ぜない**のが要点。always だと「1本は嘘」が前提知識になって、
#: 食い違いに気づく驚きが無くなる。0 にすると従来どおり全部が真になる。
#:
#: 一意に決まらない置き方には混ぜない（`resolves_once`）ので、この値を
#: 上げても**当てずっぽうでしか解けない事件は出ない**。
MISTAKE_PERCENT = 50

#: 聞き込み中に走る会話の経路を計測するか。
#:
#: 届かないときの切り分け用。**普段は切っておく**（ログが会話のたびに伸びる）。
#: 入れると、聞き込み中に走った LLM の入口と、証言者の照合が外れた理由が
#: `out/city_case.log` に出る。
WATCH_CONVERSATION = False

#: 計測する会話まわりの LLM 入口（リコンの署名より）。
#: **知識を差し込む2つは別に扱う**ので、ここには重複して入れない。
CONVERSATION_ENTRIES = (
    "conversation_resolver",
    "conversation_facilitator_in_quest",
    "conversation_join_message",
    "conversation_become_family_response",
    "master_ai_facilitator_from_conversation",
    "conversation_summarizer_in_conversation",
)

#: 聞き込みの印が古くなったら使わない（押してから会話が始まるまでの間に
#: 別の会話が割り込んだ場合に、そちらへ差し込んでしまうのを防ぐ）。
ASK_TTL = 300.0

#: 顔ぶれと特徴を組み直す試行回数。条件を満たすまで振り直す。
#:
#: 40 では足りない。`build_cast` の条件に「どの手がかりも抜けない」を足した
#: 時点で、通る顔ぶれの割合が下がった（試算で 40 回だと 3〜8 人のどの設定でも
#: 1〜19% が組めずに終わる）。300 回にすると全設定で取りこぼしが消える。
#: 振り直しは町に出る前の計算だけで、NPC はまだ作っていない ― 何回まわしても
#: 世界には何も残らない。
CAST_ATTEMPTS = 300

#: 告発の飛び先ラベルの接頭辞。`accuse_<npc_id>` になる。
ACCUSE_PREFIX = "accuse_"

#: この MOD が出すボタンの文言。**残骸の掃除に使う**（`ui.Screen.prune_stale`）。
#: セーブに焼かれたボタンは印を失うので、文言で見分けるしかない。
OUR_LABELS = (START_LABEL, ACCUSE_LABEL, STATUS_LABEL,
              ACCUSE_ONE_PREFIX, LEAVE_LABEL, GIVE_UP_LABEL)


def has_clock():
    """Kivy の `Clock` が使えるか。**オフライン検証では使えない。**"""
    try:
        from kivy.clock import Clock          # noqa: F401
        return True
    except Exception:
        return False


money = ui.money          # 金額の表示（`309_` と共有）


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    record_path = ctx.state_path(RECORD_BASENAME)
    ledger_path = ctx.state_path(LEDGER_BASENAME)
    # **いま控える。**`ctx.mod_dir` は `apply()` の外では `None` になる
    # （ローダの註）。材料を読むのはボタンが押されてからなので、そのとき
    # 取りに行くと `None` を掴んで**ファイルが1つも見つからない**。
    # 実機でこれを踏んだ。
    mod_dir = getattr(ctx, "mod_dir", None)

    write = ctx.logger(LOG_BASENAME, stamp=False)

    screen = ui.Screen(ctx, write, tag="city case", mark=MARK)
    state = {"case": None, "loaded": False, "accusing": False,
             "asking": None, "app": None, "book": None, "swept": None,
             "deadline": None, "stash": None, "patterns": None,
             "declined": None, "ignored": None,
             "axes": None, "words": None, "facts": None,
             "hints": None, "looks": None, "shorts": None,
             "incident": None}

    def stamp():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    # ------------------------------------------------------------ 控え
    def current(app):
        """いまの世界の事件。**違う世界のものは捨てる。**"""
        if not state["loaded"]:
            state["case"] = case_mod.load(record_path)
            state["loaded"] = True
        found = state["case"] or case_mod.empty()
        name = game.world_name(app)
        if case_mod.is_active(found) and name and not case_mod.belongs_to(found, name):
            found = drop(app, found, "it belongs to another world ({!r})".format(
                found.get("world")))
        elif case_mod.is_active(found) and backfill(app, found):
            pass
        elif case_mod.is_active(found) and not reachable(app, found):
            # **解けない事件を抱えたままにしない。**
            # 手がかりの置き場所が町に無いと永久に詰む。古い版が組んだ控えや、
            # `CLUE_POOL` を書き換えた後の控えがこれになる（実際に踏んだ）。黙って捨てて、
            # 次の依頼から組み直す。
            found = drop(app, found, "its clues point at facilities this town "
                                     "does not have")
        return found

    def backfill(app, found):
        """古い控えに足りない項目を埋める。**戻り値は「埋めた」かどうか。**

        控えは注入をまたいで残るので、**進行中の事件は古い版が作ったもの**
        でありうる。証言者（`witness`）を後から足したとき、進行中の事件は
        `None` のままで照合が永久に外れた（実機）。
        捨てると遊びの途中が消えるので、埋めて続けられるようにする。
        """
        filled = []
        for clue in found.get("clues", []):
            if clue.get("witness"):
                continue
            owner = game.owner_in(app, found.get("area"), clue.get("at_type"))
            if owner:
                clue["witness"] = owner
                filled.append("{}={}".format(clue.get("id"), owner))
        if filled:
            store(app)
            write("[{}] backfilled witnesses: {}".format(stamp(), filled))
        return bool(filled)

    def reachable(app, found):
        """残っている手がかりが全部この町で拾えるか。

        `case.solvable`（1人に絞れているか）とは別物。
        """
        if found.get("area") != game.area_id(app):
            return True             # 別の町に居るだけ。判断はその町へ戻ってから
        available = game.facility_types_in(app, found.get("area"))
        return all(clue.get("at_type") in available
                   for clue in found.get("clues", []) if not clue.get("found"))

    def drop(app, found, why):
        write("[{}] dropping the case: {}".format(stamp(), why))
        empty = case_mod.empty()
        state["case"] = empty
        case_mod.save(record_path, empty)
        return empty

    def store(app):
        case_mod.save(record_path, state["case"] or case_mod.empty())

    # ------------------------------------------------------------ 台帳と後始末
    def book():
        """作った NPC の台帳。**セーブの外に持つ**（`ledger` の冒頭）。"""
        if state["book"] is None:
            state["book"] = ledger.load(ledger_path)
        return state["book"]

    def book_store():
        ledger.save(ledger_path, book())

    def enroll(app, npc_id, name):
        """作った1体を台帳に控える。**作った直後に呼ぶ。**

        事件の控えに書く前に落ちても掃除できるように、ここで確定させる。
        """
        if ledger.add(book(), game.world_name(app), npc_id, name):
            book_store()

    def retire(app, npc_ids, why):
        """役目を終えた NPC を**世界から消して**、台帳からも外す。

        `set_dead` で印を立てるだけだと、繰り返し遊ぶぶんセーブに溜まり続ける
        （1件4体・1体あたり 1.4〜8KB。利用者の指摘）。
        消せる根拠は `world.remove_npc` の冒頭。
        """
        name = game.world_name(app)
        done, kept = [], []
        for npc_id in list(npc_ids):
            if game.remove_npc(app, npc_id, write=write):
                ledger.drop(book(), name, npc_id)
                done.append(str(npc_id))
            else:
                kept.append(str(npc_id))
        if done or kept:
            book_store()
            write("[{}] retired {} npc(s) ({}): {}{}".format(
                stamp(), len(done), why, done,
                "; could not remove {}".format(kept) if kept else ""))
        return done

    def sweep(app):
        """**置き去りを掃除する。**進行中の事件のキャストには手を出さない。

        対象は2つ。台帳に載っているのに事件から外れた者と、**台帳より前に
        作られた者**（名前が下地と完全一致するもの。`ledger.legacy_ids`）。
        後者を名前で拾うのは、印を NPC 自身に持たせられないため ―
        項目を足すと33項目の並びが壊れる（GAME.md §2.23）。

        遊んでいる最中に走らせない。**世界が読み込まれた直後の1回だけ**で、
        事件の最中に人が消えると何が起きたか分からなくなる。
        """
        name = game.world_name(app)
        if not name or state["swept"] == name:
            return
        state["swept"] = name
        found = current(app)
        active = set(case_mod.suspect_ids(found)) if case_mod.is_active(found) else set()

        stale = [i for i in ledger.ids(book(), name, kept=False)
                 if i not in active]
        # 名前で拾う掃除からは、**台帳に載っている者を全部外す** ―
        # 「残す」と決めた者を拾ってしまわないように。
        known = set(ledger.ids(book(), name)) | active
        npcs = game.save_npcs(app)
        legacy = ledger.legacy_ids(
            npcs, {base["name"] for base in (book_of(app).get("cast") or [])},
            skip=known)
        if legacy:
            write("[{}] found {} npc(s) with a cast name but no ledger entry "
                  "(made before the ledger existed): {}".format(
                      stamp(), len(legacy), legacy))
        target = stale + legacy
        if target:
            retire(app, target, "left over from an earlier case")

    # ------------------------------------------------------------ 材料を引く
    def book_of(app):
        """材料の束（`patterns/*.json`）。**一度だけ読む。**"""
        if state["patterns"] is None:
            state["patterns"] = patterns.load(
                mod_dir, defaults=BUILT_IN, write=write)
        return state["patterns"]

    def draw_axes(app):
        """この事件で使う特徴の軸を引き、**表を state に載せる。**

        軸は事件ごとに変わる（髪と体格の回もあれば、服の色と持ち物と声の
        回もある）。`tell_of` / `plan_facts` / `fact_order` はここで載せた表
        だけを見るので、**軸を増やしてもコードは触らなくてよい。**
        """
        axes = patterns.draw_axes(
            book_of(app), _RNG, patterns.axes_wanted(SUSPECT_COUNT))
        words, facts, hints, looks, shorts = patterns.tables_for(axes)
        state.update({"axes": axes, "words": words, "facts": facts,
                      "hints": hints, "looks": looks, "shorts": shorts})
        return axes

    def draw_incident(app):
        """この事件の題材を引く。文言と、AI への説明に使う。"""
        found = patterns.draw_incident(book_of(app), _RNG)
        state["incident"] = found or dict(BUILT_IN["incidents"][0])
        return state["incident"]

    def incident():
        return state.get("incident") or BUILT_IN["incidents"][0]

    def crime():
        """文中に差し込む短い語（「盗み」「付け火」）。"""
        return incident().get("noun", "盗み")

    def whereabouts():
        return (book_of(state.get("app")).get("whereabouts")
                or BUILT_IN["whereabouts"])

    def alibi_fact():
        return whereabouts().get("alibi_fact")

    # ------------------------------------------------------------ 事件を組む
    def build_cast(app, area):
        """事件ごとに**顔ぶれと特徴を組み直す。**

        下地（`patterns/cast.json`）から人数分を選び、特徴をその場で割り当てる。
        名前や素性に特徴を焼き付けていないので、同じ人物でも回によって
        片腕だったり大柄だったりする ― **2件目が同じ事件にならない。**

        組み方の条件は4つ。全部満たすまで振り直す:
          - **どの事実1つでも犯人が特定できない**（推理が1手で終わらない）
          - **全部集めれば1人に決まる**（詰んだ事件を出さない）
          - **どの事実も抜けない**（下記）
          - **引いた軸がどれも何かを分けている**（一覧に無意味な語を並べない）

        ##### 双子はやめた

        以前は「犯人と特徴が完全に一致する者」を1人必ず置いていた。特徴だけ
        では犯人を決められなくして、裏取り（アリバイ）を要る形にするため。

        **面白さに寄与していなかった**（利用者の指摘）。一覧に同じ行が2つ
        並ぶだけで、そこから考えることが増えない。外したので、それを支える
        ためだけに在った「裏取りが必ず1本要る」も一緒に外れた ―
        **裏取りは、特徴で消しきれない者が居るときにだけ出る**。

        ##### 振らずに置く

        条件を満たす配り方は狭い。3人を無作為に振って「全員が1軸だけ違い、
        しかもその軸が全員バラバラ」になる見込みは 6/512（約 1%）で、
        振り直しに頼ると取りこぼす。だから**最初からその形に置く**
        （`lay_out`）。振り直しは、置いた形が条件に落ちたときの保険。

        ##### 「集めれば解ける」だけでは足りない

        最初は上2つと「全部集めれば絞れる」しか見ていなかった。それだと
        **同じ働きしかしない事実が並ぶ**。実機で出た事件がこれ:

            容疑者 4人とも大柄。76/77 は黒髪の女、78/79 は淡い髪の男
            c1 犯人の髪は黒かった  -> 78, 79 が消える
            c2 犯人は女だった      -> 78, 79 が消える  ← c1 と全く同じ
            c3 ハルカのアリバイ    -> 77 が消える

        条件は全部通る（集めれば 76 に決まる）のに、**c1 と c2 はどちらか
        一方で足りる。**もう片方のために町を横断するぶんは、まるごと無駄足。
        「大柄」も4人全員なので一覧の文字が1つ増えるだけで何も決めない。

        そこで「**どの事実も抜けない**」を条件に足した。1本でも欠けたら
        絞れない、という形にしておけば、集めた手がかりが全部きちんと働く。
        併せて**最低2本**（容疑者が2人以上のとき）を要求する ― 1本で
        終わる事件は、拾った時点で答えが出てしまう。3本にしないのは下の
        `least` の註のとおりで、求めると犯人が多数派で割れる。

        試算では 3〜8 人のどの設定でも `CAST_ATTEMPTS` 回で必ず組める。
        """
        wanted = max(2, SUSPECT_COUNT)
        # **手がかりの最低本数。**1本で終わる事件を出さないための下限。
        # 3本を求めると「全員が1軸だけ違う」形しか通らなくなり、犯人が
        # 多数派で割れてしまう（`lay_out` の註）。2本あれば2段階で絞れる。
        least = min(2, wanted - 1)
        draw_incident(app)
        pool = book_of(app).get("cast") or []
        drawn = draw_axes(app)
        for _attempt in range(CAST_ATTEMPTS):
            picks = _RNG.sample(list(pool), min(wanted, len(pool)))
            cast = lay_out(picks, drawn)
            tells = [tell_of(member["traits"]) for member in cast]
            if len(set(tells)) != len(tells):
                continue                # 見分けの付かない者が居る
            if not all_telling(cast, drawn):
                continue                # 全員同じ値の軸がある（一覧が濁る）
            if not not_obvious(cast, drawn):
                continue                # 一覧だけで犯人が割れる
            facts, usable = choose_facts(cast, plan_facts(cast, 0))
            if not usable or len(facts) < least:
                continue                # 覆えない／薄すぎる
            # **思い違いを混ぜる。**混ぜられない置き方なら、そのまま真の
            # 証言だけで出す（事件が始まらないより、素直な事件のほうがよい）。
            if _RNG.randrange(100) < MISTAKE_PERCENT:
                facts = add_mistake(cast, drawn, facts)
            for member, tell in zip(cast, tells):
                member["tell"] = tell
            return cast, facts
        return [], []

    def tell_of(traits):
        """特徴を1行にする。容疑者一覧に出るのはこれ。

        **引いた軸の順に並べて、最後に性別。**軸は事件ごとに変わるので、
        並びも事件ごとに変わる（`state['axes']` の順）。

        **区切って並べる**（「黒髪・大柄・男」）。連体形のまま繋ぐと
        「目立つ色の服の煙のにおいのする袋を提げた女」のような一続きの句に
        なり、集めた事実と**突き合わせるのに読み解きが要る** ― そこが
        この遊びの本体なので、並べ方で邪魔をしない。
        """
        shorts = state.get("shorts") or {}
        parts = [shorts.get((axis["id"], traits.get(axis["id"])), "")
                 for axis in (state.get("axes") or [])]
        parts.append(SEX_WORDS.get(traits.get("sex"), ""))
        return "・".join(p for p in parts if p)

    def spec_of(member):
        """生成に渡す形にする。**特徴は立ち絵の語にも足す。**"""
        traits = member["traits"]
        looks = [member["look"]]
        for axis in (state.get("axes") or []):
            word = (state.get("looks") or {}).get(
                (axis["id"], traits.get(axis["id"])))
            if word:
                looks.insert(0, word)
        return {"name": member["name"], "category": member["category"],
                "job": member["job"], "profile": member["profile"],
                "personality": member["personality"],
                "look_description": member["profile"],
                "look": ["1{}, solo, {}, highly detailed".format(
                    "man" if traits.get("sex") == "man" else "woman",
                    ", ".join(looks))],
                "ability_scores": {"strength": 10, "dexterity": 10,
                                   "constitution": 10, "intelligence": 10,
                                   "wisdom": 10, "charisma": 10},
                "experience_level": 9, "age": 20}

    def plan_facts(cast, culprit_index):
        """犯人についての事実を組む。**1つでは決まらないように。**

        特徴による除外を並べ、最後に裏取り（アリバイ）を足す。狙いは
        「**どれか1つでは絞れないが、組み合わせると1人に決まる**」形。

        1つで決まる事実（その特徴を持つのが犯人だけ）は捨てる ―
        それが出た瞬間に推理が終わってしまう。
        """
        culprit = cast[culprit_index]
        others = [i for i in range(len(cast)) if i != culprit_index]
        facts = []
        for key, value in sorted(culprit.get("traits", {}).items()):
            text = (state.get("facts") or {}).get((key, value))
            if not text:
                continue        # `sex` はここに無い（引いた軸だけを使う）
            drops = [i for i in others
                     if cast[i].get("traits", {}).get(key) != value]
            if not drops or len(drops) == len(others):
                continue        # 誰も消えない／一発で決まる。どちらも要らない
            # `axis` / `value` は**頼み文と検算のため**に持つ（`fact_order`）。
            # 事件の論理そのものには使わない。
            facts.append({"kind": "trait", "fact": text, "drops": drops,
                          "axis": key, "value": value})

        # 裏取り。**特徴では消えなかった者**を消すのに使う。
        left = set(others)
        for fact in facts:
            left -= set(fact["drops"])
        if left:
            names = [cast[i]["name"] for i in sorted(left)]
            facts.append({"kind": "alibi",
                          "fact": alibi_fact().format(names="と".join(names)),
                          "drops": sorted(left)})
        return facts

    def lay_out(picks, axes):
        """特徴を配る。**自由に振る**（形は後で選ぶ）。

        以前は「他の者は犯人と1軸だけ違う」形に**置いて**いた。それだと
        手がかりが必ず1人ずつを消す形になり、条件は綺麗に満たされる ―
        代わりに**犯人が「どの軸でも多数派」の1人**になる:

              犯人   a1 b1 c1     ← a1 が3人、b1 が3人、c1 が3人
              2人目  a2 b1 c1
              3人目  a1 b2 c1
              4人目  a1 b1 c2

        一覧を眺めるだけで、**手がかり0本で犯人が分かる。**双子より悪い。

        だからここでは自由に振り、**どの手がかりを使うかを後で選ぶ**
        （`choose_facts`）。使わなかった軸は赤鰊として一覧に残る。
        """
        cast = [dict(base, traits={"sex": base["sex"]}) for base in picks]
        for member in cast:
            for axis in axes:
                member["traits"][axis["id"]] = _RNG.choice(
                    [v["id"] for v in axis["values"]])
        return cast

    def choose_facts(cast, candidates):
        """使う手がかりを**選ぶ**。`(選んだもの, 使えるか)`。

        条件は2つだけ:

          - 全部集めれば犯人1人に絞れる（**覆う**）
          - **どれも抜けない**（1本でも欠けたら絞れない）

        候補を全部使おうとすると、条件を満たす配り方が
        「全員が1軸だけ違う」しか無くなり、犯人が多数派で割れる（上）。
        **選ぶ側に回れば、配り方は自由でよい。**選ばなかった軸は一覧に
        残って赤鰊になる ― 見た目は違うのに、それを言う証言が無い。

        候補は多くて4本なので、部分集合を全部試して**いちばん本数の多い
        ものを採る**（拾う手が多いほうが遊びとして厚い）。
        """
        others = [i for i in range(len(cast)) if i != 0]
        best = []
        for size in range(len(candidates), 0, -1):
            picks = [combo for combo in itertools.combinations(candidates, size)
                     if covers(others, combo) and needed(others, combo)]
            if picks:
                best = list(_RNG.choice(picks))
                break
        return best, bool(best)

    def covers(others, facts):
        gone = set()
        for fact in facts:
            gone.update(fact["drops"])
        return set(others) <= gone

    def needed(others, facts):
        """どれも抜けないか（1本でも欠けたら覆えない）。"""
        for index in range(len(facts)):
            rest = set()
            for other, fact in enumerate(facts):
                if other != index:
                    rest.update(fact["drops"])
            if set(others) <= rest:
                return False
        return True

    def not_obvious(cast, axes):
        """一覧を眺めるだけで犯人が分からないか。

        **犯人が「どの軸でも多数派」の唯一の1人**だと、手がかりを1本も
        拾わずに当てられる。1軸ずつ違えて配ると必ずこうなるので、
        配り方を自由にした後もここで見張る。
        """
        names = [axis["id"] for axis in axes] + ["sex"]

        def modal_everywhere(index):
            for name in names:
                mine = cast[index]["traits"].get(name)
                same = sum(1 for m in cast if m["traits"].get(name) == mine)
                if same * 2 <= len(cast):
                    return False
            return True

        marked = [i for i in range(len(cast)) if modal_everywhere(i)]
        return not (marked == [0])

    def add_mistake(cast, axes, facts):
        """**思い違いの証言を1本混ぜる。**混ぜられなければ元のまま返す。

        ##### 何が変わるか

        真の事実だけだと、遊びは最後まで「当てはまらない者を消す」だけで
        終わる。属性の語彙を増やしても**操作は同じ**なので、そこが天井に
        なっていた（利用者の指摘）。

        1本を間違いにすると、**すべて信じたときに誰も残らない**。そこで
        初めて「どれが嘘か」を探すことになり、消し込みが推論に変わる。

        ##### 一意に決まらないなら混ぜない

        思い違いを外せば1人残る ― それだけでは足りない。**真の証言を
        外したときにも1人残ってしまうと、プレイヤーはどちらが嘘か決め
        られない。**だから「ちょうど1人残る外し方がただ1つ」を確かめて、
        満たさない置き方には混ぜない（`case.without_each`）。

        嘘は**特徴の言い間違い**にする（「黒髪だった」→本当は違う）。
        裏取りは「裏が取れた」という性質の証言なので、そこを嘘にすると
        何を信じてよいか分からなくなる。
        """
        culprit = cast[0]["traits"]
        words, texts = state.get("words") or {}, state.get("facts") or {}
        tries = []
        for axis in axes:
            name = axis["id"]
            for value in [v["id"] for v in axis["values"]]:
                if value == culprit.get(name) or (name, value) not in texts:
                    continue
                drops = [i for i in range(1, len(cast))
                         if cast[i]["traits"].get(name) != value]
                tries.append({"kind": "trait", "fact": texts[(name, value)],
                              "drops": drops + [0], "axis": name, "value": value,
                              "wrong": True})
        _RNG.shuffle(tries)
        for wrong in tries:
            if resolves_once(cast, facts + [wrong], wrong):
                return facts + [wrong]
        return facts

    def resolves_once(cast, facts, wrong):
        """思い違いが**ただ1つに決まる**か。

        - 全部信じると誰も残らない（食い違いの合図が立つ）
        - 嘘を外すと**ちょうど犯人1人**が残る
        - **真の証言を外したときは1人にならない**（0人か2人以上）

        3つ目が要点。ここを見ないと「どちらを外しても1人残る」事件が出て、
        当てずっぽうでしか決められなくなる。
        """
        ids = [str(index) for index in range(len(cast))]
        probe = case_mod.build("probe", "0", "0",
                               [{"id": i} for i in ids],
                               [dict(fact, id="p{}".format(index),
                                     eliminates=[str(d) for d in fact["drops"]])
                                for index, fact in enumerate(facts)], 0)
        for clue in probe["clues"]:
            clue["found"] = True
        if case_mod.remaining(probe):
            return False                # 食い違わない＝合図が立たない
        left = case_mod.without_each(probe)
        alone = [key for key, rest in left.items() if len(rest) == 1]
        if len(alone) != 1:
            return False                # 外し方が1つに決まらない
        key = alone[0]
        index = int(key[1:])
        return facts[index] is wrong and left[key] == ["0"]

    def all_telling(cast, axes):
        """引いた軸が**どれも何かを分けているか。**

        全員が同じ値の軸があると、一覧の全行に同じ語が並ぶ ― 読む側には
        意味の無い1語で、「特徴が被りすぎ」に見える（利用者の指摘）。本数のほうは `patterns.axes_wanted` で合わせてあるが、
        振り方の偶然で全員揃うことはあるので、ここでも見る。
        """
        for axis in axes:
            seen = {member["traits"].get(axis["id"]) for member in cast}
            if len(seen) < 2:
                return False
        # 性別も一覧に出る。**手がかりにはならない**（人物に付いてくるもので、
        # 引く軸ではない）が、全員同じなら同じ語が全行に並ぶのは変わらない。
        if len({member["traits"].get("sex") for member in cast}) < 2:
            return False
        return True

    def pick_clues(app, area, cast, culprit_index, facts, written=None):
        """事実を、その町にある施設に配る。

        町の構成は世界ごとに違う。闇市の無い町で闇市に手がかりを置くと、
        その事件は永久に解けない（利用者の報告）。

        **順番は付けない。**どこから調べてもよく、途中で分かれば残りは
        要らない。順番を強制すると道順をなぞる作業になる（同）。
        """
        available = game.facility_types_in(app, area)
        spots = [kind for kind in CLUE_POOL if kind in available]
        # **選んだ手がかりをそのまま使う。**ここで組み直すと、`build_cast` が
        # 「どれも抜けない」ように選んだ結果が捨てられ、候補が全部並ぶ ―
        # 重複した手がかりが戻ってくる（実際に戻した）。
        clues = []
        for index, (kind_name, fact) in enumerate(zip(spots, facts), start=1):
            # **書かせた文があればそれを使う。**検算に落ちた1本だけ定型に
            # 戻る形（`writer.read_facts` が落ちた要素を None で返す）。
            # 消す相手は `fact["drops"]` のままなので、文章が何であれ
            # 推理の効き方は変わらない。
            text = None
            if isinstance(written, list) and index - 1 < len(written):
                text = written[index - 1]
            if fact["kind"] == "alibi":
                # **名前は今の顔ぶれから引き直す。**`build_cast` は AI に
                # 書かせる**前**に走るので、そこで焼いた名前は下地のもの。
                # `dress` で入れ替わった後だと、居ない人物を指してしまう。
                text = alibi_fact().format(names="と".join(
                    cast[i]["name"] for i in sorted(fact["drops"])))
            clues.append({
                "id": "c{}".format(index),
                "label": "clue_c{}".format(index),
                "at_type": kind_name,
                "kind": fact["kind"],
                # **思い違いの印。**画面には決して出さない（ログと検算用）。
                "wrong": bool(fact.get("wrong")),
                "fact": text or fact["fact"],
                "eliminates": [cast[i]["npc_id"] for i in fact["drops"]],
                # **証言者を控える。**これがあれば「誰が何を知っているか」が
                # id の突き合わせだけで決まり、**どう話しかけられたかに
                # 依存しない**（ボタン経由でも、普通に話しかけても同じ）。
                "witness": game.owner_in(app, area, kind_name),
            })
        return clues, sorted(available)

    # ------------------------------------------------------------ 描写を書かせる
    def llm_module():
        return sys.modules.get(LLM_MODULE)

    def fact_order(facts):
        """事実を LLM に頼む形にする。**特徴の語と、触れてほしくない語。**

        「髪が黒い」を頼んだのに「黒髪の大柄な男」と返されると、渡していない
        特徴まで漏れて**その1文で犯人が決まってしまう**。避けたい語を先に
        渡し、返事も同じ規則で検算する（`writer.read_facts`）。
        """
        orders = []
        for fact in facts:
            axis, value = fact.get("axis"), fact.get("value")
            if not axis:
                # 裏取り（アリバイ）。**書かせない**（`writer.wanted_facts`）。
                # 名前を挙げてその人物を消す文なのに、名前は同じ応答で
                # 作らせている最中で、まだ無い。
                orders.append({"skip": True})
                continue
            avoid = set()
            for (other, _v), words in (state.get("hints") or {}).items():
                if other != axis:
                    avoid.update(words)
            orders.append({
                "word": (state.get("words") or {}).get(
                    (axis, value), "").rstrip("のな"),
                # **言い換えを認める。**実機で全部落ちた:
                #   「大柄な」を頼んで『大きな影が…』、
                #   「黒髪」を頼んで『黒い髪が揺れて…』
                # どちらも中身は正しいのに、語の完全一致で捨てていた。
                "accept": sorted((state.get("hints") or {}).get((axis, value), ())),
                "avoid": sorted(avoid)})
        return orders

    def place_notes(app, area):
        """頼み文に添える**その町の実データ**。

        架空の宿屋を書かれるより、いま歩いている町の宿屋の名前が出るほうが
        「その町の事件」になる。**取れなかったものは黙って省く** ―
        説明の無い世界もあるし、ここで落ちて事件が始まらないほうが困る。
        """
        try:
            return {
                "world_notes": game.world_notes(app),
                "area_notes": game.area_notes(app, area),
                "facilities": game.facility_notes(app, area),
                "residents": game.resident_names(app, area),
            }
        except Exception:
            ctx.log_exc("city case: cannot read the town for the prompt")
            return {}

    def write_material(app, members, facts):
        """事件の描写を LLM に書かせる。**失敗したら None を返すだけ。**

        渡すのは「見た目の指定」と「触れてほしくない語」だけで、**犯人も
        手がかりの効き先も1文字も渡していない**（`writer` の冒頭）。だから
        何が返ってきても事件の論理は壊れない。
        """
        module = llm_module()
        if not USE_LLM or not writer.available(module):
            if USE_LLM:
                write("[{}] {} cannot take a request yet; using the built-in "
                      "cast".format(stamp(), LLM_MODULE))
            return None
        orders = fact_order(facts)
        words = state.get("words") or {}
        people = []
        for member in members:
            traits = member["traits"]
            detail = [(axis.get("label") or axis["id"],
                       words.get((axis["id"], traits.get(axis["id"])), ""))
                      for axis in (state.get("axes") or [])]
            detail.append(("性別", SEX_WORDS.get(traits.get("sex"), "")))
            people.append({"words": tell_of(traits),
                           "detail": [(k, v) for k, v in detail if v]})
        try:
            structure, count = writer.build_structure(module, len(members))
        except Exception as exc:
            write("[{}] could not build the response structure: {}: {}".format(
                stamp(), type(exc).__name__, exc))
            return None
        # **町の名前を渡す。**以前はギルドの施設名を「町」として渡していて、
        # 実機の頼み文に `【町】鉄錆の徴収所` と出ていた。
        area = game.area_id(app)
        message = writer.build_prompt(
            game.world_name(app), game.area_name(app, area),
            people, orders, count, incident=incident(),
            place=place_notes(app, area))
        write("[{}] asking {} for the case material ({} people, {} facts)"
              .format(stamp(), writer.MANAGER_NAME, count, len(orders)))
        payload = writer.ask(ctx, message, structure,
                             timeout=LLM_TIMEOUT, write=write)
        if payload is None:
            return None
        written = writer.read_people(payload, count, write=write)
        if not written:
            write("[{}] the response did not carry a usable cast; using the "
                  "built-in one".format(stamp()))
            return None
        material = {"people": written,
                    "premise": writer.read_premise(payload),
                    "facts": writer.read_facts(payload, orders, write=write)}
        write("[{}] material accepted: {} / premise={} / facts {}/{}".format(
            stamp(), [p["name"] for p in written],
            "yes" if material["premise"] else "no",
            len([f for f in material["facts"] if f]), len(orders)))
        return material

    def dress(members, material):
        """書かせたものを顔ぶれに載せる。**特徴には触らない。**

        名前や来歴は差し替わるが、`traits`（性別・髪・体格）はコードが決めた
        ものがそのまま残る ― 一覧に出る `tell` も手がかりの効き先もそこから
        作るので、**文章が何であれ推理は成立する。**
        """
        if not material:
            return members
        out = []
        for member, person in zip(members, material["people"]):
            fresh = dict(member)
            fresh.update({"name": person["name"], "job": person["job"],
                          "profile": person["profile"],
                          "personality": person["personality"],
                          "look": person["look"], "category": member["category"]})
            out.append(fresh)
        return out

    def open_case(app, prepared=None):
        """事件を1件始める。

        `prepared` は `(顔ぶれ, 事実, 描写)`。**LLM に描写を頼むときは、
        頼んだのと同じ顔ぶれで始めなければならない** ― ここで組み直すと
        「書かせた人物」と「実際に立つ人物」が別物になる。
        """
        area = game.area_id(app)
        if prepared:
            members, facts, material = prepared
        else:
            members, facts = build_cast(app, area)
            material = None
        if not members:
            write("[{}] could not lay out a solvable cast".format(stamp()))
            screen.say(app, NO_CAST_TEXT)
            return
        members = dress(members, material)

        made = cast_for(app, area, [spec_of(m) for m in members])
        if not made:
            screen.say(app, NO_CAST_TEXT)
            return

        # `made` は生成順。**答えを先に固定する。**
        cast = [dict(members[index], npc_id=npc_id)
                for index, npc_id in enumerate(made)]
        culprit_index = 0
        # 言い分を配る。**犯人だけ裏が取れない場所**を挙げる。
        places = whereabouts()
        public = places.get("public") or []
        spots = _RNG.sample(public, min(len(cast), len(public)))
        for index, member in enumerate(cast):
            member["claim"] = (_RNG.choice(places.get("alone") or [""])
                               if index == culprit_index
                               else spots[index % len(spots)])
        clues, available = pick_clues(app, area, cast, culprit_index, facts,
                                      written=(material or {}).get("facts"))
        if not clues:
            write("[{}] no usable facility in area {}; types here: {}".format(
                stamp(), area, available))
            screen.say(app, NO_PLACE_TEXT)
            # 出番の無くなった人物は**世界から消す**（印を立てるだけだと残る）
            retire(app, [m["npc_id"] for m in cast], "the case never started")
            return

        # **置けた手がかりで必ず1人に絞れるようにする。**
        # 施設が足りない町では事実を全部は置けない。そのまま出すと、
        # 全部集めても複数残る＝当てずっぽうにしかならない事件になる。
        # 残ってしまう容疑者は**この事件から外す**（作った NPC は片付ける）。
        placed = set()
        for clue in clues:
            placed.update(str(npc_id) for npc_id in clue["eliminates"])
        culprit_id = cast[culprit_index]["npc_id"]
        keep, drop = [], []
        for member in cast:
            npc_id = member["npc_id"]
            if npc_id == culprit_id or npc_id in placed:
                keep.append(member)
            else:
                drop.append(member)
        if drop:
            write("[{}] {} suspect(s) cannot be ruled out with the clues that "
                  "fit this town; removing them: {}".format(
                      stamp(), len(drop), [m["name"] for m in drop]))
            retire(app, [m["npc_id"] for m in drop], "not part of the case")

        found = case_mod.build(
            game.world_name(app), area, culprit_id,
            [{"id": m["npc_id"], "tell": m.get("tell", ""),
              "claim": m.get("claim", "")} for m in keep],
            clues, REWARD_GOLD)
        state["case"] = found
        store(app)
        write("\n" + "=" * 72)
        write("[{}] case opened in area {} culprit={} ({})".format(
            stamp(), area, cast[culprit_index]["npc_id"],
            cast[culprit_index]["name"]))
        for member in cast:
            write("    suspect {} {!r} {} claim={!r}".format(
                member["npc_id"], member["name"], member.get("traits"),
                member.get("claim")))
        for clue in clues:
            write("    clue {} at {} [{}] {!r} -> drops {}".format(
                clue["id"], clue["at_type"], clue["kind"], clue["fact"],
                clue["eliminates"]))
        # **組んだ時点で解けることを確かめる。**全部集めても2人以上残るなら、
        # そのお題は詰んでいる（当てずっぽうにしかならない）。
        for clue in found["clues"]:
            clue["found"] = True
        if not case_mod.solvable(found):
            write("    WARN even with every clue, {} suspect(s) remain"
                  .format(len(case_mod.remaining(found))))
        for clue in found["clues"]:
            clue["found"] = False

        # **あらましは書かせたものがあればそれを出す。**無ければ定型。
        screen.say(app, (material or {}).get("premise")
                   or incident().get("premise") or OPENED_TEXT)
        say_roster(app, found)
        say_lead(app, found, first=True)

    def cast_for(app, area, specs):
        """事件のキャストを作る。**必ず生成する。**

        既存の NPC を犯人に仕立てるのは、機能としては動くが受け入れられない。
        プレイヤーが愛着を持っている相手が犯人にされうるからで、**その1点だけで
        この遊びは成立しなくなる**（利用者の指摘）。

        推理の面でも既存 NPC は使えない。手がかりは「犯人はこういう者だった」
        という**特徴の突き合わせ**で、そのためには容疑者の特徴が分かって
        いなければならない。世界に元から居る NPC にその情報は無い。

        **作れなければ事件を始めない。**既存 NPC に落とすと両方の問題が
        戻ってくる。始まらないほうがましだと判断している。
        """
        spots = [fid for fid, _kind in game.facilities_in(app, area)]
        if not spots:
            write("[{}] no facility to place the cast in area {}".format(
                stamp(), area))
            return []
        made = []
        for index, spec in enumerate(specs):
            # 全員を同じ場所に立たせない（その施設だけ不自然に人が増える）。
            spot = spots[index % len(spots)]
            npc_id = game.make_npc(app, spec, area, spot, write=write)
            if not npc_id:
                continue
            if npc_id in made:
                # **同じ id が返った＝前の人物を上書きしている。**
                # 実機で3人とも 47 になり、容疑者が全員同じ人物になった。
                # `generate_npc` の採番は連続で呼ぶと進まない
                # ことがある。気付かずに進めると「選択肢が全部同じ」になるので、
                # ここで止める。
                write("[{}] make_npc returned {} again; the previous cast "
                      "member was overwritten. stopping.".format(
                          stamp(), npc_id))
                break
            # **作った直後に台帳へ控える。**事件の控えに書く前に落ちても、
            # 次の起動で掃除できるようにするため。
            enroll(app, npc_id, spec.get("name"))
            made.append(npc_id)
        if len(made) < len(specs):
            write("[{}] only made {} of {} cast member(s); not starting a case"
                  .format(stamp(), len(made), len(specs)))
            # 作れた分は世界に残る。**消して片付ける** ―
            # 中途半端な登場人物を町に置きっぱなしにしない。
            retire(app, made, "the cast could not be completed")
            return []
        return made

    # ------------------------------------------------------------ 案内
    def place_of(app, found, facility_type):
        """施設の名前。引けなければ None（そのときは種類も出さない）。

        種類の名前（`inn`）をそのまま出しても、プレイヤーの画面には
        施設の名前しか出ていないので突き合わせられない。
        """
        return game.place_name(app, found.get("area"), facility_type)

    def say_roster(app, found):
        """容疑者と**その特徴**を並べる。

        これが推理の材料。集めた話と見比べて絞るのはプレイヤーの仕事なので、
        **こちらで除外の結果を出してはいけない**（出した瞬間に作業に戻る）。
        """
        screen.say(app, ROSTER_HEAD_TEXT)
        area = found.get("area")
        for suspect in found.get("suspects", []):
            # **居場所は今を見て引く。**控えではなく名簿から探すので、
            # ゲームが動かしていても正しい場所が出る（`world.facility_of`）。
            place = game.facility_of(app, area, suspect["id"])
            line = ROSTER_LINE_AT_TEXT if place else ROSTER_LINE_TEXT
            screen.say(app, line.format(
                name=game.name_of(app, suspect["id"]),
                tell=suspect.get("tell", ""), place=place))
        screen.say(app, ROSTER_HINT_TEXT)

    def say_contradiction(app, found):
        """**食い違いに気づかせる。**どれが嘘かは言わない。

        集めた話を全部信じると誰も当てはまらない ― ここで何も言わないと、
        プレイヤーは「詰んだ」「壊れている」と受け取る。**気づく手がかりは
        出すが、答えは出さない**（外して数えるのがこの遊びの山なので）。
        """
        if case_mod.contradicted(found):
            screen.say(app, CONTRADICTION_TEXT)

    def say_lead(app, found, first=False):
        """**話を聞ける場所を並べる。順番は決めない。**

        行き先が分からないと無策で歩き回ることになる（利用者の指摘）。ただし1つに絞って示すと道順をなぞる作業になるので、
        残っている場所を全部出して選ばせる。
        """
        places = []
        for clue in found.get("clues", []):
            if clue.get("found"):
                continue
            name = place_of(app, found, clue["at_type"])
            if name and name not in places:
                places.append(name)
        if places:
            screen.say(app, LEAD_TEXT.format(places="、".join(places)))
        elif first:
            screen.say(app, LEAD_FALLBACK_TEXT)
        if first:
            accuse_at = place_of(app, found, START_FACILITY_TYPE)
            if accuse_at:
                screen.say(app, GO_ACCUSE_TEXT.format(place=accuse_at))

    def say_status(app):
        """いま何が分かっているか。**忘れても取り戻せる。**

        分かった事実と容疑者の特徴を並べるだけ。**誰が消えたかは出さない。**
        """
        found = current(app)
        if not case_mod.is_active(found):
            screen.say(app, NO_CASE_TEXT)
            return
        screen.say(app, STATUS_HEAD_TEXT)
        facts = case_mod.found_clues(found)
        if facts:
            for clue in facts:
                screen.say(app, STATUS_FOUND_TEXT.format(text=clue["fact"]))
        else:
            screen.say(app, STATUS_NONE_TEXT)
        screen.say(app, HINT_TEXT.format(found=case_mod.found_count(found),
                                         total=case_mod.total_count(found)))
        say_contradiction(app, found)
        heard = case_mod.heard_claims(found)
        if heard:
            screen.say(app, STATUS_CLAIM_HEAD_TEXT)
            for suspect in heard:
                screen.say(app, STATUS_CLAIM_TEXT.format(
                    name=game.name_of(app, suspect["id"]),
                    claim=suspect.get("claim", "")))
        say_roster(app, found)
        say_lead(app, found)

    def finish_asking(app, partner=None):
        """会話が閉じた後に、分かったことを1行出す。

        会話の最中に出すと地の文に埋もれる。閉じてから出すことで、
        **何が分かったのかが必ず残る**。

        ##### 閉じた会話が、印を付けた会話と同じものか確かめる

        印（`state['asking']`）は LLM の呼び出しで立ち、ここで消える。
        だが会話が**閉じられずに終わる**ことがある（画面が変わる・
        中断する）。すると印が残ったままになり、**次に別の誰かとの会話を
        閉じた瞬間に、前の相手の手がかりが出る。**これも
        「会話内容とヒングが噛み合わない」の正体の一つ。

        防ぎ方は2つとも使う:

        - **相手の id を突き合わせる**（`partner`）。閉じた会話の相手が
          印の相手と違うなら、その印は捨てる
        - **古い印は捨てる**（`ASK_TTL`）。相手が分からない版でも効く
          最後の網
        """
        asking = state.get("asking")
        if not asking:
            return
        if partner and str(partner) != str(asking.get("npc")):
            # **別人との会話が閉じた。**印はそのまま残す ― 相手のところへ
            # 戻れば、その会話を閉じたときに出る。
            write("    [conv] conversation with {} closed, but the note is "
                  "for {}; leaving it".format(partner, asking.get("npc")))
            return
        age = time.monotonic() - asking.get("at", 0)
        if age > ASK_TTL:
            write("    [conv] dropping a stale note for {} ({:.0f}s old)"
                  .format(asking.get("npc"), age))
            state["asking"] = None
            return
        state["asking"] = None
        found = current(app)
        if asking.get("clue"):
            clue = case_mod.clue_by_id(found, asking["clue"])
            if clue is None or not case_mod.mark_found(found, clue["id"]):
                return
            store(app)
            write("[{}] clue {} found ({}/{}) remaining={}".format(
                stamp(), clue["id"], case_mod.found_count(found),
                case_mod.total_count(found), case_mod.remaining(found)))
            screen.say(app, GAINED_TEXT.format(text=clue["fact"]))
            screen.say(app, HINT_TEXT.format(
                found=case_mod.found_count(found),
                total=case_mod.total_count(found)))
            say_lead(app, found)
            return
        # 容疑者の言い分。**手がかりではない**ので数には入れないが、
        # 覚えていられないので控えて画面にも出す。
        suspect = case_mod.suspect_by_id(found, asking.get("npc"))
        if suspect is None or suspect.get("heard"):
            return
        suspect["heard"] = True
        store(app)
        write("[{}] heard {} ({}) claim={!r}".format(
            stamp(), suspect["id"], game.name_of(app, suspect["id"]),
            suspect.get("claim")))
        screen.say(app, CLAIM_HEARD_TEXT.format(
            name=game.name_of(app, suspect["id"]), claim=suspect["claim"]))

    def launch(app):
        """告発する相手を選ばせる。**自前のボタンだけで組む。**

        以前はゲームの自由施設（`FreeFacilityManager`）に場面を組ませていた。
        聞き込みを会話へ移した後は場面が**この1画面だけ**になり、中身も
        「容疑者を並べて1人選ばせる」だけ ― DSL の分岐も `flag_set` も
        使っていない。それでいて代償は大きかった:

        - `scripts.free_facility` が**まだ import されていない**か
          `lint_program` が無いと場面を組めず、**告発ボタンを押すと
          「調べていることは無い」と出て詰む**（事件は進行中なのに）。
          自由施設に一度も入っていないプレイで普通に踏む
        - ゲームの `free_*` を横取りする以上、**その施設を壊す危険**を
          1画面のために負い続けることになる

        判定は元から「押された選択肢の識別子を突き合わせるだけ」なので、
        場面をやめてもそこは何も変わらない（利用者の判断）。
        """
        found = current(app)
        if not case_mod.is_active(found):
            screen.say(app, NO_CASE_TEXT)
            return
        state["accusing"] = True
        screen.say(app, ACCUSE_TEXT)
        write("[{}] accusation opened at {} ({} suspect(s))".format(
            stamp(), game.facility_type(app), len(found.get("suspects", []))))
        repaint(app, "accuse")

    def cancel_accusation(app):
        """告発をやめて調べに戻る。**事件は続く。**

        **選び直せないのは決着だけ。**ここで降りても手がかりも容疑者も
        そのまま残るので、聞き込みの続きから再開できる。
        """
        state["accusing"] = False
        screen.say(app, FAREWELL)
        repaint(app, "accuse cancel")

    def abandon(app):
        """調べを打ち切る。**事件そのものを終わりにする。**

        「やめる」との違いは戻れるかどうか。こちらは事件が消えるので、
        ギルドで新しい噂を聞くところからやり直しになる。

        **真相は明かさない。**告発すれば当たっても外れても真相が分かる
        （外したときも `FAILED_TEXT` で犯人を告げる）ので、そこまで踏み込まずに
        降りたのなら、答えは持ち帰れないほうが筋が通る。

        キャストは**全員引き上げる。**誰も捕まっていないので `ARREST_CULPRIT`
        の出番は無いし、残すと作った NPC がそのまま町に溜まる。
        """
        found = current(app)
        state["accusing"] = False
        if not case_mod.is_active(found):
            repaint(app, "nothing to abandon")
            return
        write("[{}] the case was abandoned (knew {}/{})".format(
            stamp(), case_mod.found_count(found), case_mod.total_count(found)))
        screen.say(app, ABANDON_TEXT)
        gone = retire(app, case_mod.suspect_ids(found), "the case was abandoned")
        if gone:
            screen.say(app, DEPARTED_TEXT)
        drop(app, found, "the player gave up on it")
        repaint(app, "case abandoned")

    def accuse_action(npc_id):
        """容疑者1人ぶんの押下処理を作る。**判定は id の突き合わせだけ。**"""
        def run(app):
            state["accusing"] = False
            resolve(app, current(app), npc_id)
            repaint(app, "accuse done")
        return run

    def repaint(app, tag):
        """選択肢を組み直して**画面に塗る。**

        `refresh_choice_buttons` を呼ぶだけでは足りない。押下と同じ流れの中で
        差し替えると `app.buttons` は変わるのに**画面は古いまま**になり、
        見えているものと押せるものが食い違う（GAME.md §2.3）。
        `ui.Screen.apply_buttons` は次のフレーム・メインスレッドで
        「組み直す→塗る」までやる。

        `entries=None` は「いま `app.buttons` に入っているものを塗り直す」。
        中で `refresh_choice_buttons` が走り、そこで `offer` が
        `state['accusing']` を見て並べ直す。

        `Clock` の無い環境（`tools/` のオフライン検証）では予約が効かないので、
        その場で組み直す ― **押した結果が画面に出ないより出るほうがよい**。
        """
        if has_clock():
            screen.apply_buttons(app, None, tag)
        else:
            screen.refresh(app)

    # ------------------------------------------------------------ 判定
    def resolve(app, found, npc_id):
        """告発の決着。**照合は1行。**AI には何も聞かない。**一度きり。**"""
        right = case_mod.close(found, npc_id)
        store(app)
        name = game.name_of(app, npc_id)
        write("[{}] accused {} ({}) -> {} (knew {}/{}, remaining {})".format(
            stamp(), npc_id, name, "right" if right else "wrong",
            case_mod.found_count(found), case_mod.total_count(found),
            case_mod.remaining(found)))
        if not right:
            # **外したら終わり。**やり直せると総当たりで解けてしまい、
            # 絞り込む動機が消える（設計の選択）。
            screen.say(app, WRONG_TEXT.format(name=name))
            screen.say(app, FAILED_TEXT.format(
                name=game.name_of(app, found.get("culprit"))))
            close_cast(app, found)
            return
        screen.say(app, RIGHT_TEXT.format(name=name))
        if REWARD_GOLD:
            after = game.add_gold(app, REWARD_GOLD)
            write("    gold -> {}".format(after))
            if after is not None:
                screen.say(app, REWARD_TEXT.format(gold=money(REWARD_GOLD)))
        close_cast(app, found)

    def close_cast(app, found):
        """決着したら**キャストを世界から消す。**当てても外しても同じ。

        以前は犯人に `is_dead` を立て、無実の者は町に残していた。町に住人が
        増えていく味はあったが、**繰り返し遊ぶとセーブが太り続ける** ―
        1件4体、1体あたり 1.4〜8KB で、`npcs` はセーブの約2割（利用者の指摘）。害はバイト数より、町が見知らぬ人で埋まって土地の人物一覧や
        ゲームの組む文脈に効いてくることのほう。

        `ARREST_CULPRIT` を切っている場合は、**犯人だけ町に残す** ―
        「真相が分かるだけで犯人は町に残る」という設定の意味を保つため。
        無実の者は設定に関わらず引き上げる。
        """
        culprit = str(found.get("culprit"))
        leaving, staying = [], []
        for npc_id in case_mod.suspect_ids(found):
            if ARREST_CULPRIT or str(npc_id) != culprit:
                leaving.append(npc_id)
            else:
                staying.append(npc_id)
        # 残すと決めた者は**台帳に「残す」と書く。**台帳から外すだけでは、
        # 次の起動で「台帳より前に作られた者」として名前で拾われて消える。
        for npc_id in staying:
            ledger.keep(book(), game.world_name(app), npc_id)
        if staying:
            book_store()
            write("[{}] keeping {} in town (ARREST_CULPRIT is off)".format(
                stamp(), staying))
        gone = retire(app, leaving, "the case is closed")
        if gone:
            screen.say(app, DEPARTED_TEXT)

    # ------------------------------------------------------------ ボタン
    def is_facility_screen(buttons):
        if not isinstance(buttons, (list, tuple)):
            return False
        return any(ui.spec_cls_name(entry) == FACILITY_MARK for entry in buttons)

    def exclusive(app, buttons, wanted):
        """**この画面はこちらのものだけにする。**ゲームの選択肢は預かる。

        施設の選択肢に混ぜると、誰を告発するかを選ぶ場面なのに「出る」や
        「会話する」が並んで、いま何をしている画面なのか分からなくなる。

        **元の並びは預かっておいて、抜けるときに返す。**捨ててしまうと、
        `refresh_choice_buttons` は `app.buttons` を組み直さない（組むのは
        施設の側）ので、やめて戻ったときに選択肢が消えたままになる。
        """
        if state.get("stash") is None:
            # 自前のボタンは預からない（戻ったら組み直すので）。
            state["stash"] = [entry for entry in buttons
                              if not screen.mark_of(entry)]
        fresh = []
        for label, action in wanted:
            entry = screen.button(label, mark=action)
            if entry is None:
                write("cannot build the button (PhaseSpec unavailable)")
                continue
            fresh.append(entry)
        if not fresh:
            return False
        if [entry.get("text") for entry in buttons] == \
                [entry.get("text") for entry in fresh]:
            return False                    # もう出ている。触らない
        buttons[:] = fresh
        return True

    def restore_stash(buttons):
        """預かっていたゲームの選択肢を戻す。戻したら `True`。

        **いま出ているのが自分の画面のときだけ戻す。**告発の途中で施設を出る
        などしてゲームが別の画面を組んだ場合、そこへ預かり物を書き戻すと
        **関係の無い画面に古い選択肢が生える。**その場合は黙って捨てる ―
        ゲームが自分で組み直した後なので、戻す相手がもう居ない。
        """
        stash = state.get("stash")
        if stash is None:
            return False
        state["stash"] = None
        if not buttons or any(screen.mark_of(entry) is None for entry in buttons):
            write("city case: the screen changed while accusing; "
                  "dropping the buttons we were holding")
            return False
        buttons[:] = list(stash)
        return True

    def decline(app, why, buttons=None):
        """ボタンを出さなかった理由を残す。**同じ理由は繰り返さない。**

        「ギルドに立っているのに出ない」を外から詰めるのは難しい。塗り直す
        たびに書くとログが溢れるので、**理由が変わったときだけ**1行出す。
        """
        mark = "{}|{}|{}".format(why, game.facility_type(app), game.area_id(app))
        if state.get("declined") == mark:
            return False
        state["declined"] = mark
        write("[{}] no button: {} (facility={!r} area={!r} location={})".format(
            stamp(), why, game.facility_type(app), game.area_id(app),
            type(getattr(getattr(app, "player", None), "location", None)
                 ).__name__))
        # **画面の中身をそのまま添える。**「施設の画面と見なせない」ときは、
        # 何が並んでいるのかが分からないと次の手が打てない。
        for entry in (buttons or [])[:8]:
            write("      {!r:<28} spec={}".format(
                (entry.get("text") if isinstance(entry, dict) else entry),
                ui.spec_cls_name(entry)))
        return False

    def busy_reasons(app, buttons):
        """ボタンを出せない理由。**画面が施設の選択肢なら、旗より画面を信じる。**

        実機で `in_shopping` が立ちっぱなしだった（セーブをロードした後、
        ギルドに立って施設の選択肢が出ているのに下りない）。
        旗だけを見ていると、**その世界では二度とボタンが出ない。**

            no button: busy: ['in_shopping'] (facility='guild' area='7')
                  'クエスト掲示板'  spec=DisplayQuestChoice
                  '出る'          spec=MovePhaseManager
                  '会話する'       spec=DisplayTalkChoice

        `107_fix_battle_flag_stuck` が直した「戦闘中の印が下りない」と同じ形で、
        旗はセーブに焼かれて残る。

        旗は「別の画面が出ている」ことの**当て推量**にすぎず、こちらには
        `is_facility_screen` という直接の判定がある。**施設の選択肢が現に
        出ているなら、旗のほうが古い。**挿す先はその選択肢そのものなので、
        別の画面へ紛れ込む心配も無い。
        """
        flags = [flag for flag in BUSY_FLAGS if getattr(app, flag, False)]
        if flags and is_facility_screen(buttons):
            if state.get("ignored") != tuple(flags):
                state["ignored"] = tuple(flags)
                write("[{}] {} is set but the facility choices are on screen; "
                      "trusting the screen".format(stamp(), flags))
            return []
        return flags

    def offer(app):
        """条件が揃っていれば選択肢を足す。並びが変わったら `True`。

        **同じ画面でも並べるものが変わる。**告発の相手を選んでいる最中は
        容疑者が並び、それ以外は「気になる噂を聞く」などが並ぶ。だから
        「自分のボタンが既に在るなら何もしない」では足りない ―
        **在るものと出したいものを見比べて、違うときだけ差し替える。**
        （見比べずに毎回差し替えると、画面を塗り直すたびにボタンが作り直されて
        押下を取りこぼす。逆に見比べずに何もしないと、告発を押しても
        容疑者が出てこない。実際に後者を踏んだ）
        """
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list) or not buttons:
            return False
        # **自分が画面を預かっている間は、施設の目印が消えている。**
        # `exclusive` がゲームの選択肢を退けたので `FACILITY_MARK` も無い。
        # ここで弾くと、やめても元の並びに戻せなくなる（実際に踏んだ）。
        if state.get("stash") is None and not is_facility_screen(buttons):
            return decline(app, "not a facility screen", buttons)
        # **印を失った自前ボタンの残骸を先に落とす**（`ui.Screen.prune_stale`）。
        # `PhaseSpec.to_dict()` はセーブに `text` と `spec` しか書かないので、
        # ロードや再注入の後に「印の無い自分のボタン」が復元されている。
        # 落としておかないと重複判定をすり抜けて同じボタンが2つ並び、
        # しかも復元された方は押しても無反応になる（`301_` / `302_` が踏んだ）。
        screen.prune_stale(buttons, OUR_LABELS)
        stuck = busy_reasons(app, buttons)
        if stuck:
            return decline(app, "busy: {}".format(stuck), buttons)
        if state.get("writing"):
            # **期限を過ぎていないか、ここでも見る。**`Clock` の予約だけに
            # 頼ると、それが飛ばなかったときにロックが残る（実機で 15:28 に
            # 飛ぶはずの見張りが飛ばず、押せないまま止まった）。
            # この経路は画面を塗り直すたびに必ず通るので、取りこぼしが無い。
            if overdue():
                give_up(app)
                return False
            # 待機表示の最中。**選択肢に触らない** ― `busy_on` が全枠に
            # `...` を出しているので、ここで組み替えても見えないうえ、
            # 解けた瞬間に一瞬だけ古い並びが見える。
            return False

        found = current(app)
        kind = game.facility_type(app)
        # 告発の画面から出たら、預かっていたゲームの選択肢を先に戻す。
        # **施設を移ったときも告発は畳む** ― 別の場所で告発の途中が
        # 生き残っていると、戻ってきたときに何をしていたか分からない。
        if not (state.get("accusing") and kind == START_FACILITY_TYPE):
            state["accusing"] = False
            restore_stash(buttons)
        wanted = []
        if not case_mod.is_active(found):
            state["accusing"] = False
            if kind == START_FACILITY_TYPE:
                wanted.append((START_LABEL, "start"))
        elif found.get("area") != game.area_id(app):
            state["accusing"] = False       # 町を出たら告発の途中もやめる
            return False                    # 事件の町の外では出さない
        elif state.get("accusing") and kind == START_FACILITY_TYPE:
            # **告発は専用の画面にする。**施設の選択肢に混ぜて並べると、
            # 誰を告発するかを選ぶ場面なのに「出る」や「会話する」が並んでいて、
            # いま何をしている画面なのか分からない（利用者の指摘）。
            # ここだけはゲームの選択肢を退けて、こちらのものだけを出す。
            for suspect in found.get("suspects", []):
                wanted.append((
                    ACCUSE_ONE_LABEL.format(
                        name=game.name_of(app, suspect["id"]),
                        tell=suspect.get("tell", "")),
                    ACCUSE_PREFIX + str(suspect["id"])))
            wanted.append((LEAVE_LABEL, "cancel"))
            wanted.append((GIVE_UP_LABEL, "abandon"))
            return exclusive(app, buttons, wanted)
        else:
            if kind == START_FACILITY_TYPE:
                # **いつでも告発できる。**揃うまで待たせると、絞り込んだ
                # ことに意味が無くなる（設計の選択）。
                wanted.append((ACCUSE_LABEL, "accuse"))
                # 進み具合はいつでも見返せる（忘れても取り戻せるように）。
                wanted.append((STATUS_LABEL, "status"))
        # いま出ている自分のボタンと見比べる。**同じなら触らない。**
        mine = [entry for entry in buttons if screen.mark_of(entry)]
        if [entry.get("text") for entry in mine] == [text for text, _m in wanted]:
            return False
        if mine:
            # **同一性で外す。**中身が同じ辞書が他にも在りうるので、
            # 値の比較で消すと巻き添えが出る。
            ours = {id(entry) for entry in mine}
            buttons[:] = [entry for entry in buttons if id(entry) not in ours]
        if not wanted:
            return bool(mine)           # 引き上げただけでも並びは変わった

        at = len(buttons)
        for index, item in enumerate(buttons):
            if ui.spec_cls_name(item) == FACILITY_MARK:
                at = index
                break
        added = 0
        for label, action in wanted:
            entry = screen.button(label, mark=action)
            if entry is None:
                write("cannot build the button (PhaseSpec unavailable)")
                continue
            buttons.insert(at + added, entry)
            added += 1
        return added > 0

    def start(app):
        """依頼を受ける。**描写を書かせるあいだ画面を止めない。**

        LLM の推論は数秒〜数十秒かかる。押下と同じ流れで待つとゲームが固まる
        ので、別のスレッドで書かせて、返ってきてから**メインスレッドに戻って**
        事件を始める（UI に触るのは必ずメインスレッド。`ui.Screen.schedule`）。

        顔ぶれは**待ちに入る前に**組む。後で組み直すと、書かせた人物と実際に
        立つ人物が別物になる。
        """
        if case_mod.is_active(current(app)) or state.get("writing"):
            return
        screen.say(app, START_TEXT)
        area = game.area_id(app)
        members, facts = build_cast(app, area)
        if not members:
            write("[{}] could not lay out a solvable cast".format(stamp()))
            screen.say(app, NO_CAST_TEXT)
            return
        if not USE_LLM or not writer.available(llm_module()):
            # 書かせない、または呼べない版。定型で始める。
            open_case(app, (members, facts, None))
            return
        if not has_clock():
            # `Clock` が無いと別スレッドから戻る先が無い（オフライン検証）。
            # **その場で待って始める** ― 書かせる経路を素通りさせない。
            open_case(app, (members, facts,
                            write_material(app, members, facts)))
            return

        state["writing"] = True
        state["deadline"] = time.monotonic() + LLM_TIMEOUT + BUSY_GRACE
        screen.say(app, WRITING_TEXT)
        # **ゲーム自身と同じ待機表示にする**（`.` → `..` → `...`）。
        # 選択肢が押せるままだと、書いている最中に町を出たり別の依頼を
        # 受けたりできてしまう。`busy_on` は `is_button_enabled=False` と
        # 送信ボタンの無効化までやる（TECH.md の一覧）。
        screen.busy_on(app)
        # **必ず解ける保証を先に置く。**書き込みが返ってこなくても、
        # 待ち時間を過ぎたら表示を解いて操作を返す ― これが無いと、
        # スレッドが固まったときに**選択肢が永久に押せなくなる**。
        screen.schedule(lambda: give_up(app), LLM_TIMEOUT + BUSY_GRACE)

        def compose():
            material = None
            try:
                material = write_material(app, members, facts)
            except Exception:
                ctx.log_exc("city case: writing the material failed")
            # **注入し直されていたら、ここで降りる**（TECH.md §3.6.1）。
            # このスレッドは LLM を待つあいだ最長で `LLM_TIMEOUT + BUSY_GRACE`
            # 生き残るので、その間に注入し直されると**古い世代の続き**が
            # 新しい世代と同じ `state/city_case.json` に書き込む ― 新しい側は
            # 既に控えを読み終えているので、書いた内容が食い違ったまま残る。
            if ctx.superseded():
                write("[{}] a newer injection arrived; dropping this material"
                      .format(stamp()))
                return
            # **戻るのはメインスレッド。**ここで画面に触らない。
            screen.schedule(lambda: finish_start(app, members, facts, material))

        thread = threading.Thread(target=compose, name="city_case_writer",
                                  daemon=True)
        thread.start()

    def overdue():
        """待ちが期限を過ぎたか。"""
        deadline = state.get("deadline")
        return bool(deadline) and time.monotonic() > deadline

    def release(app, restore=True):
        """待機表示を解く。**二度呼んでも害が無い。**"""
        if not state.get("writing"):
            return False
        state["writing"] = False
        state["deadline"] = None
        screen.busy_off(app, restore=restore)
        return True

    def give_up(app):
        """待ち時間を過ぎても返ってこないとき、操作だけ返す。

        事件は始めない（書き込みが後から返れば `finish_start` が始める）。
        **押せない画面のまま放置しないこと**だけがここの役目。

        呼ばれる口が2つある ― `Clock` の予約と、画面を塗り直すたびの点検
        （`offer`）。**予約が飛ばないことがある**ので、後者が最後の砦。
        """
        if release(app):
            write("[{}] the material did not come back within {}s; unlocking "
                  "the screen".format(stamp(), LLM_TIMEOUT + BUSY_GRACE))
            screen.say(app, WRITING_SLOW_TEXT)

    def finish_start(app, members, facts, material):
        """書かせ終わってから事件を始める（メインスレッド）。"""
        # **塗り直させない。**この直後に事件の文と選択肢を出すので、
        # ここで元の選択肢を塗ると一瞬だけ古い画面が見える。
        release(app, restore=False)
        if case_mod.is_active(current(app)):
            # 待っている間に別の経路で事件が始まっていた。作らない。
            write("[{}] a case started while the material was being written; "
                  "dropping the material".format(stamp()))
            repaint(app, "case already open")
            return
        open_case(app, (members, facts, material))
        repaint(app, "case opened")

    ACTIONS = {"start": start, "accuse": launch, "status": say_status,
               "cancel": cancel_accusation, "abandon": abandon}

    def action_for(mark):
        """印から処理を引く。**告発だけは相手ごとに印が違う。**

        `accuse_<npc_id>` は容疑者の数だけあるので表には持てない。
        接頭辞で見分けて、その場で処理を作る。
        """
        if not mark:
            return None
        if mark in ACTIONS:
            return ACTIONS[mark]
        if mark.startswith(ACCUSE_PREFIX):
            return accuse_action(mark[len(ACCUSE_PREFIX):])
        return None

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        state["app"] = self
        # **置き去りの掃除は世界ごとに1回だけ。**ここを通るのは画面を
        # 塗り直すたびだが、`sweep` は世界名で番をしていて2度は走らない。
        try:
            sweep(self)
        except Exception:
            ctx.log_exc("city case: cannot sweep leftover cast")
        try:
            offer(self)
        except Exception:
            ctx.log_exc("city case: cannot offer the button")
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        entry = ui.pressed_entry(self, button_index)
        mark = screen.mark_of(entry)
        action = action_for(mark)
        if action is None:
            return orig(self, button_index, *args, **kwargs)
        try:
            action(self)
        except Exception:
            ctx.log_exc("city case: action failed ({})".format(mark))
        return None

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def conversation_starter(orig, messages, *args, **kwargs):
        """聞き込みの会話にだけ、相手が知っていることを差し込む。

        **渡す messages のコピーだけ**を書き換える（GAME.md §2.5）。
        ゲームが保持している会話履歴には触らない。
        """
        if not isinstance(messages, list) or not messages:
            return orig(messages, *args, **kwargs)
        app = state.get("app") or ui.find_app()
        # `character_instance` は署名の 4 番目（messages を除いて index 2）。
        character = args[2] if len(args) > 2 else None
        line, clue_id = (conversation_note(app, character)
                         if app is not None else (None, None))
        if line is None:
            return orig(messages, *args, **kwargs)
        # **第一声では事実そのものを言わせない。**
        #
        # 以前はここで事実を差し込み、印まで立てていた。すると**会話に入って
        # 即座に抜けるだけで手がかりが手に入る**（実機）。
        # とくに容疑者の言い分は、犯人だけ裏の取れない場所を言うので、
        # 4人を出入りするだけで**聞き込みを一切せずに犯人が割れた。**
        #
        # 印を立てるのは「プレイヤーが実際に何か尋ねた」ときだけにする
        # （`brief` ＝ `conversation_facilitator`）。第一声は
        # **「何か知っていそうだ」と匂わせるところまで**にとどめ、
        # 中身は尋ねられてから話す。
        #
        # こうすると**画面に出たことと、控えに入ることが必ず一致する** ―
        # 読んだのに数えられない、も、聞いていないのに数えられる、も起きない。
        try:
            last = messages[-1]
            if not isinstance(last, dict) or "content" not in last:
                return orig(messages, *args, **kwargs)
            copy = list(messages[:-1])
            replacement = dict(last)
            replacement["content"] = "{}\n{}".format(last["content"], TEASE_TEXT.format(crime=crime()))
            copy.append(replacement)
            messages = copy
        except Exception:
            ctx.log_exc("city case: cannot brief the witness")
        return orig(messages, *args, **kwargs)

    def npc_id_of(character):
        return (ui.element_id(frames.attr(character, "id"))
                if character is not None else "")

    #: 会話の相手が入っている属性の心当たり。
    #: `ConversationEndManager.__init__(self, app, in_conversation_id, ...)`
    #: なので1つ目が本命（`ui.conversation_partner` の記述より、実セーブで確認）。
    PARTNER_ATTRS = ("in_conversation_id", "conversation_id", "character_id")

    def partner_of(manager):
        """閉じた会話の相手の id。**分からなければ空文字。**

        `frames.attr` は属性が無いと `MISSING`（`'<missing>'` という**文字列**）
        を返す。そのまま id として扱うと「相手が居るが誰とも一致しない」に
        なり、**手がかりが永久に立たなくなる**（この検査を書いていて踏んだ）。
        分からないことは、分かった気にならずに空文字で返す。
        """
        for name in PARTNER_ATTRS:
            value = frames.attr(manager, name)
            if value is None or value == frames.MISSING:
                continue
            found = ui.element_id(value)
            if found and found != frames.MISSING:
                return found
        return ""

    def conversation_note(app, character):
        """会話に差し込む一文と、閉じたときに立てる手がかり。

        戻り値は `(差し込む文, 手がかりの id または None)`。

        **話しかけ方を問わない。**普通に「会話する」で話しかけても同じ。
        最初の版は MOD のボタン経由のときだけ差し込んでいて、普通に
        話しかけると何も起きなかった（実機）。

        相手は2種類。

        - **証言者**（施設の主）… 犯人についての事実を話す。会話を閉じると
          手がかりが立つ
        - **容疑者**… 自分の立場と、その晩どこに居たかを話す。**手がかりは
          立たない**（言い分は事実ではない）が、控えには残す
        """
        found = current(app)
        if not case_mod.is_active(found):
            return None, None
        npc_id = npc_id_of(character)
        if not npc_id:
            return None, None
        for clue in found.get("clues", []):
            if not clue.get("found") and str(clue.get("witness")) == str(npc_id):
                return KNOWS_TEXT.format(fact=clue["fact"]), clue["id"]
        suspect = case_mod.suspect_by_id(found, npc_id)
        if suspect is not None and suspect.get("claim"):
            return SUSPECT_TEXT.format(claim=suspect["claim"],
                                      crime=crime()), None
        return None, None

    def brief(args, kwargs, index, name, where="", character_at=3):
        """`retrieved_knowledge` に、この人物が知っていることを足す。

        **ゲーム自身が持っている差し込み口**（リコンの署名より）。会話の
        受け答えを組むときにここが読まれるので、開始のひとことだけでなく
        **プレイヤーが直接尋ねたときにも効く。**

        位置引数の番号は版で動きうるので、キーワードを先に見る。
        """
        app = state.get("app") or ui.find_app()
        character = args[character_at] if len(args) > character_at else None
        line, clue_id = (conversation_note(app, character)
                         if app is not None else (None, None))
        if line is None:
            if app is not None and case_mod.is_active(current(app)):
                # **黙って素通ししない。**誰と話していて、誰を待っているのか
                # が出れば、照合が外れている理由がその場で分かる。
                found = current(app)
                write("    [conv] {} with {} (no clue; waiting for {})".format(
                    where or "?",
                    ui.element_id(frames.attr(character, "id"))
                    if character is not None else "<none>",
                    [(c.get("id"), c.get("witness"))
                     for c in found.get("clues", []) if not c.get("found")]))
            return args, kwargs
        # **話し始めた時点で覚えておく。**会話が閉じたときに画面へ出す
        # （判定は会話の中身ではなく「聞きに行った」行動）。
        state["asking"] = {"clue": clue_id, "npc": str(npc_id_of(character)),
                           "at": time.monotonic()}
        if name in kwargs:
            kwargs = dict(kwargs)
            kwargs[name] = _with_line(kwargs[name], line)
            write("    briefed {} via keyword {}".format(where, name))
            return args, kwargs
        if len(args) > index:
            args = list(args)
            args[index] = _with_line(args[index], line)
            write("    briefed {} via arg[{}]".format(where, index))
            return tuple(args), kwargs
        # **届かなかった。**引数の並びが版で動いたか、この関数は
        # 知識の枠を持っていない。どちらなのかを次の1回で分かるようにする。
        write("    WARN could not brief {}: {} args, kwargs={}".format(
            where, len(args), sorted(kwargs)))
        return args, kwargs

    def _with_line(value, line):
        """知識の入れ物に1行足す。**文字列でも配列でも受ける。**"""
        if isinstance(value, (list, tuple)):
            return list(value) + [line]
        if isinstance(value, str):
            return (value + "\n" + line) if value.strip() else line
        return line

    #: `retrieved_knowledge` の位置（リコンの署名。0 起点）。
    KNOWLEDGE_AT = {
        "conversation_facilitator": 11,
        "conversation_facilitator_after_retrieval": 10,
    }

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator",
              required=False)
    def conversation_facilitator(orig, *args, **kwargs):
        try:
            args, kwargs = brief(args, kwargs,
                                 KNOWLEDGE_AT["conversation_facilitator"],
                                 "retrieved_knowledge", "facilitator")
        except Exception:
            ctx.log_exc("city case: cannot brief the witness (facilitator)")
        return orig(*args, **kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator_after_retrieval",
              required=False)
    def conversation_facilitator_after_retrieval(orig, *args, **kwargs):
        try:
            args, kwargs = brief(
                args, kwargs,
                KNOWLEDGE_AT["conversation_facilitator_after_retrieval"],
                "retrieved_knowledge", "after_retrieval")
        except Exception:
            ctx.log_exc("city case: cannot brief the witness (retrieval)")
        return orig(*args, **kwargs)

    # ------------------------------------------------------------------
    # 聞き込み中に**どの経路が実際に走るのか**を計測する
    #
    # 自由入力で尋ねても知識が届かなかった（実機）。フックは
    # 全部解決しているので、「呼ばれていない」か「引数の並びが違う」の
    # どちらか。**推測で当てにいかず、1回で確定させる。**
    #
    # 候補は会話まわりの LLM 入口すべて（リコンの署名より）。聞き込みの
    # 最中だけ、呼ばれた名前と引数の形を残す。
    # ------------------------------------------------------------------
    def watch(name):
        @ctx.wrap("scripts.llm.llm_manager:" + name, required=False)
        def watcher(orig, *args, **kwargs):
            try:
                app = state.get("app") or ui.find_app()
                if app is not None and case_mod.is_active(current(app)):
                    write("    [conv] {}({} args{})".format(
                        name, len(args),
                        ", kwargs=" + str(sorted(kwargs)) if kwargs else ""))
                    for index, value in enumerate(args):
                        if isinstance(value, str) and len(value) > 12:
                            write("        arg[{}] str: {}".format(
                                index, frames.repr_value(value)))
            except Exception:
                ctx.log_exc("city case: conversation watch failed")
            return orig(*args, **kwargs)
        return watcher

    if WATCH_CONVERSATION:
        for _name in CONVERSATION_ENTRIES:
            watch(_name)

    @ctx.wrap("__main__:ConversationEndManager.execute", required=False)
    def conversation_end(orig, self, *args, **kwargs):
        """会話が閉じたら、分かったことを出す。

        判定は会話の中身ではなく「聞きに行った」という行動で済んでいるので、
        ここでは**残っている announcement を流すだけ**。
        """
        result = orig(self, *args, **kwargs)
        try:
            if state.get("asking"):
                app = getattr(self, "app", None) or ui.find_app()
                partner = partner_of(self)
                # 会話の後始末（テキストの流し込み・要約）と噛み合わないよう、
                # 手が空いてから出す。**要約の流し込み中に `add_text` すると
                # こちらの出力が押し流される**（`300_` の実測。`ui.when_idle`）。
                #
                # `Clock` の無い環境（`tools/` のオフライン検証）では予約が
                # 効かないので、その場で出す ― **出さないより出すほうがよい**
                # （何が分かったのかが消えてしまう）。`finish_asking` は
                # 二度呼んでも何もしないので、取りこぼしも二重出力も起きない。
                if has_clock():
                    screen.when_idle(app, lambda: finish_asking(app, partner))
                else:
                    finish_asking(app, partner)
        except Exception:
            ctx.log_exc("city case: cannot close the interview")
        return result

    ctx.log("city case: start at {} reward={} arrest={} suspects={} log={}"
            .format(START_FACILITY_TYPE, REWARD_GOLD, ARREST_CULPRIT,
                    SUSPECT_COUNT, log_path))


# --------------------------------------------------------------------------
#: 事件のために作る登場人物。**先頭が犯人**、以降は容疑者（無実）。
#:
#: 既存の NPC を使わないのは、プレイヤーが愛着を持っている相手を犯人に
#: 仕立ててしまうから（利用者の指摘）。事件のために生まれた
#: 人物なら、退場させても誰の思い入れも壊さない。
#:
#: 形は実機で観測した生成直後の NPC に合わせてある（`world.NEW_NPC_TEMPLATE`）。
#: HP・スキル・装備・立ち絵は空でよく、ゲームが会話の直前に埋める。
#:
#: **名前に `"` などファイル名に使えない文字を入れないこと**（`110_` の対象。
#: 入れると立ち絵だけが無言で生成されない）。
#: 事件のために作る登場人物の**下地**。
#:
#: **特徴（片腕か・大柄か）は焼き付けない。**事件ごとに割り当てるので、
#: 名前も profile も特徴に触れない書き方にしてある。触れてしまうと
#: （「隻腕の運び屋」のように）毎回同じ顔ぶれになるうえ、割り当てた特徴と
#: 食い違う（利用者の指摘）。
#:
#: 性別だけは名前と結び付くので下地が持つ。残りの軸は `assign_traits` が振る。
#:
#: **名前に `"` などファイル名に使えない文字を入れないこと**（`110_` の対象）。
#: **同梱の材料（最後の受け皿）。**
#:
#: 事件の材料は `patterns/*.json` から読む（そちらを足せば事件の幅が増える）。
#: ここに置いてあるのは、**そのファイルが無い・壊れている・全部弾かれた**
#: ときに使う最小限の一組。読めなかった束だけがこれで埋まる。
#:
#: だから**ここを増やす必要は無い。**増やすのは `patterns/` のほう。
BUILT_IN = {
    "incidents": [
        {
            "id": "theft",
            "noun": "盗み",
            "premise": "「この町で盗みが続いている。誰がやっているのか、調べてほしい」",
            "brief": "夜ごと家々や店から物が持ち去られている",
            "targets": []
        }
    ],
    "axes": [
        {
            "id": "build",
            "label": "build",
            "values": [
                {
                    "id": "large",
                    "word": "大柄な",
                    "fact": "犯人は大柄だった",
                    "hints": [
                        "大柄",
                        "大きな",
                        "大きい",
                        "がっしり",
                        "大男",
                        "上背",
                        "体格のいい",
                        "屈強"
                    ],
                    "look": "large heavy build"
                },
                {
                    "id": "small",
                    "word": "小柄な",
                    "fact": "犯人は小柄だった",
                    "hints": [
                        "小柄",
                        "小さな",
                        "小さい",
                        "華奢",
                        "痩せ",
                        "細身"
                    ],
                    "look": "small slender build"
                }
            ]
        },
        {
            "id": "hair",
            "label": "hair",
            "values": [
                {
                    "id": "dark",
                    "word": "黒髪の",
                    "fact": "犯人の髪は黒かった",
                    "hints": [
                        "黒髪",
                        "黒い髪",
                        "黒っぽい髪",
                        "暗い色の髪"
                    ],
                    "look": "black hair"
                },
                {
                    "id": "light",
                    "word": "色の淡い髪の",
                    "fact": "犯人の髪は色が淡かった",
                    "hints": [
                        "色の淡い髪",
                        "淡い髪",
                        "明るい髪",
                        "明るい色の髪",
                        "金髪",
                        "白っぽい髪",
                        "薄い色の髪"
                    ],
                    "look": "pale blond hair"
                }
            ]
        }
    ],
    "whereabouts": {
        "public": [
            "その晩は酒場の隅で飲んでいた",
            "その晩は宿の帳場で油を売っていた",
            "その晩は広場の焚き火のそばに居た",
            "その晩は市の立つ通りを歩いていた",
            "その晩は詰所の前で雨宿りをしていた"
        ],
        "alone": [
            "その晩は一人で家に居た",
            "その晩は町外れを歩いていた",
            "その晩は納屋で眠っていた"
        ],
        "alibi_fact": "{names}は、あの晩ずっと人前に居たことが裏付けられた"
    },
    "cast": [
        {
            "name": "運び屋のガレス",
            "sex": "man",
            "category": "middle-aged man",
            "job": "adventure",
            "profile": "荷運びで日銭を稼ぐ男。人の出入りを誰よりも見ている。",
            "personality": "口は軽いが、肝心なことは言わない。金の話には目の色を変える。",
            "look": "worn cloak, carrying strap, dim alley background"
        },
        {
            "name": "代書屋のイーサン",
            "sex": "man",
            "category": "young man",
            "job": "adventure",
            "profile": "代書きで日銭を稼ぐ若者。誰の用件も引き受けるので町中の事情に詳しい。",
            "personality": "愛想はいいが、他人の秘密を売ることに罪悪感が無い。",
            "look": "ink-stained fingers, threadbare coat, town street background"
        },
        {
            "name": "流れ者のミレイユ",
            "sex": "woman",
            "category": "young woman",
            "job": "adventure",
            "profile": "少し前に流れ着いた女。何をして暮らしているのか誰も知らない。",
            "personality": "問われたことにしか答えない。目を合わせようとしない。",
            "look": "hooded dark cloak, single travel bag, quiet expression"
        },
        {
            "name": "酒場の常連ドルフ",
            "sex": "man",
            "category": "middle-aged man",
            "job": "adventure",
            "profile": "毎晩どこかの酒場に居る男。仕事をしているところを見た者がいない。",
            "personality": "話し好きで、聞かれてもいないことまで喋る。半分は出鱈目。",
            "look": "ruddy face, disheveled jacket, holding a cup, tavern background"
        },
        {
            "name": "石工のハルカ",
            "sex": "woman",
            "category": "young woman",
            "job": "adventure",
            "profile": "石を刻んで暮らす女。夜遅くまで槌を振るっていることが多い。",
            "personality": "無駄口を叩かない。訊かれたことには正確に答える。",
            "look": "stone dust on apron, tied-back hair, workshop background"
        },
        {
            "name": "薬売りのコルム",
            "sex": "man",
            "category": "middle-aged man",
            "job": "adventure",
            "profile": "町から町へ薬を売り歩く男。この町には数日前に着いたばかり。",
            "personality": "如才ないが、値段の話になると急に細かくなる。",
            "look": "leather satchel, many small bottles, market background"
        },
        {
            "name": "門番あがりのノラ",
            "sex": "woman",
            "category": "middle-aged woman",
            "job": "adventure",
            "profile": "かつて門番を務めていた女。いまは日雇いで食いつないでいる。",
            "personality": "疑り深い。人を値踏みするような目で見る。",
            "look": "old leather guard armor, weathered face, gate background"
        },
        {
            "name": "荷馬車の御者ジン",
            "sex": "man",
            "category": "young man",
            "job": "adventure",
            "profile": "荷馬車を駆って近隣を回る若者。町の外の話をよく知っている。",
            "personality": "せっかちで、話の途中で結論を急ぐ。",
            "look": "travel-worn coat, reins in hand, stable background"
        }
    ]
}

#: 性別の語。**引く軸ではない**（人物に付いてくる）。
SEX_WORDS = {"man": "男", "woman": "女"}
