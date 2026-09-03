# -*- coding: utf-8 -*-
"""133_ui_area_difficulty をゲーム抜きで通す。

    python tools/tests/test_ui_area_difficulty.py

偽の app / PhaseSpec / DisplayAreaMoveChoice / AreaMoveCofirmation と
偽の `scripts.functions.get_quest_difficulties` を差し込み、次を確認する。

  一覧   … 行き先のボタンに帯が付く（`陽光の砦（適正Lv 21〜30）`）。
           最小=最大なら1つの数。依頼の無い土地（未訪問）は枠から見積もり
           （`黄金の砂漠（適正Lv 推定 48〜57）`）、枠の無い土地は「不明」。
           `AreaMoveCofirmation` 以外のボタンと spec / args には触らない。
           開き直しても二重に化けない。難易度が育てば表示が追随する
  確認   … 押された文字列は素の名前に戻してゲームへ渡す。
           確認画面の文言に土地の名前があれば帯を添え、無ければ1行足す。
           窓の外の add_text には触らない
  設定   … SHOW=False で一覧も確認画面も素のまま。
           BAND_UNKNOWN が空なら依頼の無い土地は名前のまま。
           CONFIRM_TEXT が空なら1行足さない。LEVEL_OFFSET=0 で難易度そのもの
  落とし所 … ゲームの関数が無ければ world.quests の走査で同じ帯が出る
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
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


MOD = find_mod("_ui_area_difficulty")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Area:
    def __init__(self, area_id, name, connections=()):
        self.id = area_id
        self.name = name
        self.nodes = {}
        self.connections = list(connections)


class Quest(dict):
    pass


class World:
    def __init__(self, areas, quests):
        self.areas = areas
        self.quests = quests
        self.name = "テスト世界"


class Player:
    def __init__(self, area):
        self.current_area = area
        self.gold = 5000


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class DisplayAreaMoveChoice:
    """行き先の一覧。`connections` を読む（実測。HANDOVER §3）。"""

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.update_button_display()
        return None

    def update_button_display(self):
        current = self.app.player.current_area
        buttons = []
        for area_id in current.connections:
            area = self.app.world.areas[str(area_id)]
            buttons.append({"text": area.name,
                            "spec": PhaseSpec("AreaMoveCofirmation", [str(area_id)])})
        buttons.append({"text": "やめる",
                        "spec": PhaseSpec("JustSetButtonToNormalPhase", [])})
        self.app.buttons = buttons
        self.app.refresh_choice_buttons(reset_page=True)


class AreaMoveCofirmation:
    """徒歩と馬車が並ぶ確認画面。文言の出し方はクラス属性で切り替える。"""

    #: 確認画面でゲームが出す文言（None なら何も出さない）。実機の文言は未実測。
    line = None

    def __init__(self, app, target_area_id):
        self.app = app
        self.target_area_id = target_area_id

    def execute(self, choice_text):
        self.app.confirmed.append(choice_text)
        if type(self).line:
            self.app.add_text(type(self).line.format(
                name=self.app.world.areas[str(self.target_area_id)].name))
        self.update_button_display()
        return None

    def update_button_display(self):
        self.app.buttons = [
            {"text": "徒歩(3ヵ月)",
             "spec": PhaseSpec("AreaMoveManager", [self.target_area_id, "on_foot"])},
            {"text": "馬車(1000G)",
             "spec": PhaseSpec("AreaMoveManager", [self.target_area_id, "coach"])},
            {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class InstantaleApp:
    def __init__(self, world):
        self.world = world
        self.player = Player(world.areas["0"])
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.confirmed = []
        self.refreshes = 0

    def add_text(self, context):
        self.texts.append(context)

    def process_choice(self, function, choice_text=""):
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def on_button_press(self, button_index):
        entry = self.buttons[button_index]
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), entry.get("text"))


BASES = {"app": InstantaleApp, "choice": DisplayAreaMoveChoice,
         "confirm": AreaMoveCofirmation}


class FakeClock:
    def __init__(self):
        self.onces = []

    def schedule_interval(self, callback, timeout):
        pass

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def settle(self):
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
    sys.modules.pop("kivy.app", None)
    return clock


def install_functions(present=True):
    """`scripts.functions.get_quest_difficulties`。ゲーム自身と同じ絞り込み。"""
    sys.modules.pop("scripts.functions", None)
    if not present:
        return None
    module = types.ModuleType("scripts.functions")
    module.calls = []

    def get_quest_difficulties(area, world, include_completed=True):
        module.calls.append(str(area.id))
        return [q["difficulty"] for q in world.quests.values()
                if str(q["neighboring_settlement_id"]) == str(area.id)]

    module.get_quest_difficulties = get_quest_difficulties
    sys.modules.setdefault("scripts", types.ModuleType("scripts"))
    sys.modules["scripts.functions"] = module
    return module


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="ui_area_difficulty_mod"):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def install(hooks, targets):
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


CLOCK = install_fake_kivy()
LOG_PATH = os.path.join(OUT_DIR, "ui_area_difficulty.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def quest(qid, area_id, difficulty):
    return Quest(id=qid, neighboring_settlement_id=str(area_id),
                 difficulty=difficulty, config={"status": "incomplete"})


def make_world():
    areas = {
        "0": Area("0", "始まりの町", connections=["1", "2", "3", "21"]),
        "1": Area("1", "陽光の砦"),
        "2": Area("2", "風鳴りの村"),
        "3": Area("3", "黄金の砂漠"),    # 依頼が無い（未訪問）。枠 3 → range(47, 57)
        "21": Area("21", "雲上神殿"),    # 依頼が無く、枠も無い（エディタで足した街）
    }
    quests = {
        "10": quest("10", "1", 20), "11": quest("11", "1", 29), "12": quest("12", "1", 25),
        "13": quest("13", "2", 5),
        "14": quest("14", "0", 3), "15": quest("15", "0", 4),
    }
    return World(areas, quests)


def setup(configure=None, functions=True, confirm_line=None):
    """mod を適用し、行き先一覧が開いた状態の app を返す。"""
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    choice_cls = type("DisplayAreaMoveChoice", (BASES["choice"],), {})
    confirm_cls = type("AreaMoveCofirmation", (BASES["confirm"],), {"line": confirm_line})

    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.DisplayAreaMoveChoice = choice_cls
    main.AreaMoveCofirmation = confirm_cls
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec
    install_functions(functions)

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
    # 控えは `sys` に残るので、場面ごとに捨てる。
    sys.__dict__.pop("__instantale_ui_area_difficulty_store__", None)

    module = load_mod()
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:DisplayAreaMoveChoice.update_button_display", choice_cls,
         "update_button_display"),
        ("__main__:AreaMoveCofirmation.__init__", confirm_cls, "__init__"),
        ("__main__:AreaMoveCofirmation.execute", confirm_cls, "execute"),
        ("__main__:InstantaleApp.add_text", app_cls, "add_text"),
    ))

    world = make_world()
    app = app_cls(world)
    main.current_app = app
    app.process_choice(choice_cls(app), "他の土地へ行く")
    CLOCK.settle()
    return module, ctx, app, choice_cls, confirm_cls


def texts_of(app):
    return [entry.get("text") for entry in app.buttons]


def press(app, text):
    for index, entry in enumerate(app.buttons):
        if entry.get("text") == text:
            app.on_button_press(index)
            CLOCK.settle()
            return
    raise AssertionError("no such button: {!r} in {}".format(text, texts_of(app)))


# ================================================================ 一覧
print("[既定] 行き先のボタンに帯が付く")
module, ctx, app, choice_cls, confirm_cls = setup()
check("帯は最小〜最大に 1 を足したもの", texts_of(app)[0] == "陽光の砦（適正Lv 21〜30）",
      texts_of(app))
check("最小=最大なら1つの数", texts_of(app)[1] == "風鳴りの村（適正Lv 6）", texts_of(app))
check("未訪問の街は枠から見積もる（range(47, 57) → Lv 48〜57）",
      texts_of(app)[2] == "黄金の砂漠（適正Lv 推定 48〜57）", texts_of(app))
check("枠の無い街は「不明」", texts_of(app)[3] == "雲上神殿（適正Lv 不明）",
      texts_of(app))
check("「やめる」には触らない", texts_of(app)[4] == "やめる")
check("spec と args は元のまま",
      app.buttons[0]["spec"].cls_name == "AreaMoveCofirmation"
      and app.buttons[0]["spec"].args == ["1"])
check("次のフレームで塗り直している", app.to_display_buttons == texts_of(app),
      app.to_display_buttons)
check("ゲームの関数に聞いている", sys.modules["scripts.functions"].calls[:4] == ["1", "2", "3", "21"],
      sys.modules["scripts.functions"].calls)
check("例外は出ていない", not ctx.errors, ctx.errors)
check("ログに書き換えが残る", "label: '陽光の砦' -> '陽光の砦（適正Lv 21〜30）'" in read_log())

print("[開き直し] 二重に化けない・難易度が育てば追随する")
refreshes = app.refreshes
app.process_choice(choice_cls(app), "他の土地へ行く")
CLOCK.settle()
check("同じ表示のまま", texts_of(app)[0] == "陽光の砦（適正Lv 21〜30）", texts_of(app))
check("ゲームが素の名前で組み直すので、塗り直しは1回", app.refreshes == refreshes + 2,
      (app.refreshes, refreshes))
app.world.quests["11"]["difficulty"] = 40
app.process_choice(choice_cls(app), "他の土地へ行く")
CLOCK.settle()
check("育った後の値が出る", texts_of(app)[0] == "陽光の砦（適正Lv 21〜41）", texts_of(app))
app.world.quests["11"]["difficulty"] = 29

print("[ゲームが組み直さないビルド] 自分のラベルの上でもう一度来る")
app.process_choice(choice_cls(app), "他の土地へ行く")
CLOCK.settle()
hook = ctx.hooks["__main__:DisplayAreaMoveChoice.update_button_display"]
refreshes = app.refreshes
hook(lambda self: None, choice_cls(app))
CLOCK.settle()
check("二重に付かない", texts_of(app)[0] == "陽光の砦（適正Lv 21〜30）", texts_of(app))
check("変わらなければ塗り直さない", app.refreshes == refreshes, (app.refreshes, refreshes))
check("ログに already labelled", "already labelled" in read_log())

print("[名前が違う] ゲームが別の文字列を出していたら触らない")
app.process_choice(choice_cls(app), "他の土地へ行く")
CLOCK.settle()
app.buttons[0]["text"] = "陽光の砦（封鎖中）"
hook(lambda self: None, choice_cls(app))
CLOCK.settle()
check("知らない文字列はそのまま", texts_of(app)[0] == "陽光の砦（封鎖中）", texts_of(app))
check("理由がログに残る", "neither the name" in read_log())

# ================================================================ 確認画面
print("[確認画面] 押された文字列は素の名前に戻る・名前を含む文言に帯が添う")
module, ctx, app, choice_cls, confirm_cls = setup(confirm_line="{name}へ向かう。")
press(app, "陽光の砦（適正Lv 21〜30）")
check("ゲームには素の名前が渡る", app.confirmed == ["陽光の砦"], app.confirmed)
check("文言に帯が添う", app.texts == ["陽光の砦（適正Lv 21〜30）へ向かう。"], app.texts)
check("徒歩・馬車はゲームのまま", texts_of(app) == ["徒歩(3ヵ月)", "馬車(1000G)", "やめる"],
      texts_of(app))
app.add_text("陽光の砦は遠い。")
check("窓の外の文言には触らない", app.texts[-1] == "陽光の砦は遠い。", app.texts)
check("例外は出ていない", not ctx.errors, ctx.errors)

print("[確認画面] ゲームが名前を出さなければ1行足す")
module, ctx, app, choice_cls, confirm_cls = setup()
press(app, "風鳴りの村（適正Lv 6）")
check("素の名前が渡る", app.confirmed == ["風鳴りの村"], app.confirmed)
check("1行足す", app.texts == ["（風鳴りの村の適正Lv: 6）"], app.texts)

print("[確認画面] 依頼の無い土地")
press_target = "雲上神殿（適正Lv 不明）"
app.process_choice(choice_cls(app), "他の土地へ行く")
CLOCK.settle()
app.texts = []
app.confirmed = []
press(app, press_target)
check("素の名前が渡る", app.confirmed == ["雲上神殿"], app.confirmed)
check("帯は「不明」", app.texts == ["（雲上神殿の適正Lv: 不明）"], app.texts)

# ================================================================ 設定
print("[SHOW=False] 一覧も確認画面も素のまま")
module, ctx, app, choice_cls, confirm_cls = setup(
    configure=lambda m: setattr(m, "SHOW", False), confirm_line="{name}へ向かう。")
check("名前のまま", texts_of(app) == ["陽光の砦", "風鳴りの村", "黄金の砂漠", "雲上神殿", "やめる"],
      texts_of(app))
press(app, "陽光の砦")
check("確認画面の文言もそのまま", app.texts == ["陽光の砦へ向かう。"], app.texts)
check("素の名前が渡る", app.confirmed == ["陽光の砦"], app.confirmed)

print("[BAND_UNKNOWN が空] 依頼の無い土地は名前のまま")
module, ctx, app, choice_cls, confirm_cls = setup(
    configure=lambda m: setattr(m, "BAND_UNKNOWN", ""))
check("名前のまま", texts_of(app)[3] == "雲上神殿", texts_of(app))
check("他は帯付き", texts_of(app)[0] == "陽光の砦（適正Lv 21〜30）", texts_of(app))
check("見積もりは残る", texts_of(app)[2] == "黄金の砂漠（適正Lv 推定 48〜57）", texts_of(app))
press(app, "雲上神殿")
check("確認画面に1行足さない", app.texts == [], app.texts)

print("[CONFIRM_TEXT が空] 1行足さない")
module, ctx, app, choice_cls, confirm_cls = setup(
    configure=lambda m: setattr(m, "CONFIRM_TEXT", ""))
press(app, "陽光の砦（適正Lv 21〜30）")
check("何も出ない", app.texts == [], app.texts)
check("素の名前が渡る", app.confirmed == ["陽光の砦"], app.confirmed)

print("[LEVEL_OFFSET=0] 難易度そのもの")
module, ctx, app, choice_cls, confirm_cls = setup(
    configure=lambda m: setattr(m, "LEVEL_OFFSET", 0))
check("難易度の値", texts_of(app)[0] == "陽光の砦（適正Lv 20〜29）", texts_of(app))
check("見積もりも難易度の値", texts_of(app)[2] == "黄金の砂漠（適正Lv 推定 47〜56）",
      texts_of(app))

print("[ESTIMATE=False] 未訪問の街は「不明」")
module, ctx, app, choice_cls, confirm_cls = setup(
    configure=lambda m: setattr(m, "ESTIMATE", False))
check("不明", texts_of(app)[2] == "黄金の砂漠（適正Lv 不明）", texts_of(app))
press(app, "黄金の砂漠（適正Lv 不明）")
check("確認画面も不明", app.texts == ["（黄金の砂漠の適正Lv: 不明）"], app.texts)

print("[見積もりの表記] BAND_ESTIMATE を変える")
module, ctx, app, choice_cls, confirm_cls = setup(
    configure=lambda m: setattr(m, "BAND_ESTIMATE", "{lv_min}〜{lv_max}?"))
check("表記が変わる", texts_of(app)[2] == "黄金の砂漠（適正Lv 48〜57?）", texts_of(app))
press(app, "黄金の砂漠（適正Lv 48〜57?）")
check("確認画面にも同じ帯", app.texts == ["（黄金の砂漠の適正Lv: 48〜57?）"], app.texts)
check("素の名前が渡る", app.confirmed == ["黄金の砂漠"], app.confirmed)
check("ログに estimated が残る", "estimated from slot" in read_log())

print("[訪問後] 依頼が生えれば見積もりから実値へ")
app.world.quests["20"] = quest("20", "3", 53)
app.world.quests["21"] = quest("21", "3", 55)
app.world.quests["22"] = quest("22", "3", 48)
app.process_choice(choice_cls(app), "他の土地へ行く")
CLOCK.settle()
check("実値になる", texts_of(app)[2] == "黄金の砂漠（適正Lv 49〜56）", texts_of(app))

print("[テンプレート] 変数を変える")


def configure_templates(m):
    m.LABEL = "{name} [{band}]"
    m.BAND = "難易度{min}-{max}/{count}件"


module, ctx, app, choice_cls, confirm_cls = setup(configure=configure_templates)
check("難易度と件数", texts_of(app)[0] == "陽光の砦 [難易度20-29/3件]", texts_of(app))

# ================================================================ 落とし所
print("[関数が無い] world.quests の走査で同じ帯")
module, ctx, app, choice_cls, confirm_cls = setup(functions=False)
check("同じ表示", texts_of(app)[:4] == ["陽光の砦（適正Lv 21〜30）", "風鳴りの村（適正Lv 6）",
                                     "黄金の砂漠（適正Lv 推定 48〜57）", "雲上神殿（適正Lv 不明）"],
      texts_of(app))
check("下がったことをログに残す", "scanning world.quests instead" in read_log())
check("例外は出ていない", not ctx.errors, ctx.errors)

print("[自己検証] apply() の時点で帯の組み立てを確かめている")
check("verified の行", any("verified:" in msg for _lv, msg in ctx.logs), ctx.logs)

# ================================================================
print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
