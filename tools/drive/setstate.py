# -*- coding: utf-8 -*-
"""遊びの状態を書き換えて、今の値を1行で返す。どれも省ける。

    ARGS: {"law": {土地の id: 手配度}, "gold": 所持金, "affinity": {人物の id: 好感度},
           "elapse": 送る日数, "save": true}

好感度は実行時の人物と素データ（`npcs.npc_stores`）の両方に書く（片方だけだと保存で戻る）。
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
    for npc_id, value in (ARGS.get("affinity") or {}).items():
        from instantale_modloader.npcs import npc_stores
        rows = [getattr(a.world.characters.get(str(npc_id)), "relationship", None)]
        for _where, holder in npc_stores(a):
            data = holder.get(str(npc_id))
            rows.append(data.get("relationship") if isinstance(data, dict)
                        else getattr(data, "relationship", None))
        for rel in rows:
            if isinstance(rel, dict) and isinstance(rel.get("player"), dict):
                rel["player"]["affinity"] = value
        say("S affinity {} -> {}".format(npc_id, value))
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
