# -*- coding: utf-8 -*-
"""衛兵に見つかった場面で、その場で金を握らせる。仕様は DOC.md「その場の買収」。

ゲームは手配されている土地で衛兵に見つかると「大人しく捕まる／抵抗する！」だけを並べる
（`ui.guard_encounter`。自由入力の行き先は自由行動 `FreeInputStart` で、そこで買収を書くことはできるが、
判定は LLM 任せで率も見えない）。ここに率の見える「金を握らせる」を足す。

- 成功: 衛兵が見逃す。ゲーム自身の `set_buttons_to_normal` で、居る場所の選択肢に戻す（実機で確かめた）
- 失敗: そのまま連行（「大人しく捕まる」と同じマネージャを起こす）。贈賄を試みたことは裁判に持ち越す（`court`）
- 額は払う（受け取られても突き返されても）
"""
from instantale_modloader import ui, wanted

from . import rules

LABEL = "金を握らせる（{cost}G・成功率 {chance}%）"
LABEL_HEAD = "金を握らせる（"
OK_TEXT = "衛兵の手に{cost}ゴールドを握らせた。\n「……今日のところは、何も見なかった」"
FAIL_TEXT = "{cost}ゴールドは突き返された。\n「役人を買おうとは、いい度胸だ。来い」"
SURRENDER_SPEC = "TrialStartManager"
MARK = "encounter:bribe"


def chance(score, base_pct, per_point_pct, weight, per_weight_pct):
    """見逃してもらえる確率（%）。魅力 15 で `base_pct`。その土地の手配が重いほど下がる。"""
    pct = rules.ability_chance(score, base_pct, per_point_pct) - max(0, weight) * per_weight_pct
    return int(round(max(rules.CHANCE_FLOOR, min(rules.CHANCE_CEILING, pct))))


def surrender_entry(buttons):
    """「大人しく捕まる」のボタン（裁判を始めるマネージャ）。無ければ None。"""
    for entry in buttons or []:
        if ui.spec_cls_name(entry) == SURRENDER_SPEC:
            return entry
    return None


def install(env):
    write, screen, cfg = env.write, env.screen, env.cfg

    def cost_of(app):
        return rules.guide_amount(env.quest_reward(env.area_difficulty(app)), cfg.GUARD_BRIBE_PCT)

    def odds(app):
        player = getattr(app, "player", None)
        weight = wanted.weight_of(env.here_lawfulness(app))
        return chance(ui.ability_score(player, "charisma"), cfg.GUARD_BRIBE_BASE_PCT,
                      cfg.GUARD_BRIBE_PER_POINT, weight, cfg.GUARD_BRIBE_WANTED_PCT)

    def on_refresh(app, buttons):
        if not cfg.GUARD_BRIBE_ENABLED or ui.guard_encounter(buttons) is None:
            return
        buttons[:] = [entry for entry in buttons if screen.mark_of(entry) != MARK]
        screen.prune_stale(buttons, (LABEL_HEAD,))
        cost = cost_of(app)
        gold = ui.gold_of(app)
        if cost <= 0 or not isinstance(gold, int) or gold < cost:
            return              # 払えない回は出さない
        entry = screen.button(ui.rewrite_coins(LABEL.format(cost=ui.money(cost), chance=odds(app))),
                              mark=MARK)
        if entry is not None:
            buttons.append(entry)

    def press(app, action):
        buttons = list(getattr(app, "buttons", None) or [])
        surrender = surrender_entry(buttons)
        cost = cost_of(app)
        gold = ui.gold_of(app)
        if surrender is None or cost <= 0 or not isinstance(gold, int) or gold < cost \
                or ui.add_gold(app, -cost) is None:
            write("WARN encounter: cannot bribe (cost {} gold {} surrender {})".format(
                cost, gold, surrender is not None))
            screen.apply_buttons(app, None, "encounter")
            return
        rate = odds(app)
        roll = cfg._RNG.random() * 100
        ok = roll < rate
        write("encounter: bribe {} gold, chance {}% roll {:.0f} -> {}".format(
            cost, rate, roll, "ok" if ok else "refused"))
        env.refresh_gold(app)
        if ok:
            screen.say(app, ui.rewrite_coins(OK_TEXT.format(cost=ui.money(cost))))
            normal = getattr(app, "set_buttons_to_normal", None)
            if callable(normal):
                normal()
            return
        screen.say(app, ui.rewrite_coins(FAIL_TEXT.format(cost=ui.money(cost))))
        # 裁判へ持ち越す（突き返された袖の下と同じく刑が延び、裁判の袖の下は出ない）。
        env.carry_to_trial["bribe"] = "caught"
        phase = screen.instantiate_spec(app, surrender)
        if phase is None or not screen.start_phase(app, phase, surrender.get("text") or "大人しく捕まる"):
            write("WARN encounter: cannot start the arrest; leaving the encounter screen")
            screen.apply_buttons(app, None, "encounter")

    env.on_refresh(on_refresh)
    env.on_press(MARK, press)
