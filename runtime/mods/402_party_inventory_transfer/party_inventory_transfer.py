# -*- coding: utf-8 -*-
"""会話中のパーティーメンバーへアイテムを受け渡し、NPC装備も変更する統合MOD v18。

v18:
- 受け渡しで装備を外すとき、equipments の辞書手術より先に本体の Item.unequip() を呼ぶ。
  辞書だけ直すと本体の解除時の後始末（表示更新）が走らず、「装備中」の表示が残り、
  その残骸から本体popupの「外す」を押すと items.py:54 が KeyError で落ちていた（実測）。
- Item.unequip に番人を立てた。equipments がその品を指していない解除は
  本体へ通さず、1行記録して無視する（本体は無条件に辞書を引くため、
  食い違い状態では必ず落ちる）。

v17:
- 装備したままの受け渡しで装備参照が外れず、後からその装備欄に触ると
  本体 items.py:54 unequip が KeyError: 'weapon' で落ちていた（実機）。
  実行時の equipments の値は id 文字列とは限らないため、
  同一instanceでも照合して外すようにした。
- 渡した品の widget.is_equipped も落とす。装備印を持ち越すと
  本体popupが相手側で「外す」を出し、同じ unequip の経路で落ちる。
- 副産物の実測: items.py の unequip は app.player 固定ではなく
  self.obtainer 基準で動く。ただし素のゲームでは装備品の obtainer は常に
  プレイヤーなので両者は区別がつかず、NPC装備対応の証拠ではない
  （NPC の装備装着は素のゲームに存在しない。319 DOC の公式回答）。

v16:
- 観測専用フックを 223_probe_party_equipment へ移し、この本体は機能だけにする。
- Screen.schedule() は Clock から呼ぶ時に自分で guarded で包む。
  screen.guarded(fn) を渡していた2箇所を素の関数へ直した
  （渡すとその場で実行され、戻り値 None が予約されて毎回例外になっていた）。
- 装備解除の保存形を pop へ統一。セーブ上に None 残りとキー無しの2通りを作らない。

v14:
- v1.8共通部品（ui.Screen / party_member_ids / character_of / find_spec_button /
  spec_args / pressed_entry / clamp_into_window / frames.short / frames.repr_value）を継続利用。
- NPC装備の既存直書き経路は壊さず維持。

v13:
- 専用の party_inventory_transfer.log を廃止。
- 通常の成功ログは出さず、警告・失敗・例外だけ modloader.log へ集約。
- UI・受け渡し・装備処理そのものは v12 から変更しない。

v10:
- 402 v9 の受け渡し・所有権同期・save_game処理を維持。
- 403 の役割を統合し、同じ twin inventory のNPC側 weapon / wearable に
  MOD専用の「装備する / 外す」ボタンを出す。
- NPC装備は本体の player 固定 ItemEquipManager を呼ばず、
  そのNPC自身の equipments[weapon|wearable] を item_id で更新して save_game する。
- 会話選択肢は「＜アイテムの受け渡し＞」1本だけ。
- mod.json の after で 301_quest_from_conversation より後に読み込み、ゲーム/301が会話選択肢を組んだ後に ConversationEndManager の直前へ挿入する。
- quest_from_conversation と同じ safe spec + MOD印 + prune_stale 方式。
- タイトル→ロードでMOD印が落ちても、ConversationEndManager spec から会話相手を復元し、
  現在もparty memberなら同じ位置へ差し直す。
- 旧402/403の保存済み残骸ラベルも掃除する。

本体のドラッグ・配置UI・本体ファイルは変更しない。
"""

from instantale_modloader import frames, ui


LABEL = "＜アイテムの受け渡し＞"
OUR_LABELS = (LABEL, "アイテムを受け渡す", "装備を変更する")
MARK = "mod_party_inventory_transfer_equipment"


def apply(ctx):
    def write(message):
        """通常動作は記録せず、異常・警告だけローダー標準ログへ送る。"""
        try:
            text = str(message)
        except Exception:
            return
        lowered = text.lower()
        if (
            text.startswith("WARN")
            or "cannot" in lowered
            or "failed" in lowered
            or "ignored" in lowered
        ):
            ctx.log("party inventory/equipment: " + text)

    screen = ui.Screen(ctx, write, tag="party inventory transfer", mark=MARK)

    state = {
        "npc_id": None,
        "player": None,
        "npc": None,
        "save_generation": 0,
        "equipment_save_generation": 0,
    }

    spec_cls_name = ui.spec_cls_name
    pressed_entry = ui.pressed_entry

    # ------------------------------------------------------------ helpers

    def character_name(character, fallback=""):
        name = getattr(character, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return fallback

    def inventory_dict(owner):
        if owner is None:
            return None
        inv = getattr(owner, "inventory", None)
        if isinstance(inv, dict):
            return inv
        inner = getattr(inv, "inventory", None)
        return inner if isinstance(inner, dict) else None

    def conversation_npc_id(app):
        """現在の会話相手id。

        通常は ConversationStartManager で控えた state["npc_id"] を使う。
        タイトル→ロードではMODのstateは消える一方、会話中のbuttonはセーブから復元される。
        quest_from_conversation と同じ前提で、ConversationEndManager の spec.args[0]
        (= in_conversation_id) から復元する。
        """
        npc_id = state.get("npc_id")
        if npc_id is not None:
            return str(npc_id)

        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return None
        entry = ui.find_spec_button(buttons, "ConversationEndManager")
        if entry is None:
            return None
        args = ui.spec_args(entry)
        if not args:
            return None
        recovered = args[0]
        if recovered is None:
            return None
        recovered = str(recovered)
        state["npc_id"] = recovered
        write("recovered conversation npc_id={!r} from ConversationEndManager".format(recovered))
        return recovered

    def current_party_npc(app):
        npc_id = conversation_npc_id(app)
        if npc_id is None:
            return None
        wanted = str(npc_id)
        if not any(str(x) == wanted for x in ui.party_member_ids(app)):
            return None
        return ui.character_of(app, npc_id)

    def grid_owner(grid):
        return getattr(grid, "obtainer", None)

    def belongs_to_transfer_pair(owner):
        return owner is state.get("player") or owner is state.get("npc")

    # ------------------------------------------------------------ id / ownership

    def key_for_instance(inv, item_instance, preferred=None):
        if not isinstance(inv, dict):
            return None

        if preferred is not None:
            if inv.get(preferred) is item_instance:
                return preferred
            preferred_s = str(preferred)
            if inv.get(preferred_s) is item_instance:
                return preferred_s

        for key, value in inv.items():
            if value is item_instance:
                return key
        return None

    def next_item_id(inv, preferred=None, item_instance=None):
        if preferred is not None:
            preferred_s = str(preferred)
            existing = inv.get(preferred_s)
            if existing is None or existing is item_instance:
                return preferred_s

        used = {str(k) for k in inv.keys()}
        n = 0
        while True:
            candidate = "item_{}".format(n)
            if candidate not in used:
                return candidate
            n += 1

    def is_referenced_in_equipments(owner, item_instance, candidate_ids):
        """持ち主の equipments がこの品を指しているか（同一instance / id文字列の両対応）。"""
        equipments = getattr(owner, "equipments", None)
        if not isinstance(equipments, dict):
            return False
        wanted = {str(x) for x in candidate_ids if x is not None}
        for ref in equipments.values():
            if ref is None:
                continue
            if ref is item_instance or str(ref) in wanted:
                return True
        return False

    def remove_equipped_reference(owner, item_instance, candidate_ids):
        """渡した品への装備参照を、持ち主の equipments から全て落とす。

        実行時の equipments の値は id 文字列とは限らない。エリスの装備武器を
        渡した実機で、id 文字列との比較は何も外せず、残った参照が
        items.py:54 unequip の KeyError: 'weapon' でゲームを落とした
        （unequip は self.obtainer 基準。obtainer は先に相手側へ書き換わっている）。
        だから同一instance（`ref is item_instance`）でも照合する。
        """
        equipments = getattr(owner, "equipments", None)
        if not isinstance(equipments, dict):
            return

        wanted = {str(x) for x in candidate_ids if x is not None}
        removed = []
        for slot, ref in list(equipments.items()):
            if ref is None:
                continue
            if ref is item_instance or str(ref) in wanted:
                # 本体の解除後状態＝slotキー自体を落とす。
                # apply_npc_equipment の解除側と同じ形にして、
                # セーブ上に None 残りと キー無し の2通りを作らない。
                equipments.pop(slot, None)
                removed.append(slot)

        if removed:
            write(
                "unequipped transferred item from {}: {}".format(
                    character_name(owner, "?"), ",".join(removed)
                )
            )

    # ------------------------------------------------------------ persistence

    def save_after_transfer(app, expected_owner, item_instance, expected_id):
        """UIのdrag/drop処理が完了した後に保存する。

        連続移動時は最後の予約だけを有効にする。
        """
        state["save_generation"] += 1
        generation = state["save_generation"]

        def do_save():
            if generation != state["save_generation"]:
                return

            try:
                inv = inventory_dict(expected_owner)
                key = key_for_instance(inv, item_instance, expected_id)

                if key is None:
                    write(
                        "WARN save skipped: transferred item no longer found in {} inventory".format(
                            character_name(expected_owner, "?")
                        )
                    )
                    return

                # Item本体も保存直前に再同期する。
                try:
                    item_instance.id = str(key)
                except Exception:
                    pass
                try:
                    item_instance.obtainer = expected_owner
                except Exception:
                    pass

                write(
                    "saving transfer: owner={} id={} item={!r}".format(
                        character_name(expected_owner, "?"),
                        key,
                        frames.short(getattr(item_instance, "name", "?"), 80),
                    )
                )

                app.save_game()

                write(
                    "save_game complete: owner={} id={}".format(
                        character_name(expected_owner, "?"), key
                    )
                )
            except Exception:
                ctx.log_exc("party inventory transfer: save_game after transfer failed")

        # change_inventory直後はKivy側がgrid_pos等をまだ更新中の可能性がある。
        # 少し待ってからゲーム自身のsave_gameを使う。
        # Screen.schedule は Clock から呼ぶ時に自分で guarded で包む。
        # ここで screen.guarded(do_save) を渡すと、その場で走ったうえに
        # 戻り値 None が予約され、遅延後に NoneType is not callable になる。
        screen.schedule(do_save, 0.15)

    # ------------------------------------------------------------ transfer sync

    def sync_transfer(widget, old_grid, new_grid):
        old_owner = grid_owner(old_grid)
        new_owner = grid_owner(new_grid)

        if not (
            belongs_to_transfer_pair(old_owner)
            and belongs_to_transfer_pair(new_owner)
            and old_owner is not new_owner
        ):
            return

        old_inv = inventory_dict(old_owner)
        new_inv = inventory_dict(new_owner)
        if not isinstance(old_inv, dict) or not isinstance(new_inv, dict):
            write("WARN transfer sync: unreadable owner inventory")
            return

        item_instance = getattr(widget, "item_instance", None)
        old_widget_id = getattr(widget, "item_id", None)

        if item_instance is None:
            write("WARN transfer sync: InventoryItem has no item_instance")
            return

        old_key = key_for_instance(old_inv, item_instance, old_widget_id)
        native_new_key = key_for_instance(new_inv, item_instance, old_widget_id)
        original_item_id = getattr(item_instance, "id", None)

        # 装備中なら、obtainer も equipments もまだ揃っているこの時点で
        # 本体の Item.unequip() に外させる。辞書だけ直すと、本体が解除時に行う
        # 後始末（set_callback 経由の表示更新など）が走らず、「装備中」の表示が
        # 画面に残る。その残骸を右クリックして本体popupの「外す」を押すと、
        # 空の equipments を引いた items.py:54 が KeyError でゲームごと落ちる（実測2件）。
        # 後続の remove_equipped_reference は、この呼び出しで取り切れなかった
        # 残骸の掃除として残す。
        if is_referenced_in_equipments(old_owner, item_instance,
                                       [old_key, old_widget_id, original_item_id]):
            try:
                item_instance.unequip()
            except Exception:
                ctx.log_exc(
                    "party inventory/equipment: native unequip during transfer failed")

        # 同一instanceの参照はどちらからも一度消し、新owner側だけに置く。
        # 旧owner側から消した鍵は、装備参照を外す時の照合候補として控える。
        old_keys = []
        for key, value in list(old_inv.items()):
            if value is item_instance:
                old_inv.pop(key, None)
                old_keys.append(key)
        for key, value in list(new_inv.items()):
            if value is item_instance:
                new_inv.pop(key, None)

        base_id = (
            old_key if old_key is not None
            else native_new_key if native_new_key is not None
            else old_widget_id if old_widget_id is not None
            else getattr(item_instance, "id", None)
        )

        new_id = next_item_id(new_inv, preferred=base_id, item_instance=item_instance)
        new_inv[new_id] = item_instance

        try:
            item_instance.id = new_id
        except Exception:
            pass
        try:
            item_instance.obtainer = new_owner
        except Exception:
            pass
        try:
            widget.item_id = new_id
        except Exception:
            pass

        remove_equipped_reference(
            old_owner, item_instance,
            old_keys + [old_widget_id, original_item_id, base_id])

        # 装備印は旧ownerの状態。実測のクラッシュは装備欄の残存参照経由
        # （上の記録）で、この印の経路は未測だが、持ち越すと本体popupの
        # 文言判定が狂い、同じ unequip に届き得るので塞いでおく。
        try:
            widget.is_equipped = False
        except Exception:
            pass

        write(
            "transfer synced: {} -> {}  id {} -> {}  item={!r}".format(
                character_name(old_owner, "?"),
                character_name(new_owner, "?"),
                base_id,
                new_id,
                frames.short(getattr(item_instance, "name", "?"), 80),
            )
        )

        app = ui.find_app()
        if app is not None:
            save_after_transfer(app, new_owner, item_instance, new_id)
        else:
            write("WARN cannot schedule save: app not found")

    # ------------------------------------------------------------ NPC equipment
    # 本体の ItemPopupMenu -> ItemEquipManager は実機観測で player.equipments を
    # 先に変更するため、NPC側ではその経路を使わない。
    # twin inventory のNPC側だけ、MOD専用ボタンでNPC.equipmentsを直接更新する。

    EQUIP_TYPES = ("weapon", "wearable")
    EQUIP_BUTTON_MARK = "_mod_party_npc_equip_button"

    def item_id_in(owner, item_instance, preferred=None):
        inv = inventory_dict(owner)
        return key_for_instance(inv, item_instance, preferred)

    def equipment_dict(owner, create=False):
        if owner is None:
            return None
        eq = getattr(owner, "equipments", None)
        if isinstance(eq, dict):
            return eq
        if not create:
            return None
        try:
            owner.equipments = {}
            return owner.equipments
        except Exception:
            return None

    def is_equipped_by(owner, item_instance, preferred=None):
        item_id = item_id_in(owner, item_instance, preferred)
        eq = equipment_dict(owner, create=False)
        if item_id is None or not isinstance(eq, dict):
            return False
        return any(ref is not None and str(ref) == str(item_id) for ref in eq.values())

    def save_after_equipment(app, npc, item_instance, action):
        state["equipment_save_generation"] += 1
        generation = state["equipment_save_generation"]

        def do_save():
            if generation != state["equipment_save_generation"]:
                return
            try:
                write(
                    "saving npc equipment: npc={} action={} item={!r} equipments={}".format(
                        character_name(npc, "?"),
                        action,
                        frames.short(getattr(item_instance, "name", "?"), 80),
                        frames.repr_value(getattr(npc, "equipments", None)),
                    )
                )
                app.save_game()
                write("save_game complete: npc={} equipment action={}".format(
                    character_name(npc, "?"), action
                ))
            except Exception:
                ctx.log_exc("party inventory/equipment: save_game after equipment failed")

        screen.schedule(do_save, 0.10)

    def apply_npc_equipment(app, npc, widget, item_instance):
        if app is None or npc is None or item_instance is None:
            return

        # 押した瞬間にも所有権を再確認する。
        if getattr(item_instance, "obtainer", None) is not npc:
            write("equipment ignored: item no longer belongs to npc")
            return

        item_type = getattr(item_instance, "item_type", None)
        if item_type not in EQUIP_TYPES:
            return

        item_id = item_id_in(npc, item_instance, getattr(widget, "item_id", None))
        if item_id is None:
            write("WARN equipment: item id not found in npc inventory")
            return

        eq = equipment_dict(npc, create=True)
        if not isinstance(eq, dict):
            write("WARN equipment: npc.equipments is not writable dict")
            return

        currently = any(
            ref is not None and str(ref) == str(item_id)
            for ref in eq.values()
        )

        if currently:
            # 本体の解除後状態と同じく、そのslotキー自体を落とす。
            removed = []
            for slot, ref in list(eq.items()):
                if ref is not None and str(ref) == str(item_id):
                    eq.pop(slot, None)
                    removed.append(slot)
            action = "unequip"
            write(
                "npc unequipped: npc={} item_id={} item={!r} slots={} equipments={}".format(
                    character_name(npc, "?"),
                    item_id,
                    frames.short(getattr(item_instance, "name", "?"), 80),
                    removed,
                    frames.repr_value(eq),
                )
            )
            try:
                widget.is_equipped = False
            except Exception:
                pass
        else:
            # weapon/wearableはitem_type自身がslot名。
            previous = eq.get(item_type)
            eq[item_type] = str(item_id)
            action = "equip"
            write(
                "npc equipped: npc={} slot={} old={} -> {} item={!r} equipments={}".format(
                    character_name(npc, "?"),
                    item_type,
                    frames.repr_value(previous),
                    item_id,
                    frames.short(getattr(item_instance, "name", "?"), 80),
                    frames.repr_value(eq),
                )
            )
            try:
                widget.is_equipped = True
            except Exception:
                pass

        # popupは一旦閉じる。次回右クリック時に現在のequipmentsから文言を再判定する。
        try:
            widget.hide_popup_menu()
        except Exception:
            pass

        save_after_equipment(app, npc, item_instance, action)

    def add_npc_equipment_button(widget):
        """NPC側InventoryItemのpopupへ、確実に見えるMOD専用ボタンを1つ足す。"""
        app = ui.find_app()
        npc = state.get("npc")
        if app is None or npc is None:
            return

        item_instance = getattr(widget, "item_instance", None)
        if item_instance is None:
            return
        if getattr(item_instance, "obtainer", None) is not npc:
            return
        item_type = getattr(item_instance, "item_type", None)
        if item_type not in EQUIP_TYPES:
            return

        popup = getattr(widget, "active_popup", None)
        if popup is None:
            return

        # 同じpopupへ二重に足さない。
        for child in list(getattr(popup, "children", []) or []):
            if getattr(child, EQUIP_BUTTON_MARK, False):
                return

        try:
            from kivy.uix.button import Button
            from kivy.core.window import Window
        except Exception:
            ctx.log_exc("party inventory/equipment: Kivy Button unavailable")
            return

        # 本体popup内の既存Buttonを見本にし、サイズ・fontだけ共有する。
        template = None
        for child in list(getattr(popup, "children", []) or []):
            if isinstance(child, Button):
                template = child
                break

        if template is not None:
            size = tuple(getattr(template, "size", (120, 40)))
            font_size = getattr(template, "font_size", 14)
            font_name = getattr(template, "font_name", None)
        else:
            size = (140, 40)
            font_size = 14
            font_name = None

        equipped = is_equipped_by(npc, item_instance, getattr(widget, "item_id", None))
        kwargs = {
            "text": "外す" if equipped else "装備する",
            "size_hint": (None, None),
            "size": size,
            "font_size": font_size,
        }
        if font_name:
            kwargs["font_name"] = font_name
        button = Button(**kwargs)
        setattr(button, EQUIP_BUTTON_MARK, True)

        # popupの上下どちらかに1段拡張して必ず可視領域へ置く。
        px = float(getattr(popup, "x", 0) or 0)
        py = float(getattr(popup, "y", 0) or 0)
        pw = float(getattr(popup, "width", size[0] + 8) or (size[0] + 8))
        ph = float(getattr(popup, "height", size[1] + 8) or (size[1] + 8))
        bw = float(button.width)
        bh = float(button.height)
        gap = 4.0
        extra = bh + gap

        try:
            window_h = float(Window.height)
        except Exception:
            window_h = py + ph + extra + 1

        # 下へ伸ばせるなら、既存popupのtopを動かさず下段を追加。
        if py >= extra:
            popup.y = py - extra
            popup.height = ph + extra
            button.pos = (
                px + max(2.0, (pw - bw) / 2.0),
                py - extra + gap / 2.0,
            )
        else:
            # 下が無理なら上へ追加。
            popup.height = ph + extra
            button.pos = (
                px + max(2.0, (pw - bw) / 2.0),
                py + ph + gap / 2.0,
            )
            if py + ph + extra > window_h:
                try:
                    popup.y = max(0.0, window_h - (ph + extra))
                    button.y = popup.y + ph + gap / 2.0
                except Exception:
                    pass

        button.bind(
            on_release=lambda *_: apply_npc_equipment(
                app, npc, widget, item_instance
            )
        )

        try:
            popup.add_widget(button)
            try:
                ui.clamp_into_window(popup)
            except Exception:
                pass
            write(
                "npc equipment button shown: npc={} item_id={} item={!r} type={} action={!r}".format(
                    character_name(npc, "?"),
                    item_id_in(npc, item_instance, getattr(widget, "item_id", None)),
                    frames.short(getattr(item_instance, "name", "?"), 80),
                    item_type,
                    button.text,
                )
            )
        except Exception:
            ctx.log_exc("party inventory/equipment: cannot add npc equipment button")

    # ------------------------------------------------------------ conversation choice

    def has_transfer_button(buttons):
        return any(
            isinstance(entry, dict) and screen.mark_of(entry) == "transfer"
            for entry in buttons
        )

    def conversation_slot(buttons):
        for index, entry in enumerate(buttons):
            if spec_cls_name(entry) == "ConversationEndManager":
                return index
        return None

    def insert_transfer_button(app, buttons):
        if not isinstance(buttons, list):
            return False

        screen.prune_stale(buttons, OUR_LABELS)

        if has_transfer_button(buttons):
            return False

        npc = current_party_npc(app)
        if npc is None:
            return False

        at = conversation_slot(buttons)
        if at is None:
            return False

        entry = screen.button(LABEL, mark="transfer")
        if entry is None:
            return False

        buttons.insert(max(0, min(at, len(buttons))), entry)
        write("added {!r} for {!r}".format(
            LABEL, frames.short(character_name(npc, str(state["npc_id"])), 60)
        ))
        return True

    # ------------------------------------------------------------ labels

    def walk_widgets(root):
        if root is None:
            return
        seen = set()
        stack = [root]
        while stack:
            widget = stack.pop()
            ident = id(widget)
            if ident in seen:
                continue
            seen.add(ident)
            yield widget
            children = getattr(widget, "children", None)
            if isinstance(children, (list, tuple)):
                stack.extend(children)

    def rename_right_header(app, npc_name):
        hud = ui.find_hud(app)
        if hud is None:
            return

        for widget in walk_widgets(hud):
            text = getattr(widget, "text", None)
            if isinstance(text, str) and text.strip() == "所持品":
                try:
                    widget.text = npc_name
                    write("right header -> {!r}".format(npc_name))
                except Exception:
                    ctx.log_exc("party inventory transfer: cannot rename right header")
                return

        write("WARN right header label not found")

    # ------------------------------------------------------------ open window

    def open_transfer(app):
        npc = current_party_npc(app)
        player = getattr(app, "player", None)

        if player is None or npc is None:
            write("cannot open transfer: player or current party NPC not found")
            return

        state["player"] = player
        state["npc"] = npc

        player_name = character_name(player, "プレイヤー")
        npc_name = character_name(npc, str(state["npc_id"]))

        try:
            app.toggle_twin_inventory_window(
                player,
                npc,
                player_name,
                "party_transfer",
            )
            write("opened twin inventory: left={!r} right={!r}".format(
                player_name, npc_name
            ))
            screen.schedule(lambda: rename_right_header(app, npc_name), 0)
        except Exception:
            ctx.log_exc("party inventory transfer: toggle_twin_inventory_window failed")

    # ================================================================ hooks

    @ctx.wrap("__main__:ConversationStartManager.__init__", required=False)
    def conversation_start(orig, self, app, character_id, *args, **kwargs):
        state["npc_id"] = str(character_id) if character_id is not None else None
        return orig(self, app, character_id, *args, **kwargs)

    @ctx.wrap("__main__:ConversationEndManager.finish_conversation", required=False)
    def finish_conversation(orig, self, *args, **kwargs):
        state["npc_id"] = None
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        # ゲーム本体＋このhookより内側のMODに先に一覧を組ませる。
        # mod.json の after=["301_quest_from_conversation"] により、
        # 301の会話選択肢が先に存在する状態を狙う。
        result = orig(self, reset_page, *args, **kwargs)

        try:
            buttons = getattr(self, "buttons", None)
            if not isinstance(buttons, list):
                return result

            # タイトル→ロードで印を失った旧402/403/現行ボタンの残骸を掃除。
            screen.prune_stale(buttons, OUR_LABELS)

            # 現在の会話相手がparty memberでなければ出さない。
            if current_party_npc(self) is None:
                return result

            # insert_transfer_button は ConversationEndManager を探し、
            # その直前へ入れる。301の依頼系ボタンが既に並んでいれば自然にその下になる。
            if insert_transfer_button(self, buttons):
                write("inserted {!r} after existing conversation actions".format(LABEL))
                screen.refresh(self)
        except Exception:
            ctx.log_exc("party inventory/equipment: cannot maintain transfer button")

        return result

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        try:
            entry = pressed_entry(self, button_index)
            action = screen.mark_of(entry)
        except Exception:
            action = None

        if action != "transfer":
            return orig(self, button_index, *args, **kwargs)

        open_transfer(self)
        return None

    @ctx.wrap(
        "scripts.hud.new_hud:InventoryItem.change_inventory",
        required=False,
        safe=True,
    )
    def change_inventory(orig, self, new_inventory, *args, **kwargs):
        old_inventory = getattr(self, "inventory", None)
        result = orig(self, new_inventory, *args, **kwargs)

        try:
            sync_transfer(self, old_inventory, new_inventory)
        except Exception:
            ctx.log_exc("party inventory/equipment: persistent transfer sync failed")

        return result

    @ctx.wrap(
        "scripts.hud.new_hud:InventoryItem.show_popup_menu",
        required=False,
        safe=True,
    )
    def show_popup_menu(orig, self, pos, *args, **kwargs):
        result = orig(self, pos, *args, **kwargs)
        try:
            # popup生成後、同フレーム末尾で本体の配置が終わってから足す。
            screen.schedule(lambda: add_npc_equipment_button(self), 0)
        except Exception:
            ctx.log_exc("party inventory/equipment: cannot schedule npc equipment button")
        return result

    @ctx.wrap("scripts.items:Item.unequip", required=False, safe=True)
    def guard_unequip(orig, self, *args, **kwargs):
        """equipments と食い違う解除を、クラッシュではなく記録つきの何もしないに変える。

        本体の unequip は self.obtainer.equipments[self.item_type] を無条件に引く。
        装備の実体が別の場所へ移った後に古い「装備中」表示から解除が飛ぶと、
        そこで KeyError になりゲームごと落ちる（実測2件。どちらもこのMODの
        受け渡し・装備替えが作った表示の残骸が引き金）。
        持ち主の equipments がこの品を指している正常な解除だけ本体へ通す。
        """
        equipments = getattr(getattr(self, "obtainer", None), "equipments", None)
        slot = getattr(self, "item_type", None)
        ref = equipments.get(slot) if isinstance(equipments, dict) else None
        item_id = getattr(self, "id", None)
        if ref is not None and (
            ref is self or (item_id is not None and str(ref) == str(item_id))
        ):
            return orig(self, *args, **kwargs)
        write(
            "stale unequip ignored: item={!r} slot={} owner={} ref={!r}".format(
                frames.short(getattr(self, "name", "?"), 80),
                slot,
                character_name(getattr(self, "obtainer", None), "?"),
                frames.short(ref, 80),
            )
        )
        return None

    @ctx.wrap("__main__:InstantaleApp.return_to_title", required=False)
    def return_to_title(orig, self, *args, **kwargs):
        # 保存済みボタンはゲーム側に任せる。MODの一時参照だけ捨て、
        # ロード後はConversationEndManager specから復元する。
        state["npc_id"] = None
        state["player"] = None
        state["npc"] = None
        state["save_generation"] += 1
        state["equipment_save_generation"] += 1
        write("return_to_title: transient transfer/equipment state cleared")
        return orig(self, *args, **kwargs)

    ctx.log(
        "party inventory transfer/equipment v18: after-301 conversation insert installed; "
        "routine operation logs disabled; warnings/errors -> modloader.log"
    )
