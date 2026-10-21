# -*- coding: utf-8 -*-
"""106_fix_battle_bgm_restore のロードの直後の掃除をゲーム抜きで通す。

    python tools/tests/test_battle_bgm_restore.py

  続く戦闘 … 戦闘の最中のセーブを読み、戦闘のボタンが戻ったときに鳴った戦闘曲は止めない
  残骸     … 戦闘の旗だけ残って場所のボタンが出たときの戦闘曲は止め、場所の曲を鳴らし直す
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

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_fix_battle_bgm_restore")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  -- {!r}".format(detail)))
    if not cond:
        failures.append(name)


class FakeClock:
    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, timeout=0):
        self.pending.append(callback)

    def run(self):
        while self.pending:
            self.pending.pop(0)(0)


CLOCK = FakeClock()
kivy = types.ModuleType("kivy")
kivy_clock = types.ModuleType("kivy.clock")
kivy_clock.Clock = CLOCK
sys.modules["kivy"] = kivy
sys.modules["kivy.clock"] = kivy_clock
sys.modules.pop("kivy.app", None)
# ミキサーはまだ無い（迷子のチャンネルは無い）。
pygame = types.ModuleType("pygame")
pygame.mixer = types.SimpleNamespace(get_init=lambda: None)
sys.modules["pygame"] = pygame


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = args

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args), "kwargs": {}}


class FakeSound:
    def __init__(self, src):
        self.src = src
        self.channels = 1

    def get_num_channels(self):
        return self.channels

    def stop(self):
        self.channels = 0


class SoundManager:
    is_fading = False

    def play_music_from_src(self, app, music_src):
        app.music = FakeSound(music_src)


class Area:
    bgm = "Assets/sounds/musics/dungeons/tense/quiet.mp3"


class Player:
    current_area = Area()


class InstantaleApp:
    def __init__(self, buttons):
        self.buttons = buttons
        self.in_battle = "normal"
        self.current_enemy_dict = {"1": object()}
        self.music = None
        self.player = Player()


main = types.ModuleType("__main__")
main.InstantaleApp = InstantaleApp
sys.modules["__main__"] = main


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.ready = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def on_ready(self, fn, key=None):
        self.ready.append(fn)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def setup(buttons):
    spec = importlib.util.spec_from_file_location("bgm_restore_under_test", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    work = tempfile.mkdtemp(prefix="bgm_restore_test_")
    ctx = FakeCtx(work)
    mod.apply(ctx)
    CLOCK.pending[:] = []
    app = InstantaleApp(buttons)
    main.the_app = app
    return mod, ctx, app, work


def log_of(ctx):
    path = os.path.join(ctx.out_dir, "battle_bgm.log")
    if not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def load_plays_battle_track(ctx, app):
    """ロードの処理が `in_battle` を見て戦闘曲を鳴らす（GAME.md §2.10）。"""
    manager = SoundManager()
    hook = ctx.hooks["scripts.sounds:SoundManager.play_music_from_src"]
    hook(SoundManager.play_music_from_src, manager, app, "Assets/sounds/musics/battle/1. Echoes.mp3")
    app.sound_manager = manager
    CLOCK.run()


BATTLE_BUTTONS = [{"text": "攻撃", "spec": PhaseSpec("BattlePhaseManager", ["攻撃"])},
                  {"text": "スキル・防御", "spec": PhaseSpec("SkillChoicePhaseManager", [])},
                  {"text": "逃げる", "spec": PhaseSpec("BattlePhaseManager", ["逃げる"])}]
PLACE_BUTTONS = [{"text": "他の土地へ行く", "spec": PhaseSpec("DisplayAreaMoveChoice", [])},
                 {"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])}]

print("続く戦闘")
mod, ctx, app, work = setup(BATTLE_BUTTONS)
load_plays_battle_track(ctx, app)
check("戦闘のボタンが戻っていれば戦闘曲を止めない", app.music.channels == 1 and "battle/" in app.music.src,
      app.music.src)
check("戦闘が続くと記録する", "the battle goes on" in log_of(ctx), log_of(ctx)[-300:])
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(work, ignore_errors=True)

print("残骸")
mod, ctx, app, work = setup(PLACE_BUTTONS)
load_plays_battle_track(ctx, app)
check("場所のボタンなら戦闘曲を止めて場所の曲を鳴らし直す",
      app.music is not None and app.music.src == Area.bgm and app.music.channels == 1,
      getattr(app.music, "src", None))
check("止めたと記録する", "stopped the battle track held by the app" in log_of(ctx), log_of(ctx)[-300:])
check("例外を残さない（残骸）", ctx.errors == [], ctx.errors)
shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
