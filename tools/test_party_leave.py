# -*- coding: utf-8 -*-
"""302_leave_party_in_conversation.py をゲーム抜きで通す。

    python tools/test_party_leave.py

偽の app / Character / Facility / PhaseSpec / ConversationEndManager / Clock を
差し込み、次を確認する。

  設置   … 仲間との会話にだけ「ここで別れる」が出る（他人・戦闘中・クエスト中は出ない）
  確認   … 押すと確認の選択肢になり、やめると元のボタンに戻る
  実行   … 会話をゲームの経路で閉じてから外す・置き直す・保存する
  例外   … 置き場所が無い土地では外さない／ゲームが自分で置いたら二重に置かない
           ／`remove_party_member` が名簿を残したらこちらで落とす

`301_` と印のキーが衝突していないことも見る（衝突すると向こうの
`on_button_press` がこちらのボタンを握り潰す）。

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

# mod は `instantale_modloader.frames` を使う（ゲームの中では runtime/ が
# sys.path に入っている）。オフラインでも同じように見えるようにする。
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


MOD = find_mod("_leave_party_in_conversation")
QUEST_MOD = find_mod("_quest_from_conversation")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
# ここで定義したクラスは __main__ の属性になる。mod は
# `getattr(sys.modules['__main__'], 名前)` で引くので、これで本番と同じ形になる。
class Character:
    def __init__(self, **kw):
        self.display_position_in_battle = None
        self.current_location = None
        self.__dict__.update(kw)


def member_key(value):
    """名簿の要素の id。文字列でもインスタンスでも同じように引く。"""
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
                  Facility(id=area_id + "2", name=name + "の店",
                           facility_type="general_store")]
    if guild:
        facilities.append(Facility(id=area_id + "9", name=name + "のギルド",
                                   facility_type="guild"))
    return Area(area_id, name, facilities)


class World:
    def __init__(self, characters, areas=None):
        self.characters = characters
        self.areas = areas or {}


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    """自前ボタンに持たせる無害な spec の相手。mod 無しで押されても害が無い。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class ConversationEndManager:
    """会話を閉じる。本番と同じく in_conversation を落とすところまでを真似る。"""

    def __init__(self, app, in_conversation_id, finisher, end_text):
        self.app = app
        self.in_conversation_id = in_conversation_id
        self.finisher = finisher
        self.end_text = end_text

    def execute(self, choice_text):
        self.app.in_conversation = False
        self.app.ended.append(self.end_text)
        return None


class InstantaleApp:
    def __init__(self, world, party, decoy=None):
        self.world = world
        # セーブでは game_variables['party']。`app.party` が空のままのことが
        # あるので、名簿の在り処は形で決めつけない（GAME.md §2.8）。
        # decoy を渡すと「app.party は別物」の状況を再現できる。
        self.party = party if decoy is None else decoy
        self.game_variables = {"party": party}
        self.roster = party
        self.buttons = []
        self.display_button_map = None
        self.in_conversation = True
        self.in_battle = False
        self.in_colosseum_battle = False
        self.in_boss_battle = False
        self.current_quest_data = None
        self.original_party = []
        self.texts = []
        self.ended = []
        self.moved = []
        self.removed = []
        self.refreshes = 0
        self.saves = 0
        self.to_display_buttons = []
        self.loaded = []
        self.party_updates = 0
        self.hud = HUD()
        # 実装の分からない部分を切り替えるつまみ（本番では観測して決まる）。
        # 実機の `get_party_leave_facility` は (Facility, Node) のタプルを返す。
        self.leave_facility = (Facility(name="テストのギルド"), Node(name="受付"))
        self.game_moves_on_remove = False
        self.remove_keeps_member = False

    # -- ゲーム本来の実装 ---------------------------------------------------
    def add_text(self, context):
        self.texts.append(context)

    def process_choice(self, function, choice_text=""):
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        # 本物は to_display_buttons / display_button_map を組み直すところまで。
        # 画面に出る文字列はこちら。
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def update_party_member(self, dt):
        # ゲーム自身の仲間欄の更新（Clock コールバックの形）。
        self.party_updates += 1

    def display_button_load(self, dt):
        # ゲーム自身のボタン読み込み。呼ばれたことを控える。
        self.loaded.append(list(self.to_display_buttons))

    def on_button_press(self, button_index):
        entry = self.buttons[button_index]
        return ("game", entry.get("text"))

    def get_party_leave_facility(self, character_instance):
        return self.leave_facility

    def move_npc_to_facility(self, character_id, character_instance, target_facility,
                             target_node=None, register_facility=True):
        # タプルのまま渡されたら本物と同じように壊れる（実機の再現）。
        if isinstance(target_facility, (tuple, list)):
            raise AttributeError("'tuple' object has no attribute 'characters'")
        self.moved.append((str(character_id), target_facility, target_node))
        character_instance.current_location = target_facility

    def remove_party_member(self, member_id):
        self.removed.append(str(member_id))
        if self.game_moves_on_remove:
            # 解散処理が自分で置き直す場合（そうかどうかは実測でしか分からない）。
            self.move_npc_to_facility(member_id, self.world.characters[str(member_id)],
                                      self.leave_facility[0], self.leave_facility[1])
        if not self.remove_keeps_member:
            if isinstance(self.roster, dict):
                self.roster.pop(str(member_id), None)
            else:
                self.roster[:] = [v for v in self.roster
                                  if member_key(v) != str(member_id)]

    def add_party_member(self, character_id):
        if isinstance(self.roster, dict):
            self.roster[str(character_id)] = \
                self.world.characters[str(character_id)]
        else:
            self.roster.append(str(character_id))

    def process_party_member_choice(self, character_id):
        return ("talk", str(character_id))

    def save_game(self):
        self.saves += 1


# 派生元は名前ではなくここから引く。**直接実行時の `sys.modules['__main__']` は
# このテスト自身**なので、`main.InstantaleApp = app_cls` がここのグローバル名
# `InstantaleApp` を書き換えてしまう。素朴に `type("InstantaleApp",
# (InstantaleApp,), {})` と書くと2回目以降は「前回の派生クラス」から派生し、
# 前のテストで載せたフックの層が積み上がって同じ処理が何度も走る
# （`301_` の検証を書いていて実際に踏んだ）。
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


def install_fake_hud():
    """`scripts.hud.new_hud.InstanTaleHUD` を差し込む。

    mod は HUD を**属性名ではなく型**で探すので、型さえあれば見つかる。
    """
    name = "scripts.hud.new_hud"
    module = types.ModuleType(name)

    class InstanTaleHUD:
        def __init__(self):
            self.painted = []
            self.party_painted = 0

        def update_button_texts(self, instance, value):
            self.painted.append(list(value))

        def update_party_display(self, *args):
            # 仲間欄を塗る側。呼ばれた回数を控える。
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


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, msg):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    spec = importlib.util.spec_from_file_location("party_leave_mod", MOD,
                                            submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, cls):
    """フックを本番と同じ形（メソッドの差し替え）でクラスに載せる。"""
    for target, name in (
            ("__main__:InstantaleApp.refresh_choice_buttons", "refresh_choice_buttons"),
            ("__main__:InstantaleApp.on_button_press", "on_button_press"),
            ("__main__:InstantaleApp.remove_party_member", "remove_party_member"),
            ("__main__:InstantaleApp.move_npc_to_facility", "move_npc_to_facility"),
            ("__main__:InstantaleApp.add_party_member", "add_party_member"),
            ("__main__:InstantaleApp.process_party_member_choice",
             "process_party_member_choice")):
        hook = hooks.get(target)
        if hook is None:
            continue
        original = getattr(cls, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(cls, name, make())


def talk_buttons(partner_id, extra=None):
    """会話画面のボタン。実セーブと同じ形（args は id / finisher / end_text）。"""
    buttons = list(extra or [])
    buttons.append({"text": "会話を終了する",
                    "spec": PhaseSpec("ConversationEndManager",
                                      [partner_id, "user", "<行動: 会話を終了する>"])})
    return buttons


def setup(party=("player", "63"), partner="63", shape="shared"):
    """mod を適用し、仲間 63 と会話している状態の app を返す。

    `shape` は名簿の載り方。実行時にどれになるかは決めつけられない:

      shared    `app.party` と `game_variables['party']` が同じ配列
      split     `app.party` は空の別物で、本物は `game_variables['party']`
      instances 名簿に Character のインスタンスが並ぶ
      dict      名簿が `{id: Character}` の辞書（セーブに出る配列はキーの並び）
      bare      名簿がどこにも見つからない（**実機で起きた形**）
    """
    # クラスは毎回作り直す（前のテストで差し替えたメソッドを持ち越さないため）。
    # 派生元は `BASES` から引く（グローバル名は書き換わっている）。
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    sys.modules["__main__"].InstantaleApp = app_cls

    mod = load_mod()
    ctx = FakeCtx(os.path.join(HERE, os.pardir, "out", "test"))
    mod.apply(ctx)
    install(ctx.hooks, app_cls)

    # 7 = 雇用された町（初期位置）、9 = 別の町。どちらにもギルドがある。
    areas = {"7": build_area("7", "テストの町A"), "9": build_area("9", "テストの町B")}
    characters = {
        "63": Character(name="「試作」のテストB", id="63",
                        initial_location={"area": "7", "node": None,
                                          "facility": "71"}),
        "70": Character(name="「試作」のテストA", id="70",
                        initial_location={"area": "7", "node": None,
                                          "facility": "71"}),
    }
    members = list(party)
    if shape == "instances":
        members = [value if value == "player" else characters[value] for value in members]
    elif shape == "dict":
        members = dict((value, characters.get(value)) for value in members)
    app = app_cls(World(characters, areas), members,
                  decoy=[] if shape == "split" else None)
    app.player = Character(name="テストプレイヤー", id="player",
                           current_area=areas["7"])
    if shape == "dict":
        app.party = members
    if shape == "bare":
        # 名簿がどこにも無い状態。心当たりを全部空にする。
        app.party = None
        app.game_variables = {}
    app.buttons = talk_buttons(partner)
    return mod, ctx, app


def node_of(app, area_id):
    return list(app.world.areas[area_id].nodes.values())[0]


def facility_in(app, area_id, facility_id):
    return node_of(app, area_id).facilities.get(facility_id)


def index_of(app, mark):
    for i, entry in enumerate(app.buttons):
        if entry.get(app_mark) == mark:
            return i
    return -1


HUD = install_fake_hud()
clock = install_fake_kivy()
app_mark = load_mod().MARK

# ============================================================ 設置
print("=== ボタンの設置 ===")

mod, ctx, app = setup()
app.refresh_choice_buttons()
check("仲間との会話に「ここで別れる」が出る", index_of(app, "confirm") >= 0,
      [b["text"] for b in app.buttons])
check("「会話を終了する」の手前に入る",
      index_of(app, "confirm") == len(app.buttons) - 2,
      [b["text"] for b in app.buttons])
check("自前ボタンの spec は無害な既存クラス",
      app.buttons[index_of(app, "confirm")]["spec"].cls_name
      == "JustSetButtonToNormalPhase")
before = len(app.buttons)
app.refresh_choice_buttons()
check("並べ直しても増えない", len(app.buttons) == before, len(app.buttons))

mod, ctx, app = setup(party=("player",), partner="63")
app.refresh_choice_buttons()
check("仲間でない相手には出ない", index_of(app, "confirm") < 0)

mod, ctx, app = setup(partner="70")   # 70 は仲間ではない
app.refresh_choice_buttons()
check("同じ場に仲間が居ても、話し相手が他人なら出ない", index_of(app, "confirm") < 0)

for flag in ("in_battle", "in_colosseum_battle", "in_boss_battle"):
    mod, ctx, app = setup()
    setattr(app, flag, True)
    app.refresh_choice_buttons()
    check("{} 中は出ない".format(flag), index_of(app, "confirm") < 0)

mod, ctx, app = setup()
app.current_quest_data = {"quest_title": "瘴霧の夜警"}
app.refresh_choice_buttons()
check("クエスト中は出ない", index_of(app, "confirm") < 0)

# `original_party` は**判定に使わない**（GAME.md §2.8）。平常時も名簿と同じ内容で
# 入っており、雇用直後は控えが古いだけで食い違うので、どちらの読み方でも
# 「差し替え中」は判定できない。クエスト中は
# `current_quest_data` で断っているので、守りたい場面はそちらで足りる。
mod, ctx, app = setup()
app.original_party = ["player", "63"]          # 名簿と同じ
app.refresh_choice_buttons()
check("original_party が名簿と同じでも出る", index_of(app, "confirm") >= 0,
      (app.original_party, app.party))

mod, ctx, app = setup()
app.original_party = ["player"]                # 雇用直後（控えが古い）
app.refresh_choice_buttons()
check("original_party が古くても出る（雇用直後）", index_of(app, "confirm") >= 0,
      (app.original_party, app.party))

mod, ctx, app = setup()
app.original_party = ["player", "63", "70"]    # 名簿と食い違う
app.refresh_choice_buttons()
check("original_party が食い違っても出す（判定に使わない）",
      index_of(app, "confirm") >= 0, (app.original_party, app.party))

mod, ctx, app = setup()
app.original_party = ["player"]
app.refresh_choice_buttons()
log_text = open(os.path.join(HERE, os.pardir, "out", "test", mod.LOG_BASENAME),
                encoding="utf-8").read()
check("original_party の値は記録だけ続ける", "original_party=['player']" in log_text)

mod, ctx, app = setup()
app.buttons = [{"text": "水底管理局", "spec": PhaseSpec("MovePhaseManager", ["20", "126", "7"])}]
app.refresh_choice_buttons()
check("会話画面でなければ出ない", index_of(app, "confirm") < 0)

# ============================================================ 確認を挟む
print("=== 確認 ===")

mod, ctx, app = setup()
app.refresh_choice_buttons()
saved = list(app.buttons)
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
check("押すと確認の選択肢になる",
      [b["text"] for b in app.buttons] == [mod.CONFIRM_LABEL, mod.CANCEL_LABEL],
      [b["text"] for b in app.buttons])
check("確認文が出る", any("別れる" in t for t in app.texts), app.texts)
check("この時点ではまだ外していない", app.removed == [] and "63" in app.party)

# 差し替えは「次のフレーム・メインスレッド」で行う。押下と同じ流れの中で差し替えると
# app.buttons だけ変わって画面が古いまま残る（GAME.md §2.3）。
check("画面に出る文字列も入れ替わる",
      app.to_display_buttons == [mod.CONFIRM_LABEL, mod.CANCEL_LABEL],
      app.to_display_buttons)
# 塗るのは HUD 側の update_button_texts（GAME.md §2.3）。app.to_display_buttons を
# どう触っても画面は変わらないので、ここが呼ばれることが表示更新の条件になる。
check("ゲーム自身のボタン読み込みを呼ぶ",
      app.loaded and app.loaded[-1] == [mod.CONFIRM_LABEL, mod.CANCEL_LABEL],
      app.loaded)
check("HUD の描画関数にも同じものを渡す",
      app.hud.painted and app.hud.painted[-1] == [mod.CONFIRM_LABEL, mod.CANCEL_LABEL],
      app.hud.painted)

app.on_button_press(index_of(app, "cancel"))
clock.run_onces()
check("やめると元のボタンに戻る", [b["text"] for b in app.buttons] ==
      [b["text"] for b in saved], [b["text"] for b in app.buttons])
check("やめたら外れていない", app.removed == [] and "63" in app.party)

# ============================================================ 別れる
print("=== 実行 ===")

mod, ctx, app = setup()
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
check("会話はゲームの経路で閉じる", app.in_conversation is False)
check("end_text に別れた旨が入る",
      app.ended and "パーティ" in app.ended[0], app.ended)
check("会話が閉じるまでは外さない", app.removed == [])
clock.tick()
clock.run_onces()
check("外れた", app.removed == ["63"], app.removed)
check("名簿から消えた", app.party == ["player"], app.party)
check("game_variables['party'] も同じ", app.game_variables["party"] == ["player"])
check("置き直した", [row[0] for row in app.moved] == ["63"], app.moved)
check("初期位置（雇用された場所）へ戻す",
      app.moved and app.moved[0][1] is facility_in(app, "7", "71"), app.moved)
check("ノードも一緒に渡す",
      app.moved and app.moved[0][2] is node_of(app, "7"), app.moved)
check("別れの文が出る", any("パーティを離れ" in t for t in app.texts), app.texts)
check("保存した", app.saves == 1, app.saves)
# 仲間欄は名簿を書き換えただけでは変わらない。ゲーム自身の2つを通して塗り直す。
check("仲間欄を塗り直す（app 側）", app.party_updates > 0, app.party_updates)
check("仲間欄を塗り直す（HUD 側）", app.hud.party_painted > 0, app.hud.party_painted)
check("例外は出ていない", ctx.errors == [], ctx.errors)

# 別れの文が流れている最中は塗らない（先に消えると、まだ別れていないうちに
# 居なくなったように見える）。テキストが終わってから塗る。
mod, ctx, app = setup()
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
app.is_adding_text = True                      # 文を流している最中
clock.tick()
clock.run_onces()
check("文が流れている間は仲間欄を塗らない",
      app.party_updates == 0 and app.hud.party_painted == 0,
      (app.party_updates, app.hud.party_painted))
app.is_adding_text = False                     # 流し終わった
clock.tick()
clock.run_onces()
check("文が終わってから仲間欄を塗る",
      app.party_updates > 0 and app.hud.party_painted > 0,
      (app.party_updates, app.hud.party_painted))
check("そのとき名簿からも外れている", app.party == ["player"], app.party)

# ------------------------------------------------- ゲームが自分で置き直す場合
mod, ctx, app = setup()
app.game_moves_on_remove = True
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
clock.tick()
clock.run_onces()
check("ゲームが置いたなら二重に置かない", len(app.moved) == 1, app.moved)
check("それでも名簿からは消えている", app.party == ["player"], app.party)

# ------------------------------------- remove_party_member が名簿を残す場合
mod, ctx, app = setup()
app.remove_keeps_member = True
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
clock.tick()
clock.run_onces()
check("名簿に残ったらこちらで落とす", app.party == ["player"], app.party)
log_text = open(os.path.join(HERE, os.pardir, "out", "test", mod.LOG_BASENAME),
                encoding="utf-8").read()
check("その旨をログに残す", "WARN remove_party_member left" in log_text)

# ------------------------------------------------------- 置き場所が無い土地
mod, ctx, app = setup()
app.leave_facility = None
app.world.areas = {}                  # 初期位置もギルドも引けない
app.player.current_area = None
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
clock.tick()
clock.run_onces()
check("置き場所が無ければ外さない", app.removed == [] and app.party == ["player", "63"],
      (app.removed, app.party))
check("会話も閉じない", app.in_conversation is True)
check("断り文句を出す", any(mod.NO_PLACE_TEXT in t for t in app.texts), app.texts)

# --------------------------------------------- 会話がもう終わっている場合
mod, ctx, app = setup()
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.in_conversation = False           # 終了処理が先に走っていた
app.on_button_press(index_of(app, "leave"))
clock.tick()
clock.run_onces()
check("会話が閉じていてもそのまま外せる", app.party == ["player"], app.party)

# ============================================================ 置き場所の決め方
# 初期位置（雇用された場所）へ戻す。ただし土地を跨いで別れた場合は、
# いまの町のギルドへ。
print("=== 置き場所 ===")


def part_ways(app):
    app.refresh_choice_buttons()
    app.on_button_press(index_of(app, "confirm"))
    clock.run_onces()
    app.on_button_press(index_of(app, "leave"))
    clock.tick()
    clock.run_onces()


mod, ctx, app = setup()
app.player.current_area = app.world.areas["9"]        # 別の町で別れる
part_ways(app)
check("土地を跨いだら、いまの町のギルドへ",
      app.moved and app.moved[0][1] is facility_in(app, "9", "99"), app.moved)
check("元の町の施設には戻さない",
      app.moved and app.moved[0][1] is not facility_in(app, "7", "71"), app.moved)

mod, ctx, app = setup()
# 初期位置の施設が世界から消えている（改築などで id が変わった場合）。
node_of(app, "7").facilities.pop("71")
part_ways(app)
check("初期位置が引けなければ、いまの町のギルドへ",
      app.moved and app.moved[0][1] is facility_in(app, "7", "79"), app.moved)

mod, ctx, app = setup()
# エリアを id の文字列で持っている場合（NPC 側のセーブはこの形）。
app.player.current_area = "9"
part_ways(app)
check("current_area が id でも引き当てる",
      app.moved and app.moved[0][1] is facility_in(app, "9", "99"), app.moved)

mod, ctx, app = setup()
app.world.areas = {}                  # エリア表そのものが引けない
app.player.current_area = None
part_ways(app)
check("エリアが引けなければゲーム自身の答えに委ねる",
      app.moved and app.moved[0][1] is app.leave_facility[0], app.moved)
check("そのときタプルはほどいて渡す",
      app.moved and app.moved[0][2] is app.leave_facility[1], app.moved)

# ============================================ 名簿がどこに載っているか分からない
# `app.party` が空のまま `add_party_member` が通ることがある（GAME.md §2.8）。
# 名簿の形と在り処を決めつけると、判定が黙って外れてボタンが出ない。
print("=== 名簿の在り処 ===")

mod, ctx, app = setup(shape="split")
app.refresh_choice_buttons()
check("app.party が空でも game_variables 側の名簿を見つける",
      index_of(app, "confirm") >= 0, (app.party, app.game_variables["party"]))
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
clock.tick()
clock.run_onces()
check("その名簿から外れる", app.roster == ["player"], app.roster)
check("空の別配列は触らない", app.party == [], app.party)

mod, ctx, app = setup(shape="instances")
app.refresh_choice_buttons()
check("名簿にインスタンスが並んでいても仲間と分かる",
      index_of(app, "confirm") >= 0, app.roster)
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
clock.tick()
clock.run_onces()
check("インスタンスの名簿からも外れる",
      [member_key(v) for v in app.roster] == ["player"], app.roster)

# 名前の違う属性に載っていても拾えること。ただし「'player' が入りうる別の配列」
# （逃走メンバー等）を名簿と間違えないこと。
mod, ctx, app = setup(shape="split")
app.party_members = app.roster            # 心当たりのどれでもない名前
app.game_variables["party"] = []              # 既知の在り処は空
app.escaped_member_in_battle = ["player"]     # 紛らわしい別の配列
app.surrendered_characters = ["player", "63"]
app.refresh_choice_buttons()
check("名前の違う属性に載っていても拾う", index_of(app, "confirm") >= 0,
      app.party_members)

mod, ctx, app = setup(shape="dict")
app.refresh_choice_buttons()
check("名簿が {id: Character} の辞書でも仲間と分かる",
      index_of(app, "confirm") >= 0, app.roster)
app.on_button_press(index_of(app, "confirm"))
clock.run_onces()
app.on_button_press(index_of(app, "leave"))
clock.tick()
clock.run_onces()
check("辞書の名簿からも外れる", list(app.roster) == ["player"], app.roster)

mod, ctx, app = setup(shape="bare")
app.refresh_choice_buttons()
log_text = open(os.path.join(HERE, os.pardir, "out", "test", mod.LOG_BASENAME),
                encoding="utf-8").read()
check("名簿が1つも見つからなければ持ち物を書き出す", "census: app is" in log_text)
check("その中に属性名の一覧が入る", "census: names = " in log_text)

mod, ctx, app = setup(party=("player",), partner="63")
app.escaped_member_in_battle = ["player", "63"]
app.surrendered_characters = ["player", "63"]
app.refresh_choice_buttons()
check("party 以外の配列を名簿と間違えない", index_of(app, "confirm") < 0)

mod, ctx, app = setup(party=("player",), partner="63")
app.refresh_choice_buttons()
log_text = open(os.path.join(HERE, os.pardir, "out", "test", mod.LOG_BASENAME),
                encoding="utf-8").read()
check("出なかったときは会話画面の顔ぶれを残す",
      "screen: partner='63' member=False" in log_text)
check("その行に名簿の在り処が入る", "ConversationEndManager" in log_text
      and "app.party=" in log_text)

# ================================================ ゲーム本来の解散を記録できるか
# 死別・クエストクリアの解散は `remove_party_member` を通るはず。そこを読み取り
# 専用で記録しておけば、起きたときに経路が確定する。**呼び出し元の特定を
# 段数で数えてはいけない**（@ctx.wrap の層が1段挟まるので、自分のラッパを
# 指してしまう）。
print("=== ゲーム本来の解散 ===")

mod, ctx, app = setup()
app.remove_party_member("63")          # ゲーム側から外された、の再現
log_text = open(os.path.join(HERE, os.pardir, "out", "test", mod.LOG_BASENAME),
                encoding="utf-8").read()
check("ゲーム側の解散を記録する", "remove_party_member('63'" in log_text)
check("名簿の変化も残す", "remove_party_member: party ['player', '63'] -> ['player']"
      in log_text, log_text[-400:])
first_line = log_text.split("remove_party_member('63'")[-1].splitlines()[0]
check("呼び出し元に mod ローダのフレームを出さない",
      "instantale_modloader" not in first_line, first_line)

# ============================================================ 他 mod との共存
print("=== 301_ との共存 ===")

quest_src = open(QUEST_MOD, encoding="utf-8").read()
quest_mark = None
for line in quest_src.splitlines():
    if line.startswith("MARK = "):
        quest_mark = line.split("=", 1)[1].strip().strip('"')
        break
check("印のキーが 301_ と違う", quest_mark is not None and quest_mark != app_mark,
      (quest_mark, app_mark))

mod, ctx, app = setup()
app.refresh_choice_buttons()
# 301_ の押下ハンドラは「自分の印が無ければ素通し」。こちらのボタンが
# そのまま通ることを、同じ判定で確かめる。
entry = app.buttons[index_of(app, "confirm")]
check("301_ の判定ではこちらのボタンは素通しになる", entry.get(quest_mark) is None)

# 押した位置と buttons の添字がずれる場合（ページ送り）。
mod, ctx, app = setup()
app.refresh_choice_buttons()
at = index_of(app, "confirm")
app.display_button_map = list(range(len(app.buttons)))[::-1]
app.buttons = list(app.buttons)
pressed = app.display_button_map.index(at)
app.on_button_press(pressed)
clock.run_onces()
check("display_button_map があればそれで引く",
      [b["text"] for b in app.buttons] == [mod.CONFIRM_LABEL, mod.CANCEL_LABEL],
      [b["text"] for b in app.buttons])

# ============================================================ 他 mod のボタン
# `prune_stale`（残骸の掃除）は `refresh_choice_buttons` のたびに、**画面が
# 何であれ**走る。判定が「こちらのラベル ＋ 無害 spec ＋ こちらの印が無い」
# だけだと、**他の mod が今この場で出しているボタン**が3条件すべてに当たる。
#
# 実例（VERIFICATION.md §2.31）: `309_`（役場で罰金を納める）の確認画面の
# キャンセルは「やめておく」で、こちらの `CANCEL_LABEL` と同じ文字列。
# `309_` が `apply_buttons` を呼ぶと、その中の `refresh_choice_buttons` で
# こちらのフックが走り、**確認画面からキャンセルが最初から消える**。
print("=== 他の mod のボタンを消さない ===")

FOREIGN_MARK = "mod_pardon_action"          # 309_ の印


def foreign_confirm():
    """`309_` の確認画面（印のキーだけがこちらと違う）。"""
    return [{"text": "1000ゴールドを納める", FOREIGN_MARK: "pay",
             "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
            {"text": "やめておく", FOREIGN_MARK: "cancel",
             "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]


mod, ctx, app = setup()
app.buttons = foreign_confirm()
app.refresh_choice_buttons()
check("他の mod の確認画面からボタンを1枚も消さない",
      [b["text"] for b in app.buttons] == ["1000ゴールドを納める", "やめておく"],
      [b["text"] for b in app.buttons])

# 汎用の文言はそもそも掃除の対象にしない（ゲーム自身が同じ文言を出していても
# 巻き込まないため）。印を持たない「やめておく」でも消えてはいけない。
mod, ctx, app = setup()
app.buttons = [{"text": "やめておく",
                "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
app.refresh_choice_buttons()
check("印の無い「やめておく」も消さない（掃除の対象にしていない）",
      [b["text"] for b in app.buttons] == ["やめておく"],
      [b["text"] for b in app.buttons])
check("掃除に使う文言はこちらにしか無いものだけ",
      mod.CANCEL_LABEL not in mod.OUR_LABELS, mod.OUR_LABELS)

# 掃除そのものは効いたままであること（セーブから戻った印無しの自前ボタン）。
mod, ctx, app = setup()
app.refresh_choice_buttons()
live = [b for b in app.buttons if b.get(app_mark)][0]
app.buttons = [{"text": live["text"],           # 印の落ちた復元ぶん
                "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}] + \
    [b for b in app.buttons if not b.get(app_mark)]
app.refresh_choice_buttons()
same = [b for b in app.buttons if b["text"] == live["text"]]
check("セーブから戻った自分の残骸は今までどおり差し直す", len(same) == 1,
      [b["text"] for b in app.buttons])
check("差し直した1枚は印を持つ", bool(same and same[0].get(app_mark)), same)

print()
if failures:
    print("FAILED: {} 件".format(len(failures)))
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
