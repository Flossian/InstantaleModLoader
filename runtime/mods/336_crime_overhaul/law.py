# -*- coding: utf-8 -*-
"""手配への反応。店が委縮して値を下げる・時効。仕様は DOC.md の同じ名前の節。

##### 店が委縮して値を下げる（手配中の土地で値段が動く）

- その土地で手配されていると、店の買値が下がり、売値が上がる。率は手配の重さ × 1点あたりの率（上限あり）
- 店の主の好感度が設定の値以上なら委縮しない（値段はそのまま。気づいたうえで応じる1行を出す）
- 率はローダの窓口 `wanted` に置く（店の値の圧）。店主ごとに向きを決める MOD が引き受けていれば、値段と知らせはそちらが出す
- 値段はローダの関所（`prices.adjust`）へ一時の段として置く。保存の直前には外れる（セーブへ焼き付けない）

##### 時効（離れている土地の手配が戻る）

- 離れている土地だけ、離れていた日数 3ヵ月ごとに手配度が 10 戻る。戻るのは平常の 10 まで
- ただし全ての土地の手配の重さの合計が全域手配の線以上の間は、
  合計がその線に届いたところで止める（追手は続く。そこから先は役場の罰金）。
  線は追手を出す MOD（`316_`）がローダの窓口 `wanted` に置く。置かれていなければ全域手配は無いので止めない
- 新しい罪は、戻った後の値にそのまま足される（数え直さない）
- 日数は `elapse_days` の前後の暦の差。エリア移動の日数は出発地からも目的地からも離れていた日数として数える

追手を倒したときの前金は `316_bounty_hunter` へ移した（追手を出す MOD が、その戦闘の報酬も持つ）。
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
SHOP_FRIEND_TEXT = "{name}は手配書の顔に気づいたが、いつもどおりの値で応じてくれた。"
STATUTE_TEXT = "{area}では、騒ぎのほとぼりが冷めつつある。（手配度 {before} → {after}）"


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    owner = env.owner
    here_lawfulness, area_label = env.here_lawfulness, env.area_label
    lock = threading.Lock()

    # -------------------------------------------------- 店が委縮して値を下げる
    # 率（手配の重さ × 1点あたり）はローダの窓口 `wanted` に置く（店の値の圧）。
    # 店主ごとに向きを決める MOD（`341_shopkeeper_attitude`）が引き受けていれば、値段も知らせもそちらに任せる。
    # 居なければここで掛ける。店の主の好感度が線以上なら委縮しない。
    def keeper_of(app):
        """いま居る施設の主（人物）。主がいなければ None。"""
        facility = env.current_facility(app)
        owner_id = getattr(facility, "owner", None)
        return ui.character_of(app, str(owner_id)) if owner_id is not None else None

    def friendly_keeper(app):
        """委縮しない店の主 `(主, 好感度)`。委縮するなら None。"""
        keeper = keeper_of(app)
        affinity = rules.affinity_of(keeper) if keeper is not None else None
        if rules.keeper_unafraid(affinity, cfg.INTIMIDATION_FRIEND_AFFINITY):
            return keeper, affinity
        return None

    def pressure(app):
        """店の値の圧（窓口 `wanted` に置く関数）。切っていれば None。"""
        if not cfg.INTIMIDATION_ENABLED:
            return None
        return rules.intimidation_rate(here_lawfulness(app), cfg.INTIMIDATION_PER_POINT,
                                       cfg.INTIMIDATION_CAP)

    wanted.declare_shop_pressure(owner, ctx, pressure)

    def intimidation(item, key, price):
        """手配中の土地なら率を掛ける。引き受けた MOD が居る・触らないなら None。"""
        if not cfg.INTIMIDATION_ENABLED or wanted.shop_pressure_taker() is not None:
            return None
        app = ui.find_app()
        if app is None or friendly_keeper(app) is not None:
            return None
        return rules.intimidated_price(key, price, pressure(app) or 0.0, cfg.INTIMIDATION_SELL)

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
                rate = pressure(app) or 0.0
                taker = wanted.shop_pressure_taker() if rate > 0 else None
                friend = friendly_keeper(app) if rate > 0 and taker is None else None
                if taker is not None:
                    write("shop: lawfulness {} -> rate {}% handed to {}".format(
                        lawfulness, int(round(rate * 100)), taker))
                elif friend is not None:
                    keeper, affinity = friend
                    name = getattr(keeper, "name", None) or "店主"
                    write("shop: lawfulness {} but {} has affinity {} -> no fear".format(
                        lawfulness, name, affinity))
                    if cfg.INTIMIDATION_NOTICE:
                        text = SHOP_FRIEND_TEXT.format(name=name)
                        screen.schedule(lambda: screen.say(app, text))
                elif rate > 0:
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
