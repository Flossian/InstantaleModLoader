# -*- coding: utf-8 -*-
"""機能調整: エリア移動（土地から土地へ）の日数・料金・文言を設定で変えられるようにする。

素のゲームの移動手段は `徒歩(3ヵ月)` と `馬車(1000G)` の2つで、
日数も料金も文言も固定されている（素の値・`mode` の実値・文言の実測は GAME.md
§2.18）。
この MOD はその3つを mod.json の設定から変えられるようにする。
既定値はすべて素のゲームの値で、そのままなら挙動は何も変わらない。

変更点は5つ。

| 何を変えるか | どこで変えるか |
|---|---|
| ボタンの表示 | `AreaMoveCofirmation.update_button_display` の後で `text` だけ書き換える |
| 経過する日数 | `AreaMoveManager.execute` の間だけ `InstantaleApp.elapse_days` に渡る数を差し替える |
| 移動中の文言 | 同じ窓の間だけ `InstantaleApp.add_text` の文言をテンプレートへ置き換える |
| 馬車の料金 | ゲームが引き落とす前に差額ぶん所持金をずらす（前払い調整） |
| 離れた街への距離補正 | `325_road_opening` が開いた道だけ、挟む街の数に応じて日数と馬車代を加算/倍加（設定でオフ可） |

この作りの利点は、表示と実態が必ず一致すること。
ボタンに出す日数は設定値そのもので、`elapse_days` に渡す数も同じ設定値。
ゲームが内部で日数をどう決めていても食い違わない。

留意点:

- **ボタンは `text` だけ触る**（spec と `args` はゲーム自身のものを残す）ので、
  この MOD を外しても押下の挙動は壊れない。
  手段の見分けもラベルの文字列には下がらず `args` の
  `mode` だけで行う（`kind_of_mode` のコメント）
- 日数は渡す数を差し替えるだけ。
  `orig` は必ず呼ぶので暦の進め方はゲームのまま。
  窓は `AreaMoveManager.execute` の間だけで、
  訓練・休養・他の依頼の日数送りには触らない。
  1回の移動で複数回呼ばれても合計が設定値を超えないよう予算方式（`307_` の
  `TRAVEL_DAYS` と同じ形）
- 料金は前払い調整。
  徴収の関数も引く額（素の運賃）も変えられないので、
  引かれる前に「素の運賃 − 設定額」ぶん所持金をずらす。
  引き落としは1回でちょうど設定額。
  引き落としが起きなければ `finally` で差額をそのまま返す。
  素の運賃はボタンのラベルからそのつど読む。
  後払いの払い戻し方式から作り替えた経緯は VERIFICATION.md §3.27
- テンプレートが空ならゲームのまま。
  窓の間に手掛かり（`DEPART_MARKS` / `ARRIVE_MARKS`）へ当たった文言だけを置き換え、
  当たらなかったものはログへ残す。
  待機表示の点（`show_loading_text`）には触らない
- 距離補正の「挟む街の数」は、`325_` が道を開いた時点に控え
  （`state/road_opening/<世界>.json` の `roads` の `hops`）へ記録した値を
  **読むだけ**で使う（`WorldStore(own=False)`。TECH.md §3.2.3）。
  いまの接続で BFS し直さないのは、開いた道自体が辺になっていて必ず「隣」に
  なってしまうため。`325_` が無ければ挟む街の数は常に 0 ＝ 素の移動は不変。
  距離補正で日数が**増える**方向は、`elapse_days` に来た数が素の値
  （90 / 14）そのものだったときだけ予算へ置き換えることで効かせる。
  素の値と違う数は外側（`307_` の予算）が減らした可能性があるので、
  頭打ちだけ掛けて増やさない

「素のままなら触らない」の例外が1つだけある: **馬車のボタンには既定でも日数を出す**（`馬車(1000G)`
→ `馬車(1000G・14日)`）。
素のゲームは馬車の所要日数を画面のどこにも出さないため。
足す 14 は実測の素の値（`GAME_COACH_DAYS`）なので、表示と実態の一致は崩れない。
挙動は既定では従来どおり一切触らない。

`307_area_move_dungeon` より前（内側）に置く（mod.json の `"before"`）。
307_ は「危険な道」の到着の移動中にゲームの `徒歩で目指す...` を握り潰すので、
こちらが外側に居ると先に置き換えてしまい、307_ の伏せが効かなくなる。

遊び方の説明は MODS.md の `314_` の項、検証の経過は VERIFICATION.md §3.27。
"""

import sys

from instantale_modloader import ui
from instantale_modloader.state import WorldStore, world_key

LOG_BASENAME = "area_move_custom.log"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_area_move_custom_store__"

# ボタンには何も足さないが、`ui.Screen` の道具（say / apply_buttons）を使うので印のキーは他の MOD と別にして持つ（TECH.md
# §3.3）。
MARK = "mod_area_move_custom"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む。TECH.md
# §3.8）。
# 他のファイルへ移さないこと。
# 既定値はすべて素のゲームの値。
# 素の値のままなら、この
# MOD はその項目に一切触らない（日数もボタンの表示も料金も）。
# 変えた項目だけが効く。
# 「-1 で無効」のような番人値は使わない。
# 画面に出る数字がそのまま素の値になるほうが分かりやすい。
# 素の値との照合は下の GAME_* 定数。
# 移動にかかる日数（徒歩=90 は実測。馬車=14 は推定、`217_` で確定させる）。
WALK_DAYS = 90
COACH_DAYS = 14

# 馬車の料金（素のゲームは 1000G。実測）。
# 0 にするとタダ。
COACH_PRICE = 1000

# 手段の名前。
# ボタンと文言の {name} に入る。
# 馬車を「竜車」にする、など。
WALK_NAME = "徒歩"
COACH_NAME = "馬車"

# ボタンの表示テンプレート。
# 徒歩は日数を変えたときだけこれで表示し直す（素の値のままならゲームの表示
# `徒歩(3ヵ月)` を残し、呼び名だけ差し替える）。
# 馬車は常にこの表示。
# 素のゲームは馬車の所要日数をどこにも出さないので、
# 既定でも日数を足す（モジュール docstring の「例外」を参照）。
WALK_BUTTON = "{name}({days}日)"
COACH_BUTTON = "{name}({price}G・{days}日)"

# 移動中の文言テンプレート。
# 空文字列でゲームのまま。
# 実測（2026-08-17、`217_`）: 徒歩 `徒歩で目指す。長旅だ...` ／馬車 `1000ゴールドを支払った。快適な旅だ...` ／ 到着
# `辿り着いた。` 馬車の既定は実測の文そのもの（`{price}` 入り）。
# 素の設定なら一字も変わらず、**料金を変えると文中の金額が追随する**。
# ゲームは料金をいくらに直しても「1000ゴールドを支払った」と言い続けるので（実測）、
# ここで直すのが唯一の口。
WALK_DEPART_TEXT = "{name}で目指す。長旅だ..."
COACH_DEPART_TEXT = "{price}ゴールドを支払った。快適な旅だ..."
ARRIVE_TEXT = ""

# 離れた街への距離補正。**`325_road_opening` が開いた道だけ**に効く。
# ゲームは開いた道も隣の街と同じ扱い（徒歩90日・馬車1000G）にしてしまうので、
# 「間に挟む街の数」（道を開いた時点の値。`325_` が控えに記録している）に応じて
# 日数と馬車代を重くする。
#   "off"       補正なし（従来どおり）
#   "add"       日数 += 加算の日数×挟む街の数 ／ 馬車代 += 加算の料金×挟む街の数
#   "multiply"  日数・馬車代とも × (1 + 倍率×挟む街の数)
# 隣どうしの普通の移動は挟む街が 0 なので、この補正で素の移動は一切変わらない。
# ボタンの表示も補正後の値で出る（表示と実態の一致は崩さない）。
HOP_SCALING = "multiply"

# multiply の1街ごとの倍率。1.0 なら2つ先（挟む街1）で2倍、3つ先で3倍。
HOP_FACTOR = 1.0

# add の1街ごとの加算量（日数と馬車代）。
HOP_ADD_DAYS = 7
HOP_ADD_FARE = 500

# 移動日数の上限。距離補正（と設定した基準日数）の後に掛ける。
# 素のゲームの移動は一律3ヵ月なので、徒歩の既定は 90。馬車は 30。
# 既定の設定（徒歩90・馬車14）は上限に掛からないので、素の移動は変わらない。
# 上限で削られた値が素の値と同じになれば「触らない」に落ちる（徒歩の 90 など）。
WALK_DAYS_MAX = 90
COACH_DAYS_MAX = 30

# ---------------------------------------------------------------- コード側の設定
# `AreaMoveManager` の `mode` の実測値。
# **実機で観測できたものだけ書くこと**（GAME.md §2.18）。
# ゲームの更新で変わったらここを書き直す。
WALK_MODES = ("on_foot",)
COACH_MODES = ("coach",)

# 移動中の文言をどれと見なすかの手掛かり。
# どれも実測の文言に当たる形（徒歩 `徒歩で目指す。長旅だ...` ／ 馬車
# `1000ゴールドを支払った。快適な旅だ...`。2026-08-17、`217_probe_area_move`）。
# 通貨の語そのものは手掛かりにしない（`130_` が表記を差し替えると外れるため）。
# 窓の中でしか見ないので短くてよい。
DEPART_MARKS = ("で目指す", "長旅だ", "を支払った", "快適な旅")
ARRIVE_MARKS = ("辿り着いた",)

# 素のゲームの値。
# **設定がこれと同じ項目には触らない**ための照合値。
# 3つとも実測（徒歩90・運賃1000は GAME.md §2.18、
# 馬車14は 2026-08-17 に `217_probe_area_move` の `elapse_days(14)` で確定）。
# 運賃はボタンのラベル（`馬車(1000G)`）からも読み取り、
# 読めなかったときの落とし所としてもこの値を使う。
GAME_WALK_DAYS = 90
GAME_COACH_DAYS = 14
GAME_COACH_PRICE = 1000

# ラベルから料金を読むのはローダの語彙（`ui.parse_coin`。`315_` と共有）。
# `馬車(1000G)` → 1000。桁区切りが入っても読める。
# 通貨の表記が差し替えられていれば（`130_`）`馬車(1000円)` も読む。

# `325_road_opening` の控えのフォルダ（`state/road_opening/<世界>.json`）。
# **読むだけ**（`WorldStore(own=False)`。MOD どうしは import せず、
# 同じファイルを読むことで繋がる。TECH.md §3.2.3。325_ が入っていなければ
# ファイルが無いだけで、挟む街の数は常に 0 ＝ 補正なしに落ちる）。
ROADS_DIRNAME = "road_opening"

# 手持ちが設定した運賃に足りないときの一言。
REFUSE_TEXT = "（{name}代{price}Gに足りない ― 手持ち{gold}G）"


class _SafeDict(dict):
    """テンプレートに無い変数名が来ても落とさない（`{typo}` はそのまま残る）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def fmt(template, **values):
    """設定のテンプレートを埋める。壊れたテンプレートでも素の文字列で返す。

    埋めた後に通貨の表記を今の表記へ直す（`130_` が差し替えていれば
    `馬車(1000G・14日)` → `馬車(1000円・14日)`）。
    設定のテンプレートは素のゲームの言い方（`G`）のままでよい。
    """
    try:
        filled = str(template).format_map(_SafeDict(values))
    except Exception:
        filled = str(template)
    return ui.rewrite_coins(filled)


def kind_of_mode(mode):
    """`mode` の実測値から手段を見分ける。当たらなければ None。

    文字列（ボタンのラベル）には下がらない。
    ラベルからなら推測できるが、日数を差し替える窓（`execute`）に来るのは `mode` だけなので、
    ラベルで緩く当てると「表示は変わったのに日数は素のまま」という、
    この MOD が一番やってはいけない食い違いを作る。
    `mode` が当たらないときは表示も日数も料金も文言も全部ゲームのままにして、
    ログに残す。
    """
    value = str(mode)
    if value in WALK_MODES:
        return "walk"
    if value in COACH_MODES:
        return "coach"
    return None


def move_options(buttons):
    """確認画面に並ぶ `AreaMoveManager` のボタンを `(entry, args)` で返す。

    `args` は読むだけ。
    `[target_area_id, mode]` という並びは `targets.txt` のシグネチャそのもので、
    値の意味は解釈しない（`307_` の `world.move_options`）。
    """
    found = []
    if not isinstance(buttons, (list, tuple)):
        return found
    for entry in buttons:
        if ui.spec_cls_name(entry) != "AreaMoveManager":
            continue
        args = ui.spec_args(entry)
        if args and len(args) >= 2:
            found.append((entry, list(args)))
    return found


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="area move custom", mark=MARK)

    # **置き場は `sys`。** `apply()` は1プロセスで何度も走り、
    # 当て直しは背景スレッドの `boot()` から来る（未 import のモジュールが
    # 現れた時＝最初の LLM リクエストの時）。移動や滞在の最中にそれが挟まると、
    # ここで作り直した空の器を新しいラッパが握り、窓や予算が None のまま
    # 日数の頭打ちが効かなくなる。「2週間」の滞在が素の30日を、
    # 調整した徒歩が素の90日を消費する。
    # `311_` / `312_` が控えを `sys` に置いているのと同じ理由。
    state = getattr(sys, STATE_STORE_ATTR, None)
    if state is None:
        state = {
            # いま `AreaMoveManager.execute` の中に居るかの窓。
            # 中身は _open_window。
            "window": None,
            # 確認画面のラベルから読み取った素の運賃（読めた最新の値）。
            "game_price": None,
            # 自分が最後に書いた馬車のラベル。
            # 画面がラベルを組み直さないビルドで
            # update_button_display がもう一度来たとき、**自分の書いた
            # 300G を素の運賃として読み込まない**ための目印。
            "our_coach_label": None,
        }
        setattr(sys, STATE_STORE_ATTR, state)

    # 325_ が開いた道の控え。読むだけ（own=False はフォルダも作らず save も拒む）。
    # 書くのは 325_ だけなので `fresh=True` で読めば足りる（更新時刻が変われば
    # 読み直る）。読み取り専用なので、apply のたびに作り直しても安全。
    roads = WorldStore(ctx, ROADS_DIRNAME, own=False, write=write)

    def scaled(base, hops, add_per_hop, mode=None, factor=None):
        """距離補正後の値。挟む街が 0 なら素通し。

        `mode` / `factor` は自己検証が設定と無関係に式を確かめるための引数で、
        実経路では常に設定（`HOP_SCALING` / `HOP_FACTOR`）を読む。
        """
        mode = str(HOP_SCALING) if mode is None else mode
        factor = float(HOP_FACTOR) if factor is None else factor
        hops = max(0, int(hops))
        if hops == 0 or mode == "off":
            return max(0, int(base))
        if mode == "add":
            return max(0, int(base) + int(add_per_hop) * hops)
        if mode == "multiply":
            return max(0, int(round(int(base) * (1.0 + factor * hops))))
        return max(0, int(base))

    def road_hops(app, origin_id, target_id):
        """この2街の間に挟む街の数。`325_` が開いた道でなければ 0。

        値は道を開いた時点に `325_` が控えへ記録したもの（`roads` の `hops`）。
        いまの接続で BFS し直さないのは、開いた道自体が辺になっていて
        必ず「隣」になってしまうため。控えが読めない・`325_` が無い・記録に
        `hops` が無い、のどれでも 0（＝補正なし）へ落ちる。
        """
        if str(HOP_SCALING) == "off" or app is None \
                or not origin_id or not target_id:
            return 0
        try:
            bucket = roads.load(world_key(app), fresh=True)
            want = {str(origin_id), str(target_id)}
            for record in (bucket or {}).get("roads") or []:
                if isinstance(record, dict) and \
                        {str(record.get("from")), str(record.get("to"))} == want:
                    hops = record.get("hops")
                    if isinstance(hops, int) and not isinstance(hops, bool) \
                            and hops > 0:
                        return hops
                    return 0
        except Exception:
            ctx.log_exc("area move custom: cannot read the opened roads")
        return 0

    def days_limit(kind, hops=0):
        """その移動の実効日数。素の値のまま（触らない）なら None。

        基準は設定値（既定は素の値）。距離補正はその上に乗るので、
        設定が素のままでも挟む街が 1 以上なら実効値が変わり、差し替えが立つ。
        """
        if kind == "walk":
            value = min(scaled(int(WALK_DAYS), hops, HOP_ADD_DAYS),
                        max(1, int(WALK_DAYS_MAX)))
            return value if value != GAME_WALK_DAYS else None
        if kind == "coach":
            value = min(scaled(int(COACH_DAYS), hops, HOP_ADD_DAYS),
                        max(1, int(COACH_DAYS_MAX)))
            return value if value != GAME_COACH_DAYS else None
        return None

    def fare_for(hops=0):
        """その移動の実効の馬車代（設定値に距離補正を乗せたもの）。"""
        return scaled(int(COACH_PRICE), hops, HOP_ADD_FARE)

    def name_of(kind):
        return WALK_NAME if kind == "walk" else COACH_NAME if kind == "coach" else "?"

    def values_for(window):
        """テンプレートに渡す変数一式。日数と料金は「変えていなければ素の値」。"""
        kind = window.get("kind")
        hops = window.get("hops") or 0
        limit = days_limit(kind, hops)
        if limit is None:
            limit = GAME_WALK_DAYS if kind == "walk" else GAME_COACH_DAYS
        fare = window.get("fare")
        price = fare if fare is not None and fare != GAME_COACH_PRICE else \
            (state["game_price"] or GAME_COACH_PRICE)
        return {
            "name": name_of(kind),
            "target": window.get("target_name") or "目的地",
            "origin": window.get("origin_name") or "出発地",
            "days": limit,
            "price": price,
            "hops": hops,
        }

    def set_gold(app, value):
        """所持金を書く。型を保つ（`901_` と同じ。float の世界に int を混ぜない）。"""
        player = getattr(app, "player", None)
        current = getattr(player, "gold", None)
        player.gold = float(value) if isinstance(current, float) else int(round(value))

    # ============================================================ ボタンの表示
    def relabel(kind, old, hops=0):
        """新しいラベル。触らないなら None。

        徒歩: 実効日数が素の値と違うときだけテンプレートで表示し直す。
        素の値のままならゲームの実表示（`3ヵ月`）を尊重し、呼び名だけ差し替える。

        馬車: 常にテンプレートで表示し直す。
        素のゲームのラベルは `馬車(1000G)` で所要日数が読めないため、
        既定でも日数を足す（表示だけの例外。挙動は従来どおり、
        変えた項目にしか触らない）。
        日数が素のままのときに出す数は実測の素の値（`GAME_COACH_DAYS`）。
        `elapse_days` で実際に進む数と同じ。

        `hops` はこの確認画面の行き先までの挟む街の数（`road_hops`）。
        距離補正が乗ると実効値が変わるので、表示もその値になる。
        """
        if kind == "walk":
            days = days_limit("walk", hops)
            if days is not None:
                return fmt(WALK_BUTTON, name=WALK_NAME, days=days)
            if WALK_NAME != "徒歩" and "徒歩" in old:
                return old.replace("徒歩", WALK_NAME)
            return None
        if kind == "coach":
            days = days_limit("coach", hops)
            if days is None:
                days = GAME_COACH_DAYS
            fare = fare_for(hops)
            parsed = ui.parse_coin(old)
            shown_price = fare if fare != GAME_COACH_PRICE else \
                (parsed if parsed is not None else GAME_COACH_PRICE)
            new = fmt(COACH_BUTTON, name=COACH_NAME, price=shown_price,
                      days=days)
            # 同じ文字列なら None。
            # ラベルを組み直さないビルドで自分の書いたラベルにもう一度来ても、
            # 塗り直しを繰り返さない。
            return new if new != old else None
        return None

    @ctx.wrap("__main__:AreaMoveCofirmation.update_button_display", required=False,
              safe=True)
    def confirmation_buttons(orig, self, *args, **kwargs):
        """徒歩・馬車が並び終えた後、ラベルの `text` だけを書き直す。

        素の運賃はここで（書き換える前のラベルから）読み取って控える。
        描画はもう済んでいるので、変えたときだけ次のフレームで塗り直す（`307_` と同じ。
        二度目は同じ文字列になるので塗り直しは繰り返さない）。
        """
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is None:
                return result
            options = move_options(getattr(app, "buttons", None))
            if not options:
                return result
            # 確認画面は1つの行き先の徒歩・馬車が並ぶので、挟む街の数は1回でよい。
            origin_id = ui.area_id_of(ui.current_area(app))
            target_id = str(options[0][1][0])
            hops = road_hops(app, origin_id, target_id)
            if hops:
                write("hops: {} settlement(s) between {} and {} "
                      "(a road opened by 325_)".format(hops, origin_id, target_id))
            changed = False
            for entry, argv in options:
                kind = kind_of_mode(argv[1])
                old = entry.get("text") or ""
                if kind is None:
                    write("unknown mode {!r} on {!r}; leaving it alone "
                          "(the game may have changed its vocabulary)".format(
                              argv[1], old))
                    continue
                if kind == "coach" and old != state["our_coach_label"]:
                    price = ui.parse_coin(old)
                    if price is not None:
                        state["game_price"] = price
                new = relabel(kind, old, hops)
                if new and new != old:
                    entry["text"] = new
                    changed = True
                    if kind == "coach":
                        state["our_coach_label"] = new
                    write("label: {!r} -> {!r} ({})".format(old, new, kind))
            if changed:
                screen.apply_buttons(app, None, "relabel")
        except Exception:
            ctx.log_exc("area move custom: cannot relabel the buttons")
        return result

    # ============================================================ 手持ちの確認
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """設定した運賃に手持ちが満たないときは、押された時点で断る。

        ゲームのボタンは触らず押下だけ握る。
        確認画面はそのまま残るので、徒歩を選び直せる。
        読めなかったときは通す（値が読めないことを理由に遊びを止めない。
        `307_` の体力の断り方と同じ）。
        """
        try:
            entry = ui.pressed_entry(self, button_index)
            if isinstance(entry, dict) \
                    and ui.spec_cls_name(entry) == "AreaMoveManager":
                argv = ui.spec_args(entry)
                if argv and len(argv) >= 2 and kind_of_mode(argv[1]) == "coach":
                    hops = road_hops(self, ui.area_id_of(ui.current_area(self)),
                                     str(argv[0]))
                    fare = fare_for(hops)
                    if fare != GAME_COACH_PRICE:
                        gold = ui.gold_of(self)
                        if gold is not None and gold < fare:
                            write("refused: fare {} (hops={}) > gold {}".format(
                                fare, hops, gold))
                            screen.say(self, fmt(REFUSE_TEXT, name=COACH_NAME,
                                                 price=fare, gold=gold))
                            return None
        except Exception:
            ctx.log_exc("area move custom: fare check failed")
        return orig(self, button_index, *args, **kwargs)

    # ============================================================ 移動の窓
    @ctx.wrap("__main__:AreaMoveManager.__init__", required=False, safe=True)
    def area_move_init(orig, self, app, target_area_id, mode, *args, **kwargs):
        """`execute` には引数が来ないので、行き先と手段をここで自分に控える。

        自分専用の属性名で持つだけで、ゲームのデータには何も書かない（マネージャはセーブに入らない。セーブに入るのは
        PhaseSpec のほう）。
        """
        result = orig(self, app, target_area_id, mode, *args, **kwargs)
        try:
            self._mod_area_move_custom = {"target_id": str(target_area_id),
                                          "mode": str(mode)}
        except Exception:
            pass
        return result

    def _open_window(app, info):
        target_id = info.get("target_id")
        target = ui.world_areas(app).get(target_id) if app is not None else None
        origin = ui.current_area(app)
        kind = kind_of_mode(info.get("mode"))
        hops = road_hops(app, ui.area_id_of(origin), target_id)
        return {
            "kind": kind,
            "mode": info.get("mode"),
            "target_id": target_id,
            "target_name": getattr(target, "name", None) or "",
            "origin_name": getattr(origin, "name", None) or "",
            "hops": hops,        # 325_ が開いた道なら挟む街の数（それ以外は 0）
            "fare": fare_for(hops),          # 実効の馬車代（距離補正込み）
            "days_limit": days_limit(kind, hops),   # 実効日数。None=触らない
            "spent": 0,          # この移動で elapse_days に渡した日数の合計
            "gold_before": None,
            "prepaid": None,     # 前払い調整の後の所持金（調整したときだけ入る）
            "game_price": GAME_COACH_PRICE,
        }

    def settle_fare(app, window):
        """移動が終わった時点の帳尻。

        前払い調整が入っていれば、
        ゲームの引き落としは既にちょうど設定額になっている。
        ここで見るのは**引き落としが本当に起きたか**だけ: 起きていなければ（弾かれた・別の徴収方式のビルド）、
        前払いで積んだ差額をそのまま返す。
        想定外の値になっていたら触らずに
        WARN を残す（移動の中で料金以外の入金があった場合を壊さないため）。
        """
        before = window.get("gold_before")
        prepaid = window.get("prepaid")
        fare = window.get("fare")
        if window.get("kind") != "coach" or fare in (None, GAME_COACH_PRICE) \
                or before is None or prepaid is None:
            return
        after = ui.gold_of(app)
        arrived = ui.area_id_of(ui.current_area(app)) == window.get("target_id")
        if after is None:
            write("WARN fare: cannot re-read the gold; leaving it as is")
            return
        expected = before - int(fare)
        if after == expected:
            write("fare: charged {} in one deduction; gold {} -> {} (arrived={})"
                  .format(int(fare), before, after, arrived))
        elif after == prepaid:
            set_gold(app, before)
            write("fare: the game did not charge; gold back to {} (arrived={})"
                  .format(before, arrived))
        else:
            write("WARN fare: gold ended at {} (adjusted {}, expected {}); "
                  "leaving it as is (arrived={})".format(
                      after, prepaid, expected, arrived))

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False)
    def area_move_execute(orig, self, choice_text=None, *args, **kwargs):
        """移動の間だけ窓を開ける。日数と文言の差し替えはこの窓の中だけ。

        料金を変えているときは、ゲームが引き落とす前に「素の運賃
        − 設定額」のぶんだけ所持金をずらしておく（前払い調整）。
        ゲームは素の運賃(1000)を 1回引くだけなので、差し引きはちょうど設定額。
        「1000引いて500返す」のような紛らわしい動きを画面に出さない（実機で踏んだ。
        引いてから返す方式だと、支払い直後の所持金表示が1000引かれた値のまま残る）。
        ずらした直後に描画は走らないので、増えた瞬間が画面に見えることもない。
        """
        app = getattr(self, "app", None) or ui.find_app()
        window = None
        try:
            info = getattr(self, "_mod_area_move_custom", None) or {}
            window = _open_window(app, info)
            window["gold_before"] = ui.gold_of(app)
            window["game_price"] = state["game_price"] or GAME_COACH_PRICE
            write("move: kind={} mode={!r} {!r} -> {!r} days_limit={} hops={} "
                  "fare={} gold={}".format(
                      window["kind"], window["mode"], window["origin_name"],
                      window["target_name"], window["days_limit"],
                      window["hops"], window["fare"], window["gold_before"]))
            state["window"] = window
        except Exception:
            ctx.log_exc("area move custom: cannot open the move window")
            window = None
        # 前払い調整は窓が立ってから最後に行う。
        # `prepaid` を入れるのは所持金を実際に書けた後。
        # ここで何が起きても `finally` の帳尻が見る。
        # 手持ちが実効額以上なら、調整後は必ず素の運賃以上になるので、
        # ゲームの残高チェックには弾かれない（実効額未満は押された時点で断っている）。
        if window is not None and window["kind"] == "coach" \
                and window["fare"] != GAME_COACH_PRICE \
                and window["gold_before"] is not None:
            try:
                pre = window["gold_before"] + window["game_price"] \
                    - int(window["fare"])
                set_gold(app, pre)
                window["prepaid"] = pre
                write("fare: gold {} -> {} before the game charges {} "
                      "(ours is {}; one deduction, no refund)".format(
                          window["gold_before"], pre, window["game_price"],
                          int(window["fare"])))
            except Exception:
                ctx.log_exc("area move custom: cannot pre-adjust the fare")
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            state["window"] = None
            if window is not None:
                try:
                    settle_fare(app, window)
                except Exception:
                    ctx.log_exc("area move custom: cannot settle the fare")

    # ============================================================ 日数
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False)
    def elapse_days(orig, self, days, *args, **kwargs):
        """移動の窓の間だけ、渡る日数の合計を設定値に合わせる。

        渡す数を差し替えるだけで、
        暦の進め方も日次処理もゲームのまま（`orig` は必ず呼ぶ）。
        窓の外では1バイトも触らない。
        """
        try:
            window = state["window"]
            if window is not None and isinstance(days, (int, float)) \
                    and not isinstance(days, bool) and days > 0:
                limit = window.get("days_limit")
                if limit is not None:
                    remaining = max(0, int(limit) - window["spent"])
                    raw = GAME_WALK_DAYS if window.get("kind") == "walk" \
                        else GAME_COACH_DAYS
                    if int(days) == raw:
                        # ゲームが素の値をそのまま渡してきた（観測ではこの1回で
                        # 移動の全日数）。予算へ丸ごと置き換える。min では
                        # 距離補正で**増やす**方向（90 -> 270）が効かない。
                        granted = remaining
                    else:
                        # 素の値と違う数は、外側（`307_` の予算）が既に減らした
                        # 可能性がある。増やす方向には触らず、頭打ちだけ掛ける。
                        granted = max(0, min(int(days), remaining))
                    window["spent"] += granted
                    if granted != days:
                        write("days: {} -> {} ({} spent {}/{})".format(
                            days, granted, window.get("kind"),
                            window["spent"], limit))
                    return orig(self, granted, *args, **kwargs)
                window["spent"] += int(days)
        except Exception:
            ctx.log_exc("area move custom: cannot adjust the days")
        return orig(self, days, *args, **kwargs)

    # ============================================================ 文言
    def reword(window, text):
        """移動中の文言の置き換え。触らないなら None。

        名指しの手掛かりに当たった文言だけをテンプレートへ。
        待機表示の点は触らない。
        当たらなかった文言はログに残す（馬車側の実文言を知るため）。
        """
        if not text.strip() or not text.strip(".。 　"):
            return None                      # 待機表示の点
        kind = window.get("kind")
        if any(mark in text for mark in DEPART_MARKS):
            template = WALK_DEPART_TEXT if kind == "walk" else \
                COACH_DEPART_TEXT if kind == "coach" else ""
            if template:
                return fmt(template, **values_for(window))
            return None
        if any(mark in text for mark in ARRIVE_MARKS):
            if ARRIVE_TEXT:
                return fmt(ARRIVE_TEXT, **values_for(window))
            return None
        write("text passing through (no mark matched): {!r}".format(text))
        return None

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context=None, *args, **kwargs):
        try:
            window = state["window"]
            if window is not None and isinstance(context, str):
                replaced = reword(window, context)
                if replaced is not None and replaced != context:
                    write("text: {!r} -> {!r}".format(context, replaced))
                    return orig(self, replaced, *args, **kwargs)
        except Exception:
            ctx.log_exc("area move custom: cannot reword the travel text")
        return orig(self, context, *args, **kwargs)

    # ------------------------------------------------------------ 自己検証
    # 実経路はエリア移動を1回するまで通らない。
    # ラベルの読み書きだけは作ったデータで先に確かめておく（`103_` /
    # `215_` と同じ方針）。
    # 通貨の表記は `130_` が差し替えていることがあるので、
    # 見本のほうも同じ表記へ通してから突き合わせる。
    parsed = ui.parse_coin(ui.rewrite_coins("馬車(1,000G)"))
    sample = fmt(COACH_BUTTON, name="馬車", price=1000, days=7)
    survives = fmt("{name}と{typo}", name="徒歩")
    expected = ui.rewrite_coins("馬車(1000G・7日)")
    # 距離補正の式。設定と無関係に確かめる（mode= / factor= を明示で渡す）。
    grown = (scaled(90, 2, 7, mode="multiply", factor=1.0),
             scaled(90, 2, 7, mode="add"),
             scaled(1000, 3, 500, mode="add"),
             scaled(90, 2, 7, mode="off"),
             scaled(90, 0, 7, mode="multiply", factor=1.0))
    if parsed == 1000 and sample == expected and survives == "徒歩と{typo}" \
            and grown == (270, 104, 2500, 90, 90):
        ctx.log("verified: reads the fare from a label, formats templates, "
                "and scales by hops")
    else:
        ctx.log("VERIFY FAILED: parsed={!r} sample={!r} survives={!r} grown={!r}"
                .format(parsed, sample, survives, grown), level="ERROR")

    ctx.log("area move custom: walk={}d coach={}d fare={} names={}/{} "
            "hops={}(x{}/+{}d+{}G) log={}".format(
                WALK_DAYS, COACH_DAYS, COACH_PRICE, WALK_NAME, COACH_NAME,
                HOP_SCALING, HOP_FACTOR, HOP_ADD_DAYS, HOP_ADD_FARE, log_path))
