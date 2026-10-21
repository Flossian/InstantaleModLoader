# -*- coding: utf-8 -*-
"""衛兵に見つかった場面で、その場で金を握らせる。仕様は DOC.md「衛兵の買収」。

ゲームは手配されている土地で衛兵に見つかると「大人しく捕まる／抵抗する！」だけを並べる
（`ui.guard_encounter`。自由入力の行き先は自由行動 `FreeInputStart` で、そこで買収を書くことはできるが、
判定は LLM 任せで率も見えない）。ここに率の見える「金を握らせる」を足す。

- 成功: 衛兵が見逃す。ゲーム自身の `set_buttons_to_normal` で、居る場所の選択肢に戻す（実機で確かめた）
- 失敗: ゲームの「大人しく捕まる／抵抗する！」に戻す（金を握らせるは出さない）。
  大人しく捕まれば、贈賄を試みたことを裁判に持ち越す（`court`）。
  抵抗すればゲーム自身の衛兵戦で、勝てば外、倒れればゲームオーバーなので裁判は来ない（持ち越さない）
- 額は払う（受け取られても突き返されても）。払った後の画面でゲーム自身の保存を呼ぶ
  （ゲームはこの押下では保存しないので、呼ばないとロードで額が戻り、控えだけが進んだ形が残る）

ボタンはローダの窓口 `choices` の宣言型の口で、組み直しのたびに状態から作る（TECH.md §3.3.14）。
突き返された後の「大人しく捕まる」は、ゲームのボタンを同じ場所で差し替える（`replace`。spec はゲームの
`TrialStartManager` のまま。窓口はゲームのボタンを書き換えず、セーブには元のボタンが残る）。
突き返されたことは世界×主人公の控え（`encounter`）にも持つ。ロードしても控えを見て金を握らせるを出さず、
「大人しく捕まる」を差し替えたまま出す。衛兵の画面を離れたら控えから消す。
"""
from instantale_modloader import choices, ui, wanted

from . import rules

LABEL = "金を握らせる（{cost}G・成功率 {chance}%）"
LABEL_HEAD = "金を握らせる（"
OK_TEXT = "衛兵の手に{cost}ゴールドを握らせた。\n「……今日のところは、何も見なかった」"
FAIL_TEXT = "{cost}ゴールドは突き返された。\n「役人を買おうとは、いい度胸だ。来い」"
SURRENDER_SPEC = "TrialStartManager"


def chance(score, base_pct, per_point_pct, weight, per_weight_pct):
    """見逃してもらえる確率（%）。魅力 15 で `base_pct`。その土地の手配が重いほど下がる。"""
    # 丸めるのは手配の分を引いた後の1回だけ（先に 95% で頭打ちにすると、高い魅力で手配の分を打ち消せない）。
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = rules.ABILITY_PIVOT
    pct = base_pct + (score - rules.ABILITY_PIVOT) * per_point_pct - max(0, weight) * per_weight_pct
    return int(round(max(rules.CHANCE_FLOOR, min(rules.CHANCE_CEILING, pct))))


def surrender_entry(buttons):
    """「大人しく捕まる」のボタン（裁判を始めるマネージャ）。無ければ None。"""
    for entry in buttons or []:
        if ui.spec_cls_name(entry) == SURRENDER_SPEC:
            return entry
    return None


def install(env):
    write, screen, cfg, worlds = env.write, env.screen, env.cfg, env.worlds
    save_soon = ui.saver(env.ctx, write, "encounter")

    def cost_of(app):
        return rules.guide_amount(env.quest_reward(env.area_difficulty(app)), cfg.GUARD_BRIBE_PCT)

    def odds(app):
        player = getattr(app, "player", None)
        weight = wanted.weight_of(env.here_lawfulness(app))
        return chance(ui.ability_score(player, "charisma"), cfg.GUARD_BRIBE_BASE_PCT,
                      cfg.GUARD_BRIBE_PER_POINT, weight, cfg.GUARD_BRIBE_WANTED_PCT)

    #: 突き返された場面か（その場面の間は金を握らせるを出さない）。衛兵の画面を離れたら下ろす。
    #: ロードを挟んでも続くよう、控えにも持つ（`stored_refused`）。
    refused = {"on": False}

    def stored_refused(app):
        with worlds.lock:
            bucket = worlds.load(worlds.playthrough(app))
            return bool((bucket.get("encounter") or {}).get("refused"))

    def set_refused(app, on):
        refused["on"] = bool(on)
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            if on:
                bucket["encounter"] = {"refused": True}
            elif "encounter" in bucket:
                bucket.pop("encounter", None)
            else:
                return
            worlds.save(playthrough)

    def offer(app, view):
        """衛兵に見つかった場面に「金を握らせる」を足す。突き返された後は「大人しく捕まる」を差し替える。"""
        if ui.guard_encounter(view.buttons) is None:
            if refused["on"] or stored_refused(app):
                set_refused(app, False)
            return None
        if not cfg.GUARD_BRIBE_ENABLED:
            return None
        if refused["on"] or stored_refused(app):
            # 突き返された後は、捕まるか抵抗するかだけ。ロードの後も控えから同じ形に組む
            refused["on"] = True
            surrender = surrender_entry(view.buttons)
            if surrender is None:
                return None
            return [choices.Choice(surrender.get("text") or "大人しく捕まる", base=surrender, replace=True,
                                   on_press=lambda app: press_surrender(app, surrender))]
        cost = cost_of(app)
        gold = ui.gold_of(app)
        if cost <= 0 or not isinstance(gold, int) or gold < cost:
            return None             # 払えない回は出さない
        return [choices.Choice(ui.rewrite_coins(LABEL.format(cost=ui.money(cost), chance=odds(app))),
                               on_press=press)]

    def press(app):
        buttons = choices.game_buttons(app)
        surrender = surrender_entry(buttons)
        cost = cost_of(app)
        gold = ui.gold_of(app)
        if surrender is None or cost <= 0 or not isinstance(gold, int) or gold < cost \
                or ui.add_gold(app, -cost) is None:
            write("WARN encounter: cannot bribe (cost {} gold {} surrender {})".format(
                cost, gold, surrender is not None))
            choices.repaint(app)
            return None
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
            save_soon(app, "bribe accepted")
            return None
        screen.say(app, ui.rewrite_coins(FAIL_TEXT.format(cost=ui.money(cost))))
        if ui.guard_encounter(buttons) is None:
            write("WARN encounter: cannot rebuild the encounter choices; arresting")
            arrest(app, surrender)
            return None
        set_refused(app, True)
        choices.repaint(app)            # 組み直すと「大人しく捕まる」の差し替えと「抵抗する！」だけになる
        save_soon(app, "bribe refused")
        return None

    def arrest(app, surrender):
        """贈賄を裁判へ持ち越して連行する（突き返された袖の下と同じく刑が延び、裁判の袖の下は出ない）。"""
        env.carry(app, "bribe", "caught")
        phase = screen.instantiate_spec(app, surrender)
        if phase is None or not screen.start_phase(app, phase, surrender.get("text") or "大人しく捕まる"):
            env.carry(app, "bribe", None)
            write("WARN encounter: cannot start the arrest; leaving the encounter screen")
            choices.repaint(app)

    def press_surrender(app, entry):
        """突き返された後の「大人しく捕まる」（`entry` は差し替えたゲームのボタン）。"""
        write("encounter: surrendered after the bribe was refused")
        set_refused(app, False)
        arrest(app, entry)
        return None

    env.on_offer(offer)
    env.on_labels(lambda app: (LABEL_HEAD,))
