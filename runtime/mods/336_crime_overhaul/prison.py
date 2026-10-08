# -*- coding: utf-8 -*-
"""脱獄。服役中の毎年の画面で備え、決行する。仕様は DOC.md「脱獄」、決まりは `jailbreak`。

服役の流れは GAME.md §2.20「逮捕・裁判・服役」（`238_probe_prison` の実機）。
"""
from instantale_modloader import combat, equipment, guards, items, ui

from . import common, jailbreak


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    area_difficulty, quest_reward, refresh_gold = env.area_difficulty, env.quest_reward, env.refresh_gold

    # -------------------------------------------------- 脱獄
    # 服役中の毎年の画面（ゲームの「服役する」＝ `ImprisonmentPhaseManager`）に「脱獄を試みる」を1つ足し、
    # 押すと備えと決行の一覧を出す（DOC.md「脱獄」。2026-10-05 にまとめた）。
    # 服役の流れは GAME.md §2.20「逮捕・裁判・服役」。
    #   備え: 判定してから、ゲームの服役を「服役する」と同じ引数で1年進める。
    #         露見したら備えを潰し、残り年数と刑期を延ばして進める。
    #   決行: ゲームの衛兵の戦闘（難易度は備えの数だけ下げる。差し替えはローダの `guards`）。
    #         勝つか逃げれば、その場（捕まった場所）の画面へ戻る＝牢の外。
    #         倒れたらゲーム自身のゲームオーバー（2026-10-05。前は死なせず牢へ戻していた）。
    #         戦闘のあいだは即席の武器・防具で戦う（2026-10-08。ローダの窓口 `equipment` の貸し）。
    # 備えの数は世界×主人公の控え（`jail`）。刑期の残りはゲームのボタンが持つので控えない。
    #: `battle` は決行の戦闘、`serve` は一覧を開いたときの「服役する」のボタン（引数を持つ）、
    #: `year` は一覧を開く前の年ごとの画面の選択肢（「やめる」で戻す）。
    jail = {"battle": None, "serve": None, "year": None}
    guards.install(ctx)
    equipment.install(ctx)

    def worn_value(app, player, key):
        """今の装備の値。装備欄の MOD が居ればその答え（窓口 `combat`）、居なければ本体の `equipments` の1品。"""
        value = (combat.attack if key == "weapon" else combat.defense)(app, player)
        if value is not None:
            return value
        ref = (getattr(player, "equipments", None) or {}).get(key)
        if isinstance(ref, (str, int)):
            ref = (items.inventory_of(player) or {}).get(str(ref))
        attrs = getattr(ref, "attributes", None) if ref is not None else None
        stat = next(spec["stat"] for spec in jailbreak.IMPROVISED if spec["key"] == key)
        try:
            return float((attrs if isinstance(attrs, dict) else {}).get(stat, 0) or 0)
        except (TypeError, ValueError):
            return None

    def lend_improvised(app):
        """押収された装備の代わりに、即席の武器・防具を持たせる。持たせた品の名前の一覧を返す。"""
        player = getattr(app, "player", None)
        if not cfg.JAILBREAK_IMPROVISED or player is None:
            return []
        loans, names = {}, {}
        for spec in jailbreak.IMPROVISED:
            worn = worn_value(app, player, spec["key"])
            value = jailbreak.improvised_value(worn, cfg.JAILBREAK_IMPROVISED_PCT)
            if value is None:
                continue                    # 持っていない種類は持たせない
            item = items.make_loose(app, player, jailbreak.improvised_data(spec, value),
                                    jailbreak.IMPROVISED_KEY.format(spec["key"]), write=write)
            if item is None:
                write("WARN jailbreak: cannot make the improvised {}".format(spec["key"]))
                continue
            loans[spec["key"]] = item
            names[spec["key"]] = spec["name"]
            write("jailbreak: improvised {} {} (worn {:g} x {}%)".format(
                spec["key"], value, worn, cfg.JAILBREAK_IMPROVISED_PCT))
        lent = equipment.lend(app, player, loans, owner=env.owner, write=write) if loans else []
        return [names[key] for key in lent]

    def take_back_improvised(app):
        """即席の品を引き上げ、元の装備に戻す。戻したら真。"""
        player = getattr(app, "player", None) if app is not None else None
        if player is None or equipment.lender(app, player) != env.owner:
            return False
        return bool(equipment.end_loan(app, player, write=write))

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

    def breaking(app):
        """決行の戦闘の控え `{"area": 捕まった場所, "prep": 備え}`。決行していなければ None。

        戦闘の最中のセーブを読むと、ゲームは戦闘の続きに戻ることがある（GAME.md §2.10）。
        メモリの `jail["battle"]` はロードで消えるので、牢を出た後の始末と即席の品のために控えに持つ。
        """
        with worlds.lock:
            value = worlds.load(worlds.playthrough(app)).get("jail_break")
        return value if isinstance(value, dict) else None

    def set_breaking(app, value):
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            if value:
                bucket["jail_break"] = dict(value)
            elif bucket.pop("jail_break", None) is None:
                return
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
        """「服役する」の後ろに「脱獄を試みる」を1つ足す。"""
        serve = serve_entry(buttons)
        if not cfg.JAILBREAK_ENABLED or serve is None \
                or jailbreak.serve_args(ui.spec_args(serve)) is None:
            return
        entry = screen.button(jailbreak.MENU_LABEL.format(prep=jail_prep(app), max=cfg.JAILBREAK_PREP_MAX),
                              mark="jail:menu")
        if entry is not None:
            buttons.insert(buttons.index(serve) + 1, entry)

    def menu_entries(app):
        """「脱獄を試みる」の一覧。備え（上限なら出さない）・決行・やめる。"""
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
        texts.append(("back", jailbreak.BACK_LABEL))
        entries = [screen.button(text, mark="jail:" + key) for key, text in texts]
        return [entry for entry in entries if entry is not None]

    def open_menu(app):
        """一覧を出す。「服役する」の引数と年ごとの画面の選択肢を控える（一覧には「服役する」が無い）。"""
        buttons = list(getattr(app, "buttons", None) or [])
        serve = serve_entry(buttons)
        if serve is None:
            write("WARN jailbreak: no serve button to open the menu from")
            return False
        jail.update(serve=dict(serve), year=[dict(entry) if isinstance(entry, dict) else entry
                                             for entry in buttons])
        screen.apply_buttons(app, menu_entries(app), "jailbreak menu")
        return True

    def close_menu(app):
        """年ごとの画面に戻す。"""
        year = jail.get("year")
        if not year:
            write("WARN jailbreak: no year screen to go back to")
            return False
        screen.apply_buttons(app, year, "jailbreak back")
        return True

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
                          "area": ui.area_id_of(ui.current_area(app))}
        improvised = lend_improvised(app)
        if improvised:
            screen.say(app, jailbreak.IMPROVISED_TEXT.format(items="と".join(improvised)))
        screen.say(app, jailbreak.BREAK_TEXT)
        env.prison_event("break", app)          # 同房の囚人の加勢（`cellmate`）
        if not screen.start_phase(app, phase, common.GUARD_CHOICE_TEXT):
            jail["battle"] = None
            take_back_improvised(app)
            return False
        set_breaking(app, {"area": jail["battle"]["area"], "prep": prep})
        write("jailbreak: break out with prep {} (difficulty {} -> {}); sentence {}".format(
            prep, base, difficulty, args[:1] + args[3:]))
        return True

    @ctx.wrap("__main__:GameOverManager.__init__", required=False, safe=True)
    def jail_game_over(orig, self, *args, **kwargs):
        """ゲームオーバー（決行で倒れた・処刑・寿命）。決行と備えの控えを片付ける。

        倒れた戦闘は `BattleEndManager.end_phase` を通らない。ゲームはこの後セーブを消すので、
        控えが残るとセーブの無い周回の state にだけ残る（実機。§3.91）。
        即席の品は返さない（暗転した画面で装備の効果音が鳴る）。貸しは次のロード・新規開始でローダが捨てる。
        """
        result = orig(self, *args, **kwargs)
        jail["battle"] = None
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and (breaking(app) is not None or jail_prep(app)):
                set_breaking(app, None)
                set_jail_prep(app, 0)
                write("jailbreak: game over; dropped the break-out marks")
        except Exception:
            ctx.log_exc("crime incentive: cannot drop the jailbreak marks at the game over")
        return result

    @ctx.wrap("__main__:InstantaleApp.load_game_new", required=False, safe=True)
    def jail_after_load(orig, self, *args, **kwargs):
        """ロード。決行の戦闘の最中のセーブで戦闘の続きに戻ったら、即席の品を持たせ直し、始末を待つ。"""
        jail["battle"] = None                   # 前の周回の決行は持ち越さない
        result = orig(self, *args, **kwargs)
        try:
            resume_break(self)
        except Exception:
            ctx.log_exc("crime incentive: cannot resume the break out after the load")
        return result

    def resume_break(app):
        saved = breaking(app)
        if saved is None:
            return
        if not ui.battle_on_screen(app):
            # 戦闘の続きに戻らなかった（ゲームが場所の画面を出した）。決行は無かったことになる
            set_breaking(app, None)
            write("jailbreak: the load did not return to the break-out battle; dropped the mark")
            return
        jail["battle"] = {"phase": None, "args": [], "prep": saved.get("prep", 0), "started": True,
                          "area": saved.get("area")}
        improvised = lend_improvised(app)
        write("jailbreak: back in the break-out battle after a load (improvised: {})".format(
            ", ".join(improvised) or "none"))

    def press_jail(app, key):
        if key == "menu":
            return open_menu(app)
        if key == "back":
            return close_menu(app)
        # 一覧には「服役する」が無いので、一覧を開いたときに控えたボタンから引数を読む。
        serve = serve_entry(getattr(app, "buttons", None)) or jail.get("serve")
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
                app = getattr(self, "app", None) or ui.find_app()
                if app is not None:
                    set_breaking(app, None)
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:BattleEndManager.end_phase", required=False, safe=True)
    def jail_battle_end(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        # 即席の品はどの戦闘の終わりでも引き上げる（注入し直しで決行の控えを失っていても、貸しは残っている）
        recovered = False
        try:
            recovered = take_back_improvised(app)
        except Exception:
            ctx.log_exc("crime incentive: cannot take back the improvised equipment")
        battle = jail["battle"]
        if battle is None or not battle["started"]:
            return result
        jail["battle"] = None
        try:
            set_free(app, battle, getattr(self, "end_type", None), recovered)
        except Exception:
            ctx.log_exc("crime incentive: cannot settle the break out")
        return result

    def set_free(app, battle, end_type, recovered=False):
        """勝つか逃げた。牢の外（ゲームが戻した場所の画面）。その土地の手配度を下げる。"""
        set_jail_prep(app, 0)
        set_breaking(app, None)
        start_ban(app)
        write("jailbreak: escaped ({!r}) from area {}".format(end_type, battle["area"]))

        def settle():
            lines = [jailbreak.ESCAPED_TEXT]
            if recovered:
                lines.append(jailbreak.RECOVERED_TEXT)
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
            # 服役中も居場所は動かないので、ゲームは背景を描き直さず牢の絵が残る（実機）。
            # 居る場所の絵はゲーム自身の背景替えに任せる（絵は描かない。GAME.md §2.6・§2.31）。
            redraw = getattr(app, "change_background_image_to_current_location", None)
            if callable(redraw):
                try:
                    redraw()
                except Exception:
                    ctx.log_exc("crime incentive: the game's background change failed")
        screen.when_idle(app, settle, proceed_on_timeout=True, tag="jailbreak free")
        # 知らせるのは自分の本文を予約した後（受ける側の本文が「牢獄の外へ出た」より先に出た。実機）。
        env.prison_event("exit", app, how="escape")

    @ctx.wrap("__main__:ImprisonmentStartManager.execute", required=False, safe=True)
    def imprisonment_start(orig, self, *args, **kwargs):
        """新しい刑期。前の服役の備えは持ち越さない。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and jail_prep(app):
                set_jail_prep(app, 0)
            if app is not None:
                set_breaking(app, None)
        except Exception:
            ctx.log_exc("crime incentive: cannot reset the jailbreak preparation")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ImprisonmentEndManager.execute", required=False, safe=True)
    def imprisonment_end(orig, self, *args, **kwargs):
        """刑期を務め上げた。備えは捨て、裁判の司法取引の締め出しを数え始める。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and jail_prep(app):
                set_jail_prep(app, 0)
            if app is not None:
                set_breaking(app, None)
                start_ban(app)
        except Exception:
            ctx.log_exc("crime incentive: cannot drop the jailbreak preparation")
        result = orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        if app is not None:
            env.prison_event("exit", app, how="release")
        return result

    def start_ban(app):
        """牢を出た。司法取引で裏の事務所を売っていれば、ここから締め出しの日数を数える（`court` / `office`）。"""
        day = ui.game_day(app)
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            days = bucket.get("underworld_ban_after")
            if not isinstance(days, int) or day is None:
                return      # 日付が読めないときは控えを残す（次に牢を出たときに数え直す）
            bucket.pop("underworld_ban_after", None)
            bucket["underworld_ban"] = int(day) + days
            worlds.save(playthrough)
        write("court: shut out of the underworld until day {} ({} day(s) from leaving prison)".format(
            int(day) + days, days))

    def on_refresh(app, buttons):
        """服役の画面なら「脱獄を試みる」を足す。"""
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
