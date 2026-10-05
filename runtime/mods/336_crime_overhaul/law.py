# -*- coding: utf-8 -*-
"""手配への反応。店が委縮して値を下げる・時効・追手の前金を奪う。仕様は DOC.md の同じ名前の節。

##### 店が委縮して値を下げる（手配中の土地で値段が動く）

- その土地で手配されていると、店の買値が下がり、売値が上がる。率は手配の重さ × 1点あたりの率（上限あり）
- 値段はローダの関所（`prices.adjust`）へ一時の段として置く。保存の直前には外れる（セーブへ焼き付けない）

##### 時効（離れている土地の手配が戻る）

- 離れている土地だけ、離れていた日数 3ヵ月ごとに手配度が 10 戻る。戻るのは平常の 10 まで
- ただし全ての土地の手配の重さの合計が全域手配の線以上の間は、
  合計がその線に届いたところで止める（追手は続く。そこから先は役場の罰金）。
  線は追手を出す MOD（`316_`）がローダの窓口 `wanted` に置く。置かれていなければ全域手配は無いので止めない
- 新しい罪は、戻った後の値にそのまま足される（数え直さない）
- 日数は `elapse_days` の前後の暦の差。エリア移動の日数は出発地からも目的地からも離れていた日数として数える

##### 追手の前金を奪う（追手を倒すと前金が入る）

- 追手（`316_`）に勝つと、追手の難易度での依頼1件の報酬 × 割合（既定 30%）の前金が入る。
  手配が重いほど追手は強く（難易度 20〜75）、懐も厚い
- 追手の戦闘が終わったことは `316_` がローダの窓口（`wanted.hunt_ended`）で知らせる。逃げた・負けた回は何も無い
"""
import threading

from instantale_modloader import prices, ui, wanted

from . import rules

SHOP_TARGET = "__main__:ShoppingStartManagerRemake.execute"
ELAPSE_TARGET = "__main__:InstantaleApp.elapse_days"
MOVE_TARGET = "__main__:AreaMoveManager.execute"
#: ゲームの1ヵ月（`elapse_days(months * 30)`。GAME.md §2.17）。
DAYS_PER_MONTH = 30
SHOP_NOTICE_TEXT = "手配書の顔に気づいた店主は、怯えたように値を改めた。（買値 -{pct}%{sell}）"
SHOP_NOTICE_SELL = "・売値 +{pct}%"
STATUTE_TEXT = "{area}では、騒ぎのほとぼりが冷めつつある。（手配度 {before} → {after}）"
BOUNTY_TEXT = "倒した追手の懐から、賞金の前金{amount}ゴールドを抜き取った。"
#: ゲームの `BattleEndManager(app, end_type)` で勝ったときの `end_type`（GAME.md §2.10）。
WON_END_TYPE = "won"


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    owner = env.owner
    here_lawfulness, quest_reward = env.here_lawfulness, env.quest_reward
    refresh_gold, area_label = env.refresh_gold, env.area_label
    lock = threading.Lock()

    # -------------------------------------------------- 店が委縮して値を下げる
    def intimidation(item, key, price):
        """手配中の土地なら率を掛ける。触らないなら None。"""
        if not cfg.INTIMIDATION_ENABLED:
            return None
        app = ui.find_app()
        if app is None:
            return None
        rate = rules.intimidation_rate(here_lawfulness(app), cfg.INTIMIDATION_PER_POINT,
                                       cfg.INTIMIDATION_CAP)
        return rules.intimidated_price(key, price, rate, cfg.INTIMIDATION_SELL)

    def price_layer(item, key, price):
        """関所の段（336 の1枚）。「店が委縮して値を下げる」の後に、他のファイルの段（`env.price_layers`）を順に通す。触らないなら None。"""
        changed = None
        for layer in [intimidation] + list(env.price_layers):
            try:
                got = layer(item, key, price)
            except Exception:
                ctx.log_exc("crime incentive: a price layer failed")
                continue
            if got is not None:
                price = changed = got
        return changed

    prices.install(ctx, write)
    prices.adjust(owner, price_layer, temporary=True, write=write)

    @ctx.wrap(SHOP_TARGET, required=False, safe=True)
    def shop_start(orig, self, *args, **kwargs):
        """店に入ったときに委縮の度合いを1行知らせ、率をログに残す。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if cfg.INTIMIDATION_ENABLED and app is not None:
                lawfulness = here_lawfulness(app)
                rate = rules.intimidation_rate(lawfulness, cfg.INTIMIDATION_PER_POINT,
                                               cfg.INTIMIDATION_CAP)
                if rate > 0:
                    pct = int(round(rate * 100))
                    write("shop: lawfulness {} -> buy -{}%{}".format(
                        lawfulness, pct, " sell +{}%".format(pct) if cfg.INTIMIDATION_SELL else ""))
                    if cfg.INTIMIDATION_NOTICE:
                        text = SHOP_NOTICE_TEXT.format(
                            pct=pct, sell=SHOP_NOTICE_SELL.format(pct=pct)
                            if cfg.INTIMIDATION_SELL else "")
                        screen.schedule(lambda: screen.say(app, text))
        except Exception:
            ctx.log_exc("crime incentive: cannot read the shop's fear")
        return orig(self, *args, **kwargs)

    # -------------------------------------------------- 時効
    # 離れている土地の手配が、離れていた日数で戻る。日数はゲームが実際に進めた分
    # （`elapse_days` の前後の暦の差。他の MOD が日数を変えていれば変えた後の数）。
    # 控えは土地ごとの「まだ戻しに使っていない日数」だけで、手配度そのものはセーブの値を直に書く。
    #: エリア移動の最中か。移動の日数は出発地からも目的地からも離れている日数として数える。
    moving = {"depth": 0}

    @ctx.wrap(MOVE_TARGET, required=False, safe=True)
    def area_move(orig, self, *args, **kwargs):
        with lock:
            moving["depth"] += 1
        try:
            return orig(self, *args, **kwargs)
        finally:
            with lock:
                moving["depth"] -= 1

    def cool_down(app, before, here_before):
        after = ui.game_day(app)
        if before is None or after is None or after <= before:
            return
        days = after - before
        with lock:
            in_move = moving["depth"] > 0
        here_after = ui.area_id_of(ui.current_area(app))
        present = set() if in_move else {here_before, here_after} - {""}
        history = ui.area_history_of(getattr(app, "player", None))
        if not history:
            return
        period = max(1, int(cfg.STATUTE_PERIOD_MONTHS)) * DAYS_PER_MONTH
        entries = {str(raw_id): entry for raw_id, entry in history.items()}
        values = {area_id: ui.lawfulness_of(entry) for area_id, entry in entries.items()}
        # 全域手配かどうかは居る土地も含めた合計で見る（`316_` と同じ数え方）。
        total = wanted.total_of(values)
        plans = []
        key = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(key)
            away = bucket.setdefault("away", {})
            for area_id, lawfulness in values.items():
                if lawfulness is None or area_id in present:
                    continue
                new, rest, _times = rules.cool_down(lawfulness, away.get(area_id, 0), days,
                                                    period, cfg.STATUTE_STEP, cfg.STATUTE_RESTORE_TO)
                # 止められた回の日数も使い切る（溜めておくと、罰金で線を割った途端にまとめて戻る）。
                if rest:
                    away[area_id] = rest
                else:
                    away.pop(area_id, None)
                if new != lawfulness:
                    plans.append((area_id, lawfulness, new))
            worlds.save(key)
        # 全域手配の線は追手を出す MOD が窓口に置く。置かれていなければ全域手配は無い。
        line, line_by = wanted.hunted_line(app) if cfg.STATUTE_HOLD_HUNTED else (None, None)
        held = rules.hold_hunted(plans, total, line) if line else list(plans)
        wished = {area_id: after for area_id, _before, after in plans}
        changes = []
        for area_id, old, new in held:
            if new != wished[area_id]:
                write("statute: area {} ({}) held at {} (wanted total {} >= {} by {}; "
                      "would be {})".format(area_id, area_label(app, area_id), new, total,
                                            line, line_by, wished[area_id]))
            if new != old and ui.set_lawfulness(entries[area_id], new):
                changes.append((area_id, old, new))
        for area_id, old, new in changes:
            write("statute: area {} ({}) {} -> {} after {} day(s){}".format(
                area_id, area_label(app, area_id), old, new, days, " [moving]" if in_move else ""))
        if changes and cfg.STATUTE_NOTICE:
            lines = [STATUTE_TEXT.format(area=area_label(app, area_id), before=old, after=new)
                     for area_id, old, new in changes]
            def show():
                for line in lines:
                    screen.say(app, line)
            screen.schedule(show)

    @ctx.wrap(ELAPSE_TARGET, required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        """日数が進んだ後に、離れている土地の手配を戻す（日数には触らない）。"""
        if not cfg.STATUTE_ENABLED:
            return orig(self, *args, **kwargs)
        before, here_before = None, ""
        try:
            before = ui.game_day(self)
            here_before = ui.area_id_of(ui.current_area(self))
        except Exception:
            ctx.log_exc("crime incentive: cannot read the day before")
        result = orig(self, *args, **kwargs)
        try:
            cool_down(self, before, here_before)
        except Exception:
            ctx.log_exc("crime incentive: cannot cool down the wanted areas")
        return result

    # -------------------------------------------------- 追手の前金を奪う
    # どの戦闘が追手の戦闘かは追手を出す MOD（`316_`）しか知らないので、その MOD が窓口へ知らせる。
    def hunt_end(app, hunt):
        if not cfg.BOUNTY_ENABLED:
            return
        outcome, difficulty = hunt.get("outcome"), hunt.get("difficulty")
        if outcome != WON_END_TYPE:
            write("bounty: {!r} by {} (difficulty {}); no purse".format(
                outcome, hunt.get("by"), difficulty))
            return
        if isinstance(difficulty, bool) or not isinstance(difficulty, (int, float)):
            write("WARN bounty: unreadable difficulty {!r}".format(difficulty))
            return
        reward = quest_reward(max(1, int(round(difficulty))))
        amount = rules.guide_amount(reward, cfg.BOUNTY_PURSE_PCT)
        if amount <= 0:
            write("bounty: won (difficulty {} reward {}) but the purse is 0".format(
                difficulty, reward))
            return
        before = ui.gold_of(app)
        after = ui.add_gold(app, amount)
        if after is None:
            write("WARN bounty: cannot add {} gold".format(amount))
            return
        write("bounty: won by {} difficulty {} (here {} total {}) reward {} -> purse {}, "
              "gold {} -> {}".format(hunt.get("by"), difficulty, hunt.get("here"),
                                     hunt.get("total"), reward, amount, before, after))

        def show():
            refresh_gold(app)
            screen.say(app, ui.rewrite_coins(BOUNTY_TEXT.format(amount=ui.money(amount))))
        screen.schedule(show)

    wanted.on_hunt_end(owner, ctx, hunt_end)
