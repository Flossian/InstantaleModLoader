# -*- coding: utf-8 -*-
"""脱獄。服役中の毎年の画面で備え、決行する。仕様は DOC.md「脱獄」、決まりは `jailbreak`。

服役の流れは GAME.md §2.20「逮捕・裁判・服役」（`238_probe_prison` の実機）。
"""
from instantale_modloader import defeat, guards, ui

from . import common, jailbreak


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    area_difficulty, quest_reward, refresh_gold = env.area_difficulty, env.quest_reward, env.refresh_gold

    # -------------------------------------------------- 脱獄
    # 服役中の毎年の画面（ゲームの「服役する」＝ `ImprisonmentPhaseManager`）に備えと決行を足す
    # （本人の決定。DOC.md「脱獄」）。服役の流れは GAME.md §2.20「逮捕・裁判・服役」。
    #   備え: 判定してから、ゲームの服役を「服役する」と同じ引数で1年進める。
    #         露見したら備えを潰し、残り年数と刑期を延ばして進める。
    #   決行: ゲームの衛兵の戦闘（難易度は備えの数だけ下げる。差し替えはローダの `guards`）。
    #         勝つか逃げれば、その場（捕まった場所）の画面へ戻る＝牢の外。
    #         倒れたら死なせず、逃走と同じ終わり方で切り上げ（ローダの `defeat`）、「服役する」を延ばして置き直す。
    # 備えの数は世界×主人公の控え（`jail`）。刑期の残りはゲームのボタンが持つので控えない。
    jail = {"battle": None}
    guards.install(ctx)
    defeat.install(ctx)

    def serve_entry(buttons):
        """選択肢の中のゲームの「服役する」。無ければ None。"""
        for entry in buttons or []:
            if ui.spec_cls_name(entry) == jailbreak.SERVE_SPEC:
                return entry
        return None

    def jail_prep(app):
        with worlds.lock:
            bucket = worlds.load(worlds.playthrough(app))
            value = (bucket.get("jail") or {}).get("prep")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def set_jail_prep(app, value):
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            if value:
                bucket["jail"] = {"prep": int(value)}
            else:
                bucket.pop("jail", None)     # 服役していない間は何も残さない
            worlds.save(playthrough)

    def jail_odds(app):
        """`(備え, 露見の率, {備えの鍵: 実る率}, 看守を手懐ける額)`。画面に出すのも判定もこの値。"""
        player = getattr(app, "player", None)
        prep = jail_prep(app)
        detect = jailbreak.detect_chance(prep, cfg.JAILBREAK_DETECT_PCT, cfg.JAILBREAK_DETECT_PER_PREP)
        chances = {prep_def["key"]: jailbreak.prep_chance(
            ui.ability_score(player, prep_def["ability"]), cfg.JAILBREAK_PREP_BASE_PCT,
            cfg.JAILBREAK_PER_POINT) for prep_def in jailbreak.PREPS if prep_def["ability"]}
        cost = jailbreak.bribe_cost(quest_reward(area_difficulty(app)), cfg.JAILBREAK_BRIBE_PCT)
        return prep, detect, chances, cost

    def insert_jail_buttons(app, buttons):
        """「服役する」の後ろに備えと決行を足す。備えが上限なら決行だけ。"""
        serve = serve_entry(buttons)
        if not cfg.JAILBREAK_ENABLED or serve is None \
                or jailbreak.serve_args(ui.spec_args(serve)) is None:
            return
        prep, detect, chances, cost = jail_odds(app)
        texts = []
        if prep < cfg.JAILBREAK_PREP_MAX:
            for prep_def in jailbreak.PREPS:
                if prep_def["ability"] is None:
                    gold = ui.gold_of(app)
                    if cost <= 0 or not isinstance(gold, int) or gold < cost:
                        continue        # 払えない回は出さない
                    texts.append((prep_def["key"], ui.rewrite_coins(
                        prep_def["label"].format(cost=ui.money(cost)))))
                else:
                    texts.append((prep_def["key"], prep_def["label"].format(
                        chance=chances[prep_def["key"]], detect=detect)))
        texts.append(("break", jailbreak.BREAK_LABEL.format(prep=prep, max=cfg.JAILBREAK_PREP_MAX)))
        entries = [screen.button(text, mark="jail:" + key) for key, text in texts]
        at = buttons.index(serve) + 1
        buttons[at:at] = [entry for entry in entries if entry is not None]

    def serve_year(app, args):
        """ゲームの服役を1年進める（「服役する」を押したのと同じマネージャと引数）。"""
        cls = ui.cls_of(jailbreak.SERVE_SPEC)
        if cls is None:
            write("WARN jailbreak: {} is not available".format(jailbreak.SERVE_SPEC))
            return False
        try:
            phase = cls(app, *args)
        except Exception:
            ctx.log_exc("crime incentive: cannot build the prison year")
            return False
        return screen.start_phase(app, phase, jailbreak.SERVE_TEXT)

    def prepare_year(app, key, args):
        """備えの1年。判定して本文に1行出し、ゲームの服役を1年進める。"""
        prep_def = jailbreak.PREP_BY_KEY[key]
        prep, detect, chances, cost = jail_odds(app)
        next_args = list(args)
        if prep_def["ability"] is None:
            gold = ui.gold_of(app)
            if cost <= 0 or not isinstance(gold, int) or gold < cost \
                    or ui.add_gold(app, -cost) is None:
                write("jailbreak: cannot pay {} for the guard (gold {})".format(cost, gold))
                return False
            new, result = jailbreak.roll_prep(cfg._RNG, prep, cfg.JAILBREAK_PREP_MAX, None, 0)
            line = prep_def["ok"].format(cost=ui.money(cost))
        else:
            chance = chances[key]
            new, result = jailbreak.roll_prep(cfg._RNG, prep, cfg.JAILBREAK_PREP_MAX, chance, detect)
            if result == "detected":
                next_args = jailbreak.extended(args, cfg.JAILBREAK_EXTEND_YEARS)
                line = jailbreak.DETECTED_TEXT.format(years=cfg.JAILBREAK_EXTEND_YEARS)
            else:
                line = prep_def[result]
        if result == "ok":
            line += jailbreak.PREP_TEXT.format(prep=new, max=cfg.JAILBREAK_PREP_MAX)
        set_jail_prep(app, new)
        write("jailbreak: {} -> {} (prep {} -> {}, chance {} detect {} cost {}); serve {}".format(
            key, result, prep, new, chances.get(key), detect,
            cost if prep_def["ability"] is None else "-", next_args[:1] + next_args[3:]))
        refresh_gold(app)
        screen.say(app, ui.rewrite_coins(line))
        return serve_year(app, next_args)

    def break_out(app, args):
        """決行。ゲームの衛兵の戦闘を、備えの数だけ弱くして起こす。"""
        prep = jail_prep(app)
        base = area_difficulty(app)
        difficulty = jailbreak.eased_difficulty(base, prep, cfg.JAILBREAK_EASE_PCT)
        phase = guards.build(app, difficulty)
        if phase is None:
            return False
        jail["battle"] = {"phase": phase, "args": list(args), "prep": prep, "started": False,
                          "recapture": False, "area": ui.area_id_of(ui.current_area(app))}
        screen.say(app, jailbreak.BREAK_TEXT)
        if not screen.start_phase(app, phase, common.GUARD_CHOICE_TEXT):
            jail["battle"] = None
            return False
        write("jailbreak: break out with prep {} (difficulty {} -> {}); sentence {}".format(
            prep, base, difficulty, args[:1] + args[3:]))
        return True

    def press_jail(app, key):
        serve = serve_entry(getattr(app, "buttons", None))
        args = jailbreak.serve_args(ui.spec_args(serve)) if serve is not None else None
        if args is None:
            write("WARN jailbreak: no serve button to follow ({})".format(key))
            return False
        if key == "break":
            return break_out(app, args)
        if key in jailbreak.PREP_BY_KEY:
            return prepare_year(app, key, args)
        return False

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def jail_start_battle(orig, self, *args, **kwargs):
        """決行の戦闘が始まった印を立てる。別の戦闘が始まったら控えを捨てる（強さは `guards` が差し替える）。"""
        battle = jail["battle"]
        if battle is not None:
            if self is battle["phase"]:
                battle["started"] = True
            elif battle["started"]:
                write("jailbreak: another battle started; dropping the jailbreak battle")
                jail["battle"] = None
        return orig(self, *args, **kwargs)

    def spare(app):
        """決行で倒れたら死なせない（ローダの `defeat` が逃走と同じ終わり方で切り上げる）。"""
        battle = jail["battle"]
        if battle is None or not battle["started"] or battle["recapture"]:
            return None
        battle["recapture"] = True
        write("jailbreak: fell in the break out; ending the battle as an escape")
        return {}

    defeat.declare(env.owner, ctx, spare)

    @ctx.wrap("__main__:BattleEndManager.end_phase", required=False, safe=True)
    def jail_battle_end(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        battle = jail["battle"]
        if battle is None or not battle["started"]:
            return result
        jail["battle"] = None
        app = getattr(self, "app", None) or ui.find_app()
        try:
            if battle["recapture"]:
                back_to_cell(app, battle)
            else:
                set_free(app, battle, getattr(self, "end_type", None))
        except Exception:
            ctx.log_exc("crime incentive: cannot settle the break out")
        return result

    def set_free(app, battle, end_type):
        """勝つか逃げた。牢の外（ゲームが戻した場所の画面）。その土地の手配度を下げる。"""
        set_jail_prep(app, 0)
        write("jailbreak: escaped ({!r}) from area {}".format(end_type, battle["area"]))

        def settle():
            lines = [jailbreak.ESCAPED_TEXT]
            entry = ui.area_record(getattr(app, "player", None), battle["area"])
            before = ui.lawfulness_of(entry)
            loss = max(0, int(cfg.JAILBREAK_LOSS))
            if before is not None and loss and ui.set_lawfulness(entry, before - loss):
                town = getattr(ui.world_areas(app).get(battle["area"]), "name", None) or "この街"
                write("jailbreak: lawfulness of {} {} -> {}".format(
                    battle["area"], before, before - loss))
                lines.append(jailbreak.ESCAPED_LAW_TEXT.format(town=town, before=before,
                                                               after=before - loss))
            for line in lines:
                screen.say(app, line)
        screen.when_idle(app, settle, proceed_on_timeout=True, tag="jailbreak free")

    def back_to_cell(app, battle):
        """倒れて取り押さえられた。「服役する」を延ばして置き直す（備えは潰れる）。"""
        set_jail_prep(app, 0)
        args = jailbreak.extended(battle["args"], cfg.JAILBREAK_EXTEND_YEARS)
        write("jailbreak: recaptured; sentence {} -> {}".format(
            battle["args"][:1] + battle["args"][3:], args[:1] + args[3:]))
        spec = screen.make_spec(jailbreak.SERVE_SPEC, args)

        def settle():
            screen.say(app, jailbreak.RECAPTURED_TEXT.format(years=cfg.JAILBREAK_EXTEND_YEARS))
            if spec is not None:
                screen.apply_buttons(app, [{"text": jailbreak.SERVE_TEXT, "spec": spec}],
                                     "jailbreak recaptured")
        screen.when_idle(app, settle, proceed_on_timeout=True, tag="jailbreak recaptured")

    @ctx.wrap("__main__:ImprisonmentStartManager.execute", required=False, safe=True)
    def imprisonment_start(orig, self, *args, **kwargs):
        """新しい刑期。前の服役の備えは持ち越さない。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and jail_prep(app):
                set_jail_prep(app, 0)
        except Exception:
            ctx.log_exc("crime incentive: cannot reset the jailbreak preparation")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ImprisonmentEndManager.execute", required=False, safe=True)
    def imprisonment_end(orig, self, *args, **kwargs):
        """刑期を務め上げた。備えは捨てる。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and jail_prep(app):
                set_jail_prep(app, 0)
        except Exception:
            ctx.log_exc("crime incentive: cannot drop the jailbreak preparation")
        return orig(self, *args, **kwargs)

    def on_refresh(app, buttons):
        """服役の画面なら備えと決行を足す。"""
        if serve_entry(buttons) is not None:
            screen.prune_stale(buttons, jailbreak.LABEL_HEADS)
            if not any(screen.mark_of(entry) for entry in buttons):
                insert_jail_buttons(app, buttons)

    def press(app, action):
        key = action[len("jail:"):]
        write("pressed the jailbreak {!r}".format(key))
        if not press_jail(app, key):
            screen.apply_buttons(app, None, "jailbreak failed")

    env.on_refresh(on_refresh)
    env.on_press("jail:", press)
