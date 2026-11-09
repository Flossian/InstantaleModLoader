# -*- coding: utf-8 -*-
"""依頼から帰還すると、宿の宿泊日数のぶん（上限つき）日付が進む。

依頼の間は暦が進まないので、出発前の会話が相手の「今日の現在ログ」に残り、
戻った後の会話が前の会話の続きになる（GAME.md §2.25）。
日付が変わると現在ログは人生ログへ移る。

依頼の処理が全部終わった後で進める。報酬や帰還の文より先に、家賃などの日数の処理の文を出さないため。
片付けると移動する依頼は、移動で日数が進むので重ねない（窓口 `durations.quest_travels`。TECH.md §3.3.3）。
"""

from instantale_modloader import durations, ui

ENABLED = False
MAX_DAYS = 30

LOG_BASENAME = "quest_return_days.log"

#: 日付が進んだことを知らせる本文。{days} は日数。
RETURN_TEXT = "帰還までに{days}日経過した。"

QUEST_END_TARGET = "__main__:QuestEndManager.execute"
QUEST_RETIRE_TARGET = "__main__:QuestRetireManager.execute"


def days_after_return(stay_days, limit):
    """進める日数。宿の宿泊日数を上限で止める。"""
    return max(0, min(int(stay_days), int(limit)))


def apply(ctx):
    if not ENABLED:
        ctx.log("quest return days: off")
        return

    write = ctx.logger(LOG_BASENAME, tag="quest return days:")

    def advance(app, label, quest_id):
        days = days_after_return(durations.inn_stay_days(app, write=write), MAX_DAYS)
        if days <= 0:
            return
        before = ui.game_day(app)
        app.add_text(RETURN_TEXT.format(days=days))
        app.elapse_days(days)
        # 帰還の保存はもう済んでいるので、進めた日付を保存し直す。
        app.save_game()
        write("{}: quest {!r}, day {} -> {} ({} day(s), the limit is {})".format(
            label, quest_id, before, ui.game_day(app), days, MAX_DAYS))

    def after_return(app, label, quest_id):
        if app is None:
            return
        try:
            advance(app, label, quest_id)
        except Exception:
            ctx.log_exc("quest return days: cannot advance the calendar")

    @ctx.wrap(QUEST_END_TARGET, required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        quest_id, travels = None, False
        try:
            # 依頼のデータは元の処理の中で片付くので、先に読む。
            quest_id = ui.current_quest_id(app)
            travels = durations.quest_travels(app, quest_id, write=write)
        except Exception:
            ctx.log_exc("quest return days: cannot read the quest")
        result = orig(self, *args, **kwargs)
        if travels:
            write("cleared: quest {!r} moves on to another area; the journey takes its own days"
                  .format(quest_id))
        else:
            after_return(app, "cleared", quest_id)
        return result

    @ctx.wrap(QUEST_RETIRE_TARGET, required=False, safe=True)
    def quest_retire(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        quest_id = None
        try:
            quest_id = ui.current_quest_id(app)
        except Exception:
            ctx.log_exc("quest return days: cannot read the quest")
        result = orig(self, *args, **kwargs)
        after_return(app, "abandoned", quest_id)
        return result

    ctx.log("quest return days: on, up to {} day(s) ({})".format(
        MAX_DAYS, ctx.out_path(LOG_BASENAME)))
