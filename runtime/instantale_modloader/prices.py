# -*- coding: utf-8 -*-
"""ゲームが決めている値段をローダが1箇所で持つ（期間は `durations`）。

宿屋の部屋の値段のように、**ゲームが決めている額**を変える MOD
（`315_vacation_custom`）と、その額を**先に知りたい** MOD
（`330_real_estate` / `331_facility_investment` の自分の建物での滞在）がある。
MOD どうしは import しない（TECH.md §3.2.3）ので、両者はここで繋がる。

    値段を変える側      prices.declare(prices.INN_ROOM, fn, owner="315_…")
    値段を知りたい側    price = prices.inn_room(app, quality)   # int か None

**既定はゲーム自身の値**で、変える MOD が入っていなければそれが答えになる。
置く側は「答えを返す関数」を置く（値ではない。設定で変わるうえ、
置き直す責任を読む側に持ち込まないため。`durations` と同じ約束）。

読む側が額を先に知りたいのは、**ゲームに引かせてから返すのをやめる**ため。
ゲームは所持金を `player.gold` に直接書くので、引き落としの瞬間だけを掴む口が無い。
そこで引かれるぶんを先に足しておき、ゲームが引いて元に戻す（前払い調整。
`314_area_move_custom` の運賃・`315_vacation_custom` の宿代と同じ形）。
差額で当てると、同じ区間で動いた他の MOD の金まで巻き込む。

登録簿は `durations` と同じ1つ（`sys` の `_instantale_durations`）。
`durations.forget(owner)` で期間と値段がまとめて外れるので、片付けの口は増えない。
"""
from . import durations

#: 宿屋の部屋1回。置く関数は `fn(app, quality=...)`、答えは `{"price": int}`。
#: 「ゲームのままでよい」なら `None` を返す（既定へ落ちる）。
INN_ROOM = "inn_room"

#: 素のゲームの部屋の値段（`quality` の実値 → 額）。
#: 実測（部屋選びのボタン `個室(100G)` と `VacationStartManager` の args。
#: GAME.md §2.17）。**観測できたものだけ置く**。
GAME_ROOM_PRICES = {"kennel": 0, "bunk": 10, "private_room": 100,
                    "luxury_suite": 1000}


def declare(kind, fn, owner="", write=None):
    """その種類の値段を決める関数を置く。置き換えたら前の持ち主を返す。"""
    return durations.declare(kind, fn, owner=owner, write=write)


def source_of(kind):
    """その種類を決めている MOD の名前。誰も置いていなければ `""`（ゲームの値）。"""
    return durations.source_of(kind)


def game_inn_room(quality):
    """素のゲームの部屋の値段。知らない `quality` なら None。"""
    return GAME_ROOM_PRICES.get(str(quality))


def inn_room(app, quality, write=None):
    """宿屋の部屋1回の値段。**分からなければ None**（呼ぶ側が別の道へ落とす）。

    知らない `quality` で None を返すのは、ゲームの更新で語彙が変わったときに
    **当て推量の額を前払いしない**ため。0 と「分からない」は別の答えにする。
    """
    answer = durations.ask(INN_ROOM, app, write=write, quality=quality)
    if answer is None:
        return game_inn_room(quality)
    price = answer.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        if write:
            write("WARN prices: inn_room from {!r} returned {!r}, not a number; "
                  "using the game's own value".format(source_of(INN_ROOM), price))
        return game_inn_room(quality)
    return max(0, int(price))
