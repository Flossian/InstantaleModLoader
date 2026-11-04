# -*- coding: utf-8 -*-
"""武器と防具の効果を戦闘に効かせる。

1手（`handle_battle_situation`）の中で、手番の者が身に着けている武器の効果を読み、
ゲームの計算の入口と出口に足す。敵に当たる手だけで、敵の武器には効かない。

    会心   `calculate_battle_effect` の前に、敵に当たる裁きを1段上げる
    貫通   `get_instant_damage(素点, 防御)` に渡る敵の防御を減らす
    特効・呪い・成長・連携   `get_instant_damage` の戻り（その一撃のダメージ）に上乗せする（足してから1回掛ける）
    処刑   上乗せの後、敵の残りが割合以下なら敵の体力ぶんに引き上げる
    吸収   敵に当てるたびに、その手で与えたダメージ（敵の残りの体力まで）の割合を戻す
    呪い   当てた手で1回、自分の最大体力の割合を失う（体力 1 で止まる）
    成長   倒すたびに品の説明欄の討伐数を増やす
    毒刃   命中した敵の `status` にゲームと同じ形で書き、毎手の減算の前に体力を削る
    怯み   命中した敵の `status` に書き、その敵の一撃を弱める

防具の効果は、敵の手で味方が受ける一撃に効く。

    軽業   一撃を無効にする
    重装・呪縛・火事場   受けるダメージを減らす。足さずに順に掛ける（足すと種類が違うだけで 100% に届く）。
           器用の低下は `equipment_bonuses.py` が能力値の窓口へ答え、呪縛の代償は味方の手の上乗せから引く
    守護   仲間への一撃を、別の者がこの確率で肩代わりする（その者の防具で受け、倒れるなら肩代わりしない）
    不屈   倒れる一撃を体力 1 で耐える（戦闘に1回）
    棘     受けたダメージの割合を、その敵に返す（敵は体力 1 で止まる。手番の中で敵を消さない）
    再生   毎手の減算の前に、最大体力の割合を戻す
    耐性   敵の手の `resolve_battle_effect` で新しく付いた状態異常を外す（味方の補助は敵の手で付かない）

ダメージの経路は GAME.md §2.10.2。敵の防御は `get_npc_defense` で聞かれた直後に
`get_instant_damage` へ渡るので、直前に聞かれた敵を受け側とする（`319_` と同じ見分け方）。
味方の受け側は、防御の値で分からないとき（主人公は防具の値で来る）、
`calculate_battle_effect` の戻り（対象 → 素点）を素点で照らす。これも `319_` と同じ。
戻りは対象ごとの辞書なので、1手で同じ相手に当たるのは1回。
`319_` は `get_instant_damage` の戻りを自分の式に差し替える。この MOD はその外側に居て
（読み込み順が後）、差し替えた後のダメージに足す。

体力を動かす効果は、1手の報告（`308_`）より先に動かし、`ui.note_damage` で出どころを控える。行は 308 が出す。
"""

import sys
import threading

from instantale_modloader import frames, ui

from . import effects

ALLY_SIDE = "味方陣営"
ENEMY_SIDE = "敵側"

#: 毒刃・怯みの帳簿の上限（戦闘の始めに空にする。取りこぼしても溜めない）。
MARK_LIMIT = 200


def install(ctx, write, effects_of, armor_of, language):
    """フックを置く。

    `effects_of(app, holder)` は身に着けている武器の効果をまとめた辞書（`effects.combine`）、
    `armor_of(app, holder)` は防具の効果をまとめた辞書を返す。答える相手でなければ空。
    `language()` はゲームの表示言語。
    """
    state = {
        # 開いている1手。open_action の辞書。
        "action": None,
        # 直近の get_npc_defense（持ち主, 値）
        "last_defense": None,
        # 毒刃・怯みを付けた相手。{(id(持ち主), 種類): 持ち主}
        "marks": {},
        # 直前に敵へ当てた味方の手（持ち主, 当てた敵の並び）。連携が読む
        "last_hit": None,
        # この戦闘で不屈を使った者の id
        "endured": set(),
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

    def allies_of(app):
        """戦闘に出ている味方（逃げて一覧から外れた主人公は入らない）。"""
        found = []
        player = frames.attr(app, "player", None)
        party = frames.attr(app, "party", None)
        if player is not None and (not isinstance(party, dict) or "player" in party):
            found.append(player)
        for member_id in ui.party_member_ids(app):
            member = ui.character_of(app, member_id)
            if member is not None:
                found.append(member)
        return found

    def name_of(holder, fallback="?"):
        name = frames.attr(holder, "name", None)
        return str(name) if name else str(fallback)

    def number(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def field(holder, name):
        """`battle_action` とその中身は dict（変換後の形。GAME.md §2.10.2）。属性でも読む。"""
        return holder.get(name) if isinstance(holder, dict) else frames.attr(holder, name, None)

    def hp_of(holder):
        return number(frames.attr(holder, "current_hp", None)), number(frames.attr(holder, "max_hp", None))

    def label(kind):
        return effects.LABELS[language()][kind]

    def lose_hp(holder, amount, kind):
        """`kind` の出どころで体力を減らす。体力 1 で止まり、これでは倒れない。減らした量を返す。"""
        hp = number(frames.attr(holder, "current_hp", None))
        if hp is None or hp <= 1 or not amount:
            return 0
        new_hp = int(max(1, hp - amount))
        ui.note_damage(name_of(holder), hp - new_hp, label(kind))
        holder.current_hp = new_hp
        return hp - new_hp

    def gain_hp(holder, amount, kind):
        """`kind` の出どころで体力を戻す（最大まで。倒れている者は戻さない）。戻した量を返す。"""
        hp, top = hp_of(holder)
        if hp is None or hp <= 0 or not amount:
            return 0
        new_hp = int(min(top, hp + amount)) if top else int(hp + amount)
        if new_hp <= hp:
            return 0
        ui.note_damage(name_of(holder), new_hp - hp, label(kind))
        holder.current_hp = new_hp
        return new_hp - hp

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
        found, worn = {}, {}
        if side == ALLY_SIDE and actor is not None:
            found = effects_of(app, actor) or {}
            worn = armor_of(app, actor) or {}
        state["action"] = {"app": app, "side": side, "actor": actor,
                           "name": name_of(actor, character_key), "effects": found, "armor": worn,
                           "notes": [], "dealt": 0, "drained": 0, "slayer": [],
                           "plan": {},       # 対象 → 素点（calculate_battle_effect の戻り）
                           "foes": [],       # この手で当てた敵
                           "teamwork": False, "cursed": False}
        state["last_defense"] = None

    def close_action():
        action, state["action"] = state["action"], None
        state["last_defense"] = None
        if action is None:
            return
        lang = language()
        if action["slayer"]:
            labels = [effects.TARGET_LABELS[lang][t] for t in action["slayer"]]
            action["notes"].append(("（特効: {}）" if lang == "ja" else "(Slayer: {})")
                                   .format("・".join(labels) if lang == "ja" else ", ".join(labels)))
        if action["teamwork"]:
            action["notes"].append("（連携）" if lang == "ja" else "(Teamwork)")
        if action["foes"]:
            state["last_hit"] = (action["actor"], action["foes"])
        if action["notes"]:
            say(action["app"], "\n".join(action["notes"]))

    def drain(action):
        """吸収の体力を戻す。戻す量は1手の累計から決める（小さな当たりが続いても端数を捨てない）。"""
        owed = effects.drained(action["dealt"], action["effects"].get("lifesteal")) - action["drained"]
        if action["actor"] is None or owed <= 0:
            return
        action["drained"] += owed
        gained = gain_hp(action["actor"], owed, "lifesteal")
        if gained:
            write("lifesteal: {} dealt {} -> +{}".format(action["name"], action["dealt"], gained))

    def pay_curse(action):
        """呪いの代償。当てた手で1回だけ。"""
        if action["cursed"] or not action["effects"].get("curse") or action["actor"] is None:
            return
        action["cursed"] = True
        cost = effects.share_of_max(number(frames.attr(action["actor"], "max_hp", None)), effects.CURSE_COST)
        lost = lose_hp(action["actor"], cost, "curse")
        if lost:
            write("curse: {} loses {}".format(action["name"], lost))

    def count_kill(action, defender):
        """倒した数を、身に着けている成長の品に数える。節目を越えたら本文に1行出す。"""
        for item in action["effects"].get("growth_items") or ():
            kills = effects.add_kill(item)
            if kills is None:
                continue
            write("growth: {} on {} ({} kills) by {}".format(
                name_of(defender), name_of(item), kills, action["name"]))
            if kills % effects.GROWTH_STEP == 0:
                action["notes"].append(("（{}が手に馴染んできた: 討伐 {}）" if language() == "ja"
                                        else "({} grows stronger: {} kills)").format(name_of(item), kills))

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

    # ------------------------------------------------------------ 会心・素点の控え
    @ctx.wrap("__main__:BattlePhaseManager.calculate_battle_effect", required=False, safe=True)
    def calculate_battle_effect(orig, self, *args, **kwargs):
        action = state["action"] if not dry_run() else None
        try:
            chance = action["effects"].get("critical") if action else None
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
        result = orig(self, *args, **kwargs)
        if action is not None and isinstance(result, (list, tuple)) and result and isinstance(result[0], dict):
            action["plan"] = {str(target): raw for target, raw in result[0].items()
                              if number(raw) is not None}
        return result

    # ------------------------------------------------------------ 受け側の見分け
    @ctx.wrap("scripts.characters:Character.get_npc_defense", required=False, safe=True)
    def get_npc_defense(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if state["action"] is not None:
            state["last_defense"] = (self, result)
        return result

    def enemy_hit_by(action, defense):
        """味方の手で受ける敵。分からなければ None。"""
        last = state["last_defense"]
        if last is not None and last[1] == defense and is_enemy(action["app"], last[0]):
            return last[0]
        return None

    def ally_hit_by(action, attack, defense):
        """敵の手で受ける味方。分からなければ None。"""
        plan, app = action["plan"], action["app"]
        target = next((name for name, raw in plan.items() if raw == attack), None)
        if target is not None:
            del plan[target]
        last = state["last_defense"]
        if last is not None and last[1] == defense and any(last[0] is ally for ally in allies_of(app)):
            return last[0]
        return actor_of(app, target, ALLY_SIDE) if target is not None else None

    @ctx.wrap("scripts.functions:get_instant_damage", required=False, safe=True)
    def get_instant_damage(orig, *args, **kwargs):
        action = state["action"]
        if action is None or dry_run():
            return orig(*args, **kwargs)
        try:
            attack = frames.arg(args, kwargs, "attack", 0)
            defense = frames.arg(args, kwargs, "defense", 1)
            if action["side"] == ENEMY_SIDE:
                ally = ally_hit_by(action, attack, defense)
            else:
                foe = enemy_hit_by(action, defense)
        except Exception:
            ctx.log_exc("equipment bonuses: cannot read the hit")
            return orig(*args, **kwargs)
        if action["side"] == ENEMY_SIDE:
            return enemy_turn_hit(action, ally, orig(*args, **kwargs))
        if foe is None:
            return orig(*args, **kwargs)
        return ally_turn_hit(action, foe, defense, orig, args, kwargs)

    # ------------------------------------------------------------ 味方の手（武器）
    def ally_turn_hit(action, defender, defense, orig, args, kwargs):
        found = action["effects"]
        hp, top = hp_of(defender)
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
            percent += found.get("curse", 0) + found.get("growth", 0)
            if action["armor"].get("binding"):
                percent -= effects.BINDING_COST
            last = state["last_hit"]
            teamed = found.get("teamwork") and last is not None and last[0] is not action["actor"] \
                and any(foe is defender for foe in last[1])
            if teamed:
                percent += found["teamwork"]
            if percent and number(result):
                dealt = effects.scaled(result, percent)
                for target in matched:
                    if target not in action["slayer"]:
                        action["slayer"].append(target)
                action["teamwork"] = action["teamwork"] or bool(teamed)
            executed = number(dealt) and dealt > 0 and effects.should_execute(hp, dealt, top, found.get("execute"))
            if executed:
                dealt = hp
                action["notes"].append(("（{}を処刑した）" if language() == "ja"
                                        else "({} is executed)").format(name_of(defender)))
            if number(dealt) and dealt > 0:
                action["foes"].append(defender)
                action["dealt"] += min(dealt, hp) if hp is not None and hp > 0 else dealt
                guarded(drain, action)
                guarded(pay_curse, action)
                if hp is not None and 0 < hp <= dealt:
                    guarded(count_kill, action, defender)
                for kind, turns in (("venom", effects.VENOM_TURNS), ("daunt", effects.DAUNT_TURNS)):
                    chance = found.get(kind)
                    if chance and effects.RNG.random() * 100 < chance and mark(defender, kind, turns):
                        write("{}: {} -> {} ({}%)".format(kind, action["name"], name_of(defender), chance))
            if found:
                write("hit: {} -> {} def={}{} dmg={} -> {}{}{}".format(
                    action["name"], name_of(defender), defense,
                    " pierced to {:g}".format(effects.pierced(defense, pierce)) if pierce and number(defense) is not None else "",
                    result, dealt, " +{}% {}".format(percent, matched) if percent else "",
                    " executed" if executed else ""))
            return dealt
        except Exception:
            ctx.log_exc("equipment bonuses: weapon effects failed; damage left as is")
            return result

    def guarded(fn, *args):
        try:
            fn(*args)
        except Exception:
            ctx.log_exc("equipment bonuses: {} failed".format(getattr(fn, "__name__", "effect")))

    # ------------------------------------------------------------ 敵の手（怯み・防具）
    def enemy_turn_hit(action, ally, result):
        dealt = weakened(action, result)
        if ally is None or not number(dealt) or dealt <= 0:
            return dealt
        try:
            return armor_hit(action, ally, dealt)
        except Exception:
            ctx.log_exc("equipment bonuses: armor effects failed; damage left as is")
            return dealt

    def armor_hit(action, ally, dealt):
        """防具の効果を通した、ゲームが `ally` に当てるダメージ。"""
        app = action["app"]
        chance = armor_of(app, ally).get("evade")
        if chance and effects.RNG.random() * 100 < chance:
            action["notes"].append(("（{}は身軽にかわした）" if language() == "ja"
                                    else "({} dodges the blow)").format(name_of(ally)))
            write("evade: {} dodges {}".format(name_of(ally), dealt))
            return 0 if isinstance(dealt, int) else 0.0
        guard = guard_for(app, ally, dealt)
        if guard is not None:
            amount = reduced(app, guard, dealt)
            lost = lose_hp(guard, amount, "guardian")
            action["notes"].append(("（{}が{}をかばった）" if language() == "ja"
                                    else "({} shields {})").format(name_of(guard), name_of(ally)))
            write("guardian: {} takes {} for {}".format(name_of(guard), lost, name_of(ally)))
            give_thorns(action, armor_of(app, guard), amount)
            return 0 if isinstance(dealt, int) else 0.0
        armor = armor_of(app, ally)
        amount = reduced(app, ally, dealt)
        hp = number(frames.attr(ally, "current_hp", None))
        chance = armor.get("endure")
        if chance and hp is not None and 0 < hp <= amount and id(ally) not in state["endured"] \
                and effects.RNG.random() * 100 < chance:
            state["endured"].add(id(ally))
            amount = max(0, hp - 1)
            action["notes"].append(("（{}は不屈で踏みとどまった）" if language() == "ja"
                                    else "({} endures the blow)").format(name_of(ally)))
            write("endure: {} {} -> {} (hp {})".format(name_of(ally), dealt, amount, hp))
        give_thorns(action, armor, amount)
        return amount

    def reduced(app, holder, dealt, quiet=False):
        """重装・呪縛・火事場の後のダメージ。"""
        worn = armor_of(app, holder)
        hp, top = hp_of(holder)
        cuts = [worn.get("heavy"), worn.get("binding"),
                worn.get("desperate") if effects.is_desperate(hp, top) else None]
        amount = effects.after_cuts(dealt, cuts)
        if amount != dealt and not quiet:
            write("armor cut: {} {} -> {} {}".format(name_of(holder), dealt, amount, [c for c in cuts if c]))
        return amount

    def guard_for(app, ally, dealt):
        """`ally` への一撃を肩代わりする者。受けても倒れない者だけ。"""
        for other in allies_of(app):
            if other is ally:
                continue
            chance = armor_of(app, other).get("guardian")
            hp = number(frames.attr(other, "current_hp", None))
            if chance and hp is not None and hp > reduced(app, other, dealt, quiet=True) \
                    and effects.RNG.random() * 100 < chance:
                return other
        return None

    def give_thorns(action, armor, taken):
        """棘。受けた量の割合を手番の敵へ返す。"""
        rate = armor.get("thorns")
        enemy = action["actor"]
        if not rate or enemy is None or not number(taken) or taken <= 0:
            return
        lost = lose_hp(enemy, max(1, int(round(taken * rate / 100.0))), "thorns")
        if lost:
            write("thorns: {} loses {}".format(name_of(enemy), lost))

    def weakened(action, result):
        """怯んだ敵の一撃を弱める。"""
        actor = action["actor"]
        if actor is None or not number(result) or not marked(actor, "daunt"):
            return result
        lowered = effects.scaled(result, -effects.DAUNT_CUT)
        write("daunt: {} {} -> {}".format(action["name"], result, lowered))
        return lowered

    # ------------------------------------------------------------ 耐性
    def statuses_of(holder):
        found = frames.attr(holder, "status", None)
        return found if isinstance(found, dict) else None

    @ctx.wrap("__main__:BattlePhaseManager.resolve_battle_effect", required=False, safe=True)
    def resolve_battle_effect(orig, self, *args, **kwargs):
        action = state["action"]
        if action is None or action["side"] != ENEMY_SIDE or dry_run():
            return orig(self, *args, **kwargs)
        watched = []
        try:
            for ally in allies_of(action["app"]):
                chance = armor_of(action["app"], ally).get("resist")
                current = statuses_of(ally)
                if chance and current is not None:
                    watched.append((ally, chance, set(current)))
        except Exception:
            ctx.log_exc("equipment bonuses: cannot read the statuses")
        result = orig(self, *args, **kwargs)
        try:
            for ally, chance, before in watched:
                current = statuses_of(ally) or {}
                for name in [key for key in current if key not in before]:
                    if effects.RNG.random() * 100 < chance:
                        del current[name]
                        action["notes"].append(("（{}は{}を防いだ）" if language() == "ja"
                                                else "({} resists {})").format(name_of(ally), name))
                        write("resist: {} blocks {}".format(name_of(ally), name))
        except Exception:
            ctx.log_exc("equipment bonuses: resist failed")
        return result

    # ------------------------------------------------------------ 毎手の減算の前（毒刃・再生）
    @ctx.wrap("__main__:BattlePhaseManager.reduce_status_turns_and_log", required=False, safe=True)
    def reduce_status_turns_and_log(orig, self, *args, **kwargs):
        # ゲームの減算より先に動かす（`319_` の継続ダメージと同じ置き方。期限0で消える前の手も削る）
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
            if character is not None and not dry_run():
                app = getattr(self, "app", None) or ui.find_app()
                rate = armor_of(app, character).get("regen")
                top = number(frames.attr(character, "max_hp", None))
                gained = gain_hp(character, effects.share_of_max(top, rate), "regen") if rate else 0
                if gained:
                    write("regen: {} +{}".format(name_of(character), gained))
        except Exception:
            ctx.log_exc("equipment bonuses: status tick failed")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        state["marks"].clear()
        state["endured"].clear()
        state["action"] = None
        state["last_defense"] = None
        state["last_hit"] = None
        return orig(self, *args, **kwargs)

    return state
