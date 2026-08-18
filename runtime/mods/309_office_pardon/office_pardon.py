# -*- coding: utf-8 -*-
"""機能追加: 役場で罰金を納めて、その土地の手配を帳消しにする。

手配度（`player.area_history[エリアid]['lawfulness']`）が 0 未満だと、
プレイヤーはその土地では犯罪者として扱われる。
素のゲームにはこれを戻す手段が無いので、**役場（`administrative_office`）の選択肢に「罰金を納める」を1つ足す**。

    [役場]  会話する / 罰金を納めて手配を解く(3,000G) / 出る
                              ↓
            3,000ゴールドを納める / やめておく
                              ↓
            所持金 -3,000 ／ 手配度 -3 → 10

## 扱うのは今いるエリアの手配度だけ

`area_history` はエリア id ごとに1件ある。
他の土地の手配はその土地の役場でしか解けない。
「よその役場に金を積めば全部消える」は世界の作りとして通らないし、
どの土地の分をいくら払ったのかも画面に出せなくなる。

## 値段

    手配度 = WANTED_THRESHOLD - 手配度の実値      （既定の閾値 0。lawfulness=-3 なら 3）
    罰金   = PRICE_PER_POINT × 手配度              （既定 1000G/点 なので 3,000G）

払うと手配度は `RESTORE_TO`（既定 10 ＝ 素の平常値）に戻る。
**下げる方向には決して動かさない**（設定を触って
`RESTORE_TO` を今の値より低くしても、そのときは今の値のまま）。

## ボタンの出し方

`InstantaleApp.refresh_choice_buttons` を包み、**ゲームが選択肢を組み終える前に** 自前のボタンを1つ差し込む。
役場の選択肢そのものはゲームが持っているので、こちらは足すだけで組み直さない。
差し込む条件は全部揃ったときだけ:

    いま居る施設が administrative_office／その土地の手配度が閾値未満／
    その画面に移動（`MovePhaseManager`）のボタンがある＝施設の選択肢である／
    戦闘・会話・ポップアップの最中ではない／まだ足していない

最後の「まだ足していない」は文字列ではなく印（`MARK`）で見る。
これが無いと、こちらが塗り直すたびにボタンが増えていく。

## 自前のクラス名を `PhaseSpec` に書かない

ボタンはセーブに焼かれうる（`PhaseSpec.to_dict()`）。
無害な既存クラスを持たせ、押下は `on_button_press` を包んで印で横取りする。
印のキーは他の MOD と別にすること（`301_`=mod_action / `302_`=mod_party_action /
`305_`=mod_mini_action / `307_`=mod_road_action / ここ=mod_pardon_action）。

## セーブはこちらから書かない

書き換えるのは実行中の `player.gold` と `player.area_history` だけで、
`save_game` は呼ばない。
ゲームが次にセーブしたときに両方まとめて保存される。
セーブされる前に落ちれば**罰金も手配の帳消しも両方が無かったこと**になるので、
片方だけが残ることはない。
"""


from instantale_modloader import ui

from . import record


LOG_BASENAME = "office_pardon.log"

#: 役場の `facility_type`（実セーブで確認。GAME.md §2.7）。
OFFICE_FACILITY_TYPE = "administrative_office"

#: 押下を横取りするための印。
#: 他の MOD と別のキーにすること。
MARK = "mod_pardon_action"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが
# GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む）。
# `record.py` へ移さないこと（TECH.md §3.8）。

#: 手配度1点あたりの罰金。
#: 0 にすると無料で解ける。
PRICE_PER_POINT = 1000

#: これ未満の手配度を「手配されている」と見なす。
#: 既定 0 ＝ 負なら犯罪者。
WANTED_THRESHOLD = 0

#: 罰金を納めた後の手配度。
#: 既定 10 ＝ 素の平常値。
RESTORE_TO = 10

#: 手配されている状態で役場に入ったとき、その旨を1行出すか。
ANNOUNCE_ON_ARRIVAL = True

# ---------------------------------------------------------------- 画面に出す文言
BUTTON_LABEL = "罰金を納めて手配を解く({price}G)"
BUTTON_LABEL_FREE = "手配を解いてもらう"
ANNOUNCE_TEXT = "窓口の帳面に、{area}で手配された者として名が載っている。"
CONFIRM_TEXT = ("{area}での手配について、役場の窓口に問う。"
                "「{price}ゴールド。それで帳消しです」")
CONFIRM_DETAIL = "（手配度 {wanted} ／ 手持ち {gold}ゴールド）"
PAY_LABEL = "{price}ゴールドを納める"
CANCEL_LABEL = "やめておく"
CANCEL_TEXT = "窓口を離れた。"
PAID_TEXT = "役場に{price}ゴールドを納めた。"
CLEARED_TEXT = "{area}での手配は取り消された。"
BROKE_TEXT = "「その額を持たない者に、書き換える帳面はありません」"
BROKE_DETAIL = "（罰金 {price}ゴールド ／ 手持ち {gold}ゴールド）"

# セーブから復元された残骸を見分けるための、
# こちらのラベル一覧（`ui.Screen.prune_stale`）。
# `PhaseSpec.to_dict()` は text と spec しか書かないので印は落ちる。
# 落ちたものは `insert_button()` の印による重複判定をすり抜けるので、
# 役場でセーブ → タイトル → ロードのあと同じボタンが2つ並び、
# 復元された方は押しても無反応になる（`301_` で実際に起きた壊れ方）。
# 金額つきのラベルは `{price}` の手前までを見る（照合は前方一致）。
# `PAY_LABEL` / `CANCEL_LABEL` は入れない。
# 確認画面は一瞬しか出ないうえ、「やめておく」は `302_` も出す文言で、
# こちらのものだと言い切れない。
OUR_LABELS = (BUTTON_LABEL.split("(")[0], BUTTON_LABEL_FREE)
GONE_TEXT = "（手配はもう残っていない）"
FAILED_TEXT = "（帳面は書き換えられなかった）"

#: ここが真の間はボタンを出さない。
#: 戦闘中・会話中など。
#: 「手が空いているか」（テキストの流し込み中・ポップアップが開いている）は
#: `ui.busy_signals` 側。
# ここが真の間は選択肢を足さない。
#: 表はローダが持つ（`902_` と共有）。
BUSY_FLAGS = ui.BUSY_FLAGS

#: 施設の選択肢だと見なす目印。
#: 文字列ではなく spec のクラス名で見る。
#: 施設には必ず出口（`MovePhaseManager`）があるので、
#: これが1つも無い画面は施設の選択肢ではない（会話中・確認画面・掲示板など）。
FACILITY_MARK = "MovePhaseManager"


money = ui.money          # 金額の表示（`902_` と共有）


def price_for(wanted):
    """手配度から罰金を出す。手配されていなければ 0。"""
    if wanted <= 0:
        return 0
    return max(0, int(PRICE_PER_POINT)) * int(wanted)


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    screen = ui.Screen(ctx, write, tag="office pardon", mark=MARK)

    state = {
        # 確認画面を出す前の選択肢。
        # 「やめておく」で元に戻すために控える。
        "saved": None,
        # 手配を知らせた場所（エリア id, 施設 id）。
        # 同じ場所で言い直さないため。
        "announced": None,
    }

    # ------------------------------------------------------------ 状況を読む
    def wanted_here(app):
        """今いる土地の手配。`(エリア名, 記録, 手配度, 罰金)`。無ければ `None`。

        読めなかったものは「手配されていない」に倒す。
        値が読めないことを理由に、ありもしない罰金を請求しない。
        """
        player = getattr(app, "player", None)
        if player is None:
            return None
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        if not area_id:
            return None
        entry = record.history_entry(player, area_id)
        lawfulness = record.lawfulness_of(entry)
        if lawfulness is None:
            return None
        wanted = int(WANTED_THRESHOLD) - lawfulness
        if wanted <= 0:
            return None
        name = getattr(area, "name", None)
        return (name if isinstance(name, str) and name else "この土地",
                entry, wanted, price_for(wanted))

    def at_office(app):
        """いま役場に立っているか。"""
        facility = record.current_facility(app)
        return ui.facility_type_of(facility) == OFFICE_FACILITY_TYPE

    def busy(app):
        """ボタンを出す前提が崩れている理由の一覧（無ければ空）。"""
        return [flag for flag in BUSY_FLAGS if getattr(app, flag, False)]

    def is_facility_screen(buttons):
        """いま施設の選択肢が出ているか。文字列ではなく spec で見る。"""
        if not isinstance(buttons, (list, tuple)):
            return False
        return any(ui.spec_cls_name(entry) == FACILITY_MARK for entry in buttons)

    def facility_key(app):
        facility = record.current_facility(app)
        return (ui.area_id_of(ui.current_area(app)),
                str(getattr(facility, "id", "")))

    # ------------------------------------------------------------ ボタンを足す
    def insert_button(app):
        """役場の選択肢に「罰金を納める」を1つ足す。足したら `True`。

        **ゲームが組んだ一覧に挿すだけ**で、組み直さない。
        呼ばれるのは `refresh_choice_buttons` の中（`orig` の前）なので、
        ここで挿せばゲーム自身がそのまま画面まで運ぶ。
        こちらから塗り直す必要がない。
        """
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list) or not buttons:
            return False
        if not is_facility_screen(buttons):
            return False
        # **印を失った自前ボタンの残骸を先に落とす**（`ui.Screen.prune_stale`）。
        # 施設の選択肢だと分かってから呼ぶので、他の画面には手を出さない。
        # 落とした分は下で新しい印つきに差し直されるため、
        # 残骸は「消える」のではなく「生き返る」。
        screen.prune_stale(buttons, OUR_LABELS)
        if any(screen.mark_of(entry) for entry in buttons):
            return False        # 既に在る（塗り直しで増やさない）
        if not at_office(app):
            return False
        blocked = busy(app)
        if blocked:
            return False
        found = wanted_here(app)
        if found is None:
            return False
        area_name, _entry, wanted, price = found

        label = (BUTTON_LABEL.format(price=money(price)) if price > 0
                 else BUTTON_LABEL_FREE)
        entry = screen.button(label, mark="open")
        if entry is None:
            write("cannot build the button (PhaseSpec unavailable)")
            return False

        # 移動（出口）の手前に置く。
        # 役場でできることを先に、その場を離れる選択肢を後に、
        # という並びはゲーム自身の施設の選択肢と同じ。
        at = len(buttons)
        for index, item in enumerate(buttons):
            if ui.spec_cls_name(item) == FACILITY_MARK:
                at = index
                break
        buttons.insert(at, entry)
        write("offer: {!r} wanted={} price={} label={!r}".format(
            area_name, wanted, price, label))

        if ANNOUNCE_ON_ARRIVAL:
            key = facility_key(app)
            if state["announced"] != key:
                state["announced"] = key
                screen.say(app, ANNOUNCE_TEXT.format(area=area_name))
        return True

    # ------------------------------------------------------------ 確認画面
    def open_confirm(app):
        """「罰金を納めて手配を解く」が押されたとき。金額を出して確認を取る。"""
        found = wanted_here(app)
        if found is None:
            # 押すまでの間に手配が消えている（別経路で戻された等）。
            write("confirm: nothing is pending any more")
            screen.say(app, GONE_TEXT)
            restore(app, "not wanted")
            return
        area_name, _entry, wanted, price = found
        gold = record.gold_of(getattr(app, "player", None))

        state["saved"] = [item for item in (getattr(app, "buttons", None) or [])
                          if not screen.mark_of(item)]
        screen.say(app, CONFIRM_TEXT.format(area=area_name, price=money(price)))
        screen.say(app, CONFIRM_DETAIL.format(
            wanted=wanted, gold=money(gold) if gold is not None else "?"))

        pay = screen.button(PAY_LABEL.format(price=money(price)), mark="pay")
        cancel = screen.button(CANCEL_LABEL, mark="cancel")
        if pay is None or cancel is None:
            write("confirm: cannot build the confirmation buttons")
            restore(app, "no buttons")
            return
        write("confirm: {!r} wanted={} price={} gold={}".format(
            area_name, wanted, price, gold))
        screen.apply_buttons(app, [pay, cancel], "confirm")

    def restore(app, why):
        """役場の選択肢に戻す。

        控えからは自前のボタンを外してある。
        まだ手配が残っていれば `refresh_choice_buttons` のフックが足し直すので、
        ここで足さない（「今どうなっているか」の判断を1箇所に閉じておく）。
        """
        saved, state["saved"] = state["saved"], None
        write("restore: {} ({} entries)".format(
            why, len(saved) if saved is not None else "keep"))
        screen.apply_buttons(app, saved, "restore")

    # ------------------------------------------------------------ 支払う
    def pay(app):
        """罰金を納めて手配度を戻す。金と手配度は同じ流れの中で動かす。"""
        found = wanted_here(app)
        if found is None:
            write("pay: nothing is pending any more")
            screen.say(app, GONE_TEXT)
            restore(app, "not wanted")
            return
        area_name, entry, wanted, price = found
        player = getattr(app, "player", None)
        gold = record.gold_of(player)

        if gold is None:
            # 所持金が読めないなら払わせない。
            # 手配だけ消して金を取らない（あるいはその逆）にならないよう、
            # ここで降りる。
            write("WARN pay: cannot read player.gold; refusing")
            screen.say(app, FAILED_TEXT)
            restore(app, "no gold field")
            return
        if gold < price:
            write("pay: refused; price={} gold={}".format(price, gold))
            screen.say(app, BROKE_TEXT)
            screen.say(app, BROKE_DETAIL.format(price=money(price),
                                                gold=money(gold)))
            restore(app, "not enough gold")
            return

        before = record.lawfulness_of(entry)
        # 下げる方向には動かさない（設定を触られても手配を重くしない）。
        after = max(before, int(RESTORE_TO))
        if not record.set_lawfulness(entry, after):
            write("WARN pay: cannot write lawfulness; nothing was charged")
            screen.say(app, FAILED_TEXT)
            restore(app, "cannot write lawfulness")
            return
        if price > 0 and not record.set_gold(player, gold - price):
            # 金を取れなかったので手配度も戻す。
            # 片方だけ通さない。
            record.set_lawfulness(entry, before)
            write("WARN pay: cannot write gold; lawfulness rolled back")
            screen.say(app, FAILED_TEXT)
            restore(app, "cannot write gold")
            return

        record.refresh_status(app)
        state["announced"] = None
        write("paid: {!r} price={} gold {} -> {} lawfulness {} -> {} (wanted {})"
              .format(area_name, price, gold, gold - price, before, after, wanted))
        if price > 0:
            screen.say(app, PAID_TEXT.format(price=money(price)))
        screen.say(app, CLEARED_TEXT.format(area=area_name))
        restore(app, "paid")

    def cancel(app):
        write("cancelled")
        screen.say(app, CANCEL_TEXT)
        restore(app, "cancelled")

    ACTIONS = {"open": open_confirm, "pay": pay, "cancel": cancel}

    class PardonPhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**（セーブに焼かれる）。"""

        def __init__(self, app, action):
            self.app = app
            self.action = action

        def execute(self, choice_text):
            try:
                ACTIONS[self.action](self.app)
            except Exception:
                ctx.log_exc("office pardon: phase failed ({})".format(self.action))

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """役場の選択肢が組まれるたびに、条件が揃っていればボタンを1つ足す。

        `orig` の前に挿すので、ゲーム自身がそのまま
        `to_display_buttons` を組んで画面まで運ぶ。
        こちらから塗り直さないぶん、押下と同じ流れの中で差し替えたときの「見えているものと押されるものが食い違う」（TECH.md
        §5）も起きない。

        **自分が挿したものは印で見分ける**ので、何度呼ばれても増えない。
        """
        try:
            insert_button(self)
        except Exception:
            ctx.log_exc("office pardon: cannot offer the pardon")
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        entry = ui.pressed_entry(self, button_index)
        action = screen.mark_of(entry)
        if action not in ACTIONS:
            return orig(self, button_index, *args, **kwargs)
        text = (entry.get("text") if isinstance(entry, dict) else None) or action
        write("pressed {!r} ({})".format(text, action))
        screen.start_phase(self, PardonPhase(self, action), text,
                           fallback=lambda: ACTIONS[action](self))
        return None

    ctx.log("office pardon: {}G per point, threshold {}, restore to {}, log={}"
            .format(PRICE_PER_POINT, WANTED_THRESHOLD, RESTORE_TO, log_path))
