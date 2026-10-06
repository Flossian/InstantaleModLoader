# -*- coding: utf-8 -*-
"""店で盗む。売買画面で店の品を右クリックし、「購入」か「盗む」を選ぶ。仕様は DOC.md「店で盗む」。

- 器用で抜き取り、しくじったら判断で店主の視線に気づけば手を引ける。
  気づけなければゲーム自身の衛兵戦。回数の制限は無く、同じ店で同じ日に続けるほど、
  大きい品・高い品・レア度の高い品ほど抜き取りにくい
- 盗んだ品は、盗んだ店では売れない（売ろうとすると主人公が自分で気づいて取りやめる）。どの店から盗んだかは、
  手持ちの品の鍵（ゲームの採番の数。GAME.md §2.13.1）ごとに控える（`stolen`）。手放した品の控えは売り買いのときに捨てる
- よその店では盗品の買い取り額が正規の半分、裏の事務所の盗品買取商（`fence`）では7割。
  額は 336 の値段の段（`law` が置く1枚の中。`env.price_layers`）で `売価` に掛ける。盗品買取商は盗品しか買わない
- 版1の途中までは施設の選択肢の「盗みを働く」だった（棚から無作為に1つ、同じ店は1日1回）。
  売買画面の右クリックへ移すので外した（セーブに焼かれた残骸を消すためラベルだけ残す。`office.OUR_LABELS`）
"""
from instantale_modloader import frames, items, ui

from . import rules

#: 外した施設の選択肢。セーブに焼かれた残骸を `prune_stale` で消すためだけに残す。
THEFT_LABEL = "盗みを働く"
#: 店だと見なす選択肢（`Facility.choices` の鍵。GAME.md §2.20 の役場の項）。
SHOP_CHOICE = "売買する"
#: MOD が持つ施設の主（`331_` の店など）。自分の店から盗む話にしない。
MOD_NPC_PREFIX = "mod:"
THEFT_SUCCESS_TEXT = "店主の目を盗み、{item}を懐に滑り込ませた。"
THEFT_ABORT_TEXT = "店主の視線がこちらへ向いた。気取られる前に手を引っ込めた。"
THEFT_CAUGHT_TEXT = "「泥棒だ！」店主の叫びを聞きつけ、衛兵が駆けつけてきた。"
THEFT_NO_ROOM_TEXT = "めぼしい品はあるが、これ以上は持ちきれない。"
THEFT_FAILED_TEXT = "（品を懐へ移せなかった）"
#: 売買画面で店の品を右クリックしたときのボタン。
BUY_LABEL = "購入"
STEAL_LABEL = "盗む"
STEAL_CONFIRM_TEXT = "{item}を盗む\n成功率 {hand}%　被発見率 {caught}%"
STEAL_YES = "はい"
STEAL_NO = "いいえ"
#: 盗んだ店に売ろうとしたとき。主人公が自分で気づいて取りやめる形（店主には言わせない）。
REFUSE_TEXT = "……いや、{item}はこの店で盗んだ品だ。\nここで売れば、盗んだことがばれてしまう。"
#: 盗品買取商に盗品でない品を売ろうとしたとき。
FENCE_REFUSE_TEXT = "{broker}は{item}を押し返した。\n「堅気の品なら表の店へ持っていきな」"
#: 所持品のマスの単位（64px ＋ 隙間 1px。ゲームはこの固定の単位で置き、落とした座標も同じ単位で割る。GAME.md §2.13.3）。
CELL_PX = 65
#: 売買の窓の表示の状態を持つ HUD の辞書（`turnoff_window_visibility` の2つ目の引数）。
TWIN_WINDOW_DATA = "visible_twin_inventory_data"
#: 借りる確認の窓を探す間隔（秒）。ゲームは落とした後のフレームで窓を出す。
PANEL_WAITS = (0.05, 0.1, 0.2, 0.4)


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    refresh_gold, current_facility = env.refresh_gold, env.current_facility
    call_guards = env.call_guards

    # -------------------------------------------------- 店で盗む
    # 売買画面で店の品を右クリックし、「購入」か「盗む」を選ぶ（DOC.md「店で盗む」）。判定は2段:
    #   器用で抜き取れるか → 成功ならその品が手に入る（何も起きない）
    #   しくじったら判断で店主の視線に気づけるか → 気づけば手を引く（何も起きない）、
    #                                              気づけなければ見咎められ、窓を閉じてゲーム自身の衛兵戦
    # 回数の制限は無い。代わりに、同じ店で同じ日に続けるほど・品が大きく高くレア度が高いほど
    # 抜き取りにくい（`rules.theft_penalties`）。判断の側は品で変わらない。
    # 棚の品は店主の持ち物そのもの（GAME.md §2.13.1）。装備はゲームが作り直さないので、盗めば棚から消える。
    #
    # 売買の窓の仕組み（`227_` 版4 の実機。GAME.md §2.13.1）:
    #   品を落とす → ゲームが確認の窓（品:額G / 買う・キャンセル）を出す
    #   → 押すと `InventoryItem.buy_item` → `Item.buy`（ここでお金が動く）→ `change_inventory`（品が移る）
    # 購入はこの流れにそのまま乗せる（擬似のドラッグで品を手持ちへ落とし、確認の窓はゲームが出す）。
    # 盗むは確認の窓を通さず、`Item.buy` で持ち主を切り替えて払った額を両側へ戻し、
    # `333_` と同じ手順（`clear_current_slots` → `place_new_item` → `change_inventory`）で手持ちへ移す。
    def shop_of(app):
        """いま居る店の `(施設, 主の id, 主)`。店でなければ None。"""
        facility = current_facility(app)
        choices = getattr(facility, "choices", None)
        if facility is None or not isinstance(choices, dict) or SHOP_CHOICE not in choices:
            return None
        owner_id = getattr(facility, "owner", None)
        if owner_id is None or str(owner_id).startswith(MOD_NPC_PREFIX):
            return None
        keeper = ui.character_of(app, str(owner_id))
        return (facility, str(owner_id), keeper) if keeper is not None else None

    def shop_key(app, facility):
        return "{}/{}".format(ui.area_id_of(ui.current_area(app)), getattr(facility, "id", ""))

    def attempts_today(app, key):
        """その店で今日すでに試した回数（持ちきれなかった回は数えない）。"""
        day = ui.game_day(app)
        with worlds.lock:
            bucket = worlds.load(worlds.playthrough(app))
            entry = (bucket.get("theft") or {}).get(key)
        if day is None or not isinstance(entry, dict) or entry.get("day") != day:
            return 0
        count = entry.get("count")
        return count if isinstance(count, int) and not isinstance(count, bool) else 0

    def count_attempt(app, key):
        day = ui.game_day(app)
        if day is None:
            return
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            # 前の日の控えは要らないので、今日の分だけ残す（繰り返し遊んでも増えない）。
            theft = {k: v for k, v in (bucket.get("theft") or {}).items()
                     if isinstance(v, dict) and v.get("day") == day}
            entry = theft.get(key) or {"day": day, "count": 0}
            entry["count"] = int(entry.get("count") or 0) + 1
            theft[key] = entry
            bucket["theft"] = theft
            worlds.save(playthrough)

    def key_in(inventory, item):
        """持ち物の辞書の中でこの品を指す鍵。無ければ None。"""
        for key, value in (inventory or {}).items():
            if value is item:
                return key
        return None

    def odds(app, item_key):
        """棚の品1つを盗む確率。`{"hand", "sense", "caught", "base", "penalties", "dex", "wis", "name"}`。

        店でない・棚にその品が無ければ None。確認の画面に出すのも、判定に使うのもこの値。
        """
        found = shop_of(app)
        if found is None:
            return None
        facility, _owner_id, keeper = found
        shelf = items.inventory_of(keeper) or {}
        record = items.to_dict(shelf.get(item_key)) if item_key in shelf else None
        if record is None:
            return None
        player = getattr(app, "player", None)
        dex = ui.ability_score(player, "dexterity")
        wis = ui.ability_score(player, "wisdom")
        penalties = rules.theft_penalties(
            record, [items.to_dict(item) or {} for item in shelf.values()],
            attempts_today(app, shop_key(app, facility)), cfg.THEFT_STREAK_STEP, cfg.THEFT_CELL_STEP,
            cfg.THEFT_PRICE_STEP, cfg.THEFT_PRICE_CAP, cfg.THEFT_RARITY_STEP)
        base = rules.ability_chance(dex, cfg.THEFT_BASE_PCT, cfg.THEFT_PER_POINT, cfg.THEFT_MAX_PCT)
        hand = rules.theft_chance(base, penalties)
        sense = rules.ability_chance(wis, cfg.THEFT_BASE_PCT, cfg.THEFT_PER_POINT, cfg.THEFT_MAX_PCT)
        return {"hand": hand, "sense": sense, "caught": rules.caught_chance(hand, sense),
                "base": base, "penalties": penalties, "dex": dex, "wis": wis,
                "name": record.get("name") or "品"}

    # ---- 売買の窓の部品（ゲーム自身のウィジェットを使う）
    def player_grid_of(app, widget):
        """売買の窓の手持ちの側のグリッド（品のウィジェットと同じ親に居る）。"""
        player = getattr(app, "player", None)
        for child in list(getattr(getattr(widget, "parent", None), "children", None) or []):
            if (frames.attr(child, "place_new_item", None) is not None
                    and frames.attr(child, "obtainer", None) is player):
                return child
        return None

    def slots_of(widget):
        item = getattr(widget, "item_instance", None)
        return (max(1, int(getattr(item, "width_slots", 1) or 1)),
                max(1, int(getattr(item, "height_slots", 1) or 1)))

    def free_cell(grid, width, height):
        """手持ちのグリッドの空き（左上から）。`(列, 行)`。行は下から（ゲームの `is_valid_placement` の向き）。"""
        cols = frames.attr(grid, "cols", None)
        rows = frames.attr(grid, "rows", None)
        if not isinstance(cols, int) or not isinstance(rows, int):
            return None
        for gy in range(rows - height, -1, -1):
            for gx in range(0, cols - width + 1):
                try:
                    if grid.is_valid_placement(gx, gy, width, height):
                        return gx, gy
                except Exception:
                    return None
        return None

    #: 盗みの間だけ本文を止める（`Item.buy` の購入の文）。
    muted = {"buy": False}

    def take(app, widget, keeper):
        """盗んだ品を手持ちへ移す。`(品の名前, 理由)`。

        持ち主の切り替えは購入と同じ `Item.buy` に任せ（何を書き換えるかはゲームが知っている）、
        払った額を主人公と店主の両方へ戻す。置き場所はゲームの `place_new_item` が決める。
        """
        app_player = getattr(app, "player", None)
        item = getattr(widget, "item_instance", None)
        shelf = items.inventory_of(keeper)
        grid = player_grid_of(app, widget)
        shop_grid = getattr(widget, "inventory", None)
        if item is None or grid is None or shelf is None or key_in(shelf, item) is None:
            return None, "not on the shelf"
        width, height = slots_of(widget)
        if free_cell(grid, width, height) is None:
            return None, "no room"
        name = getattr(item, "name", None) or "品"
        gold, keeper_gold = getattr(app_player, "gold", None), getattr(keeper, "gold", None)
        # `Item.buy` は本文に「〈品〉を購入した。」も出す（実機）。盗みなので、その間の本文は止める。
        muted["buy"] = True
        try:
            item.buy()
        except Exception:
            ctx.log_exc("crime incentive: Item.buy failed while stealing")
        finally:
            muted["buy"] = False
        paid = (gold - app_player.gold) if isinstance(gold, int) else None
        # 払った額を戻す（盗んだのでお金は動かない）。
        if isinstance(gold, int):
            app_player.gold = gold
        if isinstance(keeper_gold, int):
            keeper.gold = keeper_gold
        if getattr(item, "obtainer", None) is not app_player:
            # 所持金が足りずにゲームが断った、など。持ち主だけはこちらで合わせる。
            write("theft: Item.buy did not hand the item over (paid {}); setting the owner".format(paid))
            item.obtainer = app_player
        try:
            widget.clear_current_slots()
            grid.place_new_item(widget)
            widget.change_inventory(grid)
        except Exception:
            ctx.log_exc("crime incentive: cannot move the stolen item")
            if shop_grid is not None and key_in(items.inventory_of(app_player), item) is None:
                try:
                    shop_grid.place_new_item(widget)
                except Exception:
                    ctx.log_exc("crime incentive: cannot put the item back on the shelf")
            return None, "move failed"
        write("theft: took {!r} ({}x{}; Item.buy paid {} and was refunded)".format(
            name, width, height, paid))
        refresh_gold(app)
        return name, "taken"

    def steal(app, widget):
        """右クリックした棚の品を盗む。結果は "stole" / "no room" / "failed" / "backed off" / "caught"。"""
        found = shop_of(app)
        item = getattr(widget, "item_instance", None)
        shelf = items.inventory_of(found[2]) if found is not None else None
        item_key = key_in(shelf, item)
        chances = odds(app, item_key) if item_key is not None else None
        if found is None or chances is None:
            write("theft: not at a shop any more, or the item is gone")
            return "failed"
        facility, owner_id, keeper = found
        key = shop_key(app, facility)
        roll = cfg._RNG.random() * 100
        head = "theft: {} (owner {}) {!r} dex {} -> {}% - {} = {}% roll {:.0f}".format(
            getattr(facility, "name", "?"), owner_id, chances["name"], chances["dex"],
            chances["base"], chances["penalties"], chances["hand"], roll)
        if roll < chances["hand"]:
            name, why = take(app, widget, keeper)
            if why == "no room":
                write(head + " -> no room; the attempt is not counted")
                screen.say(app, THEFT_NO_ROOM_TEXT)
                return "no room"
            count_attempt(app, key)
            if name is None:
                write(head + " -> WARN {}".format(why))
                screen.say(app, THEFT_FAILED_TEXT)
                return "failed"
            write(head + " -> stole")
            remember_stolen(app, item, key, name)
            screen.say(app, THEFT_SUCCESS_TEXT.format(item=name))
            return "stole"
        count_attempt(app, key)
        roll2 = cfg._RNG.random() * 100
        if roll2 < chances["sense"]:
            write(head + " -> fumbled; wis {} -> {}% roll {:.0f} -> backed off".format(
                chances["wis"], chances["sense"], roll2))
            screen.say(app, THEFT_ABORT_TEXT)
            return "backed off"
        write(head + " -> fumbled; wis {} -> {}% roll {:.0f} -> caught".format(
            chances["wis"], chances["sense"], roll2))
        screen.say(app, THEFT_CAUGHT_TEXT)
        return "caught"

    def close_trade_window(app):
        """売買の窓を閉じる。外側を押したときと同じ `turnoff_window_visibility(instance, 窓の表示の辞書)` → 後始末。

        2つ目の引数は窓の表示の状態を持つ辞書そのもの（売買の窓は `visible_twin_inventory_data`。
        外側を押したときにゲームが渡したものを実機で写した。2026-10-04）。
        """
        hud = ui.find_hud(app)
        close = getattr(hud, "turnoff_window_visibility", None)
        target = getattr(hud, TWIN_WINDOW_DATA, None)
        if not callable(close) or not isinstance(target, dict):
            write("WARN theft: cannot close the trade window (turnoff {} target {})".format(
                callable(close), type(target).__name__))
            return False
        try:
            close(hud, target)
            # 外側を押したときはこの後に HUD の後始末（`on_backdrop_callback` = `app.on_close_window`）が走り、
            # 取引の終わり（「取引を終了した。」・店の選択肢・`is_popup_window_opened` を下ろす）を済ませる。
            # 窓を消すだけだと旗が残り、衛兵を呼ぶ前の待ちが切れた（2026-10-04 の実機）。
            after = getattr(hud, "on_backdrop_callback", None)
            if callable(after):
                after()
        except Exception:
            ctx.log_exc("crime incentive: cannot close the trade window")
            return False
        return True

    def caught(app):
        if not close_trade_window(app):
            # 窓を閉じられないまま衛兵戦を始めると、後で窓を閉じたときにゲームが店の選択肢を戻し、
            # 戦闘のボタンが消える（2026-10-04 の実機）。閉じられなければ衛兵は呼ばない。
            write("WARN theft: the window stays open; no guards this time")
            return
        # 窓が閉じ、店の選択肢が戻って手が空いてから衛兵を呼ぶ（`is_popup_window_opened` が下りるのを待つ）。
        screen.when_idle(app, lambda: call_guards(app), tag="theft guards")

    # ---- 擬似のドラッグ（購入）
    def window_point(widget, x, y):
        parent = getattr(widget, "parent", None)
        return parent.to_window(x, y) if parent is not None else (x, y)

    def drop_into(app, widget, grid):
        """店の品を手持ちのグリッドの空きへ、マウスで運んだのと同じ入力で落とす。

        ゲームの `on_touch_down` / `on_touch_move` / `on_touch_up` がそのまま走るので、
        確認の窓・値段・支払いはどれもゲーム自身のもの。こちらは指の動きを作るだけ。
        """
        from kivy.base import EventLoop
        from kivy.core.window import Window
        from kivy.input.providers.mouse import MouseMotionEvent
        width, height = slots_of(widget)
        cell = free_cell(grid, width, height)
        if cell is None:
            return "no room"
        # 押す点は品の左下のマスの中。落とす点は空きの左下のマスの同じ所（掴んだ位置を保つ）。
        dx, dy = min(CELL_PX / 2.0, widget.width / 2.0), min(CELL_PX / 2.0, widget.height / 2.0)
        start = window_point(widget, widget.x + dx, widget.y + dy)
        end = window_point(grid, grid.x + cell[0] * CELL_PX + dx, grid.y + cell[1] * CELL_PX + dy)
        try:
            w, h = Window._get_effective_size()
        except Exception:
            w, h = Window.size
        touch = MouseMotionEvent("mouse", "mod_crime_overhaul_buy",
                                 [start[0] / float(w), start[1] / float(h), "left"],
                                 is_touch=True, type_id="touch")
        EventLoop.post_dispatch_input("begin", touch)
        touch.move([end[0] / float(w), end[1] / float(h)])
        EventLoop.post_dispatch_input("update", touch)
        touch.update_time_end()
        EventLoop.post_dispatch_input("end", touch)
        pending = getattr(widget, "_has_pending_trade", None)
        shown = pending() if callable(pending) else None
        write("buy: dropped {!r} at cell {} ({:.0f},{:.0f} -> {:.0f},{:.0f}); pending trade {}".format(
            getattr(getattr(widget, "item_instance", None), "name", "?"), cell,
            start[0], start[1], end[0], end[1], shown))
        return "shown" if shown else "no confirmation"

    # ---- 右クリックのボタンと確認の画面
    menu = {"buttons": [], "unbind": None}

    def remove_menu():
        for button in menu["buttons"]:
            parent = getattr(button, "parent", None)
            if parent is not None:
                try:
                    parent.remove_widget(button)
                except Exception:
                    pass
        menu["buttons"] = []
        unbind, menu["unbind"] = menu["unbind"], None
        if callable(unbind):
            try:
                unbind()
            except Exception:
                pass

    def show_menu(app, widget, pos):
        """店の品を右クリックした位置に「購入」「盗む」を出す。ゲームは店の品に popup を出さない。"""
        if not cfg.THEFT_ENABLED:
            return
        found = shop_of(app)
        item = getattr(widget, "item_instance", None)
        if found is None or item is None or getattr(item, "obtainer", None) is not found[2]:
            return
        if player_grid_of(app, widget) is None:
            return              # 店の品でも売買の窓の外（グリッドが並んでいない）
        from kivy.core.window import Window
        remove_menu()
        template = None
        for child in list(getattr(getattr(widget, "popup_menu", None), "children", None) or []):
            if type(child).__name__ == "Button":
                template = child
                break
        try:
            x, y = float(pos[0]), float(pos[1])
        except Exception:
            x, y = float(widget.x), float(widget.top)
        for index, (label, action) in enumerate(((BUY_LABEL, "buy"), (STEAL_LABEL, "steal"))):
            button = ui.popup_button(label, template)
            button.pos = (x, y - button.height * (index + 1))
            ui.clamp_into_window(button)
            button.bind(on_release=lambda _b, action=action: menu_pressed(app, widget, action))
            Window.add_widget(button)
            menu["buttons"].append(button)

        def outside(_window, touch):
            if not any(button.collide_point(*touch.pos) for button in menu["buttons"]):
                screen.schedule(remove_menu)
            return False

        Window.bind(on_touch_down=outside)
        menu["unbind"] = lambda: Window.unbind(on_touch_down=outside)
        write("menu: {!r} at ({:.0f},{:.0f})".format(getattr(item, "name", "?"), x, y))

    def menu_pressed(app, widget, action):
        """押されたボタンの処理は次のフレームで（押下の配信の最中に別の入力を流さない）。"""
        remove_menu()
        screen.schedule(lambda: buy(app, widget) if action == "buy" else confirm_steal(app, widget))

    def buy(app, widget):
        grid = player_grid_of(app, widget)
        result = drop_into(app, widget, grid) if grid is not None else "no grid"
        if result == "no room":
            screen.say(app, THEFT_NO_ROOM_TEXT)
        elif result != "shown":
            write("WARN buy: the game did not open its confirmation ({})".format(result))

    def confirm_steal(app, widget):
        """盗む前の確認。ゲーム自身の売買の確認の窓を借りて、成功率と被発見率を出す。

        「購入」と同じ擬似のドラッグで品を手持ちへ落とし、ゲームに確認の窓（品:額G／買う・キャンセル）を出させてから、
        文言を「〈品〉を盗む／成功率・被発見率」と「はい」「いいえ」に差し替える。大きさ・位置・見た目はゲームのまま。
        - はい: ゲームの「買う」を止め（支払いを起こさない）、ゲームの「キャンセル」で品を棚へ戻してから盗む
        - いいえ: ゲームの「キャンセル」をそのまま通す
        ゲームの `ConfirmationWindow` / `ConfirmationModalView` を自分で作る形は外した。前者は親に足しても
        並ばず見えなかった（ボタンが 40×24 のまま。2026-10-04 の実機）、後者は大きさが売買の確認と違い後ろも暗くなった。
        """
        found = shop_of(app)
        item = getattr(widget, "item_instance", None)
        item_key = key_in(items.inventory_of(found[2]) if found else None, item)
        chances = odds(app, item_key) if item_key is not None else None
        if chances is None:
            write("theft: cannot read the odds of {!r}".format(getattr(item, "name", "?")))
            return
        grid = player_grid_of(app, widget)
        # 現物を握っておく（`id` だけだと、消えた物の番地を新しい窓が使い回して見落とす）。
        before = {id(node): node for node in screen_nodes()}
        result = drop_into(app, widget, grid) if grid is not None else "no grid"
        if result == "no room":
            screen.say(app, THEFT_NO_ROOM_TEXT)
            return
        if result != "shown":
            write("WARN theft: the game did not open its confirmation ({})".format(result))
            return
        # 確認の窓は落とした呼び出しの中では出ていない（同じフレームで探すと見つからなかった。2026-10-04 の実機）。
        # 次のフレームから少しずつ間を空けて探す。
        waits = list(PANEL_WAITS)

        def look():
            panel = trade_panel(before)
            if panel is not None:
                dress(app, widget, chances, panel)
                return
            if waits:
                screen.schedule(look, waits.pop(0))
                return
            fresh = ["{}:{}".format(type(node).__name__, frames.short(getattr(node, "text", ""), 20))
                     for node in screen_nodes() if before.get(id(node)) is not node][:30]
            write("WARN theft: the game's confirmation did not show up; cancelling it (new: {})".format(fresh))
            cancel = getattr(widget, "cancel_trade_confirmation", None)
            if callable(cancel):
                cancel()
        screen.schedule(look)

    def dress(app, widget, chances, panel):
        """借りた確認の窓の文言を差し替え、はい／いいえに処理を付ける。"""
        title, yes_button, no_button = panel
        title.text = STEAL_CONFIRM_TEXT.format(item=chances["name"], hand=chances["hand"],
                                               caught=int(round(chances["caught"])))
        yes_button.text, no_button.text = STEAL_YES, STEAL_NO
        write("theft: asking {!r} hand {}% (base {} - {}) sense {}% caught {:.0f}%".format(
            chances["name"], chances["hand"], chances["base"], chances["penalties"],
            chances["sense"], chances["caught"]))
        answered = {"done": False}

        def on_yes(*_args):
            if answered["done"]:
                return None
            answered["done"] = True
            write("theft: answered {!r}".format(STEAL_YES))
            # ゲームの「キャンセル」で品を棚へ戻してから（次のフレーム）盗む。
            screen.schedule(lambda: (no_button.dispatch("on_press"), screen.schedule(go)))
            return True                     # ゲームの「買う」（支払い）へは通さない

        def on_no(*_args):
            if not answered["done"]:
                answered["done"] = True
                write("theft: answered {!r}".format(STEAL_NO))
            return None                     # ゲームの「キャンセル」はそのまま

        def go():
            outcome = steal(app, widget)
            if outcome == "caught":
                caught(app)

        # Kivy は後から付けた観測者を先に呼び、True を返すとそこで止まる。
        yes_button.fbind("on_press", on_yes)
        no_button.fbind("on_press", on_no)

    def screen_nodes():
        try:
            from kivy.core.window import Window
        except Exception:
            return []
        return list(ui.walk_widgets(Window))

    def trade_panel(before):
        """ゲームが出した売買の確認の窓。`(題の Label, 買うの Button, キャンセルの Button)`。

        落とす前に無かったウィジェットのうち、中（どの段でも）に `Button` を2つと `Label` を持ついちばん外側のもの。
        実機（2026-10-04）では `ConfirmationWindow` の下に `FloatLayout` と `Label`「品名:額G」、
        `FloatLayout` の下に `Button`「買う」「キャンセル」が新しく出た。どこに置かれるかは測っていないので画面全体から探す。
        """
        for node in screen_nodes():
            if before.get(id(node)) is node:
                continue
            inner = list(ui.walk_widgets(node))
            buttons = [child for child in inner if type(child).__name__ == "Button"]
            labels = [child for child in inner if type(child).__name__ == "Label"]
            if len(buttons) >= 2 and labels:
                ordered = sorted(buttons, key=lambda child: child.x)   # 左が「買う」、右が「キャンセル」
                write("theft: borrowed the game's confirmation ({} under {}; {!r} / {!r})".format(
                    type(node).__name__, type(getattr(node, "parent", None)).__name__,
                    ordered[0].text, ordered[-1].text))
                return labels[0], ordered[0], ordered[-1]
        return None

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.show_popup_menu", required=False, safe=True)
    def show_popup_menu(orig, self, pos, *args, **kwargs):
        """品の右クリック。ゲームの popup はそのまま。店の品なら自前のボタンを足す（`402_` と同じ口）。"""
        result = orig(self, pos, *args, **kwargs)
        try:
            app = ui.find_app()
            if app is not None:
                screen.schedule(lambda: show_menu(app, self, pos))
        except Exception:
            ctx.log_exc("crime incentive: cannot schedule the shop menu")
        return result

    # ---- 盗んだ店には売れない
    def player_key(app, item):
        return key_in(items.inventory_of(getattr(app, "player", None)), item)

    def remember_stolen(app, item, shop, name):
        """盗んだ品の鍵と、盗んだ店を控える。"""
        key = player_key(app, item)
        if key is None:
            write("WARN theft: cannot find the stolen item {!r} among the belongings".format(name))
            return
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            stolen = dict(bucket.get("stolen") or {})
            stolen[str(key)] = {"shop": shop, "name": name}
            bucket["stolen"] = stolen
            worlds.save(playthrough)

    def stolen_entry(app, item):
        """手持ちのこの品が盗品なら、その控え `{shop, name}`。違えば None（読むだけ）。"""
        if app is None or item is None:
            return None
        key = player_key(app, item)
        if key is None:
            return None
        with worlds.lock:
            stolen = worlds.load(worlds.playthrough(app)).get("stolen") or {}
            entry = stolen.get(str(key))
        return dict(entry) if isinstance(entry, dict) else None

    def prune_stolen(app):
        """手放した品の控えを捨てる。"""
        held = {str(key) for key in (items.inventory_of(getattr(app, "player", None)) or {})}
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            stolen = bucket.get("stolen") or {}
            kept = {k: v for k, v in stolen.items() if k in held}
            if len(kept) != len(stolen):
                if kept:
                    bucket["stolen"] = kept
                else:
                    bucket.pop("stolen", None)
                worlds.save(playthrough)

    def stolen_here(app, item):
        """この品をいま居る店から盗んでいたら、その控え。違えば None。"""
        found = shop_of(app)
        entry = stolen_entry(app, item)
        if found is None or entry is None:
            return None
        return entry if entry.get("shop") == shop_key(app, found[0]) else None

    def stolen_price(item, key, price):
        """値段の段。手持ちの盗品の `売価` を、盗品買取商なら7割・それ以外の店なら半分にする（既定）。"""
        app = ui.find_app()
        if key != rules.SELL or app is None or stolen_entry(app, item) is None:
            return None
        pct = cfg.FENCE_PCT if env.fence.get("broker") else cfg.STOLEN_SELL_PCT
        return rules.stolen_sell_price(key, price, pct)

    def cancel_sale(widget):
        cancel = getattr(widget, "cancel_trade_confirmation", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                ctx.log_exc("crime incentive: cannot cancel the sale")

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.sell_item", required=False, safe=True)
    def sell_item(orig, self, *args, **kwargs):
        """売りの承認。盗んだ店に盗んだ品・盗品買取商に盗品でない品を売ろうとしたら、売らずに品を手持ちへ戻す。"""
        try:
            app = ui.find_app()
            item = getattr(self, "item_instance", None)
            broker_id = env.fence.get("broker")
            if app is not None and item is not None:
                prune_stolen(app)
            if broker_id:
                entry = stolen_entry(app, item)
                refuse = "fence" if entry is None and item is not None else None
            else:
                entry = stolen_here(app, item) if app is not None and item is not None else None
                refuse = "shop" if entry is not None else None
        except Exception:
            ctx.log_exc("crime incentive: cannot check the stolen goods")
            refuse = None
        name = getattr(item, "name", None) or "その品"
        if refuse is None:
            if env.fence.get("broker"):
                write("fence: sold {!r} ({})".format(name, (getattr(item, "attributes", None) or {}).get(rules.SELL)))
            result = orig(self, *args, **kwargs)
            # 売れた品の控えはその場で捨てる（次の売り買いまで残さない）。
            try:
                app = ui.find_app()
                if app is not None:
                    prune_stolen(app)
            except Exception:
                ctx.log_exc("crime incentive: cannot prune the stolen goods")
            return result
        cancel_sale(self)
        if refuse == "fence":
            broker = getattr(ui.character_of(app, str(broker_id)), "name", None) or "盗品買取商"
            write("fence: refused {!r} (not stolen)".format(name))
            screen.say(app, FENCE_REFUSE_TEXT.format(broker=broker, item=name))
            return None
        write("theft: refused to buy back {!r} at {}".format(entry.get("name"), entry.get("shop")))
        screen.say(app, REFUSE_TEXT.format(item=name))
        return None

    env.stolen_entry = stolen_entry
    env.price_layers.append(stolen_price)

    def mute_purchase(context):
        """盗みで借りた `Item.buy` の「〈品〉を購入した。」は出さない。"""
        if muted["buy"]:
            write("theft: muted the purchase line {!r}".format(frames.short(str(context), 60)))
            return context, True
        return context, False

    env.on_text(mute_purchase)
