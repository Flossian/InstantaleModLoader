# -*- coding: utf-8 -*-
"""修正: セーブにしか無い施設や人物で、売買・人物の詳細の生成・闘技場の試合が落ちるのを直す。

##### 何が起きているか

遊んでいる最中に、ゲームはいくつかの処理で、いま遊んでいる施設や人物を世界の素データ（`app.world_dict`）から引く。
セーブエディタで足した施設や人物は `app.save_data_dict` にしか無く、そこで `KeyError` になってスレッドが落ちる
（`242_probe_world_reads` と、施設と人物を `world_dict` からだけ外す試しで洗い出した。VERIFICATION.md §3.106）。

| 場面 | 入口 | 落ちる所 |
| --- | --- | --- |
| 売買 | `ShoppingStartManagerRemake.shopping_start_method_1` | 施設を引く（`instantale.py:3194`） |
| 人物の詳細の生成（初めての会話・訓練場） | `InstantaleApp.generate_npc_detail(character_instance)` | NPC の段へ書く（3069）。落ちた後のセーブは詳細度 2 のまま話し方を欠き、作り直されない |
| 闘技場の試合 | `ColosseumMatchStart.method` → `generate_enemy_data(enemy_id)` | 闘技場の施設を引く（10282） |

##### 直し方

ローダの窓口 `save_lookup`。対象（いる施設と主、詳細を作る人物）が世界側にあればゲームのまま
（品揃えの雛形・人物の詳細・闘技場の相手は世界側にも書かれる。本人の決めた線引き）。
世界側に無いときだけ、その関数の間 `app.world_dict` をセーブ側の辞書に差し替える。書き込みはセーブ側にだけ入る。
差し替えの間に保存が走っても、窓口が書き手で本来の世界の辞書に取り替える。

世界側を読めないとき（辞書が無い・いる施設が引けない）は判断せず、ゲームのままにする。

既定は切（設定 `ENABLED`）。骨格に無い施設・人物を引くのが明確なゲームの不具合とは言い切れず、
素のゲームでは踏めない（セーブエディタで足したときだけ）ので、全員には当てない（本人。2026-10-20）。
切っているときは何も包まず、窓口にも名乗らない。
"""

from instantale_modloader import frames, save_lookup, ui

# ---- 設定（既定値は mod.json の "settings" と一致させること。`tools/check_mods.py` が AST で突き合わせる）
#: 既定は切。骨格に無い施設・人物を引くのが明確なゲームの不具合とは言い切れないので、全員には当てない（本人。2026-10-20）
ENABLED = False

LOG_BASENAME = "save_only_entities.log"
#: 窓口に名乗る持ち主の名前。
OWNER = "146_fix_save_only_entities"

SHOP_TARGET = "__main__:ShoppingStartManagerRemake.shopping_start_method_1"
NPC_DETAIL_TARGET = "__main__:InstantaleApp.generate_npc_detail"
COLOSSEUM_TARGET = "__main__:ColosseumMatchStart.generate_enemy_data"
#: 試合を仕込む本体。相手がもう作られているかを、闘技場の `config` で見る。世界側に無いと「相手がいない」と読んで
#: 試合のたびに相手を作り直すので、ここも同じ条件で差し替える（外す試しで、作られている段でも生成が呼ばれた）。
COLOSSEUM_METHOD_TARGET = "__main__:ColosseumMatchStart.method"

#: 窓口に名乗る場面の名前（`340_` などが `save_lookup.covered()` で問い合わせる）。
SITES = ("shopping", "npc_detail", "colosseum")


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    value = frames.attr(obj, key, default)
    return default if value is frames.MISSING else value


def place_keys(app):
    """いる施設の素データの道（エリア・ノード・施設の id）と主の id。引けなければ None。"""
    location = _get(_get(app, "player"), "location")
    area = ui.current_area(app)
    if location is None or area is None:
        return None
    facility_id = location if isinstance(location, str) else _get(location, "id")
    facility, node = ui.find_facility(area, facility_id)
    if facility is None or node is None:
        return None
    area_id, node_id = ui.area_id_of(area), str(_get(node, "id", "") or "")
    if not area_id or not node_id:
        return None
    owner = _get(facility, "owner")
    return area_id, node_id, str(_get(facility, "id", facility_id)), (str(owner) if owner is not None else None)


def place_missing(app):
    """いる施設（と主）が世界側に無ければ理由の文字列、あれば None。判断できなければ None。"""
    keys = place_keys(app)
    if keys is None:
        return None
    area_id, node_id, facility_id, owner = keys
    found = save_lookup.in_world(app, "areas", area_id, "nodes", node_id, "facilities", facility_id)
    if found is False:
        return "facility {} (area {} node {}) is not in world_dict".format(facility_id, area_id, node_id)
    if owner is not None and save_lookup.in_world(app, "npcs", owner) is False:
        return "owner {} of facility {} is not in world_dict".format(owner, facility_id)
    return None


def npc_missing(app, character):
    """詳細を作る人物が世界側に無ければ理由の文字列、あれば None。判断できなければ None。"""
    npc_id = _get(character, "id")
    if npc_id is None:
        npc_id = _get(character, "character_id")
    if npc_id is None:
        return None
    if save_lookup.in_world(app, "npcs", str(npc_id)) is False:
        return "npc {} ({}) is not in world_dict".format(npc_id, _get(character, "name", "?"))
    return None


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, cap=2000)
    if not ENABLED:
        # 切っているときは包まず、窓口にも名乗らない（`340_` の宿屋の守りがそのまま働く）。
        for site in SITES:
            if save_lookup.covered(site) == OWNER:
                save_lookup.withdraw(site)
        ctx.log("save only entities: off (setting ENABLED); the game looks up world_dict as is")
        return
    save_lookup.install(ctx, write)
    for site in SITES:
        save_lookup.cover(OWNER, ctx, site)

    def run(orig, args, kwargs, app, why, site):
        if not why:
            return orig(*args, **kwargs)
        with save_lookup.redirected(app, "{}: {}".format(site, why), write):
            return orig(*args, **kwargs)

    @ctx.wrap(SHOP_TARGET, required=False, safe=True)
    def shopping_start_method_1(orig, self, *args, **kwargs):
        app = _get(self, "app") or ui.find_app()
        why = None
        try:
            why = place_missing(app)
        except Exception:
            ctx.log_exc("save only entities: cannot check the shop")
        return run(orig, (self,) + args, kwargs, app, why, "shopping")

    @ctx.wrap(NPC_DETAIL_TARGET, required=False, safe=True)
    def generate_npc_detail(orig, self, *args, **kwargs):
        why = None
        try:
            character = frames.arg(args, kwargs, "character_instance", 0)
            why = npc_missing(self, character)
        except Exception:
            ctx.log_exc("save only entities: cannot check the npc")
        return run(orig, (self,) + args, kwargs, self, why, "npc_detail")

    @ctx.wrap(COLOSSEUM_TARGET, required=False, safe=True)
    def generate_enemy_data(orig, self, *args, **kwargs):
        app = _get(self, "app") or ui.find_app()
        why = None
        try:
            why = place_missing(app)
        except Exception:
            ctx.log_exc("save only entities: cannot check the arena")
        return run(orig, (self,) + args, kwargs, app, why, "colosseum")

    @ctx.wrap(COLOSSEUM_METHOD_TARGET, required=False, safe=True)
    def colosseum_method(orig, self, *args, **kwargs):
        app = _get(self, "app") or ui.find_app()
        why = None
        try:
            why = place_missing(app)
        except Exception:
            ctx.log_exc("save only entities: cannot check the arena")
        return run(orig, (self,) + args, kwargs, app, why, "colosseum")

    ctx.log("save only entities: shopping / npc detail / colosseum fall back to the save side "
            "when the target is not in world_dict; log goes to out/{}".format(LOG_BASENAME))
