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
                                     facility_type="entrance", connections=["2", "1"])
    inn = types.SimpleNamespace(id="2", name="宿屋",
                                facility_type="inn", connections=["0"])
    ward = types.SimpleNamespace(id="1", name="広場",
                                 facility_type="ward", connections=["0"])
    node = types.SimpleNamespace(id="10", entrance_facility="0",
                                 facilities={"0": entrance, "1": ward, "2": inn})
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


def make_two_node_town():
    """同じ形のノードを2つ持つ街（実機 2026-09-14 のカスティア）。

    ノードどうしは繋がっていない。入口の `connections` はそのノードの中で閉じる。
    プレイヤーはノード `20` の入口に立っている。
    """
    def one(node_id, entrance_id, ward_id, inn_id):
        entrance = types.SimpleNamespace(
            id=entrance_id, name="入口" + node_id, facility_type="entrance",
            connections=[ward_id, inn_id])
        ward = types.SimpleNamespace(id=ward_id, name="区画" + node_id,
                                     facility_type="ward", connections=[entrance_id])
        inn = types.SimpleNamespace(id=inn_id, name="宿" + node_id,
                                    facility_type="inn", connections=[entrance_id])
        node = types.SimpleNamespace(
            id=node_id, entrance_facility=entrance_id,
            facilities={entrance_id: entrance, ward_id: ward, inn_id: inn})
        return node, entrance

    first, first_entrance = one("10", "0", "1", "2")
    second, second_entrance = one("20", "50", "51", "52")
    area = types.SimpleNamespace(id="1", nodes={"10": first, "20": second})
    world = types.SimpleNamespace(areas={"1": area})
    player = types.SimpleNamespace(location=second_entrance, current_area=area)
    app = types.SimpleNamespace(
        world=world, player=player, buttons=[], buttons_backup=[],
        function_correspond_to_input=None, saved=None,
        world_dict={"world_data": {"name": "検査の世界"}},
        save_data_dict={"world_data": {"name": "検査の世界"}})
    app.process_choice = lambda phase, text: app.__setattr__("started", (phase, text))
    app.started = None
    return app, area, first, second, first_entrance, second_entrance


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
    modfacility.register("330_real_estate", facility_id=fid,
                         fields={"tier": "standard"})
    ok &= check("id は mod: の文字列", fid == "mod:915_invest:inn1")
    ok &= check("持ち主2人で層は2つ", len(modfacility.layers(fid)) == 2)
    ok &= check("後から積んだ層が勝つ",
                modfacility.fields_of(fid)["tier"] == "standard")
    ok &= check("持ち主が id から引ける",
                modfacility.owner_of_id(fid) == "915_invest")
    ok &= check("entries が持ち主で絞れる",
                modfacility.entries("330_real_estate") == [fid])

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

    print("繋ぎ先: hub=\"ward\" の層は区画へ、既定は入口へ")
    wid = modfacility.register("915_invest", key="shop1", fields={"name": "店"}, hub="ward")
    shop = modfacility.spawn(app, wid, "1")
    ok &= check("区画に繋がる", shop is not None and wid in node.facilities["1"].connections
                and wid not in entrance.connections)
    ok &= check("控えの繋ぎ先も区画", modfacility.get(app, wid).placed[2] == "1")
    ok &= check("既定の層は入口のまま", fid in entrance.connections)
    modfacility.unregister("915_invest", wid, app=app)
    ok &= check("壊すと区画の接続も外れる", wid not in node.facilities["1"].connections)
    ok &= check("区画の無い土地は入口に落ちる",
                modfacility.hub_for(types.SimpleNamespace(id="9", nodes={"1": types.SimpleNamespace(
                    id="1", entrance_facility="0", facilities={"0": types.SimpleNamespace(
                        id="0", facility_type="entrance", connections=[])})}), "ward", "x")[1] is not None)

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

    # 立ち位置を入口へ直したら、焼かれている絵も入口のものにする。
    # 空にして済ませると、ロードが前の画面の絵のまま始まる（別の場所の絵になる）。
    bg_root = os.path.join(state_root, "worlds", "検査の世界", "backgrounds")
    for name in ("街の入口", "灯火亭（改装）"):
        os.makedirs(os.path.join(bg_root, name), exist_ok=True)
        with io.open(os.path.join(bg_root, name, "image.png"), "w") as fh:
            fh.write("x")
    moved = {"player_data": {"location": "mod:915_invest:gone",
                             "current_area": "1"},
             "game_variables": {"buttons": [],
                                "location_image": os.path.join(
                                    bg_root, "灯火亭（改装）", "image.png")}}
    ok &= check("直した（絵つき）",
                modfacility.repair_player_location(world2, moved))
    ok &= check("焼かれている絵も入口のものになる",
                moved["game_variables"]["location_image"]
                == os.path.join(bg_root, "街の入口", "image.png"))
    os.remove(os.path.join(bg_root, "街の入口", "image.png"))
    moved["player_data"]["location"] = "mod:915_invest:gone"
    moved["game_variables"]["location_image"] = os.path.join(
        bg_root, "灯火亭（改装）", "image.png")
    ok &= check("入口の絵が無ければ空（建物の絵のままにはしない）",
                modfacility.repair_player_location(world2, moved)
                and moved["game_variables"]["location_image"] == "")

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

    print("下位の画面: 移動のボタンが無く空でもない画面には混ぜない")
    app2.player.location = rebuilt
    app2.buttons = [{"text": "犬小屋", "spec": PhaseSpec("VacationStartManager", [])},
                    {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
    modfacility.maintain_buttons(app2)
    ok &= check("部屋選びには足さない", len(app2.buttons) == 2)
    app2.buttons = game_buttons(("入口", "0"))
    modfacility.maintain_buttons(app2)
    ok &= check("施設の画面（移動あり）には足す", "滞在する" in [e.get("text") for e in app2.buttons])
    ok &= check("移動があるので出口は足さない", "外に出る" not in [e.get("text") for e in app2.buttons])
    # `inn` 型の建物でゲームが出す最初の画面。出口（MovePhaseManager）は作られない。
    app2.buttons = [{"text": "宿泊する(3ヵ月)", "spec": PhaseSpec("DisplayVacationChoice", [3])},
                    {"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])}]
    modfacility.maintain_buttons(app2)
    texts = [e.get("text") for e in app2.buttons]
    ok &= check("入口の種類だけの画面にも足す", "滞在する" in texts)
    ok &= check("ゲームの出口が無いので出口を足す", "外に出る" in texts)
    ok &= check("並びは ゲーム → こちら → 出口",
                texts.index("会話する") < texts.index("滞在する") < texts.index("外に出る"))

    print("残骸: 焼かれた文言が今と違っても、括弧の前までで掃除する")
    modfacility.register("915_invest", facility_id=fid,
                         choices=lambda info: [{"key": "collect", "label": "売上を受け取る(1,848G)",
                                                "on": lambda i: None}],
                         exit_label="外に出る")
    app2.player.location = rebuilt
    app2.buttons = [{"text": "売上を受け取る(まだ無い)", "spec": PhaseSpec(ui.SAFE_CLS, [])},
                    {"text": "外に出る", "spec": PhaseSpec(ui.SAFE_CLS, [])}]
    modfacility.maintain_buttons(app2)
    texts = [e.get("text") for e in app2.buttons]
    ok &= check("焼かれた売上のボタンは消える", "売上を受け取る(まだ無い)" not in texts)
    ok &= check("今の売上のボタンが1つだけ", texts.count("売上を受け取る(1,848G)") == 1)
    ok &= check("出口も1つだけ", texts.count("外に出る") == 1)
    modfacility.register("915_invest", facility_id=fid,
                         choices=[{"key": "stay", "label": "滞在する",
                                   "on": lambda info: pressed.append(info)}],
                         exit_label="外に出る")

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

    print("文言: 層の文言が変われば、在るボタンの文言も更新する")
    counter = {"gold": 0}
    modfacility.register("915_invest", facility_id=fid,
                         choices=lambda info: [{"key": "collect",
                                                "label": "売上を受け取る({}G)".format(counter["gold"]),
                                                "on": lambda info: None}])
    # ゲームの入口（`会話する`）が並ぶ施設の画面で（自前のボタンだけの画面は下位の画面に見える）。
    app2.buttons = [{"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])}]
    modfacility.maintain_buttons(app2)
    ok &= check("最初の文言", any(e.get("text") == "売上を受け取る(0G)" for e in app2.buttons))
    counter["gold"] = 300
    modfacility.maintain_buttons(app2)          # ゲームが組み直さなくても
    ok &= check("文言が更新される（増えない）",
                [e.get("text") for e in app2.buttons].count("売上を受け取る(300G)") == 1
                and not any(e.get("text") == "売上を受け取る(0G)" for e in app2.buttons))
    modfacility.register("915_invest", facility_id=fid,
                         choices=lambda info: [{"key": "stay", "label": "滞在する",
                                                "on": lambda info: pressed.append(info)}])
    app2.buttons = []

    print("背景: MOD は描かず、頼みもしない（移動でゲームが自分で描く）")
    calls = []
    app2.change_background_image_to_current_location = lambda *a: calls.append("current")
    app2.player.location = rebuilt
    app2.location_image = os.path.join("C:\\x", "backgrounds", "街の入口", "image.png")
    app2.buttons = [{"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])}]
    modfacility.maintain_buttons(app2)
    modfacility.maintain_buttons(app2)
    ok &= check("塗り直しで絵に触らない（頼むと移動の描画と重なって2枚になる）", calls == [])
    app2.player.location = entrance2
    modfacility.maintain_buttons(app2)
    app2.player.location = rebuilt
    modfacility.maintain_buttons(app2)
    ok &= check("出て戻っても触らない", calls == [])
    app2.buttons = []
    app2.location_image = ""

    print("背景: 本体の経路はそのまま通し、落ちる不具合だけ握る")
    hooks = {}
    class Ctx2(FakeCtx):
        def wrap(self, target, **kw):
            def deco(fn):
                hooks[target] = fn
                return fn
            return deco
    logged = []
    modfacility._install(Ctx2(state_root), logged.append)
    current_hook = hooks[modfacility.BG_CURRENT_TARGET]
    id_hook = hooks[modfacility.BG_ID_TARGET]
    calls[:] = []
    current_hook(lambda self, *a, **k: calls.append("orig"), app2)
    ok &= check("いまの場所の絵は本体が描く（MOD は止めない）", calls == ["orig"])
    def boom(self, *a, **k):
        raise AttributeError("'InstantaleApp' object has no attribute 'app'")
    calls[:] = []
    ok &= check("本体が落ちても外へ出さない", current_hook(boom, app2) is None
                and any("raised" in line for line in logged))
    calls[:] = []
    id_hook(lambda self, *a, **k: calls.append("orig"), app2, fid)
    ok &= check("MOD の施設の id は名前で引く経路へ回す（本体は引けない）", calls == ["current"])
    calls[:] = []
    id_hook(lambda self, *a, **k: calls.append("orig"), app2, "2")
    ok &= check("よその施設の id は本体へ", calls == ["orig"])

    print("忘れる: 世界が変わったら控えの写しも捨てる（前の周回の建物の名で新築しない）")
    # 立っている建物（fid）の記録は後の場面が使うので、控えて戻す。
    rec = modfacility._record(fid)
    keep = {k: rec.get(k) for k in ("facility", "placed", "plain", "plain_noted", "snapshot")}
    rec["snapshot"] = {"name": "前の周回の金羊亭"}
    modfacility.forget()
    ok &= check("forget は写しも捨てる", rec.get("snapshot") is None)
    rec.update(keep)
    # 新築は別の id で。同じ id に前の周回の写しが残っていても使わない。
    fresh_id = modfacility.register("915_invest", key="fresh1",
                                    fields={"name": "新築の宿", "facility_type": "inn"})
    modfacility._record(fresh_id)["snapshot"] = {"name": "前の周回の金羊亭"}
    built_fresh = modfacility.spawn(app2, fresh_id, "1", fresh=True)
    ok &= check("fresh=True の spawn は写しを使わない",
                getattr(built_fresh, "name", None) == "新築の宿")
    modfacility.unregister("915_invest", fresh_id, app=app2)

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

    print("写し: plain=True の建物は中に立っている間だけ素データに写る")
    pid = modfacility.register("915_invest", key="arena1",
                               fields={"name": "闘技場", "facility_type": "colosseum"},
                               plain=True)
    for holder in (app2.save_data_dict, app2.world_dict):      # 別々の辞書（GAME.md §2.28）
        holder["areas"] = {"1": {"nodes": {"10": {"facilities": {}}}}}
    stores = [holder["areas"]["1"]["nodes"]["10"]["facilities"]
              for holder in (app2.save_data_dict, app2.world_dict)]
    arena = modfacility.spawn(app2, pid, "1")
    ok &= check("建てただけでは写らない", all(pid not in s for s in stores))
    app2.player.location = arena
    app2.buttons = []
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("中に立つと両方の素データに写る",
                all(pid in s for s in stores) and stores[0][pid] is stores[1][pid])
    plain = stores[0][pid]
    ok &= check("写しは8項目・同じ並び", list(plain) == list(modfacility.FACILITY_FIELDS))
    ok &= check("config は実体と同じ辞書", plain["config"] is arena.config)
    plain["config"]["current_phase"] = 3                       # ゲームが試合の進みを書く
    plain["config"]["enemy_data"] = {0: {"type": "normal", "rank": 14},   # 実行時は int の鍵
                                     2: {"type": "normal", "rank": 21}}
    plain["config"]["runtime_only"] = object()                  # JSON に落ちないもの
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("塗り直しても同じ写しのまま", stores[0][pid] is plain)
    hidden = modfacility.hide(app2, screen=screen)
    ok &= check("保存の間は外れる", all(pid not in s for s in stores))
    logged = []
    modfacility.snapshot_all(app2, write=logged.append)
    entry = modfacility._persisted_entry(app2, "915_invest", pid)
    snap_config = ((entry or {}).get("snapshot") or {}).get("config") or {}
    ok &= check("試合の進みが控えに写る", snap_config.get("current_phase") == 3)
    ok &= check("int の鍵は str になって写る（セーブと同じ往復）",
                sorted(snap_config.get("enemy_data") or {}) == ["0", "2"]
                and snap_config["enemy_data"]["2"]["rank"] == 21)
    ok &= check("JSON に落ちない値だけ捨てる（config ごとは捨てない）",
                "runtime_only" not in snap_config and snap_config.get("level_of_detail") == 0)
    ok &= check("何を捨てたかがログに残る",
                any("not jsonable" in line and "config.runtime_only:object" in line
                    for line in logged))
    logged[:] = []
    modfacility.snapshot_all(app2, write=logged.append)
    ok &= check("同じ内容なら二度は書かない", not any("not jsonable" in line for line in logged))
    del plain["config"]["runtime_only"]
    modfacility.restore(app2, hidden)
    ok &= check("保存の後は戻る", all(s.get(pid) is plain for s in stores))
    ok &= check("plain を名乗らない建物は写らない", all(fid not in s for s in stores))
    data = {"areas": app2.save_data_dict["areas"], "npcs": {}}
    cleaned, dropped = modfacility.strip_plain_from(data)
    ok &= check("書き出しの網が写しを落とす",
                dropped == [pid] and pid not in cleaned["areas"]["1"]["nodes"]["10"]["facilities"])
    ok &= check("元の辞書は触らない", pid in stores[0] and cleaned is not data
                and cleaned["npcs"] is data["npcs"])
    untouched = {"areas": {"1": {"nodes": {"10": {"facilities": {"0": {}}}}}}}
    ok &= check("落とすものが無ければ元のまま", modfacility.strip_plain_from(untouched)[0] is untouched)
    app2.player.location = entrance2
    app2.buttons = game_buttons(("宿屋", "2"))
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("外に出ると写しが外れる", all(pid not in s for s in stores))
    app2.player.location = arena
    app2.buttons = []
    modfacility.maintain_buttons(app2, screen=screen)
    modfacility.unregister("915_invest", pid, app=app2)
    ok &= check("壊すと写しも外れる", all(pid not in s for s in stores))
    app2.player.location = rebuilt
    app2.buttons = []

    print("会話中の保存: 居た場所のまま保存する")
    # ゲームは会話の途中を保存して再開できる。画面がどうであれ、
    # 中のままと名乗った建物では居た場所に戻すのが正しい（本人の指摘 2026-09-14）。
    modfacility.register("915_invest", facility_id=fid, keep_inside=True)
    app2.player.location = rebuilt
    app2.buttons = [{"text": "この話から依頼を作る（ヘルガ）",
                     "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
                    {"text": "話を切り上げる",
                     "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
    app2.in_conversation = "mod:915_invest:keeper"
    hidden3 = modfacility.hide(app2, screen=screen)
    ok &= check("会話の画面でも中のまま", app2.player.location is rebuilt)
    ok &= check("焼かれる選択肢も会話のまま",
                [e.get("text") for e in app2.buttons][0] == "この話から依頼を作る（ヘルガ）")
    ok &= check("立ち位置は書き出しで守る",
                modfacility._state().get("saved_inside") == fid)
    modfacility.restore(app2, hidden3)
    app2.in_conversation = False
    app2.buttons = []

    print("背景: 入口へ移すときは焼かれる絵も入口のものにする")
    # 立ち位置だけ移しても、焼かれた絵が建物のままだとロードが建物の絵で始まる
    # （実機 2026-09-14。入口に戻ったのに店の絵のままだった）。
    root = os.path.join(state_root, "worlds", "検査の世界", "backgrounds")
    for name in ("街の入口", "灯火亭（改装）"):
        os.makedirs(os.path.join(root, name), exist_ok=True)
        with io.open(os.path.join(root, name, "image.png"), "w") as fh:
            fh.write("x")
    shop_picture = os.path.join(root, "灯火亭（改装）", "image.png")
    app2.location_image = shop_picture
    app2.player.location = rebuilt
    app2.buttons = game_buttons(("宿屋", "2"))
    modfacility.register("915_invest", facility_id=fid)        # keep_inside は名乗らない
    hidden4 = modfacility.hide(app2, screen=screen)
    ok &= check("焼かれる絵が入口のものになる",
                app2.location_image == os.path.join(root, "街の入口", "image.png"),
                )
    modfacility.restore(app2, hidden4)
    ok &= check("保存の後は元の絵に戻る", app2.location_image == shop_picture)
    os.remove(os.path.join(root, "街の入口", "image.png"))
    hidden4 = modfacility.hide(app2, screen=screen)
    ok &= check("入口の絵が無ければ空にする（建物の絵のままにはしない）",
                app2.location_image == "")
    modfacility.restore(app2, hidden4)
    app2.location_image = shop_picture

    print("背景: 中のまま保存するときは焼かれる絵をその建物のものにする")
    # ゲームは MOD の施設に入っても `location_image` を更新しない（実セーブ 2026-09-16。
    # 道場の中で保存したセーブの絵が、繋ぎ先の区画のままだった）。
    # そのままだと、中に立ったまま再開したのに繋ぎ先の絵でロードが始まる。
    os.makedirs(os.path.join(root, "街の入口"), exist_ok=True)
    with io.open(os.path.join(root, "街の入口", "image.png"), "w") as fh:
        fh.write("x")
    entrance_picture = os.path.join(root, "街の入口", "image.png")
    modfacility.register("915_invest", facility_id=fid, keep_inside=True)
    app2.player.location = rebuilt
    app2.buttons = []
    app2.location_image = entrance_picture        # 来た場所の絵のまま入った
    hidden5 = modfacility.hide(app2, screen=screen)
    ok &= check("中に居るのに来た場所の絵、を直す",
                app2.location_image == shop_picture)
    ok &= check("立ち位置は中のまま", app2.player.location is rebuilt)
    modfacility.restore(app2, hidden5)
    ok &= check("保存の後は元の絵に戻る", app2.location_image == entrance_picture)
    os.remove(shop_picture)
    hidden5 = modfacility.hide(app2, screen=screen)
    ok &= check("建物の絵がまだ無ければ触らない（空にしない）",
                app2.location_image == entrance_picture)
    modfacility.restore(app2, hidden5)
    with io.open(shop_picture, "w") as fh:
        fh.write("x")
    app2.location_image = shop_picture

    print("書き出し: 絵と立ち位置が揃っているかを最後に検める")
    # どの経路が取りこぼしても、書き出しの直前でここが揃える。
    app2.player.location = rebuilt
    room_picture = os.path.join(root, "灯火亭（改装） - room(luxury_suite)", "image.png")
    before = {"player_data": {"location": fid},
              "game_variables": {"location_image": entrance_picture}}
    after, was_folder = modfacility.keep_saved_background(before, app2)
    ok &= check("食い違っていれば立ち位置の絵に直す",
                after["game_variables"]["location_image"] == shop_picture)
    ok &= check("直す前のフォルダ名を返す（ログに出す）", was_folder == "街の入口")
    ok &= check("元の辞書は触らない",
                before["game_variables"]["location_image"] == entrance_picture)
    same = {"player_data": {"location": fid},
            "game_variables": {"location_image": shop_picture}}
    ok &= check("揃っていれば触らない",
                modfacility.keep_saved_background(same, app2)[1] == "")
    room = {"player_data": {"location": fid},
            "game_variables": {"location_image": room_picture}}
    ok &= check("宿の部屋（`<施設名> - room(<等級>)`）は揃っている扱い",
                modfacility.keep_saved_background(room, app2)[1] == "")
    blank = {"player_data": {"location": fid},
             "game_variables": {"location_image": ""}}
    ok &= check("空は触らない（据える絵を引く手掛かりが無い）",
                modfacility.keep_saved_background(blank, app2)[1] == "")
    os.remove(shop_picture)
    ok &= check("その場所の絵がまだ無ければ触らない（描かない・頼まない）",
                modfacility.keep_saved_background(before, app2)[1] == "")
    with io.open(shop_picture, "w") as fh:
        fh.write("x")
    lost = {"player_data": {"location": "999"},
            "game_variables": {"location_image": entrance_picture}}
    ok &= check("立ち位置が引けなければ触らない",
                modfacility.keep_saved_background(lost, app2)[1] == "")

    print("書き出し: 中のまま保存した立ち位置を守る")
    # ゲームは `mod:` の id を途中で切って書くことがある（実機 2026-09-14。
    # 店の中で会話しながら保存したら `player_data["location"]` が 'mod' だけになった）。
    modfacility.register("915_invest", facility_id=fid, keep_inside=True)
    app2.player.location = rebuilt
    app2.buttons = []
    hidden2 = modfacility.hide(app2, screen=screen)
    ok &= check("中のまま保存する建物を覚える",
                modfacility._state().get("saved_inside") == fid)
    cut = {"player_data": {"location": "mod", "gold": 10}, "areas": {}}
    fixed_data, fixed = modfacility.keep_saved_location(cut, fid)
    ok &= check("切れた立ち位置を書き出しの写しで直す",
                fixed and fixed_data["player_data"]["location"] == fid)
    ok &= check("元の辞書は触らない", cut["player_data"]["location"] == "mod"
                and fixed_data["player_data"]["gold"] == 10)
    same, fixed = modfacility.keep_saved_location(
        {"player_data": {"location": fid}}, fid)
    ok &= check("切れていなければ何もしない", not fixed)
    ok &= check("中に居なければ何もしない",
                not modfacility.keep_saved_location({"player_data": {"location": "9"}}, None)[1])
    modfacility.restore(app2, hidden2)
    ok &= check("保存が終われば覚えを落とす",
                not modfacility._state().get("saved_inside"))
    modfacility.register("915_invest", facility_id=fid)      # keep_inside を戻す

    print("下位の画面: ゲームの選択肢に混ぜない")
    # ゲームの画面は3通り。施設の画面（移動あり）・入口だけの画面・下位の画面。
    # 下位の画面（会話相手の一覧・部屋選び・活動）には中の選択肢も道も足さない。
    talk_list = [{"text": "測定用の来訪者",
                  "spec": PhaseSpec("ConversationStartManager", ["mod:229:visitor"])},
                 {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
    app2.player.location = entrance2          # 建物の外（道が出る場所）
    app2.buttons = game_buttons(("宿屋", "2"))
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("施設の画面には道を足す",
                any(screen.mark_of(e) for e in app2.buttons))
    app2.buttons = [dict(e) for e in talk_list]
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("会話相手の一覧に道を混ぜない",
                [e.get("text") for e in app2.buttons] == ["測定用の来訪者", "やめる"],
                )
    app2.player.location = rebuilt             # 建物の中
    app2.buttons = [dict(e) for e in talk_list]
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("会話相手の一覧に中の選択肢も混ぜない",
                [e.get("text") for e in app2.buttons] == ["測定用の来訪者", "やめる"])
    app2.buttons = [{"text": "犬小屋(0G)", "spec": PhaseSpec("VacationStartManager", [4, "kennel"])},
                    {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("部屋選びにも混ぜない",
                [e.get("text") for e in app2.buttons] == ["犬小屋(0G)", "やめる"])

    print("会話中: ゲームの選択肢に混ぜない")
    # ゲームは会話の最中も施設の入口（`売買する` など）を選択肢に残すので、
    # 画面の中身では見分けられない（実機 2026-09-14。店の会話中に売上と出口が並んだ）。
    app2.player.location = rebuilt
    app2.buttons = []
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("普段は足す", bool(app2.buttons))
    app2.buttons = [{"text": "売買する", "spec": PhaseSpec("ShoppingStartManagerRemake", [])},
                    {"text": "話を切り上げる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
    app2.in_conversation = True
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("会話中は足さない",
                [e.get("text") for e in app2.buttons] == ["売買する", "話を切り上げる"])
    ok &= check("旗の名前が読める", modfacility.game_is_busy(app2) == ["in_conversation"])
    app2.in_conversation = False
    app2.in_shopping = True
    app2.buttons = []
    modfacility.maintain_buttons(app2, screen=screen)
    ok &= check("店の中（in_shopping）では足す", bool(app2.buttons))
    app2.in_shopping = False
    app2.buttons = []

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

    print("ノードが2つある街: プレイヤーの居る側に建てる")
    two = modfacility.register("914_home", key="two",
                               fields={"name": "自分の家",
                                       "facility_type": "location"})
    town, town_area, node_a, node_b, entrance1, entrance2 = make_two_node_town()
    built = modfacility.spawn(town, two, "1")
    ok &= check("プレイヤーの居るノードに建つ",
                node_b.facilities.get(two) is built and two not in node_a.facilities)
    ok &= check("そのノードの入口から繋がる", two in entrance2.connections)
    ok &= check("別のノードの入口には繋がない", two not in entrance1.connections)
    ok &= check("控えの置き場所もそのノード",
                modfacility.get(town, two).placed[:2] == ("1", "20"))

    print("ノードが2つある街: 行けないノードに在る建物は移す")
    modfacility.despawn(town, two)
    town.player.location = entrance1                      # 一度あちら側に建てる
    modfacility.spawn(town, two, "1")
    ok &= check("いったん別のノードに建った", two in node_a.facilities)
    town.player.location = entrance2                      # 遊んでいるのはこちら側
    moved = modfacility.spawn(town, two, "1")
    ok &= check("プレイヤーの居るノードへ移る",
                node_b.facilities.get(two) is moved and two not in node_a.facilities)
    ok &= check("前のノードの入口からは外れる", two not in entrance1.connections)
    ok &= check("控えの置き場所も書き換わる",
                modfacility.get(town, two).placed[:2] == ("1", "20"))
    ok &= check("名前は写しから戻る",
                getattr(moved, "name", None) == "自分の家")

    print("ノードが2つある街: 中に立っているあいだは動かさない")
    town.player.location = moved
    same = modfacility.spawn(town, two, "1")
    ok &= check("中に居るなら建て直さない", same is moved)
    town.player.location = entrance2
    modfacility.unregister("914_home", app=town)

    print("片付け: unregister で層も控えも消える")
    modfacility.unregister("915_invest", app=app2)
    modfacility.unregister("330_real_estate", app=app2)
    ok &= check("登録簿が空", modfacility.entries() == [])
    ok &= check("控えも消える",
                modfacility._persisted_entry(app2, "915_invest", fid) is None)

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
