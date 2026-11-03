# -*- coding: utf-8 -*-
r"""計測: 難易度と品の価値から数を作るゲームの関数が、76 を超えたときにどう振る舞うか。ゲームは変えない。

76 を超えた難易度と品の価値を、報酬と品の強さにも効かせる MOD の下調べ。
VERIFICATION.md §3.112 で、報酬・才能点・働いた報酬・品の値段・武器の強さが 76 の値で止まると分かった。
伸ばす式を決めるには、76 までの曲線の形（直線か、反るか、揺らぎの幅）が要る。

録るもの:

  * 曲線（`kind: curve`）: `scripts.functions` の関数を、包む前の素の関数（`patch.original_of`）で
    0〜`SWEEP_MAX` の全部の値について呼んだ戻り。乱数を引く関数は `REPEAT` 回呼び、最小・中央・最大を残す。
    中央の値が最後に変わった値（`plateau_from`）も出す。76 で止まるならここが 76 になる。
    例外は型と文を残す（`get_talent_point_by_quest_level` などは 75 以上で KeyError）
  * 定数（`kind: const`）: `*_VALUE_MIN` / `*_VALUE_MAX` と `CHARACTER_LEVEL_*`
  * 逆引き（`kind: inverse`）: 値段→段階の3本に、正引きの戻りの全部と、その最大より上を渡した戻り
  * 品の値段（`kind: item_price`）: 主人公の持ち物から種類ごとに1品を写し（`copy.copy`。元の品は触らない）、
    価値と能力値（武器・防具は `get_weapon_spec`、回復は `get_heal_spec` の中央の値）だけを差し替えて
    `get_item_base_price` / `get_randomized_item_price` に渡した戻り
  * 品の能力値の出どころ（`kind: link`）: 品を作る3つの入口（`generate_item_from_item_data` / `_from_dict` /
    `_from_ready_made_data`）の中で呼ばれた `get_weapon_spec` / `get_heal_spec` と、できた品の種類・細分・価値・能力値。
    防具の防御力が `get_weapon_spec` から来るか、セーブから読むときにも能力値を作り直すかを見る
  * 切り（`kind: clamp`）: `clamp_quest_difficulty_value` / `clamp_npc_difficulty_value` / `clamp_equipment_value` が
    値を実際に切った回の、引数・戻り・呼び出し元（呼び出し元と引数の組ごとに1回）

曲線と逆引きと品の値段は、セーブを読み込んだ後に1プロセス1回だけ流す（印は `sys` の `SWEPT_MARK`）。
流す前に `random` / numpy / `__main__`・`scripts.functions` が持つ `random.Random` の状態を控え、終わったら戻す
（`236_` の `rng_saved` と同じ形）。流している間は `sys` の `DRY_THREAD_MARK` に自分のスレッドの id を置く。

読む用は `out\value_curves.log`、数える用は `out\value_curves.jsonl`。

版2: 切りの記録から、MOD が呼んだ回を外した。版1の初回（10-31）は、起動時に別の MOD が
`get_npc_employ_price` を 77〜106 で確かめた回が40件の枠を全部埋めていた（`frames.caller` は MOD のフレームを
飛ばすので、ゲームの呼び出しに見えていた）。呼び出しの途中に MOD のファイルがあれば数えない（`225_` と同じ判定）。

版3: 4点直した。

- 出どころ（`link`）が空振りしていた。品を作る3つの入口の戻りは品ではなく（`generate_item_from_item_data` は
  tuple を返す。`410_` の記録）、版2の `link` の行は種類・価値・能力値が全部 null だった。
  `410_` と同じく `scripts.items:Item.__init__` を包み、入口の窓が開いている間に組まれた品を控えて、
  その品（と、戻りに `attributes` を持つ物があればそれ）の種類・細分・価値・能力値を書く。
  件数は品が取れた回だけ数え、上限は入口ごと（読み込みの `generate_item_from_dict` が枠を使い切って、
  店の品の入口が録れなくなるのを避ける）。戻りの形（要素ごとの型名と品の名前）は入口ごとに1プロセス1回 `link_result` に書く
- 切りの包みが、MOD からの呼び出しかの判定（最大40段）を件数の上限より先に見ていた。上限→数か→切ったか→MOD からか、の順にした
- 一括計測の間に置く `DRY_THREAD_MARK` を、外すときに置く前の値へ戻す（版2は消していた）
- 曲線の全表を毎プロセス書き直していた（`curve` 行で約100KB）。乱数を引く関数も曲線ごとに決まった種で回し
  （終わったら乱数の状態は戻す）、表の指紋を `out\value_curves.curves.json`（全表と指紋の控え。ログの世代送りの外）と比べる。
  前回と同じ曲線は `curve_same` の1行にまとめ、変わった曲線と初めての曲線だけ `curve` 行に全表を出す
"""

import copy
import datetime
import hashlib
import json
import random
import sys
import threading
import time
import zlib

from instantale_modloader import frames, loads, patch

LOG_BASENAME = "value_curves.log"
RECORD_BASENAME = "value_curves.jsonl"
#: 曲線の全表と指紋の控え（版3）。`.json` はログの世代送り（`*.log` / `*.jsonl`）に入らない。
CURVES_BASENAME = "value_curves.curves.json"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
#: 曲線を測る値の上限（0 からこの値まで）。
SWEEP_MAX = 100
#: 乱数を引く関数を1つの値で何回呼ぶか。
REPEAT = 5
#: 品の値段を測るか。
ITEM_SWEEP = True
#: 品の能力値の出どころを何件録るか（1プロセスあたり・入口ごと。品が取れた回だけ数える）。
LINK_SAMPLES = 60
#: 切りを何件録るか（1プロセスあたり。呼び出し元と引数の組ごとに1回）。
CLAMP_SAMPLES = 40

#: 1プロセス1回の印（注入し直しても流し直さない）。
SWEPT_MARK = "_instantale_probe_243_swept"
#: 件数の控え（1プロセスに1つ）。
COUNT_MARK = "_instantale_probe_243_counts"
#: 試し打ちの間だけ置くスレッドの印（`236_` と同じ名前。同じ関数を包む probe が記録を控えるため）。
DRY_THREAD_MARK = "_instantale_probe_dry_run_thread"
#: 乱数の控えを探すモジュール。
RNG_MODULES = ("__main__", "scripts.functions")

#: 引数が難易度か価値1つの関数。
SINGLE = (
    "get_quest_reward", "get_labor_reward", "get_training_experience_point",
    "get_talent_point_quest_clear", "get_talent_point_by_quest_level",
    "get_training_price_quest_clear", "get_equipment_price", "get_heal_item_price",
    "get_other_item_price", "get_weapon_spec", "get_heal_spec",
    "get_heal_physical_integrity_barden", "get_npc_attributes", "get_npc_exp_level",
    "get_npc_employ_price", "get_quest_appropriate_stat", "clamp_quest_difficulty_value",
    "clamp_npc_difficulty_value", "clamp_equipment_value", "clamp_quest_scaling_value",
    "clamp_character_level", "get_max_physical_integrity", "get_days_elapsed_experience_point",
)
#: 第1引数が敵の段の関数。
TIERED = ("get_enemy_exp_lvl", "get_enemy_attributes_base_point", "get_enemy_count_in_quest")
TIERS = ("normal", "miniboss", "boss")
#: `get_item_skill_usefulness(item_value, item_rarity)` の希少度。
RARITIES = ("common", "rare", "magical", "epic", "legendary", "mythic")
#: 正引きと逆引きの組。
INVERSES = (
    ("get_equipment_price", "get_equipment_level_from_price"),
    ("get_heal_item_price", "get_heal_item_level_from_price"),
    ("get_other_item_price", "get_other_item_level_from_price"),
    ("get_quest_appropriate_stat", "get_quest_difficulty_from_stat"),
)
CONSTANTS = (
    "EQUIPMENT_VALUE_MIN", "EQUIPMENT_VALUE_MAX", "NPC_DIFFICULTY_VALUE_MIN",
    "NPC_DIFFICULTY_VALUE_MAX", "QUEST_DIFFICULTY_VALUE_MIN", "QUEST_DIFFICULTY_VALUE_MAX",
    "QUEST_SCALING_VALUE_MIN", "QUEST_SCALING_VALUE_MAX", "CHARACTER_LEVEL_MIN",
    "CHARACTER_LEVEL_MAX",
)
#: 品の値段を測る種類と、差し替える能力値の鍵・その値を作る関数。
ITEM_TYPES = ("weapon", "wearable", "healing_item", "consumable", "utility", "material")
STAT_SOURCES = (("攻撃力", "get_weapon_spec"), ("防御力", "get_weapon_spec"),
                ("回復", "get_heal_spec"))
#: 品を作る入口。
ITEM_ENTRIES = ("generate_item_from_item_data", "generate_item_from_dict",
                "generate_item_from_ready_made_data")
#: 品が組まれる所（`410_` と同じ）。入口の窓の間に組まれた品を控える。
ITEM_INIT = "scripts.items:Item.__init__"
#: 1回の入口で控える品と、`link` の行に書く品の上限。
MADE_LIMIT = 20
ITEMS_PER_LINK = 5
#: 戻りの形に並べる要素の上限。
SHAPE_ELEMENTS = 10
CLAMPS = ("clamp_quest_difficulty_value", "clamp_npc_difficulty_value", "clamp_equipment_value")
TEXT_CHARS = 80


def plain(value, depth=0):
    """JSON に載る形へ縮める。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return frames.short(value, TEXT_CHARS)
    if depth >= 3:
        return frames.short(repr(value), TEXT_CHARS)
    if isinstance(value, dict):
        return {str(k): plain(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [plain(v, depth + 1) for v in list(value)[:20]]
    return frames.short(repr(value), TEXT_CHARS)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def rng_saved():
    """いまの乱数の状態を控える。`[(戻す関数, 状態)]`。"""
    saved = [(random.setstate, random.getstate())]
    numpy = sys.modules.get("numpy")
    legacy = getattr(getattr(numpy, "random", None), "get_state", None)
    if callable(legacy):
        try:
            saved.append((numpy.random.set_state, legacy()))
        except Exception:
            pass
    seen = {id(getattr(random, "_inst", None))}
    for name in RNG_MODULES:
        module = sys.modules.get(name)
        try:
            items = list(vars(module).items()) if module is not None else []
        except Exception:
            items = []
        for _key, value in items:
            if isinstance(value, random.Random) and id(value) not in seen:
                seen.add(id(value))
                saved.append((value.setstate, value.getstate()))
    return saved


def rng_restore(saved):
    for setter, state in saved:
        try:
            setter(state)
        except Exception:
            pass


def rng_seed(key):
    """曲線ごとに決まった種を置く（版3）。同じゲームなら毎プロセス同じ表になり、指紋で比べられる。

    `rng_saved` が控えた全部（`random`・numpy の古い口・`RNG_MODULES` の `random.Random`）に置く。
    状態は一括計測の後に `rng_restore` で戻るので、ゲームの乱数の列は変わらない。
    """
    seed = zlib.crc32(str(key).encode("utf-8")) & 0xFFFFFFFF
    random.seed(seed)
    numpy = sys.modules.get("numpy")
    legacy = getattr(getattr(numpy, "random", None), "seed", None)
    if callable(legacy):
        try:
            legacy(seed)
        except Exception:
            pass
    seen = {id(getattr(random, "_inst", None))}
    for name in RNG_MODULES:
        module = sys.modules.get(name)
        try:
            items = list(vars(module).items()) if module is not None else []
        except Exception:
            items = []
        for _key, value in items:
            if isinstance(value, random.Random) and id(value) not in seen:
                seen.add(id(value))
                value.seed(seed)


def digest_of(table):
    """曲線の表の指紋。"""
    text = json.dumps(table, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha1(text.encode("ascii")).hexdigest()


def counts():
    """1プロセスの件数の控え。`link` は入口ごとの件数、`result_types` は戻りの形を書いた入口。"""
    found = getattr(sys, COUNT_MARK, None)
    if not (isinstance(found, dict) and isinstance(found.get("link"), dict)
            and isinstance(found.get("clamp"), set)
            and isinstance(found.get("result_types"), set)):
        # 版2の控え（`link` が数）が残っていれば、切りの控えだけ引き継いで作り直す
        clamp = found.get("clamp") if isinstance(found, dict) else None
        found = {"link": {}, "clamp": clamp if isinstance(clamp, set) else set(),
                 "result_types": set()}
        setattr(sys, COUNT_MARK, found)
    return found


def item_of(value):
    """品らしい物（`attributes` が dict）ならその品。画面の部品なら中の `item_instance`。違えば None。"""
    if value is None or isinstance(value, (dict, str, bytes, int, float, bool)):
        return None
    inner = frames.attr(value, "item_instance", None)
    if inner is not None and isinstance(frames.attr(inner, "attributes", None), dict):
        return inner
    if isinstance(frames.attr(value, "attributes", None), dict):
        return value
    return None


def items_of(result, made):
    """入口の窓の間に組まれた品と、戻りの中の品。同じ物は1つ。"""
    found, ids = [], set()
    candidates = list(made)
    elements = list(result)[:SHAPE_ELEMENTS] if isinstance(result, (tuple, list)) else [result]
    candidates.extend(item_of(element) for element in elements)
    for item in candidates:
        if item is None or id(item) in ids:
            continue
        ids.add(id(item))
        found.append(item)
    return found


def result_shape(result):
    """戻りの形。tuple・list なら要素ごとの型名（品なら名前も）。中身の repr は出さない。"""
    def one(value):
        name = type(value).__name__
        item = item_of(value)
        if item is not None:
            return "{}({!r})".format(name, frames.short(str(frames.attr(item, "name", "?")), 30))
        return name
    if isinstance(result, (tuple, list)):
        elements = list(result)
        shape = {"type": type(result).__name__, "len": len(elements),
                 "elements": [one(e) for e in elements[:SHAPE_ELEMENTS]]}
    else:
        shape = {"type": one(result)}
    return shape


def call_many(func, args, repeat):
    """`func(*args)` を `repeat` 回呼ぶ。`{"result": …}` か `{"min","median","max"}` か `{"error": …}`。"""
    results = []
    for _ in range(max(1, repeat)):
        try:
            results.append(func(*args))
        except Exception as exc:
            return {"error": "{}: {}".format(type(exc).__name__, frames.short(str(exc), 80))}
    if all(repr(r) == repr(results[0]) for r in results):
        return {"result": plain(results[0])}
    if all(is_number(r) for r in results):
        ordered = sorted(results)
        return {"min": ordered[0], "median": ordered[len(ordered) // 2], "max": ordered[-1]}
    return {"results": [plain(r) for r in results]}


def center(cell):
    """1つの値の代表（中央か唯一の戻り）。数でなければ None。"""
    if "median" in cell:
        return cell["median"]
    value = cell.get("result")
    return value if is_number(value) else None


def plateau_from(table):
    """代表の値が最後に変わった引数。数の代表が2つ未満なら None。"""
    last, changed_at = None, None
    for arg, cell in table:
        value = center(cell) if "error" not in cell else ("error", cell["error"].split(":")[0])
        if value is None:
            continue
        if last is None or value != last:
            changed_at = arg
        last = value
    return changed_at


def first_error(table):
    for arg, cell in table:
        if "error" in cell:
            return arg, cell["error"]
    return None


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    started = time.monotonic()
    local = threading.local()        # 品を作る入口の中に居る間の窓

    def event(kind, line, **fields):
        row = {"at": datetime.datetime.now().isoformat(timespec="milliseconds"),
               "t": round(time.monotonic() - started, 3), "kind": kind}
        row.update(fields)
        record(row)
        write("{} {}".format(kind, line))

    def functions_module():
        return sys.modules.get("scripts.functions")

    def raw(name):
        """包む前の素の関数。無ければ None。"""
        module = functions_module()
        func = getattr(module, name, None) if module is not None else None
        if not callable(func):
            return None
        return patch.original_of(func)

    # ------------------------------------------------------------ 一括の計測
    def measure(key, func, make_args, top):
        rng_seed(key)       # 曲線ごとに決まった種（版3。乱数の状態は一括計測の後に戻す）
        return [(v, call_many(func, make_args(v), REPEAT)) for v in range(0, top + 1)]

    def sweep_curves(top):
        tables = {}
        for name in SINGLE:
            func = raw(name)
            if func is None:
                event("missing", name, fn=name)
                continue
            tables[name] = measure(name, func, lambda v: (v,), top)
        for name in TIERED:
            func = raw(name)
            if func is None:
                event("missing", name, fn=name)
                continue
            for tier in TIERS:
                key = "{}:{}".format(name, tier)
                tables[key] = measure(key, func, lambda v, tier=tier: (tier, v), top)
        func = raw("get_item_skill_usefulness")
        if func is not None:
            for rarity in RARITIES:
                key = "get_item_skill_usefulness:{}".format(rarity)
                tables[key] = measure(key, func, lambda v, rarity=rarity: (v, rarity), top)
        report_curves(tables)
        return tables

    def report_curves(tables):
        """前回の控えと指紋を比べ、変わった曲線と初めての曲線だけ全表を出す。同じ曲線は1行にまとめる。"""
        path = ctx.out_path(CURVES_BASENAME)
        previous = ctx.read_json(path, None)
        previous = previous if isinstance(previous, dict) else {}
        before = previous.get("curves") if isinstance(previous.get("curves"), dict) else {}
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        kept, same, changed = {}, [], []
        for key, table in tables.items():
            rows = [[v, cell] for v, cell in table]
            digest = digest_of(rows)
            plateau = plateau_from(table)
            error = first_error(table)
            old = before.get(key) if isinstance(before.get(key), dict) else None
            if old is not None and old.get("digest") == digest:
                same.append(key)
                kept[key] = old
                continue
            changed.append(key)
            kept[key] = {"digest": digest, "since": stamp, "plateau_from": plateau,
                         "first_error": list(error) if error else None, "table": rows}
            event("curve", "{} plateau_from={} first_error={}{} | {}".format(
                key, plateau, error,
                " (changed; was plateau_from={} first_error={} since {})".format(
                    old.get("plateau_from"), old.get("first_error"), old.get("since"))
                if old is not None else "",
                " ".join("{}:{}".format(v, compact(cell)) for v, cell in table)),
                fn=key, plateau_from=plateau, first_error=list(error) if error else None,
                table=rows, previous=({"plateau_from": old.get("plateau_from"),
                                       "first_error": old.get("first_error"),
                                       "since": old.get("since")} if old is not None else None))
        gone = sorted(key for key in before if key not in tables)
        if same:
            event("curve_same", "{} curve(s) unchanged since the last run (full tables in {}){}".format(
                len(same), CURVES_BASENAME, "; changed or new: {}".format(changed) if changed else ""),
                count=len(same), fns=same, changed=changed, file=CURVES_BASENAME)
        if changed or gone or not before:
            ctx.write_json(path, {"written": stamp, "sweep_max": int(SWEEP_MAX), "repeat": int(REPEAT),
                                  "curves": kept}, indent=None)

    def compact(cell):
        if "error" in cell:
            return "E(" + cell["error"].split(":")[0] + ")"
        if "median" in cell:
            return "{}~{}~{}".format(cell["min"], cell["median"], cell["max"])
        if "result" in cell:
            return frames.short(repr(cell["result"]), 40)
        return frames.short(repr(cell.get("results")), 60)

    def sweep_constants():
        module = functions_module()
        found = {name: plain(getattr(module, name, None)) for name in CONSTANTS}
        event("const", " ".join("{}={}".format(k, v) for k, v in found.items()), values=found)

    def sweep_inverses(tables):
        for forward, backward in INVERSES:
            func = raw(backward)
            table = tables.get(forward)
            if func is None or not table:
                continue
            outputs = sorted({center(cell) for _v, cell in table if center(cell) is not None})
            if not outputs:
                continue
            probes = outputs + [outputs[-1] + 1, outputs[-1] * 2]
            rows = [(p, call_many(func, (p,), 1)) for p in probes]
            event("inverse", "{} <- {} | {}".format(backward, forward, " ".join(
                "{}:{}".format(p, compact(cell)) for p, cell in rows)),
                fn=backward, forward=forward, table=[[p, cell] for p, cell in rows])

    def sweep_items(app, tables, top):
        player = getattr(app, "player", None)
        inventory = getattr(player, "inventory", None)
        if not isinstance(inventory, dict):
            inventory = getattr(inventory, "inventory", None)
        if not isinstance(inventory, dict):
            event("item_price", "no inventory", skipped="no inventory")
            return
        base, randomized = raw("get_item_base_price"), raw("get_randomized_item_price")
        if base is None and randomized is None:
            return
        picked = {}
        for item in list(inventory.values()):
            kind = getattr(item, "item_type", None)
            if kind in ITEM_TYPES and kind not in picked \
                    and isinstance(getattr(item, "attributes", None), dict):
                picked[kind] = item
        for kind in ITEM_TYPES:
            item = picked.get(kind)
            if item is None:
                event("item_price", "{} none in inventory".format(kind), item_type=kind,
                      skipped="none in inventory")
                continue
            attributes = dict(item.attributes)
            stat_key, stat_fn = next(((k, f) for k, f in STAT_SOURCES if k in attributes),
                                     (None, None))
            rows = []
            for v in range(1, top + 1):
                try:
                    clone = copy.copy(item)
                    attrs = dict(attributes)
                    stat = None
                    if stat_key is not None:
                        table = dict(tables.get(stat_fn) or ())
                        stat = center(table.get(v, {}))
                        if stat is not None:
                            attrs[stat_key] = stat
                    clone.attributes = attrs
                    clone.value = v
                except Exception as exc:
                    rows.append([v, {"error": "copy {}: {}".format(type(exc).__name__, exc)}])
                    break
                cell = {"stat": stat}
                if base is not None:
                    cell["base"] = call_many(base, (clone,), 1)
                if randomized is not None:
                    cell["randomized"] = call_many(randomized, (clone,), REPEAT)
                rows.append([v, cell])
            event("item_price", "{} {} rarity={} stat={} | {}".format(
                kind, (attributes.get("item_detail")), getattr(item, "rarity", None), stat_key,
                " ".join("{}:{}/{}".format(v, compact(c.get("base", {})),
                                           compact(c.get("randomized", {})))
                         for v, c in rows if isinstance(c, dict) and "base" in c)),
                item_type=kind, item_detail=attributes.get("item_detail"),
                rarity=plain(getattr(item, "rarity", None)), stat_key=stat_key, table=rows)

    def sweep(call):
        # 判定が先（1プロセス1回）。
        if getattr(sys, SWEPT_MARK, False):
            return
        setattr(sys, SWEPT_MARK, True)
        top = max(0, int(SWEEP_MAX))
        saved = rng_saved()
        # 試し打ちの印。中で包まれた関数（`get_item_base_price` の中の `get_equipment_price` など）を
        # 包む probe が、この呼び出しを記録から外すため。置く前の値を控えて戻す
        marked_before = getattr(sys, DRY_THREAD_MARK, None)
        setattr(sys, DRY_THREAD_MARK, threading.get_ident())
        begun = time.monotonic()
        try:
            sweep_constants()
            tables = sweep_curves(top)
            sweep_inverses(tables)
            if ITEM_SWEEP:
                sweep_items(getattr(call, "app", None), tables, top)
        except Exception:
            ctx.log_exc("value curves probe: sweep failed")
        finally:
            try:
                if marked_before is None:
                    delattr(sys, DRY_THREAD_MARK)
                else:
                    setattr(sys, DRY_THREAD_MARK, marked_before)
            except Exception:
                pass
            rng_restore(saved)
        write("swept 0..{} in {:.2f}s (random state restored)".format(
            top, time.monotonic() - begun))

    loads.on("243_probe_value_curves", ctx, (loads.LOAD,), after=sweep, write=write)

    # ------------------------------------------------------------ 品の能力値の出どころ
    def in_sweep():
        return getattr(sys, DRY_THREAD_MARK, None) == threading.get_ident()

    def watch_spec(name):
        def wrapper(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                window = getattr(local, "window", None)
                if window is not None and not in_sweep() and len(window) < 20:
                    window.append([name, plain(list(args)), plain(result)])
            except Exception:
                pass
            return result
        return wrapper

    for _name in ("get_weapon_spec", "get_heal_spec"):
        ctx.wrap("scripts.functions:" + _name, required=False, safe=True)(watch_spec(_name))

    # 入口の戻りは品とは限らない（`generate_item_from_item_data` は tuple を返す。`410_` の記録）。
    # 入口の窓の間に組まれた品を、組まれた瞬間に控える（`410_` の `item_init` と同じ所）
    @ctx.wrap(ITEM_INIT, required=False, safe=True)
    def item_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            made = getattr(local, "made", None)
            if made is not None and len(made) < MADE_LIMIT:
                made.append(self)
        except Exception:
            pass
        return result

    def brief(item):
        attributes = frames.attr(item, "attributes", None)
        attributes = attributes if isinstance(attributes, dict) else {}
        return {"name": plain(frames.attr(item, "name", None)),
                "item_type": plain(frames.attr(item, "item_type", None)),
                "item_detail": plain(attributes.get("item_detail")),
                "value": plain(frames.attr(item, "value", None)),
                "stats": {k: plain(attributes.get(k)) for k, _f in STAT_SOURCES if k in attributes}}

    def watch_entry(name):
        def wrapper(orig, self, *args, **kwargs):
            seen = counts()
            full = seen["link"].get(name, 0) >= LINK_SAMPLES
            typed = name in seen["result_types"]
            if full and typed:
                return orig(self, *args, **kwargs)
            outer_window, outer_made = getattr(local, "window", None), getattr(local, "made", None)
            local.window, local.made = [], []
            try:
                result = orig(self, *args, **kwargs)
            finally:
                window, made = local.window, local.made
                local.window, local.made = outer_window, outer_made
            try:
                items = items_of(result, made)
                if not typed:
                    # 戻りの形は入口ごとに1プロセス1回（tuple の中身の形を見る。中身の repr は出さない）
                    seen["result_types"].add(name)
                    shape = result_shape(result)
                    event("link_result", "{} returned {} (items built inside: {})".format(
                        name, shape, len(made)), entry=name, result=shape, built=len(made),
                        items=len(items))
                if not full and items:
                    seen["link"][name] = seen["link"].get(name, 0) + 1
                    rows = [brief(item) for item in items[:ITEMS_PER_LINK]]
                    first = rows[0]
                    event("link", "{} {} {} value={} stats={} items={} spec_calls={}".format(
                        name, first["item_type"], first["item_detail"], first["value"],
                        first["stats"], len(items), window),
                        entry=name, item_type=first["item_type"], item_detail=first["item_detail"],
                        value=first["value"], stats=first["stats"], items=rows,
                        spec_calls=window, caller=frames.caller(4))
            except Exception:
                ctx.log_exc("value curves probe: recording {} failed".format(name))
            return result
        return wrapper

    for _name in ITEM_ENTRIES:
        ctx.wrap("__main__:InstantaleApp." + _name, required=False, safe=True)(watch_entry(_name))

    # ------------------------------------------------------------ 切り
    def called_from_a_mod():
        """呼び出しの途中に、この probe とローダ以外の MOD のファイルがあれば True。"""
        for index in range(2, 40):
            try:
                frame = sys._getframe(index)
            except ValueError:
                return False
            name = frame.f_code.co_filename
            if not frames.is_ours(name):
                continue
            if name == __file__ or "instantale_modloader" in name.replace("/", "\\"):
                continue
            return True
        return False

    def watch_clamp(name):
        def wrapper(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                # 安い順に見る: 上限 → 数か → 切ったか → MOD からか（最大40段をなめる）
                seen = counts()["clamp"]
                if len(seen) >= CLAMP_SAMPLES:
                    return result
                if not args or not is_number(args[0]) or in_sweep():
                    return result
                if result == args[0]:
                    return result
                if called_from_a_mod():
                    return result
                caller = frames.caller(4)
                key = (name, caller.split(" <- ")[0], args[0])
                if key not in seen:
                    seen.add(key)
                    event("clamp", "{}({}) -> {} caller={}".format(
                        name, args[0], result, caller),
                        fn=name, arg=args[0], result=plain(result), caller=caller)
            except Exception:
                pass
            return result
        return wrapper

    for _name in CLAMPS:
        ctx.wrap("scripts.functions:" + _name, required=False, safe=True)(watch_clamp(_name))

    ctx.log("value curves probe: sweep 0..{} once per process after load, log={}".format(
        int(SWEEP_MAX), ctx.out_path(LOG_BASENAME)))
