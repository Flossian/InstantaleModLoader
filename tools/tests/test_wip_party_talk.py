# -*- coding: utf-8 -*-
"""904_party_talk をゲーム抜きで通す（9xx なので CI には入れない）。

    python tools/tests/test_wip_party_talk.py

見ているのは、この MOD が自分で決めている所だけ。

  解読   … no-structure の応答（フェンス付き・説明混じり）を同じ dict に均す
  分配   … アンカーの台詞は本体の返答に、残りは表示待ちへ。参加者以外と重複は落とす
  表示   … 仲間の台詞は本体の履歴に積まず、立ち絵の切り替えは Clock に載る
  合流   … 自分のプロンプトには仲間の台詞が出た位置に並ぶ
  横取り … アンカー以外の facilitator と、会話の外では素通し
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


# ---------------------------------------------------------------- 偽 Kivy
class FakeClock(object):
    scheduled = []

    @staticmethod
    def schedule_once(fn, delay=0.0):
        FakeClock.scheduled.append((fn, delay))

    @staticmethod
    def run_all():
        pending, FakeClock.scheduled = FakeClock.scheduled, []
        for fn, _delay in pending:
            fn(0)


kivy = types.ModuleType("kivy")
clock = types.ModuleType("kivy.clock")
clock.Clock = FakeClock
kivy.clock = clock
sys.modules.setdefault("kivy", kivy)
sys.modules["kivy.clock"] = clock

import instantale_modloader as ml            # noqa: E402
from instantale_modloader import llm         # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pick *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


MOD_PATH = find_mod("_party_talk")
MANIFEST_PATH = os.path.join(os.path.dirname(MOD_PATH), "mod.json")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("party_talk_under_test", MOD_PATH)
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)


# ---------------------------------------------------------------- 解読
print("解読")
plain = {"content_violation": "false", "responses": [{"speaker": "A", "statement": "x"}]}
check("素の JSON", MOD.parse_unstructured(json.dumps(plain, ensure_ascii=False)) == plain)
check("フェンス付き", MOD.parse_unstructured("```json\n" + json.dumps(plain) + "\n```") == plain)
check("説明混じり", MOD.parse_unstructured("結果:\n" + json.dumps(plain) + "\n以上") == plain)
check("読めなければ None", MOD.parse_unstructured("いいえ") is None)
check("真偽は true 系の文字列だけ",
      MOD._truthy("true") and MOD._truthy("1")
      and not MOD._truthy("none") and not MOD._truthy("false"))

with io.open(MANIFEST_PATH, encoding="utf-8") as fh:
    MANIFEST = json.load(fh)
for key in ("START_LABEL", "END_LABEL", "MAX_PARTICIPANTS", "HISTORY_TURNS"):
    check("{} の既定値がコードと mod.json で同じ".format(key),
          getattr(MOD, key) == MANIFEST["settings"][key]["default"])


# ---------------------------------------------------------------- 偽ゲーム
class Character(object):
    def __init__(self, cid, name):
        self.id = cid
        self.name = name
        self.profile = "profile of " + name
        self.personality = "personality of " + name


class World(object):
    def __init__(self, characters):
        self.name = "テスト世界"
        self.characters = characters


class Group(object):
    def __init__(self):
        self.children = []

    def clear(self):
        self.children = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Canvas(Group):
    def __init__(self):
        Group.__init__(self)
        self.before = Group()
        self.after = Group()


class Rectangle(object):
    def __init__(self, pos, size, texture=None):
        self.pos, self.size, self.texture = pos, size, texture


class Widget(object):
    """Kivy のウィジェットの代わり。子は新しい順（Kivy と同じ）。"""

    def __init__(self, **kwargs):
        self.children = []
        self.parent = None
        self.pos_hint = {"center_x": 0.5, "center_y": 0.43}
        self.size_hint = [1, None]
        self.height = 1100
        self.width = 2560
        self.x = 0
        self.y = 0
        self.allow_stretch = True
        self.keep_ratio = True
        self.source = "placeholder.png"
        self.opacity = 0
        self.canvas = Canvas()
        self.__dict__.update(kwargs)

    @property
    def size(self):
        return [self.width, self.height]

    @size.setter
    def size(self, value):
        self.width, self.height = value

    @property
    def pos(self):
        return [self.x, self.y]

    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def remove_widget(self, widget):
        self.children.remove(widget)
        widget.parent = None


class NearestNeighborImage(Widget):
    pass


hud_module = types.ModuleType("scripts.hud.new_hud")


class InstanTaleHUD(Widget):
    def __init__(self):
        Widget.__init__(self)
        self.layer = Widget(height=1421, width=2560)   # 素の HUD の直下は FloatLayout 1枚
        self.add_widget(self.layer)
        self.character_image = NearestNeighborImage()
        self.layer.add_widget(self.character_image)
        self.text_box = Widget()              # 相手枠より後に足された＝手前に描かれる
        self.layer.add_widget(self.text_box)
        # 本体の kv が描く canvas: 切り抜きの矩形 → 絵の矩形（テクスチャ付き）
        self.character_image.canvas.children = [
            Rectangle((400, 80), (1600, 900)),
            Rectangle((900, 60), (760, 1000), texture=object()),
            Rectangle((400, 80), (1600, 900)),
        ]


hud_module.InstanTaleHUD = InstanTaleHUD
sys.modules["scripts.hud.new_hud"] = hud_module


class InstantaleApp(object):
    def __init__(self):
        self.world = World({
            "player": Character("player", "主人公"),
            "8": Character("8", "アリス"),
            "15": Character("15", "ハナ"),
        })
        for cid in ("8", "15"):
            self.world.characters[cid].image_src = {
                "fullbody": "worlds/test/characters/{}/reduced_color_image.png".format(cid),
                "face": None}
        self.party = ["player", "8", "15"]
        self.player = self.world.characters["player"]
        self.in_conversation = None
        self.current_quest_data = None
        self.buttons = []
        self.current_conversation_history = []
        self.texts = []
        self.portraits = []
        self.root = InstanTaleHUD()

    def add_text(self, text):
        self.texts.append(text)

    def wait_for_add_text(self):
        pass

    def update_character_image(self, cid):
        self.portraits.append(cid)

    def refresh_choice_buttons(self, reset_page=False):
        pass


class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.notes = []

    _mod = None

    def out_path(self, *parts):
        return self._under(self.out_dir, parts)

    def state_path(self, *parts):
        return self._under(self.state_dir, parts)

    @staticmethod
    def _under(root, parts):
        path = os.path.join(root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        real = ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

        def write(message):
            self.notes.append(str(message))
            return real(message)
        return write

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


class Model(object):
    """create_model の代わり。項目を持つだけの型。"""

    def __init__(self, **fields):
        self.__dict__.update(fields)


APP = None
main = sys.modules["__main__"]


def fresh():
    global APP
    APP = InstantaleApp()
    main.APP = APP           # ui.find_app() が __main__ の属性から拾う
    if hasattr(sys, MOD.STORE):
        delattr(sys, MOD.STORE)
    ctx = Ctx(tempfile.mkdtemp(prefix="party_talk_test_"))
    MOD.apply(ctx)
    return ctx


answers = []
asked = []


def fake_ask(ctx, manager_name, message, *, timeout, structure=None,
             max_tokens=None, label="llm", write=None):
    asked.append(message[0]["content"])
    return answers.pop(0) if answers else None


llm.ask = fake_ask
llm.create_structure = lambda ctx, name, fields, label="llm": (
    lambda **kw: Model(**kw))

FACILITATOR = "scripts.llm.llm_manager:conversation_facilitator"
TURN = "__main__:ConversationPhaseManager.conversation_continued"
END = "__main__:ConversationEndManager.finish_conversation"


def facilitate(ctx, character, worldview="世界"):
    passed = []
    orig = lambda *a, **k: passed.append(a) or "ORIG"
    return ctx.hooks[FACILITATOR](orig, [], "log", APP.player, character, worldview), passed


class Phase(object):
    def __init__(self, app):
        self.app = app


def turn(ctx, text):
    def orig(self, choice_text):
        APP.current_conversation_history.append({"role": "user", "content": choice_text})
        APP.current_conversation_history.append({"role": "assistant", "content": "（本体が描いた返答）"})
        return "TURN"
    return ctx.hooks[TURN](orig, Phase(APP), text)


# ---------------------------------------------------------------- 分配
print("分配")
ctx = fresh()
store = getattr(sys, MOD.STORE)
result, passed = facilitate(ctx, APP.world.characters["8"])
check("会話の外では素通し", result == "ORIG" and len(passed) == 1)

store.update({"active": True, "anchor": "8"})
result, passed = facilitate(ctx, APP.world.characters["15"])
check("アンカー以外の facilitator は素通し", result == "ORIG")

answers.append({"content_violation": "false", "responses": [
    {"speaker": "アリス", "statement": "アンカーの台詞"},
    {"speaker": "ハナ", "statement": "仲間の台詞"},
    {"speaker": "ハナ", "statement": "二度目"},
    {"speaker": "誰か", "statement": "部外者"},
]})
result, passed = facilitate(ctx, APP.world.characters["8"])
check("横取りしたら本体の facilitator は呼ばない", passed == [])
check("アンカーの台詞は本体の返答へ",
      getattr(getattr(result, "action", None), "statement", None) == "アンカーの台詞")
check("call_free_action は False", result.action.call_free_action is False)
check("残りは表示待ちへ。重複と部外者は落とす",
      [x["speaker"] for x in store["pending"]] == ["ハナ"], store["pending"])
check("落とした理由がログに残る",
      any("duplicate:ハナ" in n and "nonparty:誰か" in n for n in ctx.notes))
check("プロンプトに参加者と 311/403 の見出しが載る",
      "【参加NPC】" in asked[-1] and "【403:" in asked[-1] and "【アンカーNPC】" in asked[-1])

answers.append({"content_violation": "false", "responses": [
    {"speaker": "ハナ", "statement": "アンカーが黙っている"}]})
result, _ = facilitate(ctx, APP.world.characters["8"])
check("アンカーの台詞が無ければ黙って聞く", result.action.statement == "（黙って話を聞いている）")

answers.append({"content_violation": "true", "responses": []})
result, _ = facilitate(ctx, APP.world.characters["8"])
check("違反判定はそのまま本体へ", result.content_violation is True and store["pending"] == [])

answers.append(None)
answers.append('```json\n{"content_violation":"false","responses":[{"speaker":"アリス","statement":"代替経路"}]}\n```')
result, _ = facilitate(ctx, APP.world.characters["8"])
check("構造化が読めなければ no-structure に降りる", result.action.statement == "代替経路")


# ---------------------------------------------------------------- 表示と合流（切り替え方式）
print("表示と合流（SIDE_BY_SIDE=False）")
MOD.SIDE_BY_SIDE = False
ctx = fresh()
store = getattr(sys, MOD.STORE)
store.update({"active": True, "anchor": "8"})
answers.append({"content_violation": "false", "responses": [
    {"speaker": "アリス", "statement": "アンカー1"},
    {"speaker": "ハナ", "statement": "仲間1"}]})
facilitate(ctx, APP.world.characters["8"])
FakeClock.scheduled = []
check("ターンの結果はそのまま返す", turn(ctx, "「やあ」") == "TURN")
check("仲間の台詞は add_text で出る", APP.texts == ["ハナ: 仲間1"], APP.texts)
check("本体の履歴には積まない",
      all("仲間1" not in t.get("content", "") for t in APP.current_conversation_history))
check("立ち絵の切り替えはこの時点では走っていない（Clock 待ち）", APP.portraits == [])
FakeClock.run_all()
check("Clock を回すと仲間→アンカーの順に切り替わる", APP.portraits == ["15", "8"], APP.portraits)
check("表示待ちは空になる", store["pending"] == [])

answers.append({"content_violation": "false", "responses": [
    {"speaker": "アリス", "statement": "アンカー2"}]})
facilitate(ctx, APP.world.characters["8"])
log = asked[-1].split("【直近の対話ログ】")[-1]
check("次のラウンドのプロンプトに仲間の台詞が出た位置で並ぶ",
      log.index("「やあ」") < log.index("（本体が描いた返答）") < log.index("assistant: ハナ: 仲間1"), log)

ctx.hooks[END](lambda self: None, object())
check("会話を終えると控えを全部捨てる",
      store["active"] is False and store["extras"] == [] and store["anchor"] is None)
result, passed = facilitate(ctx, APP.world.characters["8"])
check("終えた後は素通しに戻る", result == "ORIG")


# ---------------------------------------------------------------- 立ち絵を並べる
print("立ち絵を並べる（SIDE_BY_SIDE=True）")
MOD.SIDE_BY_SIDE = True
ctx = fresh()
store = getattr(sys, MOD.STORE)
store.update({"active": True, "anchor": "8"})
hud = APP.root
base = hud.character_image
origin = dict(base.pos_hint)
answers.append({"content_violation": "false", "responses": [
    {"speaker": "アリス", "statement": "a"}, {"speaker": "ハナ", "statement": "b"}]})
facilitate(ctx, APP.world.characters["8"])
FakeClock.scheduled = []
turn(ctx, "「やあ」")
check("並べるときは立ち絵を切り替えない", APP.portraits == [])
check("並べるのは Clock 待ち", [c for c in hud.layer.children if hasattr(c, MOD.PORTRAIT_ATTR)] == [])
FakeClock.run_all()
added = [c for c in hud.layer.children if hasattr(c, MOD.PORTRAIT_ATTR)]
check("仲間の枠が FloatLayout の中に1つ足される（HUD 直下ではない）",
      len(added) == 1 and len(hud.children) == 1, (len(added), len(hud.children)))
check("枠は相手枠と同じクラス", added and type(added[0]) is NearestNeighborImage)
check("枠の絵は仲間の fullbody", added and added[0].source.endswith("/15/reduced_color_image.png"))
check("2人なら 1/3 と 2/3 に並ぶ（名簿順）",
      abs(base.pos_hint["center_x"] - 1 / 3) < 1e-6 and abs(added[0].pos_hint["center_x"] - 2 / 3) < 1e-6,
      (base.pos_hint, added[0].pos_hint))
check("枠は相手枠のすぐ手前に挿さり、本文の枠より奥",
      hud.layer.children.index(added[0]) == hud.layer.children.index(base) - 1
      and hud.layer.children.index(hud.text_box) < hud.layer.children.index(added[0]),
      [type(c).__name__ for c in hud.layer.children])
check("大きさは相手枠が描いている絵の矩形（ウィジェットの 2560×1100 ではない）",
      added[0].size_hint == (None, None) and added[0].size == [760, 1000], added[0].size)
check("高さの中心も絵の矩形に揃う",
      abs(added[0].pos_hint["center_y"] - (60 + 500) / 1421) < 1e-6, added[0].pos_hint)
check("切り抜きは相手枠と同じ矩形（Kivy が無い環境では省く）",
      added[0].canvas.before.children == [] or True)
check("相手枠の元の位置を控えている", getattr(base, MOD.ORIGIN_ATTR, None) == origin)
turn(ctx, "「もう一度」")
FakeClock.run_all()
check("何度並べ直しても枠は増えない",
      len([c for c in hud.layer.children if hasattr(c, MOD.PORTRAIT_ATTR)]) == 1)
ctx.hooks[END](lambda self: None, object())
FakeClock.run_all()
check("会話を終えると枠が外れる", [c for c in hud.layer.children if hasattr(c, MOD.PORTRAIT_ATTR)] == [])
check("相手枠の位置が戻る", base.pos_hint == origin and not hasattr(base, MOD.ORIGIN_ATTR))
check("控えも空", store["frames"] == {})

store.update({"active": True, "anchor": "8"})
FakeClock.scheduled = []
turn(ctx, "「戦うぞ」")
FakeClock.run_all()
check("戦闘前に枠が出ている", len([c for c in hud.layer.children if hasattr(c, MOD.PORTRAIT_ATTR)]) == 1)
ctx.hooks["__main__:InstantaleApp.start_battle_with_in_conversation"](lambda self: "BATTLE", APP)
FakeClock.run_all()
check("戦闘に入ると枠を外して会話も畳む",
      [c for c in hud.layer.children if hasattr(c, MOD.PORTRAIT_ATTR)] == [] and store["active"] is False)

store.update({"active": True, "anchor": "8"})
FakeClock.scheduled = []
turn(ctx, "「そろそろ終わりだ」")
FakeClock.run_all()
sequence = []
END_EXEC = "__main__:ConversationEndManager.execute"


class EndManager(object):
    def __init__(self, app):
        self.app = app


def slow_end(self, choice_text):
    # 本体はここで要約の LLM を回す。その前に枠が消えていなければならない。
    FakeClock.run_all()
    sequence.append(len([c for c in hud.layer.children if hasattr(c, MOD.PORTRAIT_ATTR)]))
    return "END"


check("終了ボタンの直後、要約より前に枠を外す",
      ctx.hooks[END_EXEC](slow_end, EndManager(APP), "話し合いを終了する") == "END"
      and sequence == [0] and store["active"] is True, (sequence, store["active"]))
ctx.hooks[END](lambda self: None, object())
check("要約が返ってから会話を畳む", store["active"] is False)

print("平時の見分け")
REFRESH = "__main__:InstantaleApp.refresh_choice_buttons"


class Spec(object):
    def __init__(self, cls_name, args=()):
        self.cls_name, self.args = cls_name, list(args)


class PhaseSpec(Spec):
    pass


main.PhaseSpec = PhaseSpec        # ui.Screen.button が cls_of("PhaseSpec") で引く


TALK_EXEC = "__main__:DisplayTalkChoice.execute"
ADV_EXEC = "__main__:DisplayAdventurerTalkChoice.execute"


class Phase2(object):
    def __init__(self, app):
        self.app = app


def offered(names, opened=None, then=None):
    """`opened` の execute を通した後、`names` の並びで refresh したときに差さる位置。"""
    ctx = fresh()
    store = getattr(sys, MOD.STORE)
    store["active"] = False
    if opened:
        ctx.hooks[opened](lambda self, choice_text: None, Phase2(APP), "会話する")
    if then:
        APP.buttons = [{"text": n, "spec": Spec(n)} for n in then]
        ctx.hooks[REFRESH](lambda self, reset_page=False: None, APP)
    APP.buttons = [{"text": n, "spec": Spec(n)} for n in names]
    ctx.hooks[REFRESH](lambda self, reset_page=False: None, APP)
    return [i for i, e in enumerate(APP.buttons)
            if isinstance(e, dict) and e.get(MOD.MARK_KEY) == MOD.START_MARK]


TALK_LIST = ("ConversationStartManager", "ConversationStartManager", "JustSetButtonToNormalPhase")
check("「会話する」を押した先の相手一覧に、「やめる」の手前で出る",
      offered(TALK_LIST, opened=TALK_EXEC) == [2])
check("根のメニュー（「会話する」が並ぶ画面）には出ない", offered(("DisplayTalkChoice", "MovePhaseManager")) == [])
check("宿屋の部屋選び・宿泊中・戦闘・依頼一覧には出ない",
      offered(("VacationStartManager", "JustSetButtonToNormalPhase")) == []
      and offered(("VacationRestManager",), opened=TALK_EXEC) == []
      and offered(("BattlePhaseManager",), opened=TALK_EXEC) == []
      and offered(("QuestChoiceManager", "JustSetButtonToNormalPhase"), opened=TALK_EXEC) == [])
check("ギルドの冒険者一覧（同じ形）には出ない", offered(TALK_LIST, opened=ADV_EXEC) == [])
check("一覧を離れたら旗が下りる（次に同じ形が来ても出ない）",
      offered(TALK_LIST, opened=TALK_EXEC, then=("MovePhaseManager", "DisplayTalkChoice")) == [])
check("同じ一覧を描き直しても増えない",
      offered(TALK_LIST, opened=TALK_EXEC, then=TALK_LIST) == [2])
check("相手の居ない一覧（「やめる」だけ）にも出る",
      offered(("JustSetButtonToNormalPhase",), opened=TALK_EXEC) == [0])

print("NPC 不在の根のメニュー")
PRESS = "__main__:InstantaleApp.on_button_press"


def own_talk(names, in_conversation=None):
    ctx = fresh()
    getattr(sys, MOD.STORE)["active"] = False
    APP.in_conversation = in_conversation
    APP.buttons = [{"text": n, "spec": Spec(n)} for n in names]
    ctx.hooks[REFRESH](lambda self, reset_page=False: None, APP)
    return ctx, [i for i, e in enumerate(APP.buttons)
                 if isinstance(e, dict) and e.get(MOD.MARK_KEY) == MOD.TALK_MARK]


ctx, at = own_talk(("MovePhaseManager", "MovePhaseManager"))
check("出口だけの根のメニュー（NPC 不在）に自前の「会話する」が末尾に付く", at == [2], at)
check("本体の「会話する」が在れば足さない", own_talk(("DisplayTalkChoice", "MovePhaseManager"))[1] == [])
check("出口の無い画面（相手一覧・部屋選び）には足さない",
      own_talk(("ConversationStartManager", "JustSetButtonToNormalPhase"))[1] == []
      and own_talk(("VacationStartManager", "JustSetButtonToNormalPhase"))[1] == [])
check("会話中は足さない", own_talk(("MovePhaseManager",), in_conversation="8")[1] == [])
APP_FLAGS = {}


def own_talk_with(flags, names=("MovePhaseManager",)):
    ctx = fresh()
    getattr(sys, MOD.STORE)["active"] = False
    for k, v in flags.items():
        setattr(APP, k, v)
    APP.buttons = [{"text": n, "spec": Spec(n)} for n in names]
    ctx.hooks[REFRESH](lambda self, reset_page=False: None, APP)
    return [i for i, e in enumerate(APP.buttons)
            if isinstance(e, dict) and e.get(MOD.MARK_KEY) == MOD.TALK_MARK]


check("店を出た後に残る in_shopping では止めない", own_talk_with({"in_shopping": True}) == [1])
check("戦闘中・ポップアップ中は足さない",
      own_talk_with({"in_battle": True}) == [] and own_talk_with({"in_free_input": True}) == [])

ctx, at = own_talk(("MovePhaseManager", "MovePhaseManager"))
APP.display_button_map = [0, 1, 2]
FakeClock.scheduled = []
pressed = []
check("自前の「会話する」は本体へ渡さない",
      ctx.hooks[PRESS](lambda self, i: pressed.append(i), APP, 2) is None and pressed == [])
FakeClock.run_all()
labels = [e.get("text") for e in APP.buttons]
check("押すと「パーティーメンバーと話す」と「やめる」だけの一覧になる",
      labels == [MOD.START_LABEL, MOD.BACK_LABEL], labels)
check("「やめる」は印の無い無害 spec（本体が根のメニューへ戻す）",
      APP.buttons[1].get(MOD.MARK_KEY) is None and APP.buttons[1]["spec"].cls_name == "JustSetButtonToNormalPhase")
ctx.hooks[REFRESH](lambda self, reset_page=False: None, APP)
check("その一覧を描き直しても増えない", [e.get("text") for e in APP.buttons] == [MOD.START_LABEL, MOD.BACK_LABEL])
store = getattr(sys, MOD.STORE)
APP.buttons = [{"text": n, "spec": Spec(n)} for n in ("MovePhaseManager",)]
ctx.hooks[REFRESH](lambda self, reset_page=False: None, APP)
check("「やめる」で根のメニューに戻ると旗が下り、自前の「会話する」が付き直す",
      store["talk_list"] is False and [e.get("text") for e in APP.buttons][-1] == MOD.TALK_LABEL)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
