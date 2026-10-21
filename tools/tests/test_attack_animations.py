# -*- coding: utf-8 -*-
"""920_attack_animations をゲーム抜きで通す。

    python tools/tests/test_wip_attack_animations.py

  型       … 審判の型の vfx の Literal に選択肢を足した子クラスを作る。欄の並びは親のまま、
             足した値で検証が通る。vfx の無い型・slash を含まない型には触らない
             （pydantic が無い環境ではここだけ飛ばす）
  送り口   … 3つ目の引数（位置でも名前でも）を差し替え、ほかの引数はそのまま渡す
  差し替え … 足した値は運び手の値に書き換わり、その手の間だけ運び手の絵と属性の音が差し替わる。
             ダメージの音・ほかの絵・時間切れの後・次の手では替えない
  絵       … 絵のフォルダが無い値は運び手の絵のまま。ゲームが持つ blow / water は書き換えない
  絵の在庫 … VFX が指す MOD の絵が画風（控えめ・はっきり）ごとに在り、64x64（全体は 128x128）の
             RGBA で frame_000.png から並ぶ。設定の画風が知らない値なら控えめに倒す
"""
import importlib.util
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import types
import typing
import weakref

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402


def find_mod(suffix):
    """mod は番号を除いた名前で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD_PATH = find_mod("_attack_animations")
spec = importlib.util.spec_from_file_location("attack_animations_under_test", MOD_PATH)
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir, mod_dir, game_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.mod_dir = mod_dir
        self.game_dir = game_dir
        self.hooks = {}
        self.errors = []
        self.notes = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path

    _mod = None

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

    def resolve(self, target):
        return (None, None, object())

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


class Clock(object):
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


CLOCK = Clock()
MOD.time = CLOCK

try:
    import pydantic
except ImportError:
    pydantic = None


# ---------------------------------------------------------------- 純関数
print("純関数")
check("Literal の選択肢を読む", MOD.literal_choices(typing.Literal["a", "b"]) == ("a", "b"))
check("Literal でなければ None", MOD.literal_choices(str) is None)
added = MOD.added_choices(("slash", "fire", "blow"))
check("持っている値は足さない", "blow" not in added and "slash" not in added, added)
check("ゲームの値を先、MOD の値を後に足す",
      added[0] == "water" and added[1:] == tuple(MOD.VFX), added)
check("区切りと末尾の / は見ない",
      MOD.same_folder("Assets\\effects\\battle_dark_\\slash1\\", "Assets/effects/battle_dark_/slash1"))
check("違うフォルダは違う", not MOD.same_folder("Assets/effects/blow", "Assets/effects/battle_dark_/slash1"))
check("足す値の数は説明の 30", len(MOD.GAME_HANDLED) + len(MOD.VFX) == 30,
      len(MOD.GAME_HANDLED) + len(MOD.VFX))
check("運び手はゲームの絵を知っている値だけ",
      all(spec[0] in MOD.CARRIER_SOURCES for spec in MOD.VFX.values()))
check("差し替える音はゲームの名前の表の中",
      all(spec[2] is None or spec[2] in MOD.ELEMENT_SOUNDS for spec in MOD.VFX.values()))
check("手引きが足す値を全部挙げる",
      all(name.replace("_aoe", "") in MOD.VFX_GUIDE for name in MOD.GAME_HANDLED + tuple(MOD.VFX)))
check("battle_action は3つ目の位置引数か名前",
      MOD.battle_action_of(("k", "player", {"vfx": "x"}), {}) == {"vfx": "x"}
      and MOD.battle_action_of(("k",), {"battle_action": 1}) == 1
      and MOD.battle_action_of(("k",), {}) is None)


# ---------------------------------------------------------------- 絵の在庫
print("絵の在庫")


def png_header(path):
    with open(path, "rb") as fh:
        head = fh.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height, head[24], head[25]


for style in MOD.ART_STYLES:
    for name, (carrier, art, _sound) in MOD.VFX.items():
        if not art.startswith("mod:"):
            continue
        folder = os.path.join(MOD_DIR, "effects", style, art[4:])
        frames = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        expected = ["frame_{:03d}.png".format(i) for i in range(len(frames))]
        headers = {png_header(os.path.join(folder, f)) for f in frames}
        check("{}/{}: {} コマが frame_000 から並ぶ".format(style, name, len(frames)),
              8 <= len(frames) <= 16 and frames == expected, frames)
        # 幅・高さ・ビット深度 8・色の型 6（RGBA）。全体の運び手に乗る絵は 128
        size = 128 if carrier == "fire_aoe" else 64
        check("{}/{}: {}x{} の RGBA".format(style, name, size, size),
              headers == {(size, size, 8, 6)}, headers)


# ---------------------------------------------------------------- 型
print("型")
if pydantic is None:
    print("  skip pydantic が無い")
else:
    from typing import List, Literal

    class Damage(pydantic.BaseModel):
        target: Literal["敵A", "敵B"]

    class Structure(pydantic.BaseModel):
        narration: str
        skill_effects: List[Damage]
        vfx: Literal["slash", "fire", "fire_aoe", "ice", "ice_aoe", "thunder", "thunder_aoe", "earth"]
        instant_damage: List[Damage]

    wide = MOD.extend_structure(Structure, pydantic)
    check("子クラスができる", wide is not None and issubclass(wide, Structure))
    check("欄の並びは親のまま", list(wide.model_fields) == list(Structure.model_fields),
          list(wide.model_fields))
    choices = MOD.literal_choices(wide.model_fields["vfx"].annotation)
    check("親の8つが先頭に残る", choices[:8] == MOD.literal_choices(Structure.model_fields["vfx"].annotation),
          choices)
    check("30 足して 38", len(choices) == 38, len(choices))
    schema = wide.model_json_schema()
    check("型の文の名前は親と同じ", schema.get("title") == "Structure", schema.get("title"))
    check("型の文に選択肢と手引きが載る",
          "gun_shot" in schema["properties"]["vfx"].get("enum", [])
          and "pistol" in schema["properties"]["vfx"].get("description", ""), schema["properties"]["vfx"])
    parsed = wide.model_validate({"narration": "撃った", "skill_effects": [], "vfx": "gun_shot",
                                  "instant_damage": [{"target": "敵A"}]})
    check("足した値で検証が通る", parsed.vfx == "gun_shot")

    class Described(pydantic.BaseModel):
        vfx: Literal["slash", "fire"] = pydantic.Field("slash", description="Select the VFX.")

    described = MOD.extend_structure(Described, pydantic)
    info = described.model_fields["vfx"]
    check("元の説明の後ろに手引きを足す",
          info.description.startswith("Select the VFX. ") and MOD.VFX_GUIDE in info.description,
          info.description)
    check("既定値を持つ欄は既定値を保つ", not info.is_required() and info.default == "slash")

    class NoVfx(pydantic.BaseModel):
        narration: str

    class OtherLiteral(pydantic.BaseModel):
        vfx: Literal["sparkle"]

    check("vfx の無い型には触らない", MOD.extend_structure(NoVfx, pydantic) is None)
    check("slash を含まない Literal には触らない", MOD.extend_structure(OtherLiteral, pydantic) is None)


# ---------------------------------------------------------------- フック
print("フック")
work = tempfile.mkdtemp(prefix="attack_animations_test_")
game_dir = os.path.join(work, "game")
# ゲームの絵は風と風の全体だけ在り、土の全体は無いことにする
os.makedirs(os.path.join(game_dir, "Assets", "effects", "battle", "wind2"))
os.makedirs(os.path.join(game_dir, "Assets", "effects", "battle", "wind_aoe2"))
ctx = Ctx(os.path.join(work, "out"), MOD_DIR, game_dir)
MOD.apply(ctx)

SEND = "scripts.llm.llm_manager_battle:send_request"
RESOLVE = MOD.RESOLVE_TARGET
APP_EFFECT = "__main__:InstantaleApp.effect"
WINDOW_EFFECT = "__main__:InstantaleApp.window_effect"
SOUND = MOD.SOUND_TARGET

check("送り口・演出の2つ・音・手に掛かる",
      all(t in ctx.hooks for t in (SEND, RESOLVE, SOUND) + MOD.EFFECT_TARGETS), sorted(ctx.hooks))
check("絵の無い値を知らせる", any("earth_aoe" in n and "art not found" in n for n in ctx.notes), ctx.notes)


def send(*args, **kwargs):
    seen = {}

    def original(*a, **k):
        seen["args"], seen["kwargs"] = a, k
        return "ok"
    result = ctx.hooks[SEND](original, *args, **kwargs)
    return result, seen


result, seen = send("referee_x", [{"role": "user"}], "not a class", model="m")
check("型でない3つ目はそのまま渡す", seen["args"][2] == "not a class" and seen["kwargs"] == {"model": "m"})
result, seen = send("referee_x", [{"role": "user"}])
check("型の無い呼び出しはそのまま渡す", seen["args"] == ("referee_x", [{"role": "user"}]))
if pydantic is not None:
    result, seen = send("referee_player_attack_new_new", ["msg"], Structure, None, 30000)
    passed = seen["args"][2]
    check("位置引数の型を差し替える", passed is not Structure and issubclass(passed, Structure))
    check("ほかの引数はそのまま", seen["args"][:2] == ("referee_player_attack_new_new", ["msg"])
          and seen["args"][3:] == (None, 30000), seen["args"])
    result, seen = send("referee_npc", ["msg"], structure=Structure, timeout=5)
    check("名前で渡した型も差し替える",
          seen["kwargs"]["structure"] is passed and seen["kwargs"]["timeout"] == 5, seen["kwargs"])
    result, seen = send("referee_player_attack_new_new", ["msg"], Structure)
    check("同じ型は作り直さない", seen["args"][2] is passed)
    check("型を足したログは審判ごとに1回",
          len([n for n in ctx.notes if "widened vfx for referee_player_attack_new_new" in n]) == 1)
    result, seen = send("referee_x", ["msg"], NoVfx)
    check("vfx の無い型はそのまま", seen["args"][2] is NoVfx)

    # 1手ごとに作られる型は、使い終えたら控えごと回収される
    import gc

    def one_turn():
        class Turn(pydantic.BaseModel):
            narration: str = ""
            vfx: Literal["slash", "fire"] = "slash"
        _, turn_seen = send("referee_player_attack_new_new", ["msg"], Turn)
        return weakref.ref(Turn), weakref.ref(turn_seen["args"][2])

    turn_ref, wide_ref = one_turn()
    gc.collect()
    check("使い終えた型は回収される", turn_ref() is None and wide_ref() is None,
          (turn_ref(), wide_ref()))



class Recorder(object):
    def __init__(self):
        self.calls = []

    def __call__(self, owner, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "drawn"


def resolve(vfx, side="味方側"):
    action = {"actor": "主人公", "vfx": vfx}
    seen = {}

    def original(self, key, side, battle_action, *rest):
        seen["vfx"] = battle_action["vfx"]
        return "resolved"
    ctx.hooks[RESOLVE](original, "manager", "player", side, action, [])
    return seen["vfx"], action


# 自由入力の審判は、送り口の答えを関数の中の元の型（8択）で作り直す（足した値が来ると落ちた。実機）。
# その審判の答えだけ、足した値を運び手に置き換えて返し、演出を決める所で元の値に戻す
if pydantic is not None:
    def ask(manager, answer):
        return ctx.hooks[SEND](lambda *a, **k: answer, manager, ["msg"], Structure)

    answer = ask("referee_player_any_input_new_new", {"actor": "主人公", "vfx": "arcane"})
    check("自由入力の審判の答えは、足した値を運び手に置き換えて返す（元の型で作り直せる）",
          answer["vfx"] == MOD.VFX["arcane"][0], answer)
    carried, action = resolve(answer["vfx"])
    check("演出を決める所で元の値に戻し、ほかの審判と同じく差し替える",
          carried == MOD.VFX["arcane"][0] and any("arcane" in n and "carries it" in n for n in ctx.notes),
          (carried, ctx.notes[-3:]))
    answer = ask("referee_player_any_input_new_new", {"battle_action": [{"vfx": "blow"}, {"vfx": "slash"}]})
    check("入れ子の答えも置き換える（ゲームが描ける値も元の型の値に）",
          [row["vfx"] for row in answer["battle_action"]] == ["slash", "slash"], answer)
    carried, action = resolve("slash")
    check("ゲームが描ける値は、演出を決める所で元の値に戻してゲームに描かせる", carried == "blow", carried)
    carried, action = resolve("slash")
    check("控えは1つずつ使い切る（元から slash の手はそのまま）", carried == "slash", carried)
    answer = ask("referee_player_attack_new_new", {"vfx": "arcane"})
    check("作り直さない審判の答えはそのまま", answer["vfx"] == "arcane", answer)
    ask("referee_player_any_input_new_new", {"vfx": "arcane"})
    CLOCK.now += MOD.PENDING_SECONDS + 1
    carried, action = resolve(MOD.VFX["arcane"][0])
    check("古い控えは使わない（時間切れ）", carried == MOD.VFX["arcane"][0]
          and not any("arcane" in n and "carries it" in n for n in ctx.notes[-1:]), ctx.notes[-2:])


def effect(src, target=APP_EFFECT, **kwargs):
    rec = Recorder()
    ctx.hooks[target](rec, "app", src, 192, 192, {"center_x": 0.5}, **kwargs)
    return rec.calls[0]


def sound(name, by_name=False):
    """`play_sound(self, app, sound_src)` の形で鳴らし、本体へ渡った名前を返す。"""
    rec = Recorder()
    if by_name:
        ctx.hooks[SOUND](rec, "sounds", app="app", sound_src=name)
        return rec.calls[0][1]["sound_src"]
    ctx.hooks[SOUND](rec, "sounds", "app", name)
    args = rec.calls[0][0]
    check("音の1つ目（app）はそのまま", args[0] == "app", args)
    return args[1]


SLASH = "Assets/effects/battle_dark_/slash1"
FIRE = "Assets/effects/battle_dark_/fire2"
GUN_ART = os.path.join(MOD_DIR, "effects", "soft", "gun_shot")
check("既定の画風は控えめ", MOD.ART_STYLE == "soft")
bright, _missing = MOD.resolve_sources(MOD_DIR, game_dir, "bright")
check("はっきりの画風は bright の絵を指す",
      bright["gun_shot"] == os.path.join(MOD_DIR, "effects", "bright", "gun_shot"), bright.get("gun_shot"))
MOD.ART_STYLE = "neon"
ctx_odd = Ctx(os.path.join(work, "out_odd"), MOD_DIR, game_dir)
MOD.apply(ctx_odd)
check("知らない画風は控えめに倒す", not any("art not found" in n and "gun_shot" in n for n in ctx_odd.notes),
      ctx_odd.notes)
MOD.ART_STYLE = "soft"

passed_vfx, action = resolve("gun_shot")
check("銃撃は斬撃に乗せてゲームへ渡す", passed_vfx == "slash" and action["vfx"] == "slash", passed_vfx)
args, kwargs = effect(SLASH)
check("斬撃の絵を銃撃の絵へ替える", args[0] == GUN_ART, args)
check("大きさと置き場所はゲームのまま", args[1:] == (192, 192, {"center_x": 0.5}), args)
check("ほかの絵は替えない", effect("Assets/effects/death")[0][0] == "Assets/effects/death")
check("属性の音は銃の音に替える", sound("斬撃") == "雷（小）")
check("ダメージの音は替えない", sound("ダメージ1") == "ダメージ1")
check("名前で渡した音も替える", sound("斬撃", by_name=True) == "雷（小）")
args, kwargs = effect(SLASH)
check("同じ手で何度出ても替える", args[0] == GUN_ART, args)
rec = Recorder()
ctx.hooks[APP_EFFECT](rec, "app", effect_src=SLASH, width=10)
check("名前で渡したフォルダも替える", rec.calls[0][1]["effect_src"] == GUN_ART, rec.calls)

CLOCK.now += MOD.PENDING_SECONDS + 1
check("時間切れの後は替えない", effect(SLASH)[0][0] == SLASH)
check("時間切れの後は音も替えない", sound("斬撃") == "斬撃")

passed_vfx, action = resolve("wind")
check("風は炎に乗せる", passed_vfx == "fire")
check("風の絵はゲームの相対パスで渡す", effect(FIRE)[0][0] == "Assets/effects/battle/wind2")
check("音の指定が無ければ運び手の音のまま", sound("炎（小）") == "炎（小）")

passed_vfx, action = resolve("wind_aoe")
check("風の全体は炎の全体に乗せる", passed_vfx == "fire_aoe")
check("全体の絵は window_effect の口で替える",
      effect("Assets/effects/battle_dark_/fire_aoe3", target=WINDOW_EFFECT)[0][0]
      == "Assets/effects/battle/wind_aoe2")

passed_vfx, action = resolve("slash")
check("ゲームの値はそのまま", passed_vfx == "slash")
check("次の手が来たら前の差し替えは捨てる", effect(FIRE)[0][0] == FIRE and effect(SLASH)[0][0] == SLASH)

passed_vfx, action = resolve("earth_aoe")
check("絵の無い値も運び手には乗せる", passed_vfx == "fire_aoe")
check("絵が無ければ運び手の絵のまま",
      effect("Assets/effects/battle_dark_/fire_aoe3")[0][0] == "Assets/effects/battle_dark_/fire_aoe3")
check("絵が無くても音は替える", sound("炎（大）") == "打撃")

for value in MOD.GAME_HANDLED:
    passed_vfx, action = resolve(value)
    check("{} はゲームが描く".format(value), passed_vfx == value)

resolve("thrust")
resolve("slash")
check("絵が出ないまま次の手が来たら NOTE を残す",
      any("NOTE thrust" in n for n in ctx.notes), ctx.notes[-3:])
passed_vfx, action = resolve("bite", side=MOD.ENEMY_SIDE)
check("敵の手も運び手に乗せる", passed_vfx == "slash")
check("敵の手でも音は替える", sound("斬撃") == "打撃")
resolve("slash")
check("敵の手は絵が出なくても NOTE を残さない",
      not any("NOTE bite" in n for n in ctx.notes), ctx.notes[-3:])
CLOCK.now += MOD.PENDING_SECONDS + 1
check("例外を残さない", ctx.errors == [], ctx.errors)

# 最後のコマに絵が残るゲームの絵は、空のコマを足した写しを渡す
print("空のコマを足した写し")
tail_game = os.path.join(work, "tail_game")
aoe = os.path.join(tail_game, "Assets", "effects", "battle", "neutral_energy_aoe1")
os.makedirs(aoe)
for row in (1, 2):
    for col in (1, 2):
        with open(os.path.join(aoe, "image_({}-{}).png".format(row, col)), "wb") as fh:
            fh.write(MOD.BLANK_PNG)
os.makedirs(os.path.join(tail_game, "Assets", "effects", "battle", "earth_aoe2"))
tail_root = os.path.join(work, "out_tail", "art")
found, _missing = MOD.resolve_sources(MOD_DIR, tail_game, "soft", tail_root=tail_root)
copy = found.get("arcane_aoe")
check("最後のコマに絵が残る絵は写しの絶対パスで渡す",
      copy == os.path.join(tail_root, "Assets", "effects", "battle", "neutral_energy_aoe1"), copy)
frames = MOD.frames_of(copy) if copy and os.path.isdir(copy) else []
check("写しは元のコマの後ろに空のコマが来る",
      frames == ["image_(1-1).png", "image_(1-2).png", "image_(2-1).png", "image_(2-2).png",
                 "image_end.png"], frames)
check("足したコマは透明", frames and open(os.path.join(copy, frames[-1]), "rb").read() == MOD.BLANK_PNG)
check("最後のコマが空の絵は写さない",
      found.get("earth_aoe") == "Assets/effects/battle/earth_aoe2", found.get("earth_aoe"))
stamp = os.path.getmtime(os.path.join(copy, "image_end.png"))
again, _missing = MOD.resolve_sources(MOD_DIR, tail_game, "soft", tail_root=tail_root)
check("写しが揃っていれば作り直さない",
      again.get("arcane_aoe") == copy and os.path.getmtime(os.path.join(copy, "image_end.png")) == stamp)
check("frame_ の並びには frame_end.png を足す",
      MOD.end_frame_name(["frame_000.png", "frame_019.png"]) == "frame_end.png")
with open(os.path.join(aoe, "image_(3-1).png"), "wb") as fh:
    fh.write(MOD.BLANK_PNG)
again, _missing = MOD.resolve_sources(MOD_DIR, tail_game, "soft", tail_root=tail_root)
check("元のコマが変われば写し直す", "image_(3-1).png" in MOD.frames_of(copy), MOD.frames_of(copy))
none, _missing = MOD.resolve_sources(MOD_DIR, tail_game, "soft")
check("写しの置き場が無ければ元の絵のまま",
      none.get("arcane_aoe") == "Assets/effects/battle/neutral_energy_aoe1", none.get("arcane_aoe"))
shutil.rmtree(work, ignore_errors=True)


print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
