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
`RESTORE_TO` が `WANTED_THRESHOLD` より低いときは閾値まで戻す
（そうしないと払っても手配が解けない）。

## ボタンの出し方

ボタンはローダの窓口 `choices` の宣言型の口に預ける（TECH.md §3.3.14）。
「罰金を納める」は組み直しのたびに窓口が呼ぶ `offer` が、いまの手配から作る。
役場の選択肢そのものはゲームが持っているので、こちらは足すだけで組み直さない。
出す条件は全部揃ったときだけ:

    いま居る施設が administrative_office／その土地の手配度が閾値未満／
    その画面に移動（`MovePhaseManager`）のボタンがある＝施設の選択肢である／
    戦闘・会話・ポップアップの最中ではない

確認の画面（納める／やめておく）は MOD だけの画面（`open_screen`）で、下の役場の選択肢は窓口が書き換えずに残す。
確認の画面で保存して読み直しても、確認の画面に戻る。
押下は窓口がボタンに持たせた処理を呼ぶ。ボタンはセーブに残らない（窓口が保存の間だけ外す）。
「罰金を納める」には印（`MARK`）を `marks` で付ける（ほかの MOD が見分けるための印。押下の振り分けには使わない）。

## セーブはこちらから書かない

書き換えるのは実行中の `player.gold` と `player.area_history` だけで、
`save_game` は呼ばない。
ゲームが次にセーブしたときに両方まとめて保存される。
セーブされる前に落ちれば**罰金も手配の帳消しも両方が無かったこと**になるので、
片方だけが残ることはない。
"""


from instantale_modloader import choices, ui

from . import record


LOG_BASENAME = "office_pardon.log"

#: 役場の `facility_type`（実セーブで確認。GAME.md §2.7）。
OFFICE_FACILITY_TYPE = "administrative_office"

#: 「罰金を納める」に付ける印（ほかの MOD が見分けるため。窓口 `choices` の `marks`）。
#: 他の MOD と別のキーにすること。
MARK = "mod_pardon_action"

#: 確認の画面の名前（窓口 `choices` の MOD だけの画面）。
CONFIRM_SCREEN = "confirm"

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

# 版3までがセーブに焼いた自前のボタンの文言（窓口 `choices` の `labels`。無害な spec で印の無いものを外す）。
# 版3までは自前のボタンを一覧に物として置いていたので、セーブに文言と spec が残った。
# 金額つきのラベルは `{price}` の手前までを見る（照合は前方一致）。
# `PAY_LABEL` / `CANCEL_LABEL` は入れない。
# 「やめておく」は `302_` も出す文言で、こちらのものだと言い切れない。
OUR_LABELS = (BUTTON_LABEL.split("(")[0], BUTTON_LABEL_FREE)
GONE_TEXT = "（手配はもう残っていない）"
FAILED_TEXT = "（帳面は書き換えられなかった）"

#: ここが真の間はボタンを出さない。
#: 戦闘中・会話中など。
#: 「手が空いているか」（テキストの流し込み中・ポップアップが開いている）は
#: `ui.busy_signals` 側。
#: 表はローダが持つ。`in_shopping` は入っていない（店に寄った後の役場で
#: ボタンが出なかった。`ui.BUSY_FLAGS` の註）。
BUSY_FLAGS = ui.BUSY_FLAGS

#: 施設の選択肢だと見なす目印。
#: 文字列ではなく spec のクラス名で見る。
#: 施設には必ず出口（`MovePhaseManager`）があるので、
#: これが1つも無い画面は施設の選択肢ではない（会話中・確認画面・掲示板など）。
FACILITY_MARK = "MovePhaseManager"


money = ui.money          # 金額の表示（`902_` と共有）
coins = ui.rewrite_coins  # 通貨の表記を今の表記へ（`130_`。`902_` と共有）

# 上の文言は素のゲームの言い方（`ゴールド` / `G`）で書いてある。
# 画面へ出す直前に `coins()` を通すので、`130_` が表記を差し替えていれば
# そちらに揃う。入っていなければ何も変わらない。


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
        # 窓口 `choices` の持ち主の名前（`provide` が返す）。
        "owner": None,
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
    def offer(app, view):
        """役場の選択肢に「罰金を納める」を1つ出す（窓口 `choices` の `offer`。組み直しのたびに呼ばれる）。

        ゲームが組んだ一覧に足すだけで、組み直さない。
        """
        buttons = view.buttons
        if not buttons or not is_facility_screen(buttons):
            return None
        if not at_office(app):
            state["announced"] = None   # 役場を出たら、次に入ったときに知らせ直す
            return None
        if busy(app):
            return None
        found = wanted_here(app)
        if found is None:
            return None
        area_name, _entry, wanted, price = found
        label = (coins(BUTTON_LABEL.format(price=money(price))) if price > 0
                 else BUTTON_LABEL_FREE)
        if ANNOUNCE_ON_ARRIVAL:
            key = facility_key(app)
            if state["announced"] != key:
                state["announced"] = key
                write("offer: {!r} wanted={} price={} label={!r}".format(
                    area_name, wanted, price, label))
                screen.say(app, ANNOUNCE_TEXT.format(area=area_name))
        # 移動（出口）の手前に置く。
        # 役場でできることを先に、その場を離れる選択肢を後に、
        # という並びはゲーム自身の施設の選択肢と同じ。
        return [choices.Choice(label, place=choices.before(FACILITY_MARK), marks={MARK: "open"},
                               on_press=_press("open", label))]

    # ------------------------------------------------------------ 確認画面
    def open_confirm(app):
        """「罰金を納めて手配を解く」が押されたとき。金額を出して確認を取る（確認の画面を開く）。"""
        found = wanted_here(app)
        if found is None:
            # 押すまでの間に手配が消えている（別経路で戻された等）。
            write("confirm: nothing is pending any more")
            screen.say(app, GONE_TEXT)
            restore(app, "not wanted")
            return
        area_name, _entry, wanted, price = found
        gold = ui.gold_of(app)
        screen.say(app, coins(CONFIRM_TEXT.format(
            area=area_name, price=money(price))))
        screen.say(app, coins(CONFIRM_DETAIL.format(
            wanted=wanted, gold=money(gold) if gold is not None else "?")))
        write("confirm: {!r} wanted={} price={} gold={}".format(
            area_name, wanted, price, gold))
        choices.open_screen(app, state["owner"], CONFIRM_SCREEN)

    def confirm_screen(app, view, _data):
        """確認の画面のボタン（組み直しのたびに、いまの手配から組む）。役場に居ない・手配が無いなら閉じる（None）。"""
        found = wanted_here(app) if at_office(app) else None
        if found is None:
            return None
        pay_label = coins(PAY_LABEL.format(price=money(found[3])))
        return [choices.Choice(pay_label, on_press=_press("pay", pay_label)),
                choices.Choice(CANCEL_LABEL, on_press=_press("cancel", CANCEL_LABEL))]

    def restore(app, why):
        """確認の画面を閉じて役場の選択肢に戻す（下の選択肢は窓口が書き換えずに残している）。

        まだ手配が残っていれば窓口の組み直しで `offer` が足し直すので、ここで足さない
        （「今どうなっているか」の判断を1箇所に閉じておく）。
        """
        write("restore: {}".format(why))
        if not choices.close_screen(app, state["owner"]):
            choices.repaint(app)

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
        gold = ui.gold_of(app)

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
            screen.say(app, coins(BROKE_DETAIL.format(price=money(price),
                                                      gold=money(gold))))
            restore(app, "not enough gold")
            return

        before = record.lawfulness_of(entry)
        # 下げる方向には動かさない（設定を触られても手配を重くしない）。
        # 閾値より下にも戻さない。戻し先が閾値未満の組み合わせ（閾値 20・戻し先 10 など）で
        # そのまま書くと、払っても手配が解けずにボタンが残り、押すたびに罰金だけ取られる。
        after = max(before, int(RESTORE_TO), int(WANTED_THRESHOLD))
        if not record.set_lawfulness(entry, after):
            write("WARN pay: cannot write lawfulness; nothing was charged")
            screen.say(app, FAILED_TEXT)
            restore(app, "cannot write lawfulness")
            return
        if price > 0 and ui.set_gold(app, gold - price) is None:
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
            screen.say(app, coins(PAID_TEXT.format(price=money(price))))
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
    def _press(action, text):
        """自前のボタンの押下（窓口がボタンに持たせる処理）。ゲームと同じ経路（`process_choice`）で動かす。"""
        def on_press(app):
            write("pressed {!r} ({})".format(text, action))
            screen.start_phase(app, PardonPhase(app, action), text,
                               fallback=lambda: ACTIONS[action](app))
            return None
        return on_press

    # 選択肢と押下はローダの窓口 `choices`（TECH.md §3.3.14 の宣言型の口）に預ける。
    state["owner"] = choices.provide(ctx, screen, offer=offer, screens={CONFIRM_SCREEN: confirm_screen},
                                     labels=OUR_LABELS)

    ctx.log("office pardon: {}G per point, threshold {}, restore to {}, log={}"
            .format(PRICE_PER_POINT, WANTED_THRESHOLD, RESTORE_TO, log_path))
