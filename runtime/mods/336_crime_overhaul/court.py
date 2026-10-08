# -*- coding: utf-8 -*-
"""裁判。釈明の入れ方を知らせ、弁護人・司法取引・判事の買収・情状を足す。仕様は DOC.md「裁判の改修」、決まりは `trial`。

素のゲームの裁判は、検察の求刑の後に「わかりました」「濡れ衣だ」の2つのボタンが出るだけに見える。
実際は、その画面で入力欄に書いて送った言葉がそのまま釈明として判事の頼みに渡る
（自由入力の行き先が裁判の段 `TrialPhaseManager` になっている。押したボタンの文言も同じ欄に渡る）。
判事の頼み文は、反省の色があれば情状酌量し、ふざけていれば重くするよう指示している（GAME.md §2.20）。

- 釈明: 画面にそのことが出ないので、裁判の画面に1度だけ知らせる
- 弁護人・司法取引・判事の買収: 裁判の画面に自前のボタンを足す。押しても判決へは進まない（同じ画面に戻る）。
  効き目は判事の頼みへの一文と、判決のボタンの直し（`trial.adjust`）の両方で確かにする
- 情状: 検察と判事の頼みに手配の重さとこの土地での活躍を足し、壊れた「人生ログの全体象」をゲーム自身の
  `context_manager.get_life_log_text` の文に置き換える

いまの裁判の効き目は、世界×主人公の控え（`court`）にも持つ。ゲームは裁判の段ごとに保存するので、
メモリだけだと、払った後や裁判が始まった後にロードすると効き目が消え、ボタンがまた出た。
介入を押した後と判決を直した後は、ゲーム自身の保存を呼ぶ（払った額・効き目・直した判決を同じ保存に入れる）。
"""
import sys

from instantale_modloader import llm, ui, wanted

from . import trial

HINT_TEXT = "入力欄に書いて送った言葉が、そのまま判事への釈明になる。\nボタンを押せば、その文言が釈明になる。"
SEND_TARGET = "scripts.llm.llm_manager:send_request"
LIFE_LOG_MODULE = "scripts.llm.context_manager"
#: 置き換える人生の記録の上限（字）。頼みが長くなりすぎないように。
LIFE_LOG_LIMIT = 1500
MARK_HEAD = "court:"


def trial_args(buttons):
    """裁判の画面なら、そのボタンの引数（求刑・罪状・出来事）。違えば None。"""
    for entry in buttons or []:
        if ui.spec_cls_name(entry) == trial.TRIAL_SPEC:
            return ui.spec_args(entry) or []
    return None


def life_log_text(app, character):
    """ゲーム自身の作り方の人生の記録を、頼みに載せる文にする。読めなければ空。"""
    module = sys.modules.get(LIFE_LOG_MODULE)
    build = getattr(module, "get_life_log_text", None)
    try:
        value = build(app, character) if callable(build) else None
    except Exception:
        value = None
    if not value:
        return ""
    text = str(value)
    return text if len(text) <= LIFE_LOG_LIMIT else text[:LIFE_LOG_LIMIT] + "…"


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    save_soon = ui.saver(ctx, write, "court")
    #: いまの裁判。`key` は求刑ごとに変わる（同じ裁判の画面が組み直されても同じ）。控えの `court` と同じ中身。
    #: `acquit` は無罪の道（None: 閉じている / "open": 判事に無罪の一文を渡した / "done": 無罪放免を出した）。
    court = {"key": None, "args": None, "effects": {}, "told": False, "applied": False, "acquit": None,
             "playthrough": None}

    def load_court(app):
        """ロードでメモリが空になっていたら、控えから戻す。周回が替わっていたら、その周回の控えを読み直す。"""
        playthrough = worlds.playthrough(app)
        if court["key"] is not None and court["playthrough"] == playthrough:
            return
        court.update(key=None, args=None, effects={}, told=False, applied=False, acquit=None,
                     playthrough=playthrough)
        with worlds.lock:
            stored = worlds.load(playthrough).get("court")
        if isinstance(stored, dict) and stored.get("key") is not None:
            court.update(key=stored.get("key"), args=list(stored.get("args") or []),
                         effects=dict(stored.get("effects") or {}),
                         told=bool(stored.get("told")), applied=bool(stored.get("applied")),
                         acquit=stored.get("acquit"))
            write("court: restored the trial from the record (effects {})".format(court["effects"]))

    def store_court(app):
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            bucket["court"] = {"key": court["key"], "args": court["args"], "effects": court["effects"],
                               "told": court["told"], "applied": court["applied"], "acquit": court["acquit"]}
            worlds.save(playthrough)

    def new_trial(app, key, args):
        effects = {"lawyer": False, "plea": False, "bribe": None}
        # 捕まる前の出来事（衛兵の買収が突き返された）を、この裁判へ持ち越す（`encounter`）。
        effects.update(env.take_carry(app))
        court.update(key=key, args=list(args), effects=effects, told=False, applied=False, acquit=None,
                     playthrough=worlds.playthrough(app))
        store_court(app)

    def fee_of(app, pct):
        return trial.fee(env.quest_reward(env.area_difficulty(app)), pct)

    def can_pay(app, cost):
        gold = ui.gold_of(app)
        return cost > 0 and isinstance(gold, int) and gold >= cost

    def plea_open(app):
        """司法取引を持ちかけられるか（裏の仕事を1つでも片付けたことがある）。"""
        with worlds.lock:
            bucket = worlds.load(worlds.playthrough(app))
            done = bucket.get("underworld_done") or 0
        return isinstance(done, int) and done > 0

    def bribe_odds(app):
        player = getattr(app, "player", None)
        return trial.bribe_chance(ui.ability_score(player, "charisma"),
                                  cfg.TRIAL_BRIBE_BASE_PCT, cfg.TRIAL_BRIBE_PER_POINT)

    # ---------------------------------------------------- 裁判の画面
    def insert_buttons(app, buttons):
        effects = court["effects"]
        texts = []
        if cfg.TRIAL_LAWYER_ENABLED and not effects["lawyer"]:
            cost = fee_of(app, cfg.TRIAL_LAWYER_PCT)
            if can_pay(app, cost):
                texts.append(("lawyer", ui.rewrite_coins(
                    trial.LAWYER_LABEL.format(cost=ui.money(cost)))))
        if cfg.TRIAL_PLEA_ENABLED and not effects["plea"] and plea_open(app):
            texts.append(("plea", trial.PLEA_LABEL))
        if cfg.TRIAL_BRIBE_ENABLED and effects["bribe"] is None:
            cost = fee_of(app, cfg.TRIAL_BRIBE_PCT)
            if can_pay(app, cost):
                texts.append(("bribe", ui.rewrite_coins(trial.BRIBE_LABEL.format(
                    cost=ui.money(cost), chance=bribe_odds(app)))))
        entries = [screen.button(text, mark=MARK_HEAD + key) for key, text in texts]
        buttons.extend(entry for entry in entries if entry is not None)

    def on_trial_screen(app, buttons, args):
        load_court(app)
        key = repr(args)[:400]
        if key != court["key"]:
            new_trial(app, key, args)
        if cfg.TRIAL_HINT and not court["told"]:
            court["told"] = True
            store_court(app)
            write("court: told how to plead ({})".format(
                (args[0] or {}).get("sentencing_request") if args and isinstance(args[0], dict)
                else None))
            screen.schedule(lambda: screen.say(app, HINT_TEXT))
        # 自分のボタンは一度外して、いまの状態で並べ直す（使った介入は消える）。
        buttons[:] = [entry for entry in buttons
                      if not str(screen.mark_of(entry) or "").startswith(MARK_HEAD)]
        screen.prune_stale(buttons, trial.LABEL_HEADS)
        insert_buttons(app, buttons)

    # ---------------------------------------------------- 判決の画面
    def on_verdict_screen(app, buttons, verdict):
        load_court(app)
        effects = court["effects"]
        kind, years = verdict
        if not court["applied"] and court["acquit"] == "open" and trial.acquitted(kind, years):
            acquit_screen(app, buttons)
            return
        if court["applied"] or court["args"] is None or not any(effects.values()):
            return
        court["applied"] = True
        store_court(app)
        sought, charges, details = (court["args"] + [None, None, None])[:3]
        # 死刑の求刑は年数を持たないので、判決が懲役でも `TRIAL_DEATH_YEARS` を基に数える（DOC.md「裁判の改修」）。
        requested = trial.requested_years(sought)
        if requested is None and trial.demanded_death(sought):
            requested = int(cfg.TRIAL_DEATH_YEARS)
        new_kind, new_years, reasons = trial.adjust(
            kind, years, requested, effects,
            {"lawyer": cfg.TRIAL_LAWYER_CUT_PCT, "plea": cfg.TRIAL_PLEA_CUT_PCT,
             "bribe": cfg.TRIAL_BRIBE_CUT_PCT},
            cfg.TRIAL_DEATH_YEARS, cfg.TRIAL_BRIBE_PENALTY_YEARS)
        write("court: verdict {} -> {} (effects {}; requested {})".format(
            trial.sentence_text(kind, years), trial.sentence_text(new_kind, new_years),
            effects, requested))
        if not reasons:
            return
        if new_kind == "imprisonment":
            entries = []
            for text in trial.PRISON_TEXTS:
                spec = screen.make_spec(trial.PRISON_SPEC, [new_years, charges, details])
                if spec is None:
                    write("WARN court: cannot build the prison verdict; leaving the game's")
                    return
                entries.append({"text": text, "spec": spec})
            buttons[:] = entries
        line = trial.verdict_line(kind, years, new_kind, new_years, reasons,
                                  cfg.TRIAL_BRIBE_PENALTY_YEARS)
        screen.schedule(lambda: screen.say(app, line))
        save_soon(app, "verdict adjusted")      # 直した判決のボタンを保存に入れる（控えの「直した」と揃える）

    def acquit_screen(app, buttons):
        """判事が懲役0年（無罪）を返した。判決のボタンを「無罪放免」1つに差し替える。"""
        entry = screen.button(trial.ACQUIT_LABEL, mark=MARK_HEAD + "acquit")
        if entry is None:
            write("WARN court: cannot build the acquittal button; leaving the game's")
            return
        buttons[:] = [entry]
        court["applied"] = True
        court["acquit"] = "done"
        store_court(app)
        write("court: acquitted (the judge gave 0 years)")
        save_soon(app, "acquitted")

    def release(app):
        """無罪放免。この土地の手配度を平常へ戻し（同じ罪は二度と問われない）、居る場所の画面へ戻す。"""
        player = getattr(app, "player", None)
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        entry = ui.area_record(player, area_id) if area_id else None
        before = ui.lawfulness_of(entry)
        after = before
        if before is not None and before < trial.NORMAL_LAWFULNESS \
                and ui.set_lawfulness(entry, trial.NORMAL_LAWFULNESS):
            after = trial.NORMAL_LAWFULNESS
        write("court: released; area {} lawfulness {} -> {}".format(area_id, before, after))
        court["acquit"] = None
        store_court(app)
        screen.say(app, trial.ACQUIT_TEXT.format(area=env.area_label(app, area_id), before=before, after=after))
        redraw = getattr(app, "change_background_image_to_current_location", None)
        normal = getattr(app, "set_buttons_to_normal", None)
        for step, name in ((redraw, "background"), (normal, "buttons")):
            if callable(step):
                try:
                    step()
                except Exception:
                    ctx.log_exc("crime incentive: the game's {} reset failed after the acquittal".format(name))
        save_soon(app, "released")

    def on_refresh(app, buttons):
        args = trial_args(buttons)
        if args is not None:
            on_trial_screen(app, buttons, args)
            return
        verdict = trial.verdict_of(buttons, ui.spec_cls_name, ui.spec_args)
        if verdict is not None:
            on_verdict_screen(app, buttons, verdict)

    # ---------------------------------------------------- 介入を押した
    def press(app, action):
        load_court(app)
        key = action[len(MARK_HEAD):]
        if key == "acquit":
            release(app)
            return
        effects = court["effects"]
        line = None
        if key == "lawyer" and not effects.get("lawyer"):
            cost = fee_of(app, cfg.TRIAL_LAWYER_PCT)
            if can_pay(app, cost) and ui.add_gold(app, -cost) is not None:
                effects["lawyer"] = True
                line = trial.LAWYER_TEXT.format(cost=ui.money(cost))
        elif key == "plea" and not effects.get("plea") and plea_open(app):
            effects["plea"] = True
            # 締め出しは牢を出てから数える（刑期は年単位なので、売った日から数えると服役の間に明ける）。
            # 日数は釈放・脱獄のときに `prison` が締め出しの期限に直す。
            playthrough = worlds.playthrough(app)
            with worlds.lock:
                bucket = worlds.load(playthrough)
                bucket["underworld_ban_after"] = max(0, int(cfg.TRIAL_PLEA_BAN_DAYS))
                worlds.save(playthrough)
            line = trial.PLEA_TEXT
        elif key == "bribe" and effects.get("bribe") is None:
            cost = fee_of(app, cfg.TRIAL_BRIBE_PCT)
            if can_pay(app, cost) and ui.add_gold(app, -cost) is not None:
                chance = bribe_odds(app)
                ok = cfg._RNG.random() * 100 < chance
                effects["bribe"] = "ok" if ok else "caught"
                line = (trial.BRIBE_OK_TEXT if ok else trial.BRIBE_CAUGHT_TEXT).format(
                    cost=ui.money(cost))
                write("court: bribe {} (chance {}%)".format(effects["bribe"], chance))
        write("court: pressed {} -> effects {}".format(key, effects))
        if line:
            store_court(app)
            env.refresh_gold(app)
            screen.say(app, ui.rewrite_coins(line))
        screen.apply_buttons(app, None, "court")
        if line:
            save_soon(app, "court " + key)

    # ---------------------------------------------------- 検察と判事の頼み
    def rewrite_message(message, app, judge):
        """頼みの写しに情状と介入を足し、人生の記録を直す。触らなければ None。"""
        if not isinstance(message, list):
            return None
        player = getattr(app, "player", None) if app is not None else None
        copied, changed = list(message), False
        for index in range(len(copied) - 1, -1, -1):
            turn = copied[index]
            if not (isinstance(turn, dict) and turn.get("role") == "user"
                    and isinstance(turn.get("content"), str)):
                continue
            content = turn["content"]
            if cfg.TRIAL_CONTEXT:
                fixed, did = trial.fix_life_log(content, life_log_text(app, player))
                if did:
                    content, changed = fixed, True
                    write("court: fixed the life log in the {} request".format(
                        "judge's" if judge else "prosecutor's"))
                area_id = ui.area_id_of(ui.current_area(app)) if app is not None else ""
                record = ui.area_record(player, area_id) or {}
                values = ui.lawfulness_by_area(player)
                here = wanted.weight_of(values.get(str(area_id)))
                content = content.rstrip() + "\n\n" + trial.circumstances(
                    here, wanted.total_of(values),
                    record.get("achievements") if isinstance(record, dict) else None)
                changed = True
            if judge:
                if app is not None:
                    load_court(app)         # 別の周回の介入を判事に渡さない
                notes = trial.judge_notes(court["effects"])
                if cfg.TRIAL_ACQUITTAL_ENABLED and app is not None:
                    sought = (court["args"] or [None])[0]
                    is_open, why = trial.acquittal_open(trial.plea_of(content), sought, court["effects"],
                                                        cfg.TRIAL_ACQUITTAL_MIN_CHARS)
                    write("court: acquittal {} ({})".format("open" if is_open else "closed", why))
                    if is_open:
                        notes = "\n".join(note for note in (notes, trial.NOTE_ACQUIT) if note)
                        court["acquit"] = "open"
                        store_court(app)
                if notes:
                    content = content.rstrip() + "\n\n" + notes
                    changed = True
            copied[index] = dict(turn, content=content)
            break
        return copied if changed else None

    def install_send(target):
        @ctx.wrap(target, required=False)
        def send_request(orig, *args, **kwargs):
            message = args[1] if len(args) > 1 else kwargs.get("message")
            blob = str(message) if isinstance(message, list) else ""
            judge = trial.JUDGE_MARK in blob
            if not judge and trial.PROSECUTOR_MARK not in blob:
                return orig(*args, **kwargs)
            try:
                rewritten = rewrite_message(message, ui.find_app(), judge)
            except Exception:
                ctx.log_exc("crime incentive: cannot add to the trial request")
                rewritten = None
            if rewritten is None:
                return orig(*args, **kwargs)
            if len(args) > 1:
                args = (args[0], rewritten) + tuple(args[2:])
            else:
                kwargs = dict(kwargs, message=rewritten)
            return orig(*args, **kwargs)

    llm.watch_aliases(ctx, [SEND_TARGET], install_send, label="crime incentive court")
    env.on_refresh(on_refresh)
    env.on_press(MARK_HEAD, press)
