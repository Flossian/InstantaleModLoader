# -*- coding: utf-8 -*-
"""347_conversation_expressions をゲーム抜きで通す。

    python tools/tests/test_conversation_expressions.py

  宣言   … apply() で窓口 `expressions` に表情を見せると宣言する
  プロンプト … 返事の【返答の要素】に表情の説明を1行足す。第一声はふだんの顔に戻す
  型     … 返事の送り口だけ、返却型を表情の欄を足した子クラスに替える（pydantic は偽物）
  見せる … 返ってきた表情の差分を次のフレームで会話の立ち絵に出す。差分が無い・印が合わないならゲームの立ち絵
  当て直し … ゲームが立ち絵を出し直したら控えた表情を当て直す。ほかの人物の絵は触らない
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


class FakeClock(object):
    scheduled = []

    @staticmethod
    def schedule_once(fn, delay=0.0):
        FakeClock.scheduled.append(fn)

    @staticmethod
    def run_all():
        pending, FakeClock.scheduled = FakeClock.scheduled, []
        for fn in pending:
            fn(0)


kivy = types.ModuleType("kivy")
clock = types.ModuleType("kivy.clock")
clock.Clock = FakeClock
kivy.clock = clock
sys.modules.setdefault("kivy", kivy)
sys.modules["kivy.clock"] = clock

import instantale_modloader as ml               # noqa: E402
from instantale_modloader import expressions, llm, modnpc  # noqa: E402

MOD_DIR = os.path.join(MODS_DIR, "347_conversation_expressions")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    MANIFEST = json.load(fh)
spec = importlib.util.spec_from_file_location("conversation_expressions_under_test",
                                              os.path.join(MOD_DIR, MANIFEST["entry"]))
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽物
class Ctx(object):
    _mod = None

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.notes = []
        self.generation = "test"

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        real = ml.ModContext.logger(self, name, **kw)

        def write(message):
            self.notes.append(str(message))
            return real(message)
        return write

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def superseded(self):
        return False

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


registered = {}
installed = {}
modnpc.install = lambda ctx, write=None: None
modnpc.register = lambda owner, npc_id=None, **kw: registered.update(owner=owner, npc_id=npc_id, **kw)


def fake_watch(ctx, targets, install, label="llm", on_arm=None):
    for target in targets:
        install(target)
        installed[target] = ctx.hooks[target]


llm.watch_aliases = fake_watch


class FakeField(object):
    def __init__(self, default, description=None):
        self.default, self.description = default, description


made = []
pydantic = types.ModuleType("pydantic")
pydantic.Field = FakeField


def create_model(name, __base__=None, **fields):
    made.append((name, __base__, fields))
    return type(name, (__base__,), {"model_fields": dict(__base__.model_fields, **fields)})


pydantic.create_model = create_model
sys.modules["pydantic"] = pydantic


class Response(object):
    model_fields = {"content_violation": None, "action": None}


class Widget(object):
    def __init__(self, source):
        self.source = source


class Character(object):
    def __init__(self, cid, fullbody):
        self.id = cid
        self.image_src = {"fullbody": fullbody, "face": None}


class InstantaleApp(object):
    def __init__(self, characters, widget):
        self.world = types.SimpleNamespace(characters=characters)
        self.root = types.SimpleNamespace(character_image=widget)


main = sys.modules["__main__"]
main.InstantaleApp = InstantaleApp
real_find_hud = MOD.ui.find_hud
MOD.ui.find_hud = lambda app: app.root

base = tempfile.mkdtemp(prefix="conversation_expressions_")
try:
    folders = {}
    for cid in ("8", "15"):
        folder = os.path.join(base, cid)
        os.makedirs(os.path.join(folder, expressions.FOLDER))
        io.open(os.path.join(folder, expressions.SOURCE), "wb").write(b"generated " + cid.encode())
        io.open(os.path.join(folder, "reduced_color_image.png"), "wb").write(b"base")
        for name in expressions.VARIANTS:
            io.open(expressions.variant_path(folder, name), "wb").write(name.encode())
        expressions.seal(folder, expressions.stamp(folder))
        folders[cid] = folder
    portrait = {cid: os.path.join(folder, "reduced_color_image.png") for cid, folder in folders.items()}
    widget = Widget(portrait["8"])
    APP = InstantaleApp({cid: Character(cid, portrait[cid]) for cid in portrait}, widget)
    main.APP = APP

    expressions.reset()
    if hasattr(sys, MOD.SHOWN_ATTR):
        delattr(sys, MOD.SHOWN_ATTR)
    ctx = Ctx(tempfile.mkdtemp(prefix="conversation_expressions_ctx_"))
    MOD.apply(ctx)

    print("宣言")
    check("apply() で表情を見せると宣言する（343 が差分を描く）",
          expressions.wanters() == [MOD.OWNER], expressions.wanters())
    check("誰との会話にも効く層を modnpc に積む",
          registered.get("owner") == MOD.OWNER and registered.get("npc_id") == modnpc.ANY
          and callable(registered.get("prompt")))
    on_prompt = registered["prompt"]

    print("プロンプト")
    args = {"response_prompt_instruction": "【返答の要素】\n- 指示: ...\n"}
    on_prompt({"site": "conversation_facilitator", "npc_id": "8", "args": args})
    check("返事の【返答の要素】の終わりに表情の説明を1行足す",
          args["response_prompt_instruction"] == "【返答の要素】\n- 指示: ...\n" + MOD.GUIDE_LINE,
          args["response_prompt_instruction"])
    check("説明は5つの表情を名指しする", all(name in MOD.GUIDE_LINE for name in expressions.NAMES))
    args = {"response_prompt_instruction": "x", "messages": []}
    on_prompt({"site": "conversation_resolver", "npc_id": "8", "args": args})
    check("返事でないプロンプトには足さない", args["response_prompt_instruction"] == "x")

    print("型")
    send = installed[MOD.SEND_TARGETS[0]]
    passed = []

    def orig(manager_name, message, structure, **kw):
        passed.append(structure)
        return {"content_violation": False, "action": {"statement": "やあ"}, "expression": "smile"}

    on_prompt({"site": "conversation_facilitator", "npc_id": "8", "args": {"response_prompt_instruction": ""}})
    FakeClock.scheduled = []
    send(orig, "conversation_facilitator", [], Response)
    widened = passed[-1]
    check("返事の送り口は、表情の欄を足した子クラスで送る",
          widened is not Response and issubclass(widened, Response) and "expression" in widened.model_fields
          and widened.__name__ == "Response", widened.model_fields)
    field = made[-1][2]["expression"]
    check("表情の欄は str で受け（一覧に無い値で返事ごと検証に落とさない）、説明つき",
          field[0] is str and field[1].description == MOD.GUIDE, field)
    on_prompt({"site": "conversation_facilitator", "npc_id": "8", "args": {"response_prompt_instruction": ""}})
    send(orig, "conversation_facilitator", [], Response)
    check("同じ型は作り直さない", passed[-1] is widened and len(made) == 1, len(made))
    passed[:] = []
    send(orig, "conversation_facilitator", [], Response)
    check("控えは1回の送信で使い切る（プロンプトを組まなかった返事は型を替えず、前の相手の表情にしない）",
          passed == [Response], passed)
    passed[:] = []
    send(orig, "npc_detail_generater", [], Response)
    check("ほかの送り口は型を替えない", passed == [Response])
    passed[:] = []
    send(orig, "conversation_facilitator_in_quest", [], Response)
    check("プロンプトを組んでいない返事（控えの名前が違う）は型を替えない", passed == [Response])

    print("見せる")
    on_prompt({"site": "conversation_facilitator", "npc_id": "8", "args": {"response_prompt_instruction": ""}})
    FakeClock.scheduled = []
    send(orig, "conversation_facilitator", [], Response)
    check("立ち絵の差し替えは次のフレーム", widget.source == portrait["8"])
    FakeClock.run_all()
    check("返ってきた表情の差分を会話の立ち絵に出す",
          widget.source == expressions.variant_path(folders["8"], "smile"), widget.source)

    def answer(expression):
        def fn(manager_name, message, structure, **kw):
            return {"content_violation": False, "action": {"statement": "", "call_free_action": True},
                    "expression": expression}
        return fn

    def talk(fn, npc_id="8"):
        """返事を1回（プロンプトを組んでから送る。ゲームと同じ順）。"""
        on_prompt({"site": "conversation_facilitator", "npc_id": npc_id, "args": {"response_prompt_instruction": ""}})
        send(fn, "conversation_facilitator", [], Response)

    talk(answer("angry"))
    FakeClock.run_all()
    check("台詞が空（自由行動へ渡った）返事でも、返ってきた表情を出す",
          widget.source == expressions.variant_path(folders["8"], "angry"), widget.source)
    talk(answer("happy"))
    FakeClock.run_all()
    check("一覧に無い表情はふだんの顔（ゲームの立ち絵）", widget.source == portrait["8"], widget.source)
    check("表情の読み方（大文字と空白は均す・読めなければふだんの顔）",
          MOD.expression_of({"expression": " SAD "}) == "sad" and MOD.expression_of(None) == "neutral"
          and MOD.expression_of({"expression": 3}) == "neutral")

    talk(answer("surprised"))
    FakeClock.run_all()
    on_prompt({"site": "conversation_starter", "npc_id": "8", "args": {"messages": []}})
    FakeClock.run_all()
    check("第一声でふだんの顔に戻す", widget.source == portrait["8"], widget.source)

    print("当て直し")
    image_hook = ctx.hooks[MOD.IMAGE_TARGET]
    talk(answer("sad"))
    FakeClock.run_all()

    def game_update(self, character_id):
        widget.source = portrait[str(character_id)]

    image_hook(game_update, APP, "8")
    check("ゲームが出し直した直後はゲームの立ち絵", widget.source == portrait["8"])
    FakeClock.run_all()
    check("次のフレームで控えた表情を当て直す",
          widget.source == expressions.variant_path(folders["8"], "sad"), widget.source)
    image_hook(game_update, APP, "15")
    FakeClock.run_all()
    check("ほかの人物を出し直したときは当て直さない", widget.source == portrait["15"], widget.source)
    on = registered.get("on") or {}
    for site in ("conversation_end", "conversation_start"):
        widget.source = portrait["8"]
        talk(answer("sad"))
        FakeClock.run_all()
        on[site]({"site": site, "npc_id": "8"})
        image_hook(game_update, APP, "8")
        FakeClock.run_all()
        check("会話の{}の後は控えた表情を当て直さない（次の会話・パーティー会話の立ち絵を前の表情で上書きしない）".format(
            "終わり" if site == "conversation_end" else "始め"), widget.source == portrait["8"], widget.source)
    widget.source = portrait["15"]
    talk(answer("smile"))
    FakeClock.run_all()
    check("立ち絵がほかの人物の絵なら触らない", widget.source == portrait["15"], widget.source)

    widget.source = portrait["8"]
    io.open(os.path.join(folders["8"], expressions.SOURCE), "wb").write(b"redrawn")
    talk(answer("smile"))
    FakeClock.run_all()
    check("立ち絵が描き直されて印の合わない差分は使わない", widget.source == portrait["8"], widget.source)
    check("例外を出していない", ctx.errors == [], ctx.errors)
finally:
    MOD.ui.find_hud = real_find_hud
    main.__dict__.pop("InstantaleApp", None)
    main.__dict__.pop("APP", None)
    expressions.reset()
    shutil.rmtree(base, ignore_errors=True)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
