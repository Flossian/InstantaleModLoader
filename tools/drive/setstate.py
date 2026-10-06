# -*- coding: utf-8 -*-
"""遊びの状態を書き換えて、今の値を1行で返す。どれも省ける。

    ARGS: {"law": {土地の id: 手配度}, "gold": 所持金, "elapse": 送る日数, "save": true}

日数はゲーム自身の `elapse_days` で送る（日数送りを包む MOD がそのまま動く）。
`save` はゲーム自身の `save_game`。書き換えた値を残すと遊びに残るので、試した後は控えへ戻す（backup.sh / relaunch.sh restore）。
"""
KEEP_OPEN = True


def main(say):
    a = app()
    p = a.player
    hist = p.area_history
    for area_id, value in (ARGS.get("law") or {}).items():
        hist.setdefault(str(area_id), {})["lawfulness"] = value
    if "gold" in ARGS:
        p.gold = ARGS["gold"]
        upd = getattr(a, "update_ui", None)
        if callable(upd):
            upd()
    if ARGS.get("elapse"):
        a.elapse_days(ARGS["elapse"])
    if ARGS.get("save"):
        a.save_game()
    areas = {str(k): getattr(v, "name", k) for k, v in (getattr(a.world, "areas", {}) or {}).items()}
    say("S here {} gold {} law {} days {}".format(
        ui.area_id_of(ui.current_area(a)), p.gold,
        {k: (areas.get(k), v.get("lawfulness")) for k, v in hist.items()
         if isinstance(v, dict) and v.get("lawfulness") != 10},
        getattr(a.world, "days_elapsed", None)))
    say("<done>")
