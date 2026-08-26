# -*- coding: utf-8 -*-
"""機能調整: エリア移動（土地から土地へ）の日数・料金・文言を設定で変えられるようにする。

素のゲームの移動手段は `徒歩(3ヵ月)` と `馬車(1000G)` の2つで、
日数も料金も文言も固定されている（素の値・`mode` の実値・文言の実測は GAME.md
§2.18）。
この MOD はその3つを mod.json の設定から変えられるようにする。
既定値はすべて素のゲームの値で、そのままなら挙動は何も変わらない。

変更点は4つ。

| 何を変えるか | どこで変えるか |
|---|---|
| ボタンの表示 | `AreaMoveCofirmation.update_button_display` の後で `text` だけ書き換える |
| 経過する日数 | `AreaMoveManager.execute` の間だけ `InstantaleApp.elapse_days` に渡る数を差し替える |
| 移動中の文言 | 同じ窓の間だけ `InstantaleApp.add_text` の文言をテンプレートへ置き換える |
| 馬車の料金 | ゲームが引き落とす前に差額ぶん所持金をずらす（前払い調整） |

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

# 手持ちが設定した運賃に足りないときの一言。
REFUSE_TEXT = "（{name}代{price}Gに足りない ― 手持ち{gold}G）"

# 手掛かりに当たらなかった移動中の文言をログに残す上限（1回の移動あたり）。
UNMATCHED_LOG_LIMIT = 8


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

    def days_limit(kind):
        """その手段に設定で変えられた日数。素の値のまま（触らない）なら None。"""
        if kind == "walk" and int(WALK_DAYS) != GAME_WALK_DAYS:
            return max(0, int(WALK_DAYS))
        if kind == "coach" and int(COACH_DAYS) != GAME_COACH_DAYS:
            return max(0, int(COACH_DAYS))
        return None

    def fare_changed():
        """馬車の料金が設定で変えられているか。素の 1000G のままなら触らない。"""
        return int(COACH_PRICE) != GAME_COACH_PRICE

    def name_of(kind):
        return WALK_NAME if kind == "walk" else COACH_NAME if kind == "coach" else "?"

    def values_for(window):
        """テンプレートに渡す変数一式。日数と料金は「変えていなければ素の値」。"""
        kind = window.get("kind")
        limit = days_limit(kind)
        if limit is None:
            limit = GAME_WALK_DAYS if kind == "walk" else GAME_COACH_DAYS
        price = int(COACH_PRICE) if fare_changed() else \
            (state["game_price"] or GAME_COACH_PRICE)
        return {
            "name": name_of(kind),
            "target": window.get("target_name") or "目的地",
            "origin": window.get("origin_name") or "出発地",
            "days": limit,
            "price": price,
        }

    def set_gold(app, value):
        """所持金を書く。型を保つ（`901_` と同じ。float の世界に int を混ぜない）。"""
        player = getattr(app, "player", None)
        current = getattr(player, "gold", None)
        player.gold = float(value) if isinstance(current, float) else int(round(value))

    # ============================================================ ボタンの表示
    def relabel(kind, old):
        """新しいラベル。触らないなら None。

        徒歩: 日数を変えたときだけテンプレートで表示し直す。
        素の値のままならゲームの実表示（`3ヵ月`）を尊重し、呼び名だけ差し替える。

        馬車: 常にテンプレートで表示し直す。
        素のゲームのラベルは `馬車(1000G)` で所要日数が読めないため、
        既定でも日数を足す（表示だけの例外。挙動は従来どおり、
        変えた項目にしか触らない）。
        日数が素のままのときに出す数は実測の素の値（`GAME_COACH_DAYS`）。
        `elapse_days` で実際に進む数と同じ。
        """
        if kind == "walk":
            days = days_limit("walk")
            if days is not None:
                return fmt(WALK_BUTTON, name=WALK_NAME, days=days)
            if WALK_NAME != "徒歩" and "徒歩" in old:
                return old.replace("徒歩", WALK_NAME)
            return None
        if kind == "coach":
            days = days_limit("coach")
            if days is None:
                days = GAME_COACH_DAYS
            parsed = ui.parse_coin(old)
            shown_price = int(COACH_PRICE) if fare_changed() else \
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
                new = relabel(kind, old)
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
            if fare_changed():
                entry = ui.pressed_entry(self, button_index)
                if isinstance(entry, dict) \
                        and ui.spec_cls_name(entry) == "AreaMoveManager":
                    argv = ui.spec_args(entry)
                    if argv and len(argv) >= 2 \
                            and kind_of_mode(argv[1]) == "coach":
                        gold = ui.gold_of(self)
                        if gold is not None and gold < int(COACH_PRICE):
                            write("refused: fare {} > gold {}".format(
                                int(COACH_PRICE), gold))
                            screen.say(self, fmt(REFUSE_TEXT, name=COACH_NAME,
                                                 price=int(COACH_PRICE), gold=gold))
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
        return {
            "kind": kind_of_mode(info.get("mode")),
            "mode": info.get("mode"),
            "target_id": target_id,
            "target_name": getattr(target, "name", None) or "",
            "origin_name": getattr(origin, "name", None) or "",
            "spent": 0,          # この移動で elapse_days に渡した日数の合計
            "gold_before": None,
            "prepaid": None,     # 前払い調整の後の所持金（調整したときだけ入る）
            "game_price": GAME_COACH_PRICE,
            "unmatched": 0,
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
        if window.get("kind") != "coach" or not fare_changed() \
                or before is None or prepaid is None:
            return
        after = ui.gold_of(app)
        arrived = ui.area_id_of(ui.current_area(app)) == window.get("target_id")
        if after is None:
            write("WARN fare: cannot re-read the gold; leaving it as is")
            return
        expected = before - int(COACH_PRICE)
        if after == expected:
            write("fare: charged {} in one deduction; gold {} -> {} (arrived={})"
                  .format(int(COACH_PRICE), before, after, arrived))
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
            write("move: kind={} mode={!r} {!r} -> {!r} days_limit={} gold={}".format(
                window["kind"], window["mode"], window["origin_name"],
                window["target_name"], days_limit(window["kind"]),
                window["gold_before"]))
            state["window"] = window
        except Exception:
            ctx.log_exc("area move custom: cannot open the move window")
            window = None
        # 前払い調整は窓が立ってから最後に行う。
        # `prepaid` を入れるのは所持金を実際に書けた後。
        # ここで何が起きても `finally` の帳尻が見る。
        # 手持ちが設定額以上なら、調整後は必ず素の運賃以上になるので、
        # ゲームの残高チェックには弾かれない（設定額未満は押された時点で断っている）。
        if window is not None and window["kind"] == "coach" \
                and fare_changed() and window["gold_before"] is not None:
            try:
                pre = window["gold_before"] + window["game_price"] \
                    - int(COACH_PRICE)
                set_gold(app, pre)
                window["prepaid"] = pre
                write("fare: gold {} -> {} before the game charges {} "
                      "(ours is {}; one deduction, no refund)".format(
                          window["gold_before"], pre, window["game_price"],
                          int(COACH_PRICE)))
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
                limit = days_limit(window.get("kind"))
                if limit is not None:
                    granted = max(0, min(int(days), limit - window["spent"]))
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
        if window["unmatched"] < UNMATCHED_LOG_LIMIT:
            window["unmatched"] += 1
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
    if parsed == 1000 and sample == expected and survives == "徒歩と{typo}":
        ctx.log("verified: reads the fare from a label and formats templates")
    else:
        ctx.log("VERIFY FAILED: parsed={!r} sample={!r} survives={!r}".format(
            parsed, sample, survives), level="ERROR")

    ctx.log("area move custom: walk={}d coach={}d fare={} names={}/{} log={}".format(
        WALK_DAYS, COACH_DAYS, COACH_PRICE, WALK_NAME, COACH_NAME, log_path))
