# -*- coding: utf-8 -*-
"""機能追加: 仲間にした NPC と、会話の中で別れられるようにする。

いまのゲームで仲間が外れる道は **死別** と **クエストクリアによる解散** の
2つしか無い。会話中に「ここで別れる」を出し、確認を挟んでから
**ゲーム自身の解散処理**を通してパーティから外す。

## 会話相手が仲間かどうかは、画面のボタンから読む

会話画面には必ず「会話を終了する」が並んでいて、その `PhaseSpec` は

    ConversationEndManager(app, in_conversation_id, finisher, end_text)
    args = ['77', 'user', '<行動: 会話を終了する>']      （実セーブの値）

つまり **`args[0]` がいま話している相手の id**。相手を自分で当てにいく必要は無く、
`ConversationStartManager.__init__` を追跡しなくてもよい。仲間の label から会話に
入る経路（`process_party_member_choice`）でも、会話画面になった時点でこのボタンは
並ぶので同じように効く。

パーティの名簿はセーブの `game_variables['party']`（`['player', '63', ...]` の
**id の配列**）。ただし**在り処も形も決めつけない**。`app.party` が存在しない
時期があり、決めつけると判定が黙って外れてボタンが出なくなる（GAME.md §2.8）。
候補を集めて中身（`'player'` を含むか）で本物を選び、`list` でも `dict` でも読む
（`party_stores` / `pick_store`）。書くときは見つかった入れ物すべてに書く。

## 外す処理は自分で書かない

`InstantaleApp` は解散に要るものを自分で持っている:

    remove_party_member(member_id)                          パーティから外す
    get_party_leave_facility(character_instance)            外れた後どこに置くか
    move_npc_to_facility(character_id, character_instance,
                         target_facility, ...)              そこへ置く

死別・クエスト解散が通っているのもこの経路のはずなので、そのまま呼ぶ。

## どこへ置くか

1. **雇用された場所（`initial_location`）へ戻す**
2. ただし**土地を跨いで別れた場合は、いまの町のギルド**へ置く

初期位置は `{"area": "7", "node": null, "facility": "127"}`（実セーブで確認）。
`node` が null のことがあるので、施設はエリアのノードを辿って探す。ギルドは
`facility_type == 'guild'` で見分ける。引けなければ順に下がる。
初期位置 → いまの町のギルド → ゲーム自身の `get_party_leave_facility`。

`get_party_leave_facility` は **`(施設, ノード)` のタプル**を返す（GAME.md §2.8）。
`move_npc_to_facility` は施設とノードを別々に取るので、ほどいてそれぞれの位置に
入れる。中身が何なのかは**解釈しない**。ほどいて渡すだけにする（引数の意味を
推測するとゲームが落ちる。GAME.md §2.2）。

`remove_party_member` が内部で置き直しまでやっているのかは、ソースが読めない以上
外からは分からない。そこで **こちらの呼び出しの間だけ `move_npc_to_facility` を
見張り**、ゲームが自分で動かしたならこちらは何もしない。動かさなかったときだけ
置きに行く。どちらでも二重にならない。

## 別れられない場面では出さない

- 戦闘中（`in_battle` / `in_colosseum_battle` / `in_boss_battle`）
- クエスト中（`current_quest_data` が入っている）
- `original_party` が**現在の名簿と食い違っている**＝一時的な差し替えの最中
  （入っているだけでは平常。平常時も名簿と同じ内容で入っている）
- `get_party_leave_facility` が置き場所を返さない土地（ダンジョン等）

最後の1つは押した後にしか分からないので、そのときは断り文句を出して何もしない。
置き場所を決められないまま外すと、その NPC が世界のどこにも居ない状態になる。

## 会話は必ずゲームの経路で閉じてから外す

閉じずに進めると立ち絵が残って付いてくる（GAME.md §2.5）。
閉じ方は画面にある「会話を終了する」ボタンの `args` をそのまま使い、
**`end_text` だけ差し替える**。`end_text` は `'<行動: 会話を終了する>'` という
自由記述なので、ここに別れた旨を書いておけば会話の要約・ライフログに
「別れた」ことがそのまま残る。引数の意味を推測しなくて済むうえ、記録も正しくなる。

## 自前のクラス名を PhaseSpec に書かない

`301_` と同じ理由（セーブに焼かれて mod 無しの次回起動で落ちる）。自前ボタンには
無害な `JustSetButtonToNormalPhase` を持たせ、押下はボタン辞書の印で横取りする。
印のキーは `301_` と別にすること（`mod_action` を共有すると、向こうの
`on_button_press` が知らない action を握り潰してしまう）。
"""

import datetime
import sys
import time

from instantale_modloader import frames, ui
from instantale_modloader.frames import repr_value

LOG_BASENAME = "party_leave.log"

# ---------------------------------------------------------------- 文言
LEAVE_LABEL = "ここで別れる"
CONFIRM_QUESTION = "{name}とはここで別れることになる。よいか？"
CONFIRM_LABEL = "ああ、ここで別れよう"
CANCEL_LABEL = "やめておく"
FAREWELL_TEXT = "{name}はパーティを離れ、{place}に残った。"
FAREWELL_TEXT_NO_PLACE = "{name}はパーティを離れた。"
NO_PLACE_TEXT = "……こんな場所で放り出すわけにはいかない。人の居る場所まで戻ろう。"
FAILED_TEXT = "（今は別れ話を切り出せない）"

# 会話を閉じるときにゲームへ渡す end_text。会話の要約とライフログに残る。
END_TEXT_TEMPLATE = "<行動: {name}と別れ、パーティから外れてもらった>"

# ---------------------------------------------------------------- 動作
# 別れた後にセーブするか。パーティの増減は game_variables なので、
# 次のセーブまで落ちると「別れたのに居る」状態で復帰してしまう。
SAVE_AFTER_LEAVE = True

# 別れた仲間をどこへ置くか。
#   True  雇用された場所（`initial_location`）へ戻す。ただし**土地を跨いで
#         別れた場合**は、いまの町のギルドへ置く
#   False ゲーム自身の `get_party_leave_facility` に任せる（元の挙動）
RETURN_TO_INITIAL_LOCATION = True

# 「町のギルド」を見分ける facility_type。実セーブで確認した値
# （他に inn / general_store / blacksmith / guild などがある）。
GUILD_FACILITY_TYPE = "guild"

# 外した後に display_position_in_battle を空に戻すか。
# 実セーブでは非パーティ NPC は全員 null。remove_party_member が戻して
# くれるならそのままにする（下の処理は残っているときだけ触る）。
CLEAR_BATTLE_POSITION = True

# 会話を閉じ終わるのを待つ間隔と上限（`301_` と同じ。終了処理は要約で LLM を
# 回すことがあるので長め）。
END_POLL = 0.3
END_TIMEOUT = 120.0

# 画面の塗り替えを待つ余韻（秒）。
SETTLE = 0.4

# 会話画面の顔ぶれ（相手・ボタンの種類・名簿の在り処）を記録するか。
# ボタンが出ないときの切り分けはこれが頼りなので、既定で入れておく。
# 署名が変わったときだけ書くので、量は増えない。
TRACE_SCREENS = True

# 選択肢の描画が誰の変化で起きているかを見るための上限付きトレース。
# **既定では切ってある。** これで「監視対象は HUD 側のプロパティであって
# `app.to_display_buttons` ではない」と分かり、目的を果たしたため。加えて
# 読み込み中の文字送り（'.' '..' '...'）で1秒に数本出るので普段は邪魔になる。
# 描画の経路をもう一度確かめたくなったら 40 くらいを入れる。
PAINT_TRACE_LIMIT = 0

# 注入時に「`method_1` を持つマネージャ」と、その定義行の対応表を書き出すか。
# **既定オフ。** `method_1 (instantale.py:6602)` は `QuestEndManager` と確定して
# おり（GAME.md §2.8）、以後は `owner_of` が記録の時点でクラス名を出すので要らない。
# 行番号から持ち主を引き直したくなったら True に戻す。
DUMP_PHASE_MAP = False

# ゲーム本来の解散の「直後」とみなす秒数。NPC は普段から動くので、
# 置き直しの記録はこの窓の中だけに絞る。
RECENT_REMOVE_WINDOW = 5.0

# ボタン辞書に付ける印。**`301_` の "mod_action" とは別のキーにすること。**
MARK = "mod_party_action"

# 自前ボタンに持たせる無害な spec は `ui.SAFE_CLS`
# （`JustSetButtonToNormalPhase`）。mod 無しで押されても選択肢が戻るだけ。


def _text(value, limit=200):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    state = {
        "saved_buttons": None,   # 確認画面を出す前のボタン。やめる で戻す
        "end_button": None,      # そのときの「会話を終了する」ボタン（辞書）
        "pending": None,         # 確認待ちの相手 id
        "leaving": False,        # 二重実行よけ
        "watching": None,        # remove_party_member 実行中の見張り
        "last_skip": None,       # ボタンを出さなかった理由（ログの重複よけ）
        "last_screen": None,     # 直近に記録した会話画面の署名（同上）
        "censused": False,       # app の持ち物を書き出したか（1回きり）
        "paint_traces": 0,       # 描画トレースの本数（上限で打ち切る）
        "recent_remove": None,   # ゲーム本来の解散の直後を見張る印 (id, 時刻)
    }

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("party leave: write failed")

    # ------------------------------------------------------------ 基本の道具
    # 選択肢・spec の読み取り・画面の塗り替え・会話の閉じ方は
    # `instantale_modloader.ui` に集約してある（`300_` / `301_` と共有）。
    # ここで確かめた「描画は HUD 側を直接呼ぶ」「差し替えは次のフレーム」も
    # そこに入っているので、他の mod からも同じものが使える。
    screen = ui.Screen(ctx, write, tag="party leave", mark=MARK)

    spec_cls_name = ui.spec_cls_name
    pressed_entry = ui.pressed_entry
    say = screen.say

    def button(text, mark=None, member_id=None):
        # `member_id` は押されたときに誰の話か分かるようボタン自身に持たせる。
        # ゲームは text と spec しか見ないので足しても害は無い。
        extra = {"mod_party_member": member_id} if member_id is not None else None
        return screen.button(text, mark=mark, extra=extra)

    def apply_buttons(app, entries, tag):
        screen.apply_buttons(app, entries, tag)

    def schedule(fn, delay=SETTLE):
        screen.schedule(fn, delay)

    # ------------------------------------------------------------ パーティ名簿
    # 名簿の**読み方**は共通部品（`ui.party_stores` / `pick_store` / …）に置いて
    # ある。手順（在り処も形も決めつけない・中身を見て本物を選ぶ・書くときは
    # 見つかった入れ物すべて）は `306_` でも同じものが要るので、`ui` に集約して
    # ある（TECH.md §5・§6.1）。
    party_stores = ui.party_stores
    element_id = ui.element_id
    store_ids = ui.store_ids
    drop_from_store = ui.drop_from_store
    pick_store = ui.pick_store
    party_ids = ui.party_ids
    describe_stores = ui.describe_stores

    # ------------------------------------------------ 名簿が見つからないとき
    def dump_census(app):
        """app の持ち物を一度だけ全部書き出す。

        名簿の在り処を2回続けて外した（`app.party` は空だと思ったら**存在せず**、
        候補も1つも見つからなかった）。**心当たりを足すのはもうやめて**、実物の
        属性名を全部見る。これがあれば、次はログを読むだけで在り処が決まる。

        1回きり（`state["censused"]`）。`vars()` が使えないビルドもありうるので
        `dir()` に落ちる道も用意する。
        """
        if state["censused"]:
            return
        state["censused"] = True
        try:
            names = sorted(vars(app).keys())
            source = "vars()"
        except Exception:
            names = sorted(n for n in dir(app) if not n.startswith("__"))
            source = "dir()"
        write("-" * 78)
        write("census: app is {} with {} attribute(s) via {}".format(
            type(app).__name__, len(names), source))
        write("census: names = {}".format(names))
        for name in names:
            low = name.lower()
            if not any(key in low for key in
                       ("party", "member", "companion", "follower", "npc")):
                continue
            write("census: app.{} = {}".format(name, describe_value(app, name)))
        census_of(app, "world", getattr(app, "world", None))
        census_of(app, "player", getattr(app, "player", None))
        world_dict = getattr(app, "world_dict", None)
        if isinstance(world_dict, dict):
            write("census: world_dict keys = {}".format(sorted(world_dict.keys())))
            variables = world_dict.get("game_variables")
            if isinstance(variables, dict):
                write("census: world_dict['game_variables'] keys = {}".format(
                    sorted(variables.keys())))
        else:
            write("census: app.world_dict is {}".format(type(world_dict).__name__))
        write("-" * 78)

    def census_of(app, label, owner):
        if owner is None:
            write("census: {} is None".format(label))
            return
        try:
            names = sorted(vars(owner).keys())
        except Exception:
            names = sorted(n for n in dir(owner) if not n.startswith("__"))
        hits = [n for n in names
                if any(key in n.lower() for key in ("party", "member", "companion"))]
        write("census: {} is {} ({} attrs); party-ish = {}".format(
            label, type(owner).__name__, len(names), hits))
        for name in hits:
            write("census: {}.{} = {}".format(label, name, describe_value(owner, name)))

    def describe_value(owner, name):
        """型・要素数・中身の要約。値そのものは短く切る。"""
        try:
            value = getattr(owner, name)
        except Exception:
            return "(unreadable)"
        kind = type(value).__name__
        try:
            if isinstance(value, dict):
                return "dict len={} keys={}".format(
                    len(value), [str(k) for k in list(value)[:12]])
            if isinstance(value, (list, tuple, set)):
                return "{} len={} -> {}".format(
                    kind, len(value), [element_id(v) for v in list(value)[:12]])
        except Exception:
            pass
        return "{} {}".format(kind, repr_value(value))

    character_of = ui.character_of

    def name_of(app, character_id):
        character = character_of(app, character_id)
        name = _text(getattr(character, "name", ""), 40)
        return name or "その仲間"

    # ------------------------------------------------------- 出してよい場面か
    def blocking_reason(app, member_id):
        """仲間と別れられない理由。None なら出してよい。

        相手が仲間かどうかは `is_member` が別に見る。ここは**場面**だけを見る。
        """
        for flag in ("in_battle", "in_colosseum_battle", "in_boss_battle"):
            if getattr(app, flag, False):
                return flag
        # クエスト中は名簿を触らない。クエスト同行者の扱いが絡むうえ、
        # `current_quest_data` は**意味の確かめが取れている**信号
        # （クエスト外では None、実セーブで確認）。
        if getattr(app, "current_quest_data", None):
            return "in a quest"
        # **`original_party` は判定に使わない**（GAME.md §2.8）。平常時も名簿と
        # 同じ内容で入っており、雇用直後は控えが更新されていないだけで名簿と
        # 食い違う ― どちらの読み方でも「差し替え中」は判定できない。
        #
        # 一時的な差し替えが本当にあるとしてもクエスト中の話で、そこは
        # `current_quest_data` で既に断っている。**意味を確かめていない
        # フィールドで断らない** ― 値は下の `screen:` 行に載せて観測だけ続ける。
        return None

    def is_member(app, member_id):
        return bool(member_id) and member_id != "player" and member_id in party_ids(app)

    # 会話相手は「会話を終了する」ボタンの `args[0]` から読む（`ui` に移した。
    # `ConversationStartManager.__init__` を追跡しなくても、ボタンを読むだけで
    # 相手が分かるという発見はこの mod のもの）。
    conversation_partner = ui.conversation_partner

    def has_leave_button(buttons):
        return any(isinstance(b, dict) and b.get(MARK) for b in buttons)

    # ============================================================ 自前のフェーズ
    class LeavePhase(object):
        """`app.process_choice` に渡してゲームと同じ経路を通すためのフェーズ。

        `app.buttons` を書いて `refresh_choice_buttons()` を直接呼ぶだけでは
        画面が塗り替わらない（`301_` の実測）。**`PhaseSpec` には決して載せない**
        （載せるとセーブに焼かれ、mod 無しの次回起動で `getattr` が失敗する）。
        """

        def __init__(self, app, action, member_id):
            self.app = app
            self.action = action
            self.member_id = member_id

        def execute(self, choice_text):
            return dispatch(self.app, self.action, self.member_id, choice_text)

    def start_phase(app, action, member_id, choice_text):
        screen.start_phase(app, LeavePhase(app, action, member_id), choice_text,
                           fallback=lambda: dispatch(app, action, member_id,
                                                     choice_text))

    def dispatch(app, action, member_id, choice_text):
        if action == "confirm":
            ask_confirmation(app, member_id)
        elif action == "leave":
            begin_leave(app, member_id)
        elif action == "cancel":
            restore_buttons(app)

    def restore_buttons(app):
        saved = state["saved_buttons"]
        state["saved_buttons"] = None
        state["pending"] = None
        if saved is None:
            return
        apply_buttons(app, saved, "restore")

    # ============================================================ 確認を挟む
    def ask_confirmation(app, member_id):
        """「本当に別れるか」を訊く。押し間違いで仲間を失わせないため。"""
        reason = blocking_reason(app, member_id)
        if reason or not is_member(app, member_id):
            write("confirm: refused ({})".format(reason or "not a party member"))
            say(app, FAILED_TEXT)
            return
        confirm = button(CONFIRM_LABEL, mark="leave", member_id=member_id)
        cancel = button(CANCEL_LABEL, mark="cancel")
        if confirm is None or cancel is None:
            write("confirm: could not build the buttons")
            say(app, FAILED_TEXT)
            return
        state["pending"] = member_id
        state["saved_buttons"] = list(getattr(app, "buttons", []) or [])
        apply_buttons(app, [confirm, cancel], "confirm")
        say(app, CONFIRM_QUESTION.format(name=name_of(app, member_id)))
        write("confirm: asking about {!r} ({})".format(name_of(app, member_id), member_id))

    # ============================================================ 別れる
    def begin_leave(app, member_id):
        """確認が済んだ後。会話を閉じてから外す。"""
        if state["leaving"]:
            return
        reason = blocking_reason(app, member_id)
        if reason or not is_member(app, member_id):
            write("leave: refused ({})".format(reason or "not a party member"))
            say(app, FAILED_TEXT)
            restore_buttons(app)
            return

        character = character_of(app, member_id)
        if character is None:
            write("leave: no character instance for {!r}".format(member_id))
            say(app, FAILED_TEXT)
            restore_buttons(app)
            return

        # 置き場所は**外す前に**決める。決まらない土地では別れさせない
        # （置き場所の無いまま外すと、その NPC が世界のどこにも居なくなる）。
        facility, node, why = choose_destination(app, character)
        if facility is None:
            write("leave: get_party_leave_facility returned nothing; refusing")
            say(app, NO_PLACE_TEXT)
            restore_buttons(app)
            return

        state["leaving"] = True
        name = name_of(app, member_id)
        write("=" * 78)
        write("leave: {!r} ({}) -> {!r} [{}] node={!r}".format(
            name, member_id, facility_name(app, facility) or facility, why, node))
        write("leave: party before = {} ({})".format(party_ids(app), describe_stores(app)))

        # 確認画面に切り替えた時点で「会話を終了する」ボタンは画面から消えている。
        # だから押された時に控えておいたものを使う。
        end_entry = state["end_button"]
        state["saved_buttons"] = None
        end_conversation_then(
            app, end_entry, name,
            lambda a: finish_leave(a, member_id, character, facility, node, name))

    # ------------------------------------------------ どこへ置くか（配置ルール）
    def choose_destination(app, character):
        """別れた仲間をどこへ置くか。`(施設, ノード, 理由)` を返す。

        1. **初期位置（雇用された場所）に戻す**
        2. ただし**土地を跨いで別れた場合は、いまの町のギルド**に置く

        初期位置は Character の `initial_location`
        （`{"area": "7", "node": null, "facility": "127"}`。実セーブで確認）。
        `node` は null のことがあるので、施設はエリアのノードを辿って探す。

        引けなかったときは黙って諦めず、順に下がる:
        初期位置 → いまの町のギルド → ゲーム自身の `get_party_leave_facility`。
        最後まで駄目なら「ここでは別れられない」になる（置き場所を決めずに外すと
        その NPC が世界のどこにも居なくなる）。
        """
        here = current_area(app)
        here_id = area_id_of(here)
        home_area_id, home_facility_id = initial_location_of(character)
        write("destination: here={!r} home_area={!r} home_facility={!r}".format(
            here_id, home_area_id, home_facility_id))

        if RETURN_TO_INITIAL_LOCATION and home_area_id and home_area_id == here_id:
            facility, node = find_facility(here, home_facility_id)
            if facility is not None:
                return facility, node, "initial location"
            write("destination: facility {!r} is not in area {!r} any more".format(
                home_facility_id, here_id))

        if here is not None:
            facility, node = find_guild(here)
            if facility is not None:
                if home_area_id and home_area_id != here_id:
                    reason = "guild of the current area (left home behind)"
                else:
                    reason = "guild of the current area (initial location unavailable)"
                return facility, node, reason
            write("destination: no {!r} facility in area {!r}".format(
                GUILD_FACILITY_TYPE, here_id))

        facility, node = game_destination(app, character)
        if facility is not None:
            return facility, node, "the game's own get_party_leave_facility"
        return None, None, "nowhere"

    # 現在地とエリア表の引き当ては `ui` に移した（`player.current_area` は
    # エリアのオブジェクトとは限らず id の文字列のこともある、エリア表は
    # 属性名ではなく中身で見分ける ― どちらもここで形を決めつけて外した結果）。
    current_area = ui.current_area
    area_id_of = ui.area_id_of

    # 施設の引き当ても `ui` に移した（施設はエリア直下ではなく**ノードの下**、
    # `initial_location` の `node` は null のことがある、ギルドは
    # `facility_type == 'guild'` ― どれも実セーブで確かめた「ゲームの形」なので、
    # `303_` にも同じものが要った時点で共通部品に上げた）。
    find_facility = ui.find_facility

    def find_guild(area):
        return ui.find_guild(area, GUILD_FACILITY_TYPE)

    def initial_location_of(character):
        """雇用された場所を `(エリア id, 施設 id)` で返す。

        セーブでは `initial_location = {"area": "7", "node": null,
        "facility": "127"}`。辞書でない持ち方をしていても読めるようにしておく。
        """
        value = getattr(character, "initial_location", None)
        if isinstance(value, dict):
            return str(value.get("area") or ""), str(value.get("facility") or "")
        if value is not None:
            return (str(getattr(value, "area", "") or ""),
                    str(getattr(value, "facility", "") or ""))
        return "", ""

    def game_destination(app, character):
        """外れた後の置き場所を `(施設, ノード)` で返す。

        `get_party_leave_facility` は **`(Facility, Node)` のタプル**を返す
        （GAME.md §2.8）。`move_npc_to_facility(character_id,
        character_instance, target_facility, target_node=None, ...)` は施設と
        ノードを別々に取るので、タプルのまま渡すと
        `'tuple' object has no attribute 'characters'` で落ちる。

        中身が何なのかは**解釈しない**。ほどいて、ゲームが持つ引数の位置に
        そのまま入れるだけにする。1個しか返らないビルドでも壊れないように、
        長さで場合分けする。
        """
        try:
            value = app.get_party_leave_facility(character)
        except Exception:
            ctx.log_exc("party leave: get_party_leave_facility failed")
            return None, None
        if isinstance(value, (tuple, list)):
            if len(value) >= 2:
                return value[0], value[1]
            if len(value) == 1:
                return value[0], None
            return None, None
        return value, None

    def end_conversation_then(app, end_entry, name, follow_up):
        """会話をゲーム自身の経路で閉じてから `follow_up` を走らせる。

        閉じ方そのものは `ui.Screen.end_conversation` に移した（`301_` と共有）。
        画面のボタンの args をそのまま写し、**`end_text` だけ**別れの記述に
        差し替える。そこは自由記述なので、書いておけば会話の要約とライフログに
        「別れた」ことがそのまま残る。閉じ終わってから**手が空くのを待つ**のも
        共通部品側（要約の流し込み中に `add_text` すると押し流される）。
        """
        def abort(_reason):
            say(app, FAILED_TEXT)
            # 打ち切られたら「実行中」の印を必ず戻す。でないと以後ずっと
            # 別れられなくなる。
            state["leaving"] = False

        screen.end_conversation(app, end_entry, follow_up,
                                end_text=END_TEXT_TEMPLATE.format(name=name),
                                on_abort=abort, poll=END_POLL, timeout=END_TIMEOUT)

    def finish_leave(app, member_id, character, facility, node, name):
        """会話が閉じた後。ゲーム自身の解散処理を通してパーティから外す。"""
        try:
            watch = {"moved": False}
            state["watching"] = {"member_id": member_id, "watch": watch}
            try:
                app.remove_party_member(member_id)
            finally:
                state["watching"] = None

            remaining = [pid for pid in party_ids(app) if pid == member_id]
            if remaining:
                # remove_party_member が名簿を触らなかった＝こちらの前提が
                # 崩れている。放置すると「別れたのに居る」まま保存されるので、
                # 見つかった入れ物すべてから落として記録に残す。
                write("WARN remove_party_member left {!r} in the party; removing by hand"
                      .format(member_id))
                for _label, store in party_stores(app):
                    drop_from_store(store, member_id)
            write("leave: party after = {} ({})".format(party_ids(app),
                                                        describe_stores(app)))

            if watch["moved"]:
                write("leave: the game moved {!r} itself; not placing again".format(name))
            else:
                place_character(app, member_id, character, facility, node)

            if CLEAR_BATTLE_POSITION:
                clear_battle_position(app, member_id, character)

            place_name = describe_facility(app, character, facility)
            say(app, (FAREWELL_TEXT.format(name=name, place=place_name) if place_name
                      else FAREWELL_TEXT_NO_PLACE.format(name=name)))

            if SAVE_AFTER_LEAVE:
                try:
                    app.save_game()
                    write("leave: saved")
                except Exception:
                    ctx.log_exc("party leave: save_game failed")
            write("leave: done ({!r})".format(name))
        except Exception:
            ctx.log_exc("party leave: finishing the farewell failed")
        finally:
            state["leaving"] = False
            state["pending"] = None
            state["end_button"] = None

    def place_character(app, member_id, character, facility, node):
        """外れた NPC を、ゲームが指した施設へ置く。

        値は `get_party_leave_facility` が返したものをほどいて渡すだけ。中身が
        何なのかは**解釈しない**（`301_` で引数を推測して落とした反省）。
        """
        try:
            app.move_npc_to_facility(member_id, character, facility, node)
            write("leave: moved {!r} to facility={!r} node={!r}".format(
                member_id, facility, node))
        except Exception:
            ctx.log_exc("party leave: move_npc_to_facility({!r}, ..., {!r}, {!r}) "
                        "failed".format(member_id, facility, node))

    def clear_battle_position(app, member_id, character):
        """戦闘中の立ち位置が残っていたら空に戻す。

        実セーブでは非パーティ NPC の `display_position_in_battle` は全て null。
        `remove_party_member` が戻してくれているなら何もしない。
        """
        try:
            if getattr(character, "display_position_in_battle", None) is None:
                return
            setattr(character, "display_position_in_battle", None)
            write("leave: cleared display_position_in_battle for {!r}".format(member_id))
        except Exception:
            ctx.log_exc("party leave: cannot clear display_position_in_battle")

    def describe_facility(app, character, facility):
        """別れた場所の名前。取れなければ空（場所抜きの文言に切り替える）。"""
        for candidate in (facility, getattr(character, "current_location", None)):
            name = facility_name(app, candidate)
            if name:
                return name
        return ""

    # 施設名の引き当ても `ui`（id の文字列で渡ってくることがあるので、
    # その場合は世界の施設表から引き直す）。
    facility_name = ui.facility_name

    def trace_screen(app, buttons, member_id):
        """会話画面の顔ぶれを、変わったときだけ1行残す。

        ボタンが出ないとき、原因は2つしかない。「相手が仲間だと分からない」か
        「そもそも `ConversationEndManager` のボタンが並んでいない」か。
        この行があれば、どちらなのかがログだけで切り分けられる。
        `refresh_choice_buttons` は頻繁に呼ばれるので、**署名が変わったときだけ**
        書く。
        """
        if not TRACE_SCREENS or not getattr(app, "in_conversation", False):
            return
        signature = (member_id, tuple(spec_cls_name(entry) for entry in buttons))
        if signature == state["last_screen"]:
            return
        state["last_screen"] = signature
        label, ids = pick_store(app)
        if label is None:
            # 名簿が1つも見つからない。心当たりを増やすのではなく、実物を見る。
            dump_census(app)
        # 画面に出ている文字列も一緒に残す。`buttons` と食い違っていたら
        # 「差し替えたのに塗り替わっていない」が一目で分かる。
        shown = list(getattr(app, "to_display_buttons", []) or [])
        # `original_party` は判定に使わない（2度読み違えた）。値だけ残して
        # 観測を続ける ― 本当に差し替えが起きる場面が来たら、ここに現れる。
        original = getattr(app, "original_party", None)
        write("screen: partner={!r} member={} buttons={} shown={} | party={} from {} "
              "| original_party={} | {}"
              .format(member_id, member_id in ids if member_id else False,
                      list(signature[1]), shown, ids, label,
                      store_ids(original) if isinstance(original, (list, dict)) else original,
                      describe_stores(app)))

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """会話相手が仲間なら「ここで別れる」を「会話を終了する」の手前に足す。

        判定は文字列ではなく spec のクラス名と `args[0]`（相手の id）。表記や
        言語設定に依存せず、仲間以外との会話には出ない。
        """
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list):
                member_id, end_entry = conversation_partner(buttons)
                # 確認画面（自前ボタンが並んでいる状態）も記録する。表示と
                # 中身が食い違ったときに、どちらが古いのかを後から見るため。
                trace_screen(self, buttons, member_id)
            if isinstance(buttons, list) and not has_leave_button(buttons):
                if member_id and is_member(self, member_id):
                    reason = blocking_reason(self, member_id)
                    if reason is None:
                        entry = button(LEAVE_LABEL, mark="confirm",
                                       member_id=member_id)
                        if entry is not None:
                            # 「会話を終了する」の手前に置く（終了は最後に残す）。
                            # 位置探しは同一性で行う ― 辞書の == 比較に頼ると
                            # 同じ文字列の別ボタンを掴みうる。
                            at = len(buttons)
                            for index, existing in enumerate(buttons):
                                if existing is end_entry:
                                    at = index
                                    break
                            buttons.insert(at, entry)
                            write("added {!r} to the conversation with {!r} ({} buttons)"
                                  .format(LEAVE_LABEL, name_of(self, member_id),
                                          len(buttons)))
                    elif state["last_skip"] != (member_id, reason):
                        # 同じ理由を毎フレーム書かない。場面が変わったときだけ残す。
                        state["last_skip"] = (member_id, reason)
                        write("not offering the farewell to {!r}: {}".format(
                            name_of(self, member_id), reason))
        except Exception:
            ctx.log_exc("party leave: cannot add the farewell button")
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通しする。

        印のキーは `301_` と別（`MARK`）。共有すると、向こうが知らない action を
        握り潰してしまう。
        """
        entry = pressed_entry(self, button_index)
        action = entry.get(MARK) if isinstance(entry, dict) else None
        if action is None:
            return orig(self, button_index, *args, **kwargs)
        member_id = entry.get("mod_party_member") or state["pending"]
        text = entry.get("text") or LEAVE_LABEL
        write("pressed {!r} ({}) member={!r}".format(text, action, member_id))
        if action == "confirm":
            # 確認画面に移ると「会話を終了する」が画面から消えるので、
            # いま控えておく。閉じるときの引数はここからしか取れない。
            _partner, end_entry = conversation_partner(getattr(self, "buttons", None))
            state["end_button"] = end_entry
        # 直接やらずにゲームの経路（process_choice）に乗せる。でないと画面が
        # 塗り替わらない（LeavePhase の説明）。
        start_phase(self, action, member_id, text)
        return None

    # ------------------------------------------------- 見張り（読み取りのみ）
    @ctx.wrap("__main__:InstantaleApp.move_npc_to_facility", required=False)
    def move_npc_to_facility(orig, self, character_id, *args, **kwargs):
        """こちらの解散処理の最中にゲームが自分で置き直したかを見る。

        置いたのなら二重に動かさない。見張っていないときは何もしない。
        """
        watching = state["watching"]
        if watching is not None and str(character_id) == watching["member_id"]:
            watching["watch"]["moved"] = True
            write("observed: the game moved {!r} during remove_party_member".format(
                character_id))
        else:
            # ゲーム本来の解散の直後なら、その置き直しも記録する。
            # （NPC は普段から動くので、直後の数秒だけに絞る）
            recent = state["recent_remove"]
            if (recent is not None and recent[0] == str(character_id)
                    and time.monotonic() - recent[1] <= RECENT_REMOVE_WINDOW):
                state["recent_remove"] = None
                write("observed: the game placed {!r} at {!r} after its own removal"
                      .format(character_id, facility_name(self, args[1])
                              if len(args) > 1 else "?"))
        return orig(self, character_id, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.remove_party_member", required=False)
    def remove_party_member(orig, self, member_id, *args, **kwargs):
        """誰が・どこから外されたかを記録する。**値は一切変えない。**

        ゲーム本来の解散（死別・クエストクリア）がどの関数から呼ばれているかは
        ソースが読めない以上ここでしか分からない。呼び出し元の連鎖と、名簿が
        どう変わったかを残しておけば、次にそれが起きたときに経路が確定する。
        """
        if state["watching"] is not None:
            return orig(self, member_id, *args, **kwargs)   # こちらの解散
        before = party_ids(self)
        write("=" * 78)
        write("remove_party_member({!r} {!r}) from {}".format(
            member_id, name_of(self, str(member_id)), caller()))
        # 直後にゲームが置き直すかどうかを見るための印（短命）。
        state["recent_remove"] = (str(member_id), time.monotonic())
        result = orig(self, member_id, *args, **kwargs)
        write("remove_party_member: party {} -> {}".format(before, party_ids(self)))
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_button_texts", required=False)
    def update_button_texts(orig, self, instance, value, *args, **kwargs):
        """選択肢の描画が**誰の変化で**起きているかを見る。読むだけ。

        `to_display_buttons` を入れ替えても画面が変わらなかったので、そもそも
        この関数が呼ばれているのかを確かめる。ゲーム自身がメニューを変えたとき
        に呼ばれ、こちらが変えたときに呼ばれないなら、監視の対象がこちらの
        触っている属性ではない、ということになる。

        出過ぎないよう上限を設ける（文字送りで1文字ずつ呼ばれる可能性がある）。
        """
        if state["paint_traces"] < PAINT_TRACE_LIMIT:
            state["paint_traces"] += 1
            write("hud.update_button_texts({}) <- {}".format(
                repr_value(value), type(instance).__name__))
        return orig(self, instance, value, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_party_member", required=False)
    def add_party_member(orig, self, character_id, *args, **kwargs):
        result = orig(self, character_id, *args, **kwargs)
        try:
            label, ids = pick_store(self)
            if label is None:
                # 仲間を入れた直後なのに名簿が見つからない ＝ 在り処の見当が
                # 外れている。ここが一番確実な観測点なので、実物を書き出す。
                dump_census(self)
            # 名簿がどこに載ったかを毎回残す。**ここが空なら判定は必ず外れる**
            # （実際にそれで一度ボタンが出なかった）ので、候補も全部並べる。
            write("add_party_member({!r} {!r}) -> party={} from {} | {}".format(
                character_id, name_of(self, str(character_id)), ids, label,
                describe_stores(self)))
        except Exception:
            ctx.log_exc("party leave: cannot log add_party_member")
        return result

    @ctx.wrap("__main__:InstantaleApp.process_party_member_choice", required=False)
    def process_party_member_choice(orig, self, character_id, *args, **kwargs):
        """仲間の label から入る経路。どこへ繋がるかを1行残すだけ。"""
        write("process_party_member_choice({!r} {!r}) | {}".format(
            character_id, name_of(self, str(character_id)), describe_stores(self)))
        return orig(self, character_id, *args, **kwargs)

    # 呼び出し元の記録は `frames.caller` / `frames.owner_of` に移した
    # （`203_` やクラッシュ記録からも同じものが使える）。要点は2つ:
    #
    #   * **段数で数えない。** `sys._getframe(2)` は `@ctx.wrap` の層を指すので、
    #     ファイル名でこちら側のフレームを飛ばす
    #   * **関数名だけでは足りない。** `method_1` / `execute` は多くのマネージャが
    #     持つので、同じコードオブジェクトを持つクラスを探して名指しする
    caller = frames.caller       # 中で frames.owner_of を使ってクラス名まで出す

    def dump_phase_map():
        """`method_1` を持つクラスと、その定義行の対応表。

        フレームから取れるのは関数名と行番号だけで、`method_1` / `execute` は
        多くのマネージャが持っている。**行番号から持ち主を引く**ための表。
        以後の記録は `owner_of` がその場でクラス名まで出すので、これは
        「既に捕まえた行番号」を後追いで解決するためだけのもの。
        """
        module = ui.main_module()
        if module is None:
            return
        try:
            entries = sorted(vars(module).items())
        except Exception:
            return
        rows = []
        for cls_name, cls in entries:
            if not isinstance(cls, type):
                continue
            try:
                members = vars(cls)
            except Exception:
                continue
            if "method_1" not in members:
                continue
            parts = []
            for attr in ("__init__", "method_1", "execute"):
                member = members.get(attr)
                func = getattr(member, "__func__", member)
                code = getattr(func, "__code__", None)
                if code is not None:
                    parts.append("{}@{}".format(attr, code.co_firstlineno))
            rows.append("{} {}".format(cls_name, " ".join(parts) or "(no line info)"))
        write("phase map: {} class(es) with method_1".format(len(rows)))
        for row in rows:
            write("  " + row)

    if DUMP_PHASE_MAP:
        try:
            dump_phase_map()
        except Exception:
            ctx.log_exc("party leave: cannot dump the phase map")

    ctx.log("leave party in conversation: save_after_leave={} log={}".format(
        SAVE_AFTER_LEAVE, log_path))
