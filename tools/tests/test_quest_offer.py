# -*- coding: utf-8 -*-
"""301_quest_from_conversation.py をゲーム抜きで通す。

    python tools/tests/test_quest_offer.py

偽の app / PhaseSpec / DisplayQuestChoice / ConversationEndManager / HUD /
Clock を差し込み、次を確認する。

  設置   … 会話画面の「会話を終了する」の手前に「依頼を受ける」が出る。
           依頼一覧そのものには出ない（入れ子にならない）
  押下   … 印で横取りしてゲームの経路（`process_choice`）に乗せる
  会話   … 掲示板を開く前に **必ず** `ConversationEndManager` を通し、
           `end_text` を差し替えて「なぜ切り上げたか」を記録に残す
  描画   … 選択肢を差し替えたら **次のフレームで HUD を直接呼ぶ**
           （`refresh_choice_buttons` だけでは画面が塗り替わらない）
  絞り込み… 会話から開いた掲示板はその NPC 発の依頼だけにする。ゲーム本来の
           掲示板から開いたときは1件も間引かない
  共存   … `302_` と印のキーが衝突していない

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import copy
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

# mod は `instantale_modloader.ui` を使う（ゲームの中では runtime/ が
# sys.path に入っている）。
# オフラインでも同じように見えるようにする。
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import ui as mlui            # noqa: E402


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


MOD = find_mod("_quest_from_conversation")
PARTY_MOD = find_mod("_leave_party_in_conversation")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Quest:
    def __init__(self, **kw):
        self.config = {"status": "incomplete"}
        self.__dict__.update(kw)


class Area:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name
        self.nodes = {}


class World:
    def __init__(self, characters, quests, areas):
        self.characters = characters
        self.quests = quests
        self.areas = areas


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


class ConversationStartManager:
    """会話の開始。mod はここで「誰と話しているか」を控える。"""

    def __init__(self, app, character_id):
        self.app = app
        self.character_id = character_id

    def execute(self, choice_text):
        self.app.in_conversation = str(self.character_id)
        return None


class QuestChoiceManager:
    """受注画面。mod は**自前で組まない**（引数の語彙が実測できていない）。"""

    def __init__(self, app, quest_type, quest_id):
        self.app = app
        self.quest_type = quest_type
        self.quest_id = quest_id

    def execute(self, choice_text):
        self.app.accepted.append((self.quest_type, self.quest_id))
        return None


class ConversationEndManager:
    def __init__(self, app, in_conversation_id, finisher, end_text):
        self.app = app
        self.in_conversation_id = in_conversation_id
        self.finisher = finisher
        self.end_text = end_text

    def execute(self, choice_text):
        # 本物と同じく終了処理を通す。
        # mod はこの **前** に書き起こしを控える（後では
        # current_conversation_history が片付けられている）。
        self.finish_conversation()
        return None

    def finish_conversation(self):
        self.app.in_conversation = False
        self.app.ended.append(self.end_text)
        self.app.current_conversation_history = []
        return None


class DisplayQuestChoice:
    """ゲーム自身のクエスト掲示板。依頼ボタンを並べるところまでを真似る。"""

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.opened_board += 1
        self.update_button_display()
        return None

    def update_button_display(self):
        self.app.buttons = [
            {"text": "【39】水底の警備", "spec": PhaseSpec("QuestChoiceManager",
                                                          ["*unknown*", "39"])},
            {"text": "【43】霧の追跡", "spec": PhaseSpec("QuestChoiceManager",
                                                        ["*unknown*", "43"])},
            # ゲーム自身の通常ボタン。
            # 会話から開いたときは出したくない側。
            {"text": "クエストを探す", "spec": PhaseSpec("QuestSearchManager", [])},
            # 他の MOD が掲示板に足したボタン。
            # 自前ボタンは申し合わせでどれも無害な
            # spec を持つので、**spec では見分けられない**。
            # 見分けるのは「余分なキーがある」こと。
            {"text": "掲示板メモ",
             "spec": PhaseSpec("JustSetButtonToNormalPhase", []),
             "other_mod_mark": "note"},
            {"text": "戻る", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)

    def generate_random_quest(self):
        # ゲーム自身の生成経路。
        # id を採番して両方の格納先に登録する。
        new_id = str(max(int(k) for k in self.app.world.quests) + 1)
        quest = Quest(id=new_id, quest_title="こぼれ話の依頼",
                      client_name="テスト依頼人A", difficulty=41,
                      neighboring_settlement_id="7")
        self.app.world.quests[new_id] = quest
        self.app.world_dict["quests"][new_id] = {"id": new_id,
                                                 "quest_title": "こぼれ話の依頼",
                                                 "client_name": "テスト依頼人A"}
        return quest


class InstantaleApp:
    def __init__(self, world):
        self.world = world
        self.world_dict = {"world_data": {"world_name": "テスト世界"},
                           "quests": {}}
        # 名簿。セーブでは game_variables['party'] で、必ず 'player' を含む
        # （GAME.md §2.8）。同行者を足すときはここへ id を入れる。
        self.game_variables = {"party": ["player"]}
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.in_conversation = False
        self.current_conversation_history = []
        self.texts = []
        self.ended = []
        self.opened_board = 0
        self.refreshes = 0
        self.loaded = []
        self.process_choice_calls = []
        self.pressed_by_game = []
        self.accepted = []
        self.hud = HUD()

    def add_text(self, context):
        self.texts.append(context)

    def process_choice(self, function, choice_text=""):
        self.process_choice_calls.append((type(function).__name__, choice_text))
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        # 本物は to_display_buttons を組み直すところまで。
        # 画面は塗らない。
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def display_button_load(self, dt):
        self.loaded.append(list(self.to_display_buttons))

    def on_button_press(self, button_index):
        """ゲーム本来の押下処理。**spec からマネージャを組んで process_choice に渡す。**

        `getattr(__main__, cls_name)(app, *args)` という形は
        206_ の計測で確定している。
        自前ボタンに無害な spec を持たせておく意味は、
        mod 無しで押されたときここが害の無いクラスを起こすからなので、
        そこも再現する。
        """
        entry = self.buttons[button_index]
        text = entry.get("text")
        self.pressed_by_game.append(text)
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), text)


# 派生元は名前ではなくこの表から引く。
# **`sys.modules['__main__']` は直接実行時にはこのテスト自身**なので、
# `main.InstantaleApp = app_cls` がここのグローバル名を書き換えてしまう。
# 素朴に `type("InstantaleApp", (InstantaleApp,), {})` と書くと
# 2回目以降は「前回の派生クラス」から派生し、
# フックの層が積み上がって同じ処理が何度も走る。
BASES = {"app": InstantaleApp, "board": DisplayQuestChoice,
         "start": ConversationStartManager, "end": ConversationEndManager}


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

    def settle(self, times=3):
        """見張り -> 予約 -> 見張り … の連鎖を落ち着くまで進める。"""
        for _ in range(times):
            self.run_onces()
            self.tick()
        self.run_onces()


def install_fake_hud():
    """`scripts.hud.new_hud.InstanTaleHUD` を差し込む（型で探されるので型だけ要る）。"""
    name = "scripts.hud.new_hud"
    module = types.ModuleType(name)

    class InstanTaleHUD:
        def __init__(self):
            self.painted = []

        def update_button_texts(self, instance, value):
            self.painted.append(list(value))

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
    sys.modules.pop("kivy.app", None)
    return clock


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        # 本番と同じく**別のフォルダ**にする（ローダの `state_dir`）。
        # 同じ場所を指すと、状態を out/ に書く mod が検証を素通りする。
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        # 読んだファイル名。
        # 会話のたびに控えを読み直していないかを数えるのに使う。
        self.reads = []

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

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, msg):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    # 本物の `ctx.write_json` / `write_text` と同じものを使う。
    # ここを自前の open(..., "w") にすると、
    # テストだけが「壊れない書き方」を通らなくなる。
    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def read_json(self, path, default=None):
        self.reads.append(os.path.basename(path))
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="quest_offer_mod"):
    spec = importlib.util.spec_from_file_location(name, path,
                                            submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, targets):
    """フックを本番と同じ形（メソッドの差し替え）でクラスに載せる。

    `targets` は `(フック名, 載せる先のクラス, メソッド名)`。
    **載せる先は毎回作り直した派生クラス**にする（前のテストで差し替えたものを持ち越さない）。
    """
    for target, owner, name in targets:
        hook = hooks.get(target)
        if hook is None:
            continue
        original = getattr(owner, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(owner, name, make())


def talk_buttons(partner_id):
    """会話画面のボタン。実測では「会話を終了する」1個だけ。"""
    return [{"text": "会話を終了する",
             "spec": PhaseSpec("ConversationEndManager",
                               [partner_id, "user", "<行動: 会話を終了する>"])}]


def facility_buttons():
    return [{"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])},
            {"text": "出る", "spec": PhaseSpec("MovePhaseManager", ["20", "134", "7"])}]


def setup(history=None, partner="62", in_conversation=True,
          party=()):
    """mod を適用し、NPC 62 と会話している状態の app を返す。"""
    # クラスは毎回作り直す。
    # `__main__` に載せるのは mod が
    # `getattr(__main__, 名前)` で引くため（本番と同じ形）。
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    board_cls = type("DisplayQuestChoice", (BASES["board"],), {})
    start_cls = type("ConversationStartManager", (BASES["start"],), {})
    end_cls = type("ConversationEndManager", (BASES["end"],), {})
    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.DisplayQuestChoice = board_cls
    main.ConversationStartManager = start_cls
    main.ConversationEndManager = end_cls
    main.QuestChoiceManager = QuestChoiceManager
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec

    mod = load_mod()
    ctx = FakeCtx(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
    mod.apply(ctx)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.refresh_choice_buttons", app_cls,
         "refresh_choice_buttons"),
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
        ("__main__:DisplayQuestChoice.update_button_display", board_cls,
         "update_button_display"),
        ("__main__:ConversationStartManager.__init__", start_cls, "__init__"),
        ("__main__:ConversationEndManager.finish_conversation", end_cls,
         "finish_conversation"),
    ))

    quests = {"39": Quest(id="39", quest_title="水底の警備", difficulty=39,
                          client_name="名も無い誰か", neighboring_settlement_id="7"),
              "43": Quest(id="43", quest_title="霧の追跡", difficulty=43,
                          client_name="名も無い誰か", neighboring_settlement_id="7")}
    # `profile` を持たせる。
    # 片付いた依頼の注入は、この素のプロフィールの後ろへ足す形になる。
    characters = {"62": Character(id="62", name="テストNPC D",
                                  profile="堅実な町の事務官。"),
                  # 別の相手。片付いた依頼の持ち主を取り違えないかを見る。
                  "63": Character(id="63", name="テストNPC E",
                                  profile="無口な鍛冶屋。")}
    areas = {"7": Area("7", "テストの町A")}
    app = app_cls(World(characters, quests, areas))
    app.game_variables["party"] = ["player"] + [str(m) for m in party]
    # 現在地は **id の文字列**で持たせる（`302_` の実測。
    # エリアのオブジェクトを直接持っているとは限らない）。
    app.player = Character(id="player", name="テストプレイヤー", current_area="7")
    # 会話の書き起こし（「この話から依頼を作る」の材料）。
    app.current_conversation_history = history if history is not None else []
    if in_conversation:
        # ゲームと同じ経路で会話に入る。
        # ここで mod が相手を控える。
        app.process_choice(start_cls(app, partner), "テストNPC D")
        app.buttons = talk_buttons(partner)
    return mod, ctx, app


def index_of(app, mark):
    for i, entry in enumerate(app.buttons):
        if entry.get(MARK) == mark:
            return i
    return -1


HUD = install_fake_hud()
clock = install_fake_kivy()
MARK = load_mod().MARK

# ============================================================ 設置
print("=== ボタンの設置 ===")

mod, ctx, app = setup()
app.refresh_choice_buttons()
check("会話画面に「依頼を受ける」が出る", index_of(app, "offer") >= 0,
      [b["text"] for b in app.buttons])
check("「会話を終了する」の手前に入る", index_of(app, "offer") == len(app.buttons) - 2,
      [b["text"] for b in app.buttons])
check("自前ボタンの spec は無害な既存クラス",
      app.buttons[index_of(app, "offer")]["spec"].cls_name
      == "JustSetButtonToNormalPhase")
check("文言で「話を切り上げる」と分かる",
      "切り上げ" in app.buttons[index_of(app, "offer")]["text"],
      app.buttons[index_of(app, "offer")]["text"])
before = len(app.buttons)
app.refresh_choice_buttons()
check("並べ直しても増えない", len(app.buttons) == before, len(app.buttons))

mod, ctx, app = setup(in_conversation=False)
app.buttons = facility_buttons()
app.refresh_choice_buttons()
check("施設の選択肢には出さない（既定 OFFER_SITES=conversation のみ）",
      index_of(app, "offer") < 0, [b["text"] for b in app.buttons])

mod, ctx, app = setup(in_conversation=False)
app.buttons = [{"text": "【39】水底の警備",
                "spec": PhaseSpec("QuestChoiceManager", ["*unknown*", "39"])}]
app.refresh_choice_buttons()
check("依頼一覧そのものには出ない（入れ子にならない）", index_of(app, "offer") < 0)

# ============================================================ 押下
print("=== 押下の横取り ===")

mod, ctx, app = setup()
app.refresh_choice_buttons()
result = app.on_button_press(index_of(app, "offer"))
check("ゲームの押下処理には渡さない", result is None, result)
check("process_choice に自前フェーズを渡す",
      any(name == "OfferPhase" for name, _t in app.process_choice_calls),
      app.process_choice_calls)

mod, ctx, app = setup()
app.refresh_choice_buttons()
app.on_button_press(len(app.buttons) - 1)      # 「会話を終了する」
check("印の無いボタンは素通しする", app.pressed_by_game == ["会話を終了する"],
      app.pressed_by_game)
check("素通しならゲームがマネージャを組んで実行する", app.ended, app.ended)

# ============================================================ 会話を閉じてから開く
print("=== 会話を閉じてから掲示板を開く ===")

clock = install_fake_kivy()
mod, ctx, app = setup()
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "offer"))
check("押した時点ではまだ掲示板を開かない", app.opened_board == 0)
check("ConversationEndManager を通す", app.ended, app.ended)
check("end_text を別の記述に差し替える",
      app.ended and app.ended[0] != "<行動: 会話を終了する>", app.ended)
check("差し替えた end_text に理由が入る",
      app.ended and "依頼" in app.ended[0], app.ended)
check("会話状態が落ちる", not app.in_conversation)
clock.settle()
check("閉じた後で掲示板が開く", app.opened_board == 1, app.opened_board)
check("エラーを出していない", not ctx.errors, ctx.errors)

# 会話が既に終わっている経路（施設側から入った場合）。
clock = install_fake_kivy()
mod, ctx, app = setup(in_conversation=False)
app.buttons = facility_buttons()
app.refresh_choice_buttons()
# 会話画面ではないので設置もされない（既定は会話中だけ）。
check("会話していなければ ConversationEndManager は通さない", not app.ended)

# ============================================================ 描画
print("=== 描画（refresh だけでは塗り替わらない） ===")

history = [{"role": "user", "content": "この辺りで妙な噂を聞いたが"},
           {"role": "assistant", "content": "……霧の夜に人が消えるのです"}]

clock = install_fake_kivy()
mod, ctx, app = setup(history=history)
app.refresh_choice_buttons()
hud_before = len(app.hud.painted)
app.on_button_press(index_of(app, "offer"))
check("押下と同じ流れの中では塗らない（次のフレームに回す）",
      len(app.hud.painted) == hud_before, app.hud.painted)
clock.settle()
check("HUD の update_button_texts を直接呼ぶ", len(app.hud.painted) > hud_before,
      app.hud.painted)
check("display_button_load も通す", app.loaded, app.loaded)
check("塗った内容が to_display_buttons と一致する",
      app.hud.painted and app.hud.painted[-1] == app.to_display_buttons,
      (app.hud.painted[-1:], app.to_display_buttons))

# ============================================================ 掲示板の絞り込み
print("=== 掲示板の絞り込みと「この話から依頼を作る」 ===")

clock = install_fake_kivy()
mod, ctx, app = setup(history=history)
app.refresh_choice_buttons()

# 「この話から依頼を作る」が出るのは**会話画面**。
# 掲示板ではない会話画面に置けば会話を閉じずに生成でき、
# 掲示板は「既にある依頼を選ぶ場所」に徹せる。
generate = [b for b in app.buttons if b.get(MARK) == "generate"]
check("会話メニューに「この話から依頼を作る」が出る", generate,
      [b["text"] for b in app.buttons])
check("依頼人の名前が文言に入る",
      generate and "テストNPC D" in generate[0]["text"],
      generate[0]["text"] if generate else None)

app.on_button_press(index_of(app, "offer"))
clock.settle()
quest_buttons = [b for b in app.buttons
                 if b["spec"].cls_name == "QuestChoiceManager"]
check("会話から開くと、その NPC 発でない依頼は間引かれる", not quest_buttons,
      [b["text"] for b in app.buttons])
check("掲示板には「この話から依頼を作る」を出さない",
      not any(b.get(MARK) == "generate" for b in app.buttons),
      [b["text"] for b in app.buttons])
check("掲示板をいじった後も HUD を塗り直す", app.hud.painted, app.hud.painted)

texts = [b["text"] for b in app.buttons]
check("ゲームの「クエストを探す」は出さない", "クエストを探す" not in texts, texts)
check("他の MOD が掲示板に足したボタンも出さない", "掲示板メモ" not in texts, texts)
check("戻り道は残る",
      any(b["spec"].cls_name == "JustSetButtonToNormalPhase" for b in app.buttons),
      texts)
check("戻り道はゲーム自身のものをそのまま使う", "戻る" in texts, texts)

# **読み込み順が後ろの MOD ほど外側**なので、
# その追加はこちらの間引きより後に起きる。
# 次のフレームの掛け直しで落ちること。
app.buttons.append({"text": "後から足されたボタン",
                    "spec": PhaseSpec("JustSetButtonToNormalPhase", []),
                    "late_mod_mark": "x"})
# ボタンを出したい MOD は必ず描画経路を通る。
# そこで掛け直す。
app.refresh_choice_buttons(reset_page=True)
clock.settle()
texts = [b["text"] for b in app.buttons]
check("後から足されたボタンも次のフレームで落ちる",
      "後から足されたボタン" not in texts, texts)

# 会話が無ければ「作る」は出さない（押した先で失敗するボタンを見せない）。
clock = install_fake_kivy()
mod, ctx, app = setup(history=[])
app.refresh_choice_buttons()
app.on_button_press(index_of(app, "offer"))
clock.settle()
check("会話が無ければ「この話から依頼を作る」は出さない",
      all(b.get(MARK) != "generate" for b in app.buttons),
      [b["text"] for b in app.buttons])

# ゲーム本来の掲示板（DisplayQuestChoice を直接押した）なら1件も間引かない。
clock = install_fake_kivy()
mod, ctx, app = setup(history=history, in_conversation=False)
app.buttons = [{"text": "クエスト掲示板", "spec": PhaseSpec("DisplayQuestChoice", [])}]
app.on_button_press(0)
clock.settle()
kept = [b for b in app.buttons if b["spec"].cls_name == "QuestChoiceManager"]
check("ゲーム本来の掲示板は全件のまま", len(kept) == 2, [b["text"] for b in app.buttons])
texts = [b["text"] for b in app.buttons]
check("ゲーム本来の掲示板では「クエストを探す」も残る",
      "クエストを探す" in texts, texts)
check("ゲーム本来の掲示板では他 MOD のボタンも残る", "掲示板メモ" in texts, texts)

# ============================================================ 依頼 id の読み取り
print("=== spec からの読み取り（語彙を知らずに済ませる） ===")

mod, ctx, app = setup()
entry = {"text": "【39】水底の警備",
         "spec": PhaseSpec("QuestChoiceManager", ["*unknown vocabulary*", "39"])}
from instantale_modloader import ui as _ui
check("quest_type の語彙を知らなくても id は args[1] から読める",
      (_ui.spec_args(entry) or [None, None])[1] == "39", _ui.spec_args(entry))
check("自前で QuestChoiceManager を組む既定にはなっていない",
      mod.LIST_MODE == "game" or mod.QUEST_TYPE_FOR_CHOICE is not None,
      (mod.LIST_MODE, mod.QUEST_TYPE_FOR_CHOICE))

# ================================================ セーブから戻った残骸との二重化
print("=== タイトル戻り・再注入後の残骸 ===")

# `PhaseSpec.to_dict()` がセーブに書くのは text と
# spec だけで、**印は落ちる**（実セーブ8件で確認。GAME.md §2.2）。
# タイトルへ戻る・ロード・再注入のあとは「印の無い自分のボタン」が復元されているので、
# 印だけで重複を見ていると同じボタンが2つ並ぶ。
def stale(text):
    """セーブから復元された自前ボタン（印が落ちている）。"""
    return {"text": text, "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}

mod, ctx, app = setup(history=history)
app.buttons = ([stale("この話から依頼を作る（事務官エドガー）"),
                stale("依頼を受ける（話を切り上げる）")] + talk_buttons("62"))
app.refresh_choice_buttons(reset_page=True)
texts = [b["text"] for b in app.buttons]
check("残骸と合わせて二重にならない",
      texts.count("依頼を受ける（話を切り上げる）") == 1, texts)
check("生成ボタンも二重にならない",
      len([t for t in texts if t.startswith("この話から依頼を作る")]) == 1, texts)
check("残った方は印がある（押せば効く）",
      all(mod.MARK in b for b in app.buttons
          if b["text"].startswith(("依頼を受ける", "この話から依頼を作る"))), texts)
check("会話を終了するは残る", "会話を終了する" in texts, texts)

# ゲーム側の同名ボタンを巻き込まないこと。
# spec が違えば別物。
from instantale_modloader import ui as _ui
screen = _ui.Screen(ctx, lambda m: None, tag="t", mark=mod.MARK)
game = [{"text": "依頼を受ける", "spec": PhaseSpec("DisplayQuestChoice", [])}]
check("ゲーム側の同名ボタンは落とさない",
      screen.prune_stale(game, mod.OUR_LABELS) == [], game)

# 印を持たない Screen（`300_` のようにボタンを作らない MOD）では何もしない。
nomark = _ui.Screen(ctx, lambda m: None, tag="t", mark=None)
victims = [stale("依頼を受ける（話を切り上げる）")]
check("印を持たない Screen では何も落とさない",
      nomark.prune_stale(victims, mod.OUR_LABELS) == [], victims)

# **他の MOD の印が付いていたら残骸ではない。**
# セーブに焼かれるのは text と spec だけ ＝ 復元された残骸は印を1つも持たない。
# 逆に印があれば、いま誰かが挿した生きているボタンなので触ってはいけない。
# ここを見ないと、`302_` が `309_`（役場の罰金）の確認画面からキャンセルを消すことになる（同じ文言・違う印のキー。
# VERIFICATION_LOG.md §2.31）。
foreign = {"text": "依頼を受ける（話を切り上げる）", "mod_pardon_action": "cancel",
           "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}
other = [dict(foreign)]
check("他の MOD の印が付いたボタンは落とさない",
      screen.prune_stale(other, mod.OUR_LABELS) == [], other)
check("marked_by_a_mod が他人の印を見つける",
      _ui.Screen.marked_by_a_mod(foreign) is True, foreign)
check("印の無い残骸は marked_by_a_mod に引っかからない",
      _ui.Screen.marked_by_a_mod(stale("依頼を受ける")) is False)

# ============================================================ 302_ との共存
print("=== 302_ との共存 ===")

party_mark = load_mod(PARTY_MOD, "party_leave_mod_probe").MARK
check("印のキーが 302_ と違う", MARK != party_mark, (MARK, party_mark))

mod, ctx, app = setup()
app.buttons = talk_buttons("62") + [{"text": "ここで別れる",
                                     "spec": PhaseSpec("JustSetButtonToNormalPhase", []),
                                     party_mark: "confirm"}]
app.on_button_press(len(app.buttons) - 1)
check("302_ のボタンはこちらでは素通しになる",
      app.pressed_by_game == ["ここで別れる"], app.pressed_by_game)

# ============================================================ 311_ の人物像
# mod どうしは import せず**同じファイルを読む**ことで繋がる（TECH.md §3.2.3）。
# ここで見るのは「在れば添える」「無ければ何も足さない」の2つ。
print("=== 311_ が覚えた人物像 ===")

GEN_TARGET = "scripts.llm.llm_manager_world_generate:random_quest_generator"


def press_generate(ctx_obj, app_obj):
    """「この話から依頼を作る」を押し、生成側へ渡った `area_description` を返す。

    **本物と同じ入れ子にする。**
    `random_quest_generator` はゲーム自身の
    `generate_random_quest()` の内側で走るので、
    その外から呼んでも遅い ― 生成が終わった時点で mod は印を使い切って
    `None` に戻している。
    """
    seen = {}
    board_cls = sys.modules["__main__"].DisplayQuestChoice
    original = board_cls.generate_random_quest

    def orig(world_overview, settlement_name, settlement_overview,
             settlement_structure_description, area_description, quest_difficulty,
             *args, **kwargs):
        seen["area"] = area_description
        return {"quest_title": "こぼれ話の依頼", "client_name": "テストNPC D"}

    def generate_random_quest(self):
        ctx_obj.hooks[GEN_TARGET](orig, "世界の概要", "テストの町A", "町の概要",
                                  "町の構造", "エリアの説明", 41)
        return original(self)

    board_cls.generate_random_quest = generate_random_quest
    try:
        app_obj.refresh_choice_buttons()
        app_obj.on_button_press(index_of(app_obj, "generate"))
        clock.settle()
    finally:
        board_cls.generate_random_quest = original
    return seen.get("area", "")


def seed_persona(state_dir, record):
    """`311_` が書くのと同じ場所・同じ形で控えを置く（`state/npc_profiles/`）。"""
    path = os.path.join(state_dir, mod.NPC_MEMORY_DIRNAME,
                        ml.state.world_filename("テスト世界"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"62": record}, ensure_ascii=False))
    return path


mod, ctx, app = setup(history=history)
persona_path = seed_persona(ctx.state_dir, {
    "name": "テストNPC D", "profile": "石橋を叩いて渡る町の事務官。",
    "about_player": "腕は認めているが、まだ全幅の信頼は置いていない。"})
try:
    area = press_generate(ctx, app)
    check("依頼人の人物像が生成プロンプトに載る",
          "石橋を叩いて渡る町の事務官。" in area, area[-400:])
    check("その人物から見た冒険者の記録も載る",
          "まだ全幅の信頼は置いていない。" in area, area[-400:])
    check("人物像は会話の記録より後ろに置く",
          "石橋を叩いて渡る" in area
          and area.index("会話の記録") < area.index("石橋を叩いて渡る"),
          area[-400:])
    check("人物像を依頼の中身にしないよう釘を刺す",
          "依頼の中身にしてはならない" in area, area[-400:])
finally:
    os.remove(persona_path)

# `311_` を入れていない（＝ファイルが無い）ときは、何も足さずに通ること。
mod, ctx, app = setup(history=history)
area = press_generate(ctx, app)
check("311_ が無ければ会話の記録だけを添える", "会話の記録" in area, area[-300:])
check("311_ が無くても人物像の節は足さない",
      "過去の会話から分かっていること" not in area, area[-300:])

# ================================== 片付いた依頼を依頼人との会話に伝える
# 会話のプロンプトには依頼の結末が入る欄が無い（GAME.md §2.25）ので、
# `character_instance` の複製の `profile` に添える。
# ここで見るのは「載る条件」「載せない条件」「世界を書き換えないこと」の3つ。
print("=== 片付いた依頼を会話に伝える ===")

CONV_TARGETS = (
    "scripts.llm.llm_manager:conversation_facilitator",
    "scripts.llm.llm_manager:conversation_facilitator_after_retrieval",
    "scripts.llm.llm_manager:conversation_facilitator_in_quest",
    "scripts.llm.llm_manager:conversation_starter",
    "scripts.llm.llm_manager:conversation_starter_in_quest")


def clients_path(ctx_obj):
    return os.path.join(ctx_obj.state_dir, mod.CLIENTS_BASENAME)


def clear_clients(ctx_obj):
    """前の回の控えを消す。残すと他の節の絞り込みが変わる。"""
    path = clients_path(ctx_obj)
    if os.path.isfile(path):
        os.remove(path)


def seed_client(ctx_obj, app_obj, quest_id, npc_id, npc_name):
    """`301_` 自身が書くのと同じ形で「この依頼はこの NPC 発」を置く。

    **`load_clients` は最初の1回で写しを持つ**ので、
    置くのは会話を1回も通す前にすること。
    """
    path = clients_path(ctx_obj)
    data = {}
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    data.setdefault(ml.state.world_key(app_obj), {})[str(quest_id)] = {
        "npc_id": str(npc_id), "npc_name": npc_name}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False))


def add_quest(app_obj, quest_id, title, status, client_name="名も無い誰か"):
    app_obj.world.quests[str(quest_id)] = Quest(
        id=str(quest_id), quest_title=title, difficulty=10,
        client_name=client_name, neighboring_settlement_id="7",
        config={"status": status})


def talk_once(ctx_obj, app_obj, npc, target=CONV_TARGETS[0], as_kwarg=False):
    """会話5関数の1本を mod のフック越しに1回呼ぶ。

    先頭4引数の並びは5本とも同じ（GAME.md §2.25）。
    返すのは「元の関数に何が渡ったか」と「何回呼ばれたか」。
    """
    seen = {"npc": None, "calls": 0}

    def orig(messages, character_life_log, player, character_instance,
             *args, **kwargs):
        seen["calls"] += 1
        seen["npc"] = character_instance
        return "返答"

    hook = ctx_obj.hooks[target]
    if as_kwarg:
        hook(orig, [], [], app_obj.player, character_instance=npc)
    else:
        hook(orig, [], [], app_obj.player, npc)
    return seen


def profile_seen(seen):
    return getattr(seen["npc"], "profile", "") or ""


check("会話5関数すべてを包む",
      all(target in ctx.hooks for target in CONV_TARGETS),
      [t for t in CONV_TARGETS if t not in ctx.hooks])

# -- 片付いた依頼が載る。未完了と、別の相手の依頼は載らない。
mod, ctx, app = setup()
clear_clients(ctx)
add_quest(app, "50", "瘴霧の夜警", "completed")
add_quest(app, "51", "水路の見張り", "incomplete")
add_quest(app, "52", "鍛冶場の火事", "completed")
seed_client(ctx, app, "50", "62", "テストNPC D")
seed_client(ctx, app, "51", "62", "テストNPC D")
seed_client(ctx, app, "52", "63", "テストNPC E")   # 別の相手の依頼
try:
    npc = app.world.characters["62"]
    seen = talk_once(ctx, app, npc)
    body = profile_seen(seen)
    check("片付いた依頼が会話のプロフィールに載る",
          mod.COMPLETED_HEADING in body and "瘴霧の夜警" in body, body)
    check("未完了の依頼は載せない", "水路の見張り" not in body, body)
    check("別の相手が出した依頼は載せない", "鍛冶場の火事" not in body, body)
    check("元のプロフィールを残す", "堅実な町の事務官。" in body, body)
    check("元の関数は1回だけ呼ぶ", seen["calls"] == 1, seen["calls"])
    check("渡すのは複製で、世界の人物そのものではない",
          seen["npc"] is not npc)
    check("世界の人物そのものは書き換えない",
          npc.profile == "堅実な町の事務官。", npc.profile)
    check("完了しているという事実として書く", "完了済み" in body, body[-200:])
    check("記憶と食い違ったらこちらを取れと言う", "食い違" in body, body[-200:])
    check("話題を切り出せとは指示しない",
          "切り出す必要は無い" in body and "切り出すこと" not in body, body[-200:])

    # 会話は1ターンに何度も回る。そのたびに控えを読み直さないこと。
    before = ctx.reads.count(mod.CLIENTS_BASENAME)
    for _ in range(4):
        talk_once(ctx, app, npc)
    check("会話のたびに控えを読み直さない",
          ctx.reads.count(mod.CLIENTS_BASENAME) == before,
          ctx.reads.count(mod.CLIENTS_BASENAME))

    # `character_instance` が kwargs で来る経路。
    seen = talk_once(ctx, app, npc, as_kwarg=True)
    check("character_instance が kwargs でも差し替える",
          mod.COMPLETED_HEADING in profile_seen(seen), profile_seen(seen))

    # 外側の mod（`311_`）が既に複製へ足している場合。
    # **受け取ったものを複製する**ので、あちらの層が残る。
    outer = copy.copy(npc)
    outer.profile = npc.profile + "\n\n【会話から形成された追加プロフィール】\n慎重。"
    body = profile_seen(talk_once(ctx, app, outer))
    check("外側の mod が足したプロフィールを消さない",
          "【会話から形成された追加プロフィール】" in body
          and mod.COMPLETED_HEADING in body
          and body.index("追加プロフィール") < body.index(mod.COMPLETED_HEADING),
          body)

    # 相手を変えると、その相手の依頼だけになる（取り違えの裏返し）。
    body = profile_seen(talk_once(ctx, app, app.world.characters["63"]))
    check("相手が変わればその相手の依頼だけが載る",
          "鍛冶場の火事" in body and "瘴霧の夜警" not in body, body)

    # 依頼をひとつも出していない相手。
    stranger = Character(id="64", name="通りすがり", profile="旅の商人。")
    app.world.characters["64"] = stranger
    seen = talk_once(ctx, app, stranger)
    check("依頼を出していない相手には何も足さない",
          mod.COMPLETED_HEADING not in profile_seen(seen)
          and seen["npc"] is stranger, profile_seen(seen))
    check("エラーを出していない", not ctx.errors, ctx.errors)
finally:
    clear_clients(ctx)

# -- `.id` が `world.characters` の鍵と食い違う世界。
# 鍵と同じ値かは先頭の1体でしか確かめられていない（GAME.md §2.7）ので、
# 素のオブジェクトが届いた回は同一性で引いた答えを採る。
mod, ctx, app = setup()
clear_clients(ctx)
add_quest(app, "55", "灯台の点検", "completed")
seed_client(ctx, app, "55", "62", "テストNPC D")
try:
    liar = app.world.characters["62"]
    liar.id = "999"          # 鍵は "62" のまま
    body = profile_seen(talk_once(ctx, app, liar))
    check("id が鍵と食い違うなら同一性で引いた方を採る", "灯台の点検" in body, body)
finally:
    app.world.characters["62"].id = "62"
    clear_clients(ctx)

# -- 依頼人の名前が一致するだけでも拾う（掲示板の絞り込みと同じ規則）。
mod, ctx, app = setup()
clear_clients(ctx)
add_quest(app, "53", "橋の修繕", "completed", client_name="テストNPC D")
try:
    body = profile_seen(talk_once(ctx, app, app.world.characters["62"]))
    check("控えが無くても依頼人名の一致で拾う", "橋の修繕" in body, body)
finally:
    clear_clients(ctx)

# -- 件数の上限。
mod, ctx, app = setup()
clear_clients(ctx)
for number in range(5):
    add_quest(app, 60 + number, "片付いた依頼{}".format(number), "completed",
              client_name="テストNPC D")
told = mod.MAX_COMPLETED_TOLD
try:
    mod.MAX_COMPLETED_TOLD = 2
    body = profile_seen(talk_once(ctx, app, app.world.characters["62"]))
    check("件数の上限を超えて並べない", body.count("・") == 2, body)
    # 残すのは id の大きい方＝後に作られた依頼。
    # 辞書順だと "10" < "9" になるので、数として並べていることも見る。
    check("残すのは新しい方", "片付いた依頼3" in body and "片付いた依頼4" in body
          and "片付いた依頼0" not in body, body)
    mod.MAX_COMPLETED_TOLD = 0
    seen = talk_once(ctx, app, app.world.characters["62"])
    check("0 件にすると何も足さない",
          mod.COMPLETED_HEADING not in profile_seen(seen), profile_seen(seen))
finally:
    mod.MAX_COMPLETED_TOLD = told
    clear_clients(ctx)

# -- 設定で切る。
mod, ctx, app = setup()
clear_clients(ctx)
add_quest(app, "54", "水門の点検", "completed", client_name="テストNPC D")
tell = mod.TELL_COMPLETED_QUESTS
try:
    mod.TELL_COMPLETED_QUESTS = False
    npc = app.world.characters["62"]
    seen = talk_once(ctx, app, npc)
    check("設定を切ると素通しする",
          seen["npc"] is npc and mod.COMPLETED_HEADING not in profile_seen(seen),
          profile_seen(seen))
    check("切っても元の関数は1回だけ呼ぶ", seen["calls"] == 1, seen["calls"])
finally:
    mod.TELL_COMPLETED_QUESTS = tell
    clear_clients(ctx)

# ============================== 仲間になっている NPC は依頼人にしない
# 同行している相手に依頼を出してもらうと、
# 受注した時点でその相手はもう隣に居る（依頼人が現地へ付いて来る形になる）。
# 名簿の読み方はローダ任せ（`ui.party_ids`。GAME.md §2.8）。
print("=== 仲間は依頼人にしない ===")


def log_since(ctx_obj, mark):
    """`quest_offer.log` に mark 以降で書かれた分。書かなかったことも見たい。"""
    path = os.path.join(ctx_obj.out_dir, mod.LOG_BASENAME)
    if not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        fh.seek(mark)
        return fh.read()


def log_mark(ctx_obj):
    path = os.path.join(ctx_obj.out_dir, mod.LOG_BASENAME)
    return os.path.getsize(path) if os.path.isfile(path) else 0


# 同行している相手との会話。
mod, ctx, app = setup(history=history, party=["62"])
app.refresh_choice_buttons()
check("同行者には「依頼を受ける」を出さない", index_of(app, "offer") < 0,
      [b.get("text") for b in app.buttons])
check("同行者には「この話から依頼を作る」も出さない",
      index_of(app, "generate") < 0, [b.get("text") for b in app.buttons])
check("会話を終了するは残る",
      any(mlui.spec_cls_name(b) == "ConversationEndManager" for b in app.buttons),
      [b.get("text") for b in app.buttons])

# 同行していない相手なら今までどおり。
mod, ctx, app = setup(history=history, party=["63"])
app.refresh_choice_buttons()
check("同行していない相手には今までどおり出す", index_of(app, "offer") >= 0,
      [b.get("text") for b in app.buttons])

# 会話を閉じても書き起こしを控えない（施設側から拾えないようにする）。
mod, ctx, app = setup(history=history, party=["62"])
app.refresh_choice_buttons()
mark = log_mark(ctx)
end = mlui.find_spec_button(app.buttons, "ConversationEndManager")
app.on_button_press(app.buttons.index(end))
clock.settle()
written = log_since(ctx, mark)
check("同行者の会話は控えない", "remembered talk" not in written, written[-200:])
check("控えなかった理由が残る", "party member" in written, written[-200:])

# 設定を切れば仲間からも受けられる。
mod, ctx, app = setup(history=history, party=["62"])
excluded = mod.EXCLUDE_PARTY_MEMBERS
try:
    mod.EXCLUDE_PARTY_MEMBERS = False
    app.refresh_choice_buttons()
    check("設定を切ると仲間にも出す", index_of(app, "offer") >= 0,
          [b.get("text") for b in app.buttons])
finally:
    mod.EXCLUDE_PARTY_MEMBERS = excluded

# 名簿がどこにも無い世界。**消す側に倒さない**。
mod, ctx, app = setup(history=history)
del app.game_variables
app.refresh_choice_buttons()
check("名簿が読めなくてもボタンは消さない", index_of(app, "offer") >= 0,
      [b.get("text") for b in app.buttons])
check("名簿が読めなくてもエラーにしない", not ctx.errors, ctx.errors)

print()
if failures:
    print("{} 件失敗: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
