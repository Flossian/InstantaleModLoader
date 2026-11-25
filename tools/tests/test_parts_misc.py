# -*- coding: utf-8 -*-
"""小さな共通部品（`npcs` / `items` / `equipment` / `saves` / `panels` / `llm`）。

MOD に写されていた読み方をローダへ寄せた関数。
ここで見るのは**寄せた先が、写しと同じ答えを返すか**と、docstring に書いた違いの所:

| 見るもの | なぜ |
|---|---|
| `npcs.is_dead` が `config` と modnpc の取っ手の属性の両方を見る | 写しの多くは `config` だけを見て取っ手の死を見落とした |
| `npcs.player_row` が `_player_row` と同じもの | 中と MOD の両方が前の名前で引いている |
| `npcs.life_log` / `life_log_text` が写しと同じ答え | `327_` は辞書、`336_` / `341_` は `str()` にした文を使う |
| `items.unwrap` が画面の部品から品を出す | 6か所に写されていた |
| `items.rarity_rank` が知らない語を 0 にする | `336_` / `350_` の段の番号 |
| `equipment.worn` が窓口の答えと本体の2枠を読む | `410_` / `411_` / `408_` に写されていた |
| `saves.read_world` が素の JSON と XOR の両方を読む | `read_save` と対 |
| `panels.Toggle` が1枚だけ持ち、もう一度押すと閉じる | 5本に写されていた |
| `llm.whole` が写しと同じ答え | `336_` / `337_` / `339_` / `341_` の均し |

ゲームは要らない。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

from instantale_modloader import equipment, items, llm, npcs, panels, saves  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    if ok:
        print("  ok   " + label)
    else:
        FAILURES.append(label)
        print("  FAIL " + label + ((" :: " + str(detail)) if detail else ""))


class Obj(object):
    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


# ------------------------------------------------------------ npcs
def test_is_dead():
    print("npcs.is_dead")
    check("config の印", npcs.is_dead(Obj(config={"is_dead": True})))
    check("config が偽なら生きている", not npcs.is_dead(Obj(config={"is_dead": False})))
    check("config が無ければ生きている", not npcs.is_dead(Obj()))
    check("modnpc の取っ手の属性", npcs.is_dead(Obj(is_dead=True, config={})))
    check("取っ手の属性が偽で config が真なら死んでいる",
          npcs.is_dead(Obj(is_dead=False, config={"is_dead": True})))
    check("素データの辞書は config を読む", npcs.is_dead({"config": {"is_dead": True}})
          and not npcs.is_dead({"config": {"is_dead": False}}) and not npcs.is_dead({}))
    check("None は生きている", npcs.is_dead(None) is False)

    class Raising(object):
        @property
        def is_dead(self):
            raise RuntimeError("boom")

        config = {"is_dead": False}

    check("属性の読み取りが投げても印とみなさない", npcs.is_dead(Raising()) is False)

    class Method(object):
        config = {}

        def is_dead(self):
            return True

    check("関数の属性は印とみなさない", npcs.is_dead(Method()) is False)

    # 写し（326_ の is_dead・336_ の cellmate）と、config だけの人物で同じ答え
    def copy_326(character):
        config = getattr(character, "config", None)
        return bool(config.get("is_dead")) if isinstance(config, dict) else False

    cases = [Obj(config={"is_dead": True}), Obj(config={"is_dead": 0}), Obj(config={}), Obj(config=None),
             Obj(config={"is_dead": "yes"}), Obj()]
    check("config だけの人物は 326_ の写しと同じ答え",
          all(npcs.is_dead(case) == copy_326(case) for case in cases))


def test_player_row():
    print("npcs.player_row")
    check("前の名前は同じ関数", npcs._player_row is npcs.player_row)
    row = {"affinity": 3}
    check("Character の属性を読む", npcs.player_row(Obj(relationship={"player": row})) is row)
    check("セーブの辞書を読む", npcs.player_row({"relationship": {"player": row}}) is row)
    check("欄が無ければ None", npcs.player_row(Obj(relationship={})) is None
          and npcs.player_row({}) is None and npcs.player_row(None) is None)
    check("欄が辞書でなければ None", npcs.player_row({"relationship": {"player": 5}}) is None)
    check("affinity_value の答えは変わらない",
          npcs.affinity_value({"relationship": {"player": {"affinity": 2.9}}}) == 2
          and npcs.affinity_value({"relationship": {"player": {"affinity": True}}}) is None)


def test_life_log():
    print("npcs.life_log / life_log_text")
    name = npcs.LIFE_LOG_MODULE
    saved = sys.modules.get(name)
    module = types.ModuleType(name)
    answer = {"value": None, "raise": False}

    def get_life_log_text(app, character):
        if answer["raise"]:
            raise ValueError("boom")
        return answer["value"]

    module.get_life_log_text = get_life_log_text

    # 写し（336_ common.py / 341_ shopkeeper_attitude.py の life_log_text、327_ の life_log_dict）
    def copy_text(app, character, limit):
        build = getattr(sys.modules.get(name), "get_life_log_text", None)
        try:
            value = build(app, character) if callable(build) else None
        except Exception:
            value = None
        if not value:
            return ""
        text = str(value)
        return text if limit is None or len(text) <= limit else text[:limit] + "…"

    def copy_dict(app, character):
        build = getattr(sys.modules.get(name), "get_life_log_text", None)
        try:
            value = build(app, character) if callable(build) else None
        except Exception:
            value = None
        return value if isinstance(value, dict) else {}

    try:
        sys.modules.pop(name, None)
        check("モジュールが無ければ空", npcs.life_log(None, None) == {} and npcs.life_log_text(None, None) == "")
        sys.modules[name] = module
        log = {"460日前-416日前": "旅に出た", "10日前-1日前": "街に着いた"}
        same = True
        for value in (log, {}, None, "文の記録", "x" * 50, 0):
            answer["value"] = value
            for limit in (None, 10, 1000):
                same = same and npcs.life_log_text("app", "who", limit) == copy_text("app", "who", limit)
            same = same and npcs.life_log("app", "who") == copy_dict("app", "who")
        check("写しと同じ答え（辞書・空・文・長い文・切り詰め）", same)
        answer["value"] = log
        check("辞書をそのまま返す", npcs.life_log(None, None) == log)
        check("文は Python の辞書の表記", npcs.life_log_text(None, None) == str(log))
        answer["value"] = "x" * 50
        check("limit 字で切って … を付ける", npcs.life_log_text(None, None, limit=10) == "x" * 10 + "…")
        answer["raise"] = True
        check("投げたら空", npcs.life_log(None, None) == {} and npcs.life_log_text(None, None) == "")
    finally:
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


# ------------------------------------------------------------ items
def test_unwrap():
    print("items.unwrap")
    item = Obj(name="剣")
    widget = Obj(item_instance=item)
    check("部品から品を出す", items.unwrap(widget) is item)
    check("部品でなければそのまま", items.unwrap(item) is item)
    check("辞書・None はそのまま", items.unwrap({"name": "x"}) == {"name": "x"} and items.unwrap(None) is None)
    check("item_instance が None ならそのまま", items.unwrap(Obj(item_instance=None)).item_instance is None)

    class Raising(object):
        @property
        def item_instance(self):
            raise RuntimeError("boom")

    raising = Raising()
    check("読み取りが投げたらそのまま（番人の文字列を返さない）", items.unwrap(raising) is raising)


def test_rarity():
    print("items.RARITIES / rarity_rank")
    check("6段の並び", items.RARITIES == ("common", "rare", "magical", "epic", "legendary", "mythic"))
    check("文字列の段", [items.rarity_rank(name) for name in items.RARITIES] == [0, 1, 2, 3, 4, 5])
    check("知らない語・None・数は 0", items.rarity_rank("ultra") == 0 and items.rarity_rank(None) == 0
          and items.rarity_rank(3) == 0)
    check("大文字は均さない（写しと同じ）", items.rarity_rank("Epic") == 0)
    check("セーブの辞書", items.rarity_rank({"rarity": "legendary"}) == 4 and items.rarity_rank({}) == 0)
    check("品と画面の部品", items.rarity_rank(Obj(rarity="rare")) == 1
          and items.rarity_rank(Obj(item_instance=Obj(rarity="mythic"))) == 5)

    # 写し（336_ rules.py の rarity_rank）とセーブの辞書で同じ答え
    def copy_336(record):
        rarity = record.get("rarity") if isinstance(record, dict) else None
        ranks = ("common", "rare", "magical", "epic", "legendary", "mythic")
        return ranks.index(rarity) if rarity in ranks else 0

    records = [{"rarity": name} for name in items.RARITIES] + [{"rarity": "x"}, {}, {"rarity": None}, None]
    check("336_ の写しと同じ答え", all(items.rarity_rank(r) == copy_336(r) for r in records))


# ------------------------------------------------------------ equipment
def test_worn():
    print("equipment.worn")
    saved = getattr(sys, equipment._ATTR, None)
    saved_loans = getattr(sys, equipment._LOANS_ATTR, None)
    setattr(sys, equipment._ATTR, {})
    setattr(sys, equipment._LOANS_ATTR, [])
    try:
        sword, coat, ring = Obj(name="剣"), Obj(name="外套"), Obj(name="指輪")
        holder = Obj(equipments={"weapon": sword, "wearable": "7"},
                     inventory=Obj(inventory={"7": coat}))
        check("窓口が無ければ本体の2枠（部位は空、id は持ち物から）",
              equipment.worn(None, holder) == [("", sword), ("", coat)])
        check("引けない id・真偽値・空の枠は数えない",
              equipment.worn(None, Obj(equipments={"weapon": "99", "wearable": True},
                                       inventory=Obj(inventory={}))) == [])
        check("同じ品は1度だけ",
              equipment.worn(None, Obj(equipments={"weapon": sword, "wearable": sword})) == [("", sword)])
        check("人物が無い・equipments が無ければ空", equipment.worn(None, None) == []
              and equipment.worn(None, Obj()) == [])

        # 写し（410_ / 411_ の worn_items）と、本体の2枠で同じ品の並び
        def copy_410(app, who):
            found = []
            equipments = getattr(who, "equipments", None)
            if isinstance(equipments, dict):
                inventory = items.inventory_of(who) or {}
                for key in ("weapon", "wearable"):
                    ref = equipments.get(key)
                    if isinstance(ref, (str, int)) and not isinstance(ref, bool):
                        ref = inventory.get(str(ref))
                    if ref is not None:
                        found.append(ref)
            unique, seen = [], set()
            for item in found:
                if id(item) not in seen:
                    seen.add(id(item))
                    unique.append(item)
            return unique

        holders = [holder, Obj(equipments={"weapon": "7"}, inventory=Obj(inventory={"7": coat})),
                   Obj(equipments={}), Obj(equipments={"weapon": sword, "wearable": sword})]
        check("410_ / 411_ の写しと同じ品（窓口なし）",
              all([item for _r, item in equipment.worn(None, h)] == copy_410(None, h) for h in holders))

        equipment.declare(equipment.GEAR, lambda app, who: [("right_hand", sword), ("free_1", ring),
                                                            ("body", coat), ("left_hand", sword)],
                          owner="test")
        check("窓口の答えを部位ごと使う",
              equipment.worn(None, holder) == [("right_hand", sword), ("free_1", ring), ("body", coat)])
        check("free=False は自由枠を外す",
              equipment.worn(None, holder, free=False) == [("right_hand", sword), ("body", coat)])
        equipment.declare(equipment.GEAR, lambda app, who: None, owner="test")
        check("窓口が None なら本体の2枠", equipment.worn(None, holder) == [("", sword), ("", coat)])
        equipment.declare(equipment.GEAR, lambda app, who: [], owner="test")
        check("窓口が空なら空（本体へ戻らない）", equipment.worn(None, holder) == [])
    finally:
        setattr(sys, equipment._ATTR, saved if saved is not None else {})
        setattr(sys, equipment._LOANS_ATTR, saved_loans if saved_loans is not None else [])


# ------------------------------------------------------------ saves
def test_read_world(root):
    print("saves.read_world")
    base = os.path.join(root, "data")
    check("パスは worlds の下", saves.world_data_path("w1", base)
          == os.path.join(base, "worlds", "w1", "world_data.json"))
    check("無ければ None", saves.read_world("none", base) is None)
    data = {"world_name": "テスト", "areas": {"1": {"size": "small"}}}
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    for folder, payload in (("plain", raw), ("xor", saves.xor(raw)), ("broken", b"\x00\x01")):
        os.makedirs(os.path.join(base, "worlds", folder))
        with io.open(saves.world_data_path(folder, base), "wb") as fh:
            fh.write(payload)
    check("素の JSON を読む", saves.read_world("plain", base) == data)
    check("XOR を読む", saves.read_world("xor", base) == data)
    check("読めなければ None", saves.read_world("broken", base) is None)
    check("セーブ（saves の下）とは別の木", saves.read_save("plain", base) is None)


# ------------------------------------------------------------ panels
class FakeView(object):
    def __init__(self, name, on_dismiss=None, fail=False):
        self.name = name
        self.on_dismiss = on_dismiss
        self.fail = fail
        self.dismissed = 0

    def dismiss(self):
        self.dismissed += 1
        if self.fail:
            raise RuntimeError("cannot dismiss")
        if self.on_dismiss is not None:
            self.on_dismiss()


def test_toggle():
    print("panels.Toggle")
    store = {"view": None}
    logged = []
    toggle = panels.Toggle(store, log_exc=logged.append)
    made = []

    def opener(app, on_dismiss):
        view = FakeView("v{}".format(len(made)), on_dismiss)
        made.append(view)
        return view

    first = toggle.toggle("app", opener)
    check("押すと開いて置く", first is made[0] and store["view"] is first and toggle.current() is first)
    check("もう一度押すと閉じる", toggle.toggle("app", opener) is None and store["view"] is None
          and first.dismissed == 1 and len(made) == 1)
    check("app が None なら開かない", toggle.toggle(None, opener) is None and store["view"] is None
          and len(made) == 1)
    second = toggle.toggle("app", opener)
    second.dismiss()
    check("画面が閉じたら空になる", store["view"] is None)
    third = toggle.open("app", opener)
    fourth = toggle.open("app", opener)
    check("開き直すと前の画面を閉じる", third.dismissed == 1 and store["view"] is fourth)
    third.on_dismiss()
    check("前の画面の通知が遅れて来ても新しい画面を手放さない", store["view"] is fourth)
    check("close は閉じる画面があったら True", toggle.close() is True and toggle.close() is False)

    def broken(app, on_dismiss):
        raise RuntimeError("cannot open")

    check("開けなければ None で空に戻す", toggle.toggle("app", broken) is None and store["view"] is None
          and logged[-1] == "could not open the window")
    store["view"] = FakeView("stuck", fail=True)
    check("閉じられなくても空にして記録する", toggle.toggle("app", opener) is None and store["view"] is None
          and logged[-1] == "could not close the window")

    def instant(app, on_dismiss):
        view = FakeView("instant", on_dismiss)
        on_dismiss()
        return view

    check("開く途中で閉じた画面は置かない", toggle.open("app", instant) is None and store["view"] is None)

    other = {"window": None}
    press = panels.Toggle(other, key="window", log_exc=logged.append).on_press(opener)
    press("app")
    opened = other["window"]
    press("app")
    check("on_press は押すたびに開け閉めする（key も効く）", opened is made[-1] and other["window"] is None
          and opened.dismissed == 1)


# ------------------------------------------------------------ llm
def test_whole():
    print("llm.whole")

    # 写し（336_ の clamp_scale / 337_ の clamp_step / 339_ の to_int）
    def copy_336(value, low, high):
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            value = value.strip().lstrip("+")
        try:
            number = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return None
        return max(low, min(high, number))

    def copy_339(value, low, high):
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            value = value.strip().lstrip("+")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return max(low, min(high, int(number)))

    samples = [0, 1, -1, 3, 9, -9, 2.7, -2.7, "2", "+2", " 3 ", "-4", "2.5", "abc", "", None, True, False,
               float("nan"), float("inf"), float("-inf"), "inf", "nan", [1], {"a": 1}, 10 ** 400, "++1",
               "1e2"]
    check("336_ / 337_ の写しと同じ答え", all(llm.whole(v, -3, 3) == copy_336(v, -3, 3) for v in samples),
          [(v, llm.whole(v, -3, 3), copy_336(v, -3, 3)) for v in samples
           if llm.whole(v, -3, 3) != copy_336(v, -3, 3)])
    check("339_ の写しと同じ答え", all(llm.whole(v, 0, 4) == copy_339(v, 0, 4) for v in samples))
    check("範囲なしは止めない", llm.whole("120") == 120 and llm.whole(-50.9) == -50)
    check("片側だけ止める", llm.whole(-5, low=0) == 0 and llm.whole(50, high=10) == 10
          and llm.whole(50, low=0) == 50)

    # 写し（341_ の clamp_stance。FAVOR/NEUTRAL/AGAINST = 2/1/0）
    def copy_341(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if isinstance(value, float) and float(value).is_integer():
            value = int(value)
        return value if value in (2, 1, 0) else None

    stances = [0, 1, 2, 3, -1, 1.0, 1.5, "1", " 2 ", "5", True, None, "x"]
    check("341_ の写しと同じ答え（choices）",
          all(llm.whole(v, choices=(2, 1, 0)) == copy_341(v) for v in stances),
          [(v, llm.whole(v, choices=(2, 1, 0)), copy_341(v)) for v in stances
           if llm.whole(v, choices=(2, 1, 0)) != copy_341(v)])
    check("choices では整数でない小数を None に", llm.whole(1.5, choices=(1, 2)) is None
          and llm.whole("2.0", choices=(1, 2)) == 2)
    check("choices と範囲は範囲が先", llm.whole(9, 0, 2, choices=(0, 1, 2)) == 2)
    check("戻りは int", isinstance(llm.whole(2.0), int) and type(llm.whole("3.0")) is int)


def main():
    root = tempfile.mkdtemp(prefix="iml_parts_misc_")
    try:
        test_is_dead()
        test_player_row()
        test_life_log()
        test_unwrap()
        test_rarity()
        test_worn()
        test_read_world(root)
        test_toggle()
        test_whole()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if FAILURES:
        print("\n失敗: " + ", ".join(FAILURES))
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
