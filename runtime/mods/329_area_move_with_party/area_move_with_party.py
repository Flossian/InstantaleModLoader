# -*- coding: utf-8 -*-
"""雇った仲間がエリア移動を拒まなくなる。

素のゲームは `AreaMoveManager.execute` → `method_1` で同行者を見て、条件に当たると
`llm_manager:area_move_rejector` に拒否の一言を書かせて戻る（運賃も日数も動かない。
GAME.md §2.18、`217_` の `out\\area_move.log` で `days=[] texts=1`）。

分岐が読むのは `relationship["player"]["relationship"]` の配列（`228_` の実機記録、2026-09-10。
雇用 NPC は `['同行中']`）。友好度（版1）も `Character.state`（版2）も読んでいない。
「家族になろう」の会話で `二人は家族になった。` と書く経路がこの配列に `家族` を入れると読む。

やること: `execute` に入った時点で同行者全員のこの配列を `['家族']` に置き換え、
判定を通り抜けた印である `elapse_days` の直前で元の配列に戻す。
拒否・所持金不足のときは `execute` を抜けるときに戻す（どちらも `elapse_days` を通らない）。
`save_game` の直前にも戻す（版1で、拒否の途中の保存に一時値が乗った。VERIFICATION.md §3.56）。
戻すときは `save_data_dict["npcs"][id]` に同じ値が写っていればそちらも戻す。

外れの検知: 置いた後も `area_move_rejector` が呼ばれたら `WARN rejected:` を書く。
"""

from instantale_modloader import ui

LOG_BASENAME = "area_move_party.log"

#: 判定の間だけ置く関係の配列。本体が「家族になろう」で付ける値（exe の定数表の `家族`）。
FAMILY = ["家族"]


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    # lifted: {npc_id: (relationship["player"] の欄, 元の配列 or MISSING, save_data_dict 側の元の配列 or MISSING)}
    state = {"lifted": None, "rejected": False, "marker": None}
    MISSING = object()

    def save_entry(app, npc_id):
        npcs = (getattr(app, "save_data_dict", None) or {}).get("npcs")
        entry = npcs.get(str(npc_id)) if isinstance(npcs, dict) else None
        return entry if isinstance(entry, dict) else None

    def slot_of(holder):
        """`relationship["player"]` の欄。Character でもセーブの辞書でも同じ形。"""
        relationship = (holder.get("relationship") if isinstance(holder, dict)
                        else getattr(holder, "relationship", None))
        slot = relationship.get("player") if isinstance(relationship, dict) else None
        return slot if isinstance(slot, dict) else None

    def restore(app, reason):
        lifted = state["lifted"]
        state["lifted"] = None
        if not lifted:
            return
        for npc_id, (slot, before, saved_before) in lifted.items():
            if slot.get("relationship") is state["marker"]:
                if before is MISSING:
                    del slot["relationship"]
                else:
                    slot["relationship"] = before
            else:
                write("WARN restore ({}): {} relationship is no longer the marker ({!r}); left as is"
                      .format(reason, npc_id, slot.get("relationship")))
            saved = slot_of(save_entry(app, npc_id))
            if saved is not None and saved.get("relationship") == FAMILY and saved_before != FAMILY:
                if saved_before is MISSING:
                    del saved["relationship"]
                else:
                    saved["relationship"] = saved_before
                write("restore ({}): {} save_data_dict copied {!r}; put back {!r}"
                      .format(reason, npc_id, FAMILY, saved_before))
        write("restore ({}): {}".format(reason, ", ".join(sorted(lifted))))

    def lift(app):
        lifted = {}
        state["marker"] = list(FAMILY)   # 同一性で見分けるので毎回新しい list
        for npc_id in ui.party_member_ids(app):
            character = ui.character_of(app, npc_id)
            slot = slot_of(character)
            if slot is None:
                write("skip: {} has no relationship['player'] ({!r})".format(npc_id, character))
                continue
            before = slot.get("relationship", MISSING)
            if before == FAMILY:
                continue
            saved = slot_of(save_entry(app, npc_id))
            saved_before = saved.get("relationship", MISSING) if saved is not None else MISSING
            slot["relationship"] = state["marker"]
            lifted[str(npc_id)] = (slot, before, saved_before)
            write("lift: {} ({}) relationship {!r} -> {!r}".format(
                npc_id, ui.character_name(app, npc_id), before, FAMILY))
        return lifted

    def app_of(self):
        return getattr(self, "app", None) or ui.find_app()

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False)
    def execute(orig, self, choice_text=None, *args, **kwargs):
        app = app_of(self)
        state["rejected"] = False
        try:
            state["lifted"] = lift(app)
        except Exception:
            ctx.log_exc("area move party: cannot lift relationship")
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            restore(app, "execute done")
            if state["rejected"]:
                write("WARN rejected: the game refused the move although every companion "
                      "had relationship {!r}; the check reads something else (VERIFICATION.md §3.56)".format(FAMILY))

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days, *args, **kwargs):
        restore(self, "elapse_days({})".format(days))
        return orig(self, days, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.save_game", required=False, safe=True)
    def save_game(orig, self, *args, **kwargs):
        restore(self, "save_game")
        return orig(self, *args, **kwargs)

    @ctx.wrap("scripts.llm.llm_manager:area_move_rejector", required=False, safe=True)
    def area_move_rejector(orig, character_life_log=None, player=None,
                           character_instance=None, worldview=None, *args, **kwargs):
        state["rejected"] = True
        write("rejector: {} relationship={!r}".format(
            getattr(character_instance, "name", character_instance),
            getattr(character_instance, "relationship", None)))
        return orig(character_life_log, player, character_instance, worldview,
                    *args, **kwargs)

    ctx.log("area move party: installed -> {}".format(ctx.out_path(LOG_BASENAME)))
