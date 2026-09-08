# -*- coding: utf-8 -*-
"""所持品の窓の左に装備欄を足す。装備はドラッグか右クリックの「装備する」で移す。

装備欄は本体の `InventoryGrid`（6列×8行）をもう1枚作ったもの。
本体の `InventoryItem.get_all_inventories()` は HUD の `FloatLayout` の中からグリッドを探す
（実機で確認。DOC.md §3）ので、同じ場所に置けばドラッグの受け渡しは本体の処理がそのまま効く。
部位はそのグリッドの上の矩形（`slots.REGIONS`）で、
ドロップは「その部位に収まる・種類が合う・空いている」ときだけ受ける（`is_valid_placement` の包み）。

本体の作り（実機で確認。DOC.md §4）:

- `InventoryGrid` はマス（`InventorySlot`）だけを子に持つ `GridLayout`。品のウィジェット
  （`InventoryItem`）はグリッドの親（窓の `FloatLayout`）に置かれる
- `place_existing_item(widget)` は `item_instance.grid_pos = [x, 下から数えた y]` の位置に置く。
  ウィジェットの座標は見ない。`current_slots` の添字は `y * cols + x`（y は下から）
- ドロップ（`InventoryItem.on_touch_up`）は `try_place_item` の可否に関わらず
  `change_inventory(target)` まで進む。断るときは `change_inventory` の包みで元へ戻す
- 所持品の窓は開くたびに本体が持ち物の辞書から品を並べ直す。装備欄の品も一度そこに並ぶので、
  1フレーム置いてから装備欄へ移す（`move_widget`）。持ち物の辞書には残したままなので、
  セーブも `equipments` の id の解決も本体のまま動く

どの品がどの部位に居るかは MOD の控え（`state\\equipment_slots\\<世界>.json`。
`{主人公: {品の鍵: [x, y]}}`。y は上から）。

本体が読む装備は `equipments` の `weapon` / `wearable` の2つだけなので、
手の武器で攻撃力が最高の1つと、全部位の防具（盾を含む）で防御力が最高の1つを
`Item.equip()` / `unequip()` で写す。合算はしない。

記録は DOC.md（開発中のため docs\\ には無い）。
"""
import sys

from instantale_modloader import frames, state, ui

from . import slots as rules

LOG_BASENAME = "equipment_slots.log"
STORE_ATTR = "_instantale_equipment_slots_store"
CONTAINER_ATTR = "_instantale_equipment_slots_container"
PANEL_ATTR = "_instantale_equipment_slots_panel"
GRID_ATTR = "_instantale_equipment_slots_grid"
GRID_MODULE, GRID_CLASS = "scripts.hud.new_hud", "InventoryGrid"

#: 窓を開いてから、本体が並べ終えた品を装備欄へ移すまでの待ち（秒）。
SETTLE_DELAY = 0.1

# GUI から変えられる値（mod.json の "settings" と同じ名前・既定値。TECH.md §3.8）。
#: 小さい品を部位いっぱいに広げて描く（1×1 を 2×2 の部位なら 2 倍）。
SCALE_TO_FIT = True

#: 装備欄の品 {持ち物の鍵: Item}。`apply()` が差す（ゲーム抜きの試験から中身を見るため）。
#: 実体は `sys` に置く（装備中の品は持ち物の辞書に居ないので、注入し直しで MOD の
#: モジュールが読み直されても消えないようにする。TECH.md §5.5 と同じ理由）。
CONTAINER = None


def _store(ctx, write):
    """世界ごとの控え。`apply()` の外（プロセス側）に1つ（TECH.md §5.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if store is None:
        store = state.WorldStore(ctx, "equipment_slots", write=write)
        setattr(sys, STORE_ATTR, store)
    store.ctx, store.write = ctx, write
    return store


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="equipment slots:")
    schedule = ui.scheduler(ctx, "equipment slots")
    store = _store(ctx, write)
    #: 装備欄の品 {持ち物の鍵: Item}。装備欄のグリッドの item_dict そのもので、
    #: ここに居る間はプレイヤーの持ち物の辞書には居ない（合流するのはセーブの間だけ）。
    global CONTAINER
    container = getattr(sys, CONTAINER_ATTR, None)
    if not isinstance(container, dict):
        container = {}
        setattr(sys, CONTAINER_ATTR, container)
    CONTAINER = container
    #: ドロップ中の品（`try_place_item` → `is_valid_placement` の間だけ）と、MOD 自身が移している最中の旗。
    placing = {"item": None, "moving": False, "native": False, "cell": None}

    def native_manager(app, name, item):
        """本体の ItemEquipManager / ItemUnequipManager を素通しで走らせる（HUD の更新と文言のため）。

        `equipments` はこの MOD が書く（本体も popup 側で書いてから Manager を呼ぶ。223_ の記録）。
        """
        cls = getattr(sys.modules.get("__main__"), name, None)
        if not isinstance(cls, type):
            write("WARN {} not found".format(name))
            return
        placing["native"] = True
        try:
            # execute は窓を閉じるので、中身の equip_item / unequip_item を直接呼ぶ
            method = "equip_item" if name == "ItemEquipManager" else "unequip_item"
            getattr(cls(app), method)(item)
        except Exception:
            ctx.log_exc("equipment slots: {} failed".format(name))
        finally:
            placing["native"] = False

    # ------------------------------------------------------------ 読み取り
    def player_of(app):
        return getattr(app, "player", None) if app is not None else None

    def inventory_of(owner):
        inv = getattr(owner, "inventory", None)
        if isinstance(inv, dict):
            return inv
        inner = getattr(inv, "inventory", None)
        return inner if isinstance(inner, dict) else None

    def positions_of(app):
        """(世界の鍵, この主人公の控え {品の鍵: [x, y]})。y は上から。"""
        key, bucket = store.of(app)
        name = getattr(player_of(app), "name", None) or "_"
        return key, bucket.setdefault(str(name), {})

    def instance_of(widget_or_item):
        inst = getattr(widget_or_item, "item_instance", None)
        return inst if inst is not None else widget_or_item

    def key_of(widget):
        value = frames.attr(widget, "item_id", None)
        return str(value) if value is not None else None

    def equipments_of(player):
        eq = getattr(player, "equipments", None)
        if not isinstance(eq, dict):
            eq = {}
            try:
                player.equipments = eq
            except Exception:
                return None
        return eq

    def is_mine(grid):
        return getattr(grid, GRID_ATTR, False) is True

    def game_class(name):
        module = sys.modules.get(GRID_MODULE)
        cls = getattr(module, name, None) if module is not None else None
        return cls if isinstance(cls, type) else None

    # ------------------------------------------------------------ 座標
    # 控えと rules は「y は上から」。本体の grid_pos / is_valid_placement は「y は下から」。
    def to_game_y(y_top, h):
        return rules.ROWS - y_top - h

    def to_top_y(game_y, h):
        return rules.ROWS - game_y - h

    def cell_of(widget):
        """装備欄に居るウィジェットの左上のマス (x, y_top)。占有していなければ None。"""
        current = frames.attr(widget, "current_slots", None)
        try:
            indices = [int(i) for i in current]
        except (TypeError, ValueError):
            return None
        if not indices:
            return None
        low = min(indices)
        _w, h = rules.size_of(instance_of(widget))
        return (low % rules.COLS, to_top_y(low // rules.COLS, h))

    # ------------------------------------------------------------ 本体との同期
    def current_slots(app):
        _key, positions = positions_of(app)
        return rules.slots_from_positions(positions, container)

    def sync_game(app, force=False):
        """控えから本体の `weapon` / `wearable` を決め直す。

        `force` は変わっていなくても本体の `equip_item` を通す（HUD の Atk/Def は
        本体が装備を変えたときにしか塗り直さないので、窓を開いたときに1度かける）。
        """
        player = player_of(app)
        eq = equipments_of(player)
        if eq is None:
            return
        slots = current_slots(app)
        items = dict(inventory_of(player) or {})
        items.update(container)
        # 本体のドロップ処理は種類を問わず `equipments[item_type]` を書く（断ったドロップでも）。
        # 本体が読む鍵は weapon / wearable だけなので、それ以外は落とす（装備印の残りもこれで消える）
        for stray in [k for k in eq if k not in dict(rules.GAME_KEYS)]:
            write("dropped stray equipments key {!r}".format(stray))
            eq.pop(stray, None)
        for game_key, _stat in rules.GAME_KEYS:
            want = rules.best(slots, container, game_key)
            current = eq.get(game_key)
            if isinstance(current, str):
                current = items.get(current)
            if current is want:
                if force and want is not None:
                    write("refresh {}: {!r}".format(
                        game_key, frames.short(getattr(want, "name", None), 60)))
                    native_manager(app, "ItemEquipManager", want)
                continue
            if current is not None and eq.get(game_key) is not None:
                eq.pop(game_key, None)
                native_manager(app, "ItemUnequipManager", current)
            if want is not None:
                eq[game_key] = want
                native_manager(app, "ItemEquipManager", want)
            write("{} -> {!r} (was {!r})".format(
                game_key, frames.short(getattr(want, "name", None), 60) if want else None,
                frames.short(getattr(current, "name", None), 60) if current else None))

    def after_change(app):
        key, _positions = positions_of(app)
        sync_game(app)
        store.save(key)
        refresh_marks(app)
        paint_labels(app)

    # ------------------------------------------------------------ 画面の走査
    def walk(widget, depth, out):
        if depth > 14:
            return
        out.append(widget)
        for child in frames.attr(widget, "children", ()) or ():
            walk(child, depth + 1, out)

    def item_widgets(app):
        """画面に居るプレイヤーの品のウィジェット。"""
        hud = ui.find_hud(app)
        if hud is None:
            return []
        found = []
        walk(hud, 0, found)
        player = player_of(app)
        return [w for w in found
                if frames.attr(w, "item_instance") not in (frames.MISSING, None)
                and getattr(instance_of(w), "obtainer", None) is player]

    def refresh_marks(app):
        """装備欄の品に装備印。所持品側は本体の印（2鍵の品）を外す。"""
        for widget in item_widgets(app):
            try:
                widget.is_equipped = is_mine(frames.attr(widget, "inventory", None))
                redraw = getattr(widget, "_update_equipped_border", None)
                if callable(redraw):
                    redraw()
            except Exception:
                pass

    def find_grid(app):
        """所持品の窓に見えているプレイヤーのグリッド（装備欄ではない方）。無ければ None。"""
        hud = ui.find_hud(app)
        if hud is None:
            return None
        found = []
        walk(hud, 0, found)
        player = player_of(app)
        for widget in found:
            if frames.attr(widget, "place_new_item") is frames.MISSING or is_mine(widget):
                continue
            if frames.attr(widget, "obtainer") is not player:
                continue
            if frames.attr(widget, "situation", None) not in (None, ""):
                continue
            node, visible = widget, True
            for _ in range(14):
                if node is None:
                    break
                try:
                    hidden = float(frames.attr(node, "opacity", 1.0)) <= 0.0
                except (TypeError, ValueError):
                    hidden = False
                if hidden:
                    visible = False
                    break
                node = frames.attr(node, "parent", None)
            if visible and float(widget.width) > 0:
                return widget
        return None

    def window_of(grid, host):
        """グリッドを包む窓（host の直下の子）。見つからなければグリッド自身。"""
        node = grid
        for _ in range(14):
            parent = frames.attr(node, "parent", None)
            if parent is None or parent is host:
                return node
            node = parent
        return grid

    def panel_of(hud):
        host = ui.overlay_host(hud)
        for child in list(frames.attr(host, "children", ()) or ()):
            if getattr(child, PANEL_ATTR, None) is not None:
                return child
        return None

    def my_grid(app):
        hud = ui.find_hud(app)
        panel = panel_of(hud) if hud is not None else None
        return getattr(panel, PANEL_ATTR)["grid"] if panel is not None else None

    # ------------------------------------------------------------ 品を移す
    def reparent(widget, parent):
        old = frames.attr(widget, "parent", None)
        if old is parent or parent is None:
            return
        try:
            if old is not None:
                old.remove_widget(widget)
            parent.add_widget(widget)
        except Exception:
            ctx.log_exc("equipment slots: reparent failed")

    def move_widget(app, widget, mine, x, y_top):
        """所持品の品を装備欄の (x, y_top) へ。本体のドロップと同じ手順を踏む。

        1. 元のグリッドの占有を外す（`clear_current_slots` は `widget.inventory` を見る）
        2. `item.grid_pos` を本体の座標（y は下から）で書き、`place_existing_item` で置く
        3. `change_inventory(mine)`（包みが控えと持ち物の辞書を直す）
        4. 描画の親を装備欄の枠へ
        """
        item = instance_of(widget)
        _w, h = rules.size_of(item)
        placing["moving"] = True
        placing["cell"] = (int(x), int(y_top))
        try:
            try:
                widget.clear_current_slots()
            except Exception:
                ctx.log_exc("equipment slots: clear_current_slots before move failed")
            item.grid_pos = [int(x), int(to_game_y(int(y_top), h))]
            mine.place_existing_item(widget)
            widget.change_inventory(mine)
        finally:
            placing["moving"] = False
            placing["cell"] = None

    def move_back(app, widget, main):
        """装備欄の品を所持品へ。空きは本体の `place_new_item` に探させる。

        本体の `change_inventory` は移動元の `item_dict` から鍵を消す（無ければ KeyError）。
        断ったドロップの戻しでは装備欄の辞書に入っていないことがあるので、先に入れておく。
        """
        placing["moving"] = True
        if is_mine(frames.attr(widget, "inventory", None)):
            container.setdefault(key_of(widget), instance_of(widget))
        try:
            try:
                widget.clear_current_slots()
            except Exception:
                ctx.log_exc("equipment slots: clear_current_slots before move back failed")
            main.place_new_item(widget)
            widget.change_inventory(main)
        finally:
            placing["moving"] = False

    def build_items(app):
        """装備欄の品のウィジェットを作って並べる（持ち物の辞書に無いので本体は作らない）。

        `InventoryGrid` はマスだけを子に持つので、品は所持品の品と同じ親（窓の `FloatLayout`）へ。
        マスの位置が決まる次のフレーム以降に呼ぶこと（同じフレームだと占有マスがずれる）。
        """
        metrics = panel_metrics(app)
        main = find_grid(app)
        cls = game_class("InventoryItem")
        if metrics is None or main is None or cls is None:
            return
        mine = metrics["grid"]
        host = frames.attr(main, "parent", None)
        _key, positions = positions_of(app)
        made = metrics.setdefault("widgets", [])
        for item_key, item in list(container.items()):
            pos = positions.get(item_key)
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                continue
            w, h = rules.size_of(item)
            placing["moving"] = True
            placing["cell"] = (int(pos[0]), int(pos[1]))
            try:
                widget = cls(w, h, getattr(item, "image_src", ""), getattr(item, "rarity", "common"),
                             "", item_key, item, mine)
                item.grid_pos = [int(pos[0]), int(to_game_y(int(pos[1]), h))]
                mine.place_existing_item(widget)
                if host is not None and frames.attr(widget, "parent", None) is None:
                    host.add_widget(widget)
                widget.is_equipped = True
                made.append(widget)
                fit_widget(app, widget)
            except Exception:
                ctx.log_exc("equipment slots: building {!r} failed".format(item_key))
            finally:
                placing["moving"] = False
                placing["cell"] = None
        write("built {} of {} items".format(len(made), len(container)))
        sync_game(app, force=True)
        after_change(app)

    # ------------------------------------------------------------ 装備欄の窓
    def drop_detail_boxes(hud):
        """取り残された品の説明窓（`ItemDetailBox`）を消す。

        説明窓はカーソルが品を離れたときに本体が消すが、品を装備欄へ移した瞬間や
        窓を閉じたときにカーソルの下に品が無くなると「離れた」が来ず、窓だけ残る。
        """
        host = ui.overlay_host(hud)
        found = []
        walk(host, 0, found)
        removed = 0
        for widget in found:
            if type(widget).__name__ != "ItemDetailBox":
                continue
            parent = frames.attr(widget, "parent", None)
            if parent is None:
                continue
            try:
                parent.remove_widget(widget)
                removed += 1
            except Exception:
                pass
        if removed:
            write("removed {} stale detail box(es)".format(removed))

    def hide_buttons(hud, hidden):
        """所持品の窓を開いている間、右側の選択肢（`hud.right_buttons`。左の `buttons` は本体が隠す）を隠す。

        `disabled` はボタンだけを子に持つ親（`right_button_layout`）に付ける。各ボタンの
        `disabled` は本体が応答待ちで切り替えるので触らない（116_ と同じ理由。Kivy は親の
        `disabled` を子へ継承するので、子の値を変えずに押せなくできる）。
        `opacity` はボタンごとに付ける。親の `opacity` は本体も 0 にしているが描画には効かない（DOC.md §3.3）。
        """
        buttons = [b for b in (frames.attr(hud, "right_buttons", None) or ()) if b is not None]
        targets = []
        for button in buttons:
            parent = frames.attr(button, "parent", None)
            if parent is not None and parent not in targets and all(
                    child in buttons for child in frames.attr(parent, "children", ())):
                targets.append(parent)
        # ponytail: 親が他の物も抱えていたらボタン単位（disabled は本体と取り合いになる）
        for target in targets or buttons:
            try:
                target.disabled = bool(hidden)
            except Exception:
                ctx.log_exc("equipment slots: hiding the choice buttons failed")
        for button in buttons:
            try:
                button.opacity = 0.0 if hidden else 1.0
            except Exception:
                ctx.log_exc("equipment slots: hiding the choice buttons failed")

    def drop_panel(hud):
        panel = panel_of(hud)
        if panel is not None:
            for widget in getattr(panel, PANEL_ATTR).get("widgets", []):
                parent = frames.attr(widget, "parent", None)
                if parent is not None:
                    try:
                        parent.remove_widget(widget)
                    except Exception:
                        pass
            try:
                panel.parent.remove_widget(panel)
            except Exception:
                pass
        drop_detail_boxes(hud)

    def build_panel(app):
        """装備欄を組んで所持品の窓の左に置く。開くたびに作り直す。"""
        hud = ui.find_hud(app)
        grid = find_grid(app)
        if hud is None:
            return
        drop_panel(hud)
        if grid is None:
            return
        cls = game_class(GRID_CLASS)
        if cls is None:
            write("WARN InventoryGrid class not found")
            return
        try:
            from kivy.uix.floatlayout import FloatLayout
            from kivy.uix.label import Label
            from kivy.graphics import Color, Line
        except Exception:
            ctx.log_exc("equipment slots: kivy unavailable")
            return

        cols = float(frames.attr(grid, "cols", 4) or 4)
        spacing = frames.attr(grid, "spacing", (1, 1))
        try:
            gap = float(spacing[0])
        except (TypeError, ValueError, IndexError):
            gap = 1.0
        cell = (float(grid.width) + gap) / cols
        width = rules.COLS * cell - gap
        height = rules.ROWS * cell - gap
        pad = cell * 0.5

        player = player_of(app)
        try:
            mine = cls(rules.COLS, rules.ROWS, container, player,
                       size_hint=(None, None), size=(width, height))
        except Exception:
            ctx.log_exc("equipment slots: building the equipment grid failed")
            return
        setattr(mine, GRID_ATTR, True)
        try:
            mine.size_hint = (None, None)
            mine.size = (width, height)
        except Exception:
            pass

        # 部位の外のマス（上の両隅）は見せない（slots の添字は上の行から）
        slots = frames.attr(mine, "slots", None)
        if isinstance(slots, (list, tuple)):
            for x, y in rules.UNUSED_CELLS:
                index = y * rules.COLS + x
                if index < len(slots):
                    ui.show_widget(slots[index], False)

        panel = FloatLayout(size_hint=(None, None), size=(width + 2 * pad, height + 2 * pad))
        panel.add_widget(mine)
        label = frames.attr(hud, "status_label")
        font_name = frames.text_of(label, "font_name") if label is not frames.MISSING else None
        labels = {}
        for name, (rx, ry, rw, rh) in rules.REGIONS.items():
            text = Label(text=rules.LABELS[name], size_hint=(None, None),
                         size=(rw * cell - gap, rh * cell - gap), font_size=cell * 0.4,
                         color=(1, 1, 1, 0.8))
            if font_name:
                text.font_name = font_name
            panel.add_widget(text)
            labels[name] = (text, rx, ry, rw, rh)
        # 枠は部位ごとに描き、外枠の矩形（所持品の窓と同じ）で全体を囲む
        with panel.canvas.after:
            Color(1, 1, 1, 0.9)
            lines = [Line(rectangle=(0, 0, 0, 0), width=1.2) for _ in rules.REGIONS]
            frame = Line(rectangle=(0, 0, 0, 0), width=1.2)

        def layout(*_args):
            px, py = panel.x, panel.y
            mine.pos = (px + pad, py + pad)
            frame.rectangle = (px, py, panel.width, panel.height)
            for i, (name, (rx, ry, rw, rh)) in enumerate(rules.REGIONS.items()):
                x = px + pad + rx * cell
                y = py + pad + height - (ry + rh) * cell + gap
                lines[i].rectangle = (x, y, rw * cell - gap, rh * cell - gap)
                labels[name][0].pos = (x, y)

        panel.bind(pos=layout)
        setattr(panel, PANEL_ATTR, {"grid": mine, "labels": labels, "cell": cell, "gap": gap})

        host = ui.overlay_host(hud)
        window = window_of(grid, host)
        wx, wy = window.to_window(window.x, window.y)
        px = wx - cell * 0.5 - panel.width
        py = wy + (float(window.height) - panel.height) / 2.0
        if hasattr(host, "to_widget"):
            px, py = host.to_widget(px, py)
        panel.pos = (max(0.0, px), max(0.0, py))
        # 窓（品の親）より先に描く＝children の末尾へ。品の親は窓のままにするので、
        # ドラッグ中も装備中も品は装備欄の上に描かれる
        children = list(frames.attr(host, "children", ()) or ())
        at = children.index(window) + 1 if window in children else len(children)
        host.add_widget(panel, index=at)
        layout()
        write("panel built: cell={:.1f} items={} pos=({:.0f},{:.0f})".format(
            cell, len(container), panel.x, panel.y))
        paint_labels(app)

    def paint_labels(app):
        """空いている部位だけ名前を見せる。"""
        hud = ui.find_hud(app)
        panel = panel_of(hud) if hud is not None else None
        if panel is None:
            return
        slots = current_slots(app)
        for name, (text, _rx, _ry, _rw, _rh) in getattr(panel, PANEL_ATTR)["labels"].items():
            text.opacity = 0.0 if slots.get(name) else 1.0

    # ------------------------------------------------------------ 見た目の大きさ
    def panel_metrics(app):
        hud = ui.find_hud(app)
        panel = panel_of(hud) if hud is not None else None
        return getattr(panel, PANEL_ATTR) if panel is not None else None

    def fit_widget(app, widget):
        """装備欄の品を部位いっぱいに広げる（1×1 を 2×2 の部位なら 2 倍）。部位の左上に寄せる。"""
        metrics = panel_metrics(app)
        cell_pos = cell_of(widget)
        if metrics is None or cell_pos is None:
            return
        item = instance_of(widget)
        name = rules.fits(item, cell_pos[0], cell_pos[1])
        if name is None:
            return
        factor = rules.fit_scale(item, name) if SCALE_TO_FIT else 1
        if factor <= 1:
            return
        cell, gap, mine = metrics["cell"], metrics["gap"], metrics["grid"]
        rx, ry, _rw, _rh = rules.REGIONS[name]
        w, h = rules.size_of(item)
        if tuple(cell_pos) != (rx, ry):
            # 占有マスを部位の左上へ寄せる（本体の装備印はマス単位に描かれるので、絵と揃える）
            key, positions = positions_of(app)
            try:
                widget.clear_current_slots()
                item.grid_pos = [rx, to_game_y(ry, h)]
                mine.place_existing_item(widget)
                positions[key_of(widget)] = [rx, ry]
                store.save(key)
            except Exception:
                ctx.log_exc("equipment slots: snapping {!r} to the region failed".format(key_of(widget)))
        try:
            widget.size = (w * factor * cell - gap, h * factor * cell - gap)
            widget.pos = (float(mine.x) + rx * cell,
                          float(mine.y) + (rules.ROWS - ry - h * factor) * cell)
        except Exception:
            ctx.log_exc("equipment slots: fitting {!r} failed".format(key_of(widget)))

    def unfit_widget(app, widget):
        """所持品へ戻す品を元の大きさに。"""
        metrics = panel_metrics(app)
        if metrics is None:
            return
        cell, gap = metrics["cell"], metrics["gap"]
        w, h = rules.size_of(instance_of(widget))
        try:
            widget.size = (w * cell - gap, h * cell - gap)
        except Exception:
            pass

    # ------------------------------------------------------------ 控えとの突き合わせ
    def occupied_except(app, item_key):
        slots = current_slots(app)
        return {name: held for name, held in slots.items() if held != item_key}

    def free_region(app, item, item_key):
        """この品が入る空いている部位 (名前, x, y_top)。無ければ None。"""
        kind = rules.kind_of(item)
        if kind is None:
            return None
        taken = occupied_except(app, item_key)
        for name, (rx, ry, _rw, _rh) in rules.REGIONS.items():
            if rules.KIND_OF[name] == kind and not taken.get(name):
                return name, rx, ry
        return None

    def refresh_container(app):
        """控えを持ち物と突き合わせ、`container` を組み直し、装備欄の品を持ち物の辞書から抜く。

        品は持ち物の辞書（ロード直後・セーブ直後）か `container` のどちらかに居る。
        どちらにも無い品（売った・渡した・捨てた）は落とす。
        本体が装備しているのに控えに無い品（MOD を入れる前のセーブ）は空いている部位
        （手は右手が先）へ拾う。窓を組む前に呼ぶこと（本体が辞書から品を並べる前に抜く）。
        """
        player = player_of(app)
        inv = inventory_of(player)
        if inv is None:
            return
        key, positions = positions_of(app)
        eq = equipments_of(player) or {}

        def item_of(item_key):
            item = inv.get(item_key)
            return item if item is not None else container.get(item_key)

        # 持ち物にも装備欄にも無い品は、プロセスに残っていれば持ち物へ返す
        # （MOD の作り直しや落ちたときの保険。持ち主がプレイヤーで、どの辞書にも居ない Item）
        if not getattr(sys, CONTAINER_ATTR + "_swept", False):
            setattr(sys, CONTAINER_ATTR + "_swept", True)
            try:
                import gc
                known = set(map(id, inv.values())) | set(map(id, container.values()))
                for obj in gc.get_objects():
                    if (type(obj).__name__ == "Item" and getattr(obj, "obtainer", None) is player
                            and id(obj) not in known and getattr(obj, "id", None) is not None
                            and str(obj.id) not in inv):
                        inv[str(obj.id)] = obj
                        write("recovered {!r} ({!r}) from the process".format(
                            str(obj.id), frames.short(getattr(obj, "name", None), 40)))
            except Exception:
                ctx.log_exc("equipment slots: recovering lost items failed")

        for item_key in list(positions):
            pos = positions[item_key]
            item = item_of(item_key)
            if item is None or not isinstance(pos, (list, tuple)) or len(pos) < 2:
                positions.pop(item_key)
                write("dropped {!r}: not in inventory".format(item_key))
            elif rules.fits(item, int(pos[0]), int(pos[1])) is None:
                positions.pop(item_key)
                write("dropped {!r}: {} is outside the regions".format(item_key, pos))
        # 部位と種類が合わない品、同じ部位に2つ目の品（部位の並びを変えた後の古い控え）も落とす
        seen = {}
        for item_key in list(positions):
            item = item_of(item_key)
            name = rules.fits(item, int(positions[item_key][0]), int(positions[item_key][1]))
            if rules.KIND_OF[name] != rules.kind_of(item) or name in seen:
                positions.pop(item_key)
                write("dropped {!r}: {} does not take it (or already holds {!r})".format(
                    item_key, name, seen.get(name)))
                continue
            seen[name] = item_key
        # 本体が装備しているのに控えに無い品を拾う
        for game_key, _stat in rules.GAME_KEYS:
            current = eq.get(game_key)
            item_key = None
            for k, v in list(inv.items()) + list(container.items()):
                if v is current or (isinstance(current, str) and str(k) == current):
                    item_key = str(k)
                    break
            if item_key is None or item_key in positions:
                continue
            found = free_region(app, item_of(item_key), item_key)
            if found is not None:
                name, rx, ry = found
                positions[item_key] = [rx, ry]
                write("adopted {!r} into {}".format(item_key, name))
        tidy_inventory_positions(inv)
        fresh = {item_key: item_of(item_key) for item_key in positions}
        # 控えに無いのに装備欄の辞書に残っている品（戻し先が無かった品）は持ち物へ返す
        for item_key, item in list(container.items()):
            if item_key not in fresh and item_key not in inv:
                inv[item_key] = item
                write("returned {!r} to the inventory".format(item_key))
        container.clear()
        container.update(fresh)
        for item_key in container:
            inv.pop(item_key, None)
        store.save(key)

    def tidy_inventory_positions(inv, cols=4, rows=6):
        """持ち物の品の `grid_pos` が重なる・はみ出すなら空きへ直す。

        装備欄から戻った品は装備欄の座標（6×10）を持ったままのことがあり、本体の
        `place_existing_item` は確かめずに置くので、他の品の下に隠れる（108_ と同じ現象）。
        窓を組む前に、所持品のグリッドの範囲で先着順に空きを割り当て直す。
        """
        taken = set()
        for item_key, item in inv.items():
            w, h = rules.size_of(item)
            pos = getattr(item, "grid_pos", None)
            cells = None
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                try:
                    x, y = int(pos[0]), int(pos[1])
                    cells = {(x + dx, y + dy) for dx in range(w) for dy in range(h)}
                    if x < 0 or y < 0 or x + w > cols or y + h > rows or cells & taken:
                        cells = None
                except (TypeError, ValueError):
                    cells = None
            if cells is None:
                for y in range(rows):
                    for x in range(cols):
                        candidate = {(x + dx, y + dy) for dx in range(w) for dy in range(h)}
                        if x + w <= cols and y + h <= rows and not candidate & taken:
                            cells = candidate
                            try:
                                item.grid_pos = [x, y]
                            except Exception:
                                pass
                            write("moved {!r} to a free cell ({}, {})".format(item_key, x, y))
                            break
                    if cells is not None:
                        break
            if cells is not None:
                taken |= cells

    def merge_into_save(data):
        """書き出す直前のセーブ辞書へ、装備欄の品を `to_dict()` で足す。足した数を返す。

        生きている持ち物の辞書には触らない。本体は戦利品の窓などを組みながら（辞書を回している
        最中に）セーブを呼ぶことがあり、そこで辞書へ足すと
        `dictionary changed size during iteration` で落ちる（実機 2026-09-08 14:31）。
        """
        if not isinstance(data, dict):
            return 0
        player_data = data.get("player_data")
        inv = player_data.get("inventory") if isinstance(player_data, dict) else None
        if not isinstance(inv, dict):
            return 0
        added = 0
        for item_key, item in list(container.items()):
            if item_key in inv:
                continue
            to_dict = getattr(item, "to_dict", None)
            if not callable(to_dict):
                continue
            try:
                inv[item_key] = to_dict()
                added += 1
            except Exception:
                ctx.log_exc("equipment slots: to_dict of {!r} failed".format(item_key))
        return added

    # ------------------------------------------------------------ フック
    def main_has_room(main, item):
        """所持品のグリッドに、この品が入る空きがあるか。"""
        w, h = rules.size_of(item)
        try:
            cols, rows = int(main.cols), int(main.rows)
            for gy in range(rows):
                for x in range(cols):
                    if main.is_valid_placement(x, gy, w, h):
                        return True
        except Exception:
            ctx.log_exc("equipment slots: scanning the inventory for room failed")
        return False

    def evict_for(app, widget, gx, y_top):
        """落とす先の部位に同じ種類の別の品が居れば、所持品の空きへ出す（置換）。

        空きが無ければ何もしない。出せたら True。
        """
        main = find_grid(app)
        if main is None:
            return False
        item = instance_of(widget)
        name = rules.fits(item, gx, y_top)
        if name is None or rules.KIND_OF[name] != rules.kind_of(item):
            return False
        held = current_slots(app).get(name)
        item_key = key_of(widget)
        if not held or held == item_key:
            return False
        old = next((w for w in item_widgets(app) if key_of(w) == held), None)
        if old is None:
            return False
        if not main_has_room(main, instance_of(old)):
            write("replace {!r} in {} refused: no room in the inventory for {!r}".format(
                item_key, name, held))
            return False
        try:
            move_back(app, old, main)
            write("replace: {!r} leaves {} for {!r}".format(held, name, item_key))
            return True
        except Exception:
            ctx.log_exc("equipment slots: evicting {!r} for replacement failed".format(held))
            return False

    @ctx.wrap("scripts.hud.new_hud:InventoryGrid.try_place_item", safe=True)
    def try_place_item(orig, self, item, pos, *args, **kwargs):
        """装備欄へのドロップ。可否は `is_valid_placement` の包みが決める。ここでは置換の先出しと品の控えだけ。"""
        placing["item"] = item if is_mine(self) else None
        try:
            result = orig(self, item, pos, *args, **kwargs)
        finally:
            placing["item"] = None
        if is_mine(self):
            # 本体はドロップの途中で Manager を通す（断ったドロップでも）ので、装備印や
            # `equipments` に余計なものが残る。次のフレームで整える
            write("try_place_item {!r} -> {!r}".format(key_of(item), result))
            app = ui.find_app()

            def settle_drop():
                # 所持品から来た品を断ったとき、本体は change_inventory を呼ばず、
                # 品を落とした座標に置いたまま（窓の外なので見えない）にする。所持品の空きへ戻す
                main = find_grid(app)
                if (not result and main is not None
                        and not is_mine(frames.attr(item, "inventory", None))
                        and main_has_room(main, instance_of(item))):
                    try:
                        move_back(app, item, main)
                        write("drop of {!r} rejected: back to inventory".format(key_of(item)))
                    except Exception:
                        ctx.log_exc("equipment slots: returning {!r} failed".format(key_of(item)))
                after_change(app)
            if app is not None:
                schedule(settle_drop)
        return result

    @ctx.wrap("scripts.hud.new_hud:InventoryGrid.is_valid_placement", safe=True)
    def is_valid_placement(orig, self, grid_x, grid_y, width, height, *args, **kwargs):
        result = orig(self, grid_x, grid_y, width, height, *args, **kwargs)
        if not is_mine(self):
            return result
        item = placing["item"]
        if item is None:
            # 本体の下見（ドロップの前）。落とす先の部位に同じ種類の品が居れば置換の先出しをして下見をやり直す
            dragging = placing.get("dragging")
            if not result and dragging is not None and "on_touch_up" in frames.caller(4):
                app = ui.find_app()
                try:
                    if app is not None and evict_for(app, dragging, int(grid_x), to_top_y(int(grid_y), int(height))):
                        result = orig(self, grid_x, grid_y, width, height, *args, **kwargs)
                except (TypeError, ValueError):
                    pass
            return result
        if not result:
            return result
        app = ui.find_app()
        item_key = key_of(item)
        try:
            reason = rules.accepts(instance_of(item), int(grid_x), to_top_y(int(grid_y), int(height)),
                                   occupied_except(app, item_key))
        except (TypeError, ValueError):
            reason = "bad cell"
        write("drop {!r} at ({}, {}) {}x{} -> {}".format(
            item_key, grid_x, grid_y, width, height, reason or "ok"))
        placing["cell"] = (int(grid_x), to_top_y(int(grid_y), int(height))) if reason is None else None
        return reason is None

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.change_inventory", safe=True)
    def change_inventory(orig, self, new_inventory, *args, **kwargs):
        old = frames.attr(self, "inventory", None)
        result = orig(self, new_inventory, *args, **kwargs)
        app = ui.find_app()
        if app is None or not (is_mine(new_inventory) or is_mine(old)):
            return result
        item_key = key_of(self)
        item = instance_of(self)
        _key, positions = positions_of(app)
        inv = inventory_of(player_of(app))
        if is_mine(new_inventory):
            cell = placing["cell"]
            if not placing["moving"]:
                placing["cell"] = None
            if cell is None:
                # 断ったドロップ（占有していない）。本体は構わず移してくるので、
                # 本体のドロップ処理が終わった次のフレームで元へ置き直す
                # （装備欄から来た品は元の部位へ、所持品から来た品は所持品の空きへ）。
                came_from_mine = is_mine(old)
                previous = positions.get(item_key)
                if inv is not None:
                    inv[item_key] = item

                def put_back():
                    mine = my_grid(app)
                    main = find_grid(app)
                    try:
                        if came_from_mine and mine is not None and previous is not None:
                            move_widget(app, self, mine, previous[0], previous[1])
                            write("drop of {!r} rejected: back to {}".format(item_key, previous))
                        elif main is not None and main_has_room(main, item):
                            move_back(app, self, main)
                            container.pop(item_key, None)
                            positions.pop(item_key, None)
                            write("drop of {!r} rejected: back to inventory".format(item_key))
                        else:
                            # 所持品に空きが無い。次に窓を開いたとき、控えに無い品として所持品へ戻す
                            write("drop of {!r} rejected: no room to return it now".format(item_key))
                    except Exception:
                        ctx.log_exc("equipment slots: putting {!r} back failed".format(item_key))
                    after_change(app)

                schedule(put_back)
                return result
            container[item_key] = item
            positions[item_key] = [cell[0], cell[1]]
            if inv is not None:
                inv.pop(item_key, None)       # 装備欄に居る間は持ち物の辞書に居ない
            schedule(lambda: fit_widget(app, self))   # 本体がドロップの座標を置き終えた後で
            write("equipped {!r} at {}".format(item_key, cell))
        else:
            container.pop(item_key, None)
            positions.pop(item_key, None)
            if inv is not None:
                inv[item_key] = item
            unfit_widget(app, self)
            write("unequipped {!r}".format(item_key))
        after_change(app)
        return result

    # 右クリックの popup（`ItemPopupMenu`）は右クリックのたびに作られ、ボタンはそのときの
    # `on_equip_item` / `on_unequip_item` に束縛される。ここなら包みが効く（メインスレッド）。
    # 本体の Manager はロード時に1度だけ作られ、ロード時に束縛した参照で呼ばれるので、
    # `ItemEquipManager.execute` 等をクラス属性で包いても届かない（実機 2026-09-08、DOC.md §4）。
    # プレイヤーの品は本体の経路（equipments の直書き → Manager）を通さず、ここで移すだけにする。
    def popup_widget(self, app):
        """popup が指す InventoryItem。プレイヤーの品で、所持品の窓が開いていなければ None。"""
        widget = getattr(self, "item", None)
        item = getattr(widget, "item_instance", None) if widget is not None else None
        if item is None or getattr(item, "obtainer", None) is not player_of(app):
            return None
        if find_grid(app) is None:
            return None
        return widget

    def hide_popup(widget):
        hide = getattr(widget, "hide_popup_menu", None)
        if callable(hide):
            try:
                hide()
            except Exception:
                pass

    def do_equip(app, widget):
        """品を空いている部位へ移す（手は右手が先）。埋まっていれば右手の品と入れ替える。"""
        mine = my_grid(app)
        main = find_grid(app)
        item_instance = instance_of(widget)
        if mine is None or main is None:
            return
        if is_mine(frames.attr(widget, "inventory", None)):
            return                                                       # もう装備欄に居る
        item_key = key_of(widget)
        found = free_region(app, item_instance, item_key)
        if found is None:
            kind = rules.kind_of(item_instance)
            for name, (rx, ry, _rw, _rh) in rules.REGIONS.items():
                if rules.KIND_OF[name] == kind:
                    held = current_slots(app).get(name)
                    old = next((w for w in item_widgets(app) if key_of(w) == held), None)
                    if old is not None:
                        try:
                            move_back(app, old, main)
                            write("popup equip: {!r} leaves {} for {!r}".format(held, name, item_key))
                        except Exception:
                            ctx.log_exc("equipment slots: evicting {!r} failed".format(held))
                    found = (name, rx, ry)
                    break
        if found is None:
            write("popup equip ignored: {!r} is not equipment".format(item_key))
            return
        name, rx, ry = found
        try:
            move_widget(app, widget, mine, rx, ry)
            write("popup equip {!r} -> {}".format(item_key, name))
        except Exception:
            ctx.log_exc("equipment slots: popup equip failed")

    @ctx.wrap("scripts.hud.new_hud:ItemPopupMenu.on_equip_item", safe=True)
    def on_equip_item(orig, self, *args, **kwargs):
        """右クリックの「装備」。"""
        app = ui.find_app()
        widget = popup_widget(self, app)
        if widget is None:
            return orig(self, *args, **kwargs)
        hide_popup(widget)
        do_equip(app, widget)
        return None

    @ctx.wrap("scripts.hud.new_hud:ItemPopupMenu.on_unequip_item", safe=True)
    def on_unequip_item(orig, self, *args, **kwargs):
        """右クリックの「外す」。所持品の空きへ戻す。"""
        app = ui.find_app()
        widget = popup_widget(self, app)
        if widget is None:
            return orig(self, *args, **kwargs)
        hide_popup(widget)
        main = find_grid(app)
        if not is_mine(frames.attr(widget, "inventory", None)):
            refresh_marks(app)                                            # 装備印だけ残っていた
            return None
        if main is None or not main_has_room(main, instance_of(widget)):
            write("popup unequip {!r} ignored: no room in the inventory".format(key_of(widget)))
            return None
        try:
            move_back(app, widget, main)
            write("popup unequip {!r}".format(key_of(widget)))
        except Exception:
            ctx.log_exc("equipment slots: popup unequip failed")
        return None

    @ctx.wrap("scripts.save_codec:write_obfuscated_json_file", safe=True)
    def write_save(orig, file_path, data, *args, **kwargs):
        """セーブの書き出し。装備欄の品を JSON 側へ足す（`equipments` の id は本体が書いている）。"""
        added = merge_into_save(data)
        if added:
            write("save: added {} equipped item(s) to the save data".format(added))
        return orig(file_path, data, *args, **kwargs)

    @ctx.wrap("scripts.hud.new_hud:ItemPopupMenu.on_discard_item", safe=True)
    def on_discard_item(orig, self, *args, **kwargs):
        """装備欄の品を捨てるときは、先に所持品へ戻す（本体は持ち物の辞書から消すので、無いと落ちる）。"""
        app = ui.find_app()
        widget = popup_widget(self, app)
        if widget is None or not is_mine(frames.attr(widget, "inventory", None)):
            return orig(self, *args, **kwargs)
        main = find_grid(app)
        if main is None or not main_has_room(main, instance_of(widget)):
            hide_popup(widget)
            write("discard of {!r} ignored: no room to unequip first".format(key_of(widget)))
            return None
        try:
            move_back(app, widget, main)
        except Exception:
            ctx.log_exc("equipment slots: unequipping before discard failed")
            return None
        return orig(self, *args, **kwargs)

    # ドラッグ中の品を控える。本体はドロップの前に `is_valid_placement` で下見をし、
    # 通らなければ `try_place_item` を呼ばずに品を元へ戻す。置換の先出しはその下見で行う。
    @ctx.wrap("scripts.hud.new_hud:InventoryItem.on_touch_down", safe=True)
    def on_touch_down(orig, self, touch, *args, **kwargs):
        try:
            if self.collide_point(*touch.pos) and getattr(instance_of(self), "obtainer", None) is player_of(ui.find_app()):
                placing["dragging"] = self
        except Exception:
            pass
        return orig(self, touch, *args, **kwargs)

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.on_touch_up", safe=True)
    def on_touch_up(orig, self, touch, *args, **kwargs):
        try:
            return orig(self, touch, *args, **kwargs)
        finally:
            if placing.get("dragging") is self:
                schedule(lambda: placing.__setitem__("dragging", None))

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.__init__", safe=True)
    def inventory_item_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            item = getattr(self, "item_instance", None)
            if item is not None and getattr(item, "obtainer", None) is player_of(ui.find_app()):
                self.is_equipped = key_of(self) in container
        except Exception:
            ctx.log_exc("equipment slots: marking failed")
        return result

    def pull_before_window(orig, self, *args, **kwargs):
        """所持品以外の窓（売買・クラフト・強化）。組む前に装備欄の品を辞書から抜く。

        ロード直後は装備欄の品も持ち物の辞書に居る。先にこれらの窓を開くと、本体が 24 マスへ
        全部並べようとして、装備欄の座標の `grid_pos` を持つ品が範囲外に置かれる（108_ と同じ）。
        """
        app = ui.find_app()
        if app is not None:
            try:
                refresh_container(app)
            except Exception:
                ctx.log_exc("equipment slots: refreshing the container before a window failed")
        return orig(self, *args, **kwargs)

    for _target in ("toggle_twin_inventory_visibility", "toggle_craft_inventory_visibility",
                    "toggle_reinforcement_inventory_visibility"):
        ctx.wrap("scripts.hud.new_hud:InstanTaleHUD." + _target, required=False, safe=True)(pull_before_window)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.toggle_center_inventory_visibility", safe=True)
    def toggle_inventory(orig, self, *args, **kwargs):
        app = ui.find_app()
        if app is not None:
            try:
                refresh_container(app)        # 本体が辞書から品を並べる前に、装備欄の品を抜く
            except Exception:
                ctx.log_exc("equipment slots: refreshing the container failed")
        result = orig(self, *args, **kwargs)

        def settle():
            if app is None:
                return
            try:
                build_panel(app)              # 閉じたときは片付けるだけ
                opened = find_grid(app) is not None
                hide_buttons(ui.find_hud(app), opened)
                if opened:
                    schedule(lambda: build_items(app), delay=SETTLE_DELAY)
            except Exception:
                ctx.log_exc("equipment slots: panel failed")

        schedule(settle)
        return result
