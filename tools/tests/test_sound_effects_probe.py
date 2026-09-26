# -*- coding: utf-8 -*-
"""235_probe_sound_effects.py をゲーム抜きで通す。

    python tools/tests/test_sound_effects_probe.py

偽の app / SoundManager / ItemEquipManager を差し込み、次を確認する。

  装備   … `equip_item` の呼び出しを1行録り、呼んだ MOD（スタックのファイル名）が写る
  中     … `equip_item` の中で鳴った効果音には within=['equip_item'] が付く
  手     … 戦闘の1手の中で鳴った効果音には within=['battle_turn'] と戦闘の旗が付く
  素通し … どの包みも本体の戻り値を変えない

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
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

import instantale_modloader as ml            # noqa: E402


def find_mod(suffix):
    """mod は **番号を除いた名前** で探す（番号は振り直されることがある）。"""
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


MOD_PATH = find_mod("_probe_sound_effects")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.mod_dir = os.path.dirname(MOD_PATH)
        self.hooks = {}
        self.errors = []
        self.rows = []

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def jsonl(self, name):
        return self.rows.append

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


spec = importlib.util.spec_from_file_location("probe_sound_effects_under_test", MOD_PATH)
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)

out_dir = tempfile.mkdtemp(prefix="sound_probe_test_")
ctx = Ctx(out_dir)
MOD.apply(ctx)
PLAY = ctx.hooks["scripts.sounds:SoundManager.play_sound"]
EQUIP = ctx.hooks["__main__:ItemEquipManager.equip_item"]
TURN = ctx.hooks["__main__:BattlePhaseManager.resolve_battle_effect"]

app = types.SimpleNamespace(in_battle=False, in_boss_battle=False, in_colosseum_battle=False)
manager = object()
played = []


def real_play(self, app_, name):
    played.append(name)
    return "played"


def real_equip(self, item):
    # 本体の equip_item は中で装備の音を鳴らす（ことにする）
    return PLAY(real_play, manager, app, "equip")


# 333 のファイルから呼んだことにする（スタックのファイル名で MOD を拾うため）
fake_333 = os.path.join(MODS_DIR, "333_equipment_slots", "equipment_slots.py")
namespace = {}
exec(compile("def call_from_333(hook, orig, item):\n    return hook(orig, object(), item)\n",
             fake_333, "exec"), namespace)

print("装備の入口と、その中の効果音")
item = types.SimpleNamespace(name="テストの剣")
result = namespace["call_from_333"](EQUIP, real_equip, item)
check("本体の戻り値を変えない", result == "played", result)
equip_rows = [r for r in ctx.rows if r["kind"] == "equip"]
check("equip_item を1行録る", len(equip_rows) == 1, ctx.rows)
check("呼んだ MOD が写る", equip_rows and equip_rows[0]["mods"] == ["333_equipment_slots"],
      equip_rows)
check("品の名前が写る", equip_rows and equip_rows[0]["item"] == "テストの剣", equip_rows)
sound_rows = [r for r in ctx.rows if r["kind"] == "sound"]
check("効果音を1行録る", len(sound_rows) == 1 and played == ["equip"], (ctx.rows, played))
check("equip_item の中で鳴ったと分かる", sound_rows and sound_rows[0]["within"] == ["equip_item"],
      sound_rows)
check("音の名前が写る", sound_rows and sound_rows[0]["args"] == ["'equip'"], sound_rows)

print("戦闘の1手の中の効果音")
ctx.rows[:] = []
app.in_battle = True


def real_turn(self):
    return PLAY(real_play, manager, app, "attack")


check("本体の戻り値を変えない", TURN(real_turn, object()) == "played")
sound_rows = [r for r in ctx.rows if r["kind"] == "sound"]
check("手の始まりを録る", any(r["kind"] == "turn" for r in ctx.rows), ctx.rows)
check("戦闘の1手の中と分かる", sound_rows and sound_rows[0]["within"] == ["battle_turn"], sound_rows)
check("戦闘の旗が写る", sound_rows and sound_rows[0]["flags"] == ["in_battle"], sound_rows)
check("装備の中ではない", sound_rows and "equip_item" not in sound_rows[0]["within"], sound_rows)

print("出た後は中ではない")
ctx.rows[:] = []
app.in_battle = False
PLAY(real_play, manager, app, "click")
check("within が空", ctx.rows and ctx.rows[0]["within"] == [], ctx.rows)
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
