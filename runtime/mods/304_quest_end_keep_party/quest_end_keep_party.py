# -*- coding: utf-8 -*-
"""機能追加: クエストクリアで**そもそもパーティを解散しない**。

ゲーム本来のクエストクリアは、同行した仲間を全員パーティから外して雇った町へ
帰してしまう。`303_` は**帰す先**を「いま居る町のギルド」に変えて会いに行けるように
したが、そこで別れること自体は変わらない ― 続けて次の依頼へ向かいたければ、
毎回ギルドで雇い直す必要がある。この mod は**外す処理そのものを起こさせない**。
クエストが終わっても仲間は仲間のまま、次の冒険にそのまま同行する。

## `303_` との関係 ― 重なるが、噛み合う

同じ場面（`QuestEndManager` の解散）に手を入れるので、両方入れると **`304_` が勝つ**。
番号が後 ＝ フックが**外側**に載るため、こちらが解散を止めた時点で `303_` の
`remove_party_member` フックには**そもそも呼び出しが届かない**（控えも取らないので
置き直しも時間切れの保険も走らない）。「誰も外れない」のだから「外れた人をどこに
置くか」は出番が無い、という素直な形に収まる。

`303_` が要らなくなるわけではない:

* こちらが**降りた**とき（`QuestEndManager` を捕まえられないビルド、`ALSO_ON_QUEST_RETIRE`
  を切ったままのクエスト放棄）は、`303_` が本来どおり置き先を差し替える
* この mod のファイル名の頭に `_` を付けて止めれば、`303_` の挙動だけが残る

## どこで解散しているか（実測で確定・2026-07-26。TECH.md §7.8）

```
add_text('パーティは帰還した...') → 報酬・才能
remove_party_member('71' '魔導師・リアナ')
  from QuestEndManager.method_1 (instantale.py:6602)
  <- QuestEndManager.execute (instantale.py:6635) <- run (threading.py:953)
remove_party_member: party ['player', '71'] -> ['player']
add_text('魔導師・リアナはパーティから離脱した。')
observed: the game placed '71' at '漣のギルド (Ripple Guild)' after its own removal
```

名簿を実際に書き換えているのは `remove_party_member` の**中**（前後のログで
`['player','71'] -> ['player']` と出ている）。だから外す処理を止めるには、
**この呼び出しを解散の場面でだけ通さなければよい**。名簿には指一本触れない ―
「外れなかった」のだから、パーティは元のままでよい。

止めるだけでは足りないものが2つあり、順に潰す:

1. **置き直し** ― ゲームは removal の後で `move_npc_to_facility` を呼ぶ。放っておくと
   仲間がパーティに居るまま施設にも登録され、ギルドに立っているのに付いてくる
2. **文言** ― `'…はパーティから離脱した。'` が出る。**離脱していないのだから嘘になる**

どちらも「引き留めた相手か」を控えで見るので、**先に外してから置く**（実測の順序）
にも、**置き先を聞いてから外す**にも間に合う（後者は未観測だが
`get_party_leave_facility` で先回りしてある）。**聞きもせず置いてから外すビルド**
だけは、置く時点でその相手が解散対象だと知る手掛かりが無いので取りこぼす ―
そのときも解散自体は止まる（`302_` の「ここで別れる」で外せば辻褄は戻る）。

## 世界のどこにも居ない NPC を作らない

置き直しを止めるのは、**こちらが解散を止めた相手**についてだけ。控え（`kept`）に
載っている間しか見ないので、普段の NPC の移動は素通りする（`303_` と同じ理由で
`move_npc_to_facility` では**スタックを見ない** ― 日常の移動で何度も通る経路なので、
控えが空なら辞書が空かどうかを見るだけで抜ける）。

**本当に外れる相手の置き直しまで止めてはいけない。** 死別・`302_` の「ここで別れる」で
`remove_party_member` が解散の外から呼ばれたら、その id を控えから**即座に落とす** ―
外れた直後の置き直しを止めてしまうと、その NPC は世界のどこにも居なくなる（`302_` が
一番気を使っている失敗）。控えは `KEEP_WINDOW` 秒で自然に消えるので、置き直しが
来ないまま終わっても後を引かない。

## 意図的に変わること

* **雇い直しの費用がかからなくなる。** 本来はクエストごとに雇い直す前提の設計なので、
  ここはゲームバランスを変える改造だと分かった上で入れること
* パーティは自分では減らない。外したくなったら `302_` の「ここで別れる」を使う
  （そちらは置き先を決めてから外すので、世界から消えることはない）

記録は `302_` / `303_` と同じ `party_leave.log` に `quest-end keep:` を付けて書く
（パーティの増減・解散・引き留めが**1つの時系列で読める**方が切り分けが速い）。
"""

import datetime
import time

from instantale_modloader import frames

# `302_` / `303_` と同じログに書く（パーティの増減を1つの時系列で読むため）。
LOG_BASENAME = "party_leave.log"
LOG_TAG = "quest-end keep"

# ---------------------------------------------------------------- 何を捕まえるか
# クエストクリアの解散を行うマネージャ。実測で確定（上の docstring）。
DISBAND_MANAGERS = ("QuestEndManager",)

# クエスト**放棄**でも解散を止めるか。`QuestRetireManager` が同じ役どころだと
# 思われるが**未観測**なので既定では触らない（`303_` と同じ判断）。放棄でも仲間に
# 残ってほしくなったら True にする。
ALSO_ON_QUEST_RETIRE = False
RETIRE_MANAGERS = ("QuestRetireManager",)

# 見張るメソッド。解散そのものは `method_1` の中だが、どちらの層から呼ばれるかは
# 決めつけない（`frames.MethodWatch` が両方を表に入れる）。
WATCH_METHODS = ("method_1", "execute")

# 解散の中かどうかを見るとき、スタックを遡る上限。
MAX_STACK = 60

# ---------------------------------------------------------------- 動作
# 引き留めた相手を控えておく秒数。**この間だけ**置き直しと文言に手を入れる。
# 実測では removal の直後に置き直しと `add_text` が来るので数ミリ秒あれば足りるが、
# `303_` の時間切れの保険（8秒）より長く取って、そちらが動いた場合も止められる
# ようにしてある。過ぎれば控えは消え、その相手も普通の NPC として扱われる。
KEEP_WINDOW = 30.0

# 「…はパーティから離脱した。」を差し替えるか。**離脱していないので既定は差し替え。**
# False にすると、ゲームの文言がそのまま出る（実際には残っているのに離脱したと
# 書かれるので、切り分けのとき以外は勧めない）。
REPLACE_LEAVE_TEXT = True

# 離脱を告げる文を見分ける手掛かり。実測は '魔導師・リアナはパーティから離脱した。'。
# 相手の名前が入っていることも併せて確かめるので、これだけで巻き込むことはない。
LEAVE_TEXT_HINTS = ("パーティから離脱", "left the party")
KEEP_TEXT = "{name}はパーティに残り、引き続き行動を共にすることになった。"


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
        # 解散を止めた相手。id -> {"at": 時刻, "name": 名前, "source": 呼び出し元}。
        # `move_npc_to_facility` と `add_text` は、この辞書が空なら即座に降りる。
        "kept": {},
    }

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}: {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"),
                    LOG_TAG, text))
        except Exception:
            ctx.log_exc("quest-end keep: write failed")

    # -------------------------------------------------- 解散の中かどうかを見る
    def watched_managers():
        names = list(DISBAND_MANAGERS)
        if ALSO_ON_QUEST_RETIRE:
            names += list(RETIRE_MANAGERS)
        return names

    # 見張る相手（＝設計判断）だけがここに残る。判定そのものは `303_` と共通の
    # `frames.MethodWatch`（段数で数えない・関数名で決めない。TECH.md §7.8）。
    disband = frames.MethodWatch(watched_managers(), WATCH_METHODS,
                                 max_stack=MAX_STACK, on_warn=write)

    def in_disband():
        """いま解散処理の中か。中なら `'QuestEndManager.method_1'` を返す。"""
        return disband.current()

    # ------------------------------------------------------------------ 控え
    def character_of(app, character_id):
        characters = getattr(getattr(app, "world", None), "characters", None)
        if isinstance(characters, dict) and character_id is not None:
            return characters.get(str(character_id))
        return None

    def name_of(app, character_id):
        name = _text(getattr(character_of(app, character_id), "name", ""))
        return name or str(character_id)

    def id_of(character):
        """Character から id を読む。読めなければ空文字（控えない）。"""
        for attr in ("id", "character_id", "npc_id"):
            found = getattr(character, attr, None)
            if isinstance(found, (str, int)):
                return str(found)
        return ""

    def keep(app, member_id, source, reason):
        """引き留めた相手を控える。既に控えてあれば時刻だけ延ばす。"""
        entry = state["kept"].get(member_id)
        if entry is None:
            entry = {"name": name_of(app, member_id), "source": source}
            state["kept"][member_id] = entry
            write("{!r} ({}) stays in the party — {} {}".format(
                entry["name"], member_id, source, reason))
        entry["at"] = time.monotonic()
        return entry

    def kept_entry(member_id):
        """控えてある相手なら中身を返す。時間切れのものはここで落とす。"""
        entry = state["kept"].get(member_id)
        if entry is None:
            return None
        if time.monotonic() - entry["at"] > KEEP_WINDOW:
            state["kept"].pop(member_id, None)
            return None
        return entry

    def release(member_id, why):
        """控えから落とす。**本当に外れる相手の置き直しを邪魔しないため。**"""
        entry = state["kept"].pop(member_id, None)
        if entry is not None:
            write("{!r} ({}) is leaving for real ({}); not holding them any more"
                  .format(entry["name"], member_id, why))

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.remove_party_member", required=False)
    def remove_party_member(orig, self, member_id, *args, **kwargs):
        """解散の中からの removal だけ、**元の処理を呼ばない**。

        名簿を書き換えているのは `orig` の中なので、通さなければパーティは元のまま。
        こちらから名簿を触ることはしない。
        """
        key = str(member_id)
        source = None
        try:
            source = in_disband()
        except Exception:
            ctx.log_exc("quest-end keep: cannot tell where remove_party_member "
                        "was called from")
        if source is None:
            # 死別・`302_` の「ここで別れる」・その他。**本当に外れる。**
            # 控えに残っていると直後の置き直しまで止めてしまうので先に落とす。
            release(key, "removed outside the disband")
            return orig(self, member_id, *args, **kwargs)
        try:
            keep(self, key, source, "did not disband the party")
        except Exception:
            # 控えられなかったら止めない。中途半端に止めると、置き直しだけ通って
            # 「パーティに居るのに施設にも居る」状態になる。
            ctx.log_exc("quest-end keep: cannot hold {!r}; letting the game "
                        "disband as usual".format(member_id))
            return orig(self, member_id, *args, **kwargs)
        return None

    @ctx.wrap("__main__:InstantaleApp.get_party_leave_facility", required=False)
    def get_party_leave_facility(orig, self, character_instance=None, *args, **kwargs):
        """解散の中で置き先を聞かれたら、その相手を先に控える。

        実測のビルドは removal の**後**に置き直すので、ここまで来なくても
        `remove_party_member` 側で間に合う。**聞いてから外すビルド**（置き直しが
        removal より先に来る）でも取りこぼさないための先回りで、離脱の場面でしか
        呼ばれない関数なので常時の負担にはならない。
        """
        try:
            source = in_disband()
            if source is not None:
                member_id = id_of(character_instance)
                if member_id:
                    keep(self, member_id, source, "was asked where to leave them")
        except Exception:
            ctx.log_exc("quest-end keep: cannot hold the character the game "
                        "asked about")
        return orig(self, character_instance, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.move_npc_to_facility", required=False)
    def move_npc_to_facility(orig, self, character_id, character_instance=None,
                             target_facility=None, target_node=None, *args, **kwargs):
        """引き留めた相手を施設へ置く呼び出しだけ、通さない。

        NPC の日常の移動でも呼ばれる経路なので、**控えが空なら何もせず抜ける**
        （`303_` と同じ理由でスタックは見ない）。
        """
        if not state["kept"]:
            return orig(self, character_id, character_instance, target_facility,
                        target_node, *args, **kwargs)
        try:
            entry = kept_entry(str(character_id))
        except Exception:
            ctx.log_exc("quest-end keep: cannot tell whether {!r} was held"
                        .format(character_id))
            entry = None
        if entry is None:
            return orig(self, character_id, character_instance, target_facility,
                        target_node, *args, **kwargs)
        write("not placing {!r} anywhere: they are still in the party".format(
            entry["name"]))
        return None

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context, *args, **kwargs):
        """「…はパーティから離脱した。」を、残った旨に差し替える。

        引き留めた直後に**その相手の名前が入った離脱の文**だけを見る。控えが空なら
        何もせず抜けるので、普段の描画には触らない。
        """
        if REPLACE_LEAVE_TEXT and state["kept"] and isinstance(context, str):
            try:
                replaced = keep_text_for(context)
                if replaced is not None:
                    write("text: {!r} -> {!r}".format(_text(context, 60),
                                                      _text(replaced, 60)))
                    context = replaced
            except Exception:
                ctx.log_exc("quest-end keep: cannot rewrite the farewell line")
        return orig(self, context, *args, **kwargs)

    def keep_text_for(context):
        """離脱を告げる文なら、残った旨の文を返す。違えば None。"""
        if not any(hint in context for hint in LEAVE_TEXT_HINTS):
            return None
        for member_id in list(state["kept"]):
            entry = kept_entry(member_id)
            if entry is None:
                continue
            name = entry["name"]
            # 名前も入っていることを確かめる（同じ場面で他人の離脱が流れても
            # 巻き込まないため）。
            if name and name in context:
                return KEEP_TEXT.format(name=name)
        return None

    ctx.log("quest-end keep: log -> {}".format(log_path))
