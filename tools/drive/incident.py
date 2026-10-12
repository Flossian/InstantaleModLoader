# -*- coding: utf-8 -*-
"""`338_town_incidents` の出来事を、決めた街に決めた種類で起こす。ARGS: {"kind": 種類の英名, "area": 土地の id, "elapse": 日数}

    kind    `incidents.KINDS` の `key`（`influx` など）。省くと MOD の抽選のまま
    area    起こす街の土地の id か名前。省くといまいる街
    elapse  送る日数（ゲーム自身の `elapse_days`。既定 1）
    force   true なら、その街の「収まった後の休み」を外してから起こす

次の1回の抽選だけ `incidents.roll` を差し替え、候補をその街と種類に絞り、確率と抽選の日を満たす。
呼び名と情景の文の頼み（LLM）は MOD がふだんどおり出す。差し替えは抽選が済んだら戻す。
"""
KEEP_OPEN = True


def main(say):
    MOD = mod("town_incidents")
    if MOD is None:
        say("town_incidents is not loaded")
        say("<done>")
        return
    inc = MOD.incidents
    real = inc.roll
    a = app()
    area = str(ARGS.get("area") or ui.area_id_of(ui.current_area(a)))
    kind_key = ARGS.get("kind")

    def once(bucket, day, towns, rng, kinds, **kwargs):
        inc.roll = real
        picked = [row for row in towns if area in (str(row[0]), str(row[1]))]
        if kind_key:
            kinds = [kind for kind in kinds if kind["key"] == kind_key]
        say("roll: towns {} -> {}, kinds -> {}".format(
            len(towns), picked, [kind["key"] for kind in kinds]))
        if ARGS.get("force") and isinstance(bucket.get("cooldown"), dict):
            for row in picked:
                bucket["cooldown"].pop(str(row[0]), None)     # 収まった後の休みを外す（試しの手順）
        bucket["next_roll_day"] = day
        kwargs["chance_percent"] = 100
        return real(bucket, day, picked, rng, kinds, **kwargs)

    inc.roll = once
    try:
        a.elapse_days(int(ARGS.get("elapse") or 1))
    finally:
        inc.roll = real
    say("<done>")
