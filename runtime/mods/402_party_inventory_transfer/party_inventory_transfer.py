# -*- coding: utf-8 -*-
"""機能追加: 会話中の仲間へアイテムを渡し、仲間から受け取る。

素のゲームには、同行している仲間へアイテムを渡す手段が無い
（自由入力で頼む以外に無く、帰ってくる保証も無い）。
この MOD は仲間との会話に「＜アイテムの受け渡し＞」を1つ足し、
押すと店の売買と同じ2枚並びの窓（`toggle_twin_inventory_window`）を
場面名 `party_transfer` で開く。左が自分、右がその仲間。
ドラッグで移した品は持ち主ごと移り、ゲーム自身の `save_game` で保存される。
本体のドラッグ・配置 UI・本体ファイルは変更しない。

## 選択肢

`301_quest_from_conversation` と同じ safe spec ＋ MOD 印 ＋ prune_stale 方式。
mod.json の `after` で 301 より後に読み込み、ゲームと 301 が選択肢を組んだ後に
「会話を終了する」（`ConversationEndManager`）の直前へ差す。
タイトル→ロードで印が落ちても、`ConversationEndManager` の spec から会話相手を復元し、
今も仲間なら同じ位置へ差し直す。旧版の文言の残骸も掃除する。

## 受け渡しの同期

本体の `InventoryItem.change_inventory` は画面側の登録を動かすだけなので、
その直後に持ち物の実体（`inventory` の辞書）・`Item.id`・`Item.obtainer` を
新しい持ち主へ揃え、少し待ってから保存する。連続で動かしたときは最後の1回だけ保存する。
`Screen.schedule()` は Clock から呼ぶときに自分で guarded を掛けるので、素の関数を渡す
（`guarded(fn)` を渡すとその場で走り、戻り値 None が予約されて毎回例外になる）。

装備中の品を渡すときは、obtainer も equipments も揃っている時点で本体の
`Item.unequip()` に外させる。辞書だけ直すと本体の解除時の後始末（表示更新）が走らず、
「装備中」の表示が残ってそこからの解除で本体が落ちる。辞書手術は残骸掃除として残す。
照合は同一 instance と id 文字列の両方で行う
（実行時の equipments の値は id 文字列とは限らない。セーブに焼く時だけ id になる）。

`Item.unequip` には番人を立てる。equipments がその品を指していない解除は本体へ通さず、
1行記録して無視する（本体は無条件に辞書を引くため、食い違い状態では必ず落ちる）。

装備欄の MOD（`333_`）が居るときは、仲間の `equipments` はそちらだけが書く。ローダの窓口
`combat.equipped` が答える持ち主の品では、解除も掃除もしない（DOC.md「「装備する」ボタンは記録を書く」）。

## 仲間の装備

NPC の装備装着は素のゲームに存在しない（`319_` の DOC にある公式回答）。
本体の `ItemEquipManager` はプレイヤー固定なので呼ばず、窓の右側の武器・防具を右クリックしたとき
MOD 専用の「装備する／外す」ボタンを 1 つ出し、その NPC 自身の
`equipments[weapon|wearable]` を id で書いて `save_game` する。
本体は仲間側の品に popup を出さない（`ItemPopupMenu` を作るだけで親を付けない。店の品と同じ扱い）ので、
本体の popup へ足すのではなく、押した位置に自前のボタンを Window の直下に置く（品の説明の箱より上）。
外を触れば消える。この記録を読むのは `401_`（審判への文）と、`333_`＋`319_` を入れているときの戦闘の数。
解除時は slot キーごと落とす（本体がプレイヤーの装備を外した後と同じ形）。

## ログ

移動・保存・装備の書き換えは `out/party_inventory_transfer.log` に1件ずつ残る。
例外だけはローダの `modloader.log`。
popup と装備欄の中身まで写す観測は `223_probe_party_equipment` に分けてある。
"""

from instantale_modloader import combat, frames, ui


#: 会話に足す選択肢の文言。
LABEL = "＜アイテムの受け渡し＞"
#: この MOD（と旧版）が作った選択肢の文言。印を失った残骸を文言で見分けて掃除する
#: （`screen.prune_stale`）。旧版の2つは今は作らないが、古いセーブに残り得る。
OUR_LABELS = (LABEL, "アイテムを受け渡す", "装備を変更する")
#: 選択肢の dict に付ける印のキー。値が "transfer" なら受け渡しボタン。
#: 404_party_talk はこのキーを見て、パーティー会話の間だけこのボタンを隠す。
MARK = "mod_party_inventory_transfer_equipment"

#: `out/` に置くこの MOD のログ。移動・保存・装備の書き換えが1件ずつ残る。
#: 例外だけはローダの `modloader.log`（`ctx.log_exc`）。
LOG_BASENAME = "party_inventory_transfer.log"


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # ローダ共通の画面部品。選択肢の生成（`button`）・印の読み取り（`mark_of`）・
    # 残骸掃除（`prune_stale`）・Clock への予約（`schedule`）・描き直し（`refresh`）を担う。
    screen = ui.Screen(ctx, write, tag="party inventory transfer", mark=MARK)

    # apply() の外へ持ち出さない控え。
    state = {
        "npc_id": None,                  # 今の会話相手の id。会話開始で控え、終了で消す
        "player": None,                  # 受け渡しの窓の左側（同一instanceで見分ける）
        "npc": None,                     # 同じく右側。この2人の間の移動だけ同期する（店の売買には触らない）
        "save_generation": 0,            # 遅延保存の世代番号。予約のたびに増やし、古い予約は走らない
        "equipment_save_generation": 0,  # 同じく装備の書き換え後の保存
        "open": False,                   # 受け渡しの窓を開いている間 True（閉じた後の画面を1行残すため）
    }

    # 選択肢の dict から spec のクラス名を読む／押された index の dict を引く（ローダ共通）。
    spec_cls_name = ui.spec_cls_name
    pressed_entry = ui.pressed_entry

    # ------------------------------------------------------------ 補助

    def character_name(character, fallback=""):
        """ログ用の名前。無ければ `fallback`（id ではなく Character を持っている場面用）。"""
        return frames.short(frames.text_of(character, "name"), 80) or fallback

    def inventory_dict(owner):
        """持ち物の実体 `{item_id: Item}`。

        Character の `inventory` は Inventory オブジェクトで、その `.inventory` が辞書。
        辞書を直接持つ形にも備える。読めなければ None。
        """
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
        """会話相手が今のパーティーメンバーなら、その Character。違えば None。"""
        npc_id = conversation_npc_id(app)
        if npc_id is None:
            return None
        wanted = str(npc_id)
        if not any(str(x) == wanted for x in ui.party_member_ids(app)):
            return None
        return ui.character_of(app, npc_id)

    def grid_owner(grid):
        """2枚並びの窓の片側（Inventory）の持ち主 Character。"""
        return getattr(grid, "obtainer", None)

    def belongs_to_transfer_pair(owner):
        """受け渡しの窓を開いている2人のどちらかか（同一instanceで見る）。"""
        return owner is state.get("player") or owner is state.get("npc")

    # ------------------------------------------------------------ id と持ち主
    # 持ち物は `{item_id: Item}`。Item 自身も `id` と `obtainer`（持ち主）を持つ。
    # 本体のドラッグ処理は画面側の登録を動かすだけで、渡した先の持ち物の辞書・
    # Item.id・Item.obtainer までは揃えない。ここから下がその同期。

    def key_for_instance(inv, item_instance, preferred=None):
        """持ち物の辞書の中でこの Item を指している鍵。無ければ None。

        `preferred`（画面のボタンが覚えている id）が合っていればそれを返し、
        違っていれば全件を同一instanceで走査する。文字列比較はしない
        （同じ id の別の Item が両側に居ることがある）。
        """
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
        """渡した先の持ち物で使う鍵を決める。

        元の id（`preferred`）が空いているか、既にこの Item 自身を指していればそのまま。
        相手側に同じ id の別の品が居るときだけ `item_N` の空き番号を振る。
        """
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
        """持ち主の equipments がこの品を指しているか（同一instance / id文字列の両対応）。

        `equipments` は `{"weapon": 参照, "wearable": 参照}`。参照は実行時には
        Item オブジェクト、セーブから読んだ直後は id の文字列。
        """
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

        `sync_transfer` で本体の `Item.unequip()` に外させた後の残骸掃除。
        実行時の equipments の値は id 文字列とは限らないので、id 文字列との比較
        だけでは装備中の武器を取りこぼす。そのまま残ると、本体の `unequip` が
        `self.obtainer.equipments[self.item_type]` を引いたときに KeyError で落ちる
        （`obtainer` は先に相手側へ書き換わっている。DOC.md「困ったとき」）。
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

    # ------------------------------------------------------------ 保存

    def save_after_transfer(app, expected_owner, item_instance, expected_id):
        """UIのdrag/drop処理が完了した後に保存する。

        連続移動時は最後の予約だけを有効にする（世代番号 `save_generation`）。
        走るときに Item がまだ `expected_owner` の持ち物に居るかを確かめ、
        居なければ保存せず WARN だけ残す（次の移動の保存が後から走る）。
        保存の直前に Item.id / obtainer を鍵と持ち主へもう一度合わせるのは、
        予約から実行までの間に本体側が触っている可能性への備え。
        保存はゲーム自身の `app.save_game()`。セーブの形式には触らない。
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

    # ------------------------------------------------------------ 受け渡しの同期

    def sync_transfer(widget, old_grid, new_grid):
        """ドラッグで片側から片側へ移った直後に、持ち物の実体を持ち主に合わせる。

        `InventoryItem.change_inventory` の包みから呼ばれる。`widget` が画面の
        ボタン（`item_instance` と `item_id` を持つ）、`old_grid` / `new_grid` が
        移動前後の Inventory。手順:
          1. 受け渡しの窓の2人の間の移動でなければ何もしない（店の売買は素通し）
          2. 装備中なら本体の `Item.unequip()` に外させる
          3. 同じ Item を指す鍵を両側から全部消し、新しい側に1つだけ置く
          4. Item.id / Item.obtainer / widget.item_id を新しい側に合わせる
          5. 旧持ち主の equipments に残った参照を掃除し、装備印を落とす
          6. 少し待ってから保存を予約する
        """
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

        # 本体の change_inventory が既に新しい側へ登録している場合があるので、
        # 旧側・新側の両方で鍵を探しておく（どちらが見つかるかは本体の実装次第）。
        old_key = key_for_instance(old_inv, item_instance, old_widget_id)
        native_new_key = key_for_instance(new_inv, item_instance, old_widget_id)
        original_item_id = getattr(item_instance, "id", None)

        # 装備欄の MOD（333_）がこの持ち主の装備を持っていれば、`equipments` はそちらが書く。
        # ここで外すと書き手が 2 本になり、装備欄から主人公側へ引いた品が仲間の持ち物にも残った
        # （DOC.md「「装備する」ボタンは記録を書く」）。窓口が None なら装備欄は無く、ここで外す
        app = ui.find_app()
        slots_answer = combat.equipped(app, old_owner, item_instance) if app is not None else None

        # 装備中なら、obtainer も equipments もまだ揃っているこの時点で
        # 本体の Item.unequip() に外させる。辞書だけ直すと、本体が解除時に行う
        # 後始末（set_callback 経由の表示更新など）が走らず、「装備中」の表示が
        # 画面に残る。その残骸を右クリックして本体popupの「外す」を押すと、
        # 空の equipments を引いた本体の unequip が KeyError でゲームごと落ちる
        # （DOC.md「困ったとき」）。後続の remove_equipped_reference は、
        # この呼び出しで取り切れなかった残骸の掃除として残す。
        if slots_answer is None and is_referenced_in_equipments(
                old_owner, item_instance, [old_key, old_widget_id, original_item_id]):
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

        # 新しい側で使いたい id。旧側の鍵 → 本体が新側に付けた鍵 → ボタンの id →
        # Item.id の順で、最初に読めたもの。
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

        if slots_answer is None:
            remove_equipped_reference(
                old_owner, item_instance,
                old_keys + [old_widget_id, original_item_id, base_id])
        else:
            write("equipment of {} is left to the equipment slots (equipped={} id={!r} at the check)".format(
                character_name(old_owner, "?"), slots_answer, original_item_id))

        # ボタンの `is_equipped` は旧持ち主のときの状態。持ち越すと本体popupの
        # 「装備する／外す」の文言判定が狂い、同じ unequip に届き得るので塞いでおく
        # （この印だけが引き金になった例は無い。念のための処置）。
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

        if app is not None:
            save_after_transfer(app, new_owner, item_instance, new_id)
        else:
            write("WARN cannot schedule save: app not found")

    # ------------------------------------------------------------ 仲間の装備
    # 本体の ItemPopupMenu -> ItemEquipManager はプレイヤー固定で、NPC の品で押すと
    # player.equipments を書き換える。だから NPC 側ではその経路を使わず、
    # twin inventory の NPC 側だけ、MOD専用ボタンで NPC.equipments を直接更新する。
    # これは MOD が作る記録で、素のゲームは読まない（DOC.md「装備する」の節）。

    #: 装備できる item_type。本体の equipments のキー（slot 名）と同じ文字列。
    EQUIP_TYPES = ("weapon", "wearable")
    #: MOD が足したボタンに付ける印（属性名）。同じ popup へ二重に足さないため。
    EQUIP_BUTTON_MARK = "_mod_party_npc_equip_button"
    menu_skip = set()

    def skip_once(reason, widget):
        """ボタンを出せなかった理由を、理由ごとに 1 回だけ残す（黙って戻ると切り分けられない）。"""
        if reason in menu_skip:
            return
        menu_skip.add(reason)
        write("npc equipment button skipped: {} (item_id={})".format(
            reason, getattr(widget, "item_id", None)))

    def item_id_in(owner, item_instance, preferred=None):
        """持ち主の持ち物の中でのこの Item の鍵。"""
        inv = inventory_dict(owner)
        return key_for_instance(inv, item_instance, preferred)

    def equipment_dict(owner, create=False):
        """`owner.equipments`。無い／辞書でないときは `create=True` なら空の辞書を作って付ける。"""
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
        """MOD の記録上、この持ち主がこの品を装備しているか（ボタンの文言を決める）。"""
        item_id = item_id_in(owner, item_instance, preferred)
        eq = equipment_dict(owner, create=False)
        if item_id is None or not isinstance(eq, dict):
            return False
        return any(ref is not None and str(ref) == str(item_id) for ref in eq.values())

    def save_after_equipment(app, npc, item_instance, action):
        """装備の記録を書き換えた後の保存。`save_after_transfer` と同じ世代番号方式。"""
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
        """MOD専用ボタンを押したときの処理。装備していれば外し、していなければ装備する。

        書くのは `npc.equipments[item_type]` に id 文字列を入れる／slot ごと消す、だけ。
        本体の ItemEquipManager は呼ばない（プレイヤー固定のため）。
        popup は閉じ、次に右クリックしたときに現在の記録から文言を決め直す。
        """
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

    def remove_npc_menu():
        """出している自前のボタンを消す（同時に 1 つだけ）。"""
        button = state.get("menu")
        state["menu"] = None
        if button is not None:
            parent = getattr(button, "parent", None)
            if parent is not None:
                try:
                    parent.remove_widget(button)
                except Exception:
                    pass
        unbind = state.get("menu_unbind")
        state["menu_unbind"] = None
        if callable(unbind):
            try:
                unbind()
            except Exception:
                pass

    def show_npc_menu(widget, pos):
        """仲間側の武器・防具を右クリックしたとき、その位置に「装備する／外す」を 1 つ出す。

        本体は仲間側の品には popup を出さない（`ItemPopupMenu` を作るだけで親を付けない。
        実機 2026-09-23。店の品と同じ扱い）。だから本体の popup へ足すのではなく、本体の popup と
        同じ置き場（品のウィジェットの親＝窓の FloatLayout）に自前のボタンを置く。
        外を触ったら消える。押すと記録を書き換えて消える。
        """
        app = ui.find_app()
        npc = state.get("npc")
        if app is not None and npc is None:
            npc = current_party_npc(app)          # 窓を開いたまま注入し直した（state が空）
            if npc is not None:
                state["npc"] = npc
                state["player"] = getattr(app, "player", None)
        if app is None or npc is None:
            skip_once("no app or no party npc in the transfer state", widget)
            return
        item_instance = getattr(widget, "item_instance", None)
        if item_instance is None:
            skip_once("widget has no item_instance", widget)
            return
        if getattr(item_instance, "obtainer", None) is not npc:
            return                                  # 自分側の品は本体の popup に任せる
        item_type = getattr(item_instance, "item_type", None)
        if item_type not in EQUIP_TYPES:
            return
        try:
            from kivy.uix.button import Button
            from kivy.core.window import Window
        except Exception:
            write("npc equipment menu skipped: kivy unavailable")
            return
        host = Window            # 品の説明の箱（hover で出る）より上に描くため、窓ではなく Window の直下

        remove_npc_menu()
        # 見た目は本体の popup のボタンと同じにする（実機で写した描き方。DOC.md「装備する」の節）:
        # 75×37、背景は黒 70%、文字は白の MisakiGothic 20、下地に暗い矩形、四辺に明るい 2px の縁
        template = None
        for child in list(getattr(getattr(widget, "popup_menu", None), "children", []) or []):
            if isinstance(child, Button):
                template = child
                break
        kwargs = {"size_hint": (None, None), "size": (75, 37), "background_normal": "",
                  "background_color": (0, 0, 0, 0.7), "color": (1, 1, 1, 1), "font_size": 20}
        if template is not None:
            kwargs["size"] = tuple(template.size)
            for name in ("font_size", "font_name", "background_normal", "background_down",
                         "background_color", "color", "border"):
                value = getattr(template, name, None)
                if value is not None:
                    kwargs[name] = value
        equipped = combat.equipped(app, npc, item_instance)        # 装備欄の MOD が居ればそちらの記録
        if equipped is None:
            equipped = is_equipped_by(npc, item_instance, getattr(widget, "item_id", None))
        button = Button(text="外す" if equipped else "装備", **kwargs)
        setattr(button, EQUIP_BUTTON_MARK, True)
        try:
            from kivy.graphics import Color, Rectangle
            with button.canvas.before:
                Color(0.07, 0.06, 0.05, 0.5)
                shade = Rectangle(pos=button.pos, size=button.size)
            with button.canvas.after:
                Color(0.8, 0.78, 0.76, 1.0)
                edges = [Rectangle() for _ in range(4)]

            def redraw(*_):
                x, y, w, h = button.x, button.y, button.width, button.height
                shade.pos, shade.size = (x, y), (w, h)
                for edge, (pos, size) in zip(edges, (((x, y + h - 2), (w, 2)), ((x, y), (w, 2)),
                                                    ((x, y), (2, h)), ((x + w - 2, y), (2, h)))):
                    edge.pos, edge.size = pos, size
            redraw()
            button.bind(pos=redraw, size=redraw)
        except Exception:
            ctx.log_exc("party inventory/equipment: button look failed")
        try:
            x, y = float(pos[0]), float(pos[1])
        except Exception:
            x, y = float(getattr(widget, "x", 0)), float(getattr(widget, "top", 0))
        button.pos = (x, y - float(button.height))   # 本体の popup と同じく、押した点の下に
        try:
            ui.clamp_into_window(button)
        except Exception:
            pass

        def pressed(*_):
            write("npc equipment button pressed: {!r}".format(button.text))
            done = combat.toggle(app, npc, item_instance)              # 装備欄の MOD が居ればそちらが移す
            if done is None:
                apply_npc_equipment(app, npc, widget, item_instance)
            else:
                write("npc equipment via the equipment slots: {}".format(done))
            remove_npc_menu()

        def outside(_window, touch):
            if not button.collide_point(*touch.pos):
                screen.schedule(remove_npc_menu, 0)
            return False

        button.bind(on_release=pressed)
        try:
            host.add_widget(button)
        except Exception:
            ctx.log_exc("party inventory/equipment: cannot add npc equipment button")
            return
        Window.bind(on_touch_down=outside)
        state["menu"] = button
        state["menu_unbind"] = lambda: Window.unbind(on_touch_down=outside)
        write("npc equipment button shown: npc={} item_id={} item={!r} type={} action={!r} pos={}".format(
            character_name(npc, "?"),
            item_id_in(npc, item_instance, getattr(widget, "item_id", None)),
            frames.short(getattr(item_instance, "name", "?"), 80),
            item_type, button.text, [int(button.x), int(button.y)]))

    # ------------------------------------------------------------ 会話の選択肢
    # 会話の選択肢は `app.buttons` の list（1件が dict。`spec` に押したときの処理）。
    # 本体の「会話を終了する」は spec が ConversationEndManager。その直前へ差す。

    def has_transfer_button(buttons):
        """受け渡しボタンが既に並んでいるか（印で見る）。"""
        return any(
            isinstance(entry, dict) and screen.mark_of(entry) == "transfer"
            for entry in buttons
        )

    def conversation_slot(buttons):
        """差し込む位置 = 「会話を終了する」の index。会話画面でなければ None。"""
        for index, entry in enumerate(buttons):
            if spec_cls_name(entry) == "ConversationEndManager":
                return index
        return None

    def insert_transfer_button(app, buttons):
        """条件が揃っていれば受け渡しボタンを差す。差したら True。

        残骸掃除 → 既に在れば何もしない → 相手が仲間でなければ出さない →
        会話画面でなければ出さない、の順に見る。
        """
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

    # ------------------------------------------------------------ 見出しの描き替え

    def rename_right_header(app, npc_name):
        """2枚並びの窓の右側の見出しを仲間の名前にする。

        借りているのは店の売買UIなので、右の見出しは「所持品」で固定。
        HUD の中から文言「所持品」のウィジェットを探し、最初の1つだけ書き換える。
        窓を開いた次のフレームに呼ぶ（開いた直後はまだ組み上がっていない）。
        """
        hud = ui.find_hud(app)
        if hud is None:
            return

        for widget in ui.walk_widgets(hud, oldest_first=True):
            text = getattr(widget, "text", None)
            if isinstance(text, str) and text.strip() == "所持品":
                try:
                    widget.text = npc_name
                    write("right header -> {!r}".format(npc_name))
                except Exception:
                    ctx.log_exc("party inventory transfer: cannot rename right header")
                return

        write("WARN right header label not found")

    # ------------------------------------------------------------ 窓を開く

    def open_transfer(app):
        """受け渡しの窓を開く。選択肢を押したときの処理。

        `app.toggle_twin_inventory_window(左の持ち主, 右の持ち主, 左の見出し, 場面名)`
        は本体が店の売買に使っている窓。場面名 `party_transfer` は本体に無い名前で、
        本体の売買処理（値段・所持金）がこの窓に掛からないようにしている。
        開いた2人を state に控え、以後のドラッグの同期はこの2人の間だけを見る。
        """
        npc = current_party_npc(app)
        player = getattr(app, "player", None)

        if player is None or npc is None:
            write("cannot open transfer: player or current party NPC not found")
            return

        state["player"] = player
        state["npc"] = npc

        player_name = character_name(player, "プレイヤー")
        npc_name = character_name(npc, str(state["npc_id"]))
        keep_conversation_choices(app)

        try:
            state["open"] = True
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
            state["open"] = False
            ctx.log_exc("party inventory transfer: toggle_twin_inventory_window failed")

    def keep_conversation_choices(app):
        """窓を閉じたときにゲームが戻す選択肢を、今の会話の選択肢にしておく。

        売買の窓を閉じると、ゲームは `app.buttons_backup_for_shopping` を選択肢へ戻す。
        これを書くのは売買や装備の強化の入口で、`toggle_twin_inventory_window` は書かない。
        そのまま借りると、**前に開いた店の選択肢**が戻り、会話の旗
        （`in_conversation`）だけが残る。そこから「出る」で施設を離れられるので、
        旗は会話を1度終えるまで下りず、旗を見る MOD が止まる（VERIFICATION.md §3.72）。
        """
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            write("WARN cannot keep the conversation choices: app.buttons is {}".format(
                type(buttons).__name__))
            return
        try:
            app.buttons_backup_for_shopping = list(buttons)
        except Exception:
            ctx.log_exc("party inventory transfer: cannot keep the conversation choices")
            return
        write("kept the conversation choices for the window: {}".format(
            [spec_cls_name(entry) for entry in buttons]))

    # ================================================================ フック

    @ctx.wrap("__main__:InstantaleApp.close_shopping_window_process", required=False,
              safe=True)
    def close_shopping_window(orig, self, *args, **kwargs):
        """受け渡しの窓を閉じた後の画面を1行残す（会話に戻れたかを後から読むため）。"""
        result = orig(self, *args, **kwargs)
        if state["open"]:
            state["open"] = False

            def report():
                # 選択肢の塗り直しは閉じる処理の後に来ることがあるので、次のフレームで読む。
                buttons = getattr(self, "buttons", None)
                write("closed the window: in_conversation={!r} choices={}".format(
                    getattr(self, "in_conversation", None),
                    [spec_cls_name(entry) for entry in buttons]
                    if isinstance(buttons, list) else None))

            screen.schedule(report, 0)
        return result

    @ctx.wrap("__main__:ConversationStartManager.__init__", required=False)
    def conversation_start(orig, self, app, character_id, *args, **kwargs):
        """会話の入口。相手の id を控える（誰との会話かはここでしか分からない）。"""
        state["npc_id"] = str(character_id) if character_id is not None else None
        return orig(self, app, character_id, *args, **kwargs)

    @ctx.wrap("__main__:ConversationEndManager.finish_conversation", required=False)
    def finish_conversation(orig, self, *args, **kwargs):
        """会話の出口。相手の控えを消す。窓の2人（player / npc）は次に開くまで残してよい。"""
        state["npc_id"] = None
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """選択肢を描き直すたびに呼ばれる本体の関数。ここで受け渡しボタンを維持する。

        本体は `app.buttons` を組んでからこの関数で画面に並べる。ロード直後・
        選択肢を押した後・ページ送りなど、描き直しのたびに通るので、
        毎回「在るべきなら在る、無いべきなら無い」に揃える。
        """
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
        """選択肢が押されたとき。押されたのが受け渡しボタンなら本体へ渡さず窓を開く。"""
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
        """ドラッグで品が別の側へ落ちたときに本体が呼ぶ。`self` は画面のボタン（InventoryItem）。

        本体に先に処理させてから、移動前後の Inventory を `sync_transfer` へ渡す。
        同期の失敗はログに残すだけで、本体の戻り値はそのまま返す。
        """
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
        """品を右クリックしたときの本体の popup。仲間側の品には本体が出さないので、自前のボタンを出す。"""
        result = orig(self, pos, *args, **kwargs)
        try:
            screen.schedule(lambda: show_npc_menu(self, pos), 0)
        except Exception:
            ctx.log_exc("party inventory/equipment: cannot schedule npc equipment button")
        return result

    @ctx.wrap("scripts.items:Item.unequip", required=False, safe=True)
    def guard_unequip(orig, self, *args, **kwargs):
        """equipments と食い違う解除を、クラッシュではなく記録つきの何もしないに変える。

        本体の unequip は self.obtainer.equipments[self.item_type] を無条件に引く。
        装備の実体が別の場所へ移った後に古い「装備中」表示から解除が飛ぶと、
        そこで KeyError になりゲームごと落ちる（このMODの受け渡し・装備替えが
        作った表示の残骸が引き金になった。DOC.md「困ったとき」）。
        持ち主の equipments がこの品を指している正常な解除だけ本体へ通す。
        プレイヤー自身の通常の装備解除もここを通るが、その場合は参照が
        一致するので素通し。
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
        """タイトルへ戻るとき。控えを全部捨て、走っていない遅延保存も無効にする。"""
        # 保存済みボタンはゲーム側に任せる。MODの一時参照だけ捨て、
        # ロード後はConversationEndManager specから復元する。
        # 世代番号を進めるのは、予約済みの保存が別のセーブへ走らないようにするため。
        state["npc_id"] = None
        state["player"] = None
        state["npc"] = None
        state["save_generation"] += 1
        state["equipment_save_generation"] += 1
        write("return_to_title: transient transfer/equipment state cleared")
        return orig(self, *args, **kwargs)

    ctx.log("party inventory transfer: installed (after 301); log -> {}".format(
        ctx.out_path(LOG_BASENAME)))
