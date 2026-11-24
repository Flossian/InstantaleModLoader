# -*- coding: utf-8 -*-
"""348_party_departure をゲーム抜きで通す。

    python tools/tests/test_party_departure.py

偽の app / QuestEndManager / QuestRetireManager / PhaseSpec / ConversationEndManager /
Area / Facility / Clock を差し込み、次を確認する。

  解散しない   … 依頼のクリアの解散だけを止める（名簿は元のまま・置き直しも起きない）。離脱の文を差し替える
  近くに残す   … 土地を跨いで解散したら、いまの町のギルドへ置き先を差し替える。雇った土地ならゲームの置き先のまま
  素通り       … 死別・既定の放棄・普段の NPC 移動・解散の外からの removal は触らない
  取り違え     … 本当に外れた相手の置き直しは止めない（世界から消さないため）
  会話の別れ   … 仲間との会話にだけ「ここで別れる」が出る。確認を挟み、会話を閉じてから外す・置く・保存する
  置き先       … 雇った場所 → いまの町のギルド → ゲームの置き先。置き先の無い土地では別れない
  名簿         … 名簿の在り処と形を決めつけない

解散の検出はスタックのコードオブジェクトで行うので、依頼の解散は `QuestEndManager.method_1` の中から呼ぶ形にしてある。
ゲームが起動していなくても走るので、MOD を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """MOD を番号を除いた名前で探す。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return matches[0], os.path.join(folder, entry)


FOLDER, MOD = find_mod("_party_departure")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def names_of(moves):
    return [getattr(m["facility"], "name", m["facility"]) for m in moves]


# ---------------------------------------------------------------- 偽ゲーム
# ここで定義したクラスは __main__ の属性になる（MOD は `sys.modules['__main__']` から引く）。
class Character:
    def __init__(self, **kw):
        self.display_position_in_battle = None
        self.current_location = None
        self.__dict__.update(kw)


def member_key(value):
    return value if isinstance(value, str) else str(getattr(value, "id", value))


class Facility:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Node:
    def __init__(self, **kw):
        self.facilities = {}
        self.__dict__.update(kw)


class Area:
    """実セーブと同じ入れ子: areas[id].nodes[nid].facilities[fid]。"""

    def __init__(self, area_id, name, facilities):
        self.id = area_id
        self.name = name
        node = Node(id=area_id + "0", name=name + "・中央")
        node.facilities = dict((f.id, f) for f in facilities)
        self.nodes = {node.id: node}


def build_area(area_id, name, guild=True):
    facilities = [Facility(id=area_id + "1", name=name + "の宿", facility_type="inn"),
                  Facility(id=area_id + "2", name=name + "の店", facility_type="general_store")]
    if guild:
        facilities.append(Facility(id=area_id + "9", name=name + "のギルド", facility_type="guild"))
    return Area(area_id, name, facilities)


def node_of(areas, area_id):
    return areas[area_id].nodes[area_id + "0"]


def guild_of(areas, area_id):
    node = node_of(areas, area_id)
    for facility in node.facilities.values():
        if facility.facility_type == "guild":
            return facility, node
    return None, None


def inn_of(areas, area_id):
    node = node_of(areas, area_id)
    return node.facilities[area_id + "1"], node


class World:
    def __init__(self, characters, areas):
        self.characters = characters
        self.areas = areas


def unpack(answer):
    if isinstance(answer, (tuple, list)):
        return (answer[0], answer[1]) if len(answer) >= 2 else (answer[0], None)
    return answer, None


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    """自前ボタンに持たせる無害な spec の相手。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class ConversationEndManager:
    """会話を閉じる。in_conversation を落とすところまでを真似る。"""

    def __init__(self, app, in_conversation_id, finisher, end_text):
        self.app = app
        self.end_text = end_text

    def execute(self, choice_text):
        self.app.in_conversation = False
        self.app.ended.append(self.end_text)
        return None


class InstantaleApp:
    def __init__(self, world, roster, player, decoy=None):
        self.world = world
        self.player = player
        # 名簿の在り処は形で決めつけない（GAME.md §2.8）。decoy を渡すと「app.party は別物」を再現する。
        self.party = roster if decoy is None else decoy
        self.game_variables = {"party": roster}
        self.roster = roster
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.in_conversation = True
        self.in_battle = False
        self.in_colosseum_battle = False
        self.in_boss_battle = False
        self.current_quest_data = None
        self.original_party = []
        self.texts = []
        self.ended = []
        self.moved = []
        self.move_shapes = []
        self.removed = []
        self.saves = 0
        self.loaded = []
        self.party_updates = 0
        self.hud = HUD()
        # 実装の分からない部分を切り替えるつまみ。
        self.leave_facility = None        # setup で入れる（ゲームの置き先 ＝ 雇った場所）
        self.leave_shape = "tuple"
        self.leave_answer = None          # 解散処理が受け取った答え
        self.game_places_after_remove = True
        self.game_asks_leave_facility = True
        self.places_before_remove = False
        self.keyword_move = False
        self.remove_raises = False
        self.game_moves_on_remove = False
        self.remove_keeps_member = False

    def add_text(self, context):
        self.texts.append(context)

    def process_choice(self, function, choice_text=""):
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def update_party_member(self, dt):
        self.party_updates += 1

    def display_button_load(self, dt):
        self.loaded.append(list(self.to_display_buttons))

    def on_button_press(self, button_index):
        return ("game", self.buttons[button_index].get("text"))

    def get_party_leave_facility(self, character_instance):
        if self.leave_facility is None:
            return None
        facility, node = self.leave_facility
        if self.leave_shape == "tuple":
            return (facility, node)
        if self.leave_shape == "list":
            return [facility, node]
        if self.leave_shape == "single":
            return facility
        raise RuntimeError("the game cannot decide where to put them")

    def move_npc_to_facility(self, *args, **kwargs):
        self.move_shapes.append((len(args), sorted(kwargs)))
        return self._move(*args, **kwargs)

    def _move(self, character_id, character_instance, target_facility, target_node=None,
              register_facility=True):
        # タプルのまま渡されたら本物と同じように壊れる。
        if isinstance(target_facility, (tuple, list)):
            raise AttributeError("'tuple' object has no attribute 'characters'")
        self.moved.append({"id": str(character_id), "facility": target_facility, "node": target_node,
                           "register_facility": register_facility})
        if character_instance is not None:
            character_instance.current_location = target_facility

    def remove_party_member(self, member_id):
        if self.remove_raises:
            raise RuntimeError("the game could not remove the member")
        self.removed.append(str(member_id))
        if self.game_moves_on_remove:
            self.move_npc_to_facility(member_id, self.world.characters[str(member_id)],
                                      self.leave_facility[0], self.leave_facility[1])
        if self.remove_keeps_member:
            return
        if isinstance(self.roster, dict):
            self.roster.pop(str(member_id), None)
        else:
            self.roster[:] = [v for v in self.roster if member_key(v) != str(member_id)]

    def save_game(self):
        self.saves += 1


class QuestEndManager:
    """依頼のクリア。実測の順序（GAME.md §2.8）: 帰還 → 報酬 → remove_party_member → 離脱の文 → 呼び出し元が置き直す。"""

    def __init__(self, app):
        self.app = app

    def place(self, member_id, character):
        app = self.app
        if not app.game_places_after_remove:
            return
        if app.game_asks_leave_facility:
            app.leave_answer = app.get_party_leave_facility(character)
            facility, node = unpack(app.leave_answer)
        else:
            facility, node = app.leave_facility
        if app.keyword_move:
            app.move_npc_to_facility(character_id=member_id, character_instance=character,
                                     target_facility=facility)
            return
        app.move_npc_to_facility(member_id, character, facility, node)

    def method_1(self, member_id):
        app = self.app
        character = app.world.characters[str(member_id)]
        app.add_text("パーティは帰還した...")
        app.add_text("9475ゴールドの報酬を受け取った。")
        if app.places_before_remove:
            self.place(member_id, character)
            app.remove_party_member(member_id)
            app.add_text("{}はパーティから離脱した。".format(character.name))
            return
        app.remove_party_member(member_id)
        app.add_text("{}はパーティから離脱した。".format(character.name))
        self.place(member_id, character)

    def execute(self, choice_text):
        return self.method_1(choice_text)


class QuestRetireManager(QuestEndManager):
    """依頼の放棄。`method_1` を上書きする（継承のままだとコードオブジェクトが同じになり、見分けを検証できない）。"""

    def method_1(self, member_id):
        app = self.app
        character = app.world.characters[str(member_id)]
        app.remove_party_member(member_id)
        app.add_text("{}はパーティから離脱した。".format(character.name))
        self.place(member_id, character)


class DeathManager(QuestRetireManager):
    """死別。解散のマネージャではないので素通りしなければならない。"""

    def method_1(self, member_id):
        return QuestRetireManager.method_1(self, member_id)


BASES = {"app": InstantaleApp}


class FakeClock:
    def __init__(self):
        self.intervals = []
        self.onces = []

    def schedule_interval(self, callback, timeout):
        self.intervals.append(callback)

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def tick(self, times=1):
        for _ in range(times):
            self.intervals = [cb for cb in self.intervals if cb(0.3) is not False]

    def run_onces(self):
        for _ in range(8):
            pending, self.onces = self.onces, []
            if not pending:
                return
            for callback in pending:
                callback(0.0)


def install_fake_hud():
    """`scripts.hud.new_hud.InstanTaleHUD` を差し込む（MOD は HUD を型で探す）。"""
    name = "scripts.hud.new_hud"
    module = types.ModuleType(name)

    class InstanTaleHUD:
        def __init__(self):
            self.painted = []
            self.party_painted = 0

        def update_button_texts(self, instance, value):
            self.painted.append(list(value))

        def update_party_display(self, *args):
            self.party_painted += 1

    module.InstanTaleHUD = InstanTaleHUD
    sys.modules[name] = module
    return InstanTaleHUD


def install_fake_kivy():
    clock = FakeClock()
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    return clock


class FakeCtx:
    """`ctx.wrap` を当てた順に控える。世代は apply() ごとに変える（窓口の包みは世代ごとに1枚）。"""

    _seq = 0

    def __init__(self, out_dir):
        self.out_dir = out_dir
        FakeCtx._seq += 1
        self.generation = FakeCtx._seq
        self.hooks = []
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

    def log(self, msg):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.append((target, func))
            return func
        return decorator


def load_mod():
    name = "instantale_mod_test_" + FOLDER
    for cached in [key for key in sys.modules if key == name or key.startswith(name + ".")]:
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def install(hooks, cls):
    """`__main__:InstantaleApp.<名前>` のフックを、本番と同じ形（メソッドの差し替え）で当てた順に載せる。"""
    prefix = "__main__:InstantaleApp."
    for target, hook in hooks:
        if not target.startswith(prefix):
            continue
        name = target[len(prefix):]
        original = getattr(cls, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(cls, name, make())


OUT_DIR = os.path.join(tempfile.gettempdir(), "instantale_test_party_departure")


def read_log():
    try:
        with io.open(os.path.join(OUT_DIR, "party_leave.log"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def talk_buttons(partner_id):
    """会話画面のボタン。実セーブと同じ形（args は相手の id / finisher / end_text）。"""
    return [{"text": "会話を終了する",
             "spec": PhaseSpec("ConversationEndManager", [partner_id, "user", "<行動: 会話を終了する>"])}]


def setup(here="9", home="7", member="63", party=None, partner=None, shape="shared", configure=None):
    """MOD を適用し、エリア `here` に居て、雇った土地が `home` の仲間 `member` と会話している app を返す。

    ゲームの置き先（`get_party_leave_facility`）は雇った場所（`home` の宿）。
    `shape` は名簿の載り方（shared / split / instances / dict）。
    設定は `configure` で apply() の前に入れる（本番も設定は注入のときに効く）。
    """
    main = sys.modules["__main__"]
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    main.InstantaleApp = app_cls
    for name in ("QuestEndManager", "QuestRetireManager", "DeathManager", "ConversationEndManager",
                 "JustSetButtonToNormalPhase", "PhaseSpec", "Area", "Node", "Facility", "Character", "World"):
        setattr(main, name, globals()[name])
    choices.reset()
    text_filters.reset()

    areas = {"7": build_area("7", "テストの町A"),
             "9": build_area("9", "テストの町B"),
             "13": build_area("13", "忘れられた坑道", guild=False)}
    characters = {
        member: Character(id=member, name="テスト仲間C",
                          initial_location={"area": home, "node": None, "facility": home + "1"}),
        "70": Character(id="70", name="テスト旅人", initial_location={"area": "7", "node": None, "facility": "71"}),
    }
    player = Character(id="player", name="テストプレイヤー", current_area=areas[here])
    characters["player"] = player
    members = list(party or ("player", member))
    if shape == "instances":
        members = [value if value == "player" else characters[value] for value in members]
    elif shape == "dict":
        members = dict((value, characters.get(value)) for value in members)
    app = app_cls(World(characters, areas), members, player, decoy=[] if shape == "split" else None)
    if shape == "dict":
        app.party = members
    app.leave_facility = inn_of(areas, home)
    app.buttons = talk_buttons(partner or member)

    ctx = FakeCtx(OUT_DIR)
    module = load_mod()
    if configure is not None:
        configure(module)
    module.apply(ctx)
    install(ctx.hooks, app_cls)
    return app, ctx, module, areas


def no_keep(module):
    module.KEEP_PARTY = False


def index_of(app, label):
    for i, entry in enumerate(app.buttons):
        if choices.ours(entry) and entry.get("text") == label:
            return i
    return -1


def part_ways(app, mod):
    app.refresh_choice_buttons()
    app.on_button_press(index_of(app, mod.farewell.LEAVE_LABEL))
    clock.run_onces()
    app.on_button_press(index_of(app, mod.farewell.CONFIRM_LABEL))
    clock.tick()
    clock.run_onces()


HUD = install_fake_hud()
clock = install_fake_kivy()
from instantale_modloader import choices, text_filters  # noqa: E402

# ================================================================== 解散しない
print("=== 依頼の解散：解散しない（既定） ===")
app, ctx, mod, areas = setup()
QuestEndManager(app).method_1("63")
check("パーティから外れない", app.party == ["player", "63"], app.party)
check("ゲームの removal を通していない", app.removed == [], app.removed)
check("どこにも置かれない", app.moved == [], names_of(app.moved))
check("報酬などの流れは止めない", any("報酬を受け取った" in t for t in app.texts), app.texts)
check("「離脱した」とは言わない", not any("離脱" in t for t in app.texts), app.texts)
check("残った旨を1回だけ出す", sum(1 for t in app.texts if "パーティに残り" in t and "テスト仲間C" in t) == 1, app.texts)
check("例外を1つも出していない", not ctx.errors, ctx.errors)
check("引き留めをログに残す（呼び出し元つき）",
      "stays in the party" in read_log() and "QuestEndManager.method_1" in read_log())
before = len(app.texts)
app.add_text(context="テスト仲間Cはパーティから離脱した。")
check("キーワード引数の文も差し替える", app.texts[before:] and "パーティに残り" in app.texts[-1], app.texts[before:])
app.add_text("旅人はパーティから離脱した。")
check("引き留めていない相手の離脱の文は書き換えない", app.texts[-1] == "旅人はパーティから離脱した。", app.texts[-1])

app, ctx, mod, areas = setup()
long_name = "古き森の誓いを継ぐ者にして七つの丘を渡り歩いた放浪の吟遊詩人たる長い名のテスト仲間"
app.world.characters["63"].name = long_name
QuestEndManager(app).method_1("63")
check("40字を超える名前でも離脱の文を差し替える",
      any(long_name in t and "パーティに残り" in t for t in app.texts), app.texts)

app, ctx, mod, areas = setup()
app.places_before_remove = True
QuestEndManager(app).method_1("63")
check("置き先を聞いてから外すビルドでも置かれず、解散もしない",
      app.moved == [] and app.party == ["player", "63"], (names_of(app.moved), app.party))

app, ctx, mod, areas = setup()
app.places_before_remove = True
app.game_asks_leave_facility = False
QuestEndManager(app).method_1("63")
check("聞かずに先に置くビルドでも解散はしない（置き直しだけは間に合わない）",
      app.party == ["player", "63"] and len(app.moved) == 1, (app.party, names_of(app.moved)))

print("=== 素通り ===")
app, ctx, mod, areas = setup()
DeathManager(app).method_1("63")
check("死別は外れて、ゲームの置き先へ", app.removed == ["63"]
      and [m["facility"] for m in app.moved] == [inn_of(areas, "7")[0]], names_of(app.moved))

app, ctx, mod, areas = setup()
QuestRetireManager(app).method_1("63")
check("依頼の放棄は既定では触らない", app.removed == ["63"]
      and [m["facility"] for m in app.moved] == [inn_of(areas, "7")[0]], names_of(app.moved))

app, ctx, mod, areas = setup()
app.move_npc_to_facility("63", app.world.characters["63"], inn_of(areas, "9")[0])
check("普段の NPC の移動は素通りする", [m["facility"] for m in app.moved] == [inn_of(areas, "9")[0]],
      names_of(app.moved))

app, ctx, mod, areas = setup()
app.remove_party_member("63")
check("解散の外からの removal は止めない", app.party == ["player"], app.party)

print("=== 本当に外れる相手は邪魔しない ===")
app, ctx, mod, areas = setup()
QuestEndManager(app).method_1("63")
app.remove_party_member("63")
check("引き留めた後でも、別経路の removal は通す", app.party == ["player"], app.party)
app.move_npc_to_facility("63", app.world.characters["63"], inn_of(areas, "9")[0])
check("  → その置き直しは止めない", [m["facility"] for m in app.moved] == [inn_of(areas, "9")[0]],
      names_of(app.moved))
check("  → その旨をログに残す", "is leaving for real" in read_log())

app, ctx, mod, areas = setup()
QuestEndManager(app).method_1("63")
mod.HOLD_SECONDS = 0.0
try:
    time.sleep(0.01)
    app.move_npc_to_facility("63", app.world.characters["63"], inn_of(areas, "9")[0])
    check("控えが切れたら普通に動かせる", len(app.moved) == 1, names_of(app.moved))
finally:
    mod.HOLD_SECONDS = 30.0


def keep_on_retire(module):
    module.ALSO_ON_QUEST_RETIRE = True


app, ctx, mod, areas = setup(configure=keep_on_retire)
QuestRetireManager(app).method_1("63")
check("放棄でも同じ扱いなら、放棄でも残る", app.party == ["player", "63"] and app.moved == [],
      (app.party, names_of(app.moved)))

# ================================================================== 近くに残す
print("=== 依頼の解散：会いに行ける場所に残す ===")
app, ctx, mod, areas = setup(configure=no_keep)
QuestEndManager(app).method_1("63")
check("解散はする", app.party == ["player"], app.party)
check("土地を跨いだら、いまの町のギルドに置く", [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]],
      names_of(app.moved))
check("ノードもいまの町のもの", app.moved and app.moved[0]["node"] is guild_of(areas, "9")[1])
check("置き先を聞かれたらギルドを答え、タプルの形を保つ",
      isinstance(app.leave_answer, tuple) and app.leave_answer[0] is guild_of(areas, "9")[0], app.leave_answer)
check("差し替えは1回だけで、register_facility は変えない",
      len(app.moved) == 1 and app.moved[0]["register_facility"] is True, app.moved)
check("例外は出ていない", not ctx.errors, ctx.errors)
clock.run_onces()
check("行き先を本文に出す", any("テスト仲間Cはテストの町Bのギルドに留まることになった" in t for t in app.texts), app.texts)
app.move_npc_to_facility("63", app.world.characters["63"], inn_of(areas, "9")[0])
check("解散の後の普段の移動までは追いかけない", app.moved[-1]["facility"] is inn_of(areas, "9")[0],
      names_of(app.moved))

app, ctx, mod, areas = setup(here="7", configure=no_keep)
QuestEndManager(app).method_1("63")
check("雇った土地で解散したら、ゲームの置き先（雇った場所）のまま",
      [m["facility"] for m in app.moved] == [inn_of(areas, "7")[0]], names_of(app.moved))
clock.run_onces()
check("  → そのときは行き先を言わない", not any("留まることになった" in t for t in app.texts), app.texts)

for shape in ("list", "single"):
    app, ctx, mod, areas = setup(configure=no_keep)
    app.leave_shape = shape
    QuestEndManager(app).method_1("63")
    check("ゲームの答えが {} の形ならその形で答える".format(shape),
          (isinstance(app.leave_answer, list) if shape == "list" else app.leave_answer is guild_of(areas, "9")[0])
          and [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], app.leave_answer)

app, ctx, mod, areas = setup(configure=no_keep)
app.leave_shape = "boom"
QuestEndManager(app).method_1("63")
check("ゲームが答えられなくても、解散の中なら置き先を出す",
      isinstance(app.leave_answer, tuple) and app.leave_answer[0] is guild_of(areas, "9")[0], app.leave_answer)

app, ctx, mod, areas = setup(configure=no_keep)
app.leave_shape = "boom"
raised = None
try:
    app.get_party_leave_facility(app.world.characters["63"])
except RuntimeError as exc:
    raised = exc
check("解散の外でのゲームの失敗は握り潰さない", raised is not None, raised)

app, ctx, mod, areas = setup(configure=no_keep)
app.game_asks_leave_facility = False
app.keyword_move = True
QuestEndManager(app).method_1("63")
check("置き先を聞かず、キーワードで置き直すビルドでも差し替わる",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]]
      and app.moved[0]["node"] is guild_of(areas, "9")[1], names_of(app.moved))
check("  → 受けた形のまま渡す（ノードはキーワードで足す）",
      app.move_shapes == [(0, ["character_id", "character_instance", "target_facility", "target_node"])],
      app.move_shapes)

app, ctx, mod, areas = setup(here="13", configure=no_keep)
QuestEndManager(app).method_1("63")
check("ギルドの無い土地ではゲームの置き先のまま", [m["facility"] for m in app.moved] == [inn_of(areas, "7")[0]],
      names_of(app.moved))

app, ctx, mod, areas = setup(configure=no_keep)
app.player.current_area = "9"
QuestEndManager(app).method_1("63")
check("いまの土地が id の文字列でも引き当てる", [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]],
      names_of(app.moved))

app, ctx, mod, areas = setup(configure=no_keep)
app.remove_raises = True
raised = None
try:
    QuestEndManager(app).method_1("63")
except RuntimeError as exc:
    raised = exc
check("ゲームが外せなければ例外はそのまま上がり、仲間は残る", raised is not None and app.party == ["player", "63"],
      (raised, app.party))
app.remove_raises = False
app.move_npc_to_facility("63", app.world.characters["63"], inn_of(areas, "9")[0])
check("  → 外せなかった相手の後の移動は差し替えない", [m["facility"] for m in app.moved] == [inn_of(areas, "9")[0]],
      names_of(app.moved))

app, ctx, mod, areas = setup(configure=no_keep)
app.is_adding_text = True
QuestEndManager(app).method_1("63")
clock.run_onces()
check("本文を流している間は行き先を割り込ませない", not any("留まることになった" in t for t in app.texts), app.texts)
app.is_adding_text = False
clock.tick()
clock.run_onces()
check("流し終わってから出す", any("留まることになった" in t for t in app.texts), app.texts)


def both_off(module):
    module.KEEP_PARTY = False
    module.STAY_NEARBY = False


app, ctx, mod, areas = setup(configure=both_off)
QuestEndManager(app).method_1("63")
check("両方切ればゲームのまま（雇った場所へ帰る）", app.party == ["player"]
      and [m["facility"] for m in app.moved] == [inn_of(areas, "7")[0]], names_of(app.moved))


def retire_nearby(module):
    module.KEEP_PARTY = False
    module.ALSO_ON_QUEST_RETIRE = True


app, ctx, mod, areas = setup(configure=retire_nearby)
QuestRetireManager(app).method_1("63")
check("放棄でも同じ扱いなら、放棄の解散でもギルドに置く",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))

# ================================================================== 会話の別れ
print("=== 会話の別れ：ボタン ===")
app, ctx, mod, areas = setup(here="7")
LEAVE, CONFIRM, CANCEL = mod.farewell.LEAVE_LABEL, mod.farewell.CONFIRM_LABEL, mod.farewell.CANCEL_LABEL
app.refresh_choice_buttons()
check("仲間との会話に「ここで別れる」が出る", index_of(app, LEAVE) >= 0, [b["text"] for b in app.buttons])
check("「会話を終了する」の手前に入る", index_of(app, LEAVE) == len(app.buttons) - 2, [b["text"] for b in app.buttons])
check("404_ が見分ける印（mod_party_action）が付く",
      app.buttons[index_of(app, LEAVE)].get("mod_party_action") == "confirm")
check("自前ボタンの spec は無害な既存クラス",
      app.buttons[index_of(app, LEAVE)]["spec"].cls_name == "JustSetButtonToNormalPhase")
count = len(app.buttons)
app.refresh_choice_buttons()
check("並べ直しても増えない", len(app.buttons) == count, len(app.buttons))

app, ctx, mod, areas = setup(here="7", partner="70")
app.refresh_choice_buttons()
check("話し相手が仲間でなければ出ない", index_of(app, LEAVE) < 0)

for flag in ("in_battle", "in_colosseum_battle", "in_boss_battle"):
    app, ctx, mod, areas = setup(here="7")
    setattr(app, flag, True)
    app.refresh_choice_buttons()
    check("{} 中は出ない".format(flag), index_of(app, LEAVE) < 0)

app, ctx, mod, areas = setup(here="7")
app.current_quest_data = {"quest_title": "テストの依頼"}
app.refresh_choice_buttons()
check("依頼の最中は出ない", index_of(app, LEAVE) < 0)

app, ctx, mod, areas = setup(here="7")
app.original_party = ["player"]
app.refresh_choice_buttons()
check("original_party が名簿と食い違っても出す（判定に使わない）", index_of(app, LEAVE) >= 0)


def no_farewell(module):
    module.FAREWELL_ENABLED = False


app, ctx, mod, areas = setup(here="7", configure=no_farewell)
app.refresh_choice_buttons()
check("会話の別れを切れば出ない", index_of(app, LEAVE) < 0)

print("=== 会話の別れ：確認 ===")
app, ctx, mod, areas = setup(here="7")
app.refresh_choice_buttons()
saved = [b["text"] for b in app.buttons]
app.on_button_press(index_of(app, LEAVE))
clock.run_onces()
check("押すと確認の選択肢になる", [b["text"] for b in app.buttons] == [CONFIRM, CANCEL], [b["text"] for b in app.buttons])
check("確認文が出る", any("別れることになる" in t for t in app.texts), app.texts)
check("画面のボタンも塗り替わる", app.hud.painted and app.hud.painted[-1] == [CONFIRM, CANCEL], app.hud.painted)
check("この時点ではまだ外していない", app.removed == [] and "63" in app.party)
app.on_button_press(index_of(app, CANCEL))
clock.run_onces()
check("やめると元のボタンに戻る", [b["text"] for b in app.buttons] == saved, [b["text"] for b in app.buttons])

app, ctx, mod, areas = setup(here="7")
app.refresh_choice_buttons()
game_list = [dict(b) for b in app.buttons if not choices.ours(b)]
app.on_button_press(index_of(app, LEAVE))
clock.run_onces()
written = []
save_hook = [hook for target, hook in ctx.hooks if target == "__main__:InstantaleApp.save_game"][-1]
save_hook(lambda self: written.append([b["text"] for b in self.buttons]), app)
check("確認の画面で保存すると、セーブには会話のボタンだけが残る", written == [["会話を終了する"]], written)
app.buttons = [dict(b) for b in game_list]
app.refresh_choice_buttons()
check("ロードの後も確認の画面に戻る", [b["text"] for b in app.buttons] == [CONFIRM, CANCEL], [b["text"] for b in app.buttons])
app.buttons = talk_buttons("70")
app.refresh_choice_buttons()
check("その仲間との会話でなくなったら確認の画面を閉じる",
      [b["text"] for b in app.buttons] == ["会話を終了する"] and choices.opened(app) is None,
      [b["text"] for b in app.buttons])

print("=== 会話の別れ：実行 ===")
app, ctx, mod, areas = setup(here="7")
app.refresh_choice_buttons()
app.on_button_press(index_of(app, LEAVE))
clock.run_onces()
app.on_button_press(index_of(app, CONFIRM))
check("会話はゲームの経路で閉じる", app.in_conversation is False)
check("end_text に別れた旨が入る", app.ended and "パーティ" in app.ended[0], app.ended)
check("会話が閉じるまでは外さない", app.removed == [])
clock.tick()
clock.run_onces()
check("外れた", app.removed == ["63"] and app.party == ["player"], (app.removed, app.party))
check("game_variables['party'] も同じ", app.game_variables["party"] == ["player"])
check("雇った場所へ置く（ノードも渡す）", app.moved and app.moved[0]["facility"] is inn_of(areas, "7")[0]
      and app.moved[0]["node"] is node_of(areas, "7"), names_of(app.moved))
check("別れの文が出る", any("パーティを離れ、テストの町Aの宿に残った" in t for t in app.texts), app.texts)
check("保存した", app.saves == 1, app.saves)
check("仲間欄を塗り直す", app.party_updates > 0 and app.hud.party_painted > 0, (app.party_updates, app.hud.party_painted))
check("例外は出ていない", ctx.errors == [], ctx.errors)

app, ctx, mod, areas = setup(here="7")
app.refresh_choice_buttons()
app.on_button_press(index_of(app, LEAVE))
clock.run_onces()
app.on_button_press(index_of(app, CONFIRM))
app.is_adding_text = True
clock.tick()
clock.run_onces()
check("別れの文が流れている間は仲間欄を塗らない", app.party_updates == 0 and app.hud.party_painted == 0,
      (app.party_updates, app.hud.party_painted))
app.is_adding_text = False
clock.tick()
clock.run_onces()
check("流し終わってから塗る", app.party_updates > 0 and app.hud.party_painted > 0)

app, ctx, mod, areas = setup(here="7")
app.game_moves_on_remove = True
part_ways(app, mod)
check("ゲームが自分で置いたなら二重に置かない", len(app.moved) == 1 and app.party == ["player"], app.moved)

app, ctx, mod, areas = setup(here="7")
app.remove_keeps_member = True
part_ways(app, mod)
check("remove_party_member が名簿に残したらこちらで落とす", app.party == ["player"], app.party)
check("  → その旨をログに残す", "WARN remove_party_member left" in read_log())

app, ctx, mod, areas = setup(here="7")
QuestEndManager(app).method_1("63")              # 解散しないで引き留めた直後
part_ways(app, mod)
check("引き留めた仲間とも会話で別れられ、置き直しも止めない",
      app.party == ["player"] and [m["facility"] for m in app.moved] == [inn_of(areas, "7")[0]],
      (app.party, names_of(app.moved)))

app, ctx, mod, areas = setup(here="9", configure=no_keep)
part_ways(app, mod)
app.roster.append("63")                            # 雇い直した
app.game_asks_leave_facility = False               # 置き直しの差し替えだけが頼りのビルド
QuestEndManager(app).method_1("63")
check("会話で別れた後の控えは片付ける（雇い直した後の解散でも置き先を差し替える）",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0], guild_of(areas, "9")[0]],
      names_of(app.moved))

app, ctx, mod, areas = setup(here="7")
app.refresh_choice_buttons()
app.on_button_press(index_of(app, LEAVE))
clock.run_onces()
app.in_conversation = False                       # 終了処理が先に走っていた
app.on_button_press(index_of(app, CONFIRM))
clock.tick()
clock.run_onces()
check("会話が閉じていてもそのまま外せる", app.party == ["player"], app.party)

app, ctx, mod, areas = setup(here="7")
app.leave_facility = None
app.world.areas = {}
app.player.current_area = None
part_ways(app, mod)
check("置き先が無ければ外さず、会話も閉じない", app.removed == [] and app.in_conversation is True,
      (app.removed, app.in_conversation))
check("断り文句を出す", any(mod.farewell.NO_PLACE_TEXT in t for t in app.texts), app.texts)

from instantale_modloader import ui as _ui_module  # noqa: E402

app, ctx, mod, areas = setup(here="7")
app.refresh_choice_buttons()
app.on_button_press(index_of(app, LEAVE))
clock.run_onces()
real_end_conversation = _ui_module.Screen.end_conversation


def broken_end_conversation(self, *args, **kwargs):
    raise RuntimeError("cannot build the end button")


_ui_module.Screen.end_conversation = broken_end_conversation
try:
    app.on_button_press(index_of(app, CONFIRM))
    clock.tick()
    clock.run_onces()
finally:
    _ui_module.Screen.end_conversation = real_end_conversation
check("会話を閉じに行く前に投げても外さない", app.removed == [] and app.party == ["player", "63"])
check("例外は記録に残り、断り文句を出す", any("cannot start the farewell" in e for e in ctx.errors)
      and any(mod.farewell.FAILED_TEXT in t for t in app.texts), ctx.errors)
check("押す前の選択肢に戻る", index_of(app, LEAVE) >= 0, [b.get("text") for b in app.buttons])
part_ways(app, mod)
check("実行中の印が戻っているので、もう一度押せば別れられる", app.party == ["player"], app.party)

print("=== 会話の別れ：置き先 ===")
app, ctx, mod, areas = setup(here="9")
part_ways(app, mod)
check("土地を跨いだら、いまの町のギルドへ", app.moved and app.moved[0]["facility"] is guild_of(areas, "9")[0],
      names_of(app.moved))

app, ctx, mod, areas = setup(here="7")
node_of(areas, "7").facilities.pop("71")
part_ways(app, mod)
check("雇った場所が引けなければ、いまの町のギルドへ", app.moved and app.moved[0]["facility"] is guild_of(areas, "7")[0],
      names_of(app.moved))

app, ctx, mod, areas = setup(here="9")
app.player.current_area = "9"
part_ways(app, mod)
check("いまの土地が id の文字列でも引き当てる", app.moved and app.moved[0]["facility"] is guild_of(areas, "9")[0],
      names_of(app.moved))

app, ctx, mod, areas = setup(here="7")
app.world.areas = {}
app.player.current_area = None
part_ways(app, mod)
check("土地が引けなければゲームの置き先（タプルはほどく）", app.moved and app.moved[0]["facility"] is app.leave_facility[0]
      and app.moved[0]["node"] is app.leave_facility[1], names_of(app.moved))


def not_nearby(module):
    module.STAY_NEARBY = False


app, ctx, mod, areas = setup(here="9", configure=not_nearby)
part_ways(app, mod)
check("近くに残す設定を切れば、土地を跨いでもゲームの置き先（雇った場所）",
      app.moved and app.moved[0]["facility"] is inn_of(areas, "7")[0], names_of(app.moved))

print("=== 会話の別れ：名簿の在り処 ===")
app, ctx, mod, areas = setup(here="7", shape="split")
part_ways(app, mod)
check("app.party が空の別物でも game_variables 側から外す", app.roster == ["player"] and app.party == [],
      (app.roster, app.party))

app, ctx, mod, areas = setup(here="7", shape="instances")
part_ways(app, mod)
check("名簿にインスタンスが並んでいても外す", [member_key(v) for v in app.roster] == ["player"], app.roster)

app, ctx, mod, areas = setup(here="7", shape="dict")
part_ways(app, mod)
check("名簿が {id: Character} の辞書でも外す", list(app.roster) == ["player"], app.roster)

app, ctx, mod, areas = setup(here="7", party=("player",))
app.escaped_member_in_battle = ["player", "63"]
app.surrendered_characters = ["player", "63"]
app.refresh_choice_buttons()
check("party 以外の配列を名簿と間違えない", index_of(app, LEAVE) < 0)

print("=== ほかの MOD のボタン ===")
app, ctx, mod, areas = setup(here="7")
app.buttons = [{"text": "1000ゴールドを納める", "mod_pardon_action": "pay", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
               {"text": "やめておく", "mod_pardon_action": "cancel", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
app.refresh_choice_buttons()
check("ほかの MOD の確認画面からボタンを消さない",
      [b["text"] for b in app.buttons] == ["1000ゴールドを納める", "やめておく"], [b["text"] for b in app.buttons])
check("掃除に使う文言はこちらにしか無いものだけ", CANCEL not in mod.farewell.OUR_LABELS, mod.farewell.OUR_LABELS)

app, ctx, mod, areas = setup(here="7")
app.buttons = [{"text": LEAVE, "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}] + talk_buttons("63")
app.refresh_choice_buttons()
same = [b for b in app.buttons if b["text"] == LEAVE]
check("古い版がセーブに焼いた「ここで別れる」は外して組み直す", len(same) == 1 and choices.ours(same[0]),
      [b["text"] for b in app.buttons])

print()
if failures:
    print("失敗 {} 件: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
