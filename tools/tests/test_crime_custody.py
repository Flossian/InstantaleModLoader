# -*- coding: utf-8 -*-
"""336_crime_overhaul の身柄の拘束（custody.py）を、ゲーム抜きで通す。

    python tools/tests/test_crime_custody.py

  解散 … 裁判の始まりの前に、仲間をゲーム自身の手順で外し（`remove_party_member`）、雇われた場所へ置き直す
         （`get_party_leave_facility` のタプルをほどいて `move_npc_to_facility`）。1行出し、仲間欄を塗り直す
  外さない … MOD の人物・置き先の無い仲間は外さない。設定を切れば何もしない。仲間が居なければ何も出さない
  牢の印 … 立てる・下ろす。控えに持つ（ロードをまたぐ）。牢を出た知らせ（`prison_event("exit")`）で下ろす
  ゲームオーバー … 牢の中で倒れた・処刑された・寿命を迎えたときも、ゲームオーバーの包み（`prison`）が下ろす
  手引き … 処刑の場面で手引き役を入れた後に依頼が作られなければ、手引き役を雇われた場所へ戻し、牢の外から始めたなら印も戻す
"""
import importlib
import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import ui  # noqa: E402


def load_custody():
    matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_overhaul"))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *_crime_overhaul: {}".format(matches))
    package = types.ModuleType("crime_overhaul_custody_parts")
    package.__path__ = [os.path.join(MODS_DIR, matches[0])]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + ".custody")


custody = load_custody()
failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({!r})".format(detail) if not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx(object):
    def __init__(self):
        self.hooks = {}

    def wrap(self, target, **options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate

    def log_exc(self, msg):
        raise AssertionError(msg)


class Cfg(object):
    CUSTODY_DISBAND = True


class Screen(object):
    def __init__(self):
        self.said = []
        self.painted = 0

    def say(self, app, text):
        self.said.append(text)

    def paint_party(self, app):
        self.painted += 1

    def schedule(self, fn):
        fn()


class Worlds(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.data = {}
        self.saves = 0

    def playthrough(self, app):
        return "W/P"

    def load(self, playthrough):
        return self.data.setdefault(playthrough, {})

    def save(self, playthrough):
        self.saves += 1


class Person(object):
    def __init__(self, name):
        self.name = name


class Place(object):
    def __init__(self, name):
        self.name = name


class App(object):
    def __init__(self):
        self.party = ["player", "11", "12", "mod:9", "13"]
        self.world = types.SimpleNamespace(characters={
            "11": Person("リュウ"), "12": Person("ジーン"), "mod:9": Person("影"), "13": Person("迷子")})
        self.removed = []
        self.moved = []

    def remove_party_member(self, member_id):
        self.removed.append(member_id)
        self.party.remove(member_id)

    def get_party_leave_facility(self, character):
        if character.name == "迷子":
            return (None, None)
        return (Place(character.name + "の宿"), "node")

    def move_npc_to_facility(self, npc_id, character, facility, node=None):
        self.moved.append((npc_id, facility.name, node))


handlers = {"exit": [], "break": []}
screen = Screen()
worlds = Worlds()
lines = []
ctx = FakeCtx()
env = types.SimpleNamespace(ctx=ctx, write=lines.append, screen=screen, worlds=worlds, cfg=Cfg(),
                            on_prison=lambda kind, fn: handlers[kind].append(fn))
saved = (ui.party_member_ids, ui.find_app)
app = App()
ui.party_member_ids = lambda a: [m for m in a.party if m != "player"]
ui.find_app = lambda: app
try:
    custody.install(env)
    trial = ctx.hooks[custody.TRIAL_TARGET]
    started = types.SimpleNamespace(app=app)

    print("解散")
    check("裁判は素通し", trial(lambda self: "trial", started) == "trial")
    check("仲間を外す（MOD の人物・置き先の無い仲間は残す）", app.removed == ["11", "12"]
          and app.party == ["player", "mod:9", "13"], (app.removed, app.party))
    check("雇われた場所へ置き直す（タプルをほどく）",
          app.moved == [("11", "リュウの宿", "node"), ("12", "ジーンの宿", "node")], app.moved)
    check("1行出す", screen.said == [custody.PARTED_TEXT.format(names="リュウ・ジーン")], screen.said)
    check("仲間欄を塗り直す", screen.painted == 1)
    check("残した理由をログに残す", any("kept mod:9" in l for l in lines)
          and any("kept 13" in l and "no facility" in l for l in lines), lines)

    print("外さない")
    app.party = ["player", "11"]
    del screen.said[:]
    Cfg.CUSTODY_DISBAND = False
    trial(lambda self: "trial", started)
    check("設定を切れば外さない", app.party == ["player", "11"] and screen.said == [])
    Cfg.CUSTODY_DISBAND = True
    app.party = ["player"]
    trial(lambda self: "trial", started)
    check("仲間が居なければ何も出さない", screen.said == [] and screen.painted == 1, screen.said)

    print("牢の印")
    check("はじめは牢の外", env.in_custody(app) is False)
    env.set_custody(app, True, "imprisonment")
    check("立てると牢の中", env.in_custody(app) is True and worlds.data["W/P"].get("custody") is True)
    saves = worlds.saves
    env.set_custody(app, True, "rescue")
    check("同じ値なら書かない", worlds.saves == saves)
    for handler in handlers["exit"]:
        handler(app, how="release")
    check("牢を出た知らせで下ろし、控えに何も残さない",
          env.in_custody(app) is False and "custody" not in worlds.data["W/P"], worlds.data)
    check("ログに出入り", any("custody: in (imprisonment)" in l for l in lines)
          and any("custody: out (release)" in l for l in lines), lines)

    print("ゲームオーバー")
    # 牢の中で倒れた・処刑された・寿命を迎えた。牢を出る知らせは来ないので、ゲームオーバーの包み（`prison`）が下ろす
    prison = importlib.import_module("crime_overhaul_custody_parts.prison")
    noop = lambda *a, **k: None     # noqa: E731
    env.area_difficulty = env.quest_reward = env.refresh_gold = noop
    for name in ("close_screen", "jail_screen_open", "on_labels", "on_offer", "on_screen", "open_screen",
                 "prison_event", "screen_open", "start_ban"):
        setattr(env, name, noop)
    env.owner = "336_crime_overhaul"
    screen.when_idle = screen.start_phase = noop
    prison.install(env)
    game_over = ctx.hooks["__main__:GameOverManager.__init__"]
    env.set_custody(app, True, "imprisonment")
    game_over(lambda self, *a, **k: None, types.SimpleNamespace(app=app))
    check("ゲームオーバーで牢の印を下ろす",
          env.in_custody(app) is False and "custody" not in worlds.data["W/P"], worlds.data)
    check("その記録", any("custody: out (game over)" in l for l in lines), lines)

    print("手引きに賭けて依頼が作られない")
    # 処刑の判決の画面で手引き役をパーティに入れてから依頼を作る。作れなければ手引き役と牢の印を戻す
    rescue = importlib.import_module("crime_overhaul_custody_parts.rescue")
    offers, phases = [], []
    env.on_offer = lambda fn: offers.append(fn)
    env.on_text = env.on_refresh = noop
    env.prison_event = noop
    env.area_difficulty = lambda a: 20
    env.quest_inject = types.SimpleNamespace(update=lambda **k: None)
    Cfg.RESCUE_ENABLED, Cfg.RESCUE_AFFINITY, Cfg.RESCUE_DIFFICULTY_PLUS, Cfg.RESCUE_LOSS = True, 60, 10, 10
    screen.busy_on = screen.busy_off = noop
    screen.start_phase = lambda a, phase, *args, **kw: phases.append(phase)
    helper = Person("ミナ")
    helper.relationship = {"player": {"affinity": 90}}
    helper.config = {"is_dead": False}
    app.world.characters["21"] = helper
    app.party = ["player"]
    app.add_party_member = lambda member_id: app.party.append(member_id)
    del app.removed[:], app.moved[:]

    class Board(object):
        def __init__(self, app_):
            pass

        def generate_random_quest(self):
            pass                                   # 生成が1件も作らなかった

    patched = (ui.cls_of, ui.current_area, ui.world_areas, ui.quest_ids, rescue.choices.repaint)
    ui.cls_of = lambda name: {"DisplayQuestChoice": Board, rescue.QUEST_START_SPEC: object}.get(name)
    ui.current_area = lambda a: types.SimpleNamespace(id="7", name="試しの街")
    ui.world_areas = lambda a: {}
    ui.quest_ids = lambda a: []
    rescue.choices.repaint = noop
    try:
        rescue.install(env)
        view = types.SimpleNamespace(buttons=[{"text": "嫌だ！", "spec": types.SimpleNamespace(
            cls_name=rescue.DEATH_SPEC, args=[])}])
        made = [choice for fn in offers for choice in (fn(app, view) or [])]
        check("処刑の判決の画面に手引きの選択肢が出る", len(made) == 1 and "ミナ" in made[0].text,
              [getattr(c, "text", c) for c in made])
        env.set_custody(app, False, "before")
        made[0].on_press(app)
        phases[-1].execute("x")
        check("依頼が作られなければ手引き役を外して雇われた場所へ戻す",
              app.party == ["player"] and app.removed == ["21"] and app.moved == [("21", "ミナの宿", "node")],
              (app.party, app.removed, app.moved))
        check("牢の外から始めたなら牢の印も戻す", env.in_custody(app) is False, worlds.data.get("W/P"))
        env.set_custody(app, True, "imprisonment")
        del app.removed[:], app.moved[:]
        phases[-1].execute("x")
        check("牢の中から始めたなら牢の印は残す（服役中のまま）",
              env.in_custody(app) is True and app.party == ["player"], (worlds.data.get("W/P"), app.party))
    finally:
        ui.cls_of, ui.current_area, ui.world_areas, ui.quest_ids, rescue.choices.repaint = patched
finally:
    ui.party_member_ids, ui.find_app = saved

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
