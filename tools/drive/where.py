# -*- coding: utf-8 -*-
"""今いる所を1行で返す。ARGS: {"npcs": [人物の id, …]}（省ける。並べた人物の居場所と好感度も返す）

    W day 日 area 土地の id 名前 at 施設の id 名前 (種類) conv 会話の相手 idle 手が空いたか
    N 人物の id 名前 at 土地/施設 affinity 好感度 text 感情の文
"""


def main(say):
    a = app()
    p = a.player
    area = ui.current_area(a)
    loc = getattr(p, "location", None)
    say("W day {} area {} {} at {} {} ({}) conv {} idle {}".format(
        ui.game_day(a), ui.area_id_of(area), getattr(area, "name", None),
        getattr(loc, "id", loc), getattr(loc, "name", None),
        ui.facility_type_of(loc) if loc is not None else None,
        getattr(a, "in_conversation", None), idle()))
    for npc_id in ARGS.get("npcs") or []:
        c = a.world.characters.get(str(npc_id))
        if c is None:
            say("N {} not in the world".format(npc_id))
            continue
        row = (getattr(c, "relationship", None) or {}).get("player") or {}
        say("N {} {} at {}/{} affinity {} text {}".format(
            npc_id, c.name, ui.area_id_of(getattr(c, "current_area", None)),
            getattr(getattr(c, "location", None), "id", getattr(c, "location", None)),
            row.get("affinity"), row.get("affinity_text")))
