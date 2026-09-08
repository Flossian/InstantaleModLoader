# -*- coding: utf-8 -*-
"""327_inn_quality をゲーム抜きで通す。

    python tools/tests/test_inn_quality.py

偽ゲームは `test_vacation_custom.py` と同じ実測に合わせてある（宿泊1回＝活動1回、
宿代と日数は `VacationStartManager.execute` の中で1回）。

確認するもの:

  活動の数 … 簡易寝台は素のまま（活動の後に活動が並ばない）。
             高級個室は活動の後に活動がもう一度並び、3回目の後には並ばない。
             並ぶのはゲームの活動の cls / args の写し。連泊で残りが積み直る
  常連     … 宿泊のたびに記録が増え、好感度が等級ぶん上がり、累計 20 で止まる。
             `"player"` の欄が無い主には足さず記録だけ。金が足りない宿泊は数えない
  1行      … 宿の主と話しているときだけ本文の先頭に入り、二度は入らない
  社交     … 高級個室は同行者、同行者が居なければ好感度の高い相手、居なければランダム。
             個室は好感度から、犬小屋はランダムだけ、簡易寝台は触らない。
             窓の間だけ宿の名簿と相手の location を差し替え、終わったら戻す
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "inn_quality"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    assert len(matches) == 1, matches
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
GAME_PRICES = {"kennel": 0, "bunk": 10, "private_room": 100, "luxury_suite": 1000}
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class Facility:
    def __init__(self, fid, owner, characters=()):
        self.id = fid
        self.owner = owner
        self.facility_type = "inn"
        self.characters = list(characters)


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, nodes):
        self.id = "1"
        self.nodes = {str(i): n for i, n in enumerate(nodes)}


class Character:
    def __init__(self, name, relationship, location=None):
        self.name = name
        self.relationship = relationship
        self.location = location


class Player:
    def __init__(self, location, area):
        self.gold = 100000
        self.location = location
        self.current_area = area


class World:
    def __init__(self):
        self.name = "テスト世界"
        self.days_elapsed = 100
        self.characters = {
            "7": Character("エリン", {"player": {"affinity": 0}}),
            "8": Character("ボブ", {}),
            "9": Character("仲間", {"player": {"affinity": 30}}),
            "10": Character("友人", {"player": {"affinity": 25}}),
            "11": Character("店主", {"player": {"affinity": 3}}),
        }


class VacationStartManager:
    def __init__(self, app, months, quality):
        self.app, self.months, self.quality = app, months, quality

    def execute(self, choice_text):
        price = GAME_PRICES[self.quality]
        if self.app.player.gold < price:
            self.app.add_text("金が足りない...")
            return None
        self.app.player.gold -= price
        self.app.elapse_days(int(self.months) * 30)
        self.app.buttons = [
            {"text": "訓練する", "spec": PhaseSpec("VacationTrainManager", [self.months, self.quality])},
            {"text": "休養をとる", "spec": PhaseSpec("VacationRestManager", [self.months, self.quality])},
            {"text": "宿泊を終える", "spec": PhaseSpec("VacationEndManager", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class Activity:
    def __init__(self, app, months, quality):
        self.app, self.months, self.quality = app, months, quality

    def execute(self, choice_text):
        self.app.done.append(type(self).__name__)
        self.app.buttons = [
            {"text": "まだ宿泊する", "spec": PhaseSpec("VacationStartManager", [self.months, self.quality])},
            {"text": "宿泊を終える", "spec": PhaseSpec("VacationEndManager", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class VacationTrainManager(Activity):
    pass


class VacationRestManager(Activity):
    pass


class VacationSocializeManager(Activity):
    """社交。ゲームは宿の名簿から相手を選ぶ（実測: 参加NPC が宿の主）。"""

    def execute(self, choice_text):
        inn = self.app.player.location
        self.app.social.append((list(inn.characters),
                                [self.app.world.characters[i].location is inn
                                 for i in inn.characters]))
        self.app.scene_hook(lambda *a, **k: None, None, None, None, None, None, None,
                            [self.app.world.characters[i] for i in inn.characters])
        return Activity.execute(self, choice_text)


class VacationEndManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.buttons = [{"text": "出る", "spec": PhaseSpec("MovePhaseManager", [])}]
        self.app.refresh_choice_buttons(reset_page=True)


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        pass


class InstantaleApp:
    def __init__(self):
        self.world = World()
        inn = Facility("50", "7", ["7"])
        shop = Facility("60", "11", ["11", "10"])
        self.world.characters["7"].location = inn
        self.world.characters["11"].location = shop
        self.world.characters["10"].location = shop
        self.player = Player(inn, Area([Node([inn, shop])]))
        self.party = ["player"]
        self.social = []
        self.scene_hook = None
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.done = []
        self.in_conversation = None
        self.chat_hook = None

    def add_text(self, context):
        self.texts.append(context)

    def elapse_days(self, days):
        self.world.days_elapsed += days

    def refresh_choice_buttons(self, reset_page=False):
        self.to_display_buttons = [e["text"] for e in self.buttons]

    def process_choice(self, function, choice_text=""):
        return function.execute(choice_text)

    def press(self, text):
        entry = next(e for e in self.buttons if e["text"] == text)
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"])
        return self.process_choice(cls(self, *data["args"]), text)

    def labels(self):
        return [e["text"] for e in self.buttons]

    def send_prompt(self, prompt):
        messages = [{"role": "system", "content": "GM"},
                    {"role": "user", "content": prompt}]
        sent = []

        def orig(_self, _model, msgs, _format=None, *a, **k):
            sent.append(msgs)

        self.chat_hook(orig, object(), "model", messages, None)
        return sent[-1]


class FakeCtx:
    _mod = None
    manager = types.SimpleNamespace(
        send_request=lambda *a, **k: None,
        send_request_with_no_structure=lambda *a, **k: None)

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator

    def resolve(self, target):
        name = target.rpartition(":")[2].rsplit(".", 1)[-1]
        owner = self.manager if "llm_manager" in target else None
        return owner, name, getattr(owner, name, None)

    def superseded(self):
        return False


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


def setup():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    for attr in list(vars(sys)):
        if attr.startswith("__instantale_inn_quality"):
            delattr(sys, attr)
    classes = {}
    for base in (InstantaleApp, VacationStartManager, VacationTrainManager,
                 VacationRestManager, VacationSocializeManager, VacationEndManager):
        classes[base.__name__] = type(base.__name__, (base,), {})
        setattr(sys.modules["__main__"], base.__name__, classes[base.__name__])
    sys.modules["__main__"].JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    sys.modules["__main__"].PhaseSpec = PhaseSpec

    path = find_mod("_inn_quality")
    spec = importlib.util.spec_from_file_location("inn_quality_mod", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["inn_quality_mod"] = module
    spec.loader.exec_module(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    app_cls = classes["InstantaleApp"]
    install(ctx.hooks, [
        ("__main__:InstantaleApp.refresh_choice_buttons", app_cls, "refresh_choice_buttons"),
        ("__main__:InstantaleApp.elapse_days", app_cls, "elapse_days"),
        ("__main__:VacationStartManager.__init__", classes["VacationStartManager"], "__init__"),
        ("__main__:VacationStartManager.execute", classes["VacationStartManager"], "execute"),
        ("__main__:VacationTrainManager.execute", classes["VacationTrainManager"], "execute"),
        ("__main__:VacationRestManager.execute", classes["VacationRestManager"], "execute"),
        ("__main__:VacationSocializeManager.execute", classes["VacationSocializeManager"], "execute"),
        ("__main__:VacationEndManager.execute", classes["VacationEndManager"], "execute"),
    ])
    app = app_cls()
    app.chat_hook = ctx.hooks.get(CHAT_TARGET)
    app.scene_hook = ctx.hooks["scripts.llm.llm_manager:vacation_scene_generator"]
    ml.ui.find_app = lambda: app
    return module, ctx, app


def stay(app, quality, months=1):
    main = sys.modules["__main__"]
    app.process_choice(main.VacationStartManager(app, months, quality), quality)


def record_of(ctx):
    path = os.path.join(ctx.state_dir, "inn_regular")
    files = os.listdir(path) if os.path.isdir(path) else []
    if not files:
        return {}
    with io.open(os.path.join(path, files[0]), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- 活動の数
module, ctx, app = setup()

stay(app, "bunk")
check("bunk: game menu shown", app.labels() == ["訓練する", "休養をとる", "宿泊を終える"], app.labels())
app.press("訓練する")
check("bunk: no second activity", app.labels() == ["まだ宿泊する", "宿泊を終える"], app.labels())
app.press("宿泊を終える")

stay(app, "luxury_suite")
app.press("訓練する")
check("luxury: activities again", app.labels() == ["訓練する", "休養をとる", "まだ宿泊する", "宿泊を終える"], app.labels())
entry = app.buttons[0]
check("luxury: copied game spec", entry["spec"].to_dict() == {"cls_name": "VacationTrainManager", "args": [1, "luxury_suite"]}, entry["spec"].to_dict())
check("luxury: marked as ours", entry.get(module.MARK) == "again")
app.press("休養をとる")
check("luxury: third activity offered", app.labels()[:2] == ["訓練する", "休養をとる"], app.labels())
app.press("訓練する")
check("luxury: no fourth activity", app.labels() == ["まだ宿泊する", "宿泊を終える"], app.labels())
check("luxury: three activities ran", app.done[-3:] == ["VacationTrainManager", "VacationRestManager", "VacationTrainManager"], app.done)
app.press("まだ宿泊する")
app.press("訓練する")
check("luxury: refilled on a second night", app.labels()[:2] == ["訓練する", "休養をとる"], app.labels())
app.press("宿泊を終える")
check("after end: no menu", app.labels() == ["出る"], app.labels())

# ---------------------------------------------------------------- 常連
rec = record_of(ctx)["7"]
check("regular: stays counted", rec["stays"] == 3 and rec["by_quality"] == {"bunk": 1, "luxury_suite": 2}, rec)
erin = app.world.characters["7"].relationship["player"]
check("regular: affinity +1 +4 +4", erin["affinity"] == 9 and rec["granted"] == 9, (erin, rec))
check("regular: last_day recorded", rec["last_day"] == app.world.days_elapsed, rec)

for _ in range(4):
    stay(app, "luxury_suite")
    app.press("宿泊を終える")
rec = record_of(ctx)["7"]
check("regular: capped at 20", erin["affinity"] == 20 and rec["granted"] == 20 and rec["stays"] == 7, (erin, rec))

app.player.gold = 5
stay(app, "luxury_suite")
check("regular: unpaid stay not counted", record_of(ctx)["7"]["stays"] == 7, record_of(ctx)["7"])
check("unpaid stay: no menu", app.labels() == ["出る"], app.labels())
app.player.gold = 100000

app.player.location = Facility("51", "8")
stay(app, "bunk")
app.press("宿泊を終える")
rec = record_of(ctx)["8"]
check("regular: owner without 'player' recorded only", rec["stays"] == 1 and rec["granted"] == 0
      and app.world.characters["8"].relationship == {}, rec)

# ---------------------------------------------------------------- 会話の1行
app.in_conversation = None
sent = app.send_prompt("こんにちは")
check("line: not in conversation", "泊まっている" not in sent[0]["content"], sent)
app.in_conversation = "7"
app.world.days_elapsed += 12
sent = app.send_prompt("こんにちは")
line = "【宿の客として】プレイヤーはエリンの宿にこれまで7回泊まっている（簡易寝台1回・高級個室6回）。最後の宿泊は42日前。"
check("line: innkeeper conversation", sent[0]["content"].startswith(line), sent[0]["content"])
check("line: user text untouched", sent[1]["content"] == "こんにちは")
sent = app.send_prompt(line + "\nGM")
check("line: not duplicated", sum(m["content"].count("【宿の客として】") for m in sent) == 1, sent)
app.in_conversation = "9"
sent = app.send_prompt("こんにちは")
check("line: other npc", "泊まっている" not in sent[0]["content"])

# ---------------------------------------------------------------- 社交の相手
app.player.location = app.world.characters["7"].location
inn = app.player.location


def socialize(quality, party=("player",)):
    app.party = list(party)
    app.social = []
    stay(app, quality)
    app.press("社交する") if "社交する" in app.labels() else None
    return app.social[-1] if app.social else None


for m in (VacationStartManager,):
    pass
# 偽の VacationStartManager は社交のボタンを持たないので足す
main = sys.modules["__main__"]
orig_execute = main.VacationStartManager.execute


def with_social(self, choice_text):
    result = orig_execute(self, choice_text)
    self.app.buttons.insert(0, {"text": "社交する", "spec": PhaseSpec(
        "VacationSocializeManager", [self.months, self.quality])})
    self.app.refresh_choice_buttons(reset_page=True)
    return result


main.VacationStartManager.execute = with_social

seen = socialize("luxury_suite", ("player", "9"))
check("social: luxury picks the companion", seen == (["9"], [True]), seen)
check("social: roster restored", inn.characters == ["7"], inn.characters)
check("social: companion location restored", app.world.characters["9"].location is None)
app.press("宿泊を終える")

seen = socialize("luxury_suite")
check("social: luxury without party picks the best-liked local", seen == (["10"], [True]), seen)
app.press("宿泊を終える")

seen = socialize("private_room", ("player", "9"))
check("social: private ignores the companion", seen == (["10"], [True]), seen)
app.press("宿泊を終える")

app.world.characters["10"].relationship["player"]["affinity"] = 5
seen = socialize("private_room")
check("social: private falls back to random", seen[0][0] in ("10", "11", "7") and seen[1] == [True], seen)
app.press("宿泊を終える")

seen = socialize("bunk", ("player", "9"))
check("social: bunk untouched", seen == (["7"], [True]), seen)
app.press("宿泊を終える")

seen = socialize("kennel", ("player", "9"))
check("social: kennel never picks the companion", seen[0][0] != "9", seen)
app.press("宿泊を終える")
check("social: log says effective", "swap effective" in io.open(
    os.path.join(OUT_DIR, "inn_quality.log"), encoding="utf-8").read())

check("no hook errors", not ctx.errors, ctx.errors)

print("\n{} failure(s)".format(len(failures)))
sys.exit(1 if failures else 0)
