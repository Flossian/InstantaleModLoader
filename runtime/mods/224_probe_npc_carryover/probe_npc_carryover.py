# -*- coding: utf-8 -*-
r"""計測: ロードのどの地点から NPC を世界へ入れられるか（`323_` §9 の 1）。

`323_npc_carryover` は「予約された世界のロードが済んだとき」に NPC を
1体入れる。その**「済んだとき」がどこか**が決まっていない。

早すぎれば素データも施設も揃っておらず、`make_npc` は書く先を見つけられない
（`902_` が2度決め打って外した場所と同じ穴）。
遅すぎれば、プレイヤーが動き出した後に人が湧く。

## 何を見れば「入れられる」と言えるのか

`instantale_modloader.npcs.make_npc` が要るものを、そのまま並べて数える。
推測で「たぶん揃っている」と書かず、**その瞬間に在るかどうか**を毎回記録する。

| 要るもの | 無いとどうなるか |
|---|---|
| `state.world_key(app)` が `"_"` 以外 | どの世界に居るか分からない。予約を引けない |
| 素データの `npcs` 辞書（`npc_stores`） | 書く先が無い。`generate_character` が `KeyError` |
| 採番台帳 `index['npc']`（`ids.counter`） | 台帳を進められず、次の町の生成が同じ番号を踏む（GAME.md §2.23） |
| `World.generate_character` | `Character` を組めない |
| `move_npc_to_facility` と置ける施設 | 世界には居るが誰とも会えない |

置ける施設は `guild` と `inn` の両方を数える（`323_` §6 手順6 の
「ギルド、無ければ宿」がその世界で成り立つかは、実際に数えないと分からない）。

## どこで数えるか

`120_` と `110_` が名簿を控え直しているのと同じ2点を、前後の両方で見る。

    __main__:InstantaleApp.load_game_new     新規
    __main__:InstantaleApp.start_game        続きから

名前だけでは新規と続きの別は決められない（GAME.md §1.3）ので両方包み、
ログで見分ける。

そのうえで**3地点目**を録る ― ロードの後、最初に選択肢が組み直される瞬間
（`refresh_choice_buttons`）。
上の2つが早すぎたときの逃げ場になるかを、同じ物差しで測るため。
ここは1回のロードにつき1度だけ録る（毎回録るとログが選択肢の数だけ埋まる）。

注入した時点（`on_ready`）でも1度録る。
遊んでいる最中に注入し直したときに何が見えているかは、
上の3地点とは別の話なので混ぜない。

## ゲームは変更しない

200番台の約束どおり読み取りだけ。
数えるだけで、`make_npc` は**呼ばない**（呼べば世界に人が増える）。
記録に失敗しても本体は必ず呼ぶ。
"""

from instantale_modloader import ids, npcs as npc_tools, state, ui

LOG_BASENAME = "npc_carryover_probe.log"

#: 置き先として数える施設。`323_` §6 手順6 の「ギルド、無ければ宿」。
PLACEABLE = ("guild", "inn")

#: ロードの入口。新規と続きの両方（名前では決められない。GAME.md §1.3）。
LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",
                "__main__:InstantaleApp.start_game")


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    # `armed` はロードの後、最初の選択肢の組み直しを1度だけ録るための旗。
    seen = {"armed": False, "loads": 0}

    def count_npcs(app):
        """素データの `npcs` 辞書ごとの件数。`[(どこにあるか, 件数)]`。"""
        out = []
        for where, store in npc_tools.npc_stores(app):
            try:
                out.append((where, len(store)))
            except Exception:
                out.append((where, -1))
        return out

    def count_facilities(app):
        """置ける施設の数。`(エリア数, {種別: 件数}, 置けるエリア数)`。"""
        kinds = {name: 0 for name in PLACEABLE}
        areas = ui.world_areas(app)
        placeable_areas = 0
        for area in (areas or {}).values():
            here = False
            for node in ui.nodes_of(area):
                for facility in ui.facilities_of(node).values():
                    kind = ui.facility_type_of(facility)
                    if kind in kinds:
                        kinds[kind] += 1
                        here = True
            if here:
                placeable_areas += 1
        return len(areas or {}), kinds, placeable_areas

    def record(app, label):
        """その瞬間に揃っているものを1件ぶん書く。"""
        if app is None:
            write("{}: no app; nothing to look at".format(label))
            return
        missing = []

        key = state.world_key(app)
        if key == state.UNKNOWN_WORLD:
            missing.append("world_key")

        stores = count_npcs(app)
        # 実行時の名簿（`world.characters`）は素データではない。
        # `make_npc` が書くのは素データの側なので、そちらだけを数える。
        raw = [(where, size) for where, size in stores
               if "characters" not in where.rsplit(".", 1)[-1]]
        if not raw:
            missing.append("npc stores")

        counter = ids.counter(app, "npc")
        if counter is None:
            missing.append("index['npc']")

        world = getattr(app, "world", None)
        if not callable(getattr(world, "generate_character", None)):
            missing.append("generate_character")
        if not callable(getattr(app, "move_npc_to_facility", None)):
            missing.append("move_npc_to_facility")

        areas, kinds, placeable_areas = count_facilities(app)
        if not placeable_areas:
            missing.append("guild/inn")

        write("{}: world_key={!r} index.npc={} areas={} placeable_areas={} "
              "{}".format(label, key, counter, areas, placeable_areas,
                          " ".join("{}={}".format(name, kinds[name])
                                   for name in PLACEABLE)))
        write("    npc stores: {}".format(
            ", ".join("{}={}".format(where, size) for where, size in stores)
            or "<none>"))
        # 台帳と実在の食い違いはそのまま `323_` §9 の 2 の裏取りになる
        # （ローダは `ids.claim` で台帳を読むが、その台帳が
        #  この地点で既に進んでいるかはここでしか見えない）。
        largest = -1
        for where, store in npc_tools.npc_stores(app):
            if "characters" in where.rsplit(".", 1)[-1]:
                continue
            for npc_id in store:
                try:
                    largest = max(largest, int(str(npc_id)))
                except (TypeError, ValueError):
                    continue
        write("    ids: index.npc={} largest existing={} (claim would take {})"
              .format(counter, largest,
                      max(counter or 0, largest + 1)))
        write("    {} <- {}".format(
            "READY" if not missing else "NOT READY",
            "everything make_npc needs is here" if not missing
            else "missing " + ", ".join(missing)))

    def make_load(target):
        label = target.rsplit(".", 1)[-1]

        @ctx.wrap(target, required=False, safe=True)
        def on_load(orig, self, *args, **kwargs):
            app = self if getattr(self, "world_dict", None) is not None else ui.find_app()
            try:
                seen["loads"] += 1
                write("==== load #{} ({}) ====".format(seen["loads"], label))
                record(app, "before {}".format(label))
            except Exception:
                ctx.log_exc("npc carryover probe: cannot record the entry")
            result = orig(self, *args, **kwargs)
            try:
                after = self if getattr(self, "world_dict", None) is not None else ui.find_app()
                record(after, "after {}".format(label))
                # ロードの後、最初の選択肢の組み直しを1度だけ録る。
                seen["armed"] = True
            except Exception:
                ctx.log_exc("npc carryover probe: cannot record the exit")
            return result

        return on_load

    for target in LOAD_TARGETS:
        make_load(target)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons",
              required=False, safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        if seen["armed"]:
            seen["armed"] = False       # 1回のロードにつき1度だけ
            try:
                record(self, "first refresh_choice_buttons after the load")
            except Exception:
                ctx.log_exc("npc carryover probe: cannot record the first refresh")
        return orig(self, *args, **kwargs)

    def on_ready():
        try:
            record(ui.find_app(), "at injection (on_ready)")
        except Exception:
            ctx.log_exc("npc carryover probe: cannot record the injection")

    ctx.on_ready(on_ready, key="npc carryover probe")
    ctx.log("npc carryover probe: armed (log: {})".format(LOG_BASENAME))
