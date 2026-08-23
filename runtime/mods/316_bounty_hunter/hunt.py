# -*- coding: utf-8 -*-
"""追手を出すかどうかと、どれだけ強いかの計算。ゲームには触らない。

ここに在るのは数だけで、いつ呼ぶか・どう起こすかは入口の `bounty_hunter.py`。
分けてあるのは、この部分をゲーム抜きで確かめられるようにするため
（`tools/tests/test_bounty_hunter.py`）。

手配度の**読み方**はローダ（`ui.lawfulness_by_area`。TECH.md §5.1.3）。
ここが持っているのは**数え方**で、それは MOD ごとに違う
（`309_` は罰金の基準、`220_` は下調べの要約、ここは追手の条件と強さ）。

数え方は3本とも同じ形にしてある:

    重さ = 0 - 手配度        （手配度が 0 未満の土地だけ。平常の 10 は数えない）

`309_office_pardon` の罰金がこの数え方で、
手配度 -3 の土地は重さ3 ＝ 3,000ゴールドになる。
追手も同じ物差しに乗せておくと、「罰金3,000ゴールドぶんの手配」と
「追手が来る手配」を同じ数で比べられる。
"""

#: 手配とみなす境界。これ未満の手配度が手配（`309_` の既定と同じ）。
#: 設定にしていないのは、ここを動かすと平常（10）の土地まで手配に数え始めて、
#: 追手が全世界から来るようになるため。
WANTED_THRESHOLD = 0


def weight_of(lawfulness):
    """その土地の手配の重さ。手配されていなければ 0。

    読めない値（`None` や文字列）は 0 にする。
    読めなかったことを手配の重さに変換しない。
    """
    if isinstance(lawfulness, bool) or not isinstance(lawfulness, (int, float)):
        return 0
    return max(0, WANTED_THRESHOLD - int(lawfulness))


def weights(by_area):
    """`{エリアid: 重さ}`。手配されていない土地は入れない。"""
    rows = {}
    for area_id, value in (by_area or {}).items():
        weight = weight_of(value)
        if weight > 0:
            rows[str(area_id)] = weight
    return rows


def total_weight(by_area):
    """全ての土地の重さの合計。"""
    return sum(weights(by_area).values())


def area_weight(by_area, area_id):
    """今いる土地の重さ。id が読めなければ 0。"""
    if area_id in (None, ""):
        return 0
    return weights(by_area).get(str(area_id), 0)


def should_send(here, total, start_wanted, start_total):
    """追手を出す条件。**2つのうちどちらかを満たせば出す。**

    | 条件 | 意味 |
    | --- | --- |
    | `here >= start_wanted` | 今いる土地で手配されている |
    | `total >= start_total` | よその土地の手配が積み上がっている |

    2つ目があるので、**手配されていない土地へ逃げても追ってくる**。
    追手が「エリアを跨ぐ衛兵」であるための条件はこちら側。
    """
    if here <= 0 and total <= 0:
        return False
    return here >= max(1, int(start_wanted)) or total >= max(1, int(start_total))


def difficulty_of(total, base, per_wanted, cap):
    """追手の難易度。**この数1つが敵のレベルと能力値の両方を決める**。

        難易度 = 下限 + 重さの合計 × 手配1あたりの難易度      （上限で頭打ち）

    ゲーム自身の衛兵は難易度 20 でレベル 21 の敵3体だった
    （実測。GAME.md §2.20）。既定の下限を 20 にしてあるのはそれに合わせたもので、
    **手配が軽いうちは衛兵と同じ強さの追手が来る**。

    合計（今いる土地だけではなく）で決めるのは、追手が賞金を追ってきた側だから。
    どこで捕まえたかではなく、いくら賞金がかかっているかで人数と腕が決まる。
    """
    base = max(1, int(base))
    cap = max(base, int(cap))
    try:
        scaled = base + float(per_wanted) * max(0, int(total))
    except (TypeError, ValueError):
        return base
    return int(min(cap, max(base, round(scaled))))


def ready(day, last_day, cooldown_days):
    """前の追手から日数が空いているか。

    `last_day` が `None`（まだ一度も来ていない）なら常に True。
    暦は MOD が自分で数えた通算日数で、ゲームの日付ではない
    （`elapse_days` に渡った日数を足しているだけ）。
    """
    if last_day is None:
        return True
    cooldown = max(0, int(cooldown_days))
    if cooldown == 0:
        return True
    try:
        return (float(day) - float(last_day)) >= cooldown
    except (TypeError, ValueError):
        return True
