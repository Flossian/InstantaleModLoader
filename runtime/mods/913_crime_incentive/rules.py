# -*- coding: utf-8 -*-
"""犯罪の見返りの決まり（ゲームに触らない部品）。

本体（`crime_incentive.py`）が読んだ値を受け取り、額と率だけを返す。
設定はモジュールのグローバルへ書き込まれるので（TECH.md §3.8.1）、本体が持って引数で渡す。
"""

import math

from instantale_modloader import wanted

#: 盗みの規模の段。0 は金を奪っていない。
SCALE_MAX = 4

BUY = "買価"
SELL = "売価"


def _get(container, name):
    """LLM の戻り値から項目を1つ読む。dict か属性を持つ型かは決めつけない。"""
    if isinstance(container, dict):
        return container.get(name)
    return getattr(container, name, None)


def _amount(value):
    """額を整数にする。読めない・負・真偽値は None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def clamp_scale(value):
    """LLM の答えを 0〜4 の整数に均す。読めなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().lstrip("+")
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(SCALE_MAX, number))


def gold_claims(process):
    """facilitator の処理の並びが、プレイヤーへ動かすと言った金の合計。

    `get_gold`（target が player）と、`move_gold`（player へ／player から）だけを数える。
    実際に動いた額は本体が所持金の差で見る。こちらは突き合わせ用の控え。
    """
    if not isinstance(process, (list, tuple)):
        return 0
    total = 0
    for item in process:
        kind = _get(item, "type")
        amount = _amount(_get(item, "amount"))
        if not amount:
            continue
        if kind == "get_gold" and _get(item, "target") == "player":
            total += amount
        elif kind == "move_gold":
            source = _get(item, "from_character")
            dest = _get(item, "to_character")
            if dest == "player" and source != "player":
                total += amount
            elif source == "player" and dest != "player":
                total -= amount
    return total


def guide_amount(quest_reward, share_pct):
    """盗みの目安の額。その土地の依頼1件の報酬 × 割合。"""
    if not quest_reward or quest_reward <= 0 or share_pct <= 0:
        return 0
    return int(round(quest_reward * share_pct / 100.0))


def plan_loot(gained, guide, floor_pct, ceiling_pct):
    """`(所持金へ足す額, 理由)` を返す。減らすときは負。

    | 理由 | |
    |---|---|
    | `"no_guide"` | 目安が 0（割合が 0、または報酬が引けない） |
    | `"gave_nothing"` | LLM が金を動かさなかった。額を決めていないとみなして目安を渡す |
    | `"floor"` | 目安の `floor_pct`% に満たないので、そこまで足す |
    | `"ceiling"` | 目安の `ceiling_pct`% を超えたので、そこまで削る |
    | `"within"` | 幅の中。触らない |
    """
    if guide <= 0:
        return 0, "no_guide"
    if gained <= 0:
        return guide, "gave_nothing"
    floor = int(round(guide * max(0, floor_pct) / 100.0))
    ceiling = max(floor, int(round(guide * max(100, ceiling_pct) / 100.0)))
    if gained < floor:
        return floor - gained, "floor"
    if gained > ceiling:
        return ceiling - gained, "ceiling"
    return 0, "within"


def cool_down(lawfulness, away_days, days, period_days, step, restore_to):
    """離れている土地の手配が時とともに戻る。`(新しい手配度, 残りの日数, 戻した回数)`。

    離れていた日数を積み、`period_days` ごとに `step` だけ戻す。戻すのは `restore_to` まで。
    端数の日数は次へ持ち越す。`restore_to` 以上の土地は何もせず、積んだ日数も捨てる
    （次に罪を犯して下回ったら、そこから数え始める）。
    全域手配の線で止めるのは `hold_hunted` の役目で、ここでは見ない。
    """
    if lawfulness is None or lawfulness >= restore_to or period_days <= 0 or step <= 0:
        return lawfulness, 0, 0
    away = max(0, int(away_days or 0)) + max(0, int(days))
    times = away // period_days
    if not times:
        return lawfulness, away, 0
    after = min(restore_to, lawfulness + times * step)
    away -= times * period_days
    return after, (0 if after >= restore_to else away), times


def hold_hunted(plans, total_before, hold_total):
    """全域手配の間は、重さの合計が `hold_total` に届いたところで戻すのを止める。

    `plans` は `[(土地, 前, 後), ...]`（戻す予定）、`total_before` は全ての土地の重さの合計（`wanted.total_of`）
    （居る土地も含む）。合計が `hold_total` 未満なら手を付けない。
    以上なら、合計から `hold_total` を引いた分だけ重さを減らせる。
    重い土地から順に配り、配りきれなかった土地はそこで止める（重さの無い 0〜10 の戻りは妨げない）。
    返すのは同じ形の並び。
    """
    if hold_total <= 0 or total_before < hold_total:
        return list(plans)
    budget = total_before - hold_total
    held = []
    for area_id, before, after in sorted(plans, key=lambda plan: plan[1]):
        reduce = wanted.weight_of(before) - wanted.weight_of(after)
        allowed = min(reduce, budget)
        budget -= allowed
        held.append((area_id, before, after if allowed >= reduce else before + allowed))
    return held


#: 能力値の基準。ここで基準の確率になる（`313_` の基準と同じ。作成時は 11〜30）。
ABILITY_PIVOT = 15

#: 確率の下限と上限（%）。どれだけ鍛えても見つかることはあり、どれだけ不器用でも機会はある。
CHANCE_FLOOR = 5
CHANCE_CEILING = 95

#: 持ち物のマスの数（`InventoryGrid` は 4列×6段。GAME.md §2.13）。
INVENTORY_CELLS = 24


def ability_chance(score, base_pct, per_point_pct, cap_pct=CHANCE_CEILING):
    """能力値から成功の確率（%）。基準 15 で `base_pct`、1点ごとに `per_point_pct`。

    読めない能力値は基準とみなす（読めないことを不利にも有利にもしない）。
    """
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = ABILITY_PIVOT
    pct = base_pct + (score - ABILITY_PIVOT) * per_point_pct
    return max(CHANCE_FLOOR, min(min(CHANCE_CEILING, cap_pct), pct))


#: レア度の段（GAME.md §2.13.2 のスキーマの6段）。低い順。知らない語は最下段とみなす。
RARITY_RANKS = ("common", "rare", "magical", "epic", "legendary", "mythic")


def rarity_rank(record):
    rarity = record.get("rarity") if isinstance(record, dict) else None
    return RARITY_RANKS.index(rarity) if rarity in RARITY_RANKS else 0


def buy_price(record):
    attributes = record.get("attributes") if isinstance(record, dict) else None
    return _amount(attributes.get(BUY)) if isinstance(attributes, dict) else None


def median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def theft_penalties(record, shelf, streak, streak_step, cell_step, price_step, price_cap,
                    rarity_step):
    """盗む品と回数で下がる分（%）。`{"streak", "size", "price", "rarity"}`。

    - 連続: その店で今日すでに試した回数 × `streak_step`
    - 大きさ: 1マスを超えた分 × `cell_step`
    - 値段: 棚の買価の中央値から倍になるごとに `price_step`（上限 `price_cap`）。
      世界ごとに物価の桁が違うので、絶対額ではなくその店の棚と比べる
    - レア度: 段（common 0 〜 mythic 5）× `rarity_step`
    """
    out = {"streak": max(0, int(streak or 0)) * streak_step,
           "size": max(0, cells_of(record) - 1) * cell_step,
           "price": 0,
           "rarity": rarity_rank(record) * rarity_step}
    price = buy_price(record)
    middle = median([p for p in (buy_price(other) for other in shelf or ()) if p and p > 0])
    if price and middle and price > middle:
        out["price"] = min(price_cap, int(math.log(price / float(middle), 2) * price_step))
    return out


def theft_chance(base_chance, penalties):
    """器用の確率から品と回数の分を引く。下限は `CHANCE_FLOOR`。"""
    return max(CHANCE_FLOOR, base_chance - sum(penalties.values()))


def caught_chance(hand_pct, sense_pct):
    """見咎められる確率（%）。抜き取りにしくじり、かつ視線に気づけなかったとき。"""
    return (100 - hand_pct) * (100 - sense_pct) / 100.0


def cells_of(record):
    """品が占めるマスの数（`items.to_dict` の形）。"""
    width = record.get("width_slots") if isinstance(record, dict) else None
    height = record.get("height_slots") if isinstance(record, dict) else None
    width = width if isinstance(width, int) and not isinstance(width, bool) and width > 0 else 1
    height = height if isinstance(height, int) and not isinstance(height, bool) and height > 0 else 1
    return width * height


def free_cells(records):
    """持ち物の空きマスの見積もり（並び方による隙間は数えない）。"""
    used = sum(cells_of(record) for record in records)
    return max(0, INVENTORY_CELLS - used)


def intimidation_rate(lawfulness, per_point, cap_pct):
    """手配の重さから店の怯え方（0〜1 の率）。手配されていなければ 0。

    重さはローダの `wanted.weight_of`（手配度が 0 からどれだけ下か。-60 なら 60）。
    """
    depth = wanted.weight_of(lawfulness)
    if not depth:
        return 0.0
    pct = min(max(0.0, float(cap_pct)), depth * max(0.0, float(per_point)))
    return pct / 100.0


def intimidated_price(key, price, rate, sell_too):
    """怯えた店の値段。触らないなら None。

    店の品（`買価`）は下がり、売りに出したプレイヤーの品（`売価`）は上がる。
    """
    if rate <= 0 or price is None:
        return None
    if key == BUY:
        return price * (1.0 - rate)
    if key == SELL and sell_too:
        return price * (1.0 + rate)
    return None
