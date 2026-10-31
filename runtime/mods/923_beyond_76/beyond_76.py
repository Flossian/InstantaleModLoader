# -*- coding: utf-8 -*-
"""難易度と品の価値が 76 を超えた分を、報酬と品の強さにも効かせる。

ゲームは依頼の難易度と品の価値を 100 まで持てる（`318_` で上げた街の依頼、LLM が書く品）が、
報酬・才能点・日雇い・品の値段・武器と回復の数は 76（`QUEST_DIFFICULTY_VALUE_MAX`）の値で止める
（VERIFICATION.md §3.112。0〜100 の曲線は `243_probe_value_curves` の実測）。
76 を超えると強くなるのは敵だけになる。

この MOD は `scripts.functions` の関数を包み、76 を超えた分を 76 までの曲線の傾きのまま直線で延ばす:

    f(v) = f(76) + 傾き × (min(v, 100) − 76)

| 関数 | 傾き |
|---|---|
| `get_quest_reward` / `get_labor_reward` / `get_equipment_price` / `get_heal_item_price` / `get_other_item_price` | `(f(76) − f(66)) / 10`。乱数を引かない関数なので、初めて 76 を超えた呼び出しのときに素の関数で計る |
| `get_weapon_spec` / `get_heal_spec` | 乱数を引くので計らず、243 の中央の値の 56〜76 の最小二乗（`WEAPON_SLOPE` / `HEAL_SLOPE`）。`f(76)` の揺らぎはそのまま残る |
| `get_talent_point_quest_clear` | 67 から 12 で止まる作り。76 から `TALENT_EVERY` 段ごとに +1 |
| 逆引き `get_*_level_from_price` | 値段の延ばし方の逆。76 の値段より高ければ、その分を傾きで割って段に足す（素は 76 を返す） |

延ばした数は 76 の値を素の関数から取り、そこへ足すだけなので、76 までの曲線は1つも変えない。
乱数を引く関数は素の関数を1回だけ呼ぶ（引数を 76 に替えて）ので、乱数の進みも変わらない。
訓練の経験（`get_training_experience_point`）は1段で約1.15倍の指数なので延ばさない（100 で数十億になる）。
ゲーム自身の切り（`clamp_*`）と敵の関数（もともと 100 まで伸びる）は触らない。

品の価値をこの上限まで効かせることは、窓口 `value_limits` に出す（TECH.md §3.3.26）。
品を作る MOD（`312_` / `340_` / `921_`）は、そこを見て価値を切る上限を決める。

遊び方の説明は DOC.md、検証の経過は VERIFICATION.md §3.118。
"""

import sys
import threading

from instantale_modloader import value_limits

LOG_BASENAME = "beyond_76.log"

#: 延ばす上限。難易度の天井（`318_` の `DIFFICULTY_CEILING`、`922_` の上限）と同じ 100。
LIMIT = 100

#: 決まった値を返す関数の傾きを計る幅（76 と 76 − SPAN の差を SPAN で割る）。
SPAN = 10

#: 武器と回復の数の傾き。243 の 0〜100 の曲線（2回の起動、各5回の中央の値）の 56〜76 の最小二乗。
WEAPON_SLOPE = 4.3
HEAL_SLOPE = 4.8

#: 同じ (関数, 値) の記録は1回。記録の行の上限。
LOG_LIMIT = 300

#: 計測の probe（`236_` / `243_` など）が数を流している間、自分のスレッドの id を置く印。その間は記録しない
#: （243 は読み込みの後に値段の関数を 0〜100 で流すので、記録が測りの行で埋まり、遊んだ回の行が残らなかった）。
DRY_THREAD_MARK = "_instantale_probe_dry_run_thread"

# ---------------------------------------------------------------- 設定（mod.json）
# 報酬・日雇い・才能点を延ばすか。
REWARDS = True
# 品の値段・武器と回復の数・逆引きを延ばすか。切ると品を作る MOD の上限も 76 のまま。
ITEMS = True
# 才能点を何段ごとに 1 増やすか。0 で延ばさない。
TALENT_EVERY = 10

#: (関数名, 傾きの決め方, 種類)。決め方は "measure"（素の関数で計る）か数（定数）。
LINEAR = (
    ("get_quest_reward", "measure", "rewards"),
    ("get_labor_reward", "measure", "rewards"),
    ("get_equipment_price", "measure", "items"),
    ("get_heal_item_price", "measure", "items"),
    ("get_other_item_price", "measure", "items"),
    ("get_weapon_spec", WEAPON_SLOPE, "items"),
    ("get_heal_spec", HEAL_SLOPE, "items"),
)
#: 逆引きと、その元の値段の関数。
INVERSE = (
    ("get_equipment_level_from_price", "get_equipment_price"),
    ("get_heal_item_level_from_price", "get_heal_item_price"),
    ("get_other_item_level_from_price", "get_other_item_price"),
)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def like(sample, value):
    """`sample` と同じ型で返す。整数なら四捨五入。"""
    return int(round(value)) if isinstance(sample, int) and not isinstance(sample, bool) else float(value)


def first_value(args, kwargs):
    """1つ目の引数（位置でも名前でも）。`(値, 名前 / None)`。無ければ `(None, None)`。"""
    if args:
        return args[0], None
    if len(kwargs) == 1:
        name = next(iter(kwargs))
        return kwargs[name], name
    return None, None


def call_with(orig, args, kwargs, name, value):
    """1つ目の引数だけを `value` に替えて呼ぶ。"""
    if name is None:
        return orig(value, *args[1:], **kwargs)
    changed = dict(kwargs)
    changed[name] = value
    return orig(*args, **changed)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    owner = "923_beyond_76"
    slopes = {}
    logged = set()
    lock = threading.Lock()

    def base():
        return value_limits.game_max()

    def enabled(kind):
        return bool(REWARDS) if kind == "rewards" else bool(ITEMS)

    def note(name, value, top_value, result):
        if getattr(sys, DRY_THREAD_MARK, None) == threading.get_ident():
            return
        key = (name, value)
        with lock:
            if key in logged or len(logged) >= LOG_LIMIT:
                return
            logged.add(key)
        write("{}({}) -> {}（76 の値 {}）".format(name, value, result, top_value))

    value_limits.provide(owner, ctx, lambda: LIMIT if ITEMS else value_limits.game_max())

    def measured(name, orig, args, kwargs, key_name, top_value):
        """決まった値を返す関数の 76 までの傾き。1プロセスで1回だけ計る。"""
        with lock:
            found = slopes.get(name)
        if found is not None:
            return found
        lower = call_with(orig, args, kwargs, key_name, base() - SPAN)
        slope = (top_value - lower) / float(SPAN) if is_number(lower) else 0.0
        with lock:
            slopes[name] = slope
        write("{}: 傾き {:.2f}（{} の値 {} → 76 の値 {}）".format(name, slope, base() - SPAN, lower, top_value))
        return slope

    def linear(name, how, kind):
        @ctx.wrap("scripts.functions:" + name, required=False, safe=True)
        def wrapper(orig, *args, **kwargs):
            value, key_name = first_value(args, kwargs)
            top = base()
            if not enabled(kind) or not is_number(value) or value <= top:
                return orig(*args, **kwargs)
            top_value = call_with(orig, args, kwargs, key_name, top)
            if not is_number(top_value):
                return top_value
            slope = measured(name, orig, args, kwargs, key_name, top_value) if how == "measure" else float(how)
            result = like(top_value, top_value + slope * (min(value, LIMIT) - top))
            note(name, value, top_value, result)
            return result
        return wrapper

    for _name, _how, _kind in LINEAR:
        linear(_name, _how, _kind)

    @ctx.wrap("scripts.functions:get_talent_point_quest_clear", required=False, safe=True)
    def talent(orig, *args, **kwargs):
        value, key_name = first_value(args, kwargs)
        top = base()
        if not REWARDS or int(TALENT_EVERY) <= 0 or not is_number(value) or value <= top:
            return orig(*args, **kwargs)
        top_value = call_with(orig, args, kwargs, key_name, top)
        if not is_number(top_value):
            return top_value
        result = like(top_value, top_value + int((min(value, LIMIT) - top) // int(TALENT_EVERY)))
        note("get_talent_point_quest_clear", value, top_value, result)
        return result

    def inverse(name, price_name):
        @ctx.wrap("scripts.functions:" + name, required=False, safe=True)
        def wrapper(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            price, _key = first_value(args, kwargs)
            top = base()
            if not ITEMS or not is_number(price) or result != top:
                return result
            functions = sys.modules.get("scripts.functions")
            price_fn = getattr(functions, price_name, None) if functions else None
            if not callable(price_fn):
                return result
            top_price = price_fn(top)
            if not is_number(top_price) or price <= top_price:
                return result
            with lock:
                slope = slopes.get(price_name)
            if slope is None:
                lower = price_fn(top - SPAN)
                slope = (top_price - lower) / float(SPAN) if is_number(lower) else 0.0
                with lock:
                    slopes[price_name] = slope
            if slope <= 0:
                return result
            level = like(result, min(LIMIT, top + int((price - top_price) // slope)))
            note(name, price, top_price, level)
            return level
        return wrapper

    for _name, _price in INVERSE:
        inverse(_name, _price)

    ctx.log("beyond 76: rewards={} items={} talent every {}; up to {}; log goes to out/{}".format(
        bool(REWARDS), bool(ITEMS), TALENT_EVERY, LIMIT, LOG_BASENAME))
