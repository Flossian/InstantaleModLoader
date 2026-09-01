# -*- coding: utf-8 -*-
"""908_npc_carryover を窓抜き・ゲーム抜きで通す。

    python tools/tests/test_wip_npc_carryover.py

9xx なので CI は走らせない（`test_wip_*`）。

  復号    … 素の JSON も XOR も読める。読めないものは None（例外にしない）
  名前    … 使えない字は潰す。世界の控えと違って**短い印は付けない**（ゲームの
            書いたフォルダ名と同じ文字列でなければ画像が見つからない）
  往復    … 書き出した zip を読み返すと 33項目が同じ順で戻る。画像と記憶も入る
  予約    … pending.json を書いて読める。同名の検査が効く
  絞込    … 名前は部分一致、エリア・施設・職・分類・親密度は選択、レベルは下限
  立ち絵  … `image_src` は書いた機械の絶対パス。別の機械の世界を持ってきても
            `%LOCALAPPDATA%` から繋ぎ直して見つける
  居場所  … エクスポートの一覧は名前の隣に滞在中のエリア名と施設名を出す。
            同じものが zip にも入る（zip だけで誰だったか分かる）
  持ち物  … 持ち込まない（inventory / equipments は空で入る）
  取込    … ロードのフック（実行時の名簿がまだ空の状態）で NPC が
            **ゲームの保存する辞書へ**入り、adventurer_npcs に載り、
            持ち物の id が置き先の台帳で採り直され、記憶が写り、`status` が
            `placed` になる。2度目のロードでは増えない
  見送り  … 同名が居るときは作らず `skipped` にして、ゲーム内に1度出す
  保存    … 置いただけでは果たされていない。ゲームが保存したら印を付け、
            保存せずに終えた予約は次のロードで置き直す
"""
import collections
import importlib.util
import io
import random
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "908_npc_carryover")
sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import npcs as npc_tools          # noqa: E402

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


def load(name, path, package_dir=None):
    spec = importlib.util.spec_from_file_location(
        name, path,
        submodule_search_locations=[package_dir] if package_dir else None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# `carryover` は tool.py と本体の両方が読む。単体で読めることも要件。
carryover = load("carryover", os.path.join(MOD_DIR, "carryover.py"))


# ---- ゲームの代役 ---------------------------------------------------------

class Facility:
    def __init__(self, facility_id, facility_type, name=None):
        self.id = facility_id
        self.facility_type = facility_type
        # 名前を持たない個体も居る（到着の知らせは種別の語に落とす）。
        if name is not None:
            self.name = name
        self.characters = []


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, name, size, facilities):
        self.id = area_id
        self.name = name
        self.size = size
        self.nodes = {"0": Node(facilities)}
        self.adventurer_npcs = []
        self.resident_npcs = []


class Character:
    def __init__(self, character_id, data):
        self.id = character_id
        self.name = data.get("name")
        self.config = dict(data.get("config") or {})
        self.job = data.get("job")


class World:
    def __init__(self, areas, save_data_dict):
        self.areas = areas
        self.characters = {}
        self._save = save_data_dict

    def generate_character(self, character_id, character_value):
        data = self._save["npcs"][character_id]      # 無い id は KeyError
        scores = data["ability_scores"]
        for key in ("strength", "dexterity", "constitution",
                    "intelligence", "wisdom", "charisma"):
            scores[key]
        character = Character(str(character_id), data)
        self.characters[str(character_id)] = character
        return character


class App:
    def __init__(self, world, world_dict, save_data_dict):
        self.world = world
        self.world_dict = world_dict
        self.save_data_dict = save_data_dict
        self.said = []
        self.moved = []
        self.saves = 0

    def add_text(self, context=None, *args, **kwargs):
        self.said.append(context)

    def move_npc_to_facility(self, character_id, character_instance,
                             target_facility, target_node=None,
                             register_facility=True):
        self.moved.append((str(character_id), getattr(target_facility, "id", None)))
        target_facility.characters.append(str(character_id))

    def save_game(self, *args, **kwargs):
        self.saves += 1


class FakeCtx:
    def __init__(self, out_dir, state_dir):
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        # 例外の中身まで控える。名前だけだと、落ちた検査から原因へ辿れない。
        self.errors.append("{}: {}".format(msg, traceback.format_exc().strip()
                                           .splitlines()[-1]))

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def npc_record(name, npc_id, job="adventure", level=30, inventory=None,
               image_src=None):
    """セーブの形（33項目・この順）で1体。"""
    data = json.loads(json.dumps(npc_tools.NEW_NPC_TEMPLATE))
    data.update({
        "name": name, "id": str(npc_id), "category": "young man",
        "profile": "{}の経歴。".format(name), "personality": "静か。",
        "look_description": "背が高い。", "speech_style": "淡々と話す。",
        "job": job, "experience_level": level, "age": 20,
        "inventory": dict(inventory or {}),
        "image_src": dict(image_src or {"base_normal": None,
                                        "base_upscaled": None,
                                        "fullbody": None, "opponent": None,
                                        "face": None}),
        "relationship": {"player": {"affinity": 40,
                                    "affinity_text": ["仲間だと感じている", "魅力を感じている"],
                                    "relationship": ["初対面"],
                                    "conversation_count": 3}},
        "life_log": ["旅に出た"],
        "config": {"level_of_detail": 2, "is_player": False,
                   "is_dead": False, "difficulty_level": 20},
    })
    for key in ("strength", "dexterity", "constitution",
                "intelligence", "wisdom", "charisma"):
        data["ability_scores"][key] = 10
    return data


#: セーブの `areas`（居場所の名前を引くのに要る形だけ）。
SAVE_AREAS = {
    "0": {"id": "0", "name": "始まりの泥濘", "size": "town",
          "nodes": {"0": {"facilities": {
              "1": {"id": "1", "name": "泥濘のギルド", "facility_type": "guild"},
              "2": {"id": "2", "name": "灯り亭", "facility_type": "inn"}}}}},
    "1": {"id": "1", "name": "忘れられた坑道", "size": "dungeon", "nodes": {}},
}


def build_app(npcs, index=None):
    save = {"npcs": {str(k): v for k, v in npcs.items()},
            "index": dict(index or {"npc": 40, "item": 100, "area": 3})}
    areas = {"0": Area("0", "始まりの泥濘", "town",
                       [Facility("0", "entrance"), Facility("1", "guild",
                                                           "泥濘のギルド"),
                        Facility("2", "inn", "灯り亭")]),
             "1": Area("1", "忘れられた坑道", "dungeon",
                       [Facility("3", "entrance")]),
             # 名前を持たない施設（到着の知らせが種別の語に落ちる側）。
             "2": Area("2", "灰の交易都市", "city",
                       [Facility("4", "inn")])}
    world = World(areas, save)
    # 実機ではロードの時点で `world.characters` が埋まっている。
    # ここが空だと `npcs.npc_stores` が素データの辞書を見分けられない
    # （既存の character id を鍵に持つ辞書か、で判別している）。
    for npc_id, record in save["npcs"].items():
        world.characters[str(npc_id)] = Character(str(npc_id), record)
    world_dict = {"npcs": dict(save["npcs"]),
                  "index": dict(save["index"]),
                  "areas": {"0": {"adventurer_npcs": [], "resident_npcs": []},
                            "1": {"adventurer_npcs": []},
                            "2": {"adventurer_npcs": [], "resident_npcs": []}}}
    return App(world, world_dict, save)


tmp = tempfile.mkdtemp(prefix="npc_carryover_test_")
try:
    data_root = os.path.join(tmp, "game")
    state_dir = os.path.join(tmp, "state")
    out_dir = os.path.join(tmp, "out")
    os.environ["IML_INSTANTALE_DATA"] = data_root
    os.environ["IML_ROOT"] = ROOT
    os.environ["IML_STATE_DIR"] = state_dir

    # ---- 世界を2つこしらえる（元と置き先）---------------------------------
    source = {"0": npc_record("重装のハンス", 0,
                              inventory={"item_27": {"name": "使い古された短剣"},
                                         "item_28": {"name": "薬草"}}),
              "1": npc_record("星読みのリリア", 1, job="inn", level=12)}
    source["0"]["equipments"] = {"weapon": "item_27"}
    source["0"]["current_area"] = "0"
    source["0"]["current_location"] = "1"          # 泥濘のギルド
    # リリアは current_* が空で initial_location にだけ入っている個体
    source["1"]["initial_location"] = {"area": "0", "node": None, "facility": "2"}
    target = {"0": npc_record("先住のバルガス", 0)}

    for world, npcs, counters in (("ヴェスティア", source, {"npc": 2, "item": 30}),
                                  ("アルカディア", target, {"npc": 1, "item": 5})):
        folder = os.path.join(carryover.saves_dir(), world)
        os.makedirs(folder, exist_ok=True)
        payload = json.dumps({"npcs": npcs, "index": counters,
                              "areas": SAVE_AREAS,
                              "world_data": {"world_name": world}},
                             ensure_ascii=False).encode("utf-8")
        with io.open(carryover.save_path(world), "wb") as fh:
            fh.write(carryover.xor(payload))       # 実物と同じ難読化
        # 立ち絵を置く
        chars = carryover.characters_dir(world, "重装のハンス")
        os.makedirs(chars, exist_ok=True)
        for image in ("face_image.png", "fullbody.png"):
            with io.open(os.path.join(chars, image), "wb") as fh:
                fh.write(b"\x89PNG" + image.encode("ascii"))
    # image_src は絶対パス（実セーブと同じ形）
    hans_dir = carryover.characters_dir("ヴェスティア", "重装のハンス")
    source["0"]["image_src"] = {
        "base_normal": None, "base_upscaled": None,
        "fullbody": os.path.join(hans_dir, "fullbody.png"),
        "opponent": None,
        "face": os.path.join(hans_dir, "face_image.png")}
    payload = json.dumps({"npcs": source, "index": {"npc": 2, "item": 30},
                          "areas": SAVE_AREAS,
                          "world_data": {"world_name": "ヴェスティア"}},
                         ensure_ascii=False).encode("utf-8")
    with io.open(carryover.save_path("ヴェスティア"), "wb") as fh:
        fh.write(carryover.xor(payload))

    print("-- 復号と場所")
    check("XOR のセーブを読める",
          len(carryover.npcs_of(carryover.read_save("ヴェスティア"))) == 2)
    plain = os.path.join(carryover.saves_dir(), "素", "savedata.json")
    os.makedirs(os.path.dirname(plain), exist_ok=True)
    io.open(plain, "w", encoding="utf-8").write('{"npcs": {}}')
    check("素の JSON も読める", carryover.read_save("素") == {"npcs": {}})
    io.open(plain, "wb").write(b"\x00\x01\x02")
    check("読めないセーブは None（例外にしない）", carryover.read_save("素") is None)
    check("世界の一覧に出る",
          set(carryover.list_worlds()) == {"ヴェスティア", "アルカディア", "素"},
          carryover.list_worlds())

    print("-- 名前をファイル名に")
    check("普通の名前はそのまま", carryover.safe_name("重装のハンス") == "重装のハンス")
    check("使えない字は潰す", carryover.safe_name('a/b:c') == "a_b_c",
          carryover.safe_name("a/b:c"))
    check("短い印は付けない（世界の控えとは別）",
          "-" not in carryover.safe_name("a/b"), carryover.safe_name("a/b"))
    check("予約デバイス名は避ける", carryover.safe_name("CON") == "_CON")

    print("-- 書き出しと読み返し")
    export_dir = os.path.join(carryover.carryover_dir(state_dir), "ヴェスティア")
    dest = carryover.free_path(export_dir, "重装のハンス")
    carryover.export(source["0"], "ヴェスティア", "0", dest,
                     extra={"profile": {"name": "重装のハンス", "profile": "覚えた人物像"}})
    package = carryover.read_package(dest)
    check("読み返せる", package is not None and package.name == "重装のハンス")
    check("33項目が同じ順で戻る",
          list(package.npc) == list(source["0"]),
          list(package.npc)[:5])
    check("画像も入る", sorted(package.images) == ["face_image.png", "fullbody.png"],
          sorted(package.images))
    check("記憶も入る", package.extra.get("profile", {}).get("profile") == "覚えた人物像")
    check("元の辞書は触られていない",
          source["0"]["display_position_in_battle"] is None
          and source["0"]["relationship"]["player"]["affinity"] == 40)
    check("親密度を読める（配列の先頭）",
          package.affinity == (40, "仲間だと感じている"), package.affinity)
    check("レベルと職を読める",
          (package.level, package.job) == (30, "adventure"))
    second = carryover.free_path(export_dir, "重装のハンス")
    check("同じ名前は連番になる", second.endswith("(2).zip"), second)
    bad = os.path.join(export_dir, "壊れもの.zip")
    io.open(bad, "wb").write(b"not a zip")
    check("壊れた zip は None（一覧を止めない）", carryover.read_package(bad) is None)
    check("一覧は読めるものだけ（壊れた zip を飛ばす）",
          [p.name for p in carryover.list_packages(
              carryover.carryover_dir(state_dir))] == ["重装のハンス"],
          [p.name for p in carryover.list_packages(
              carryover.carryover_dir(state_dir))])

    print("-- 立ち絵の在り処")
    hans_folder = carryover.characters_dir("ヴェスティア", "重装のハンス")
    check("記録どおりの場所を見つける",
          carryover.image_dir_of(source["0"], "ヴェスティア") == hans_folder,
          carryover.image_dir_of(source["0"], "ヴェスティア"))
    # 別の機械で作られた世界を持ってきた形（`image_src` が他人のユーザー名を指す）。
    # 実データのペルディションがこの形で、95人中 93人が `C:\Users\Owner\...` だった。
    foreign = json.loads(json.dumps(source["0"]))
    foreign["image_src"] = {
        key: (value.replace(data_root, r"C:\Users\Owner\AppData\Local\Darmabeko\Instantale")
              if isinstance(value, str) and value else value)
        for key, value in foreign["image_src"].items()}
    check("別の機械のパスは繋ぎ直して見つける",
          carryover.image_dir_of(foreign, "ヴェスティア") == hans_folder,
          carryover.image_dir_of(foreign, "ヴェスティア"))
    check("繋ぎ直しは worlds から後ろを残す",
          carryover.local_path(
              r"C:\Users\Owner\AppData\Local\Darmabeko\Instantale"
              r"\worlds\X\characters\Y\face_image.png").endswith(
                  os.path.join("worlds", "X", "characters", "Y", "face_image.png")))
    check("目印の無いパスはそのまま",
          carryover.local_path(r"D:\somewhere\a.png") == r"D:\somewhere\a.png")
    check("繋ぎ直しても在る画像だけを持ち出す",
          sorted(name for _p, name in carryover.image_files("ヴェスティア", foreign))
          == ["face_image.png", "fullbody.png"])
    check("絵の無い NPC はフォルダ無し（空文字）",
          carryover.image_dir_of({"name": "まだ絵の無い者",
                                  "image_src": {"face": None}}, "ヴェスティア") == "")

    print("-- 居た場所")
    save_v = carryover.read_save("ヴェスティア")
    check("current_* から引ける",
          carryover.location_of(save_v, source["0"]) == ("始まりの泥濘", "泥濘のギルド"),
          carryover.location_of(save_v, source["0"]))
    check("initial_location からも引ける",
          carryover.location_of(save_v, source["1"]) == ("始まりの泥濘", "灯り亭"),
          carryover.location_of(save_v, source["1"]))
    check("引けないものは空",
          carryover.location_of(save_v, {"name": "どこにも居ない"}) == ("", ""))

    print("-- 画面（tool.py）")
    sys.path.insert(0, MOD_DIR)
    tool = load("carryover_tool", os.path.join(MOD_DIR, "tool.py"))
    model = tool.Model()
    check("世界を開ける", model.open_world("ヴェスティア"))
    rows = model.rows()
    check("一覧が出る", len(rows) == 2, rows)
    hans_row = next(r for r in rows if r[1] == "重装のハンス")
    check("名前の隣に居場所が並ぶ",
          (hans_row[2], hans_row[3]) == ("始まりの泥濘", "泥濘のギルド"), hans_row)
    check("エリアと施設の選択肢を集める",
          model.places() == (["始まりの泥濘"], ["泥濘のギルド", "灯り亭"]),
          model.places())
    check("エリアで絞れる",
          [r for r in rows if tool.same(r[2], "始まりの泥濘")] == rows)
    check("施設で絞れる",
          len([r for r in rows if tool.same(r[3], "泥濘のギルド")]) == 1)
    check("名前は部分一致", tool.matches("重装のハンス", "ハンス")
          and not tool.matches("重装のハンス", "リリア"))
    keys = sorted(["64", "7", "", "100 盟友だと思っている", "40 多少の好意がある", "adventure"],
                  key=tool.sort_value)
    check("見出しの並べ替えは数字で始まる欄を数で、空欄を末尾に",
          keys == ["7", "40 多少の好意がある", "64", "100 盟友だと思っている", "adventure", ""], keys)
    check("職は選択", tool.same("adventure", "adventure")
          and tool.same("adventure", tool.ANY) and not tool.same("inn", "adventure"))
    check("レベルは下限", tool.at_least(30, "30") and not tool.at_least(12, "30")
          and tool.at_least(12, ""))
    check("親密度の語彙はセーブから拾う",
          model.affinity_texts() == ["仲間だと感じている"], model.affinity_texts())
    done, failed = model.export(["1"])
    check("チェックした分を書き出せる", done == 1 and not failed, failed)
    model.reload_packages()
    check("書き出したものが一覧に出る",
          "星読みのリリア" in [p.name for p in model.packages])
    lilia = next(p for p in model.packages if p.name == "星読みのリリア")
    check("zip にも居場所が入る（誰だったかが zip だけで分かる）",
          lilia.where == ("始まりの泥濘", "灯り亭"), lilia.where)

    check("元の世界には同名が居る（検査が効く）",
          model.collides("星読みのリリア", "ヴェスティア") is True)
    check("置き先には居ない", model.collides("星読みのリリア", "アルカディア") is False)
    check("読めない世界は検査できない（None）",
          model.collides("だれか", "素") is None)
    model.reserve(lilia, "アルカディア",
                  {"memory": True, "relationship": True, "life_log": False})
    check("予約が書かれる", os.path.isfile(carryover.pending_path(state_dir)))
    check("予約を読み返せる",
          carryover.load_pending(state_dir)[0]["target_world"] == "アルカディア")

    print("-- 取り込み（ゲームの中）")
    hans = next(p for p in model.packages if p.name == "重装のハンス")
    model.reserve(hans, "アルカディア",
                  {"memory": True, "relationship": True, "life_log": True})
    # 記憶の受け皿（311_ が入っている世界を演じる）
    os.makedirs(os.path.join(state_dir, "npc_profiles"), exist_ok=True)

    ctx = FakeCtx(out_dir, state_dir)
    mod = load("npc_carryover_mod", os.path.join(MOD_DIR, "npc_carryover.py"),
               package_dir=MOD_DIR)
    mod.apply(ctx)
    check("ロードのフックが2つ当たる",
          all(t in ctx.hooks for t in mod.LOAD_TARGETS), sorted(ctx.hooks))

    app = build_app(target, index={"npc": 1, "item": 5})
    app.world_dict["world_data"] = {"world_name": "アルカディア"}
    app.save_data_dict["world_data"] = {"world_name": "アルカディア"}
    # 実機のロード直後は `world.characters` がまだ埋まっていない
    # （2026-08-30 の1回目は1件だけだった）。素データを見分けられるかは
    # ローダ側の `npc_stores` の仕事だが、ここを空にしておかないと
    # その経路を通らない。
    app.world.characters.clear()
    hook = ctx.hooks[mod.LOAD_TARGETS[1]]
    hook(lambda _self, *a, **k: None, app)

    made = [npc_id for npc_id in app.save_data_dict["npcs"] if npc_id != "0"]
    check("2人とも世界に入る", len(made) == 2, sorted(app.save_data_dict["npcs"]))
    brought = {app.save_data_dict["npcs"][i]["name"]: i for i in made}
    check("名前で引ける", set(brought) == {"重装のハンス", "星読みのリリア"}, sorted(brought))
    hans_id = brought["重装のハンス"]
    record = app.save_data_dict["npcs"][hans_id]
    check("ゲームが保存する側に入る（world_dict だけにしない）",
          all(i in app.save_data_dict["npcs"] for i in made),
          sorted(app.save_data_dict["npcs"]))
    check("最後の手段に落ちずに generate_character が通る",
          all(i in app.world.characters for i in made),
          sorted(app.world.characters))
    check("書けなかった警告は出ていない",
          not any("not in save_data_dict" in text
                  for _lvl, text in ctx.logs), ctx.logs)
    check("項目の並びが崩れない",
          list(record) == list(npc_tools.NEW_NPC_TEMPLATE), list(record)[:6])
    check("台帳を進めている",
          app.save_data_dict["index"]["npc"] == int(hans_id) + 1
          or app.world_dict["index"]["npc"] == int(hans_id) + 1,
          (app.save_data_dict["index"], app.world_dict["index"]))
    # 持ち物と装備は持ち込まない（アイテムの id は世界ごとの台帳で振られる。
    # 実データ369体の `equipments` は全て空だった）。
    check("持ち物は持ち込まない", record["inventory"] == {}, record["inventory"])
    check("装備も持ち込まない", record["equipments"] == {}, record["equipments"])
    check("好感度を引き継ぐ", record["relationship"]["player"]["affinity"] == 40)
    check("経歴を引き継ぐ", record["life_log"] == ["旅に出た"])
    lilia_id = brought["星読みのリリア"]
    check("経歴を切ったほうは空",
          app.save_data_dict["npcs"][lilia_id]["life_log"] == [])
    check("いま居る場所は空に戻す",
          record["location"] == {"area": None, "node": None, "facility": None})
    check("画像を置き先の世界へ展開する",
          os.path.isfile(os.path.join(
              carryover.characters_dir("アルカディア", "重装のハンス"),
              "face_image.png")))
    check("image_src を貼り替える",
          (record["image_src"]["face"] or "").startswith(
              carryover.characters_dir("アルカディア", "重装のハンス")),
          record["image_src"]["face"])
    placed_areas = {a for a in ("0", "2")
                    if hans_id in app.world_dict["areas"][a]["adventurer_npcs"]}
    check("adventurer_npcs に載る", placed_areas, app.world_dict["areas"])
    check("ダンジョンには置かない",
          hans_id not in app.world_dict["areas"]["1"]["adventurer_npcs"])
    check("ギルドか宿に置く",
          all(f in ("1", "2", "4") for _c, f in app.moved), app.moved)
    # 到着の知らせは「〈エリア名〉の〈場所名〉へ」。
    # 場所は種別の語（「ギルド」）ではなく施設そのものの名前を出す。
    refresh = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"]
    refresh(lambda _self, *a, **k: None, app)
    arrivals = [text for text in app.said if "流れて来た" in (text or "")]
    # 置き先は乱数なので施設は決め打たない。
    # 見るのは「エリア名 + 施設そのものの名前」で書かれていること。
    check("到着の知らせは〈エリア名〉の〈場所名〉で書く",
          len(arrivals) == 2
          and all(any(spot in text for spot in
                      ("始まりの泥濘の泥濘のギルドへ", "始まりの泥濘の灯り亭へ",
                       "灰の交易都市の宿へ"))
                  for text in arrivals),
          arrivals)
    profiles = json.load(io.open(
        os.path.join(state_dir, "npc_profiles", "アルカディア.json"),
        encoding="utf-8"))
    check("記憶を新しい id で写す",
          profiles.get(hans_id, {}).get("profile") == "覚えた人物像", profiles)
    rows = carryover.load_pending(state_dir)
    check("予約が placed になる",
          all(row["status"] == carryover.PLACED for row in rows),
          [row["status"] for row in rows])

    print("-- 置き先の引き方")
    # 代役の世界: area 0 にギルド(1)と宿(2)、area 2 に宿(4)だけ、area 1 はダンジョン。
    spread = collections.Counter()
    for seed in range(300):
        random.seed(seed)
        fresh = build_app({"0": npc_record("先住のバルガス", 0)})
        fresh.world_dict["world_data"] = {"world_name": "アルカディア"}
        fresh.save_data_dict["world_data"] = {"world_name": "アルカディア"}
        fresh.world.characters.clear()
        rows = carryover.load_pending(state_dir)
        for row in rows:
            row["status"] = carryover.PENDING
            row.pop("saved", None)
        carryover.save_pending(state_dir, rows)
        hook(lambda _self, *a, **k: None, fresh)
        for _cid, fid in fresh.moved:
            spread[fid] += 1
    check("ギルドにも宿にも置かれる",
          spread["1"] > 0 and (spread["2"] + spread["4"]) > 0, dict(spread))
    check("ダンジョンには置かない", spread["3"] == 0, dict(spread))
    check("同じ土地のギルドと宿がほぼ半々（宿の数に引きずられない）",
          0.7 < spread["1"] / max(spread["2"], 1) < 1.4, dict(spread))

    print("-- 保存されるまでは果たされていない")
    rows = carryover.load_pending(state_dir)
    check("置いただけでは saved の印が付かない",
          all(not row.get("saved") for row in rows), rows)
    save_hook = ctx.hooks[mod.SAVE_TARGET]
    save_hook(lambda _self, *a, **k: None, app)
    rows = carryover.load_pending(state_dir)
    check("ゲームが保存したら印が付く",
          all(row.get("saved") for row in rows), rows)

    # 置いたが保存せずに終えた形。次のロードで予約が戻り、置き直される。
    lonely = build_app({"0": npc_record("先住のバルガス", 0)})
    lonely.world_dict["world_data"] = {"world_name": "アルカディア"}
    lonely.save_data_dict["world_data"] = {"world_name": "アルカディア"}
    lonely.world.characters.clear()
    for row in carryover.load_pending(state_dir):
        pass
    rows = carryover.load_pending(state_dir)
    for row in rows:
        row.pop("saved", None)           # 保存しないまま終えた状態にする
    carryover.save_pending(state_dir, rows)
    hook(lambda _self, *a, **k: None, lonely)
    names = {n.get("name") for n in lonely.save_data_dict["npcs"].values()}
    check("保存されずに終えた予約は置き直される",
          {"重装のハンス", "星読みのリリア"} <= names, sorted(names))
    check("置き直したら status は placed に戻る",
          all(row["status"] == carryover.PLACED
              for row in carryover.load_pending(state_dir)),
          [row["status"] for row in carryover.load_pending(state_dir)])

    # 保存済みの予約は、その NPC を後から消しても戻さない。
    saved_rows = carryover.load_pending(state_dir)
    for row in saved_rows:
        row["saved"] = "2026-08-30T15:00:00"
    carryover.save_pending(state_dir, saved_rows)
    empty = build_app({"0": npc_record("先住のバルガス", 0)})
    empty.world_dict["world_data"] = {"world_name": "アルカディア"}
    empty.save_data_dict["world_data"] = {"world_name": "アルカディア"}
    empty.world.characters.clear()
    hook(lambda _self, *a, **k: None, empty)
    check("保存済みの予約は勝手に戻さない",
          list(empty.save_data_dict["npcs"]) == ["0"],
          sorted(empty.save_data_dict["npcs"]))

    # `save_game` を包む前に置いた回は `saved` の控えを持たない。
    # その NPC が世界に居るなら保存は済んでいるので、控えを追いつかせる。
    late = carryover.load_pending(state_dir)
    for row in late:
        row.pop("saved", None)
    carryover.save_pending(state_dir, late)
    hook(lambda _self, *a, **k: None, lonely)      # ハンスたちが居る世界
    check("居るのに控えが無い予約は saved を付けて済ませる",
          all(row.get("saved") for row in carryover.load_pending(state_dir)),
          carryover.load_pending(state_dir))
    check("そのとき置き直しはしない",
          len(lonely.save_data_dict["npcs"]) == 3,
          sorted(lonely.save_data_dict["npcs"]))

    print("-- 2度目のロードとぶつかったとき")
    before = len(app.save_data_dict["npcs"])
    hook(lambda _self, *a, **k: None, app)
    check("繰り返しロードしても増えない",
          len(app.save_data_dict["npcs"]) == before)

    model.pending = carryover.load_pending(state_dir)
    model.forget_names()
    model.reserve(hans, "アルカディア", {"memory": False})
    app2 = build_app({"0": npc_record("重装のハンス", 0)})   # 同名が先に居る
    app2.world_dict["world_data"] = {"world_name": "アルカディア"}
    app2.save_data_dict["world_data"] = {"world_name": "アルカディア"}
    hook(lambda _self, *a, **k: None, app2)
    check("同名が居たら作らない", list(app2.save_data_dict["npcs"]) == ["0"],
          sorted(app2.save_data_dict["npcs"]))
    skipped = [row for row in carryover.load_pending(state_dir)
               if row["status"] == carryover.SKIPPED]
    check("見送りを理由ごと残す",
          len(skipped) == 1 and skipped[0]["reason"] == "同名の人物が居る", skipped)
    # 実機の1回目は、ロードの後・画面が出る前に注入し直しが走っていた
    # （out/modloader.log。ロード 14:03:37 → 適用 14:03:39）。
    # 言付けを閉包に持たせていると、その瞬間に消える。
    mod.apply(ctx)
    refresh = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"]
    refresh(lambda _self, *a, **k: None, app2)
    check("注入し直しを跨いでも言付けが残る",
          any("同名" in (text or "") for text in app2.said), app2.said)
    refresh(lambda _self, *a, **k: None, app2)
    check("言付けは1度きり",
          sum(1 for text in app2.said if "同名" in (text or "")) == 1, app2.said)
    print("-- 施設に名前が無いとき")
    # 名前を持たない個体に当たった回だけ、種別の語（「ギルド」「宿」）で埋める。
    model.pending = carryover.load_pending(state_dir)
    model.forget_names()
    model.reserve(lilia, "アルカディア", {"memory": False})
    # 先住を1人置いておく。素データの置き場は「既存の id を鍵に持つ辞書か」で
    # 見分けるので、空の世界では `make_npc` が書き先を選べない。
    plain = build_app({"0": npc_record("先住のバルガス", 0)},
                      index={"npc": 1, "item": 5})
    plain.world_dict["world_data"] = {"world_name": "アルカディア"}
    plain.save_data_dict["world_data"] = {"world_name": "アルカディア"}
    for area in plain.world.areas.values():
        for node in area.nodes.values():
            for facility in node.facilities.values():
                facility.__dict__.pop("name", None)
    ctx.hooks[mod.LOAD_TARGETS[1]](lambda _self, *a, **k: None, plain)
    ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"](
        lambda _self, *a, **k: None, plain)
    plain_said = [text for text in plain.said if "流れて来た" in (text or "")]
    check("施設の名前が読めない回は種別の語で埋める",
          any(("始まりの泥濘のギルドへ" in text or "始まりの泥濘の宿へ" in text
               or "灰の交易都市の宿へ" in text) for text in plain_said),
          plain_said)

    check("記録に例外が残っていない", not ctx.errors, ctx.errors)
finally:
    os.environ.pop("IML_INSTANTALE_DATA", None)
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
