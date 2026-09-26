# -*- coding: utf-8 -*-
r"""計測: 効果音。ゲームは変えない。

効果音の出口 `SoundManager.play_sound` / `play_sound_from_src`（`106_` が控えを取っている2つ。
GAME.md §2.11）を包み、鳴らすたびに1行書く。
あわせて本体の装備の入口 `ItemEquipManager.equip_item` / `ItemUnequipManager.unequip_item` と、
戦闘の1手 `BattlePhaseManager.resolve_battle_effect` の出入りを録る。

`333_equipment_slots` が装備を変えないのに `equip_item` を呼び直して、所持品の窓を開くたびと
戦闘の1手ごとに装備の音が鳴っていた（別の環境の実機）。直した後に、
装備の音がいつ・誰の呼び出しで鳴っているか、攻撃の音が鳴っているかを耳ではなく数で確かめる。

録るもの（1件＝1行）

    sound     効果音を1回鳴らした。引数（音の名前か絵のパス）、equip_item / unequip_item /
              戦闘の1手の中か、戦闘の旗、スタックに居る MOD、呼び出し元（ゲーム側）
    equip     本体の equip_item / unequip_item が呼ばれた。品の名前、スタックに居る MOD、呼び出し元
    turn      戦闘の1手の始まり

`frames.caller()` はローダと MOD のフレームを飛ばすので、どの MOD が呼んだかは
スタックのファイル名（`runtime\mods\<フォルダ>\`）から別に拾う。

    out\sound_effects.log     読む用
    out\sound_effects.jsonl   後から数える用
"""
import datetime
import os
import sys
import threading
import time

from instantale_modloader import frames

LOG_BASENAME = "sound_effects.log"
RECORD_BASENAME = "sound_effects.jsonl"

#: 戦闘の旗（`107_` の表と同じ3つ）。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")

#: 呼び出し元の連鎖の段数。
CALLER_DEPTH = 6


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    started = time.monotonic()
    # ctx の値は apply() の間だけ使える。自分のフォルダ名はここで決めておく
    own = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "235_probe_sound_effects"
    mods_mark = os.sep + "mods" + os.sep
    #: いま中に居る本体の関数（入れ子の数）。スレッドをまたぐと正しくないが、装備と戦闘の1手は1本の流れ
    inside = {"equip_item": 0, "unequip_item": 0, "battle_turn": 0}

    def mods_on_stack():
        """いまのスタックに居る MOD のフォルダ名（近い順）。この probe 自身は除く。"""
        names = []
        frame = sys._getframe(1)
        while frame is not None:
            path = (frame.f_code.co_filename or "").replace("/", os.sep)
            at = path.find(mods_mark)
            if at >= 0:
                folder = path[at + len(mods_mark):].split(os.sep, 1)[0]
                if folder and folder != own and folder not in names:
                    names.append(folder)
            frame = frame.f_back
        return names

    def flags_of(app):
        return [flag for flag in BATTLE_FLAGS if getattr(app, flag, False)]

    def event(kind, **fields):
        row = {"at": datetime.datetime.now().isoformat(timespec="milliseconds"),
               "t": round(time.monotonic() - started, 3), "kind": kind,
               "thread": threading.current_thread().name}
        row.update(fields)
        record(row)
        write("{:>9.3f} [{}] {} {}".format(
            row["t"], row["thread"], kind,
            " ".join("{}={}".format(k, v) for k, v in fields.items())))

    # ------------------------------------------------------------ 効果音
    def watch_sound(method):
        def wrapper(orig, self, app=None, *args, **kwargs):
            try:
                event("sound", method=method,
                      args=[frames.short(repr(a), 120) for a in args],
                      within=[name for name, depth in inside.items() if depth > 0],
                      flags=flags_of(app), mods=mods_on_stack(),
                      caller=frames.caller(CALLER_DEPTH))
            except Exception:
                ctx.log_exc("sound effects probe: recording {} failed".format(method))
            return orig(self, app, *args, **kwargs)
        return wrapper

    for _method in ("play_sound", "play_sound_from_src"):
        ctx.wrap("scripts.sounds:SoundManager." + _method, required=False, safe=True)(
            watch_sound(_method))

    # ------------------------------------------------------------ 装備と戦闘の1手
    def watch_inside(name, kind):
        def wrapper(orig, self, *args, **kwargs):
            try:
                if kind == "equip":
                    item = args[0] if args else None
                    event("equip", method=name,
                          item=frames.short(getattr(item, "name", None) or repr(item), 60),
                          mods=mods_on_stack(), caller=frames.caller(CALLER_DEPTH))
                else:
                    event("turn", caller=frames.caller(CALLER_DEPTH))
            except Exception:
                ctx.log_exc("sound effects probe: recording {} failed".format(name))
            inside[name] += 1
            try:
                return orig(self, *args, **kwargs)
            finally:
                inside[name] -= 1
        return wrapper

    ctx.wrap("__main__:ItemEquipManager.equip_item", required=False, safe=True)(
        watch_inside("equip_item", "equip"))
    ctx.wrap("__main__:ItemUnequipManager.unequip_item", required=False, safe=True)(
        watch_inside("unequip_item", "equip"))
    ctx.wrap("__main__:BattlePhaseManager.resolve_battle_effect", required=False, safe=True)(
        watch_inside("battle_turn", "turn"))
