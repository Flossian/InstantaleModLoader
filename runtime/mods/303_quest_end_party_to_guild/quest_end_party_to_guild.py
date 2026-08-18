# -*- coding: utf-8 -*-
"""機能追加: クエストクリアの解散で、仲間をいま居る町のギルドに残す。

ゲーム本来のクエストクリアは、
同行した仲間を**雇った町（`initial_location`）**へ帰してしまう。
冒険から戻ってきたその足で相手が世界の裏側へ消えるので、
また組みたくても会いに行けない。
この
mod は解散のときだけ置き先を差し替え、**プレイヤーがいま立っている町のギルド**に残す。

## どこで解散しているか（GAME.md §2.8）

解散は `QuestEndManager.method_1` の中で `remove_party_member` を呼ぶ形で行われ、
それは **`execute` から別スレッドで**走る。
ここから3つ読める:

* `remove_party_member` 自身は NPC を動かさない。
  置き直すのは呼び出し元で、removal の後に `move_npc_to_facility` を呼んでいる
* 解散は `QuestEndManager.method_1`。
  クエスト放棄側は `QuestRetireManager` が同じ役どころ（未観測なので既定では触らない。
  `ALSO_ON_QUEST_RETIRE`）
* 帰還は解散より先に済んでいる（`add_text('パーティは帰還した...')` → 報酬 →
  `add_text('…はパーティから離脱した。')`）。
  つまり解散の時点で `player.current_area` はもう町になっている。
  だから「いま居る町」をその場で引いてよい

## 差し替え方: 解散処理は書かない。置き先だけ変える

`remove_party_member` の結果には一切触らない。
パーティから外すのはゲームの仕事で、こちらは「外れた後どこへ置くか」だけを変える。
層は3つで、上から順に効けば下は何もしない:

1. `get_party_leave_facility`: 解散処理の中で呼ばれたら、いま居る町のギルドを返す。
   **戻り値の形はゲームのものに合わせる**（`(施設, ノード)` のタプル。
   長さ1のビルドもありうるので、元の形を見てから同じ形で返す）
2. `move_npc_to_facility`: 解散した相手を動かす呼び出しだけ、
   引数の置き先をギルドに差し替える。
   1 が効いていれば置き先は既にギルドなので何もしない
3. 時間切れの置き直し。
   `PLACE_TIMEOUT` 秒待っても誰も動かさなかったら、こちらで置く。
   **観測できているビルドではここまで来ない**（保険）

`move_npc_to_facility` は NPC の日常の移動でも呼ばれる。
**スタックを見に行くのは `remove_party_member` と
`get_party_leave_facility` だけ**にして、
こちらは「いま解散した相手か」を辞書で引くだけにしてある（常時走る経路を重くしない）。

## 解散の中かどうかは、コードオブジェクトの同一性で見る

呼び出し元を段数で数えてはいけない（`@ctx.wrap` の層が1段挟まる。
`302_` が実際に踏んだ）。
関数名でも足りない。
`method_1` / `execute` は12個のマネージャが持っている。
そこで `QuestEndManager.method_1` / `.execute` の**コードオブジェクトそのもの**を先に引いておき、スタックの `f_code` と
`is` で突き合わせる。
この判定は `frames.MethodWatch` に置いてある（`304_` にも同じものが要ったので共通部品に上げた。
見張る相手の名前＝設計判断だけがこちらに残る）。

## ギルドが無い土地では何もしない

置き先が決まらないまま外すのは `302_` と違ってこちらの管轄ではない。
ゲームは自分の置き先を持っているので、**こちらが黙って降りればゲーム本来の挙動に戻るだけ**。
ダンジョンで解散した場合や、
その町にギルドが無い場合は差し替えを諦めて理由をログに残す。

記録は `302_` と同じ `party_leave.log` に `quest-end:` を付けて書く。
パーティの増減はあちらが既に書いている。
**1つの時系列で解散の一部始終を読めれば**切り分けは速い（`106_` と `207_` が
`battle_bgm.log` を共有しているのと同じ理由）。
"""


from instantale_modloader import frames, ui

# `302_` と同じログに書く（パーティの増減と解散を1つの時系列で読むため）。
LOG_BASENAME = "party_leave.log"
LOG_TAG = "quest-end"

# ---------------------------------------------------------------- 何を捕まえるか
# クエストクリアで解散するマネージャ。
# 実測で確定（上の docstring）。
DISBAND_MANAGERS = ("QuestEndManager",)

# クエスト放棄でも同じことをするか。
# `QuestRetireManager` が同じ役どころだと思われるが未観測なので既定では触らない。
# 放棄でも町のギルドに残ってほしくなったら True にする（捕まえ方は同じで、
# 対象クラスが増えるだけ）。
ALSO_ON_QUEST_RETIRE = False
RETIRE_MANAGERS = ("QuestRetireManager",)

# 解散の中かどうかを見るとき、スタックを遡る上限。
MAX_STACK = 60

# 見張るメソッド。
# 解散そのものは `method_1` の中にある。
# ただし置き直しがどちらの層から呼ばれているかは決めつけない。
# コードオブジェクトが引けないビルドでは、
# この名前のフレームに出会ったら持ち主クラスまで確かめる予備に落ちる（`frames.MethodWatch`）。
WATCH_METHODS = ("method_1", "execute")

# ---------------------------------------------------------------- 動作
# 「町のギルド」を見分ける facility_type。
# 実セーブで確認した値。
GUILD_FACILITY_TYPE = ui.GUILD_FACILITY_TYPE

# 解散の後、ゲームが置き直すのを待つ秒数。
# ここまで誰も動かさなければこちらで置く。
# 観測できているビルドでは removal の直後に置き直しが来るので使われない。
PLACE_TIMEOUT = 8.0

# 置き先を差し替えたことを画面に出すか。
# **ゲーム自身は行き先を言わない**ので、出さないと「離脱した」だけが残り、
# どこへ行ったのか分からなくなる。
# 出力は手が空いてから（クエスト終了は要約の流し込みが続くため。`300_` の知見）。
ANNOUNCE_DESTINATION = True
ANNOUNCE_TEXT = "{name}は{place}に留まることになった。"

# こちらが自分で置いたとき（時間切れの保険が働いたとき）だけ保存する。
# ゲームが置いた場合はゲーム自身の保存に乗るので触らない。
SAVE_AFTER_LATE_PLACE = True


def _text(value, limit=40):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    state = {
        # 解散したばかりの仲間。
        # id -> 置き先と状態。
        "pending": {},
    }

    write = ctx.logger(LOG_BASENAME, tag=LOG_TAG + ":")

    # 画面と
    # Clock は共通部品を通す（`add_text` の待ち方・スレッドの扱いがそこに入っている）。
    # 自前ボタンは出さないので mark は要らない。
    screen = ui.Screen(ctx, write, tag="quest-end guild")

    # -------------------------------------------------- 解散の中かどうかを見る
    def watched_managers():
        names = list(DISBAND_MANAGERS)
        if ALSO_ON_QUEST_RETIRE:
            names += list(RETIRE_MANAGERS)
        return names

    # 解散マネージャのコードオブジェクトを先に引いておき、
    # スタックの `f_code` と突き合わせる見張り（判定そのものは
    # `frames.MethodWatch`。ここに残るのは「誰を見張るか」＝この
    # mod の設計判断だけ）。
    # 表は初回の判定時に1度だけ引く。
    disband = frames.MethodWatch(watched_managers(), WATCH_METHODS,
                                 max_stack=MAX_STACK, on_warn=write)

    def in_disband():
        """いま解散処理の中か。中なら `'QuestEndManager.method_1'` を返す。"""
        return disband.current()

    # ------------------------------------------------------------ 置き先を決める
    def guild_here(app):
        """いま居る町のギルド。`(施設, ノード, エリア)`。

        ギルドが無ければ `(None, None, エリア)`。
        **見つからないことは正常な答え**で、
        そのときは差し替えを諦めてゲーム本来の置き先に任せる。
        """
        area = ui.current_area(app)
        facility, node = ui.find_guild(area, GUILD_FACILITY_TYPE)
        return facility, node, area

    def character_of(app, character_id):
        characters = getattr(getattr(app, "world", None), "characters", None)
        if isinstance(characters, dict) and character_id is not None:
            return characters.get(str(character_id))
        return None

    def name_of(app, character_id):
        name = _text(getattr(character_of(app, character_id), "name", ""))
        return name or str(character_id)

    def describe_answer(app, value):
        """ゲームが返した置き先を、ログに読める形にする。読むだけ。

        中身の解釈はしない（`(施設, ノード)` をほどかずに渡すとゲームが落ちる。
        GAME.md §2.8）。
        名前が引ければ名前、駄目なら型だけ出す。
        """
        first = value[0] if isinstance(value, (tuple, list)) and value else value
        return ui.facility_name(app, first) or type(first).__name__

    def where_label(area):
        name = _text(getattr(area, "name", ""))
        return name or ui.area_id_of(area) or "?"

    def register(app, member_id, source):
        """解散した仲間を控える。置き先はこの時点で引く。

        帰還は解散より先に済んでいるので、ここで引く「いま居る町」は戻ってきた町になる（実測の順序。
        docstring 参照）。
        後から引き直すと、待っている間にプレイヤーが移動した町を拾ってしまう。
        """
        facility, node, area = guild_here(app)
        name = name_of(app, member_id)
        if facility is None:
            # ダンジョンで解散した・その町にギルドが無い。
            # 黙って降りるとゲーム本来の置き先（雇った町）になる。
            write("{!r} ({}) left the party in {} via {} ― no {!r} there; "
                  "leaving the game's own destination alone"
                  .format(name, member_id, where_label(area), source,
                          GUILD_FACILITY_TYPE))
            return

        state["pending"][member_id] = {
            "facility": facility,
            "node": node,
            "character": character_of(app, member_id),
            "name": name,
            "source": source,
            "done": None,
        }
        write("{!r} ({}) left the party in {} via {} -> {!r}"
              .format(name, member_id, where_label(area), source,
                      ui.facility_name(app, facility) or "the guild"))
        # ゲームが誰も動かさなかったときの保険。
        # Clock なのでメインスレッドで走る（解散はゲーム側の別スレッド）。
        screen.schedule(lambda: late_place(app, member_id), PLACE_TIMEOUT)

    def announce(app, name, facility):
        if not ANNOUNCE_DESTINATION:
            return
        place = ui.facility_name(app, facility)
        if not place:
            return
        text = ANNOUNCE_TEXT.format(name=name, place=place)
        # クエスト終了は報酬・才能・要約と出力が続く。
        # その最中に差し込むと押し流されるので手が空くのを待つ（`300_` の知見）。
        # 既に確定した出来事なので、待ちきれなくても出す。
        screen.when_idle(app, lambda: screen.say(app, text),
                         proceed_on_timeout=True, tag="quest-end guild announce")

    def late_place(app, member_id):
        """時間切れの置き直し。観測できているビルドではここまで来ない。"""
        entry = state["pending"].pop(member_id, None)
        if entry is None or entry["done"]:
            return
        write("nobody moved {!r} within {:.0f}s; placing them by hand"
              .format(entry["name"], PLACE_TIMEOUT))
        try:
            # 自分のフックへ戻ってくるが、控えは既に落としてあるので素通りする。
            app.move_npc_to_facility(member_id, entry["character"],
                                     entry["facility"], entry["node"])
        except Exception:
            ctx.log_exc("quest-end guild: move_npc_to_facility({!r}) failed"
                        .format(member_id))
            return
        announce(app, entry["name"], entry["facility"])
        if SAVE_AFTER_LATE_PLACE:
            # ゲームが置いた場合はゲーム自身の保存に乗るので触らない。
            # ここはこちらが勝手に置いた場合だけで、保存されないと次回起動で戻る。
            try:
                app.save_game()
                write("saved after placing {!r} by hand".format(entry["name"]))
            except Exception:
                ctx.log_exc("quest-end guild: save_game failed")

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.remove_party_member", required=False)
    def remove_party_member(orig, self, member_id, *args, **kwargs):
        """解散を捕まえて控える。外す処理そのものには触らない。"""
        source = None
        try:
            source = in_disband()
        except Exception:
            ctx.log_exc("quest-end guild: cannot tell where remove_party_member "
                        "was called from")
        if source is None:
            # 死別・`302_` の会話からの別れ・その他。
            # こちらの管轄ではない。
            return orig(self, member_id, *args, **kwargs)
        try:
            register(self, str(member_id), source)
        except Exception:
            ctx.log_exc("quest-end guild: cannot register {!r}".format(member_id))
        return orig(self, member_id, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.get_party_leave_facility", required=False)
    def get_party_leave_facility(orig, self, character_instance=None, *args, **kwargs):
        """解散処理の中で聞かれたら、いま居る町のギルドを答える。

        戻り値の形はゲームのものに合わせる。
        `(施設, ノード)` のタプル（GAME.md §2.8）。
        中身は解釈せず、形だけ写す。
        """
        try:
            value = orig(self, character_instance, *args, **kwargs)
        except Exception:
            # 解散の中でなければこちらの管轄ではない。
            # 握り潰さずそのまま投げる（mod があるとゲームの失敗が消える、
            # が一番たちが悪い）。
            if in_disband() is None:
                raise
            # 解散の中なら、こちらが答えれば先へ進める。
            ctx.log_exc("quest-end guild: the game's get_party_leave_facility failed")
            value = None
        try:
            source = in_disband()
            if source is None:
                return value
            facility, node, area = guild_here(self)
            if facility is None:
                write("asked for a leave facility in {} via {} but there is no {!r} "
                      "there; keeping the game's answer"
                      .format(where_label(area), source, GUILD_FACILITY_TYPE))
                return value
            write("leave facility via {} -> {!r} (was {!r})"
                  .format(source, ui.facility_name(self, facility) or "the guild",
                          describe_answer(self, value)))
            if isinstance(value, tuple):
                return (facility, node) if len(value) >= 2 else (facility,)
            if isinstance(value, list):
                return [facility, node] if len(value) >= 2 else [facility]
            if value is None:
                # 形が分からない（ゲーム側が落ちた）。
                # 既知の形で答える。
                return (facility, node)
            return facility
        except Exception:
            ctx.log_exc("quest-end guild: cannot answer get_party_leave_facility")
            return value

    @ctx.wrap("__main__:InstantaleApp.move_npc_to_facility", required=False)
    def move_npc_to_facility(orig, self, character_id, character_instance=None,
                             target_facility=None, target_node=None, *args, **kwargs):
        """解散したばかりの相手を動かす呼び出しだけ、置き先を差し替える。

        NPC の日常の移動でも呼ばれる経路なので、ここではスタックを見ない。
        控えの辞書を1回引くだけで、関係が無ければそのまま通す。
        """
        entry = state["pending"].get(str(character_id))
        if entry is None or entry["done"]:
            return orig(self, character_id, character_instance, target_facility,
                        target_node, *args, **kwargs)
        try:
            if target_facility is entry["facility"]:
                # `get_party_leave_facility` の差し替えが既に効いている。
                entry["done"] = "already"
                write("{!r} is already headed for the guild".format(entry["name"]))
            else:
                entry["done"] = "redirected"
                write("{!r}: {!r} -> {!r}".format(
                    entry["name"],
                    ui.facility_name(self, target_facility) or target_facility,
                    ui.facility_name(self, entry["facility"]) or "the guild"))
                target_facility, target_node = entry["facility"], entry["node"]
        except Exception:
            ctx.log_exc("quest-end guild: cannot redirect {!r}".format(character_id))
        result = orig(self, character_id, character_instance, target_facility,
                      target_node, *args, **kwargs)
        try:
            announce(self, entry["name"], target_facility)
        except Exception:
            ctx.log_exc("quest-end guild: cannot announce the destination")
        return result

    ctx.log("quest-end guild: log -> {}".format(log_path))
