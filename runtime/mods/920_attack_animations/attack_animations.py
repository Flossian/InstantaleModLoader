# -*- coding: utf-8 -*-
"""戦闘の攻撃の演出を、武器と攻撃の種類に合わせて出し分ける。

素のゲームでは、審判 LLM が返す `vfx`（8択の Literal）を `resolve_battle_effect` が読み、
演出の絵のフォルダと効果音を選ぶ。選択肢は slash / fire / fire_aoe / ice / ice_aoe /
thunder / thunder_aoe / earth だけで、記録では8割強が slash だった。
銃でも弓でも拳でも同じ斬撃の絵が出る。

この MOD は2か所に手を入れる。

## 1. 審判の選択肢を足す

`scripts.llm.llm_manager_battle:send_request` の3つ目の引数（返却型の pydantic クラス）に
`vfx` の Literal があれば、選択肢を足した子クラスに差し替えて渡す。
ローカルの grammar（`json_schema`）もプロンプトに埋め込まれる型の文も、
クラウドの構造化出力も、ゲームがこのクラスから作るので、どの接続先にも同じ選択肢が届く。
子クラスは `vfx` だけを上書きするので、欄の並び（LLM が書く順）は親のまま
（pydantic v2 は上書きした欄を親の位置に残す）。返ってきた値の検証も子クラスが通す。
送り口は「開始する」のたびに置き直されるので、`llm.watch_aliases` で包む（TECH.md §3.4）。

足すのは、ゲームが処理を持っている `blow` / `water` と、この MOD が絵を差し替える値（`VFX`）。

## 2. 演出の絵と音を差し替える

足した値はゲームの分岐に無いので、そのままでは何も出ない。
`resolve_battle_effect` の入口で `vfx` を、絵の大きさと置き方が近いゲームの値（運び手。
単体の物理は slash、単体の魔法は fire、全体は fire_aoe）に置き換え、
どの絵に差し替えるかを控える。
絵と音はゲームが Clock で少し後に出すので、控えはその手の間だけ持つ
（次の `resolve_battle_effect` で捨てる。手が来なくても `PENDING_SECONDS` で捨てる）。
演出を出す関数（`InstantaleApp.effect` / `window_effect`）に運び手の絵のフォルダが渡ったら、
控えた絵のフォルダに替える。置き方と大きさはゲームが決めたまま。
効果音も同じ間だけ、属性の音（`ELEMENT_SOUNDS`）を控えた音に替える。ダメージの音は替えない。

絵は2通り。
武器と体の攻撃・衝撃波・毒（`effects/<画風>/` の17種）はこの MOD が描いたコマ送りで、
ゲームの slash1 / blow に揃えてある（1体は 64x64、毒の全体は 128x128。描き方は `build/make_frames.py`）。
画風は控えめ（soft）とはっきり（bright）の2組で、設定 `ART_STYLE` で選ぶ。
属性（風・光・闇・魔力・水の全体・土の全体・爆発）はゲームの `Assets/effects` に在るのに
使われていない絵を指す。ゲームの更新でフォルダが消えていれば、その値は運び手の絵のまま出す。
"""

import os
import sys
import time
import typing
import weakref

from instantale_modloader import llm

LOG_BASENAME = "attack_animations.log"

#: 返却型を差し替える送り口。返却型を取らない `send_request_with_no_structure` は見ない。
BATTLE_SEND_TARGETS = ("scripts.llm.llm_manager_battle:send_request",)
RESOLVE_TARGET = "__main__:BattlePhaseManager.resolve_battle_effect"
#: 演出を出す関数。単体の絵は effect（→ HUD の start_effect）、全体の絵は window_effect
#: （→ start_window_effect）を通る（実機の記録。DOC.md §2）。HUD の側は app の側から呼ばれるので包まない。
EFFECT_TARGETS = (
    "__main__:InstantaleApp.effect",
    "__main__:InstantaleApp.window_effect",
)
SOUND_TARGET = "scripts.sounds:SoundManager.play_sound"

#: ゲームの値 → ゲームが出す絵のフォルダ（exe の定数で確かめた。運び手に使う分だけ）。
CARRIER_SOURCES = {
    "slash": "Assets/effects/battle_dark_/slash1",
    "fire": "Assets/effects/battle_dark_/fire2",
    "fire_aoe": "Assets/effects/battle_dark_/fire_aoe3",
}

#: 差し替えてよい効果音（`scripts.sounds` の名前の表のうち、攻撃の属性の音）。
#: ダメージ1〜3 は当たった音なので、どの演出でもそのまま鳴らす。
ELEMENT_SOUNDS = frozenset((
    "斬撃", "打撃", "炎（小）", "炎（大）", "雷（小）", "雷（大）", "氷（小）", "氷（大）",
))

#: ゲームが自分で処理を持っているので、選択肢に足すだけで差し替えない値。
GAME_HANDLED = ("blow", "water")

#: 足す値 → (運び手, 絵, 効果音)。
#: 絵は "mod:<effects/<画風> の下のフォルダ>" か、ゲームの Assets からの相対パス。
#: 効果音は `scripts.sounds` の名前。None なら運び手の音のまま。
VFX = {
    # 武器と体の攻撃（この MOD の絵）
    "heavy_slash": ("slash", "mod:heavy_slash", "打撃"),
    "cross_slash": ("slash", "mod:cross_slash", None),
    "energy_slash": ("slash", "mod:energy_slash", None),
    "thrust": ("slash", "mod:thrust", None),
    "whip": ("slash", "mod:whip", None),
    "claw": ("slash", "mod:claw", None),
    "bite": ("slash", "mod:bite", "打撃"),
    "arrow_shot": ("slash", "mod:arrow_shot", None),
    "thrown_weapon": ("slash", "mod:thrown_weapon", None),
    "gun_shot": ("slash", "mod:gun_shot", "雷（小）"),
    "rapid_fire": ("slash", "mod:rapid_fire", "雷（小）"),
    "shotgun_blast": ("slash", "mod:shotgun_blast", "雷（小）"),
    "sniper_shot": ("slash", "mod:sniper_shot", "雷（小）"),
    "energy_beam": ("slash", "mod:energy_beam", "雷（小）"),
    "shockwave": ("slash", "mod:shockwave", "打撃"),
    # 毒（この MOD の絵。ゲームに毒の絵は無い）
    "poison": ("slash", "mod:poison", "炎（小）"),
    "poison_aoe": ("fire_aoe", "mod:poison_aoe", "炎（小）"),
    # 属性（ゲームに在って使われていない絵）
    "explosion": ("fire", "Assets/effects/fire_aoe4", "炎（大）"),
    "wind": ("fire", "Assets/effects/battle/wind2", None),
    "wind_aoe": ("fire_aoe", "Assets/effects/battle/wind_aoe2", "炎（小）"),
    "light": ("fire", "Assets/effects/battle/thunder_aoe2", "氷（小）"),
    "light_aoe": ("fire_aoe", "Assets/effects/battle/thunder_aoe2", "氷（大）"),
    "dark": ("fire", "Assets/effects/battle/wisp1", None),
    "dark_aoe": ("fire_aoe", "Assets/effects/battle/wisp_aoe1", "炎（小）"),
    "arcane": ("fire", "Assets/effects/battle/neutral_energy1", "雷（小）"),
    "arcane_aoe": ("fire_aoe", "Assets/effects/battle/neutral_energy_aoe1", "雷（大）"),
    "water_aoe": ("fire_aoe", "Assets/effects/battle/water_aoe1", "氷（小）"),
    "earth_aoe": ("fire_aoe", "Assets/effects/battle/earth_aoe2", "打撃"),
}

#: 足した選択肢の手引き。型の `vfx` の説明に足す（クラウドの構造化出力と、ローカルの埋め込みの型の文に載る）。
#: 世界観によらないよう、武器の仕組みと当たり方だけを書く。
VFX_GUIDE = (
    "Pick the visual that matches how this action actually lands. "
    "Weapons and bodies: slash (blade cut), heavy_slash (greatsword, axe, any heavy swing), "
    "cross_slash (two quick cuts, dual wielding), energy_slash (glowing blade, beam saber, magic sword), "
    "thrust (spear, rapier, any stab), whip (whip, chain, tentacle lash), "
    "blow (fist, kick, club, hammer, staff strike), claw, bite, arrow_shot (bow, crossbow), "
    "thrown_weapon (knife, shuriken, rock), gun_shot (pistol, rifle, single shot), "
    "rapid_fire (machine gun, automatic burst), shotgun_blast (spread shot), "
    "sniper_shot (long-range precise shot), energy_beam (laser, ray, plasma, railgun), "
    "explosion (bomb, grenade, rocket), shockwave (impact wave, roar, sonic blast, ki blast). "
    "Elements: fire, ice, thunder, earth, water, wind, light (holy), dark (shadow, curse), "
    "arcane (pure magical force), poison (venom, acid, toxic gas); "
    "use the _aoe form when it hits all enemies at once."
)

#: 控えた差し替えを捨てるまでの秒数。ゲームは絵と音を Clock で少し後に出す。
PENDING_SECONDS = 6.0
#: 演出の関数ごとに、最初のこの回数だけ引数をログに残す（どこを通るかの確かめ）。
EFFECT_SAMPLES = 5
#: 敵の手の `character_side`。ゲームは敵が味方を打つ手には絵を出さない（揺れと音だけ）ので、
#: 絵が来なくても知らせない。音は差し替える。
ENEMY_SIDE = "敵側"
#: MOD が描いた絵の画風（`effects/` の下のフォルダ名）。設定で変えられる。
ART_STYLE = "soft"
ART_STYLES = ("soft", "bright")


def literal_choices(annotation):
    """`Literal[...]` なら選択肢のタプル、違えば None。"""
    if typing.get_origin(annotation) is typing.Literal:
        return tuple(typing.get_args(annotation))
    return None


def added_choices(existing):
    """既にある選択肢の後ろに足す値（重ねない）。並びは GAME_HANDLED → VFX の順。"""
    have = set(existing)
    return tuple(value for value in GAME_HANDLED + tuple(VFX) if value not in have)


def extend_structure(structure, pydantic_module):
    """`vfx` の選択肢を足した子クラス。対象でなければ None。

    対象は「`vfx` の欄が Literal で、slash を含む」型だけ。
    戦闘の審判以外の型や、版が変わって形が違う型には触らない。
    """
    fields = getattr(structure, "model_fields", None)
    if not isinstance(fields, dict) or "vfx" not in fields:
        return None
    info = fields["vfx"]
    choices = literal_choices(getattr(info, "annotation", None))
    if not choices or "slash" not in choices:
        return None
    extra = added_choices(choices)
    if not extra:
        return None
    widened = typing.Literal[choices + extra]
    description = getattr(info, "description", None)
    description = (description + " " + VFX_GUIDE) if description else VFX_GUIDE
    if info.is_required():
        field = pydantic_module.Field(..., description=description)
    else:
        field = pydantic_module.Field(default=info.default, description=description)
    return pydantic_module.create_model(
        structure.__name__, __base__=structure, vfx=(widened, field))


def same_folder(a, b):
    """絵のフォルダが同じか（区切りと末尾の / の違いは見ない）。"""
    def norm(path):
        return os.path.normcase(str(path).replace("\\", "/").rstrip("/"))
    return norm(a) == norm(b)


def resolve_sources(mod_dir, game_dir, style):
    """VFX の絵を、ゲームへ渡すフォルダに直す。無いものは入れない（運び手の絵のまま出る）。

    戻り値は (値 → フォルダ, 見つからなかった値の一覧)。
    MOD の絵は `effects/<style>/` の絶対パス、ゲームの絵はゲームが自分で使うのと同じ相対パスで渡す。
    """
    sources, missing = {}, []
    for name, (_carrier, art, _sound) in VFX.items():
        if art.startswith("mod:"):
            folder = os.path.join(mod_dir, "effects", style, art[4:])
            ok = os.path.isdir(folder)
        else:
            folder = art
            ok = os.path.isdir(os.path.join(game_dir, art)) if game_dir else False
        if ok:
            sources[name] = folder
        else:
            missing.append(name)
    return sources, missing


def battle_action_of(args, kwargs):
    """`resolve_battle_effect(character_key, character_side, battle_action, ...)` の battle_action。"""
    if "battle_action" in kwargs:
        return kwargs["battle_action"]
    if len(args) >= 3:
        return args[2]
    return None


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="attack animations:")
    style = ART_STYLE if ART_STYLE in ART_STYLES else ART_STYLES[0]
    sources, missing = resolve_sources(ctx.mod_dir, ctx.game_dir, style)
    if missing:
        write("WARN art not found, falls back to the carrier: {}".format(", ".join(missing)))

    # 型の差し替えの控え。送り口は1手ごとに新しい Structure を作るので、クラスを鍵に弱く持つ。
    extended = weakref.WeakKeyDictionary()
    # 差し替えの控え（resolve_battle_effect の1回ぶん）。
    pending = {"vfx": None, "carrier": None, "source": None, "sound": None, "until": 0.0,
               "art": False, "heard": False, "side": None}
    effect_seen = {}
    widened_for = set()

    # ------------------------------------------------------------ 1. 審判の選択肢
    def install_send(target):
        @ctx.wrap(target, required=False, safe=True)
        def battle_send(orig, *args, **kwargs):
            if "structure" in kwargs:
                structure, where = kwargs["structure"], "kw"
            elif len(args) >= 3:
                structure, where = args[2], "pos"
            else:
                return orig(*args, **kwargs)
            if not isinstance(structure, type):
                return orig(*args, **kwargs)
            try:
                widened = extended.get(structure)
            except TypeError:
                widened = None
            if widened is None:
                pydantic_module = sys.modules.get("pydantic")
                if pydantic_module is None:
                    return orig(*args, **kwargs)
                widened = extend_structure(structure, pydantic_module)
                if widened is None:
                    return orig(*args, **kwargs)
                try:
                    extended[structure] = widened
                except TypeError:
                    pass
                manager_name = args[0] if args else kwargs.get("manager_name")
                if manager_name not in widened_for:
                    widened_for.add(manager_name)
                    write("widened vfx for {}: {}".format(
                        manager_name, ", ".join(literal_choices(widened.model_fields["vfx"].annotation))))
            if where == "kw":
                kwargs = dict(kwargs)
                kwargs["structure"] = widened
            else:
                args = args[:2] + (widened,) + tuple(args[3:])
            return orig(*args, **kwargs)

    llm.watch_aliases(ctx, list(BATTLE_SEND_TARGETS), install_send, label="attack animations")

    # ------------------------------------------------------------ 2. 絵と音の差し替え
    def live():
        return pending["vfx"] is not None and time.monotonic() < pending["until"]

    def report_unused():
        """前の手で控えた差し替えが、絵か音のどちらかに当たらなかったら残す。"""
        if pending["vfx"] is None or pending["side"] == ENEMY_SIDE:
            return
        if not pending["art"]:
            write("NOTE {}: no {} effect call came within {}s".format(
                pending["vfx"], pending["carrier"], PENDING_SECONDS))

    @ctx.wrap(RESOLVE_TARGET, required=False, safe=True)
    def resolve_battle_effect(orig, self, *args, **kwargs):
        report_unused()
        pending["vfx"] = None
        action = battle_action_of(args, kwargs)
        vfx = action.get("vfx") if isinstance(action, dict) else None
        spec = VFX.get(vfx) if isinstance(vfx, str) else None
        if spec is not None:
            carrier, _art, sound = spec
            action["vfx"] = carrier
            side = kwargs.get("character_side", args[1] if len(args) >= 2 else None)
            pending.update(vfx=vfx, carrier=carrier, source=sources.get(vfx), sound=sound,
                           until=time.monotonic() + PENDING_SECONDS, art=False, heard=False,
                           side=side)
            write("{} ({}): {} carries it{}{}".format(
                vfx, side, carrier,
                "" if pending["source"] else " (no art, carrier's own)",
                ", sound {}".format(sound) if sound else ""))
        elif isinstance(vfx, str) and vfx in GAME_HANDLED:
            write("{}: the game draws it".format(vfx))
        return orig(self, *args, **kwargs)

    def install_effect(target):
        name = target.rpartition(":")[2]

        @ctx.wrap(target, required=False, safe=True)
        def effect(orig, self, *args, **kwargs):
            key = "effect_src" if "effect_src" in kwargs else "folder_path"
            src = kwargs.get(key) if key in kwargs else (args[0] if args else None)
            seen = effect_seen.get(name, 0)
            if seen < EFFECT_SAMPLES:
                effect_seen[name] = seen + 1
                write("{} src={} rest={} {}".format(name, src, args[1:], kwargs))
            if (src is not None and live() and pending["source"]
                    and same_folder(src, CARRIER_SOURCES[pending["carrier"]])):
                pending["art"] = True
                if key in kwargs:
                    kwargs = dict(kwargs)
                    kwargs[key] = pending["source"]
                else:
                    args = (pending["source"],) + tuple(args[1:])
                write("{}: {} -> {} ({})".format(pending["vfx"], src, pending["source"], name))
            return orig(self, *args, **kwargs)

    for target in EFFECT_TARGETS:
        install_effect(target)

    # `play_sound(self, app, sound_src)`。音の名前は2つ目（recon の targets.txt）。
    @ctx.wrap(SOUND_TARGET, required=False, safe=True)
    def play_sound(orig, self, *args, **kwargs):
        named = "sound_src" in kwargs
        name = kwargs["sound_src"] if named else (args[1] if len(args) >= 2 else None)
        if live() and pending["sound"] and name in ELEMENT_SOUNDS:
            if name != pending["sound"]:
                if not pending["heard"]:
                    write("{}: sound {} -> {}".format(pending["vfx"], name, pending["sound"]))
                if named:
                    kwargs = dict(kwargs)
                    kwargs["sound_src"] = pending["sound"]
                else:
                    args = (args[0], pending["sound"]) + tuple(args[2:])
            pending["heard"] = True
        return orig(self, *args, **kwargs)

    ctx.log("attack animations: {} choices added ({} with art, style {}); log -> {}".format(
        len(GAME_HANDLED) + len(VFX), len(sources), style, ctx.out_path(LOG_BASENAME)))
