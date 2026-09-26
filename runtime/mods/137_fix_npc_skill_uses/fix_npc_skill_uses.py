# -*- coding: utf-8 -*-
"""修正: 仲間と敵は、使い切ったスキルと、効果が続いている強化・弱体を選ばない。回数は宿で休むと戻る。

##### 何が起きているか

スキルは `Character.skills` に `{名前: {..., "current_uses": 残り, "max_uses": 上限}}` で入り、
ゲームは使うたびに残りを減らす。プレイヤーは残りが 0 になると選べなくなる（`SkillUnusableManager`）。
**NPC（仲間と敵）には止めが無い**。NPC の手は審判 LLM（`referee_npc`）がスキル一覧から選ぶが、
一覧には残りがマイナスになったスキルもそのまま載り、審判はそれを選び続ける。
`236_probe_enemy_stats` の記録で、仲間の自己強化が残り −23、ボスの全体弱体が −2 まで減っていた。
ローカルのモデルは戦闘の記録にある自分の前の手をなぞりやすく、仲間が効果の見えない自己強化を
毎手繰り返して一度も攻撃しない戦闘が出た（VERIFICATION.md §3.77）。

強化・弱体だけのスキル（効果が `buff` / `debuff`）は、残りがあるうちも同じことが起きる。
回数を使い切るまで毎手重ねがけし、その間は戦力にならない（実機）。
NPC の審判の記録 344 件のうち 137 件が強化・弱体のスキルだった（GAME.md §2.10.4）。

仲間の残り回数はゲームが戻さない。クエストの終わりにも、宿屋の休養（2回）を挟んでも減り続けた。
プレイヤーの回数は、クエストの終わりから宿屋の休養までの間に上限へ戻っていた（GAME.md §2.10.4）。

##### 直し方

- **使い切ったスキルを審判に見せない**。`referee_npc` の間だけ、手番の者の `skills` を
  「残りが 1 以上か、上限の無い（`max_uses` が 0 以下の）スキル」に絞った辞書へ差し替え、
  戻ったら元の辞書に戻す。頼み文とスキルの選択肢（構造化出力の列挙）はどちらも `skills` から
  組まれるので、両方から消える。通常攻撃は残り回数に関わらず外さない（何も選べない手を作らない）。
  スキルの中身の辞書は同じものを指すので、この間にゲームが残りを減らしても元の辞書に残る
- **強化・弱体だけのスキルは、効果の続く間もう一度は見せない**。
  審判がそれを選んだら、その者の次の手番から効果の `duration`（読めなければ 3）手番のあいだ外す。
  数えるのはその者の手番で、戦闘が変わると数え直す
- 審判の途中でセーブが走っても、セーブには元の辞書が入る（`save_game` の間だけ戻す）
- **宿で休養したときだけ、仲間の残りを上限へ戻す**（`VacationRestManager.execute` の後。
  宿屋のほか、同じ休養の経路を使う家なども含む）。プレイヤーの回数はゲームのまま触らない。
  敵は戦闘ごとに作られるので戻さない

MOD を外せば元どおり（使い切ったスキルも選ばれる）。セーブに独自の鍵は増やさない。
"""

from instantale_modloader import frames, ui

LOG_BASENAME = "npc_skill_uses.log"
LOG_TAG = "npc skill uses"

#: 通常攻撃のスキル名。残り回数に関わらず審判に見せる。
BASIC_ATTACK = "通常攻撃"

#: 仲間の回数を戻す休養。宿泊の活動のうち「休養をとる」（GAME.md §2.17）。
REST_TARGET = "__main__:VacationRestManager.execute"

#: 強化・弱体の効果の種類。スキルの効果がこれだけなら、効果の続く間は選ばせない。
LINGERING_TYPES = ("buff", "debuff")

#: 効果に `duration` が無いときの手番数。
DEFAULT_DURATION = 3


def _uses(skill, key):
    value = skill.get(key) if isinstance(skill, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def usable(name, skill):
    """残り回数の上で審判に見せるスキルか。通常攻撃・上限の無いもの・残りの読めないものは見せる。"""
    if name == BASIC_ATTACK:
        return True
    current, limit = _uses(skill, "current_uses"), _uses(skill, "max_uses")
    if current is None or limit is None or limit <= 0:
        return True
    return current > 0


def lingering_turns(skill):
    """強化・弱体だけのスキルなら、効果の続く手番数。ダメージなどを含むスキルは 0。"""
    effects = skill.get("effects") if isinstance(skill, dict) else None
    if not isinstance(effects, list) or not effects:
        return 0
    turns = 0
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") not in LINGERING_TYPES:
            return 0
        duration = effect.get("duration")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            duration = DEFAULT_DURATION
        turns = max(turns, duration)
    return turns


def usable_skills(skills, resting=()):
    """審判に見せるスキルの辞書（並びは元のまま）。外すものが無ければ None。

    外すのは、使い切ったものと、`resting`（効果の続いている強化・弱体の名前）に入っているもの。
    通常攻撃は外さない。
    """
    if not isinstance(skills, dict):
        return None
    kept = {name: skill for name, skill in skills.items()
            if usable(name, skill) and (name == BASIC_ATTACK or name not in resting)}
    return kept if len(kept) != len(skills) else None


def refill(skills):
    """残りが上限より少ないスキルを上限へ戻す。戻したスキル名と前の残りを返す。"""
    changed = []
    if not isinstance(skills, dict):
        return changed
    for name, skill in skills.items():
        current, limit = _uses(skill, "current_uses"), _uses(skill, "max_uses")
        if current is None or limit is None or limit <= 0 or current >= limit:
            continue
        skill["current_uses"] = limit
        changed.append((name, current, limit))
    return changed


def chosen_skill(response):
    """審判の戻りから選ばれたスキル名。辞書でも pydantic のモデルでも読む。"""
    if isinstance(response, dict):
        name = response.get("skill")
    else:
        name = getattr(response, "skill", None)
    return name if isinstance(name, str) else None


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag=LOG_TAG + ":")

    # 審判の間だけ絞っている者。{id(持ち主): (持ち主, 元の辞書, 絞った辞書)}
    hidden = {}
    # 効果の続いている強化・弱体。{id(持ち主): {スキル名: 残りの手番数}}。戦闘の開始で空にする
    resting = {}

    def tick(actor):
        """この者の手番が1つ来た。外している強化・弱体の残りを1つ減らし、今外すものを返す。"""
        own = resting.get(id(actor))
        if not own:
            return set()
        now = set(name for name, turns in own.items() if turns > 0)
        for name in list(own):
            own[name] -= 1
            if own[name] <= 0:
                del own[name]
        return now

    @ctx.wrap("scripts.llm.llm_manager_battle:referee_npc", required=False, safe=True)
    def referee_npc(orig, player=None, combat_log=None, actor_name=None, actor=None,
                    *args, **kwargs):
        original = frames.attr(actor, "skills", None)
        kept = None
        try:
            kept = usable_skills(original, tick(actor))
            if kept is not None:
                gone = [name for name in original if name not in kept]
                spent = [name for name in gone if not usable(name, original[name])]
                actor.skills = kept
                hidden[id(actor)] = (actor, original, kept)
                write("{}: hid from the referee: spent={} resting={}".format(
                    actor_name, spent, [name for name in gone if name not in spent]))
        except Exception:
            kept = None
            ctx.log_exc("npc skill uses: cannot narrow the skills")
        try:
            response = orig(player, combat_log, actor_name, actor, *args, **kwargs)
        finally:
            if kept is not None:
                hidden.pop(id(actor), None)
                try:
                    actor.skills = original
                except Exception:
                    ctx.log_exc("npc skill uses: cannot put the skills back")
        try:
            name = chosen_skill(response)
            turns = lingering_turns(original.get(name)) if isinstance(original, dict) and name else 0
            if turns:
                resting.setdefault(id(actor), {})[name] = turns
                write("{}: used {} (buff/debuff); resting it for {} turn(s)".format(actor_name, name, turns))
        except Exception:
            ctx.log_exc("npc skill uses: cannot read the chosen skill")
        return response

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        resting.clear()
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.save_game", required=False)
    def save_game(orig, self, *args, **kwargs):
        # 審判の途中で保存が走っても、セーブには元の辞書を入れる
        entries = list(hidden.values())
        for holder, original, _kept in entries:
            holder.skills = original
        try:
            return orig(self, *args, **kwargs)
        finally:
            for holder, original, kept in entries:
                if id(holder) in hidden:
                    holder.skills = kept

    @ctx.wrap(REST_TARGET, required=False, safe=True)
    def rest(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            for member_id in ui.party_member_ids(app):
                member = ui.character_of(app, member_id)
                changed = refill(frames.attr(member, "skills", None))
                if changed:
                    write("rest: {} {}".format(frames.attr(member, "name", member_id), ", ".join(
                        "{} {:g} -> {:g}".format(name, before, after)
                        for name, before, after in changed)))
        except Exception:
            ctx.log_exc("npc skill uses: cannot refill the party's skills")
        return result

    ctx.log("npc skill uses: spent skills and lingering buffs/debuffs are hidden from referee_npc; "
            "party refills at rest (log -> {})".format(ctx.out_path(LOG_BASENAME)))
