# -*- coding: utf-8 -*-
"""ローダの `modfacility`（MOD が持つ施設）をゲーム抜きで通す。

    python tools/tests/test_modfacility.py

偽の `Facility` と街を差し込み、次を確認する。

  層     … 同じ持ち主は積み重ならず差し替わる（注入し直しても増えない）
  素形   … 8項目が実セーブの `location` と同じ並びで出る
  建てる … ノードの `facilities` に入り、繋ぎ先との接続が両側に張られる
  控える … 建てた・壊した・素データの写しが世界ごとの控えに残る
  戻す   … 読み直しで控えから建ち直り、接続も張り直る
  隠す   … 保存の間だけ立ち位置が入口へ移り、選択肢も一緒に置かれる
  掃除   … MOD の施設を指す選択肢が保存の間だけ外れ、後で戻る
  救済   … 街に無い施設を指すセーブが入口へ直る（そのままではロードで落ちる）
  出口   … 選択肢が1つも無い画面でも出る（無いと建物から出られない）
  道     … 繋ぎ先に立つと建物への道が出る。ゲームが既に出していれば足さない
  押下   … 印で横取りし、層のハンドラが呼ばれる
  背景   … 二度描かない
  関所   … 立っていない世代では建てない

背景: この形が守っているのは「セーブに残さない」こと。
実体は保存が舐めないので隠さなくてよく、代わりに**id が載る器**を引き上げる
（`player_data["location"]` に残った id は、その世界を二度と開けなくする）。
"""
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import modfacility, ui  # noqa: E402


class FakeFacility:
    """`__main__.Facility(app, parent_node, facility_data)` の代役。"""

    def __init__(self, app, parent_node, data):
        self.app = app
        self.parent_node = parent_node
        for field in modfacility.FACILITY_FIELDS:
            setattr(self, field, data[field])     # 8項目が揃っていないと KeyError
        self.characters = []


class PhaseSpec:
    """ボタンに載る spec の代役。"""

    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class MovePhaseManager:
    def __init__(self, app, node_id, facility_id, area_id):
        self.args = (node_id, facility_id, area_id)


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        pass


class FakeCtx:
    """控え（`state.WorldStore`）と `Screen` が要るものだけ。"""

    def __init__(self, root):
        self.state_dir = root
        self.logs = []

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        try:
            with io.open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return default

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return True

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.logs.append(("ERROR", msg))


def make_town():
    """入口（`0`）と宿（`2`）だけの街。入口はノードが名乗っている。"""
    entrance = types.SimpleNamespace(id="0", name="街の入口",
                                     facility_type="entrance", connections=["2"])
    inn = types.SimpleNamespace(id="2", name="宿屋",
                                facility_type="inn", connections=["0"])
    node = types.SimpleNamespace(id="10", entrance_facility="0",
                                 facilities={"0": entrance, "2": inn})
    area = types.SimpleNamespace(id="1", nodes={"10": node})
    world = types.SimpleNamespace(areas={"1": area})
    player = types.SimpleNamespace(location=entrance, current_area=area)
    app = types.SimpleNamespace(
        world=world, player=player, buttons=[], buttons_backup=[],
        function_correspond_to_input=None, saved=None,
        world_dict={"world_data": {"name": "検査の世界"}},
        save_data_dict={"world_data": {"name": "検査の世界"}})
    app.process_choice = lambda phase, text: app.__setattr__("started", (phase, text))
    app.started = None
    return app, world, area, node, entrance, inn


def game_buttons(*entries):
    """ゲームが組んだ移動のボタンの形。"""
    return [{"text": text,
             "spec": PhaseSpec("MovePhaseManager", ["10", fid, "1"])}
            for text, fid in entries]


def check(label, cond):
    print("  {} {}".format("ok  " if cond else "FAIL", label))
    return bool(cond)


def main():
    ok = True
    main_module = sys.modules["__main__"]
    for cls in (FakeFacility, PhaseSpec, MovePhaseManager,
                JustSetButtonToNormalPhase):
        setattr(main_module, "Facility" if cls is FakeFacility else cls.__name__, cls)
    modfacility.purge()
    # 関所はゲームの `save_game` に当てるもので、偽の環境には無い。
    modfacility.gate_is_live = lambda: True
    state_root = os.path.join(HERE, os.pardir, os.pardir, "out", "test", "state")
    shutil.rmtree(os.path.join(state_root, "modfacility"), ignore_errors=True)
    ctx = FakeCtx(state_root)
    modfacility.bind_store(ctx)
    screen = ui.Screen(ctx, lambda *a: None, tag="test", mark=modfacility.MARK)
    setattr(sys, modfacility.SCREEN_ATTR, screen)

    print("層: 同じ持ち主は積み重ならない")
    fid = modfacility.register("915_invest", key="inn1",
                               fields={"name": "灯火亭", "facility_type": "inn"})
    modfacility.register("915_invest", key="inn1",
                         fields={"name": "灯火亭", "facility_type": "inn",
                                 "tier": "basic"})
    modfacility.register("914_real_estate", facility_id=fid,
                         fields={"tier": "standard"})
    ok &= check("id は mod: の文字列", fid == "mod:915_invest:inn1")
    ok &= check("持ち主2人で層は2つ", len(modfacility.layers(fid)) == 2)
    ok &= check("後から積んだ層が勝つ",
                modfacility.fields_of(fid)["tier"] == "standard")
    ok &= check("持ち主が id から引ける",
                modfacility.owner_of_id(fid) == "915_invest")
    ok &= check("entries が持ち主で絞れる",
                modfacility.entries("914_real_estate") == [fid])

    print("素形: 実セーブの location と同じ8項目・同じ並び")
    data = modfacility.template(fid, modfacility.fields_of(fid), "0")
    ok &= check("項目と並びが FACILITY_FIELDS のまま",
                list(data) == list(modfacility.FACILITY_FIELDS))
    ok &= check("繋ぎ先が connections に入る", data["connections"] == ["0"])
    ok &= check("config は level_of_detail を持つ",
                data["config"].get("level_of_detail") == 0)

    print("建てる: ノードに入り、接続が両側に張られる")
    app, world, area, node, entrance, inn = make_town()
    facility = modfacility.spawn(app, fid, "1")
    ok &= check("実体が組めた", facility is not None)
    ok &= check("ノードの facilities に入る", node.facilities.get(fid) is facility)
    ok &= check("入口から建物へ繋がる", fid in entrance.connections)
    ok &= check("建物から入口へ繋がる", "0" in facility.connections)
    ok &= check("二度建てない", modfacility.spawn(app, fid, "1") is facility)
    ok &= check("控えに置き場所が残る",
                modfacility.get(app, fid).placed[:2] == ("1", "10"))

    print("控える: 素データの写しが控えに入る")
    facility.name = "灯火亭（改装）"
    modfacility.snapshot_all(app)
    entry = modfacility._persisted_entry(app, "915_invest", fid)
    ok &= check("写しが控えに在る", isinstance(entry, dict) and entry.get("spawned"))
    ok &= check("書き換えた名前が写る",
                (entry.get("snapshot") or {}).get("name") == "灯火亭（改装）")
    ok &= check("接続は写さない（建て直しで張り直す）",
                "connections" not in (entry.get("snapshot") or {}))

    print("戻す: 世界を読み直すと控えから建ち直る")
    modfacility.forget()
    ok &= check("実体を忘れる", modfacility.registry()[fid]["facility"] is None)
    ok &= check("層は残る", len(modfacility.layers(fid)) == 2)
    app2, world2, area2, node2, entrance2, _inn2 = make_town()
    modfacility.restore_world(app2, world=world2,
                              save_data_dict=app2.save_data_dict)
    rebuilt = node2.facilities.get(fid)
    ok &= check("建ち直る", rebuilt is not None)
    ok &= check("写しの名前で建つ", getattr(rebuilt, "name", "") == "灯火亭（改装）")
    ok &= check("接続も張り直る", fid in entrance2.connections)

    print("隠す: 保存の間だけ立ち位置が入口へ移る")
    app2.player.location = rebuilt
    app2.buttons = game_buttons(("宿屋", "2"))
    hidden = modfacility.hide(app2, screen=screen)
    ok &= check("立ち位置が入口へ移る",
                modfacility.facility_id_of(app2.player.location) == "0")
    ok &= check("入口の選択肢が一緒に置かれる",
                any(ui.spec_cls_name(e) == "MovePhaseManager"
                    for e in app2.buttons))
    ok &= check("MOD の施設への道は焼かない",
                all(str((ui.spec_args(e) or ["", ""])[1]) != fid
                    for e in app2.buttons))
    modfacility.restore(app2, hidden)
    ok &= check("保存の後に戻る", app2.player.location is rebuilt)

    print("中のまま: keep_inside を名乗った層は移さない")
    modfacility.register("keeper", facility_id=fid, keep_inside=True)
    app2.player.location = rebuilt
    hidden = modfacility.hide(app2, screen=screen)
    ok &= check("立ち位置はそのまま", app2.player.location is rebuilt)
    modfacility.restore(app2, hidden)
    asked = []
    modfacility.register("keeper", facility_id=fid,
                         keep_inside=lambda info: asked.append(info) or False)
    hidden = modfacility.hide(app2, screen=screen)
    ok &= check("関数で断れる",
                modfacility.facility_id_of(app2.player.location) == "0")
    ok &= check("どの建物か渡る", asked and asked[0].get("facility_id") == fid)
    modfacility.restore(app2, hidden)
    modfacility.unregister("keeper", app=app2)

    print("掃除: MOD の施設を指す選択肢は保存の間だけ外れる")
    app2.player.location = entrance2
    app2.buttons = game_buttons(("宿屋", "2"), ("灯火亭", fid))
    hidden = modfacility.hide(app2, screen=screen)
    ok &= check("MOD の施設を指すボタンが消える",
                all(str((ui.spec_args(e) or ["", ""])[1]) != fid
                    for e in app2.buttons))
    ok &= check("ゲームのボタンは残る", len(app2.buttons) == 1)
    modfacility.restore(app2, hidden)
    ok &= check("保存の後に戻る", len(app2.buttons) == 2)

    print("救済: 街に無い施設を指すセーブは入口へ直る")
    save_data = {"player_data": {"location": "mod:915_invest:gone",
                                 "current_area": "1"},
                 "game_variables": {"buttons": []}}
    ok &= check("直した", modfacility.repair_player_location(world2, save_data))
    ok &= check("入口を指す", save_data["player_data"]["location"] == "0")
    ok &= check("選択肢も置かれる（ロードは組み直さない）",
                len(save_data["game_variables"]["buttons"]) >= 1)
    stay = {"player_data": {"location": fid, "current_area": "1"},
            "game_variables": {"buttons": []}}
    ok &= check("建っている建物の中はそのまま",
                not modfacility.repair_player_location(world2, stay))

    print("出口: 選択肢が1つも無い画面でも出る")
    pressed = []
    modfacility.register("915_invest", facility_id=fid,
                         choices=[{"key": "stay", "label": "滞在する",
                                   "on": lambda info: pressed.append(info)}],
                         exit_label="外に出る")
    app2.player.location = rebuilt
    app2.buttons = []
    modfacility.maintain_buttons(app2)
    labels = [e.get("text") for e in app2.buttons]
    ok &= check("宣言した選択肢が出る", "滞在する" in labels)
    ok &= check("出口が出る", "外に出る" in labels)
    ok &= check("何度呼んでも増えない",
                (modfacility.maintain_buttons(app2),
                 len(app2.buttons))[1] == len(labels))
    ok &= check("spec は無害な既存クラス",
                all(ui.spec_cls_name(e) == ui.SAFE_CLS for e in app2.buttons))

    print("道: 繋ぎ先に立つと建物への道が出る")
    app2.player.location = entrance2
    app2.buttons = game_buttons(("宿屋", "2"))
    modfacility.maintain_buttons(app2)
    ok &= check("建物への道が足される",
                any(e.get("text") == "灯火亭（改装）" for e in app2.buttons))
    app2.buttons = game_buttons(("宿屋", "2"), ("灯火亭", fid))
    before = len(app2.buttons)
    modfacility.maintain_buttons(app2)
    ok &= check("ゲームが出していれば足さない", len(app2.buttons) == before)

    print("押下: 印で横取りして層のハンドラを呼ぶ")
    app2.player.location = rebuilt
    app2.buttons = []
    modfacility.maintain_buttons(app2)
    index = [i for i, e in enumerate(app2.buttons)
             if e.get("text") == "滞在する"][0]
    app2.pressed_button_index = index
    ok &= check("横取りした", modfacility.press(app2, index, screen=screen))
    ok &= check("ハンドラが呼ばれた", len(pressed) == 1)
    ok &= check("どの建物か渡る",
                pressed and pressed[0].get("facility_id") == fid)
    index = [i for i, e in enumerate(app2.buttons)
             if e.get("text") == "外に出る"][0]
    modfacility.press(app2, index, screen=screen)
    ok &= check("出口は移動を起こす",
                isinstance(getattr(app2, "started", (None,))[0], MovePhaseManager))
    ok &= check("印の無いボタンは素通し",
                not modfacility.press(app2, 0, screen=screen)
                if app2.buttons and not screen.mark_of(app2.buttons[0])
                else True)

    print("背景: 二度描かない")
    painted = []
    modfacility.register("915_invest", facility_id=fid,
                         on={"background": lambda info: painted.append(info) or True})
    modfacility._state()["painted"] = None
    modfacility.paint_background(app2, fid, "test")
    modfacility.paint_background(app2, fid, "test")
    ok &= check("同じ建物では1度だけ", len(painted) == 1)
    modfacility._state()["painted"] = None
    modfacility.paint_background(app2, fid, "test")
    ok &= check("場所が変われば描き直す", len(painted) == 2)

    print("飲む: 層のフックが投げても関所は止まらない")
    def boom(info):
        raise RuntimeError("boom")
    modfacility.register("boomer", facility_id=fid, on={"save": boom})
    logged = []
    modfacility.fire_all("save", app2, write=logged.append)
    ok &= check("例外が外へ出ない", True)
    ok &= check("飲んだことがログに残る",
                any("boomer" in line for line in logged))
    modfacility.unregister("boomer", app=app2)

    print("壊す: 街からもノードからも消える")
    ok &= check("壊せた", modfacility.despawn(app2, fid))
    ok &= check("ノードから消える", fid not in node2.facilities)
    ok &= check("入口の接続も外れる", fid not in entrance2.connections)
    entry = modfacility._persisted_entry(app2, "915_invest", fid)
    ok &= check("控えは建っていない印になる",
                isinstance(entry, dict) and not entry.get("spawned"))

    print("関所: 立っていない世代では建てない")
    modfacility.gate_is_live = lambda: False
    ok &= check("建てない", modfacility.spawn(app2, fid, "1") is None)
    modfacility.gate_is_live = lambda: True

    print("片付け: unregister で層も控えも消える")
    modfacility.unregister("915_invest", app=app2)
    modfacility.unregister("914_real_estate", app=app2)
    ok &= check("登録簿が空", modfacility.entries() == [])
    ok &= check("控えも消える",
                modfacility._persisted_entry(app2, "915_invest", fid) is None)

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
