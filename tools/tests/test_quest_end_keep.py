# -*- coding: utf-8 -*-
"""304_quest_end_keep_party.py をゲーム抜きで通す。

    python tools/tests/test_quest_end_keep.py

偽の app / QuestEndManager / QuestRetireManager / Area / Facility /
Clock を差し込み、次を確認する。

  引き留め … クエストクリアの解散だけを止める（名簿は元のまま・置き直しも起きない）
  文言     … 「…はパーティから離脱した。」を残った旨に差し替える
  素通り   … 死別・クエスト放棄・普段の NPC 移動・解散の外からの removal は触らない
  取り違え … 本当に外れた相手の置き直しは**止めない**（世界から消さないため）
  順序     … 置き先を聞いてから外すビルドでも取りこぼさない
  共存     … `303_` と重ねると `304_` が勝つ（誰も外れないので置き直しも走らない）

**解散の検出はスタックのコードオブジェクトで行う**ので、
テストも `QuestEndManager.method_1` の**中から**呼ぶ形にしてある（app のメソッドを直接叩くと本番と違う経路になり、
検出そのものを検証できない）。

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD = find_mod("_quest_end_keep_party")
GUILD_MOD = find_mod("_quest_end_party_to_guild")
LEAVE_MOD = find_mod("_leave_party_in_conversation")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def names_of(moves):
    """失敗したときに読める形にする（施設は repr が効かないため）。"""
    return [getattr(m["facility"], "name", m["facility"]) for m in moves]


# ---------------------------------------------------------------- 偽ゲーム
# ここで定義したクラスは __main__ の属性になる。
# mod は `getattr(sys.modules['__main__'], 名前)` で引くので、
# これで本番と同じ形になる。
class Character:
    def __init__(self, **kw):
        self.current_location = None
        self.__dict__.update(kw)


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
                  Facility(id=area_id + "2", name=name + "の店",
                           facility_type="general_store")]
    if guild:
        facilities.append(Facility(id=area_id + "9", name=name + "のギルド",
                                   facility_type="guild"))
    return Area(area_id, name, facilities)


def guild_of(areas, area_id):
    node = areas[area_id].nodes[area_id + "0"]
    for facility in node.facilities.values():
        if facility.facility_type == "guild":
            return facility, node
    return None, None


def inn_of(areas, area_id):
    node = areas[area_id].nodes[area_id + "0"]
    return node.facilities[area_id + "1"], node


class World:
    def __init__(self, characters, areas):
        self.characters = characters
        self.areas = areas


def unpack(answer):
    """`get_party_leave_facility` の戻り値をほどく（実機は `(施設, ノード)`）。"""
    if isinstance(answer, (tuple, list)):
        if len(answer) >= 2:
            return answer[0], answer[1]
        return answer[0], None
    return answer, None


class InstantaleApp:
    def __init__(self, world, party, player):
        self.world = world
        self.party = party
        self.game_variables = {"party": party}
        self.player = player
        self.texts = []
        self.moved = []
        self.removed = []
        self.saves = 0
        self.leave_facility = None       # setup で入れる（雇った町のギルド）
        self.leave_answer = None
        self.game_places_after_remove = True
        self.game_asks_leave_facility = True
        # 置き直しが removal より**先**に来るビルド（未観測。
        # 取りこぼし防止の検証用）。
        self.places_before_remove = False

    def add_text(self, context):
        self.texts.append(context)

    def get_party_leave_facility(self, character_instance):
        facility, node = self.leave_facility
        return (facility, node)

    def move_npc_to_facility(self, character_id, character_instance, target_facility,
                             target_node=None, register_facility=True):
        if isinstance(target_facility, (tuple, list)):
            raise AttributeError("'tuple' object has no attribute 'characters'")
        self.moved.append({"id": str(character_id), "facility": target_facility,
                           "node": target_node,
                           "register_facility": register_facility})
        if character_instance is not None:
            character_instance.current_location = target_facility

    def remove_party_member(self, member_id):
        self.removed.append(str(member_id))
        self.party[:] = [v for v in self.party if v != str(member_id)]

    def save_game(self):
        self.saves += 1


class QuestEndManager:
    """クエストクリア。実測の順序をそのまま真似る（GAME.md §2.8）:

        帰還 → 報酬 → `remove_party_member` → 離脱の文 → **呼び出し元が**置き直す
    """

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
        app.move_npc_to_facility(member_id, character, facility, node)

    def method_1(self, member_id):
        app = self.app
        character = app.world.characters[str(member_id)]
        app.add_text("パーティは帰還した...")
        app.add_text("9475ゴールドの報酬を受け取った。")
        if app.places_before_remove:
            # 置き先を決めてから外すビルド。
            # 実測のものとは順序が逆。
            self.place(member_id, character)
            app.remove_party_member(member_id)
            app.add_text("{}はパーティから離脱した。".format(
                getattr(character, "name", "")))
            return
        app.remove_party_member(member_id)
        app.add_text("{}はパーティから離脱した。".format(getattr(character, "name", "")))
        self.place(member_id, character)

    def execute(self, choice_text):
        return self.method_1(choice_text)


class QuestRetireManager(QuestEndManager):
    """クエスト放棄。既定では捕まえない側（`ALSO_ON_QUEST_RETIRE`）。

    **`method_1` を上書きする**こと ― 継承したままだとコードオブジェクトが
    `QuestEndManager.method_1` と同一になり、
    「別のマネージャなら捕まえない」を検証できない。
    """

    def method_1(self, member_id):
        app = self.app
        character = app.world.characters[str(member_id)]
        app.remove_party_member(member_id)
        app.add_text("{}はパーティから離脱した。".format(getattr(character, "name", "")))
        app.leave_answer = app.get_party_leave_facility(character)
        facility, node = unpack(app.leave_answer)
        app.move_npc_to_facility(member_id, character, facility, node)


class DeathManager(QuestRetireManager):
    """死別。解散マネージャではないので素通りしなければならない。"""

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


def install_fake_kivy():
    clock = FakeClock()
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    return clock


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx:
    """`ctx.wrap` の**順番を保つ**。

    本番のローダはファイル名順に mod を当て、後から当てたものが**外側**になる。
    `303_` と `304_` は3つの対象を共有していて、どちらが外側かが挙動を決めるので、
    ここを辞書にすると肝心の検証ができない。
    """

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = []          # [(target, func)] 当てた順
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.append((target, func))
            return func
        return decorator


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path,
                                            submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOOK_NAMES = {
    "__main__:InstantaleApp.remove_party_member": "remove_party_member",
    "__main__:InstantaleApp.get_party_leave_facility": "get_party_leave_facility",
    "__main__:InstantaleApp.move_npc_to_facility": "move_npc_to_facility",
    "__main__:InstantaleApp.add_text": "add_text",
}


def install(hooks, cls):
    """フックを本番と同じ形（メソッドの差し替え）で、**当てた順に**載せる。"""
    for target, hook in hooks:
        name = HOOK_NAMES.get(target)
        if name is None:
            continue
        original = getattr(cls, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(cls, name, make())


OUT_DIR = os.path.join(os.environ.get("TEMP", HERE), "instantale_test_quest_keep")
LOG_PATH = os.path.join(OUT_DIR, "party_leave.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(here="9", home="7", member="71", with_guild_mod=False, extra_mods=(),
          configure=None, configure_guild=None):
    """mod を適用し、エリア `here` に居る状態の app を返す。

    `with_guild_mod` は `303_` を**先に**当てる（本番の適用順と同じ ＝
    `304_` が外側）。
    共存の検証はこの順序でしか意味を持たない。

    設定は `configure` / `configure_guild` で **`apply()` の前に**入れること。
    見張る相手は `apply()` の時点で決まる（本番でも設定の変更は再注入で効く）。
    """
    clock = install_fake_kivy()
    main = sys.modules["__main__"]

    # クラスは毎回作り直す（前のテストで差し替えたメソッドを持ち越さないため）。
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    main.InstantaleApp = app_cls
    for name in ("QuestEndManager", "QuestRetireManager", "DeathManager",
                 "Area", "Node", "Facility", "Character", "World"):
        setattr(main, name, globals()[name])

    areas = {"7": build_area("7", "テストの町A"),
             "9": build_area("9", "テストの町B"),
             "13": build_area("13", "忘れられた坑道", guild=False)}
    character = Character(id=member, name="テスト仲間C")
    player = Character(id="player", name="テストプレイヤー", current_area=areas[here])
    world = World({member: character, "player": player}, areas)
    app = app_cls(world, ["player", member], player)
    app.leave_facility = guild_of(areas, home)

    ctx = FakeCtx(OUT_DIR)
    if with_guild_mod:
        guild = load_mod(GUILD_MOD, "quest_end_guild_mod")
        if configure_guild is not None:
            configure_guild(guild)
        guild.apply(ctx)
    for extra in extra_mods:
        load_mod(extra, "extra_" + os.path.basename(extra)[:3]).apply(ctx)
    module = load_mod(MOD, "quest_end_keep_mod")
    if configure is not None:
        configure(module)
    module.apply(ctx)
    install(ctx.hooks, app_cls)
    return app, ctx, clock, module, areas


# ================================================================== 検証
print("=== 解散しない ===")
app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
check("パーティから外れない", app.party == ["player", "71"], app.party)
check("ゲームの removal を通していない", app.removed == [], app.removed)
check("どこにも置かれない", app.moved == [], names_of(app.moved))
check("NPC の現在地も動いていない",
      app.world.characters["71"].current_location is None,
      app.world.characters["71"].current_location)
check("例外を1つも出していない", not ctx.errors, ctx.errors)
check("game_variables 側の名簿も元のまま",
      app.game_variables["party"] == ["player", "71"], app.game_variables["party"])
check("報酬などの流れは止めない",
      any("報酬を受け取った" in text for text in app.texts), app.texts)

log_text = read_log()
check("引き留めをログに残す", "stays in the party" in log_text)
check("その行に呼び出し元が入る", "QuestEndManager.method_1" in log_text)

print("=== 文言 ===")
app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
check("「離脱した」とは言わない",
      not any("離脱" in text for text in app.texts), app.texts)
check("残った旨を出す", any("パーティに残り" in text for text in app.texts), app.texts)
check("名前も入っている",
      any("テスト仲間C" in text for text in app.texts), app.texts)
check("差し替えは1文だけ",
      sum(1 for text in app.texts if "パーティに残り" in text) == 1, app.texts)

app, ctx, clock, mod, areas = setup()
mod.REPLACE_LEAVE_TEXT = False
try:
    QuestEndManager(app).method_1("71")
    check("REPLACE_LEAVE_TEXT=False ならゲームの文言のまま",
          any("離脱した" in text for text in app.texts), app.texts)
    check("  → それでも解散はしない", app.party == ["player", "71"], app.party)
finally:
    mod.REPLACE_LEAVE_TEXT = True

app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
app.add_text("旅人はパーティから離脱した。")
check("引き留めていない相手の離脱文は書き換えない",
      app.texts[-1] == "旅人はパーティから離脱した。", app.texts[-1])

print("=== 素通り ===")
app, ctx, clock, mod, areas = setup()
DeathManager(app).method_1("71")
check("死別は素通りする（外れる）", app.removed == ["71"], app.removed)
check("  → 置き直しも通す",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
QuestRetireManager(app).method_1("71")
check("クエスト放棄は既定では止めない", app.removed == ["71"], app.removed)
check("  → 置き直しも通す",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))

def enable_retire(module):
    module.ALSO_ON_QUEST_RETIRE = True


app, ctx, clock, mod, areas = setup(configure=enable_retire)
QuestRetireManager(app).method_1("71")
check("ALSO_ON_QUEST_RETIRE=True なら放棄でも残る",
      app.party == ["player", "71"], app.party)
check("  → そのときも置かれない", app.moved == [], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
app.remove_party_member("71")
check("解散の外からの removal は止めない", app.removed == ["71"], app.removed)
check("  → 名簿からも消える", app.party == ["player"], app.party)

app, ctx, clock, mod, areas = setup()
app.move_npc_to_facility("71", app.world.characters["71"], inn_of(areas, "9")[0])
check("普段の NPC の移動は素通りする",
      [m["facility"] for m in app.moved] == [inn_of(areas, "9")[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
app.add_text("パーティは帰還した...")
check("普段の描画にも触らない", app.texts == ["パーティは帰還した..."], app.texts)

print("=== 本当に外れる相手は邪魔しない ===")
app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
app.remove_party_member("71")                    # `302_` の「ここで別れる」相当
check("引き留めた後でも、別経路の removal は通す", app.removed == ["71"], app.removed)
check("  → 名簿から消える", app.party == ["player"], app.party)
app.move_npc_to_facility("71", app.world.characters["71"], inn_of(areas, "9")[0])
check("  → その置き直しは止めない（世界から消さない）",
      [m["facility"] for m in app.moved] == [inn_of(areas, "9")[0]], names_of(app.moved))
check("  → その旨をログに残す", "is leaving for real" in read_log())

app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
mod.KEEP_WINDOW = 0.0
try:
    time.sleep(0.01)
    app.move_npc_to_facility("71", app.world.characters["71"], inn_of(areas, "9")[0])
    check("控えが切れたら普通に動かせる",
          [m["facility"] for m in app.moved] == [inn_of(areas, "9")[0]], names_of(app.moved))
finally:
    mod.KEEP_WINDOW = 30.0

print("=== 置き先を先に聞くビルド（未観測。取りこぼし防止） ===")
app, ctx, clock, mod, areas = setup()
app.places_before_remove = True
QuestEndManager(app).method_1("71")
check("聞いてから外すビルドでも置かれない", app.moved == [], names_of(app.moved))
check("  → 解散もしない", app.party == ["player", "71"], app.party)
check("  → 例外も出ない", not ctx.errors, ctx.errors)

app, ctx, clock, mod, areas = setup()
app.places_before_remove = True
app.game_asks_leave_facility = False        # 聞かずに自分で決めるビルド
QuestEndManager(app).method_1("71")
check("聞かずに先に置くビルドだけは間に合わない（記録が残る）",
      len(app.moved) == 1, names_of(app.moved))
check("  → それでも解散はしない", app.party == ["player", "71"], app.party)

print("=== 303_ との共存（304_ が勝つ） ===")
keep_mod = load_mod(MOD, "keep_mod_probe")
guild_mod = load_mod(GUILD_MOD, "guild_mod_probe")
check("同じログに書く（時系列を1本にする）",
      keep_mod.LOG_BASENAME == guild_mod.LOG_BASENAME, keep_mod.LOG_BASENAME)
check("ログのタグは別（どちらが働いたか分かる）",
      keep_mod.LOG_TAG != guild_mod.LOG_TAG, keep_mod.LOG_TAG)

app, ctx, clock, mod, areas = setup(with_guild_mod=True)
QuestEndManager(app).method_1("71")
check("303_ と重ねてもパーティから外れない", app.party == ["player", "71"], app.party)
check("ギルドにも置かれない", app.moved == [], names_of(app.moved))
check("例外も出ない", not ctx.errors, ctx.errors)
clock.run_onces()
check("303_ の時間切れの保険も働かない", app.moved == [], names_of(app.moved))
check("  → 保存も走らない", app.saves == 0, app.saves)
check("行き先の案内も出ない",
      not any("留まることになった" in text for text in app.texts), app.texts)
check("残った旨は出る", any("パーティに残り" in text for text in app.texts), app.texts)

# `304_` が降りた場面では `303_` が本来どおり働かなければならない。
# クエスト**放棄**を `303_` にだけ見せる（`304_` は既定のまま ＝ 放棄には手を出さない）と、「外れるが、
# 置き先はいまの町のギルド」という `303_` 単体の挙動になる。
app, ctx, clock, mod, areas = setup(with_guild_mod=True,
                                    configure_guild=enable_retire)
QuestRetireManager(app).method_1("71")
check("304_ が降りた場面では 303_ がいつも通り働く",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))
check("  → そちらは外れる（303_ は解散を止めない）", app.party == ["player"], app.party)

app, ctx, clock, mod, areas = setup(with_guild_mod=True)
DeathManager(app).method_1("71")
check("死別はどちらも触らない（ゲーム本来の置き先）",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup(with_guild_mod=True, extra_mods=(LEAVE_MOD,))
QuestEndManager(app).method_1("71")
check("302_ / 303_ と3枚重ねても解散しない",
      app.party == ["player", "71"] and app.moved == [], app.party)
check("3枚重ねても例外は出ない", not ctx.errors, ctx.errors)

print()
if failures:
    print("失敗 {} 件: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
