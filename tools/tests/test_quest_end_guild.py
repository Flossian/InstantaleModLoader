# -*- coding: utf-8 -*-
"""303_quest_end_party_to_guild.py をゲーム抜きで通す。

    python tools/tests/test_quest_end_guild.py

偽の app / QuestEndManager / QuestRetireManager / Area / Facility /
Clock を差し込み、次を確認する。

  検出   … クエストクリアの解散だけを捕まえる（死別・放棄・普段の移動は素通り）
  置き先 … `get_party_leave_facility` がいまの町のギルドを**元の形のまま**返す
  差し替え… 解散した相手の `move_npc_to_facility` だけ置き先が変わる
  例外   … ギルドが無い土地では何もしない（ゲーム本来の置き先に任せる）
  保険   … 誰も動かさなければ時間切れでこちらが置いて保存する
  画面   … 行き先が `add_text` に出る（要約の流し込み中は割り込まない）
  共存   … `302_` のフックと重ねても両方効く

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


MOD = find_mod("_quest_end_party_to_guild")
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
    # ギルドを**末尾**に置くのは、
    # 順番で当たってしまう実装を弾くため（`facility_type` を見ずに先頭を拾うと宿になる）。
    facilities = [Facility(id=area_id + "1", name=name + "の宿", facility_type="inn"),
                  Facility(id=area_id + "2", name=name + "の店",
                           facility_type="general_store")]
    if guild:
        facilities.append(Facility(id=area_id + "9", name=name + "のギルド",
                                   facility_type="guild"))
    return Area(area_id, name, facilities)


def guild_of(areas, area_id):
    """そのエリアのギルド `(施設, ノード)`。無ければ `(None, None)`。"""
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
    """`get_party_leave_facility` の戻り値をほどく。

    実機は `(施設, ノード)` のタプルを返し、
    `move_npc_to_facility` は施設とノードを**別々に**取る（`302_` がタプルのまま渡して落ちた）。
    呼び出し元のゲームは必ずここを通っているはずなので、偽ゲームも同じ形にする。
    """
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
        # 実装の分からない部分を切り替えるつまみ（本番では観測して決まる）。
        self.leave_facility = None       # setup で入れる（雇った町のギルド）
        self.leave_shape = "tuple"
        self.leave_answer = None         # 解散処理が受け取った答え
        self.game_places_after_remove = True
        # ゲームが置き先を `get_party_leave_facility` に聞くかどうかは**未観測**。
        # 聞かずに自分で決めているビルドでも効かなければならない（差し替えの第2層）。
        self.game_asks_leave_facility = True

    def add_text(self, context):
        self.texts.append(context)

    def get_party_leave_facility(self, character_instance):
        facility, node = self.leave_facility
        if self.leave_shape == "tuple":
            return (facility, node)
        if self.leave_shape == "list":
            return [facility, node]
        if self.leave_shape == "single":
            return facility
        raise RuntimeError("the game cannot decide where to put them")

    def move_npc_to_facility(self, character_id, character_instance, target_facility,
                             target_node=None, register_facility=True):
        # タプルのまま渡されたら本物と同じように壊れる（実機の再現）。
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

        帰還 → 報酬 → `remove_party_member` → **呼び出し元が**置き直す

    置き直しが `remove_party_member` の後に来ることと、
    置き先を `get_party_leave_facility` に聞くことが、この mod の前提。
    """

    def __init__(self, app):
        self.app = app

    def method_1(self, member_id):
        app = self.app
        character = app.world.characters[str(member_id)]
        app.add_text("パーティは帰還した...")
        app.remove_party_member(member_id)
        app.add_text("{}はパーティから離脱した。".format(getattr(character, "name", "")))
        if not app.game_places_after_remove:
            return
        if app.game_asks_leave_facility:
            app.leave_answer = app.get_party_leave_facility(character)
            facility, node = unpack(app.leave_answer)
        else:
            # 聞かずに自分で決めるビルド。
            # 差し替えの第2層だけが頼りになる。
            facility, node = app.leave_facility
        app.move_npc_to_facility(member_id, character, facility, node)

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
        app.leave_answer = app.get_party_leave_facility(character)
        facility, node = unpack(app.leave_answer)
        app.move_npc_to_facility(member_id, character, facility, node)


class DeathManager(QuestRetireManager):
    """死別。解散マネージャではないので素通りしなければならない。"""

    def method_1(self, member_id):
        return QuestRetireManager.method_1(self, member_id)


# 派生元は名前ではなくここから引く（`302_` の検証と同じ理由。
# 直接実行時の `sys.modules['__main__']` はこのテスト自身なので、
# `main.InstantaleApp = cls` がここのグローバル名を書き換えてしまう）。
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
        # once が once を積むことがあるので、積まれなくなるまで回す。
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
    辞書で持つと後から当てた mod が前のものを消してしまい、
    重ね掛けを検証できない（`302_` と `303_` は3つの対象を共有している）。
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


# 偽 app が持っているメソッドだけを差し替える（`302_` は会話画面のフックも当てるが、
# こちらの偽 app には無いので飛ばす）。
HOOK_NAMES = {
    "__main__:InstantaleApp.remove_party_member": "remove_party_member",
    "__main__:InstantaleApp.get_party_leave_facility": "get_party_leave_facility",
    "__main__:InstantaleApp.move_npc_to_facility": "move_npc_to_facility",
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


OUT_DIR = os.path.join(os.environ.get("TEMP", HERE), "instantale_test_quest_end")


def setup(here="9", home="7", guild_here=True, member="71", extra_mods=()):
    """mod を適用し、エリア `here` に居る状態の app を返す。

    `home` は雇った町（ゲーム本来の置き先が指す方）。
    `here != home` が「土地を跨いだままクエストを終えた」＝この mod が効く状況。
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
             "9": build_area("9", "テストの町B", guild=guild_here),
             "13": build_area("13", "忘れられた坑道", guild=False)}
    character = Character(id=member, name="テスト仲間C")
    player = Character(id="player", name="テストプレイヤー", current_area=areas[here])
    world = World({member: character, "player": player}, areas)
    app = app_cls(world, ["player", member], player)
    # ゲーム本来の置き先＝雇った町のギルド（実測。無い土地なら宿で代用）。
    app.leave_facility = guild_of(areas, home) if guild_of(areas, home)[0] \
        else inn_of(areas, home)

    ctx = FakeCtx(OUT_DIR)
    module = load_mod(MOD, "quest_end_guild_mod")
    module.apply(ctx)
    for extra in extra_mods:
        load_mod(extra, "extra_" + os.path.basename(extra)[:3]).apply(ctx)
    install(ctx.hooks, app_cls)
    return app, ctx, clock, module, areas


# ================================================================== 検証
print("=== 解散の検出 ===")
app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
check("クエストクリアの解散を捕まえる", app.removed == ["71"], app.removed)
check("パーティから外れている", app.party == ["player"], app.party)
check("例外を1つも出していない", not ctx.errors, ctx.errors)
check("いまの町のギルドに置かれる",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))
check("ノードも一緒に渡している", app.moved[0]["node"] is guild_of(areas, "9")[1])
check("NPC の現在地も書き換わる",
      app.world.characters["71"].current_location is guild_of(areas, "9")[0])

app, ctx, clock, mod, areas = setup()
DeathManager(app).method_1("71")
check("死別は素通りする（雇った町のまま）",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
QuestRetireManager(app).method_1("71")
check("クエスト放棄は既定では捕まえない",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
app.remove_party_member("71")
app.move_npc_to_facility("71", app.world.characters["71"], inn_of(areas, "7")[0])
check("解散処理の外からの removal は控えない",
      [m["facility"] for m in app.moved] == [inn_of(areas, "7")[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
app.move_npc_to_facility("71", app.world.characters["71"], inn_of(areas, "9")[0])
check("普段の NPC の移動は素通りする",
      [m["facility"] for m in app.moved] == [inn_of(areas, "9")[0]], names_of(app.moved))

print("=== 置き先（get_party_leave_facility） ===")
app, ctx, clock, mod, areas = setup()
plain = app.get_party_leave_facility(app.world.characters["71"])
check("解散の外ではゲームの答えをそのまま返す", plain[0] is app.leave_facility[0],
      getattr(plain[0], "name", plain))

app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
check("解散中はいまの町のギルドを返す",
      app.leave_answer[0] is guild_of(areas, "9")[0],
      getattr(app.leave_answer[0], "name", app.leave_answer))
check("タプルの形を保つ",
      isinstance(app.leave_answer, tuple) and len(app.leave_answer) == 2,
      app.leave_answer)
check("ノードもいまの町のもの", app.leave_answer[1] is guild_of(areas, "9")[1])

app, ctx, clock, mod, areas = setup()
app.leave_shape = "list"
QuestEndManager(app).method_1("71")
check("リストで返すビルドにはリストで返す", isinstance(app.leave_answer, list),
      type(app.leave_answer).__name__)
check("  → 置き先はやはりギルド",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
app.leave_shape = "single"
QuestEndManager(app).method_1("71")
check("施設1つで返すビルドには施設1つで返す",
      app.leave_answer is guild_of(areas, "9")[0], app.leave_answer)

app, ctx, clock, mod, areas = setup()
app.leave_shape = "boom"          # ゲーム側が置き先を出せない
QuestEndManager(app).method_1("71")
check("ゲームが答えられなくても置き先を出す",
      isinstance(app.leave_answer, tuple)
      and app.leave_answer[0] is guild_of(areas, "9")[0], app.leave_answer)

app, ctx, clock, mod, areas = setup()
app.leave_shape = "boom"
raised = None
try:
    app.get_party_leave_facility(app.world.characters["71"])
except Exception as exc:
    raised = exc
check("解散の外でのゲームの失敗は握り潰さない", isinstance(raised, RuntimeError), raised)

print("=== 差し替え（move_npc_to_facility） ===")
# ここが本命の第2層。
# ゲームが置き先を聞かずに自分で決めているビルドでも、
# 実際に動かす呼び出しを捕まえて差し替えられなければならない。
app, ctx, clock, mod, areas = setup()
app.game_asks_leave_facility = False
QuestEndManager(app).method_1("71")
check("置き先を聞かないビルドでも差し替わる",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))
check("  → ノードもいまの町のもの", app.moved[0]["node"] is guild_of(areas, "9")[1])
clock.run_onces()
check("  → 行き先も画面に出る", any("テストの町Bのギルド" in t for t in app.texts), app.texts)

app, ctx, clock, mod, areas = setup(here="13")
app.game_asks_leave_facility = False
QuestEndManager(app).method_1("71")
check("聞かないビルドでもギルドが無ければ触らない",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))

app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
check("差し替えは1回だけ", len(app.moved) == 1, app.moved)
check("register_facility の既定は変わらない", app.moved[0]["register_facility"] is True)

app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
app.move_npc_to_facility("71", app.world.characters["71"], inn_of(areas, "9")[0])
check("解散の後の普段の移動までは追いかけない",
      app.moved[-1]["facility"] is inn_of(areas, "9")[0], names_of(app.moved))

print("=== ギルドが無い土地 ===")
app, ctx, clock, mod, areas = setup(here="13")
QuestEndManager(app).method_1("71")
check("ダンジョンで解散したら差し替えない（ゲーム本来の置き先）",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))
check("そのときも例外は出ない", not ctx.errors, ctx.errors)

app, ctx, clock, mod, areas = setup(guild_here=False)
QuestEndManager(app).method_1("71")
check("いまの町にギルドが無い場合も同じ",
      [m["facility"] for m in app.moved] == [app.leave_facility[0]], names_of(app.moved))

print("=== エリアを id で持っている場合 ===")
app, ctx, clock, mod, areas = setup()
app.player.current_area = "9"      # Area ではなく id の文字列
QuestEndManager(app).method_1("71")
check("id の文字列でも引き当てる",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))

print("=== 保険（誰も動かさなかったとき） ===")
app, ctx, clock, mod, areas = setup()
app.game_places_after_remove = False
QuestEndManager(app).method_1("71")
check("ゲームが動かさなければ移動はまだ無い", app.moved == [], app.moved)
clock.run_onces()
check("時間切れでこちらが置く",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))
check("自分で置いたときは保存する", app.saves == 1, app.saves)
clock.run_onces()
check("時間切れは1回しか働かない", len(app.moved) == 1, app.moved)

app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
clock.run_onces()
check("ゲームが置いた後は時間切れが何もしない", len(app.moved) == 1, app.moved)
check("そのときは保存もしない", app.saves == 0, app.saves)

print("=== 画面 ===")
app, ctx, clock, mod, areas = setup()
QuestEndManager(app).method_1("71")
clock.run_onces()
check("行き先が画面に出る",
      any("テストの町Bのギルド" in text for text in app.texts), app.texts)
check("名前も入っている",
      any("テスト仲間Cは" in text for text in app.texts), app.texts)

app, ctx, clock, mod, areas = setup()
app.is_adding_text = True          # 要約の流し込み中
QuestEndManager(app).method_1("71")
clock.run_onces()
check("流し込み中は割り込まない",
      not any("留まることになった" in text for text in app.texts), app.texts)
app.is_adding_text = False
clock.tick()
clock.run_onces()
check("手が空いてから出す",
      any("留まることになった" in text for text in app.texts), app.texts)

app, ctx, clock, mod, areas = setup(here="13")
QuestEndManager(app).method_1("71")
clock.run_onces()
check("差し替えなかったときは何も言わない",
      not any("留まることになった" in text for text in app.texts), app.texts)

print("=== 302_ との共存 ===")
leave_mod = load_mod(LEAVE_MOD, "leave_mod_probe")
check("同じログに書く（時系列を1本にする）",
      mod.LOG_BASENAME == leave_mod.LOG_BASENAME, mod.LOG_BASENAME)
app, ctx, clock, mod, areas = setup(extra_mods=(LEAVE_MOD,))
QuestEndManager(app).method_1("71")
check("302_ のフックと重ねても差し替えは効く",
      [m["facility"] for m in app.moved] == [guild_of(areas, "9")[0]], names_of(app.moved))
check("重ねても例外は出ない", not ctx.errors, ctx.errors)
check("302_ の記録も走っている（removal を数えている）", app.removed == ["71"], app.removed)

print()
if failures:
    print("失敗 {} 件: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
