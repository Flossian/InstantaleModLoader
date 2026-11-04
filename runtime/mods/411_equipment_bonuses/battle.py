# -*- coding: utf-8 -*-
"""武器の効果を戦闘に効かせる（会心・特効・貫通・吸収・毒刃・怯み）。

1手（`handle_battle_situation`）の中で、手番の者が身に着けている武器の効果を読み、
ゲームの計算の入口と出口に足す。どれも敵に当たる手だけで、敵の武器には効かない。

    会心   `calculate_battle_effect` の前に、敵に当たる裁きを1段上げる
    貫通   `get_instant_damage(素点, 防御)` に渡る敵の防御を減らす
    特効   `get_instant_damage` の戻り（その一撃のダメージ）に上乗せする
    吸収   その手で敵に与えたダメージ（敵の残りの体力まで）の割合を、1手の終わりに戻す
    毒刃   命中した敵の `status` にゲームと同じ形で書き、毎手の減算の前に体力を削る
    怯み   命中した敵の `status` に書き、その敵の一撃を弱める

ダメージの経路は GAME.md §2.10.2。敵の防御は `get_npc_defense` で聞かれた直後に
`get_instant_damage` へ渡るので、直前に聞かれた敵を受け側とする（`319_` と同じ見分け方）。
`319_` は `get_instant_damage` の戻りを自分の式に差し替える。この MOD はその外側に居て
（読み込み順が後）、差し替えた後のダメージに上乗せする。
"""

import sys
import threading

from instantale_modloader import frames, ui

from . import effects

ALLY_SIDE = "味方陣営"
ENEMY_SIDE = "敵側"

#: 毒刃・怯みの帳簿の上限（戦闘の始めに空にする。取りこぼしても溜めない）。
MARK_LIMIT = 200


def install(ctx, write, effects_of, language):
    """フックを置く。

    `effects_of(app, holder)` は身に着けている武器の効果をまとめた辞書（`effects.combine`）を返す。
    答える相手でなければ空。`language()` はゲームの表示言語。
    """
    state = {
        # 開いている1手。{"app","side","actor","name","effects","notes","dealt","slayer"}
        "action": None,
        # 直近の get_npc_defense（持ち主, 値）
        "last_defense": None,
        # 毒刃・怯みを付けた相手。{(id(持ち主), 種類): 持ち主}
        "marks": {},
    }

    def dry_run():
        """計測 MOD の試し打ちの最中か（GAME.md §2.10.4 の印）。"""
        return getattr(sys, "_instantale_probe_dry_run_thread", None) == threading.get_ident()

    def say(app, text):
        try:
            app.add_text(text)
        except Exception:
            ctx.log_exc("equipment bonuses: add_text failed")

    def enemies_of(app):
        found = frames.attr(app, "current_enemy_dict", None)
        return found if isinstance(found, dict) else {}

    def is_enemy(app, holder):
        return any(found is holder for found in enemies_of(app).values())

    def enemy_names(app):
        names = set()
        for key, holder in enemies_of(app).items():
            names.add(str(key))
            name = frames.attr(holder, "name", None)
            if name:
                names.add(str(name))
        return names

    def actor_of(app, character_key, character_side):
        """1手の主。敵は `current_enemy_dict` の鍵、味方は表示名で引く（`319_` と同じ）。"""
        if character_side == ENEMY_SIDE:
            return enemies_of(app).get(character_key)
        player = frames.attr(app, "player", None)
        if player is not None and str(frames.attr(player, "name", "")) == str(character_key):
            return player
        for member_id in ui.party_member_ids(app):
            member = ui.character_of(app, member_id)
            if member is not None and str(frames.attr(member, "name", "")) == str(character_key):
                return member
        return None

    def name_of(holder, fallback="?"):
        name = frames.attr(holder, "name", None)
        return str(name) if name else str(fallback)

    def number(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def field(holder, name):
        """`battle_action` とその中身は dict（変換後の形。GAME.md §2.10.2）。属性でも読む。"""
        return holder.get(name) if isinstance(holder, dict) else frames.attr(holder, name, None)

    # ------------------------------------------------------------ 毒刃・怯み
    def status_name(kind):
        return effects.STATUS_NAMES[language()][kind]

    def marked(holder, kind):
        """`holder` に、この MOD が付けた `kind` がまだ乗っているか。"""
        key = (id(holder), kind)
        if state["marks"].get(key) is not holder:
            return False
        statuses = frames.attr(holder, "status", None)
        if isinstance(statuses, dict) and status_name(kind) in statuses:
            return True
        del state["marks"][key]           # ゲームが期限で消した
        return False

    def mark(holder, kind, turns):
        """ゲーム自身の入れ物（`Character.status`）へ同じ形で書く。期限の減算と削除はゲームに任せる。"""
        statuses = frames.attr(holder, "status", None)
        if not isinstance(statuses, dict):
            return False
        lang = language()
        name = effects.STATUS_NAMES[lang][kind]
        statuses[name] = {"status_name": name,
                          "description": effects.STATUS_TEXTS[lang][kind],
                          "duration": int(turns)}
        if len(state["marks"]) >= MARK_LIMIT:
            state["marks"].clear()
        state["marks"][(id(holder), kind)] = holder
        return True

    # ------------------------------------------------------------ 1手
    def open_action(app, character_key, character_side):
        side = ENEMY_SIDE if character_side == ENEMY_SIDE else ALLY_SIDE
        actor = actor_of(app, character_key, character_side)
        found = {}
        if side == ALLY_SIDE and actor is not None:
            found = effects_of(app, actor) or {}
        state["action"] = {"app": app, "side": side, "actor": actor,
                           "name": name_of(actor, character_key),
                           "effects": found, "notes": [], "dealt": 0, "slayer": []}
        state["last_defense"] = None

    def close_action():
        action, state["action"] = state["action"], None
        state["last_defense"] = None
        if action is None:
            return
        app, actor = action["app"], action["actor"]
        lang = language()
        if action["slayer"]:
            labels = [effects.TARGET_LABELS[lang][t] for t in action["slayer"]]
            action["notes"].append(("（特効: {}）" if lang == "ja" else "(Slayer: {})")
                                   .format("・".join(labels) if lang == "ja" else ", ".join(labels)))
        heal = effects.drained(action["dealt"], action["effects"].get("lifesteal"))
        if heal and actor is not None:
            hp, top = number(frames.attr(actor, "current_hp", None)), number(frames.attr(actor, "max_hp", None))
            if hp is not None and hp > 0:
                new_hp = int(min(top, hp + heal)) if top else int(hp + heal)
                if new_hp > hp:
                    actor.current_hp = new_hp
                    action["notes"].append(("（{}は体力を {} 吸収した）" if lang == "ja"
                                            else "({} drains {} HP)").format(action["name"], new_hp - int(hp)))
                    write("lifesteal: {} dealt {} -> +{} ({} -> {})".format(
                        action["name"], action["dealt"], new_hp - int(hp), hp, new_hp))
        if action["notes"]:
            say(app, "\n".join(action["notes"]))

    @ctx.wrap("__main__:BattlePhaseManager.handle_battle_situation", required=False, safe=True)
    def handle_battle_situation(orig, self, *args, **kwargs):
        if dry_run():
            return orig(self, *args, **kwargs)
        try:
            open_action(getattr(self, "app", None) or ui.find_app(),
                        frames.arg(args, kwargs, "character_key", 0),
                        frames.arg(args, kwargs, "character_side", 1))
        except Exception:
            state["action"] = None
            ctx.log_exc("equipment bonuses: cannot open the action")
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                close_action()
            except Exception:
                ctx.log_exc("equipment bonuses: cannot close the action")

    # ------------------------------------------------------------ 会心
    @ctx.wrap("__main__:BattlePhaseManager.calculate_battle_effect", required=False, safe=True)
    def calculate_battle_effect(orig, self, *args, **kwargs):
        try:
            action = state["action"]
            chance = action["effects"].get("critical") if action and not dry_run() else None
            if chance and effects.RNG.random() * 100 < chance:
                battle_action = frames.arg(args, kwargs, "battle_action", 0)
                names = enemy_names(action["app"])
                raised = []
                for entry in field(battle_action, "instant_damage") or []:
                    target, power = field(entry, "target"), field(entry, "power")
                    higher = effects.raise_power(power)
                    if str(target) not in names or higher is None:
                        continue
                    if isinstance(entry, dict):
                        entry["power"] = higher
                    else:
                        setattr(entry, "power", higher)
                    raised.append("{} {}->{}".format(target, power, higher))
                if raised:
                    action["notes"].append(("（{}の会心の一撃）" if language() == "ja"
                                            else "({} lands a critical hit)").format(action["name"]))
                    write("critical: {} ({}%) {}".format(action["name"], chance, "; ".join(raised)))
        except Exception:
            ctx.log_exc("equipment bonuses: critical failed")
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------ 受け側の見分け
    @ctx.wrap("scripts.characters:Character.get_npc_defense", required=False, safe=True)
    def get_npc_defense(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if state["action"] is not None:
            state["last_defense"] = (self, result)
        return result

    # ------------------------------------------------------------ 貫通・特効・吸収・毒刃・怯み
    @ctx.wrap("scripts.functions:get_instant_damage", required=False, safe=True)
    def get_instant_damage(orig, *args, **kwargs):
        action = state["action"]
        if action is None or dry_run():
            return orig(*args, **kwargs)
        try:
            if action["side"] == ENEMY_SIDE:
                return weakened(action, orig(*args, **kwargs))
            if not action["effects"]:
                return orig(*args, **kwargs)
            defense = frames.arg(args, kwargs, "defense", 1)
            last = state["last_defense"]
            defender = last[0] if last is not None and last[1] == defense \
                and is_enemy(action["app"], last[0]) else None
        except Exception:
            ctx.log_exc("equipment bonuses: cannot read the hit")
            return orig(*args, **kwargs)
        if defender is None:
            return orig(*args, **kwargs)
        found = action["effects"]
        hp = number(frames.attr(defender, "current_hp", None))
        top = number(frames.attr(defender, "max_hp", None))
        pierce = found.get("pierce")
        if pierce and number(defense) is not None:
            # 来た側（位置かキーワード）のまま差し替える（TECH.md の規則の表「包みは受けた引数をそのまま」）
            args, kwargs, _ = frames.replace_arg(args, kwargs, "defense", 1,
                                                 effects.pierced(defense, pierce))
        result = orig(*args, **kwargs)
        try:
            app = action["app"]
            dealt = result
            percent, matched = effects.slayer_bonus(
                found.get("slayer"), bool(frames.attr(app, "in_boss_battle", False)),
                number(frames.attr(action["actor"], "experience_level", None)),
                number(frames.attr(defender, "experience_level", None)), hp, top)
            if percent and number(result):
                dealt = effects.scaled(result, percent)
                for target in matched:
                    if target not in action["slayer"]:
                        action["slayer"].append(target)
            if number(dealt) and dealt > 0:
                action["dealt"] += min(dealt, hp) if hp is not None and hp > 0 else dealt
                for kind, turns in (("venom", effects.VENOM_TURNS), ("daunt", effects.DAUNT_TURNS)):
                    chance = found.get(kind)
                    if chance and effects.RNG.random() * 100 < chance and mark(defender, kind, turns):
                        write("{}: {} -> {} ({}%)".format(kind, action["name"], name_of(defender), chance))
            write("hit: {} -> {} def={}{} dmg={} -> {}{}".format(
                action["name"], name_of(defender), defense,
                " pierced to {:g}".format(effects.pierced(defense, pierce)) if pierce and number(defense) is not None else "",
                result, dealt, " slayer +{}% {}".format(percent, matched) if percent else ""))
            return dealt
        except Exception:
            ctx.log_exc("equipment bonuses: weapon effects failed; damage left as is")
            return result

    def weakened(action, result):
        """怯んだ敵の一撃を弱める。"""
        actor = action["actor"]
        if actor is None or not number(result) or not marked(actor, "daunt"):
            return result
        lowered = effects.scaled(result, -effects.DAUNT_CUT)
        write("daunt: {} {} -> {}".format(action["name"], result, lowered))
        return lowered

    # ------------------------------------------------------------ 毒刃の毎手の減算
    @ctx.wrap("__main__:BattlePhaseManager.reduce_status_turns_and_log", required=False, safe=True)
    def reduce_status_turns_and_log(orig, self, *args, **kwargs):
        # ゲームの減算より先に削る（`319_` の継続ダメージと同じ置き方。期限0で消える前の手も削る）
        try:
            character = frames.arg(args, kwargs, "character", 0)
            if character is not None and marked(character, "venom"):
                hp = number(frames.attr(character, "current_hp", None))
                amount = effects.venom_tick(number(frames.attr(character, "max_hp", None)))
                if hp is not None and hp > 1 and amount:
                    new_hp = int(max(1, hp - amount))      # 毒刃では倒れない
                    try:
                        ui.note_damage(name_of(character), hp - new_hp, status_name("venom"))
                    except Exception:
                        pass
                    character.current_hp = new_hp
                    write("venom tick: {} {} -> {}".format(name_of(character), hp, new_hp))
        except Exception:
            ctx.log_exc("equipment bonuses: venom tick failed")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        state["marks"].clear()
        state["action"] = None
        state["last_defense"] = None
        return orig(self, *args, **kwargs)

    return state
