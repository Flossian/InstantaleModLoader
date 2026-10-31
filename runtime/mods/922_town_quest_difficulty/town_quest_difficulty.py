# -*- coding: utf-8 -*-
"""街ごとに、これから作られる依頼の難易度の範囲を決める。

ゲームは街の依頼の難易度を、街の id ごとに決まった範囲から引く（GAME.md §2.9）:

    初訪問の3件   random.sample(range(lo, hi), 3)      save_area_json:write_area_data_to_world_dict（:329）
    探した1件     random.randint(lo, hi - 1)          DisplayQuestChoice.generate_random_quest（:5957）

id 0〜8 の範囲は世界の形と関係なく id で決まり、id 9 以降（世界の一括生成やエディタで足した街）は 1〜76 の全域。
この MOD は、設定画面（`tool.py`）で決めた範囲をこの2つの乱数の場所でだけ差し替える。
**引いた値はゲーム自身が LLM への指示と依頼の記録へ渡す**ので、指示の「難易度は N」・セーブ・
`318_area_difficulty_growth` の素の値がどれも同じ数になる（作った後で書き直す形だと 318 と順番を取り合う）。

すでにある依頼は触らない。範囲を決めていない街は素のまま。

| 差し替える所 | 見分け方 |
|---|---|
| `random.sample` | `write_area_data_to_world_dict` の中、母集団が `range` で `k=3` の最初の1回 |
| `random.randint` | `generate_random_quest` の中の最初の1回 |

どちらもスレッドごとの印で窓を開く（ゲームは移動も依頼探しも別スレッドで走らせる）。
窓の外の乱数は素通しする。

範囲は `state/town_quest_difficulty/<世界名>.json` の `towns`（設定画面が書く。本体は読むだけ）。
世界名だけを鍵にするのは、世界の設定だから（同じ世界で作り直した主人公にも効く）。
範囲は窓口 `quest_bands` にも出す（`133_ui_area_difficulty` が訪ねる前の街の見積もりに使う。TECH.md §3.3.25）。

遊び方の説明は DOC.md、検証の経過は VERIFICATION.md §3.117。
"""

import random
import threading

from instantale_modloader import quest_bands, ui
from instantale_modloader.state import UNKNOWN_WORLD, WorldStore, world_key, world_key_of_dict

from . import bands

LOG_BASENAME = "town_quest_difficulty.log"

#: `state/` 直下のフォルダ名。設定画面と同じ（`modtool.state_dirname`）。
STATE_DIRNAME = "town_quest_difficulty"

# ---------------------------------------------------------------- 設定（mod.json）
# 範囲の上限。既定 76 はゲームが依頼の難易度に使う上限。100 まで上げられる。
# 76 を超えた分は敵だけが強くなる（報酬・才能点・品はゲームが 76 で止める。GAME.md §2.13.2。
# `923_beyond_76` が入っていれば伸びる）。
DIFFICULTY_LIMIT = 76


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    owner = "922_town_quest_difficulty"

    # 設定画面が書く控え。本体は読むだけなので、フォルダは作らない（own=False）。
    store = WorldStore(ctx, STATE_DIRNAME, own=False, write=write)

    #: スレッドごとの窓。`build`（初訪問）/ `search`（探す）。
    local = threading.local()

    def limit():
        return bands.clamp_limit(DIFFICULTY_LIMIT)

    def band_for(key, area_id):
        """その世界のその街に決めた範囲。決めていなければ None。

        設定画面が遊んでいる間に書き換えても次の生成から効くよう、更新時刻が変われば読み直す。
        """
        if not key or key == UNKNOWN_WORLD or area_id is None:
            return None
        bucket = store.load(key, fresh=True)
        towns = bucket.get("towns") if isinstance(bucket, dict) else None
        entry = towns.get(str(area_id)) if isinstance(towns, dict) else None
        if not isinstance(entry, dict):
            return None
        return bands.normalize(entry.get("lo"), entry.get("hi"), limit())

    quest_bands.provide(owner, ctx, lambda app, area_id: band_for(world_key(app), area_id))

    # ------------------------------------------------------------ 初訪問の3件
    @ctx.wrap("save_area_json:write_area_data_to_world_dict", required=False, safe=True)
    def build(orig, *args, **kwargs):
        """街の中身を作る。間の `random.sample` の窓を開く。引数は来た形のまま渡す。"""
        world_dict = args[0] if args else kwargs.get("world_dict")
        area_id = args[1] if len(args) > 1 else kwargs.get("area_id")
        previous = getattr(local, "build", None)
        local.build = {"key": world_key_of_dict(world_dict, UNKNOWN_WORLD),
                       "area": None if area_id is None else str(area_id), "used": False}
        try:
            return orig(*args, **kwargs)
        finally:
            local.build = previous

    @ctx.wrap("random:sample", required=False, safe=True)
    def sample(orig, *args, **kwargs):
        window = getattr(local, "build", None)
        if not window or window["used"]:
            return orig(*args, **kwargs)
        population = args[0] if args else kwargs.get("population")
        k = args[1] if len(args) > 1 else kwargs.get("k")
        if not isinstance(population, range) or population.step != 1 or k != 3:
            return orig(*args, **kwargs)
        window["used"] = True
        band = band_for(window["key"], window["area"])
        if band is None:
            write("初訪問: 土地 {} は範囲を決めていない（ゲームの range({}, {}) のまま）".format(
                window["area"], population.start, population.stop))
            return orig(*args, **kwargs)
        rest = {name: value for name, value in kwargs.items() if name not in ("population", "k")}
        result = orig(range(band[0], band[1] + 1), 3, **rest)
        write("初訪問: 土地 {} range({}, {}) -> {}〜{} を引いた {}".format(
            window["area"], population.start, population.stop, band[0], band[1], result))
        return result

    # ------------------------------------------------------------ 探した1件
    @ctx.wrap("__main__:DisplayQuestChoice.generate_random_quest", required=False, safe=True)
    def search(orig, self, *args, **kwargs):
        """依頼を探す。間の最初の `random.randint` の窓を開く。"""
        app = getattr(self, "app", None) or ui.find_app()
        previous = getattr(local, "search", None)
        try:
            area_id = ui.area_id_of(ui.current_area(app))
            local.search = {"key": world_key(app), "area": area_id, "used": False}
        except Exception:
            ctx.log_exc("town quest difficulty: 依頼を探す土地を読めなかった")
            local.search = None
        try:
            return orig(self, *args, **kwargs)
        finally:
            local.search = previous

    @ctx.wrap("random:randint", required=False, safe=True)
    def randint(orig, *args, **kwargs):
        window = getattr(local, "search", None)
        if not window or window["used"] or len(args) != 2 or kwargs:
            return orig(*args, **kwargs)
        window["used"] = True
        band = band_for(window["key"], window["area"])
        if band is None:
            write("探す: 土地 {} は範囲を決めていない（ゲームの randint{} のまま）".format(
                window["area"], tuple(args)))
            return orig(*args, **kwargs)
        result = orig(band[0], band[1])
        write("探す: 土地 {} randint{} -> {}〜{} を引いた {}".format(
            window["area"], tuple(args), band[0], band[1], result))
        return result

    ctx.log("town quest difficulty: limit {}; ranges in state/{}/<world>.json; log goes to out/{}"
            .format(limit(), STATE_DIRNAME, LOG_BASENAME))
