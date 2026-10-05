# -*- coding: utf-8 -*-
"""故買屋。裏の事務所で盗品を売る。仕様は DOC.md「店で盗む」の盗品の項。

本人の決定（2026-10-05）: 故買屋は裏の事務所に置き、盗品の買い取り額は正規の7割。それ以外の店では正規の半分
（額の段は `theft.stolen_price`。盗んだ店では今までどおり売れない）。

- 事務所の選択肢に「盗品を売る」（盗品を持っているときだけ）。押すと、事務所の主を相手に
  ゲーム自身の売買の窓（`toggle_twin_inventory_window`）を開く。ドラッグ・確認の窓・支払いはゲームのまま
  （店でない施設でも開け、売れる。2026-10-05 の実機）。開くのはゲームの店と同じく `process_choice` を通した自前のフェーズから
- 盗品でない品は主が押し返す（`theft.sell_item`）。主の棚から買うことはできない
- 売った品はゲームが主の持ち物へ積む。窓を閉じたら（ゲームの後始末 `on_close_window` の後）、開く前に無かった品を主の持ち物から外す（セーブに積もらない）。
  閉じる前にゲームが落ちたときのために、開く前の主の持ち物の鍵を控えの `fence` に持ち、窓の開いていないときに選択肢を組んだら片付ける
- 「盗品を売る」は事務所の最初の画面にだけ出し、会話の間は開かない（入ったときの話しかけの選択肢に足され、会話の上に窓が開いた。実機）
- 窓を開いている間は、選択肢を「出る」「会話する」（と組み直しで付く「盗品を売る」）だけにし、閉じたらゲームの
  `set_buttons_to_normal` で事務所の選択肢に戻す。事務所の選択肢は左の4つに収まらず右の欄が開いていて、
  売った後の塗り直しで右の欄が窓の上に戻った（実機）。`process_choice` の待機表示で右の欄が閉じ、4つ以下なら開き直さない。
  ゲームの後始末（`on_close_window`）はこの開き方では選択肢を戻さず（`buttons_backup_for_shopping` は前に寄った店のものが残る）、
  2つに絞ったまま残った（実機）
- 司法取引で裏の事務所を売った締め出しの間は出さない（`office`）
"""
from instantale_modloader import ui

from . import common, office

LABEL = "盗品を売る"
MARK = "fence:open"
#: 売買の窓の左の見出し。ゲームは短い語しか出さない（主の名前を渡すと末尾だけが出た。実機）。
WINDOW_LABEL = "故買"
WINDOW_SITUATION = "shop"
OPEN_TEXT = "{broker}が帳場の奥から顔を上げた。\n「盗品なら引き取ってやる。相場の{rate}だ」"
NO_BUY_TEXT = "{broker}は品を引き寄せた。\n「買い取った品は売り物じゃねえ」"
TWIN_WINDOW_DATA = "visible_twin_inventory_data"
#: 窓を開いている間に残す選択肢（施設の「出る」「会話する」）。閉じたら `set_buttons_to_normal` で戻す。
KEEP_WHILE_OPEN = (common.FACILITY_MARK, "DisplayTalkChoice")


def rate_label(pct):
    """「7割」「65%」。"""
    pct = int(round(float(pct)))
    return "{}割".format(pct // 10) if pct % 10 == 0 else "{}%".format(pct)


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg

    def window_open(app):
        """売買の窓が開いているか。閉じるとゲームの後始末（`on_close_window`）が `is_popup_window_opened` を下ろす。"""
        if getattr(app, "is_popup_window_opened", False):
            return True
        data = getattr(ui.find_hud(app), TWIN_WINDOW_DATA, None)
        return isinstance(data, dict) and bool(data.get("TorF"))

    def broker_here(app):
        """いま居る裏の事務所の `(主の id, 主)`。事務所でなければ None。"""
        facility = env.current_facility(app)
        if ui.facility_type_of(facility) != office.OFFICE_FACILITY_TYPE:
            return None
        owner_id = getattr(facility, "owner", None)
        broker = ui.character_of(app, str(owner_id)) if owner_id is not None else None
        if broker is None or not isinstance(getattr(broker, "inventory", None), dict):
            return None
        return str(owner_id), broker

    def holds_stolen(app):
        player = getattr(app, "player", None)
        return any(env.stolen_entry(app, item) is not None
                   for item in list((getattr(player, "inventory", None) or {}).values()))

    def clear_bought(app):
        """主の持ち物から、窓を開く前に無かった品（売った盗品）を外す。控えの `fence` も消す。"""
        env.fence.update(broker=None, opening=False)
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            record = bucket.pop("fence", None)
            if record is not None:
                worlds.save(playthrough)
        if not isinstance(record, dict):
            return
        broker = ui.character_of(app, str(record.get("broker")))
        inventory = getattr(broker, "inventory", None)
        if not isinstance(inventory, dict):
            write("WARN fence: cannot find the broker {!r} to clear".format(record.get("broker")))
            return
        keep = {str(key) for key in record.get("keep") or []}
        gone = [key for key in list(inventory) if str(key) not in keep]
        names = [getattr(inventory.pop(key), "name", None) for key in gone]
        write("fence: closed; cleared {} item(s) from {!r}: {}".format(
            len(gone), getattr(broker, "name", None), names))

    def on_refresh(app, buttons):
        if not window_open(app) and not env.fence.get("opening"):
            with worlds.lock:
                pending = worlds.load(worlds.playthrough(app)).get("fence")
            if env.fence.get("broker") or pending is not None:
                clear_bought(app)
        buttons[:] = [entry for entry in buttons if screen.mark_of(entry) != MARK]
        screen.prune_stale(buttons, (LABEL,))
        if not common.is_facility_screen(buttons) or getattr(app, "in_conversation", False):
            return              # 施設の最初の画面だけ（入ったときの話しかけの選択肢には足さない。実機）
        if not cfg.FENCE_ENABLED or broker_here(app) is None or env.underworld_banned(app):
            return
        if not holds_stolen(app):
            return
        entry = screen.button(LABEL, mark=MARK)
        if entry is None:
            return
        at = next((index for index, item in enumerate(buttons)
                   if ui.spec_cls_name(item) == common.FACILITY_MARK), len(buttons))
        buttons.insert(at, entry)

    class FencePhase(object):
        """自前のフェーズ。ゲームの店（`ShoppingStartManagerRemake`）と同じく `process_choice` を通して開く。

        `process_choice` の待機表示で、開いていた右の欄が閉じる（GAME.md §2.2）。
        通さずに開くと、事務所の選択肢で開いていた右の欄が、売った後の塗り直しで窓の上に戻った（実機）。
        値段を付けるのはこのスレッド、窓を開くのはメインスレッド（ゲームの店と同じ分け方）。
        """

        def __init__(self, app, broker_id):
            self.app = app
            self.broker_id = broker_id

        def execute(self, choice_text):
            app = self.app
            broker = ui.character_of(app, self.broker_id)
            player = getattr(app, "player", None)
            try:
                app.normalize_shop_inventory_prices(broker, player)
            except Exception:
                ctx.log_exc("crime incentive: cannot price the goods for the fence")
            screen.say(app, OPEN_TEXT.format(broker=getattr(broker, "name", None) or "事務所の主",
                                             rate=rate_label(cfg.FENCE_PCT)))
            # ゲームのマネージャと同じく、終わりに選択肢を組み直す合図を出す。出さないと画面へ塗る一覧
            # （`to_display_buttons`）が絞る前の6つのまま残り、売った後の塗り直しで右の欄に「出る」「会話する」が出た（実機）。
            screen.refresh(app)
            screen.schedule(lambda: open_window(app, self.broker_id))

    def open_window(app, broker_id):
        broker = ui.character_of(app, broker_id)
        try:
            app.toggle_twin_inventory_window(broker, getattr(app, "player", None), WINDOW_LABEL, WINDOW_SITUATION)
        except Exception:
            ctx.log_exc("crime incentive: cannot open the fence")
            env.fence["opening"] = False
            clear_bought(app)
            restore_choices(app)
            return
        env.fence["opening"] = False
        write("fence: opened with {!r} ({})".format(getattr(broker, "name", None), broker_id))

    def restore_choices(app):
        """事務所の選択肢に戻す（ゲーム自身の `set_buttons_to_normal`。選択肢を組み直す合図で 913 のボタンも付き直る）。"""
        normal = getattr(app, "set_buttons_to_normal", None)
        if callable(normal):
            normal()
        else:
            screen.apply_buttons(app, None, "fence")

    def press(app, action):
        found = broker_here(app)
        if found is None or getattr(app, "player", None) is None or getattr(app, "in_conversation", False):
            write("WARN fence: no broker here or in a conversation ({})".format(
                getattr(app, "in_conversation", None)))
            screen.apply_buttons(app, None, "fence")
            return
        broker_id, broker = found
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            bucket["fence"] = {"broker": broker_id, "keep": [str(key) for key in broker.inventory]}
            worlds.save(playthrough)
        env.fence.update(broker=broker_id, opening=True)
        # 窓を開いている間は「出る」「会話する」（と、組み直しで付く「盗品を売る」）だけにする。
        # 事務所の選択肢は左の4つに収まらず、塗り直すと右の欄が窓の上に開く（GAME.md §2.2）。
        # 閉じたら `restore_choices` で事務所の選択肢に戻す（ゲームの後始末は、この開き方では選択肢を戻さなかった。実機）。
        app.buttons = [entry for entry in (getattr(app, "buttons", None) or [])
                       if ui.spec_cls_name(entry) in KEEP_WHILE_OPEN]
        app.buttons_backup_for_shopping = list(app.buttons)
        screen.start_phase(app, FencePhase(app, broker_id), LABEL)

    @ctx.wrap("__main__:InstantaleApp.on_close_window", required=False, safe=True)
    def on_close_window(orig, self, *args, **kwargs):
        """窓の後始末（外側を押したとき・HUD の `on_backdrop_callback`）。故買屋の窓なら、主の持ち物を片付ける。

        選択肢の組み直しの合図だけに頼ると、閉じても片付かない回があった（会話の画面の上で閉じた回。実機）。
        """
        result = orig(self, *args, **kwargs)
        if env.fence.get("broker") and not env.fence.get("opening"):
            try:
                clear_bought(self)
            except Exception:
                ctx.log_exc("crime incentive: cannot clear the fence")
            restore_choices(self)
        return result

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.buy_item", required=False, safe=True)
    def buy_item(orig, self, *args, **kwargs):
        """故買屋の窓では、主の棚から買わせない。"""
        broker_id = env.fence.get("broker")
        if not broker_id:
            return orig(self, *args, **kwargs)
        app = ui.find_app()
        cancel = getattr(self, "cancel_trade_confirmation", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                ctx.log_exc("crime incentive: cannot cancel the purchase at the fence")
        write("fence: refused a purchase of {!r}".format(getattr(getattr(self, "item_instance", None), "name", None)))
        broker = getattr(ui.character_of(app, broker_id), "name", None) if app is not None else None
        screen.say(app, NO_BUY_TEXT.format(broker=broker or "事務所の主"))
        return None

    env.on_refresh(on_refresh)
    env.on_press(MARK, press)
