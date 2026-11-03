# -*- coding: utf-8 -*-
"""計測: 本体の装備経路が誰の equipments を触るか。

##### 何を決めるための計測か（→ 問いは閉じた）

`402_party_inventory_transfer` は NPC の装備を、本体の
`ItemEquipManager` を呼ばずに NPC 自身の `equipments` を直接書いて変えている。
本体の経路をそのまま使えるなら直書きは要らなくなるので、その材料を録っていた。

**答えは出た。使える本体経路は無い。**
NPC の装備装着は素のゲームに存在しない（NPC は武器を参照しない、という
公式回答。319 DOC）。装備品の obtainer は素のゲームでは常にプレイヤーなので、
items.py の unequip が self.obtainer 基準で書かれていても NPC 対応の証拠にはならない。
402 の直書きが唯一の道。

残す目的は1つ: 402 v17（装備したままの受け渡しの修正）の確認セッションで、
装備欄の**値**の動きをログに写すこと。402 自身は通常動作を記録しないため。
確認が済んだらこの MOD は退役してよい。

##### どこで測るか

| 見るもの | 対象 |
|---|---|
| 右クリックで出る popup の中身と、その時点の player / NPC の `equipments` | `scripts.hud.new_hud:InventoryItem.show_popup_menu` |
| 装備の入口の前後で Manager が誰を握っているか | `__main__:ItemEquipManager.__init__` / `execute` / `equip_item` |
| 解除側も同じ | `__main__:ItemUnequipManager.__init__` / `execute` / `unequip_item` |

会話相手の id は `ConversationStartManager.__init__` で控える。
MOD どうしは import しない（TECH.md §3.2.3）ので、402 の state は見ない。

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。
引数も戻り値も装備処理も触らず、記録に失敗しても本体は必ず呼ぶ。

##### 版の記録

- 版5: 402 の確認は済んでいた（402 は版21 まで進んだ）が、デバッグモードで毎回動き続け、
  ログの 8 割が `333_` の内部の装備（マネージャを組み直して `equip_item` を直に呼ぶ）
  だった。次のように直した。
  - 受け取った引数をそのまま `orig` へ渡す。`app` / `character_id` / `pos` /
    `item_instance` を名前で受けて位置で渡していたので、内側の MOD が受け取る
    呼び方が素と変わっていた（`227_` で実例。VERIFICATION.md §3.76）
  - 行数の枠（`LOG_CAP`）を使い切った後は、行を組む前・popup の予約の前に降りる
    （前は装備欄を文字にしてから、ロガーの枠で捨てていた）
  - マネージャの `__init__` はクラスごとに注入1回につき1行にした
- 版6: それでもマネージャの `__init__` の行が毎プロセス2行ずつ出て、ログの 9 割を占めていた
  （中身はいつも `keys=['app']` で装備欄は空）。次のように直した。
  - `__init__` の行は、マネージャの属性の鍵の形が前の形と違うときだけ書く。
    前の形は `sys` に控えて注入し直しをまたぐ。プロセスの最初の「前の形」は、
    版5までの行で毎回同じだった `('app',)`（`KNOWN_INIT_SHAPE`）にしてある。
    `sys` の控えだけではプロセスごとに1回は書くので、起動のたびに2行が残っていた
  - 自前の `arg_of` をやめ、ローダの `frames.arg` で引数を引く
"""

import sys

from instantale_modloader import frames, ui


LOG_BASENAME = "party_equipment_probe.log"
LOG_CAP = 500
SEEN_CAP = 300

# 1プロセスで持ち越す控え（版6）。`init_shapes` はクラスごとの、前に見た鍵の形。
STORE_ATTR = "_instantale_probe_party_equipment"

# マネージャの `__init__` の後の鍵の形として、版5までの行で毎回写っていたもの（版6）。
# プロセスの最初はこれを「前の形」にする。違う形が来たら1行書く。
KNOWN_INIT_SHAPE = ("app",)


def process_store():
    """1プロセスで持ち越す控え。`sys` に置いて注入し直しをまたぐ（版6）。"""
    found = getattr(sys, STORE_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, STORE_ATTR, found)
    found.setdefault("init_shapes", {})
    return found


def shape_of(manager):
    """マネージャの属性の鍵の形。読めなければ型名だけ。"""
    try:
        return tuple(sorted(str(key) for key in vars(manager)))
    except Exception:
        return ("<{}>".format(type(manager).__name__),)


def apply(ctx):
    _probe = ctx.logger(LOG_BASENAME, tag="party equipment probe:", cap=LOG_CAP)
    schedule = ui.scheduler(ctx, "party equipment probe")
    state = {"npc_id": None}
    seen = set()
    store = process_store()
    written = [0]

    def probe(text):
        written[0] += 1
        _probe(text)

    def has_room():
        """この注入で書ける行が残っているか。行を組む前に見る（版5）。"""
        return written[0] < LOG_CAP

    def character_name(character, fallback=""):
        name = getattr(character, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return fallback

    def conversation_npc(app):
        npc_id = state.get("npc_id")
        if npc_id is None or app is None:
            return None
        return ui.character_of(app, npc_id)

    def owner_text(owner):
        if owner is None:
            return "None"
        return "{}:{!r}".format(owner.__class__.__name__, character_name(owner, ""))

    def ref_text(ref):
        """装備参照1つの中身。id 文字列か Item オブジェクトかを見分けたい。"""
        if ref is None:
            return "None"
        if isinstance(ref, (str, int, float, bool)):
            return repr(ref)
        name = getattr(ref, "name", None)
        return "{}:{!r}".format(ref.__class__.__name__,
                                frames.short(name, 60) if isinstance(name, str) else "?")

    def equipments_text(character):
        if character is None:
            return "<none>"
        equipments = getattr(character, "equipments", None)
        if not isinstance(equipments, dict):
            return frames.repr_value(equipments)
        return "{" + ", ".join(
            "{!r}: {}".format(slot, ref_text(ref))
            for slot, ref in equipments.items()) + "}"

    # ------------------------------------------------------------ 会話相手
    # 版5: どの包みも受け取った引数をそのまま `orig` へ渡す。
    # 記録に要る値は `frames.arg` で引く（版6。版5は自前の `arg_of`）。

    @ctx.wrap("__main__:ConversationStartManager.__init__", required=False, safe=True)
    def conversation_start(orig, self, *args, **kwargs):
        try:
            character_id = frames.arg(args, kwargs, "character_id", 1)
            state["npc_id"] = str(character_id) if character_id is not None else None
        except Exception:
            ctx.log_exc("party equipment probe: cannot read the conversation partner")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ConversationEndManager.finish_conversation",
              required=False, safe=True)
    def finish_conversation(orig, self, *args, **kwargs):
        state["npc_id"] = None
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------ popup

    def probe_popup(widget):
        try:
            if not has_room():
                return
            item = getattr(widget, "item_instance", None)
            owner = getattr(item, "obtainer", None) if item is not None else None
            popup = getattr(widget, "active_popup", None)
            app = ui.find_app()
            player = getattr(app, "player", None) if app is not None else None
            npc = conversation_npc(app)

            item_id = getattr(widget, "item_id", None)
            item_type = getattr(item, "item_type", None) if item is not None else None
            key = (
                id(widget),
                str(item_id),
                id(owner) if owner is not None else None,
                id(popup) if popup is not None else None,
            )
            if key in seen:
                return
            seen.add(key)
            if len(seen) > SEEN_CAP:
                seen.clear()

            # popup 直下は FloatLayout 等の入れ子で、ボタンはその中に居る
            # （クラッシュ記録の locals より）。孫まで辿る。
            buttons = []
            stack = list(getattr(popup, "children", []) or [])
            visited = set()
            while stack and len(buttons) < 20:
                child = stack.pop()
                if id(child) in visited:
                    continue
                visited.add(id(child))
                text = getattr(child, "text", None)
                if isinstance(text, str):
                    buttons.append({
                        "class": child.__class__.__name__,
                        "text": frames.short(text, 120),
                    })
                grand = getattr(child, "children", None)
                if isinstance(grand, (list, tuple)):
                    stack.extend(grand)

            probe(
                "popup widget={} item_id={} item_type={} item={!r} is_equipped={} "
                "owner={} owner_is_player={} owner_is_npc={} "
                "player_eq={} npc_eq={} owner_eq={} popup={} buttons={}".format(
                    widget.__class__.__name__,
                    frames.short(item_id, 120),
                    frames.short(item_type, 80),
                    frames.short(getattr(item, "name", None), 120),
                    getattr(widget, "is_equipped", None),
                    owner_text(owner),
                    owner is not None and owner is player,
                    owner is not None and owner is npc,
                    equipments_text(player),
                    equipments_text(npc),
                    equipments_text(owner),
                    popup.__class__.__name__ if popup is not None else "None",
                    frames.short(buttons, 1000),
                )
            )
        except Exception:
            ctx.log_exc("party equipment probe: popup observation failed")

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.show_popup_menu",
              required=False, safe=True)
    def show_popup_menu(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        # active_popup はこの戻りの時点では未設定だった（同期読みは popup=None を写す）。
        # 402 のボタン追加と同じく、次フレームで読む。版5: 枠を使い切った後は予約もしない。
        if has_room():
            schedule(lambda: probe_popup(self))
        return result

    # ------------------------------------------------------------ 装備Manager

    def probe_manager(stage, manager, item=None):
        try:
            # 版5: 枠を使い切った後は、行を組む前に降りる（前は組んでから捨てていた）。
            if not has_room():
                return
            app = getattr(manager, "app", None)
            if app is None:
                app = ui.find_app()
            player = getattr(app, "player", None) if app is not None else None
            npc = conversation_npc(app)
            owner = getattr(item, "obtainer", None) if item is not None else None
            probe(
                "{} manager={} app={} item_id={} item_type={} item={!r} "
                "owner={} owner_is_player={} owner_is_npc={} "
                "manager_dict={} player_eq={} npc_eq={} owner_eq={}".format(
                    stage,
                    manager.__class__.__name__ if manager is not None else "None",
                    manager is not None and getattr(manager, "app", None) is not None,
                    frames.short(getattr(item, "id", None), 120),
                    frames.short(getattr(item, "item_type", None), 80),
                    frames.short(getattr(item, "name", None), 120),
                    owner_text(owner),
                    owner is not None and owner is player,
                    owner is not None and owner is npc,
                    frames.repr_value(getattr(manager, "__dict__", None))
                    if manager is not None else "<none>",
                    equipments_text(player),
                    equipments_text(npc),
                    equipments_text(owner),
                )
            )
        except Exception:
            ctx.log_exc("party equipment probe: manager observation failed")

    def probe_init(stage, manager):
        """`__init__` は、クラスごとの鍵の形が前に書いた形と違うときだけ1行書く。

        版5: `333_` が装備のたびにマネージャを組み直すので、`__init__` の行が
        ログの 3 割近くを占めていた。ロード時に作られることは確かめ済み（GAME.md §2.13.3）。
        版6: 注入1回につき1行でも、毎プロセス同じ形の行がログの 9 割になっていたので、
        前の形を `sys` に控えて比べる。最初の前の形は `KNOWN_INIT_SHAPE`。
        """
        if not has_room():
            return
        shape = shape_of(manager)
        shapes = store["init_shapes"]
        if shapes.get(stage, KNOWN_INIT_SHAPE) == shape:
            return
        shapes[stage] = shape
        probe_manager(stage, manager, None)

    def watch_manager(cls_name, method):
        @ctx.wrap("__main__:{}.{}".format(cls_name, method), required=False, safe=True)
        def around(orig, self, *args, **kwargs):
            item = frames.arg(args, kwargs, "item_instance", 0)
            label = "{}.{}".format(cls_name, method)
            probe_manager(label + ":before", self, item)
            result = orig(self, *args, **kwargs)
            probe_manager(label + ":after", self, item)
            return result
        return around

    def watch_init(cls_name):
        @ctx.wrap("__main__:{}.__init__".format(cls_name), required=False, safe=True)
        def init(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            probe_init("{}.__init__:after".format(cls_name), self)
            return result
        return init

    for cls_name, methods in (("ItemEquipManager", ("execute", "equip_item")),
                              ("ItemUnequipManager", ("execute", "unequip_item"))):
        watch_init(cls_name)
        for method in methods:
            watch_manager(cls_name, method)

    ctx.log("party equipment probe: popup + ItemEquipManager/ItemUnequipManager "
            "observation goes to out/{}".format(LOG_BASENAME))
