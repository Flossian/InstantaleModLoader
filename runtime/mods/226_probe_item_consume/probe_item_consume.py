# -*- coding: utf-8 -*-
"""計測: 回復アイテムを使ったとき、何が誰にどれだけ効くかを録る。ゲームは変えない。

##### 何を決めるための計測か

回復アイテムの効き方を種別ごとに作り直したい
（食料・飲み物はスタミナ、医薬品は HP、ポーションはバフ／デバフ解除、
薬草・きのこはスタミナと HP が生成時に決まった幅で増減）。
素のゲームでは `healing_item` の `attributes` に `回復` と `疲労負荷` が書かれ、
使うとスタミナ（`physical_integrity`。GAME.md §2.19）を `疲労負荷` ぶん払って
HP を `回復` ぶん戻す、と**読める**。
だが読めるだけで、次のどれも測っていない。

    読めていない   `ItemConsumeManager.consume_item(item_instance, usable)` の `usable` は
                   誰がどう決めているか（スタミナが `疲労負荷` に足りないと偽になるのか）
    読めていない   使ったときに実際に動く項目（`current_hp` だけか、`max_hp` も動くか、
                   `physical_integrity` が減った後に `update_max_physical_integrity` が
                   走るのか）と、その量が `attributes` の値そのままか
    読めていない   使えなかったときに何が起きるか（文が出るだけか、品が減るか）
    読めていない   `ItemPopupMenu.on_consume_item` と `on_use_item` の違い
                   （`healing_item` と `consumable` で経路が分かれるのか）
    読めていない   画面に足される文（`add_text`）の実文。差し替えるなら同じ口に出したい

この4つが分からないと、効き方を差し替える MOD は「本体を呼んだ後に戻して自分の効果を
乗せる」か「本体を呼ばずに全部自分で書く」かを選べない。
前者は本体が HP 以外も動かしていれば戻し漏れが出るし、後者は品を減らす・画面を塗る・
文を出すの3つを本体と同じ手順で書かなければならない。

##### 録るもの

| 何を録るか | 見どころ |
| --- | --- |
| 右クリック（`ItemPopupMenu`）の押下 | `on_consume_item` / `on_use_item` のどちらが押されたか、popup が持っている項目（`usable` 相当の値がここに在るはず） |
| `consume_item` / `use_item` の前後 | プレイヤー（と品の持ち主）の HP・スタミナ・上限・`exhausted`・`status`・持ち物の数の差分。`usable` の実値。戻り値 |
| 呼び出しの間に足された文 | `add_text` の実文（回復の報告、使えなかったときの文） |
| `Item.consume` / `Item.use` | 誰が呼んでいるか（popup から直か、manager 経由か） |
| 純関数 | `get_heal_spec` / `get_heal_physical_integrity_barden` / `get_max_physical_integrity` の対応表 |
| 上限の更新 | `update_max_physical_integrity` / `update_max_hp` の前後（スタミナが最大 HP を削る式を出す材料） |

200番台の約束どおり読み取りだけ。
`safe=True` と握り潰しで、記録に失敗しても本体は必ず1回呼ぶ。

出力は `out/item_consume.log`（読む用）と `out/item_consume.jsonl`（1件1行）。
純関数の対応表は同じ引数の組を1度しか書かない（`221_` と同じ形）。
"""

import datetime
import json

from instantale_modloader import frames, ui

LOG_BASENAME = "item_consume.log"
RECORD_BASENAME = "item_consume.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
CONSUME_SAMPLES = 100
TABLE_SAMPLES = 120

# キャラクタの、使用の前後で比べる項目。
# HP とスタミナの周りは全部。`status` はバフ／デバフの器（GAME.md §2.10.2）。
WATCH_ATTRS = ("current_hp", "max_hp", "original_max_hp",
               "physical_integrity", "max_physical_integrity",
               "original_max_physical_integrity", "exhausted",
               "experience_level", "status", "ability_scores", "gold")

# 使用の外で走った上限の更新（レベルアップ・日送り）を録る件数。
# 式を読むのに要るのは数件で、日送りのたびに書くと埋もれる。
UPDATE_SAMPLES = 40

# 純関数。同じ引数の組は1度だけ。
PURE_TARGETS = (
    "get_heal_spec",
    "get_heal_physical_integrity_barden",
    "get_max_physical_integrity",
)

# popup の項目のうち、値ごと写すもの（あとは鍵だけ）。
# `usable` の正体はここに在るはずだが、名前を決め打ちしないで全部の鍵を出す。
POPUP_INLINE_TYPES = (bool, int, float, str, type(None))


def item_brief(item, limit=40):
    """品物1つを数で写す。説明文と画像は要らない。"""
    if item is None:
        return None
    # `InventoryItem`（画面の部品）が来たら中の `item_instance` を見る。
    inner = frames.attr(item, "item_instance", None)
    if inner is not None:
        item = inner
    if isinstance(item, dict):
        get = item.get
    else:
        def get(name, default=None):
            return frames.attr(item, name, default)
    attributes = get("attributes", None)
    obtainer = get("obtainer", None)
    return {
        "id": get("id", None),
        "name": frames.short(get("name", ""), limit),
        "item_type": get("item_type", None),
        "value": get("value", None),
        "rarity": get("rarity", None),
        "attributes": attributes if isinstance(attributes, dict) else None,
        "obtainer": frames.short(frames.attr(obtainer, "name", None), limit)
        if obtainer is not None else None,
    }


def inventory_size(character):
    inventory = frames.attr(character, "inventory", None)
    if isinstance(inventory, (dict, list, tuple)):
        return len(inventory)
    return None


def snapshot(character):
    """比較のために状態だけ写す。読むだけで書かない。"""
    if character is None:
        return None
    out = {}
    for name in WATCH_ATTRS:
        value = frames.attr(character, name)
        if value is frames.MISSING:
            continue
        if isinstance(value, (bool, int, float, str)) or value is None:
            out[name] = value
        elif isinstance(value, dict):
            try:
                json.dumps(value, ensure_ascii=False)
                out[name] = value if len(value) <= 12 else sorted(value)[:12]
            except (TypeError, ValueError):
                out[name] = frames.repr_value(value)
        else:
            out[name] = frames.repr_value(value)
    out["inventory"] = inventory_size(character)
    return out


def diff(before, after):
    """前後で違う項目だけ。`{名前: [前, 後]}`。"""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    changed = {}
    for key in sorted(set(before) | set(after)):
        old = before.get(key, "<absent>")
        new = after.get(key, "<absent>")
        if old != new:
            changed[key] = [old, new]
    return changed


def apply(ctx):
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    # 呼び出しの入れ子。`consume_item` の中で足された文だけを拾うための印。
    # 記録の件数と、純関数の対応表もここに持つ。
    seen = {"consume": 0, "table": {}, "stack": [], "texts": [], "updates": 0}

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("item consume probe: record failed")

    def brief(value):
        """引数を短く写す。オブジェクトは id と名前だけ。"""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return frames.short(value, 80)
        if isinstance(value, (list, tuple)):
            return [brief(item) for item in list(value)[:8]]
        if isinstance(value, dict):
            return {str(key): brief(value[key]) for key in list(value)[:12]}
        return frames.short(frames.describe_instance(value), 80)

    def take_slot():
        if CONSUME_SAMPLES <= 0 or seen["consume"] >= CONSUME_SAMPLES:
            return False
        seen["consume"] += 1
        return True

    def app_of(self):
        return frames.attr(self, "app", None) or ui.find_app()

    def player_of(app):
        return frames.attr(app, "player", None)

    def owner_of(item):
        inner = frames.attr(item, "item_instance", None)
        if inner is not None:
            item = inner
        owner = frames.attr(item, "obtainer", None)
        return owner if owner is not None else None

    # ------------------------------------------------------------------
    # 使用の前後（manager）
    # ------------------------------------------------------------------
    def watch_call(label, self, item_instance, extra, call):
        """本体を1回呼び、前後の差分と、間に足された文を録る。

        `call` は本体を呼ぶ関数（引数は閉じ込めてある）。
        記録の失敗は本体の結果を変えない。
        """
        app = None
        player = owner = None
        before_player = before_owner = None
        try:
            app = app_of(self)
            player = player_of(app)
            owner = owner_of(item_instance)
            if owner is player:
                owner = None
            before_player = snapshot(player)
            before_owner = snapshot(owner)
        except Exception:
            ctx.log_exc("item consume probe: cannot read the state before")

        seen["stack"].append(label)
        texts_from = len(seen["texts"])
        try:
            result = call()
        finally:
            seen["stack"].pop()
        try:
            if take_slot():
                texts = seen["texts"][texts_from:]
                del seen["texts"][texts_from:]
                after_player = snapshot(player)
                after_owner = snapshot(owner)
                row = {"at": now(), "phase": label,
                       "item": item_brief(item_instance),
                       "result": brief(result),
                       "result_type": type(result).__name__,
                       "player_before": before_player,
                       "player_diff": diff(before_player, after_player),
                       "owner_before": before_owner,
                       "owner_diff": diff(before_owner, after_owner),
                       "texts": [frames.short(text, 200) for text in texts],
                       "caller": frames.caller()}
                row.update(extra)
                record(row)
                write("{}: {} {} -> {!r}".format(
                    label, row["item"], extra, brief(result)))
                write("    player: {}".format(row["player_diff"] or "<no change>"))
                if before_owner is not None:
                    write("    owner:  {}".format(row["owner_diff"] or "<no change>"))
                for text in row["texts"]:
                    write("    text:   {}".format(text))
                write("    caller: {}".format(row["caller"]))
        except Exception:
            ctx.log_exc("item consume probe: cannot record {}".format(label))
        return result

    @ctx.wrap("__main__:ItemConsumeManager.consume_item", required=False, safe=True)
    def consume_item(orig, self, item_instance=None, usable=None, *args, **kwargs):
        return watch_call("consume_item", self, item_instance,
                          {"usable": brief(usable)},
                          lambda: orig(self, item_instance, usable, *args, **kwargs))

    @ctx.wrap("__main__:ItemConsumeManager.execute", required=False, safe=True)
    def consume_execute(orig, self, item_instance=None, usable=None, *args, **kwargs):
        return watch_call("consume_execute", self, item_instance,
                          {"usable": brief(usable)},
                          lambda: orig(self, item_instance, usable, *args, **kwargs))

    @ctx.wrap("__main__:ItemUseManager.use_item", required=False, safe=True)
    def use_item(orig, self, item_instance=None, *args, **kwargs):
        return watch_call("use_item", self, item_instance, {},
                          lambda: orig(self, item_instance, *args, **kwargs))

    @ctx.wrap("__main__:ItemUseManager.execute", required=False, safe=True)
    def use_execute(orig, self, item_instance=None, *args, **kwargs):
        return watch_call("use_execute", self, item_instance, {},
                          lambda: orig(self, item_instance, *args, **kwargs))

    # ------------------------------------------------------------------
    # 呼び出しの間に足された文
    # ------------------------------------------------------------------
    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        try:
            if seen["stack"]:
                seen["texts"].append(context if isinstance(context, str)
                                     else frames.repr_value(context))
        except Exception:
            pass
        return orig(self, context, *args, **kwargs)

    # ------------------------------------------------------------------
    # 右クリックの popup（`usable` を決めているのはここのはず）
    # ------------------------------------------------------------------
    def popup_fields(popup):
        """popup の項目。数・真偽・文字列は値ごと、それ以外は型だけ。"""
        out = {}
        try:
            for key, value in sorted(vars(popup).items()):
                if str(key).startswith("_"):
                    continue
                if isinstance(value, POPUP_INLINE_TYPES):
                    out[key] = value if not isinstance(value, str) \
                        else frames.short(value, 60)
                else:
                    out[key] = "<{}>".format(type(value).__name__)
        except Exception:
            return None
        return out

    def watch_popup(label):
        @ctx.wrap("scripts.hud.new_hud:ItemPopupMenu.{}".format(label),
                  required=False, safe=True)
        def pressed(orig, self, instance=None, *args, **kwargs):
            try:
                if CONSUME_SAMPLES > 0 and seen["consume"] < CONSUME_SAMPLES:
                    row = {"at": now(), "phase": "popup/" + label,
                           "item": item_brief(frames.attr(self, "item", None)),
                           "popup": popup_fields(self)}
                    record(row)
                    write("popup/{}: {} popup={}".format(
                        label, row["item"], row["popup"]))
            except Exception:
                ctx.log_exc("item consume probe: cannot record the popup")
            return orig(self, instance, *args, **kwargs)
        return pressed

    for name in ("on_consume_item", "on_use_item"):
        watch_popup(name)

    # ------------------------------------------------------------------
    # 品物の側（誰が呼ぶか）
    # ------------------------------------------------------------------
    def watch_item(label):
        @ctx.wrap("scripts.items:Item.{}".format(label), required=False, safe=True)
        def item_side(orig, self, *args, **kwargs):
            try:
                if CONSUME_SAMPLES > 0 and seen["consume"] < CONSUME_SAMPLES:
                    write("Item.{}: {} caller={}".format(
                        label, item_brief(self), frames.caller()))
            except Exception:
                pass
            return orig(self, *args, **kwargs)
        return item_side

    for name in ("consume", "use"):
        watch_item(name)

    # ------------------------------------------------------------------
    # 純関数の対応表
    # ------------------------------------------------------------------
    def table(name, args, kwargs, result):
        if TABLE_SAMPLES <= 0 or len(seen["table"]) >= TABLE_SAMPLES:
            return
        shown = [brief(value) for value in args]
        shown_kwargs = {key: brief(value) for key, value in kwargs.items()}
        key = json.dumps([name, shown, shown_kwargs], ensure_ascii=False,
                         sort_keys=True, default=str)
        if key in seen["table"]:
            return
        seen["table"][key] = True
        record({"at": now(), "phase": "純関数", "func": name,
                "args": shown, "kwargs": shown_kwargs,
                "result": brief(result), "result_type": type(result).__name__})
        write("純関数: {}({}) -> {!r} [{}]".format(
            name, ", ".join(json.dumps(value, ensure_ascii=False, default=str)
                            for value in shown),
            brief(result), type(result).__name__))

    def watch_pure(name):
        @ctx.wrap("scripts.functions:{}".format(name), required=False, safe=True)
        def pure(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                table(name, args, kwargs, result)
            except Exception:
                pass
            return result
        return pure

    for name in PURE_TARGETS:
        watch_pure(name)

    # ------------------------------------------------------------------
    # 上限の更新（スタミナが最大 HP を削る式の材料）
    # ------------------------------------------------------------------
    def watch_update(label):
        @ctx.wrap("scripts.characters:Character.{}".format(label),
                  required=False, safe=True)
        def update(orig, self, *args, **kwargs):
            before = None
            try:
                before = snapshot(self)
            except Exception:
                pass
            result = orig(self, *args, **kwargs)
            try:
                # 使用の間は毎回。外（レベルアップ・日送り）は式が読める程度に数件だけ。
                inside = bool(seen["stack"])
                if not inside:
                    if seen["updates"] >= UPDATE_SAMPLES:
                        return result
                    seen["updates"] += 1
                if CONSUME_SAMPLES > 0 and seen["consume"] < CONSUME_SAMPLES:
                    changed = diff(before, snapshot(self))
                    write("Character.{}: {} {} {} caller={}".format(
                        label, frames.short(frames.attr(self, "name", ""), 30),
                        "(使用中)" if inside else "(使用外)",
                        changed or "<no change>", frames.caller()))
            except Exception:
                pass
            return result
        return update

    for name in ("update_max_physical_integrity", "update_max_hp"):
        watch_update(name)

    ctx.log("item consume probe: consume<={} table<={}; log goes to out/{} and out/{}"
            .format(CONSUME_SAMPLES, TABLE_SAMPLES, LOG_BASENAME, RECORD_BASENAME))
