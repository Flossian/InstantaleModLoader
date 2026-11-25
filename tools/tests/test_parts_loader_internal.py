# -*- coding: utf-8 -*-
"""ローダの中の写しを寄せた内部の部品（`_kinds` / `_layered` / `state._process_value` ほか）。

ここで見るのは**寄せた先が、写しと同じ答えを返すか**:

| 見るもの | なぜ |
|---|---|
| `_kinds.KindRegistry` が置き換えの前の持ち主を返し、持ち主が替わったときだけ書く | `combat` / `durations` / `equipment` に写されていた |
| `_kinds.OwnerRegistry` が置き直しても元の位置に残る | `battle_effects` の倒した時の行がこの順に従う |
| 各窓口の `source_of` / `forget` / `owners` が前と同じ答え | 公開 I/F は変えない |
| `abilities._number` / `battle_effects._num` が文字列の数を数にしない | `frames.to_number` とは答えが違う |
| `_layered.LayeredRegistry` の層・控え・場面の通知・関所の状態 | `modfacility` / `modnpc` に写されていた |
| `modfacility` と `modnpc` の `_LAST_WRITTEN` が別の辞書 | 両方が `snapshot` と `gate` の種類を使う |
| `modfacility.game_is_busy` が `ui.busy_flags` と同じ答え | 旗の表はローダの1か所 |
| `ui.fill_template` が `frames.safe_format` の後に通貨を直す | 答えを変えない |
| `state._process_value` が型の違う入れ物を作り直す | list・set の入れ物 |
| `config._flag` / `config._read_dict` / `saves._game_ai_setting` | 設定とゲームの config.json の読み方を1か所に |
| 小さな窓口が `window_put` で置き直した登録を後ろへ回す | 共有部品の表の並びの決まり |

ゲームは要らない。
"""

import codecs
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import (  # noqa: E402
    _kinds, _layered, abilities, battle_effects, combat, config, confinement, durations,
    equipment, frames, imagegen, items, llm, modfacility, modnpc, saves, state, ui)

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


TEST_ATTRS = []


def attr_name(tail):
    name = "_instantale_test_internal_" + tail
    TEST_ATTRS.append(name)
    if hasattr(sys, name):
        delattr(sys, name)
    return name


# ------------------------------------------------------------ _kinds
def test_kind_registry():
    print("_kinds.KindRegistry")
    reg = _kinds.KindRegistry(attr_name("kinds"), "tag")
    wrote = []
    first = reg.declare("attack", len, owner="a", write=wrote.append)
    check("最初の置き場は前の持ち主が無い", first is None and wrote == [], (first, wrote))
    again = reg.declare("attack", len, owner="a", write=wrote.append)
    check("同じ持ち主の置き直しは書かない", again == "a" and wrote == [], (again, wrote))
    other = reg.declare("attack", abs, owner="b", write=wrote.append)
    check("持ち主が替わったら前の持ち主を返して1行書く",
          other == "a" and wrote == ["tag: 'attack' is now decided by 'b' (was 'a')"], (other, wrote))
    check("source_of", reg.source_of("attack") == "b" and reg.source_of("none") == "")
    check("entry", reg.entry("attack") == ("b", abs) and reg.entry("none") is None)
    reg.declare("defense", len, owner="b")
    reg.declare("speed", len, owner="c")
    check("owners は集合", reg.owners() == {"b", "c"}, reg.owners())
    check("drop は外した種類を置いた順で返す", reg.drop("b") == ["attack", "defense"])
    check("drop の後に残る", reg.table() == {"speed": ("c", len)}, reg.table())
    check("置き場は sys の属性", getattr(sys, reg.attr) is reg.table())
    try:
        reg.declare("x", None)
        check("関数でなければ投げる", False)
    except TypeError:
        check("関数でなければ投げる", True)


def test_owner_registry():
    print("_kinds.OwnerRegistry")
    reg = _kinds.OwnerRegistry(attr_name("owners"), "label")
    reg.put("a", 1)
    reg.put("b", 2)
    reg.put("a", 3)
    check("置き直しても元の位置に残る", reg.owners() == ["a", "b"] and reg.table()["a"] == 3, reg.table())
    reg.put(None, 4)
    check("置くときの鍵は str(owner or '')", "" in reg.table())
    check("drop は [label]", reg.drop("a") == ["label"])
    check("無ければ []", reg.drop("a") == [])


def test_windows_keep_answers():
    print("combat / durations / equipment / abilities / battle_effects")
    owner = "zz_internal_test"
    try:
        combat.declare(combat.ATTACK, lambda app, holder: 3, owner=owner)
        durations.declare("zz_kind", lambda app: None, owner=owner)
        equipment.declare("zz_kind", lambda app, holder: None, owner=owner)
        abilities.declare(owner, lambda app, holder: None)
        battle_effects.declare(owner, lambda app, holder: None)
        check("combat.source_of", combat.source_of(combat.ATTACK) == owner)
        check("durations.source_of", durations.source_of("zz_kind") == owner)
        check("equipment.source_of", equipment.source_of("zz_kind") == owner)
        check("owners に並ぶ", owner in combat.owners() and owner in durations.owners()
              and owner in equipment.owners() and owner in abilities.owners()
              and owner in battle_effects.owners())
        check("登録簿は前と同じ sys の属性", getattr(sys, combat._ATTR) is combat._registry()
              and getattr(sys, durations._ATTR) is durations._registry()
              and getattr(sys, equipment._ATTR) is equipment._registry())
        gone = durations.forget(owner)
        check("durations.forget が片付けの全部を外す",
              set(gone) >= {"zz_kind", combat.ATTACK, "abilities", "battle_effects"}, gone)
        check("外した後は誰も決めない", combat.source_of(combat.ATTACK) == ""
              and owner not in abilities.owners() and owner not in battle_effects.owners())
    finally:
        durations.forget(owner)


def test_number_helpers():
    print("abilities._number / battle_effects._num")
    for fn in (abilities._number, battle_effects._num):
        check("{}: 数はそのまま".format(fn.__module__), fn(2.5) == 2.5 and fn(3) == 3)
        check("{}: 真偽値は None".format(fn.__module__), fn(True) is None)
        check("{}: 文字列の数は None".format(fn.__module__), fn("3") is None)
        check("{}: None は None".format(fn.__module__), fn(None) is None)


# ------------------------------------------------------------ _layered
class FakeStore(object):
    def __init__(self):
        self.saved = []

    def save(self, key, bucket):
        self.saved.append((key, json.loads(json.dumps(bucket))))
        return True


class FakeStores(object):
    def __init__(self, override_attr):
        self.override_attr = override_attr
        self.found = FakeStore()
        self.buckets = {}

    def store(self):
        return self.found

    def bucket(self, app):
        if app is None:
            return None, None
        return "k", self.buckets.setdefault("k", {})


def test_layered():
    print("_layered.LayeredRegistry")
    stores = FakeStores(attr_name("override"))
    reg = _layered.LayeredRegistry(
        "tag", attr_name("registry"), attr_name("installed"), stores,
        id_key="thing_id", obj_key="thing", gate_mark="__test_gate__",
        save_target="__main__:Nothing.save", off_text="off the test",
        new_record=lambda entity_id: {"id": entity_id, "layers": [], "extra": 1})
    record = reg.record("mod:a:1")
    check("record は new_record で作る", record == {"id": "mod:a:1", "layers": [], "extra": 1})
    check("record は同じ物を返す", reg.record("mod:a:1") is record)
    record["layers"].append({"owner": "a", "on": {"site": lambda info: info}})
    reg.record("mod:b:1")["layers"].append({"owner": "b", "on": {}})
    check("layers は写し", reg.layers("mod:a:1") == record["layers"]
          and reg.layers("mod:a:1") is not record["layers"])
    check("entries は並べた id", reg.entries() == ["mod:a:1", "mod:b:1"])
    check("entries(owner)", reg.entries("b") == ["mod:b:1"])
    check("layer_of", reg.layer_of("a", "mod:a:1") is record["layers"][0] and reg.layer_of("b", "mod:a:1") is None)

    wrote = []

    def broken(info):
        raise RuntimeError("boom")

    layers = [{"owner": "a", "on": {"site": lambda info: (info["thing_id"], info["thing"], info["owner"])}},
              {"owner": "b", "on": {"site": broken}},
              {"owner": "c", "on": {}}]
    saved_log_exc = _layered.log_exc
    _layered.log_exc = lambda *a, **k: None
    try:
        results = reg.fire("site", None, "mod:a:1", layers, "OBJ", write=wrote.append)
    finally:
        _layered.log_exc = saved_log_exc
    check("fire は層の順に呼び、info の鍵は引数で決める",
          results == [("a", ("mod:a:1", "OBJ", "a"))], results)
    check("fire はフックの例外を飲んで WARN を書く",
          wrote == ["WARN tag: site hook of b on mod:a:1 failed"], wrote)

    seen = []
    reg.fire_all(lambda site, app, entity_id, **kw: seen.append(entity_id) or [(entity_id, site)], "world", None)
    check("fire_all は登録簿の全部に渡す", seen == ["mod:a:1", "mod:b:1"], seen)

    check("changed は同じ中身なら偽", reg.changed("snapshot", ("a",)) and not reg.changed("snapshot", ("a",)))

    check("installed は立っていなければ None", reg.installed() is None)
    setattr(sys, reg.installed_attr, {"generation": "g"})
    check("installed は辞書を返す", reg.installed() == {"generation": "g"})
    check("gate_is_live は対象が引けなければ偽", reg.gate_is_live() is False)

    with reg.keyed("outer"):
        with reg.keyed("inner"):
            inner = getattr(sys, stores.override_attr, None)
        outer = getattr(sys, stores.override_attr, None)
        with reg.keyed(""):
            empty = getattr(sys, stores.override_attr, None)
    check("keyed は入れ子の外側の鍵を残す", (inner, outer, empty) == ("inner", "outer", "outer"),
          (inner, outer, empty))
    check("keyed は抜けると消す", not hasattr(sys, stores.override_attr))

    app = object()
    check("persist は持ち主が無ければ書かない", reg.persist(app, "", "mod:a:1", spawned=True) is False)
    check("persist は書く", reg.persist(app, "a", "mod:a:1", snapshot={"n": 1}, spawned=1, place=("1", "2")))
    check("控えの形", stores.buckets["k"] == {"a": {"mod:a:1": {"snapshot": {"n": 1}, "spawned": True,
                                                                "place": ["1", "2"]}}}, stores.buckets)
    check("persisted_entry", reg.persisted_entry(app, "a", "mod:a:1")["spawned"] is True)
    check("persist は app が無ければ書かない", reg.persist(None, "a", "mod:a:1", spawned=False) is False)

    calls = []
    record["layers"] = [{"owner": "a", "on": {}}]
    dropped = reg.unregister("a", None, app, wrote.append,
                             lambda app, target, write=None: calls.append(("despawn", target)),
                             lambda app, owner, target: calls.append(("drop", owner, target)))
    check("unregister は despawn の後に控えを消す",
          dropped == ["mod:a:1"] and calls == [("despawn", "mod:a:1"), ("drop", "a", "mod:a:1")], (dropped, calls))
    check("層の無くなった記録は捨てる", "mod:a:1" not in reg.registry())
    check("drop_persisted", reg.drop_persisted(app, "a", "mod:a:1") is True and stores.buckets["k"] == {})

    gone = []
    reg.purge(app, wrote.append, lambda app, target, write=None: gone.append(target))
    check("purge は全部を despawn して空にする", gone == ["mod:b:1"] and reg.registry() == {})
    check("purge の行", wrote[-1] == "tag: the registry is empty", wrote[-1])


def test_layered_modules():
    print("modfacility / modnpc の別名")
    check("_LAST_WRITTEN は別の辞書", modfacility._LAST_WRITTEN is not modnpc._LAST_WRITTEN)
    check("modfacility の公開名", modfacility.registry() is getattr(sys, modfacility.REGISTRY_ATTR))
    check("modnpc の公開名", modnpc.registry() is getattr(sys, modnpc.REGISTRY_ATTR))
    check("keyed は公開名で残る", callable(modfacility.keyed) and callable(modnpc.keyed))
    check("_character_class は items と同じ", modnpc._character_class is items.character_class)
    check("BATTLE_FLAGS は同じ並び", modfacility.BATTLE_FLAGS == ui.BATTLE_FLAGS)
    app = Obj(in_battle=True, in_conversation=True, in_shopping=True)
    check("game_is_busy は ui.busy_flags と同じ答え",
          modfacility.game_is_busy(app) == ui.busy_flags(app) == ["in_battle", "in_conversation"],
          modfacility.game_is_busy(app))
    check("game_is_busy は旗が無ければ空", modfacility.game_is_busy(Obj()) == [])


# ------------------------------------------------------------ ui / state / config / saves
def test_fill_template():
    print("ui.fill_template")
    check("知らない名前は残す", ui.fill_template("{a}と{typo}", a=1) == "1と{typo}")
    check("壊れた書式は素の文字列", ui.fill_template("{", a=1) == "{")
    check("safe_format と同じ埋め方", ui.fill_template("{x}", x="y") == frames.safe_format("{x}", x="y"))


def test_process_value():
    print("state._process_value")
    name = attr_name("value")
    made = state._process_value(name, list)
    check("無ければ作る", made == [] and getattr(sys, name) is made)
    made.append(1)
    check("在れば同じ物", state._process_value(name, list) is made)
    setattr(sys, name, {"wrong": True})
    check("型が違えば作り直す", state._process_value(name, set) == set())


def test_config_and_saves(root):
    print("config._flag / config._read_dict / saves._game_ai_setting")
    runtime = os.path.join(root, "runtime")
    os.makedirs(os.path.join(root, "settings"))
    os.makedirs(runtime)
    flags = config.flags_path(runtime)
    check("loader.json が無ければ既定", config._flag(runtime, "x") is True and config._flag(runtime, "x", False) is False)
    with open(flags, "w", encoding="utf-8") as fh:
        json.dump({"x": False, "y": "no"}, fh)
    check("真偽値ならその値", config._flag(runtime, "x") is False)
    check("真偽値でなければ既定", config._flag(runtime, "y") is True)
    with open(flags, "wb") as fh:
        fh.write(codecs.BOM_UTF8 + json.dumps({"x": True}).encode("utf-8"))
    check("BOM 付きも読む", config.load_flags(runtime) == {"x": True})
    with open(flags, "w", encoding="utf-8") as fh:
        json.dump([1, 2], fh)
    saved_log = config.log
    config.log = lambda *a, **k: None
    try:
        check("辞書でなければ {}", config.load_flags(runtime) == {})
    finally:
        config.log = saved_log

    game = os.path.join(root, "config.json")
    check("無ければ None", saves._game_ai_setting(game) is None)
    data = {"ai_setting": {"llm_inference": "local",
                           "cloud_model_setting": {"cloud_llm_provider": "P", "cloud_llm": "M"},
                           "local_model_setting": {"sd_backend": {"name": "comfy"}}}}
    with open(game, "wb") as fh:
        fh.write(codecs.BOM_UTF8 + json.dumps(data).encode("utf-8"))
    check("BOM 付きでも ai_setting を返す", saves._game_ai_setting(game) == data["ai_setting"])
    check("imagegen.backend", imagegen.backend(game) == "comfy")
    check("llm.game_choice", llm.game_choice(game) == {"inference": "local", "provider": "P", "model": "M"})
    with open(game, "w", encoding="utf-8") as fh:
        json.dump({"ai_setting": []}, fh)
    check("ai_setting が辞書でなければ None", saves._game_ai_setting(game) is None
          and imagegen.backend(game) is None and llm.game_choice(game) is None)


def test_window_put_order():
    print("小さな窓口の置き直しは後ろへ")
    saved = getattr(sys, confinement.STORE_ATTR, None)
    try:
        confinement.reset()
        ctx = Obj()
        confinement.declare("a", ctx, lambda app: "first")
        confinement.declare("b", ctx, lambda app: "second")
        check("先に置いた方が答える", confinement.why(None) == ("first", "a"))
        confinement.declare("a", ctx, lambda app: "again")
        check("置き直した登録は後ろへ回る", confinement.why(None) == ("second", "b"))
        confinement.declare("b", ctx, lambda app: 1 / 0)
        confinement.declare("a", ctx, lambda app: None)
        check("投げた登録は飛ばして errors に残す",
              confinement.why(None) == (None, None)
              and (confinement._store()["errors"] or [""])[-1].startswith("b: ZeroDivisionError"),
              confinement._store()["errors"])
    finally:
        setattr(sys, confinement.STORE_ATTR, saved)


def test_log_line():
    print("_log_line")
    saved = ml._state.get("log_path")
    ml._state["log_path"] = None
    try:
        ml._log_line("WARN test")
        ml._log_line("test")
        check("投げない", True)
        check("窓口の既定の書き先は同じ関数", equipment._write_default is ml._log_line)
    finally:
        ml._state["log_path"] = saved


def main():
    root = tempfile.mkdtemp(prefix="iml_internal_")
    try:
        test_kind_registry()
        test_owner_registry()
        test_windows_keep_answers()
        test_number_helpers()
        test_layered()
        test_layered_modules()
        test_fill_template()
        test_process_value()
        test_config_and_saves(root)
        test_window_put_order()
        test_log_line()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        for name in TEST_ATTRS:
            if hasattr(sys, name):
                delattr(sys, name)
    if FAILURES:
        print("\n失敗: " + ", ".join(FAILURES))
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
