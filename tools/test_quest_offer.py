# -*- coding: utf-8 -*-
"""301_quest_from_conversation.py をゲーム抜きで通す。

    python tools/test_quest_offer.py

偽の app / PhaseSpec / DisplayQuestChoice / ConversationEndManager / HUD / Clock を
差し込み、次を確認する。

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

この mod は実機での確認がまだ済んでいないので、まずここを通すこと。
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

# mod は `instantale_modloader.ui` を使う（ゲームの中では runtime/ が
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
        # 本物と同じく終了処理を通す。mod はこの **前** に書き起こしを控える
        # （後では current_conversation_history が片付けられている）。
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
            {"text": "戻る", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)

    def generate_random_quest(self):
        # ゲーム自身の生成経路。id を採番して両方の格納先に登録する。
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
        # 本物は to_display_buttons を組み直すところまで。画面は塗らない。
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def display_button_load(self, dt):
        self.loaded.append(list(self.to_display_buttons))

    def on_button_press(self, button_index):
        """ゲーム本来の押下処理。**spec からマネージャを組んで process_choice に渡す。**

        `getattr(__main__, cls_name)(app, *args)` という形は 206_ の計測で
        確定している。自前ボタンに無害な spec を持たせておく意味は、mod 無しで
        押されたときここが害の無いクラスを起こすからなので、そこも再現する。
        """
        entry = self.buttons[button_index]
        text = entry.get("text")
        self.pressed_by_game.append(text)
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), text)


# 派生元は名前ではなくこの表から引く。**`sys.modules['__main__']` は直接実行時
# にはこのテスト自身**なので、`main.InstantaleApp = app_cls` がここのグローバル名を
# 書き換えてしまう。素朴に `type("InstantaleApp", (InstantaleApp,), {})` と書くと
# 2回目以降は「前回の派生クラス」から派生し、フックの層が積み上がって
# 同じ処理が何度も走る（実際にそれで1件誤判定した）。
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


def load_mod(path=MOD, name="quest_offer_mod"):
    spec = importlib.util.spec_from_file_location(name, path,
                                            submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, targets):
    """フックを本番と同じ形（メソッドの差し替え）でクラスに載せる。

    `targets` は `(フック名, 載せる先のクラス, メソッド名)`。**載せる先は毎回
    作り直した派生クラス**にする（前のテストで差し替えたものを持ち越さない）。
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


def setup(history=None, partner="62", in_conversation=True):
    """mod を適用し、NPC 62 と会話している状態の app を返す。"""
    # クラスは毎回作り直す。`__main__` に載せるのは mod が
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
    ctx = FakeCtx(os.path.join(HERE, os.pardir, "out", "test"))
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
    characters = {"62": Character(id="62", name="テストNPC D")}
    areas = {"7": Area("7", "テストの町A")}
    app = app_cls(World(characters, quests, areas))
    # 現在地は **id の文字列**で持たせる（`302_` の実測。エリアの
    # オブジェクトを直接持っているとは限らない）。
    app.player = Character(id="player", name="テストプレイヤー", current_area="7")
    # 会話の書き起こし（「この話から依頼を作る」の材料）。
    app.current_conversation_history = history if history is not None else []
    if in_conversation:
        # ゲームと同じ経路で会話に入る。ここで mod が相手を控える。
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
app.on_button_press(index_of(app, "offer"))
clock.settle()
quest_buttons = [b for b in app.buttons
                 if b["spec"].cls_name == "QuestChoiceManager"]
check("会話から開くと、その NPC 発でない依頼は間引かれる", not quest_buttons,
      [b["text"] for b in app.buttons])
check("「この話から依頼を作る」が先頭に出る",
      app.buttons and app.buttons[0].get(MARK) == "generate",
      [b["text"] for b in app.buttons])
check("依頼人の名前が文言に入る", "テストNPC D" in app.buttons[0]["text"],
      app.buttons[0]["text"])
check("掲示板をいじった後も HUD を塗り直す", app.hud.painted, app.hud.painted)

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

print()
if failures:
    print("{} 件失敗: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
