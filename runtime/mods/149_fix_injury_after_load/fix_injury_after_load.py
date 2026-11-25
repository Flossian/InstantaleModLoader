# -*- coding: utf-8 -*-
"""修正: セーブを読み込んだ後も、怪我が能力値に効き続ける。

## 原因（GAME.md §2.19.1、VERIFICATION.md §3.131）

怪我（`body_parts` の段階）は能力値に倍率を掛ける。
セーブに残るのは元の能力値（`original_ability_scores`）と怪我だけで、下がった後の能力値は残らない。
読み込むとゲームは元の能力値から `ability_scores` を作り直すが、そこで怪我の倍率を掛けなかった。
次に怪我の判定（`check_body_damage`）が走るまで、怪我があっても能力値は元の値のままだった。

## 直し方

読み込みの後に、怪我のある主人公の `update_ability_scores` を1回呼ぶ。
ゲームが怪我の判定の後に呼んでいる関数で、元の能力値から計算し直すので、何度呼んでも同じ値になる。

`check_body_damage` は呼ばない。
体力の上限の組み直し（`update_max_physical_integrity`）も一緒に走り、
セーブに残っている下がった後の上限へ、倍率がもう一度掛かった。
"""
from instantale_modloader import abilities, frames, loads

LOG_BASENAME = "injury_after_load.log"

#: ゲームが怪我の判定の後に呼んでいる、能力値の組み直し。
UPDATE = "update_ability_scores"
SCORES = "ability_scores"
INTACT = "intact"


def injured(body_parts):
    """怪我のある部位 `[(部位, 段階)]`。段階が `intact` の部位と、形の読めない部位は入れない。"""
    if not isinstance(body_parts, dict):
        return []
    found = []
    for part, info in body_parts.items():
        stage = info.get("stage") if isinstance(info, dict) else None
        if isinstance(stage, str) and stage.strip() and stage != INTACT:
            found.append((str(part), stage))
    return found


def changes(before, after):
    """組み直しで変わった能力値 `[(名前, 前, 後)]`。"""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    return [(name, before[name], after[name]) for name in after
            if name in before and before[name] != after[name]]


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    def reapply(app, where):
        """怪我のある主人公の能力値を組み直す。怪我が無ければ何もしない。"""
        player = frames.attr(app, "player", None)
        parts = injured(frames.attr(player, "body_parts", None))
        if not parts:
            return False
        update = frames.attr(player, UPDATE, None)
        if not callable(update):
            write("{}: WARN 怪我はあるが {} を呼べない".format(where, UPDATE))
            return False
        raw = abilities.raw(player, SCORES)
        before = dict(raw) if isinstance(raw, dict) else None
        update()
        raw = abilities.raw(player, SCORES)
        moved = changes(before, dict(raw) if isinstance(raw, dict) else None)
        write("{}: 怪我 {} の倍率を掛け直した（{}）".format(
            where, ", ".join("{} {}".format(part, stage) for part, stage in parts),
            ", ".join("{} {} -> {}".format(name, old, new) for name, old, new in moved) or "変わった能力値は無い"))
        return True

    def after_load(call):
        try:
            reapply(call.app, call.label)
        except Exception:
            ctx.log_exc("injury after load: could not rebuild the ability scores")

    owner = ctx.mod_name or "149_fix_injury_after_load"
    # 新しく始めるときも `load_game_new` を通る（TECH.md §3.3.21）。怪我は無いので何も起きない。
    loads.on(owner, ctx, (loads.LOAD,), after=after_load, write=write)
