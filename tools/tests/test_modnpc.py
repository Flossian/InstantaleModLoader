# -*- coding: utf-8 -*-
"""ローダの `modnpc`（MOD が持つ NPC と、正規 NPC への層）をゲーム抜きで通す。

    python tools/tests/test_modnpc.py

偽の `Character` と世界を差し込み、次を確認する。

  層     … 同じ持ち主は積み重ならず差し替わる（注入し直しても増えない）
  組む   … `original_ability_scores` に6つの鍵が渡る（None だと実機で落ちる）
  載る   … 文字列の id で `world.characters` に載り、素データの写しが置かれる
  隠す   … 保存の間だけ名簿と素データの反復から消え、id では引ける。施設の名簿と主も一緒に外れる
  写す   … 保存のたびに実体が控えへ写され、読み直しで写しから組み直される
  取っ手 … 読み書きとも実体へ素通し。正規 NPC の素データは取っ手から触らない
  窓口   … 誰にでも効く層と notes が1つの複製にまとまる
  捨てる … 世界が変わると実体は捨て、層は残す
  飲む   … 層のフックが投げても関所は止まらない

背景: セーブに残さないことを守っているのは保存の直前に隠す1箇所だけ
（`modnpc` の冒頭）。ここが抜けると `mod:` の id がセーブに焼かれ、
MOD を外した後も残る。
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

from instantale_modloader import modnpc  # noqa: E402

ABILITY_KEYS = ("strength", "dexterity", "constitution", "intelligence",
                "wisdom", "charisma")

#: 実機の `Character` が持っている属性のうち、ここで読み書きするもの。
CHARACTER_ATTRS = ("name", "profile", "personality", "job", "look_description",
                   "speech_style", "image_src", "relationship", "current_log",
                   "life_log", "skills", "knowledges", "physical_integrity")


class FakeCharacter:
    """`scripts.characters.Character` の代役。

    `original_ability_scores` を**添字で読む**ところまで似せる。
    実機ではここが `None` のままで落ちた（GAME.md §2.23）ので、
    ひな型が6つの鍵を渡していることをこの代役が検査する。
    """

    def __init__(self, **kwargs):
        scores = kwargs.get("original_ability_scores")
        for key in ABILITY_KEYS:
            scores[key]            # None なら TypeError。鍵が無ければ KeyError
        for attr in CHARACTER_ATTRS:
            setattr(self, attr, None)
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeCtx:
    """控え（`state.WorldStore`）が要るものだけ。`out/test/state` に書く。"""

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


class FakeFacility:
    def __init__(self, facility_id, owner=None):
        self.id = facility_id
        self.characters = []
        self.owner = owner


class FakeArea:
    def __init__(self, facilities):
        self.nodes = {"0": types.SimpleNamespace(facilities=facilities)}


class FakeWorld:
    def __init__(self, characters, areas):
        self.characters = characters
        self.areas = areas


def make_app():
    """世界に正規 NPC が1人、施設が1つある状態。"""
    plain = FakeCharacter(name="宿の主", original_ability_scores=dict(
        (key, 10) for key in ABILITY_KEYS))
    plain.profile = "この街で宿を継いだ"
    plain.config = {"level_of_detail": 2, "is_player": False, "is_dead": False}
    facility = FakeFacility("5", owner="7")
    world = FakeWorld({"7": plain}, {"1": FakeArea({"5": facility})})
    app = types.SimpleNamespace(world=world, saved=None,
                                save_data_dict={"npcs": {"7": {"name": "宿の主"}}},
                                world_dict={"npcs": {"7": {"name": "宿の主"}},
                                            "world_data": {"name": "検査の世界"}})
    # セーブに焼かれる、id を載せた器（実セーブの game_variables と同じ形）。
    app.buttons = [{"text": "会話する", "spec": {"cls_name": "DisplayTalkChoice", "args": []}}]
    app.buttons_backup = []
    app.function_correspond_to_input = {"cls_name": "FreeInputStart", "args": []}
    app.party = ["player"]
    app.original_party = ["player"]
    app.current_enemy_dict = {}

    def save_game():
        # 保存の瞬間に名簿へ見えているものを控える（漏れの検査）。
        # ゲームは保存のときに app の属性から game_variables を組む。
        # ここで見えているものがそのままディスクに焼かれる。
        app.saved = {"roster": sorted(world.characters),
                     "owner": facility.owner,
                     "facility": list(facility.characters),
                     "plain": sorted(app.save_data_dict["npcs"]),
                     "plain_copy": sorted(dict(app.save_data_dict["npcs"])),
                     "buttons": json.dumps(app.buttons, ensure_ascii=False),
                     "buttons_backup": json.dumps(app.buttons_backup, ensure_ascii=False),
                     "input": json.dumps(app.function_correspond_to_input, ensure_ascii=False),
                     "party": list(app.party),
                     "original_party": list(app.original_party),
                     "enemies": sorted(app.current_enemy_dict)}

    app.save_game = save_game
    return app, world, facility, plain


def check(label, cond):
    print("  {} {}".format("ok  " if cond else "FAIL", label))
    return bool(cond)


def main():
    ok = True
    sys.modules["scripts.characters"] = types.SimpleNamespace(
        Character=FakeCharacter)
    modnpc.purge()
    # 関所はゲームの `save_game` に当てるもので、偽の環境には無い。
    # `spawn` は関所が今の世代で立っていなければ載せない（実機で踏んだ穴）ので、
    # ここでは立っていることにする。
    modnpc.gate_is_live = lambda: True
    state_root = os.path.join(HERE, os.pardir, os.pardir, "out", "test", "state")
    shutil.rmtree(os.path.join(state_root, "modnpc"), ignore_errors=True)
    modnpc.bind_store(FakeCtx(state_root))

    print("層: 同じ持ち主は積み重ならない")
    npc_id = modnpc.register("229_probe", key="clerk", fields={"name": "受付"})
    modnpc.register("229_probe", key="clerk", fields={"name": "受付", "job": "inn"})
    modnpc.register("914_real_estate", npc_id=npc_id, fields={"job": "guild"})
    ok &= check("id は mod: の文字列", npc_id == "mod:229_probe:clerk")
    ok &= check("持ち主2人で層は2つ", len(modnpc.layers(npc_id)) == 2)
    ok &= check("後から積んだ層が勝つ", modnpc.fields_of(npc_id)["job"] == "guild")
    ok &= check("entries が持ち主で絞れる",
                modnpc.entries("914_real_estate") == [npc_id])

    print("組む: 能力値の6鍵が渡る")
    built = modnpc.build(modnpc.fields_of(npc_id))
    ok &= check("Character が組めた", built is not None)
    ok &= check("名前が入る", getattr(built, "name", None) == "受付")
    ok &= check("6鍵が揃っている",
                sorted(built.original_ability_scores) == sorted(ABILITY_KEYS))
    # 2（詳細生成済み）で組むとゲームが会話の直前に埋めない（実機 2026-09-12）。
    ok &= check("level_of_detail は 1（生成直後の住人と同じ）",
                built.config.get("level_of_detail") == 1)

    print("載る: 文字列の id で名簿に載り、素データの写しが置かれる")
    app, world, facility, plain = make_app()
    character = modnpc.spawn(app, npc_id)
    ok &= check("名簿に mod: の id が載る", npc_id in world.characters)
    ok &= check("実体は組んだもの", world.characters[npc_id] is character)
    ok &= check("二度呼んでも組み直さない",
                modnpc.spawn(app, npc_id) is character)
    ok &= check("npc_id_of が名簿から引ける",
                modnpc.npc_id_of(app, character) == npc_id)
    # ゲームの詳細生成は `save_data_dict['npcs'][id]` へ書く（実機 2026-09-12: 無いと KeyError）。
    ok &= check("素データの写しが save_data_dict['npcs'] に居る",
                npc_id in app.save_data_dict["npcs"])
    ok &= check("world_dict['npcs'] にも同じ辞書",
                app.world_dict["npcs"].get(npc_id) is app.save_data_dict["npcs"].get(npc_id))
    ok &= check("写しは33項目の並び",
                tuple(app.save_data_dict["npcs"][npc_id]) == tuple(modnpc.npcs.NEW_NPC_TEMPLATE))

    print("置く: 施設の名簿と主、実体の location")
    ok &= check("置けた", modnpc.place(app, npc_id, "1", "5", owner=True))
    ok &= check("施設の名簿に載る", facility.characters == [npc_id])
    ok &= check("主になった", facility.owner == npc_id)
    # 「会話する」の一覧は各人物の `.location` を施設と突き合わせる（実機 2026-09-12）。
    ok &= check("location が施設オブジェクト", character.location is facility)
    ok &= check("current_area がエリア", character.current_area is world.areas["1"])

    print("隠す: 保存の間だけ引き上げる")
    # 実セーブの `game_variables.buttons_backup` には
    # `ConversationStartManager(args=[<id>])` が入っている（2026-09-13 に確認）。
    # 一覧を出したまま保存すると `mod:` の id がそこへ焼かれ、MOD を外した後に押すと落ちる。
    app.buttons.append({"text": "受付", "spec": {"cls_name": "ConversationStartManager",
                                                 "args": [npc_id]}})
    app.buttons_backup = [{"text": "受付", "spec": {"cls_name": "ConversationStartManager",
                                                    "args": [npc_id]}},
                          {"text": "やめる", "spec": {"cls_name": "JustSetButtonToNormalPhase",
                                                      "args": []}}]
    app.function_correspond_to_input = {"cls_name": "ConversationInQuestStart",
                                        "args": [npc_id]}
    app.party = ["player", npc_id]
    app.original_party = ["player", npc_id]
    app.current_enemy_dict = {npc_id: {"name": "受付"}}
    hidden = modnpc.hide(app)
    # 反復からは消えるが id では引ける（保存は別スレッドで、その間にゲームが引く。
    # 外していた頃は `KeyError` で落ちた。実機 2026-09-12）。
    ok &= check("保存の間、id では引ける", world.characters.get(npc_id) is character
                and npc_id in world.characters)
    ok &= check("保存の間、反復には出ない",
                npc_id not in list(world.characters) and npc_id not in dict(world.characters.items()))
    # C レベルの複製（`dict(view)` / `json`）も内部の格納しか見ない。そこにも出ない。
    ok &= check("保存の間、dict(view) にも出ない", npc_id not in dict(world.characters)
                and len(world.characters) == 1)
    world.characters["99"] = object()
    ok &= check("保存中の書き込みは元へ通る", "99" in world.characters.source)
    app.save_game()
    modnpc.restore(app, hidden)
    ok &= check("保存の後、素の辞書に戻る", type(world.characters) is dict)
    world.characters.pop("99", None)
    saved = app.saved
    ok &= check("保存の瞬間、名簿に mod: が無い",
                all(not key.startswith("mod:") for key in saved["roster"]))
    ok &= check("保存の瞬間、施設の名簿が素", saved["facility"] == [])
    ok &= check("保存の瞬間、主が元の値", saved["owner"] == "7")
    ok &= check("保存の瞬間、素データの写しが反復に出ない",
                saved["plain"] == ["7"] and saved["plain_copy"] == ["7"])
    ok &= check("保存の瞬間、選択肢に mod: が無い",
                npc_id not in saved["buttons"] and npc_id not in saved["buttons_backup"])
    ok &= check("保存の瞬間、自由入力の spec に mod: が無い", npc_id not in saved["input"])
    ok &= check("保存の瞬間、パーティに mod: が無い",
                saved["party"] == ["player"] and saved["original_party"] == ["player"])
    ok &= check("保存の瞬間、敵の一覧に mod: が無い", saved["enemies"] == [])
    ok &= check("落とすのは mod: を指すものだけ（他の選択肢は残る）",
                "DisplayTalkChoice" in saved["buttons"] and "やめる" in saved["buttons_backup"])
    ok &= check("保存の後、名簿に戻る", npc_id in world.characters)
    ok &= check("保存の後、施設にも戻る", facility.characters == [npc_id])
    ok &= check("保存の後、主に戻る", facility.owner == npc_id)
    ok &= check("保存の後、素データの写しが戻る",
                type(app.save_data_dict["npcs"]) is dict and npc_id in app.save_data_dict["npcs"])
    ok &= check("保存の後、選択肢・自由入力・パーティ・敵が戻る",
                any(npc_id in json.dumps(e, ensure_ascii=False) for e in app.buttons)
                and app.function_correspond_to_input.get("args") == [npc_id]
                and app.party == ["player", npc_id]
                and app.original_party == ["player", npc_id]
                and npc_id in app.current_enemy_dict)
    app.buttons = [app.buttons[0]]
    app.buttons_backup = []
    app.function_correspond_to_input = {"cls_name": "FreeInputStart", "args": []}
    app.party = ["player"]
    app.original_party = ["player"]
    app.current_enemy_dict = {}

    print("写す: 保存のたびに実体が控えへ写り、読み直しで写しから組み直す")
    character.current_log = ["受付と話した"]
    character.physical_integrity = 42
    character.config["is_dead"] = True
    done = modnpc.snapshot_all(app)
    entry = modnpc._persisted_entry(app, "229_probe", npc_id)
    ok &= check("写した", done == [npc_id] and entry is not None)
    ok &= check("写しに記憶・属性・config が入る",
                entry["snapshot"].get("current_log") == ["受付と話した"]
                and entry["snapshot"].get("config", {}).get("is_dead") is True)
    ok &= check("実行時のオブジェクトは写さない", "location" not in entry["snapshot"])
    ok &= check("存在と置き場所も控えに在る",
                entry["spawned"] is True and entry["place"] == ["1", "5", True])
    ok &= check("state/modnpc/<世界>.json に書かれている",
                os.path.isfile(modnpc.store().path("検査の世界")))
    modnpc.forget()
    ok &= check("捨てると実体と写しの材料が消える",
                modnpc.registry()[npc_id]["character"] is None
                and modnpc.registry()[npc_id]["snapshot"] is None)
    app2, world2, fac2, _plain2 = make_app()
    done = modnpc.restore_world(app2, world=world2, save_data_dict=app2.world_dict)
    ok &= check("控えの分だけ戻る", done == [("229_probe", npc_id)])
    rebuilt = world2.characters.get(npc_id)
    ok &= check("写しから組み直される（記憶が戻る）",
                rebuilt is not None and rebuilt.current_log == ["受付と話した"]
                and rebuilt.config.get("is_dead") is True)
    ok &= check("宣言時の初期値は写しに負ける", rebuilt.name == "受付")
    ok &= check("置き場所も戻る", fac2.characters == [npc_id] and fac2.owner == npc_id)

    print("取っ手: 読み書きとも実体へ素通し。素データの口は無い")
    v = modnpc.get(app2, npc_id)
    g = modnpc.get(app2, "7")
    ok &= check("居ない id は None", modnpc.get(app2, "999") is None)
    ok &= check("is_mod の見分け", v.is_mod and not g.is_mod)
    ok &= check("33項目がプロパティで読める", v.name == "受付" and g.profile == "この街で宿を継いだ")
    ok &= check("名前の違いを吸収（ability_scores）",
                sorted(v.ability_scores) == sorted(ABILITY_KEYS))
    ok &= check("config の鍵", v.is_dead is True and v.level_of_detail == 1)
    v.is_dead = False
    ok &= check("config へ書ける（実体と写しは同じ辞書）",
                rebuilt.config["is_dead"] is False and v.data["config"]["is_dead"] is False)
    v.profile = "受付係。今日は休み"
    ok &= check("プロパティへの書き込みは実体と写しの両方へ",
                rebuilt.profile == "受付係。今日は休み" and v.data["profile"] == "受付係。今日は休み")
    v.physical_integrity = 7
    ok &= check("知らない属性も実体へ素通し", rebuilt.physical_integrity == 7 and v.physical_integrity == 7)
    ok &= check("正規 NPC の data は無い（素データはここから触らない）", g.data is None)
    ok &= check("location は施設の実体", v.location is fac2)
    ok &= check("snapshot() でいまの写しが取れる", v.snapshot().get("profile") == "受付係。今日は休み")
    v.unplace()
    ok &= check("メソッドは既存の関数を呼ぶ", fac2.characters == [] and v.location is not fac2)
    v.despawn()
    ok &= check("降ろした後は in_world が偽（実体は記録に残るので exists は真）",
                not v.in_world and v.exists and modnpc.get(app2, npc_id) is not None)
    ok &= check("降ろすと控えは spawned=False",
                modnpc._persisted_entry(app2, "229_probe", npc_id)["spawned"] is False)
    ok &= check("降ろすと素データの写しも消える",
                npc_id not in app2.save_data_dict["npcs"] and npc_id not in app2.world_dict["npcs"])

    print("外す: 層を外しても本物は世界から消えない。控えも消える")
    modnpc.register("903_test", npc_id="7", notes=lambda info: "密偵の噂")
    modnpc.unregister("903_test", "7", app=app2)
    ok &= check("本物は名簿に残る", "7" in world2.characters)
    ok &= check("記録は消える", "7" not in modnpc.registry())
    modnpc.spawn(app2, npc_id)
    modnpc.unregister("229_probe", npc_id, app=app2)
    ok &= check("ModNPC の持ち主を外すと控えも消える（spawned=False が書き戻らない）",
                modnpc._persisted_entry(app2, "229_probe", npc_id) is None)
    ok &= check("別の持ち主の層が残っていれば記録は残る", npc_id in modnpc.registry())

    print("組み直す: 同じ持ち主が登録し直したら実体は組み直す")
    # 登録簿は注入をまたいで生きる。前の版で組んだ実体を使い回すと、
    # `build` を直しても古い実体が同じ場所で落ちる（実機 2026-09-12）。
    modnpc.register("229_probe", key="clerk", fields={"name": "受付"})
    stale = modnpc.spawn(app2, npc_id)
    modnpc.register("229_probe", key="clerk", fields={"name": "受付２"})
    ok &= check("実体が捨てられた", modnpc.registry()[npc_id]["character"] is None)
    fresh_one = modnpc.spawn(app2, npc_id)
    ok &= check("組み直された（写しが在るので名前は写しのまま）",
                fresh_one is not stale and fresh_one.name == "受付")
    modnpc.registry()[npc_id]["snapshot"] = None
    modnpc.register("229_probe", key="clerk", fields={"name": "受付２"})
    ok &= check("写しが無ければ宣言の初期値で組む", modnpc.spawn(app2, npc_id).name == "受付２")

    print("窓口: 誰にでも効く層と notes が1つの複製にまとまる")
    # 311 / 317 / 321 / 403 が4本とも自前で持っていた手順（相手を複製して profile に足す）を
    # 関所が1回でやる。順は priority → id 指定 → ANY → 積んだ順。同じ文章は1つに畳む。
    modnpc.purge()
    modnpc.register("311_test", modnpc.ANY, notes=lambda info: "記憶: 前に会った", priority=10)
    modnpc.register("317_test", modnpc.ANY, notes=lambda info: "評判: 街で知られている")
    modnpc.register("403_test", modnpc.ANY, notes=lambda info: "記憶: 前に会った")   # 311 と同文
    modnpc.register("321_test", modnpc.ANY, notes=lambda info: None)                # 何も足さない
    modnpc.register("903_test", npc_id="7", notes=lambda info: "この人だけ: 密偵")
    modnpc.register("905_test", modnpc.ANY, notes=lambda info: "要約には足さない",
                    sites={"conversation_resolver"})
    app3, world3, _f3, plain3 = make_app()
    ok &= check("ANY の層は has_layers に効く", modnpc.has_layers("7") and modnpc.has_layers("99"))
    stack = modnpc.layers_for("7")
    ok &= check("順は priority → id 指定 → ANY → 積んだ順",
                [l["owner"] for l in stack] == ["903_test", "317_test", "403_test", "321_test",
                                                "905_test", "311_test"])
    args = {"messages": [], "character_instance": plain3}
    info = {"site": "conversation_starter", "app": app3, "npc_id": "7",
            "character": plain3, "args": args}
    chars, owners = modnpc.apply_notes(info, stack)
    clone = args["character_instance"]
    ok &= check("引数の相手が複製に差し替わる", clone is not plain3)
    ok &= check("本物の profile は触らない", plain3.profile == "この街で宿を継いだ")
    ok &= check("複製の profile は素の文 + 足した文",
                clone.profile.startswith("この街で宿を継いだ\n\n") and chars > 0)
    ok &= check("同じ文章は1つに畳む", clone.profile.count("記憶: 前に会った") == 1)
    ok &= check("持ち主は繋いだ順（畳んだ分と None は含まない）",
                owners == ["903_test", "317_test", "403_test"])
    ok &= check("要約向けの層は会話には足さない", "要約には足さない" not in clone.profile)
    info2 = {"site": "conversation_resolver", "app": app3, "npc_id": "7",
             "character": plain3, "args": {"character_instance": plain3}}
    _text2, owners2 = modnpc.compose_notes(info2, stack)
    ok &= check("sites を指定した層は要約にだけ足す", owners2 == ["905_test"])

    print("飲む: 層のフックが投げても止まらない（下の ERROR は飲んだ記録）")
    calls = []

    def boom(info):
        calls.append(info["site"])
        raise RuntimeError("フックの中の失敗")

    def quiet(info):
        calls.append(info["owner"])
        return True

    modnpc.register("905_broken", npc_id="7", on={"world": boom})
    modnpc.register("906_quiet", npc_id="7", on={"world": quiet})
    results = modnpc.fire("world", app3, "7")
    ok &= check("投げた層の後も次が呼ばれる", "906_quiet" in calls)
    ok &= check("戻り値は投げなかった層のぶん", ("906_quiet", True) in results)

    print("片付け: purge で登録簿が空になる")
    modnpc.register("229_probe", key="clerk", fields={"name": "受付"})
    modnpc.spawn(app3, npc_id)
    modnpc.purge(app3)
    ok &= check("登録簿が空", modnpc.registry() == {})
    ok &= check("世界からも降りた", npc_id not in world3.characters)

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
